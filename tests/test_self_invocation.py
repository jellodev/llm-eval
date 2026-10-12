from llm_eval.config import Config
from llm_eval.rules import SourceFile, check

RULE = "transactional-self-invocation"
ONLY = Config(rules=(RULE,))
SERVICE = "src/main/java/com/example/order/application/service/OrderService.java"


def service(*lines, path=SERVICE):
    return SourceFile(path, list(lines))


def hits(file):
    return [(v.line, v.text) for v in check([file], ONLY).violations]


def test_non_transactional_method_calling_transactional_method_by_name_is_violation():
    file = service(
        "public class OrderService {",
        "  public void runAll() {",
        "    ids.forEach(id -> process(id));",
        "  }",
        "",
        "  @Transactional",
        "  public void process(Long id) {",
        "    repository.findById(id);",
        "  }",
        "}",
    )

    assert hits(file) == [(3, "ids.forEach(id -> process(id));")]


def test_call_through_this_and_method_reference_are_violations():
    file = service(
        "class OrderService {",
        "  void a() { this.process(1L); }",
        "  void b() { ids.forEach(this::process); }",
        '  @Transactional("bizTransactionManager")',
        "  public void process(Long id) {}",
        "}",
    )

    assert [line for line, _ in hits(file)] == [2, 3]


def test_call_through_another_bean_is_allowed():
    file = service(
        "class OrderService {",
        "  void runAll() { processor.process(1L); }",
        "  @Transactional",
        "  public void process(Long id) {}",
        "}",
    )

    assert hits(file) == []


def test_transactional_caller_joining_same_transaction_is_allowed():
    file = service(
        "class OrderService {",
        "  @Transactional",
        "  public void replace() { disconnect(); }",
        "  @Transactional",
        "  public void disconnect() {}",
        "}",
    )

    assert hits(file) == []


def test_class_level_transactional_makes_callers_transactional():
    file = service(
        "@Transactional",
        "class OrderService {",
        "  public void replace() { disconnect(); }",
        "  @Transactional(readOnly = true)",
        "  public void disconnect() {}",
        "}",
    )

    assert hits(file) == []


def test_propagation_on_self_invoked_method_is_violation_even_inside_transaction():
    file = service(
        "class OrderService {",
        "  @Transactional",
        "  public void outer() { inner(); }",
        "  @Transactional(propagation = Propagation.REQUIRES_NEW)",
        "  public void inner() {}",
        "}",
    )

    assert hits(file) == [(3, "public void outer() { inner(); }")]


def test_annotation_arguments_with_braces_keep_method_transactional():
    file = service(
        "class OrderService {",
        "  void runAll() { process(); }",
        "  @Transactional(rollbackFor = {IOException.class, TimeoutException.class})",
        "  public void process() throws IOException {}",
        "}",
    )

    assert [line for line, _ in hits(file)] == [2]


def test_calls_inside_comments_and_strings_are_ignored():
    file = service(
        "class OrderService {",
        "  void runAll() {",
        "    // process();",
        '    log.info("process() skipped");',
        "  }",
        "  @Transactional",
        "  public void process() {}",
        "}",
    )

    assert hits(file) == []


def test_transactional_event_listener_is_not_a_transaction_boundary():
    file = service(
        "class OrderService {",
        "  void runAll() { on(event); }",
        "  @TransactionalEventListener",
        "  public void on(Object event) {}",
        "}",
    )

    assert hits(file) == []


def test_recursion_is_not_self_invocation_violation():
    file = service(
        "class OrderService {",
        "  @Transactional",
        "  public void walk(int depth) { if (depth > 0) { walk(depth - 1); } }",
        "}",
    )

    assert hits(file) == []


def test_methods_of_nested_types_are_kept_apart():
    file = service(
        "class OrderService {",
        "  @Transactional",
        "  public void process() {}",
        "  static class Helper {",
        "    void run() { process(); }",
        "    void process() {}",
        "  }",
        "}",
    )

    assert hits(file) == []


