import tomllib
from dataclasses import dataclass, field
from pathlib import Path

CONFIG_FILE = "llm-eval.toml"


@dataclass(frozen=True)
class Allowance:
    rule: str
    file: str
    match: str
    reason: str


@dataclass(frozen=True)
class Config:
    rules: tuple[str, ...] | None = None
    transaction_layer: str = "application"
    allowances: list[Allowance] = field(default_factory=list)


def load_config(path: Path) -> Config:
    if not path.is_file():
        return Config()
    with path.open("rb") as source:
        data = tomllib.load(source)
    rules = data.get("rules")
    return Config(
        rules=tuple(rules) if rules is not None else None,
        transaction_layer=data.get("transaction_layer", Config.transaction_layer),
        allowances=[Allowance(**entry) for entry in data.get("exception", [])],
    )
