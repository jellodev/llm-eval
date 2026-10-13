# llm-eval

Java(Spring) 프로젝트를 정적 규칙으로 채점하는 CLI. LLM 이 만든 코드가 프로젝트 규칙을 지켰는지, 그리고 코드베이스 전체가 지금 규칙을 지키고 있는지를 같은 방식으로 검사한다.

변경분(diff)이 아니라 **git 추적 파일 + gitignore 되지 않은 새 파일 전체**를 검사한다. 변경분만 보면 기존 위반이 숨기 때문이다.

규칙 채점 다음 단계(골든 과제·Judge)의 계획은 [ROADMAP.md](ROADMAP.md).

## 실행

```bash
uvx --from git+https://github.com/jellodev/llm-eval@<태그> llm-eval --root path/to/project
```

- 결과는 JSON 으로 stdout 에 출력되고, 위반이 있으면 종료 코드는 `1` 이다.
- 버전은 태그로 고정해서 쓴다. 채점기가 바뀌어도 프로젝트마다 원할 때 올리면 된다.

## 규칙

| 규칙 | 위반 조건 |
|---|---|
| `transactional-outside-application` | `src/main/java` 에서 트랜잭션 레이어 패키지 밖의 `@Transactional` (`@TransactionalEventListener` 제외) |
| `transactional-self-invocation` | 같은 클래스 안에서 `@Transactional` 메서드를 `x()`·`this.x()`·`this::x` 로 호출하는데, 호출하는 쪽에 트랜잭션이 없거나, 호출받는 쪽이 새 트랜잭션(`REQUIRES_NEW`·`NOT_SUPPORTED`·`NESTED`·`NEVER`)을 요구하거나, `readOnly` 안에서 쓰기 트랜잭션을 부르는 경우. 프록시를 거치지 않아 트랜잭션 설정이 무시된다 |
| `new-comment` | 주석 줄 (Java·Gradle·SQL·YAML·properties·TOML·shell·Python). shebang 과 대문자 `TODO` 가 들어간 줄은 허용 |
| `migration-pair` | 같은 디렉터리에 짝이 없는 `*.up.sql` / `*.down.sql` |

`transactional-self-invocation` 은 파일 하나 안에서 판정한다. private 메서드는 같은 클래스의 호출자가 모두 트랜잭션 안이면 트랜잭션 안으로 본다. 인터페이스에만 붙은 `@Transactional`, inner·익명 클래스에서 바깥 클래스 메서드를 부르는 경우는 검사하지 않는다.

주석은 언어별 lexer 로 판정한다. 주석 안에 적힌 `@Transactional` 같은 텍스트는 코드 규칙에 걸리지 않는다.

## 설정

프로젝트 루트의 `llm-eval.toml` 을 읽는다(`--config` 로 다른 경로 지정 가능). 파일이 없으면 전체 규칙과 기본값으로 검사한다.

```toml
rules = ["transactional-outside-application", "migration-pair"]
transaction_layer = "application"

[[exception]]
rule = "transactional-outside-application"
file = "src/main/java/com/example/TokenStore.java"
match = "@Transactional(propagation = Propagation.REQUIRES_NEW)"
reason = "유스케이스 없이 어댑터가 직접 호출하는 행 락 경계"
```

- `rules` — 켤 규칙. 생략하면 전체이고, 모르는 이름을 적으면 실행을 거부한다.
- `transaction_layer` — `@Transactional` 을 허용할 패키지 이름. 기본값은 `application`.
- `[[exception]]` — 허용 예외. `file` 이 같고, 공백을 뺀 줄 내용이 `match` 와 같은 위반이 `excepted` 로 옮겨진다. 어떤 위반과도 맞지 않는 예외는 `stale_exceptions` 로 보고된다.

## 출력

```json
{
  "passed": false,
  "rules": ["transactional-outside-application"],
  "summary": {"transactional-outside-application": 1},
  "violations": [{"rule": "...", "file": "...", "line": 2, "text": "@Transactional"}],
  "excepted": [{"rule": "...", "file": "...", "line": 48, "text": "...", "reason": "..."}],
  "stale_exceptions": []
}
```

## Judge (3단계)

규칙으로 가를 수 없는 결함을 LLM Judge 로 판정하고, 사람 라벨(골드 라벨)과의 일치율을 잰다. 판정 결과로 PR 을 막지 않는다 — 일치율이 측정되지 않은 Judge 점수는 쓰지 않는다([ROADMAP](ROADMAP.md)).

```bash
llm-eval judge --set eval/gold.json --rubric eval/rubric.md --out eval/runs/v1
```

- `--set` — 표본 목록 `[{"id": "31", "sha": "<커밋>", "label": "정상" | "결함" | null}]`. `label` 이 없는 표본은 판정만 하고 일치율에서 뺀다.
- `--rubric` — Judge 의 시스템 프롬프트. 항목별 판정(`items`)과 최종 `verdict`(`정상`·`결함`)·`reason` 을 JSON 으로 받는다.
- `--out` — 표본별 판정(`<id>.json`)과 `summary.json`. 같은 sha 의 판정이 이미 있으면 다시 부르지 않는다.

Judge 는 `claude -p` 를 도구 없이(`--tools ""`), 설정·CLAUDE.md 를 읽지 않게(`--setting-sources ""`) 빈 임시 디렉터리에서 부른다. 보는 것은 다음 세 가지뿐이다.

- `[judge] context` 에 적은 파일의 **커밋 직전(`<sha>^`) 내용** — 나중에 생긴 규칙으로 과거 커밋을 판정하지 않도록
- 커밋 메시지
- 커밋 diff (`[judge] exclude` 패턴 제외)

```toml
[judge]
context = ["CLAUDE.md", "backend/CLAUDE.md"]
exclude = ["**/openapi.json"]
model = "sonnet"
timeout_seconds = 600
```

`context` 경로는 저장소 루트 기준이다. 출력은 혼동표(`tp`·`fn`·`fp`·`tn`, 결함이 양성), 정확도, Cohen κ, 비용, 사람과 다른 판정 목록(`disagreements`), 실패 목록(`failed`)이다. 판정에 실패한 표본이 있으면 종료 코드는 `1` 이다.

## 개발

```bash
uv run pytest
uv run llm-eval
```
