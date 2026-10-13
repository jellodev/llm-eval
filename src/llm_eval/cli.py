import argparse
import json
import subprocess
import sys
from collections import Counter
from dataclasses import asdict
from pathlib import Path

from llm_eval import judge
from llm_eval.config import CONFIG_FILE, load_config
from llm_eval.rules import SourceFile, check, enabled_rules


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["judge"]:
        return _judge(argv[1:])
    return _grade(argv)


def _grade(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="llm-eval",
        description="git 추적 파일과 gitignore 되지 않은 새 파일 전체를 규칙으로 채점해 JSON 리포트를 출력한다. 위반이 있으면 종료 코드 1.",
    )
    parser.add_argument("--root", type=Path, default=Path.cwd(), help="검사할 디렉터리 (기본: 현재 디렉터리)")
    parser.add_argument("--config", type=Path, help=f"설정 TOML (기본: <root>/{CONFIG_FILE}, 없으면 전체 규칙·기본값)")
    args = parser.parse_args(argv)

    config = load_config(args.config or args.root / CONFIG_FILE)
    report = check(_load_files(args.root), config)
    output = {
        "passed": not report.violations,
        "rules": enabled_rules(config),
        "summary": dict(Counter(v.rule for v in report.violations)),
        "violations": [_without_reason(v) for v in report.violations],
        "excepted": [asdict(v) for v in report.excepted],
        "stale_exceptions": [asdict(a) for a in report.stale_exceptions],
    }
    json.dump(output, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 0 if output["passed"] else 1


def _judge(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="llm-eval judge",
        description="커밋 표본을 LLM Judge 로 판정하고, 사람 라벨이 있으면 일치율(정확도·Cohen κ)을 계산한다. 판정 실패가 있으면 종료 코드 1.",
    )
    parser.add_argument("--set", type=Path, required=True, help="표본 JSON: [{id, sha, label: 정상|결함|null}]")
    parser.add_argument("--rubric", type=Path, required=True, help="Judge 시스템 프롬프트(루브릭) 파일")
    parser.add_argument("--out", type=Path, required=True, help="표본별 판정과 summary.json 을 쓸 디렉터리 (있는 판정은 재사용)")
    parser.add_argument("--root", type=Path, default=Path.cwd(), help="git 저장소 안의 디렉터리 (기본: 현재 디렉터리)")
    parser.add_argument("--config", type=Path, help=f"설정 TOML (기본: <root>/{CONFIG_FILE})")
    parser.add_argument("--workers", type=int, default=4, help="동시 판정 수 (기본: 4)")
    args = parser.parse_args(argv)

    config = load_config(args.config or args.root / CONFIG_FILE)
    repo = Path(
        subprocess.run(
            ["git", "rev-parse", "--show-toplevel"], cwd=args.root, check=True, capture_output=True, text=True
        ).stdout.strip()
    )
    summary = judge.run(
        repo,
        judge.load_samples(args.set),
        args.rubric.read_text(encoding="utf-8"),
        config.judge,
        args.out,
        args.workers,
    )
    json.dump(summary, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 1 if summary["failed"] else 0


def _load_files(root: Path) -> list[SourceFile]:
    listed = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    files = []
    for path in filter(None, listed.split("\0")):
        target = root / path
        if not target.is_file():
            continue
        try:
            text = target.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        files.append(SourceFile(path, text.splitlines()))
    return files


def _without_reason(violation) -> dict:
    data = asdict(violation)
    del data["reason"]
    return data
