import pytest

from llm_eval.config import Allowance, Config
from llm_eval.rules import SourceFile, check

JAVA_MAIN = "src/main/java/com/example/"
MIGRATIONS = "db/migrations/"


def java(path, *lines):
    return SourceFile(JAVA_MAIN + path, list(lines))


def file(path, *lines):
    return SourceFile(path, list(lines))


def hits(*files, config=Config()):
    return [(v.rule, v.file, v.line) for v in check(list(files), config).violations]


def rules_hit(*files, config=Config()):
    return sorted({rule for rule, _, _ in hits(*files, config=config)})


def test_transactional_outside_application_is_violation():
    store = java("order/adapter/out/OrderStore.java", "  @Transactional(readOnly = true)")

    assert hits(store) == [("transactional-outside-application", store.path, 1)]


def test_transactional_in_application_is_allowed():
    assert rules_hit(java("order/application/service/OrderService.java", "  @Transactional")) == []


def test_transaction_layer_is_configurable():
    service = java("order/service/OrderService.java", "  @Transactional")

    assert rules_hit(service) == ["transactional-outside-application"]
    assert rules_hit(service, config=Config(transaction_layer="service")) == []


def test_transactional_event_listener_is_allowed():
    assert rules_hit(java("order/adapter/in/event/L.java", "  @TransactionalEventListener")) == []


def test_transactional_in_test_sources_is_allowed():
    assert rules_hit(file("src/test/java/com/example/OrderTest.java", "  @Transactional")) == []


def test_annotation_text_inside_comment_is_only_a_comment_violation():
    listener = java("order/adapter/in/event/L.java", "/**", "@Transactional", " */")

    assert hits(listener) == [("new-comment", listener.path, n) for n in (1, 2, 3)]


def test_comment_lines_are_violations_in_every_checked_file():
    files = [
        java("common/U.java", "// why", "class U {}"),
        file(MIGRATIONS + "000001_x.up.sql", "-- why"),
        file(MIGRATIONS + "000001_x.down.sql", "DROP TABLE x;"),
    ]

    assert hits(*files) == [
        ("new-comment", files[0].path, 1),
        ("new-comment", files[1].path, 1),
    ]


def test_up_migration_without_down_is_violation():
    up = file(MIGRATIONS + "000046_x.up.sql", "ALTER TABLE x;")

    assert hits(up) == [("migration-pair", up.path, None)]


def test_down_migration_without_up_is_violation():
    assert rules_hit(file(MIGRATIONS + "000046_x.down.sql", "ALTER TABLE x;")) == ["migration-pair"]


def test_migration_pair_is_allowed():
    up = file(MIGRATIONS + "000046_x.up.sql", "ALTER TABLE x;")
    down = file(MIGRATIONS + "000046_x.down.sql", "ALTER TABLE x;")

    assert rules_hit(up, down) == []


def test_migration_pair_must_share_directory():
    up = file("db/a/000046_x.up.sql", "ALTER TABLE x;")
    down = file("db/b/000046_x.down.sql", "ALTER TABLE x;")

    assert rules_hit(up, down) == ["migration-pair"]


def test_only_enabled_rules_run():
    files = [java("common/U.java", "// why"), file(MIGRATIONS + "1_x.up.sql", "SELECT 1;")]

    assert rules_hit(*files, config=Config(rules=("migration-pair",))) == ["migration-pair"]


def test_unknown_rule_is_rejected():
    with pytest.raises(ValueError, match="no-such-rule"):
        check([], Config(rules=("no-such-rule",)))


def test_exception_moves_matching_violation_to_excepted():
    store = java(
        "order/adapter/out/TokenStore.java",
        "  @Transactional(propagation = Propagation.REQUIRES_NEW)",
        "  @Transactional",
    )
    allowed = Allowance(
        rule="transactional-outside-application",
        file=store.path,
        match="@Transactional(propagation = Propagation.REQUIRES_NEW)",
        reason="행 락 직렬화",
    )

    report = check([store], Config(allowances=[allowed]))

    assert [(v.line, v.reason) for v in report.excepted] == [(1, "행 락 직렬화")]
    assert [v.line for v in report.violations] == [2]


def test_unused_exception_is_reported_as_stale():
    allowed = Allowance(rule="migration-pair", file="gone.up.sql", match="down.sql 짝 없음", reason="옛 예외")

    assert check([], Config(allowances=[allowed])).stale_exceptions == [allowed]


def test_todo_comment_is_allowed():
    files = [
        java("common/U.java", "// TODO(임시): 확장 ID 로 좁힐 것", "int a = 1; // TODO 정리", "// todo 소문자"),
        file("application.yml", "  # TODO(임시): 로컬 FE 개발용"),
    ]

    assert hits(*files) == [("new-comment", files[0].path, 3)]


def test_todo_inside_block_comment_allows_only_that_line():
    block = java("common/U.java", "/**", " * TODO 정리", " */")

    assert [line for _, _, line in hits(block)] == [1, 3]
