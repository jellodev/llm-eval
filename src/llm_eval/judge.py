import json
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from llm_eval.config import JudgeConfig

PASS = "정상"
FAIL = "결함"

SCHEMA = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "hit": {"type": "boolean"},
                    "evidence": {"type": "string"},
                },
                "required": ["id", "hit", "evidence"],
                "additionalProperties": False,
            },
        },
        "verdict": {"type": "string", "enum": [PASS, FAIL]},
        "reason": {"type": "string"},
    },
    "required": ["items", "verdict", "reason"],
    "additionalProperties": False,
}


@dataclass(frozen=True)
class Sample:
    id: str
    sha: str
    label: str | None


def load_samples(path: Path) -> list[Sample]:
    samples = []
    for entry in json.loads(path.read_text(encoding="utf-8")):
        label = entry.get("label")
        if label not in (None, PASS, FAIL):
            raise ValueError(f"{entry['id']}: label 은 {PASS}·{FAIL}·null 중 하나여야 한다 (받은 값: {label})")
        samples.append(Sample(str(entry["id"]), entry["sha"], label))
    return samples


def build_prompt(repo: Path, sha: str, config: JudgeConfig) -> str:
    rules = []
    for path in config.context:
        shown = _git(repo, "show", f"{sha}^:{path}", check=False)
        if shown.returncode == 0:
            rules.append(f"### {path}\n{shown.stdout}")
    message = _git(repo, "show", "-s", "--format=%B", sha).stdout
    excludes = [f":(exclude){pattern}" for pattern in config.exclude]
    diff = _git(repo, "show", "--format=", sha, "--", ".", *excludes).stdout
    rule_text = "\n\n".join(rules) if rules else "(없음)"
    return (
        f"## 프로젝트 규칙 (커밋 직전 시점)\n\n{rule_text}\n\n"
        f"## 커밋 메시지\n\n{message}\n\n"
        f"## diff\n\n{diff}"
    )


def judge_sample(repo: Path, sample: Sample, rubric: str, config: JudgeConfig) -> dict:
    with tempfile.TemporaryDirectory() as sandbox:
        proc = subprocess.run(
            [
                "claude", "-p",
                "--tools", "",
                "--model", config.model,
                "--setting-sources", "",
                "--system-prompt", rubric,
                "--output-format", "json",
                "--json-schema", json.dumps(SCHEMA),
            ],
            input=build_prompt(repo, sample.sha, config),
            capture_output=True,
            text=True,
            cwd=sandbox,
            timeout=config.timeout_seconds,
        )
    try:
        result = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {"error": (proc.stderr or proc.stdout).strip()[:500]}
    verdict = result.get("structured_output")
    if result.get("is_error") or not verdict:
        return {"error": str(result.get("result"))[:500]}
    return {"judge": verdict, "cost_usd": result.get("total_cost_usd")}


def run(repo: Path, samples: list[Sample], rubric: str, config: JudgeConfig, out: Path, workers: int) -> dict:
    out.mkdir(parents=True, exist_ok=True)

    def one(sample: Sample) -> dict:
        target = out / f"{sample.id}.json"
        if target.is_file():
            cached = json.loads(target.read_text(encoding="utf-8"))
            if cached["sha"] == sample.sha:
                return {**cached, "label": sample.label}
        outcome = {"id": sample.id, "sha": sample.sha, "label": sample.label, **judge_sample(repo, sample, rubric, config)}
        if "judge" in outcome:
            target.write_text(json.dumps(outcome, ensure_ascii=False, indent=2), encoding="utf-8")
        return outcome

    with ThreadPoolExecutor(max_workers=workers) as pool:
        outcomes = list(pool.map(one, samples))
    summary = summarize(outcomes)
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def summarize(outcomes: list[dict]) -> dict:
    judged = [o for o in outcomes if "judge" in o]
    labeled = [o for o in judged if o["label"] is not None]
    confusion = {"tp": 0, "fn": 0, "fp": 0, "tn": 0}
    for o in labeled:
        verdict = o["judge"]["verdict"]
        if o["label"] == FAIL:
            confusion["tp" if verdict == FAIL else "fn"] += 1
        else:
            confusion["fp" if verdict == FAIL else "tn"] += 1
    return {
        "judged": len(judged),
        "labeled": len(labeled),
        "confusion": confusion,
        "accuracy": _accuracy(confusion),
        "kappa": cohen_kappa(confusion),
        "cost_usd": round(sum(o.get("cost_usd") or 0 for o in judged), 4),
        "disagreements": [
            {"id": o["id"], "sha": o["sha"], "label": o["label"], "verdict": o["judge"]["verdict"], "reason": o["judge"]["reason"]}
            for o in labeled
            if o["label"] != o["judge"]["verdict"]
        ],
        "failed": [{"id": o["id"], "error": o["error"]} for o in outcomes if "error" in o],
    }


def cohen_kappa(confusion: dict) -> float | None:
    tp, fn, fp, tn = confusion["tp"], confusion["fn"], confusion["fp"], confusion["tn"]
    total = tp + fn + fp + tn
    if total == 0:
        return None
    observed = (tp + tn) / total
    expected = ((tp + fn) * (tp + fp) + (fp + tn) * (fn + tn)) / total / total
    if expected == 1:
        return None
    return round((observed - expected) / (1 - expected), 4)


def _accuracy(confusion: dict) -> float | None:
    total = sum(confusion.values())
    return round((confusion["tp"] + confusion["tn"]) / total, 4) if total else None


def _git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=check)
