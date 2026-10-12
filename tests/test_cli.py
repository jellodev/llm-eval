import json
import subprocess

from llm_eval.cli import main

SERVICE = "src/main/java/com/example/order/application/service/OrderService.java"
ADAPTER = "src/main/java/com/example/order/adapter/out/OrderStore.java"
TRANSACTIONAL = "class A {\n  @Transactional\n}\n"


def repo(tmp_path, files):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    for path, text in files.items():
        target = tmp_path / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
    return tmp_path


def run(capsys, root, *args):
    code = main(["--root", str(root), *args])
    return code, json.loads(capsys.readouterr().out)


def test_clean_tree_passes_with_all_rules_by_default(tmp_path, capsys):
    root = repo(tmp_path, {SERVICE: TRANSACTIONAL})

    code, report = run(capsys, root)

    assert code == 0
    assert report["passed"] is True
    assert report["summary"] == {}
    assert report["rules"] == [
        "entity-data",
        "transactional-outside-application",
        "transactional-self-invocation",
        "new-comment",
        "migration-pair",
    ]


def test_violation_fails_with_location_and_summary(tmp_path, capsys):
    root = repo(tmp_path, {ADAPTER: TRANSACTIONAL})

    code, report = run(capsys, root)

    assert code == 1
    assert report["summary"] == {"transactional-outside-application": 1}
    assert report["violations"] == [
        {
            "rule": "transactional-outside-application",
            "file": ADAPTER,
            "line": 2,
            "text": "@Transactional",
        }
    ]


def test_gitignored_files_are_skipped(tmp_path, capsys):
    root = repo(tmp_path, {".gitignore": "build/\n", "build/B.java": "// generated\n"})

    code, _ = run(capsys, root)

    assert code == 0


def test_config_in_root_is_applied(tmp_path, capsys):
    root = repo(
        tmp_path,
        {
            ADAPTER: TRANSACTIONAL,
            "llm-eval.toml": 'rules = ["transactional-outside-application"]\n'
            "\n"
            "[[exception]]\n"
            'rule = "transactional-outside-application"\n'
            f'file = "{ADAPTER}"\n'
            'match = "@Transactional"\n'
            'reason = "테스트"\n',
        },
    )

    code, report = run(capsys, root)

    assert code == 0
    assert report["rules"] == ["transactional-outside-application"]
    assert report["excepted"][0]["reason"] == "테스트"


def test_explicit_config_path_overrides_root_config(tmp_path, capsys):
    root = repo(tmp_path / "project", {ADAPTER: TRANSACTIONAL})
    config = tmp_path / "other.toml"
    config.write_text('transaction_layer = "adapter"\n')

    code, _ = run(capsys, root, "--config", str(config))

    assert code == 0
