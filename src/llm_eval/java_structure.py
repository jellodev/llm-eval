import re
from dataclasses import dataclass, field

NESTED_PARENS = r"(?:[^()]|\((?:[^()]|\([^()]*\))*\))*"
TYPE_DECLARATION = re.compile(r"\b(?:class|interface|enum|record)\s+\w+")
ANONYMOUS_CLASS = re.compile(r"\bnew\s+[\w.<>]+\s*\(" + NESTED_PARENS + r"\)\s*$")
METHOD_DECLARATION = re.compile(
    r"(?<![\w.])(\w+)\s*\((" + NESTED_PARENS + r")\)\s*(?:throws\s+[\w.,\s<>]+)?$"
)
SELF_CALL = re.compile(r"(?<![\w.$:])(?:(?:\w+\s*\.\s*)?this\s*\.\s*)?(\w+)\s*\(|\bthis\s*::\s*(\w+)")
TRANSACTIONAL = re.compile(r"@(?:\w+\.)*Transactional\b(\s*\(" + NESTED_PARENS + r"\))?")
NEW_TRANSACTION = re.compile(r"\b(?:REQUIRES_NEW|NOT_SUPPORTED|NESTED|NEVER)\b")
READ_ONLY = re.compile(r"\breadOnly\s*=\s*true\b")
CONSTRUCTOR_CALL = re.compile(r"\bnew\s+[\w.<>]*$")
NOT_METHODS = {"if", "for", "while", "switch", "catch", "synchronized", "try", "return", "do", "else"}


@dataclass(frozen=True)
class Boundary:
    read_only: bool
    new_transaction: bool


@dataclass(eq=False)
class Method:
    name: str
    arity: int
    private: bool
    boundary: Boundary | None
    body_start: int
    body_end: int = -1


@dataclass(eq=False)
class JavaType:
    boundary: Boundary | None
    body_start: int
    body_end: int = -1
    methods: list[Method] = field(default_factory=list)


def blank_comments_and_strings(source: str) -> str:
    out = list(source)
    i, n = 0, len(source)

    def blank(start: int, end: int) -> None:
        for k in range(start, min(end, n)):
            if out[k] != "\n":
                out[k] = " "

    while i < n:
        if source.startswith("//", i):
            end = source.find("\n", i)
            end = n if end < 0 else end
        elif source.startswith("/*", i):
            end = source.find("*/", i + 2)
            end = n if end < 0 else end + 2
        elif source.startswith('"""', i):
            end = _text_block_end(source, i + 3)
        elif source[i] in "\"'":
            end = _string_end(source, i)
        else:
            i += 1
            continue
        blank(i, end)
        i = end
    return "".join(out)


def _text_block_end(source: str, k: int) -> int:
    while k < len(source):
        if source[k] == "\\":
            k += 2
        elif source.startswith('"""', k):
            return k + 3
        else:
            k += 1
    return len(source)


def _string_end(source: str, start: int) -> int:
    quote, k = source[start], start + 1
    while k < len(source) and source[k] != quote and source[k] != "\n":
        k += 2 if source[k] == "\\" else 1
    return k + 1


def parse_types(code: str) -> list[JavaType]:
    types: list[JavaType] = []
    stack: list[tuple[str, object, int]] = []
    header_start = 0
    parens = 0
    for i, char in enumerate(code):
        if char == "(":
            parens += 1
        elif char == ")":
            parens -= 1
        elif char == ";" and parens == 0:
            header_start = i + 1
        elif char == "{":
            kind, frame = _open(code[header_start:i], i, parens, stack, types)
            stack.append((kind, frame, parens))
            parens = 0
            if kind != "inline":
                header_start = i + 1
        elif char == "}" and stack:
            kind, frame, parens = stack.pop()
            if frame is not None:
                frame.body_end = i
            if kind != "inline":
                header_start = i + 1
    return types


def _open(header: str, position: int, parens: int, stack: list, types: list[JavaType]) -> tuple[str, object]:
    if ANONYMOUS_CLASS.search(header) or (parens == 0 and TYPE_DECLARATION.search(header)):
        java_type = JavaType(_boundary(header), position)
        types.append(java_type)
        return "type", java_type
    if parens > 0:
        return "inline", None
    if stack and stack[-1][0] == "type":
        found = METHOD_DECLARATION.search(header.strip())
        if found and found.group(1) not in NOT_METHODS and not CONSTRUCTOR_CALL.search(header[: header.rfind(found.group(1))]):
            method = Method(
                name=found.group(1),
                arity=count_arguments(found.group(2)),
                private=re.search(r"\bprivate\b", header) is not None,
                boundary=_boundary(header),
                body_start=position,
            )
            stack[-1][1].methods.append(method)
            return "method", method
    return "block", None


