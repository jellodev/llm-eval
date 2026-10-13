import json
import os
import subprocess
import sys

import pytest

from llm_eval.cli import main
from llm_eval.judge import cohen_kappa

FAKE_CLAUDE = """#!{python}
import json, os, sys
prompt = sys.stdin.read()
log = os.environ.get("FAKE_CLAUDE_LOG")
if log:
    with open(log, "a", encoding="utf-8") as f:
        f.write(json.dumps({{"argv": sys.argv[1:], "prompt": prompt, "cwd": os.getcwd()}}) + "\\n")
if "CRASH" in prompt:
    print(json.dumps({{"is_error": True, "result": "boom"}}))
    sys.exit(0)
verdict = "결함" if "BUG" in prompt else "정상"
print(json.dumps({{
    "is_error": False,
    "total_cost_usd": 0.01,
    "structured_output": {{"items": [{{"id": "R1", "hit": verdict == "결함", "evidence": "-"}}], "verdict": verdict, "reason": verdict}},
}}))
"""


@pytest.fixture
def fake_claude(tmp_path, monkeypatch):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    script = bin_dir / "claude"
    script.write_text(FAKE_CLAUDE.format(python=sys.executable))
    script.chmod(0o755)
    log = tmp_path / "calls.jsonl"
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_CLAUDE_LOG", str(log))
    return log


def commit(repo, files, message):
    for path, text in files.items():
        target = repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", message], check=True
    )
    return subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], check=True, capture_output=True, text=True
    ).stdout.strip()


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    commit(root, {"CLAUDE.md": "old rule\n", "llm-eval.toml": '[judge]\ncontext = ["CLAUDE.md"]\nexclude = ["**/gen.json"]\n'}, "init")
    return root


def judge(capsys, repo, tmp_path, samples):
    sample_file = tmp_path / "set.json"
    sample_file.write_text(json.dumps(samples, ensure_ascii=False))
    rubric = tmp_path / "rubric.md"
    rubric.write_text("rubric text")
    out = tmp_path / "out"
    code = main(["judge", "--root", str(repo), "--set", str(sample_file), "--rubric", str(rubric), "--out", str(out)])
    return code, json.loads(capsys.readouterr().out), out


def calls(log):
    return [json.loads(line) for line in log.read_text().splitlines()]


def test_agreement_is_measured_against_labels(fake_claude, repo, tmp_path, capsys):
    bug = commit(repo, {"a.txt": "BUG\n"}, "feat: a")
    clean = commit(repo, {"b.txt": "fine\n"}, "feat: b")
    missed = commit(repo, {"c.txt": "fine\n"}, "feat: c")
    unlabeled = commit(repo, {"d.txt": "BUG\n"}, "feat: d")

    code, summary, out = judge(
        capsys,
        repo,
        tmp_path,
        [
            {"id": "1", "sha": bug, "label": "결함"},
            {"id": "2", "sha": clean, "label": "정상"},
            {"id": "3", "sha": missed, "label": "결함"},
            {"id": "4", "sha": unlabeled, "label": None},
        ],
    )

    assert code == 0
    assert summary["judged"] == 4
    assert summary["labeled"] == 3
    assert summary["confusion"] == {"tp": 1, "fn": 1, "fp": 0, "tn": 1}
    assert summary["accuracy"] == round(2 / 3, 4)
    assert [d["id"] for d in summary["disagreements"]] == ["3"]
    assert summary["cost_usd"] == 0.04
    assert json.loads((out / "summary.json").read_text()) == summary
    assert json.loads((out / "4.json").read_text())["judge"]["verdict"] == "결함"


def test_judge_sees_rules_as_of_parent_commit_and_no_excluded_paths(fake_claude, repo, tmp_path, capsys):
    sha = commit(
        repo,
        {"CLAUDE.md": "new rule added later\n", "src/x.txt": "change\n", "api/gen.json": "generated\n"},
        "feat: x",
    )

    judge(capsys, repo, tmp_path, [{"id": "1", "sha": sha, "label": None}])

    [call] = calls(fake_claude)
    assert "old rule" in call["prompt"].split("## 커밋 메시지")[0]
    assert "new rule added later" not in call["prompt"].split("## 커밋 메시지")[0]
    assert "feat: x" in call["prompt"]
    assert "src/x.txt" in call["prompt"]
    assert "gen.json" not in call["prompt"]
    assert call["argv"][call["argv"].index("--tools") + 1] == ""
    assert call["argv"][call["argv"].index("--setting-sources") + 1] == ""
    assert call["argv"][call["argv"].index("--system-prompt") + 1] == "rubric text"
    assert call["cwd"] != str(repo)


def test_existing_verdicts_are_reused(fake_claude, repo, tmp_path, capsys):
    sha = commit(repo, {"a.txt": "BUG\n"}, "feat: a")
    samples = [{"id": "1", "sha": sha, "label": "결함"}]

    judge(capsys, repo, tmp_path, samples)
    _, summary, _ = judge(capsys, repo, tmp_path, samples)

    assert len(calls(fake_claude)) == 1
    assert summary["confusion"]["tp"] == 1


def test_failed_judgement_is_reported_and_not_cached(fake_claude, repo, tmp_path, capsys):
    sha = commit(repo, {"a.txt": "CRASH\n"}, "feat: a")

    code, summary, out = judge(capsys, repo, tmp_path, [{"id": "1", "sha": sha, "label": "결함"}])

    assert code == 1
    assert summary["failed"] == [{"id": "1", "error": "boom"}]
    assert summary["judged"] == 0
    assert not (out / "1.json").exists()


def test_unknown_label_is_rejected(fake_claude, repo, tmp_path, capsys):
    sha = commit(repo, {"a.txt": "x\n"}, "feat: a")

    with pytest.raises(ValueError, match="label"):
        judge(capsys, repo, tmp_path, [{"id": "1", "sha": sha, "label": "범위 이탈"}])


def test_cohen_kappa_matches_hand_computation():
    assert cohen_kappa({"tp": 9, "fn": 7, "fp": 2, "tn": 10}) == 0.3762
    assert cohen_kappa({"tp": 5, "fn": 0, "fp": 0, "tn": 5}) == 1.0
    assert cohen_kappa({"tp": 0, "fn": 0, "fp": 0, "tn": 0}) is None
    assert cohen_kappa({"tp": 0, "fn": 0, "fp": 0, "tn": 4}) is None
