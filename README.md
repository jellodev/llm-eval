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
| `entity-data` | `@Entity` 클래스에 `@Data` |
| `transactional-outside-application` | `src/main/java` 에서 트랜잭션 레이어 패키지 밖의 `@Transactional` (`@TransactionalEventListener` 제외) |
| `transactional-self-invocation` | 같은 클래스 안에서 `@Transactional` 메서드를 `x()`·`this.x()`·`this::x` 로 호출하는데, 호출하는 쪽에 트랜잭션이 없거나, 호출받는 쪽이 새 트랜잭션(`REQUIRES_NEW`·`NOT_SUPPORTED`·`NESTED`·`NEVER`)을 요구하거나, `readOnly` 안에서 쓰기 트랜잭션을 부르는 경우. 프록시를 거치지 않아 트랜잭션 설정이 무시된다 |
| `new-comment` | 주석 줄 (Java·Gradle·SQL·YAML·properties·TOML·shell·Python). shebang 과 대문자 `TODO` 가 들어간 줄은 허용 |
| `migration-pair` | 같은 디렉터리에 짝이 없는 `*.up.sql` / `*.down.sql` |

`transactional-self-invocation` 은 파일 하나 안에서 판정한다. private 메서드는 같은 클래스의 호출자가 모두 트랜잭션 안이면 트랜잭션 안으로 본다. 인터페이스에만 붙은 `@Transactional`, inner·익명 클래스에서 바깥 클래스 메서드를 부르는 경우는 검사하지 않는다.

주석은 언어별 lexer 로 판정한다. 주석 안에 적힌 `@Transactional` 같은 텍스트는 코드 규칙에 걸리지 않는다.

## 설정

프로젝트 루트의 `llm-eval.toml` 을 읽는다(`--config` 로 다른 경로 지정 가능). 파일이 없으면 전체 규칙과 기본값으로 검사한다.

```toml
rules = ["entity-data", "transactional-outside-application", "migration-pair"]
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

## 개발

```bash
uv run pytest
uv run llm-eval
```
