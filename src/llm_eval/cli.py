import argparse
import json
import subprocess
import sys
from collections import Counter
from dataclasses import asdict
from pathlib import Path

from llm_eval.config import CONFIG_FILE, load_config
from llm_eval.rules import SourceFile, check, enabled_rules


def main(argv: list[str] | None = None) -> int:
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
