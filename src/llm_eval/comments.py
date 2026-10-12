from dataclasses import dataclass
from pathlib import PurePosixPath


@dataclass(frozen=True)
class Syntax:
    line_marker: str
    block: tuple[str, str] | None
    quotes: str
    text_block: str | None
    marker_after_space: bool


C_LIKE = Syntax("//", ("/*", "*/"), "\"'", '"""', False)
SQL = Syntax("--", ("/*", "*/"), "'\"", None, False)
HASH = Syntax("#", None, "'\"", None, True)
PYTHON = Syntax("#", None, "'\"", '"""', False)

SYNTAX_BY_SUFFIX = {
    ".java": C_LIKE,
    ".gradle": C_LIKE,
    ".kts": C_LIKE,
    ".sql": SQL,
    ".yml": HASH,
    ".yaml": HASH,
    ".properties": HASH,
    ".toml": HASH,
    ".sh": HASH,
    ".py": PYTHON,
}


def comment_lines(path: str, lines: list[str]) -> set[int]:
    syntax = SYNTAX_BY_SUFFIX.get(PurePosixPath(path).suffix)
    if syntax is None:
        return set()
    found = set()
    in_block = False
    in_text_block = False
    for number, line in enumerate(lines, start=1):
        if number == 1 and line.startswith("#!"):
            continue
        is_comment = in_block
        i = 0
        while i < len(line):
            if in_block:
                end = line.find(syntax.block[1], i)
                in_block = end < 0
                i = len(line) if in_block else end + len(syntax.block[1])
            elif in_text_block:
                end = line.find(syntax.text_block, i)
                in_text_block = end < 0
                i = len(line) if in_text_block else end + len(syntax.text_block)
            elif syntax.text_block and line.startswith(syntax.text_block, i):
                in_text_block = True
                i += len(syntax.text_block)
            elif line[i] in syntax.quotes:
                i = _string_end(line, i) + 1
            elif line.startswith(syntax.line_marker, i) and _marker_allowed(syntax, line, i):
                is_comment = True
                break
            elif syntax.block and line.startswith(syntax.block[0], i):
                is_comment = True
                in_block = True
                i += len(syntax.block[0])
            else:
                i += 1
        if is_comment:
            found.add(number)
    return found


def _marker_allowed(syntax: Syntax, line: str, i: int) -> bool:
    return not syntax.marker_after_space or i == 0 or line[i - 1].isspace()


def _string_end(line: str, start: int) -> int:
    quote = line[start]
    i = start + 1
    while i < len(line):
        if line[i] == "\\":
            i += 2
        elif line[i] == quote:
            return i
        else:
            i += 1
    return len(line)
