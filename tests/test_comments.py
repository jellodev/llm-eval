from llm_eval.comments import comment_lines


def test_java_line_block_and_trailing_comments():
    lines = [
        "/**",
        " * doc",
        " */",
        "class A {",
        "  int a = 1; // trailing",
        "  int b = 2; /* inline */ int c = 3;",
        "}",
    ]

    assert comment_lines("A.java", lines) == {1, 2, 3, 5, 6}


def test_java_comment_markers_inside_strings_are_ignored():
    lines = [
        '  String url = "https://example.com/*";',
        "  char c = '/';",
        '  String block = """',
        "      // not a comment",
        '      """;',
    ]

    assert comment_lines("A.java", lines) == set()


def test_java_escaped_quote_does_not_end_string():
    assert comment_lines("A.java", ['  String s = "a\\" // b";']) == set()


def test_gradle_uses_java_style():
    assert comment_lines("build.gradle", ["// note", "id 'java'"]) == {1}


def test_sql_dash_and_block_comments():
    lines = ["-- why", "SELECT '--' FROM t; -- tail", "/* a", "b */", "SELECT 1;"]

    assert comment_lines("x.up.sql", lines) == {1, 2, 3, 4}


def test_hash_comments_in_yaml_python_shell():
    assert comment_lines("a.yml", ["# top", "  key: v # tail", "  color: '#fff'", "  url: a#b"]) == {
        1,
        2,
    }
    assert comment_lines("a.py", ["x = 1  # note", 's = "#"']) == {1}


def test_shebang_is_not_a_comment():
    assert comment_lines("run.sh", ["#!/usr/bin/env bash", "# note"]) == {2}


def test_unsupported_extensions_are_not_checked():
    assert comment_lines("README.md", ["# 제목"]) == set()
    assert comment_lines("openapi.json", ["{}"]) == set()