def _boundary(header: str) -> Boundary | None:
    annotation = TRANSACTIONAL.search(header)
    if annotation is None:
        return None
    arguments = annotation.group(1) or ""
    return Boundary(READ_ONLY.search(arguments) is not None, NEW_TRANSACTION.search(arguments) is not None)


def count_arguments(text: str, generics: bool = True) -> int:
    if not text.strip():
        return 0
    opening, closing = ("(<[{", ")>]}") if generics else ("([{", ")]}")
    depth, count = 0, 1
    for char in text:
        if char in opening:
            depth += 1
        elif char in closing:
            depth -= 1
        elif char == "," and depth == 0:
            count += 1
    return count


def self_invocations(code: str) -> list[int]:
    types = parse_types(code)
    found = []
    for java_type in types:
        nested = [t for t in types if t is not java_type and java_type.body_start < t.body_start < java_type.body_end]
        contexts = _contexts(java_type, nested, code)
        for caller in java_type.methods:
            context = contexts[caller]
            for position, callee in _calls(java_type, caller, nested, code):
                if callee is caller:
                    continue
                if context is None or callee.boundary.new_transaction or (context.read_only and not callee.boundary.read_only):
                    found.append(position)
    return sorted(found)


def _calls(java_type: JavaType, caller: Method, nested: list[JavaType], code: str):
    for call in SELF_CALL.finditer(code, caller.body_start, caller.body_end):
        if any(t.body_start < call.start() < t.body_end for t in nested):
            continue
        if CONSTRUCTOR_CALL.search(code[caller.body_start : call.start()]) or _is_declaration(code, caller.body_start, call):
            continue
        name = call.group(1) or call.group(2)
        named = [m for m in java_type.methods if m.name == name]
        if call.group(1):
            arity = count_arguments(code[call.end() : _closing_paren(code, call.end() - 1)], generics=False)
            named = [m for m in named if m.arity == arity]
        if named and all(m.boundary is not None for m in named):
            yield call.start(), named[0]


def _is_declaration(code: str, start: int, call: re.Match) -> bool:
    before = re.search(r"(?<!-)([\w>\]]+)\s*$", code[start : call.start()])
    return before is not None and before.group(1) not in {"return", "throw", "else", "case", "yield", "assert", "do", "new"}


def _closing_paren(code: str, opening: int) -> int:
    depth = 0
    for k in range(opening, len(code)):
        if code[k] == "(":
            depth += 1
        elif code[k] == ")":
            depth -= 1
            if depth == 0:
                return k
    return len(code)


def _contexts(java_type: JavaType, nested: list[JavaType], code: str) -> dict[Method, Boundary | None]:
    callers: dict[Method, list[Method]] = {m: [] for m in java_type.methods}
    for caller in java_type.methods:
        for _, callee in _calls_any(java_type, caller, nested, code):
            callers[callee].append(caller)
    resolved: dict[Method, Boundary | None] = {}

    def context(method: Method, visiting: frozenset) -> Boundary | None:
        if method in resolved:
            return resolved[method]
        own = method.boundary or java_type.boundary
        if own is not None or not method.private or not callers[method] or method in visiting:
            result = own
        else:
            inherited = [context(c, visiting | {method}) for c in callers[method] if c is not method]
            result = None if not inherited or None in inherited else Boundary(all(b.read_only for b in inherited), False)
        if not visiting:
            resolved[method] = result
        return result

    return {m: context(m, frozenset()) for m in java_type.methods}


def _calls_any(java_type: JavaType, caller: Method, nested: list[JavaType], code: str):
    for call in SELF_CALL.finditer(code, caller.body_start, caller.body_end):
        if any(t.body_start < call.start() < t.body_end for t in nested) or _is_declaration(code, caller.body_start, call):
            continue
        name = call.group(1) or call.group(2)
        for method in java_type.methods:
            if method.name == name and method.private:
                yield call.start(), method