def test_constructor_call_with_same_name_is_ignored():
    file = service(
        "class OrderService {",
        "  void runAll() { new Process(); }",
        "  @Transactional",
        "  public void Process() {}",
        "}",
    )

    assert hits(file) == []


def test_test_sources_are_not_checked():
    file = service(
        "class OrderServiceTest {",
        "  void a() { process(); }",
        "  @Transactional",
        "  void process() {}",
        "}",
        path="src/test/java/com/example/OrderServiceTest.java",
    )

    assert hits(file) == []


def test_private_helper_reached_only_from_transaction_is_inside_transaction():
    file = service(
        "class OrderService {",
        "  @Transactional",
        "  public void a() { helper(); }",
        "  private void helper() { x(); }",
        "  @Transactional",
        "  public void x() {}",
        "}",
    )

    assert hits(file) == []


def test_private_helper_reached_from_non_transactional_entry_is_violation():
    file = service(
        "class OrderService {",
        "  public void a() { helper(); }",
        "  private void helper() { x(); }",
        "  @Transactional",
        "  public void x() {}",
        "}",
    )

    assert [line for line, _ in hits(file)] == [3]


def test_non_transactional_overload_is_not_confused_with_transactional_one():
    file = service(
        "class OrderService {",
        '  void a() { save("s", 1); }',
        "  public void save(String s, int n) {}",
        "  @Transactional",
        "  public void save(Long id) {}",
        "}",
    )

    assert hits(file) == []


def test_harmless_propagation_inside_transaction_is_allowed():
    file = service(
        "class OrderService {",
        "  @Transactional",
        "  void a() { x(); }",
        "  @Transactional(propagation = Propagation.REQUIRED)",
        "  public void x() {}",
        "}",
    )

    assert hits(file) == []


def test_method_declared_in_anonymous_class_is_not_a_call():
    file = service(
        "class OrderService {",
        "  void a() { bus.register(new Handler() { public void process(Event e) {} }); }",
        "  @Transactional",
        "  public void process(Event e) {}",
        "}",
    )

    assert hits(file) == []


def test_write_transaction_called_from_read_only_class_is_violation():
    file = service(
        "@Transactional(readOnly = true)",
        "class OrderService {",
        "  public Foo getOrCreate() { return create(); }",
        "  @Transactional",
        "  public Foo create() { return null; }",
        "}",
    )

    assert [line for line, _ in hits(file)] == [3]


def test_read_only_callee_from_read_only_caller_is_allowed():
    file = service(
        "@Transactional(readOnly = true)",
        "class OrderService {",
        "  public Foo a() { return find(); }",
        "  @Transactional(readOnly = true)",
        "  public Foo find() { return null; }",
        "}",
    )

    assert hits(file) == []


def test_fully_qualified_and_jakarta_annotations_are_recognised():
    file = service(
        "class OrderService {",
        "  void a() { x(); }",
        "  @Transactional",
        "  void b() { y(); }",
        "  @org.springframework.transaction.annotation.Transactional",
        "  public void x() {}",
        "  @Transactional(Transactional.TxType.REQUIRES_NEW)",
        "  public void y() {}",
        "}",
    )

    assert [line for line, _ in hits(file)] == [2, 4]


def test_qualified_this_call_is_violation():
    file = service(
        "class OrderService {",
        "  void a() { OrderService.this.x(); }",
        "  @Transactional",
        "  public void x() {}",
        "}",
    )

    assert [line for line, _ in hits(file)] == [2]


def test_nested_parameter_annotations_keep_method_recognised():
    file = service(
        "class OrderService {",
        "  void a() { x(1L); }",
        "  @Transactional",
        '  public void x(@Parameter(schema = @Schema(type = "s")) Long id) {}',
        "}",
    )

    assert [line for line, _ in hits(file)] == [2]


def test_escaped_quotes_inside_text_block_do_not_end_it():
    file = service(
        "class OrderService {",
        '  String s = """',
        '    a \\""" b',
        '    """;',
        "  void a() { x(); }",
        "  @Transactional",
        "  public void x() {}",
        "}",
    )

    assert [line for line, _ in hits(file)] == [5]
