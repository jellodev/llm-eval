import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import PurePosixPath

from llm_eval.comments import comment_lines
from llm_eval.config import Allowance, Config
from llm_eval.java_structure import blank_comments_and_strings, self_invocations


@dataclass(frozen=True)
class SourceFile:
    path: str
    lines: list[str]


@dataclass(frozen=True)
class Violation:
    rule: str
    file: str
    line: int | None
    text: str
    reason: str | None = None


@dataclass
class Report:
    violations: list[Violation] = field(default_factory=list)
    excepted: list[Violation] = field(default_factory=list)
    stale_exceptions: list[Allowance] = field(default_factory=list)


JAVA_MAIN = "src/main/java/"
MIGRATION = re.compile(r"^(.+)\.(up|down)\.sql$")
ENTITY = re.compile(r"^\s*@Entity\b")
DATA = re.compile(r"^\s*@Data\b")
TRANSACTIONAL = re.compile(r"^\s*@Transactional\b")
TODO = re.compile(r"\bTODO\b")


def _code_lines(file: SourceFile) -> Iterator[tuple[int, str]]:
    comments = comment_lines(file.path, file.lines)
    for number, text in enumerate(file.lines, start=1):
        if number not in comments:
            yield number, text


def _is_main_java(file: SourceFile) -> bool:
    return file.path.startswith(JAVA_MAIN) and file.path.endswith(".java")


def entity_data(files: list[SourceFile], config: Config) -> Iterator[Violation]:
    for file in filter(_is_main_java, files):
        code = list(_code_lines(file))
        if any(ENTITY.match(text) for _, text in code):
            for number, text in code:
                if DATA.match(text):
                    yield Violation("entity-data", file.path, number, text.strip())


def transactional_outside_application(files: list[SourceFile], config: Config) -> Iterator[Violation]:
    for file in filter(_is_main_java, files):
        if config.transaction_layer in PurePosixPath(file.path).parts:
            continue
        for number, text in _code_lines(file):
            if TRANSACTIONAL.match(text):
                yield Violation("transactional-outside-application", file.path, number, text.strip())


def new_comment(files: list[SourceFile], config: Config) -> Iterator[Violation]:
    for file in files:
        for number in sorted(comment_lines(file.path, file.lines)):
            text = file.lines[number - 1]
            if not TODO.search(text):
                yield Violation("new-comment", file.path, number, text.strip())


def transactional_self_invocation(files: list[SourceFile], config: Config) -> Iterator[Violation]:
    for file in filter(_is_main_java, files):
        code = blank_comments_and_strings("\n".join(file.lines))
        for position in self_invocations(code):
            number = code.count("\n", 0, position) + 1
            yield Violation("transactional-self-invocation", file.path, number, file.lines[number - 1].strip())


def migration_pair(files: list[SourceFile], config: Config) -> Iterator[Violation]:
    migrations = {}
    for file in files:
        found = MIGRATION.search(file.path)
        if found:
            migrations[file.path] = (found.group(1), found.group(2))
    present = set(migrations.values())
    for path, (name, direction) in migrations.items():
        counterpart = "down" if direction == "up" else "up"
        if (name, counterpart) not in present:
            yield Violation("migration-pair", path, None, f"{counterpart}.sql 짝 없음")


RULES: dict[str, Callable[[list[SourceFile], Config], Iterator[Violation]]] = {
    "entity-data": entity_data,
    "transactional-outside-application": transactional_outside_application,
    "transactional-self-invocation": transactional_self_invocation,
    "new-comment": new_comment,
    "migration-pair": migration_pair,
}


def enabled_rules(config: Config) -> list[str]:
    if config.rules is None:
        return list(RULES)
    unknown = sorted(set(config.rules) - set(RULES))
    if unknown:
        raise ValueError(f"알 수 없는 규칙: {', '.join(unknown)}")
    return list(config.rules)


def check(files: list[SourceFile], config: Config) -> Report:
    report = Report()
    allowances = config.allowances
    used = set()
    for name in enabled_rules(config):
        for violation in RULES[name](files, config):
            allowance = next((a for a in allowances if _allows(a, violation)), None)
            if allowance is None:
                report.violations.append(violation)
            else:
                used.add(allowance)
                report.excepted.append(
                    Violation(violation.rule, violation.file, violation.line, violation.text, allowance.reason)
                )
    report.stale_exceptions = [a for a in allowances if a not in used]
    return report


def _allows(allowance: Allowance, violation: Violation) -> bool:
    return (
        allowance.rule == violation.rule
        and allowance.file == violation.file
        and allowance.match == violation.text
    )
