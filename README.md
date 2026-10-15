# KnitCode — 코드 구조 분석기 (v2.3)

Python 프로젝트의 함수·클래스 호출 관계를 AST로 추출해서, **파일 경로가 포함된 고유 ID**를 가진 그래프(JSON)로 만드는 분석기입니다.
프로젝트 안으로 이어지는 호출은 연결하고, 프로젝트 밖으로 나가거나 못 푼 호출은 **왜 못 풀었는지 분류**해서 기록합니다.
이후 Git 이력·테스트 분석과 결합해 "코드를 수정하면 어디까지 영향이 가는지"를 보여주는 KnitCode의 분석 엔진 파트입니다.

---

## 빠른 시작

```bash
python analyze_project.py <분석할 프로젝트 폴더>     # 분석 (저장소 루트를 그대로 넣어도 됩니다)
python tests/test_analyzer.py                       # 회귀 테스트 (36개)
```

- Python **3.9 이상** (`ast.unparse` 사용), 별도 패키지 설치 없음
- 결과: 터미널 요약 + `graph_<프로젝트명>.json` (프로젝트마다 따로 저장되어 서로 덮어쓰지 않음)
- 저장소 루트를 분석하면 `tests/`, `docs/`도 함께 분석되고, 패키지가 `src/` 아래에 있어도 `tests/`의 절대 import가 연결됩니다 (v2.2)
- 결과 그래프에는 호출 간선뿐 아니라 **모듈 `imports` / 클래스 `extends` / 소속 `contains`** 구조 간선이 함께 들어 있습니다 (v2.3)
- 노드마다 **줄 범위(`end_lineno`)·시그니처·docstring·데코레이터·복잡도** 같은 메타데이터가 붙고, **중첩 함수·클래스도 노드**로 수집되며, 콜백·데코레이터·property·`Foo()`→`__init__`·`with` 같은 **호출이 아닌 연결**도 간선으로 들어 있습니다
- **그래프용 정보**도 함께 나옵니다: import 간선의 종류와 순환 import, `from x import *` 확장, 진입점(`entry_points`)과 진입점에서의 호출 거리(`entry_depth`), 폴더 → 파일 → 클래스 → 함수로 집계한 가중치 그래프(`hierarchy`)
- 파싱하지 못해 건너뛴 파일은 터미널에 경고로 나오고 JSON의 `skipped_files`에도 기록됩니다 (Python 3.9로 3.10+ 문법을 쓴 파일을 분석하면 빠질 수 있습니다)
- 표준 라이브러리 판별이 환경에서 제대로 되는지 확인하는 한 줄 (결과가 `[True, True, False, True, True]`이면 정상)

```bash
python -c "import analyze_project as a; print([a.is_stdlib(x) for x in ('os','math','requests','urllib','typing')])"
```

---

## 버전별 변화

| 구분 | v1 | v2 | v2.1 | v2.2 | v2.3 (현재) |
|---|---|---|---|---|---|
| 미해결 호출 | 한 덩어리 | 원인별 8개 분류, 외부 호출은 외부 노드로 연결 | + 타입 추적으로 미확정 해소 | 동일 | 동일 |
| 상속 | 자식 → 부모 | + 부모 → 자식 오버라이드 후보 | 동일 | 동일 | + **`extends` 구조 간선**, 외부 부모는 노드의 `external_bases` |
| 수신 객체 타입 | `x = Foo()` 만 | 동일 | + 타입힌트, 외부 호출 결과, `self.속성` | + 생성자 직후 `Foo(...).method()` | + **프로젝트 함수의 반환 타입힌트**, **모듈 변수**, **표준 라이브러리 반환표** |
| 분석 대상 | 패키지 폴더 | 동일 | 동일 | 저장소 루트 (tests/ 포함), src 레이아웃 | 동일 |
| import 해석 | 절대·상대 | + 재수출(`compat.py`) | 동일 | + 패키지 재수출, 점 표기 모듈 트리 | + **모듈 변수 재수출** (`requests.codes`), **`imports` 구조 간선** |
| 그래프 구조 | 호출만 | 동일 | 동일 | 동일 | **모듈 노드 + `contains` / `imports` / `extends` 간선** |
| 이름 가림 | 고려 안 함 | 동일 | 동일 | 지역 이름이 import를 가리면 연결하지 않음 | 모듈 변수에도 적용 |
| 검증 방법 | 숫자 비교 | 숫자 비교 | 회귀 테스트 | + src 레이아웃 정답지, 저장소 루트 기준선 | + 타입 정답지(함정 포함), 구조 간선 정답지 |

> **이전 버전의 내부 연결은 하나도 사라지지 않았습니다.** v2.3은 v2.2와 비교해 Sublist3r, requests(`src/requests`, 저장소 루트) 모두 사라진 호출 간선이 0건이고, 새로 생긴 간선만 추가되었습니다.

### "해석률"에서 "분류 완료율"로 바꾼 이유

v1의 해석률은 분모에 `print`, `str.split`처럼 **원래 프로젝트 안 호출이 아닌 것**까지 섞여 있어서 실제보다 훨씬 낮아 보였습니다.

```
분류 완료율 = (프로젝트 내부로 연결된 호출 + 외부로 분류된 호출) / 전체 호출
```

신뢰도에 따라 3단계로 나누어 봅니다.

| 단계 | 포함되는 것 |
|---|---|
| 확실 | 코드만 보고 결정된 것 (직접 호출, 상속, import, 내장 이름) |
| + 타입 추적 | 변수·속성의 타입을 추적해서 결정된 것 (`confidence: inferred`) |
| + 이름 추정 | 변수 타입은 모르지만 메서드 이름이 str/list/dict 것이라고 추정한 것 (`heuristic`, `weak`) |

---

## 분석 원리

1. **파일 파싱**: 폴더를 순회하며 `.py`를 AST로 파싱 (`.git`, `venv` 등 제외)
2. **정의 수집**: 함수·클래스·메서드마다 `파일경로::클래스명.함수명` 형태의 고유 ID 부여
3. **import 해석**: `import x`, `from x import y`, 상대 import를 해석하고, 재수출(re-export)을 끝까지 따라감
4. **import 루트 보정** (v2.2): `__init__.py`를 거슬러 올라가 패키지 최상위를 찾아, 코드가 쓰는 이름(`requests`)과 폴더 기준 이름(`src.requests`)을 연결
5. **상속 관계 수집**: 프로젝트 안 부모 / 프로젝트 밖 부모를 나누어 기록
6. **호출 해석**: 호출문이 프로젝트 안의 어느 함수인지 추적. 중첩 함수·중첩 클래스의 메서드도 각자의 노드로 분석하고, 호출 지점은 가장 안쪽 함수에서 한 번만 센다
7. **타입 추적** (v2.1 → v2.3): 수신 객체의 타입을 알아내 못 푼 호출을 한 번 더 해석 (타입힌트, 생성자, 반환 타입힌트, 모듈 변수, 표준 라이브러리 반환표)
8. **실패한 호출 분류**: 못 푼 호출을 아래 분류 체계로 구분
9. **오버라이드 후보 연결**: 자식 클래스가 덮어쓴 메서드까지 연결
10. **구조 간선** (v2.3): 모듈 `imports`, 클래스 `extends`, 소속 `contains`를 그래프에 추가
11. **참조·암묵 호출 간선**: 호출문이 아닌 연결(콜백, 데코레이터, property, `Foo()`→`__init__`, `with`)을 추가

---

## 저장소 루트와 src 레이아웃 지원 (v2.2)

### 문제

`requests`는 패키지가 `src/requests/`에 있고, 테스트는 `from requests.utils import super_len`처럼 씁니다.
분석기는 폴더 기준으로 모듈 이름을 `src.requests.utils`라고 알고 있어서 둘이 연결되지 않았고, 저장소 루트를 분석하면 **`tests/`의 호출 492건이 "프로젝트 안 이름인데 못 찾음"** 으로 남았습니다. "수정하면 어떤 테스트를 돌려야 하는지"(테스트 관계)에 필요한 `tests → 코드` 연결이 끊겨 있던 것입니다.

### 해결

| 변경 | 설명 | 예시 |
|---|---|---|
| import 루트 보정 | `__init__.py` 체인으로 패키지 최상위를 찾아 이름을 연결. `__init__.py`가 없는 폴더(네임스페이스 패키지)는 건드리지 않고, 같은 이름의 패키지가 둘 이상이면 믿지 않음 | `requests` → `src.requests` |
| 패키지 재수출 추적 | `__init__.py`가 안쪽 모듈에서 가져와 다시 내보내는 이름을 실제 정의로 연결 | `requests.get` → `api.get`, `from requests import Session` |
| 점 표기 모듈 트리 | 모듈·클래스·메서드를 점 표기를 따라가며 해석 | `mypkg.utils.helper()`, `mypkg.models.Client` |
| 생성자 직후 메서드 | 생성자 호출 결과에 바로 붙은 메서드는 타입이 확실 | `Request(...).prepare()` |
| 이름 가림 수정 | 지역 변수·매개변수가 import한 이름을 가리면 그쪽으로 연결하지 않음 (예전부터 있던 결함) | `mypkg = object(); mypkg.fetch()` |

### 효과 (requests 저장소 루트)

| | v2.1 | v2.2 |
|---|---|---|
| 내부 연결 | 339 | **970** |
| 프로젝트 안 이름을 못 찾음 (`project_unresolved`) | 492 | **11** |
| 분류 완료율 (확실) | 47.2% | **72.0%** |

- 새로 생긴 간선 중 무작위 25개와 생성자 직후 메서드 연결 20건을 소스에서 열어 확인했고 **모두 올바른 연결**이었습니다.
- 새로 생긴 연결의 거의 전부가 `tests/ → src/` 방향입니다.
- 이름 가림 수정은 실제 프로젝트에서는 영향이 0건이었고(예방용), 정답지 테스트로 동작을 고정했습니다.

---

## v2.3 : 반환 타입 · 모듈 변수 · 표준 라이브러리 표 · 구조 간선

### 1) 프로젝트 함수의 반환 타입힌트

`resp = self.send()` 에서 `resp` 가 무엇인지는 `send` 의 `-> Response` 가 알려 줍니다. 호출 결과에 바로 붙은 메서드(`requests.post(url).json()`)도 같은 방식으로 풉니다.

| 코드 | 알아내는 것 | via |
|---|---|---|
| `resp = c.send()` → `resp.json()` | `send` 의 반환 힌트 `Resp` → `Resp.json` | `local_assign` |
| `c.send().json()` | 호출 결과의 타입 → `Resp.json` | `return_type` |
| `def chain(self) -> Self` → `c.chain().ping()` | `Self` = 그 클래스 | `return_type` |
| `make_client().ping()` / `models.make_client().ping()` | 함수의 반환 힌트 | `return_type` |
| `-> "Client"`, `-> Optional[Resp]` | 문자열 힌트, `Optional` 은 벗겨서 본다 | |

반환값은 **선언된 타입**이라 실제 객체는 자식 클래스일 수 있습니다. 그래서 `declared` 표시를 붙이고, 자식이 오버라이드한 메서드를 `calls_override` 로 함께 연결합니다 (매개변수 타입힌트와 같은 규칙).

**믿지 않는 경우**: `async def` (코루틴을 돌려줌), `@property`와 알 수 없는 데코레이터가 붙은 함수 (`staticmethod`/`classmethod`/`abstractmethod` 만 허용), 같은 이름이 둘 이상 정의된 함수, 반환 힌트가 `Any`/`Callable`/`Iterator` 처럼 구체 타입이 아닌 경우.

### 2) 모듈 수준 변수의 타입

```python
# status_codes.py
codes = LookupDict(name="status_codes")
# 다른 파일
requests.codes.get("TOO_EARLY")        # LookupDict.get 으로 연결 (via: module_var)
from requests import codes; codes.get(...)
```

- 모듈 최상위에서 **딱 한 번**, 단순 대입(`X = ...`, `X: T = ...`)으로만 정의된 변수만 인정합니다. 재대입, `import`/`for`/`with`/`def`/`class`와 이름 겹침, 다른 함수의 `global X` 수정이 있으면 포기합니다.
- `from .status_codes import codes` 처럼 패키지가 변수를 재수출해도 끝까지 따라갑니다.
- 함수 안에서 같은 이름의 매개변수·지역 변수가 있으면 그쪽이 우선이고, 모듈 변수로 연결하지 않습니다 (이름 가림).
- 같은 한 번만 대입 규칙을 `builtin_str = str` 같은 내장 별칭 판별에도 적용했습니다 (예전에는 나중에 바뀌는 변수도 내장으로 분류했습니다).

### 3) 표준 라이브러리 반환 타입표

소문자 함수는 반환 타입을 알 수 없어서 포기했었는데, 자주 쓰는 것만 표로 확정했습니다.

| 반환 | 함수 예시 |
|---|---|
| 표준 라이브러리 객체 | `re.compile/match/search`, `socket.socket`, `collections.defaultdict/deque`, `logging.getLogger`, `datetime.datetime.now`, `urllib.parse.urlparse`, `subprocess.run`, `hashlib.sha256`, `sqlite3.connect` ... |
| 내장 타입 (str/bytes/list) | `os.path.join/basename/abspath`, `os.getcwd`, `json.dumps`, `base64.b64encode`, `shlex.split` ... |
| 외부 패키지 | `requests.get/post/...` → `requests` |

- 내장 함수는 **타입을 만드는 함수만** 인정합니다 (`str`, `list`, `len`, `open`, `sorted` ...). `next()`, `getattr()`, `max()`, `iter()` 처럼 무엇이든 돌려줄 수 있는 함수의 결과는 타입을 부여하지 않습니다 (v2.2까지는 이 결과를 내장 타입으로 잘못 분류했습니다).
- 타입 힌트 중 `List/Dict/Set/Tuple` 만 내장 타입으로 보고, `Any`, `object`, `Callable`, `Iterator`, `Mapping` 등은 구체 타입을 모르는 것으로 취급합니다. 이전에는 `x: Any` 가 "표준 라이브러리 `typing`"으로 분류되던 문제가 있었습니다. `Final[X]`, `ClassVar[X]`, `Annotated[X, ...]` 는 안쪽 `X` 를 봅니다.

### 4) 구조 간선

| 간선 | 방향 | 내용 |
|---|---|---|
| `contains` | 모듈 → 함수·클래스, 클래스 → 메서드 | 노드의 `parent` 와 같은 정보 |
| `extends` | 클래스 → 부모 클래스 | 프로젝트 안의 부모. 프로젝트 밖 부모는 클래스 노드의 `external_bases` 에 이름으로 기록 |
| `imports` | 모듈 → 모듈 | 프로젝트 안 파일끼리의 import (`from pkg import submodule` 은 그 모듈로, `from module import name` 은 그 모듈로). 같은 대상 import 문 수를 `count` 에 기록, 자기 자신은 제외 |

모듈은 새 노드(`type: "module"`, ID는 파일 경로)로 들어가며, `summary.nodes` 는 모듈을 뺀 수(함수·클래스·메서드)이고 모듈 수는 `summary.module_nodes` 입니다.

### 효과

| | v2.2 | v2.3 |
|---|---|---|
| Sublist3r 미확정 | 37건 | **18건** (정규식 객체 `link_regx.findall` 12건, 소켓 `s.connect` 등 해소) |
| requests (`src/requests`) 내부 연결 / 미확정 | 266 / 82 | **270 / 75** (이후 중첩 정의 수집으로 275 / 70) |
| requests 저장소 루트 내부 연결 / 미확정 | 970 / 450 | **1026 / 370** (이후 1032 / 364) |
| `project_unresolved` (저장소 루트) | 11건 | **1건** |
| 사라진 호출 간선 | - | **0건** |

- 저장소 루트에서 새로 생긴 호출 간선 57개를 전부 소스 줄과 대조했고 **모두 올바른 연결**이었습니다 (`Response.json` 호출, `Session.get`, `requests.codes.get`, `adapter.send` 오버라이드 포함).
- 타입 추적 방식이 더 엄격해진 곳에서는 **외부 분류가 일부 바뀌었습니다** (호출 간선은 그대로). 예: `self.raw.close` 가 "표준 라이브러리 typing"으로 분류되던 것(`raw: Any` 때문)과 `DEFAULT_PORTS.get` 이 확실(`certain`)로 분류되던 것(나중에 바뀔 수 있는 변수)이 바르게 고쳐졌고, 대신 `OrderedDict` 와 `Mapping` 이 섞인 `self.adapters.items` 처럼 타입을 증명할 수 없는 7건이 미확정으로 돌아왔습니다.
- 진짜로 줄어든 것은 Sublist3r(표준 라이브러리 반환표)와 requests 저장소 루트(반환 힌트, 모듈 변수)입니다. 타입힌트가 거의 없는 코드에서는 반환 힌트의 효과가 작습니다.

---

## 노드 메타데이터 · 중첩 정의 · 참조 간선

### 노드 메타데이터

시각화(크기·색·툴팁)와 Git 연동(커밋의 변경 줄 → 어느 함수인지)에 필요한 정보입니다.

| 필드 | 대상 | 내용 |
|---|---|---|
| `line`, `end_lineno`, `loc` | 함수·클래스·모듈 | 줄 범위와 줄 수. Git의 변경 줄을 함수 단위로 대응시킬 때 사용 |
| `name`, `parent` | 전부 | 짧은 이름, 소속 노드 ID (`contains` 간선과 같은 정보) |
| `signature` | 함수·메서드 | `(self, url: str, *args) -> Response` |
| `docstring` | 전부 | 첫 줄만 (120자) |
| `decorators` | 함수·클래스 | `["staticmethod", "app.route('/')"]` 처럼 원문 |
| `is_async`, `visibility` | 함수·클래스 | 공개 `public` / `_private` / `__dunder__` |
| `kind` | 메서드 | `instance` / `static` / `class` / `property` |
| `complexity` | 함수·메서드 | 순환 복잡도 = 1 + (if, for, while, except, 삼항, and/or, 컴프리헨션의 for·if, match case). 별도 노드가 된 중첩 함수의 본문은 세지 않음 |
| `bases` / `external_bases` | 클래스 | 부모 클래스 표현식 / 프로젝트 밖 부모 이름 |
| `nested` | 중첩 정의 | 함수·클래스 안에 정의된 것이면 `true` |

같은 이름의 함수가 두 번 정의되면(property의 getter/setter 등) 노드 하나에 **마지막 정의**의 정보가 담깁니다.

### 중첩 함수·클래스 수집

- ID는 파이썬의 qualname과 같은 모양입니다: `파일::바깥.<locals>.안쪽`, 중첩 클래스는 `파일::Outer.Inner`, 그 메서드는 `파일::Outer.Inner.go`
- **호출은 가장 안쪽 함수의 것**입니다. 중첩 함수 안의 `a.b()`는 바깥 함수가 아니라 그 중첩 함수에서 나가는 간선이고, 총 호출 수는 그대로입니다.
- 바깥 함수의 호출 `inner()`는 `calls` 간선(`via: closure`)으로 연결합니다. 단, 그 이름이 **한 번만** 바인딩될 때만 (`f = other`로 다시 대입되면 포기)
- 안쪽 함수는 바깥 함수의 변수 타입을 물려받습니다 (클로저). 안쪽에서 같은 이름을 따로 바인딩하면 물려받지 않습니다.
- `if/else`에 같은 이름으로 두 번 정의된 중첩 함수처럼 어느 쪽인지 알 수 없는 정의는 수집하지 않고 `nested_defs_not_collected`로 셉니다.
- 중첩 클래스의 부모가 같은 범위에 정의된 클래스라서 이름으로 찾을 수 없으면 '외부 부모'로 단정하지 않습니다 (잘못된 `inherited_external` 방지).

### 호출이 아닌 연결

`summary.nodes`나 `internal_resolved`에는 **포함되지 않는** 별도 간선입니다. 같은 호출 지점은 `calls` 한 번으로만 세고, 아래는 "그 코드가 실제로 실행하거나 가리키는 대상"을 추가로 알려 줍니다. 같은 (from, to, via)는 `count`로 묶습니다.

| type | via | 예시 | 의미 |
|---|---|---|---|
| `references` | `callback` | `Thread(target=self.run)`, `sorted(xs, key=f)`, `signal.signal(SIGINT, handler)` | 인자로 넘긴 함수 |
| `references` | `value` | `handlers = {"x": on_done}`, `return wrapper`, `parser.error = parser_error` | 호출하지 않고 값으로 쓴 함수 |
| `references` | `decorator` | `@log_calls`, `@tagged("x")` (함수·클래스) | 데코레이터 적용: 데코된 노드 → 데코레이터 |
| `calls_implicit` | `init` | `Foo()` | 실제로 실행되는 `__init__` (물려받은 것 포함) |
| `calls_implicit` | `property` | `obj.label`, `obj.label = x` | getter/setter 실행 |
| `calls_implicit` | `context_manager` | `with Foo() as f:` | `__enter__`, `__exit__` (`async with`는 `__aenter__`, `__aexit__`) |

- 수신 객체의 타입을 알 때만 연결합니다 (`self.label`, 타입힌트·생성자·반환 타입으로 타입이 정해진 변수). 모르는 객체의 `x.label`은 연결하지 않습니다.
- 실제 사례 (Sublist3r): `multiprocessing.Process.__init__(self, target=self.run)`, `signal.signal(signal.SIGINT, killproc)`, `sorted(subdomains, key=subdomain_sorting_key)`는 호출처럼 보이지 않아서 이전에는 "아무도 부르지 않는 함수"로 보였습니다.
- 아직 없는 것: 연산자 오버로딩(`a + b`→`__add__`), `__call__`, 클래스를 값으로 넘기는 참조, `getattr` 등 동적 접근

### 효과

| | 이전 | 현재 |
|---|---|---|
| Sublist3r 미확정 | 18건 | 18건 (참조 간선 10개 추가: 콜백 6, 값 1, `__init__` 3) |
| requests (`src/requests`) 내부 연결 / 미확정 / 노드 | 270 / 75 / 283 | **275 / 70 / 291** (중첩 노드 8개) |
| requests 저장소 루트 내부 연결 / 미확정 / 노드 | 1026 / 370 / 687 | **1032 / 364 / 778** (중첩 노드 91개) |
| 호출이 아닌 연결 (저장소 루트) | - | `__init__` 229, property 35, `with` 38, 콜백 28, 값 22 |
| 전체 호출 수 | 929 / 2615 | 929 / 2615 (변화 없음: 호출 지점 중복 집계 없음) |
| 사라진 호출 간선 | - | **0건** (중첩 함수 안의 호출은 바깥 함수로 접어서 비교) |

- 새로 생긴 `calls`는 전부 중첩 함수 호출(`KD`, `generate`, `get_proxy` 등)이고, 미확정이 줄어든 곳은 안쪽 함수가 바깥 함수의 변수 타입을 물려받은 경우입니다.
- 참조·암묵 호출 간선은 종류별로 무작위 표본(콜백 12, 값 12, property 10, `with` 8, `__init__` 8)과 Sublist3r 전체 10개를 소스 줄과 대조했고 모두 맞았습니다. (`references:decorator`는 requests에 해당 사례가 없어서 정답지로만 검증했습니다.)
- 대가: 저장소 루트 분석 시간이 약 0.7초 → 1.0초로 늘었습니다 (참조 탐색 때문).

---

## 그래프용 정보 : import 종류·순환 · 진입점 · 계층 집계

시각화가 바로 쓸 수 있도록 "어디서 시작하는가", "어떤 import 가 위험한가", "폴더·파일·클래스·함수 어느 굵기로 볼 것인가"를 분석 단계에서 미리 계산합니다. 모두 기존 키에 **추가**된 것이라 `schema_version`은 그대로 1입니다.

### 1) import 간선의 종류 · 순환 import · `from x import *`

모듈 `imports` 간선에 import 문이 놓인 자리가 붙습니다.

| `scope` | 의미 | 
|---|---|
| `top` | 모듈 최상단 (클래스 본문 포함). import 하는 순간 실행됨 |
| `conditional` | 최상단의 `if`/`try`/`for`/`while`/`match` 안 (`try: ... except ImportError:` 폴백 등) |
| `function` | 함수 안. 호출해야 실행됨 (순환을 피하려는 지연 import 인 경우가 많음) |
| `type_checking` | `if TYPE_CHECKING:` 안. 타입 검사기만 읽음 (`else:` 쪽은 `conditional`) |

한 간선에 여러 종류가 섞이면 `scopes`(종류별 개수)에 모두 남기고, `scope`는 `top` > `conditional` > `function` > `type_checking` 순서로 대표 하나를 고릅니다. `import_time` 은 `top`·`conditional` 이 하나라도 있으면 `true` 입니다.

**순환 import** 는 간선을 강연결 요소(반복형 Tarjan)로 묶어 찾고, 어떤 종류의 import 까지 쳐야 고리가 생기는지로 나눕니다.

| `kind` | 뜻 | 위험도 |
|---|---|---|
| `import_time` | 최상단·조건부 import 만으로 서로를 요구 | 실제 `ImportError` 위험 |
| `deferred` | 함수 안 import 까지 쳐야 고리가 생김 | 순환을 지연 import 로 피해 둔 상태 (결합은 남아 있음) |
| `type_only` | `TYPE_CHECKING` 까지 쳐야 고리가 생김 | 실행에는 영향 없음 (타입 힌트 결합) |

더 약한 조건에서 이미 보고된 같은 모듈 묶음은 다시 보고하지 않습니다. 결과는 최상위 `import_cycles` (`id`, `kind`, `modules`, `size`, `path`=가장 짧은 고리, `edges`)와 모듈 노드의 `import_cycles: [id]` 로 나옵니다.

**`from x import *`** 는 가져오는 이름을 import 표에 넣어 그 이름의 호출이 연결되게 합니다. 프로젝트 안 모듈만 대상이며, 이름의 범위는 `__all__`(리스트 리터럴일 때) 또는 밑줄로 시작하지 않는 모듈 수준 이름입니다. 다음은 **연결하지 않습니다**: `__all__` 을 다른 모듈 것과 더하는 등 확정할 수 없는 경우, 가져오는 쪽 모듈이 같은 이름을 스스로 정의·대입·import 한 경우, `__all__` 에만 있고 정의를 못 찾은 이름. `summary.star_imports` 에 문장 수와 연결한 이름 수가 나옵니다.

### 2) 진입점 (`entry_points`)

"코드가 어디서 시작되는가"를 알려 줍니다. 시각화에서 "여기서부터 읽으세요" 표시와 호출 깊이의 출발점이 됩니다.

| `kind` | 탐지 대상 | `category` | `confidence` |
|---|---|---|---|
| `main_guard` / `main_call` | `if __name__ == "__main__":` 이 있는 모듈 / 그 블록에서 호출하는 프로젝트 함수·클래스 | runtime | certain |
| `main_module` | `__main__.py` (`python -m 패키지`) | runtime | certain |
| `console_script` | `setup.py`·`setup.cfg`·`pyproject.toml` 의 실행 명령(`pkg.mod:func`, `Class.method`). 분석 폴더에서 설정 파일이 있는 폴더까지 최대 3단계 위로 찾음 | runtime | certain |
| `route` / `web_hook` | Flask·FastAPI·Sanic·Quart·Starlette·Bottle 의 `@app.route`·`@app.get`·`@bp.before_request` ... (`detail`: `GET\|POST /items`) | api | inferred |
| `cli_command` / `task` | click(`@click.command`, 그룹의 `@cli.command()`), Typer, Celery(`@app.task`, `@shared_task`) | runtime | inferred |
| `public_api` | 모듈의 `__all__`, 또는 `__all__` 이 없는 패키지 `__init__.py` 가 다시 내보내는 공개 이름 | api | certain / inferred |
| `test` / `fixture` | 테스트 파일(`test_*.py`, `*_test.py`, `tests/`)의 `test*` 함수, `Test*`·`TestCase` 의 `test*` 메서드, `@pytest.fixture` | test | heuristic / inferred |

프레임워크 판별은 **이름이 아니라 출처**로 합니다: `app = Flask(...)` 처럼 모듈 변수가 `flask` 에서 import 한 생성자로 만들어졌을 때만 `@app.route` 를 라우트로 봅니다. 그래서 직접 만든 `Flask` 클래스나 `Cache().get("/x")` 같은 데코레이터는 진입점이 아닙니다. 모듈 최상위 함수의 데코레이터만 보고(함수 안에서 `app` 을 만드는 앱 팩토리는 못 찾음), 진입점마다 `framework`(해당할 때)가 붙습니다.

노드에는 `entry`(진입점 종류 목록)와 `entry_depth`가 붙습니다. `entry_depth` 는 **테스트를 뺀 진입점**에서 호출·참조 간선(`calls`, `calls_override`, `calls_implicit`, `references`)을 따라 몇 걸음인지입니다 (진입점 자신이 0, 닿지 않으면 `null`). 진입점이 하나도 없는 프로젝트에서는 모두 `null` 입니다.

### 3) 패키지 → 모듈 → 클래스 → 함수 계층 (`hierarchy`)

같은 호출 관계를 네 가지 굵기로 모아 둡니다. 화면에서는 폴더 → 파일 → 클래스 → 함수 순으로 펼쳐 볼 수 있습니다.

| 층 | 단위 | 비고 |
|---|---|---|
| `package` | 폴더 (`pkg/`, 루트는 `./`) | 모듈이 직접 들어 있는 폴더 단위로 집계. `rollup_packages(hierarchy, depth)` 로 원하는 깊이까지 합칠 수 있음 |
| `module` | 파일 | |
| `class` | 최상위 클래스 + 모듈 수준 함수 (`kind` 로 구분) | 메서드·중첩 클래스는 바깥 클래스로 합산 |
| `function` | 모든 함수·메서드 (중첩 함수 포함) | 모듈 수준 함수는 `class` 층과 `function` 층에 모두 나옴 |

- **단위** (`units`): `uid`(`층:id`), `parent`(윗 층 단위), `loc`, `functions`, `classes`, `complexity`, `children` 과 결합도 지표 `in_weight`/`out_weight`/`internal_weight`/`fan_in`/`fan_out`/`instability`(= fan_out / (fan_in + fan_out)). 폴더는 직접 들어 있는 파일 기준 값과 하위 폴더까지 합친 `total_loc`·`total_modules` 를 따로 가집니다
- **간선** (`edges[층]`): `weight` = 호출 지점 수 (`by_type` 으로 `calls`·`calls_implicit`·`references` 별), 상속이 있으면 `extends`, import 가 있으면 `import_weight`(폴더·파일 층). 같은 단위 안의 호출은 간선이 아니라 그 단위의 `internal_weight` 입니다
- **보존 법칙**: 어느 층에서나 `간선 weight 합 + internal_weight 합` = 프로젝트의 호출·참조 간선 수입니다. 함수 층만 클래스가 끝점인 간선(`Foo()` 호출, 클래스 데코레이터)이 빠집니다 (`__init__` 은 별도 `calls_implicit` 간선으로 남음). 테스트가 모든 층에서 이 합계를 확인합니다

### 효과

| | Sublist3r | requests (`src/requests`) | requests 저장소 루트 |
|---|---|---|---|
| import 간선 종류 | top 1 | top 61 · type_checking 12 | top 94 · type_checking 12 |
| 순환 import | 없음 | `type_only` 1개 (8개 모듈) | `type_only` 1개 (8개 모듈) |
| 진입점 | console_script 1, main 호출 5 | 공개 API 22, main 호출 1 | 공개 API 22, 테스트 347, 픽스처 4 |
| 진입점에서 닿는 함수·클래스 | 34 | 43 | 43 |
| 계층 단위 (폴더/파일/클래스/함수) | 2 / 4 / 33 / 99 | 2 / 19 / 120 / 245 | 7 / 37 / 212 / 688 |
| 호출 간선·총 호출 수 | 변화 없음 | 변화 없음 | 변화 없음 |

- 기존 호출 간선·외부 분류·미확정은 세 프로젝트 모두 **한 건도 달라지지 않았습니다** (이전 버전 결과와 항목별 비교). 새로 생긴 것은 추가 키와 `imports` 간선의 필드뿐입니다
- requests 에는 실행 시점 순환이 없고, `_types.py` 가 `TYPE_CHECKING` 으로 다른 모듈을 가져오는 타입 전용 순환 하나만 있습니다
- 분석 시간: Sublist3r 0.2초, requests 저장소 루트 0.9초

---

---

## 타입 추적 (v2.1)

`x.method()`에서 `x`가 어떤 객체인지 알아야 `method`가 어디 것인지 알 수 있습니다. 아래 세 가지 단서로 `x`의 타입을 알아냅니다.

| 단서 | 예시 | 알아내는 것 |
|---|---|---|
| 매개변수 타입힌트 | `def f(req: PreparedRequest): req.copy()` | `req`는 프로젝트의 `PreparedRequest` → 내부 연결 |
| 변수에 담긴 값 | `parser = argparse.ArgumentParser()` | `parser`는 표준 라이브러리 `argparse` 객체 → 외부 분류 |
| `self.속성` 대입 | `self.lock = threading.Lock()` | `self.lock`은 `threading` 객체 → 외부 분류 |

그 외 `*args`는 tuple, `**kwargs`는 dict로 확정하고, `Optional[X]`와 `X | None`은 `X`로 봅니다.

### 원칙: 확실할 때만 타입을 부여한다

잘못된 연결이 생기면 "없는 영향"이 그래프에 나타나서, 못 풀고 남겨두는 것보다 위험합니다. 그래서 아래 경우는 **타입을 포기하고 미확정으로 남깁니다.**

- 같은 이름이 다른 타입으로도 대입되는 경우, 또는 하나라도 타입을 모르는 대입이 섞인 경우
- 루프 변수, 튜플 풀기(`c, x = f()`), `except ... as`, `import`, `global` 등 다른 방식으로도 바인딩되는 경우
- 안쪽 함수의 인자 이름이 겹치는 경우 (`def inner(c)`)
- `Union[A, B]`처럼 후보가 둘 이상인 타입힌트
- `self.속성`이 상속 가족(조상·자손) 중 어디서든 다르게 대입되거나 메서드 이름과 겹치는 경우
- 소문자 함수의 반환값: 반환 타입을 알 수 없어서 대문자로 시작하는 생성자(`argparse.ArgumentParser()`), 프로젝트 함수의 반환 타입힌트, 반환표에 있는 표준 라이브러리 함수(`re.compile()` 등)만 인정 (v2.3)

### 타입 추적의 정확도 점검

- 새로 생긴 내부 연결 6건(requests)은 소스를 열어 전부 확인했고 모두 올바른 연결이었습니다.
- 타입 추적으로 외부 분류된 간선 124개(호출 145건) 중 무작위 25개를 확인했고 25개 모두 올바른 분류였습니다. (`typing`에서 온 타입은 출처가 `typing`으로 표시되는 점은 알아두세요)
- 느슨한 규칙으로 시뮬레이션했을 때 나왔던 연결 4건은 엄격 모드에서 빠졌습니다. 2건은 `A | B` 타입이 모호해 틀린 연결이었고, 2건은 변수가 재대입되어 증명할 수 없어 보류된 경우입니다.

---

## 호출 분류 체계

| 구분 | category | 의미 | 예시 |
|---|---|---|---|
| 내부 연결 | (edges) | 프로젝트 안의 함수로 연결 성공 | `api.py::place_order → service.py::create_order` |
| **외부 (확실)** | `builtin` | 내장 함수·예외 | `print`, `len`, `ValueError` |
| | `stdlib` | 표준 라이브러리 | `sys.exit`, `urlparse`, `os.path.join` |
| | `third_party` | 외부 패키지 | `urllib3`, `dns`, `requests` |
| | `inherited_external` | 외부 부모 클래스에게 물려받은 메서드 | `self.update` ← 부모 `MutableMapping` |
| **외부 (추정)** | `builtin_type_method` | 변수 타입은 모르지만 str/list/dict 메서드 이름 | `name.strip()`, `items.append()` |
| **미확정** | `unknown_receiver` | 변수 타입을 알아야 해석 가능 | `proxies.get`, `link_regx.findall` |
| | `local_callable` | 매개변수·중첩 함수·변수에 담긴 함수 호출 | `hash_utf8(...)`, pytest 픽스처 `httpbin(...)` |
| | `project_unresolved` | 프로젝트 안 이름인데 못 찾음 (**점검 필요**) | (저장소 루트 1건, 나머지 0건) |

`confidence` 값: `certain`(코드만 보고 확정) / `inferred`(타입 추적으로 확정) / `heuristic`, `weak`(메서드 이름 추정, `get`·`join`처럼 모호한 이름은 `weak`)

프로젝트 안 클래스가 같은 이름의 메서드를 가지고 있으면 이름만 보고 추정하지 않고 `unknown_receiver`로 남깁니다 (프로젝트 내부 호출을 외부로 숨기지 않기 위해).

---

## 출력 JSON 구조

```
schema_version    출력 구조 버전 (구조가 바뀌면 올림, 현재 1)
nodes             모듈(type: module) · 함수 · 클래스 · 메서드 (중첩 정의 포함). 메타데이터는 위 표 참고
                    진입점이면 entry, 함수·클래스는 entry_depth, 순환에 속한 모듈은 import_cycles
edges             프로젝트 내부 간선
                    호출:   calls | calls_override  (근거를 알 수 있는 것은 via 표시)
                    구조:   contains | extends | imports
                    그 외:  references (callback | value | decorator), calls_implicit (init | property | context_manager)
entry_points      진입점 목록 {id, kind, category, detail, file, line, confidence}
import_cycles     순환 import {id, kind, modules, size, path, edges}
hierarchy         {levels, units, edges}  패키지 → 모듈 → 클래스 → 함수 집계 (위 "그래프용 정보" 참고)
external_nodes    외부 카테고리 노드 (EXTERNAL::stdlib 등 5개)
external_edges    외부로 나가는 호출 (같은 호출은 count로 묶음)
unresolved        끝까지 확정하지 못한 호출 (category 포함)
skipped_files     파싱하지 못해 분석에서 빠진 파일 [{file, error}]
summary           통계 요약 (import_aliases, module_path_edges, nested_nodes, skipped_files 등 포함)
```

`via` 값: `type_hint` / `local_assign` / `self_attr` (타입 추적, 추정 단계), `constructor` (생성자 직후 메서드), `module_path` (점 표기 모듈 트리), `return_type` (호출 결과의 반환 타입힌트, v2.3), `module_var` (모듈 수준 변수의 타입, v2.3), `closure` (중첩 함수 호출). `summary.edge_via` 에 via 별 개수가 들어 있습니다.

실제 출력 예시:

```jsonc
// 타입힌트로 찾은 내부 연결 (requests)
{"from": "auth.py::HTTPDigestAuth.handle_401", "to": "models.py::Response.close",
 "type": "calls", "line": 301, "via": "type_hint"}

// 오버라이드 후보 (Sublist3r): 부모의 self.generate_query()가 실제로는 자식 것이 실행될 수 있음
{"from": "sublist3r.py::enumratorBase.enumerate",
 "to": "sublist3r.py::GoogleEnum.generate_query", "type": "calls_override", "line": 227}

// 구조 간선 (v2.3)
{"from": "models.py::Child", "to": "base.py::Base", "type": "extends"}
{"from": "service.py", "to": "models.py", "type": "imports", "count": 1, "line": 3,
 "scope": "top", "scopes": {"top": 1}, "import_time": true}
{"from": "models.py::Child", "to": "models.py::Child.run", "type": "contains"}

// 호출이 아닌 연결
{"from": "sublist3r.py::signal_init", "to": "sublist3r.py::killproc", "type": "references", "via": "callback", "count": 1, "line": 62}
{"from": "app.py::run", "to": "app.py::Resource.__init__", "type": "calls_implicit", "via": "init", "count": 3, "line": 80}

// 외부 호출
{"from": "sublist3r.py::parser_error", "to": "EXTERNAL::stdlib", "type": "calls_external",
 "category": "stdlib", "package": "sys", "detail": "sys.exit", "confidence": "certain", "count": 1, "line": 90}

// 미확정
{"from": "sublist3r.py::parse_args", "expr": "parser.add_argument", "category": "unknown_receiver", "line": 98}
```

그래프를 그릴 때는 호출 `edges`는 실선, `calls_override`는 점선, `external_edges`는 회색 외부 노드로 표현하면 됩니다. 외부 노드는 카테고리 단위(5개)라 노드 수가 늘지 않고, 자세한 호출 내용은 간선의 `detail`·`package`에 보존되어 있습니다.

---

## 상속 처리

| 호출 형태 | 처리 방식 | 버전 |
|---|---|---|
| `self.method()` | 현재 클래스에 없으면 부모 클래스로 타고 올라가며 탐색 (다단계·다중 상속 지원) | v1 |
| `ParentClass.__init__(self, ...)` | 클래스 이름으로 직접 호출하는 구식 패턴 | v1 |
| `super().method()` | 현재 클래스를 건너뛰고 부모부터 탐색 | v1 |
| 자식이 오버라이드한 메서드 | `self.method()` 호출 시 자식 클래스의 같은 이름 메서드를 `calls_override`로 추가 | v2 |
| 외부 부모에게 물려받은 메서드 | 상속 사슬에 프로젝트 밖 부모가 있으면 `inherited_external`로 분류 | v2 |
| 타입힌트로 받은 객체의 메서드 | 선언된 클래스의 자식이 오버라이드했을 수 있어 `calls_override`도 추가 (반환 타입힌트로 알게 된 객체도 같음) | v2.1 / v2.3 |
| 클래스 상속 관계 자체 | `extends` 구조 간선 (프로젝트 밖 부모는 `external_bases`) | v2.3 |

### 왜 오버라이드 연결이 필요한가

```
SessionRedirectMixin.resolve_redirects  →  self.send()
```

v1은 이 호출을 믹스인 안의 빈 껍데기 `SessionRedirectMixin.send`에만 연결했지만, 실제로 실행되는 것은 자식 클래스의 `Session.send`입니다.
오버라이드를 연결하지 않으면 `Session.send`를 수정해도 그 영향이 그래프에 나타나지 않아, "수정 영향 범위"라는 프로젝트 목적에 직접 걸리는 문제입니다.
Sublist3r에서 33건, requests에서 1건이 이에 해당했습니다.

---

## 검증 결과

실제 오픈소스 2개로 검증했고, Python 3.9와 3.12에서 결과가 동일함을 확인했습니다.

| | [Sublist3r](https://github.com/aboul3la/Sublist3r) | [requests](https://github.com/psf/requests) `src/requests` | requests 저장소 루트 |
|---|---|---|---|
| 파일 / 노드 / 전체 호출 | 4 / 115 / 469 | 19 / 291 / 929 | 37 / 778 / 2615 |
| 내부 연결 | 117 (24.9%) | 275 (29.6%) | 1032 (39.5%) |
| 오버라이드 후보 간선 | 33 | 2 | 6 |
| 구조 간선 (`contains` / `extends` / `imports`) | 115 / 12 / 1 | 291 / 32 / 73 | 778 / 36 / 106 |
| 내장 `builtin` | 130 (27.7%) | 301 (32.4%) | 540 (20.7%) |
| 표준 라이브러리 `stdlib` | 113 (24.1%) | 164 (17.7%) | 349 (13.3%) |
| 외부 패키지 `third_party` | 28 (6.0%) | 21 (2.3%) | 181 (6.9%) |
| 외부 부모 `inherited_external` | 2 (0.4%) | 12 (1.3%) | 32 (1.2%) |
| 추정 `builtin_type_method` | 61 (13.0%) | 86 (9.3%) | 117 (4.5%) |
| 미확정 `unknown_receiver` | 17 (3.6%) | 54 (5.8%) | 151 (5.8%) |
| 미확정 `local_callable` | 1 (0.2%) | 16 (1.7%) | 212 (8.1%) |
| 점검 필요 `project_unresolved` | 0 | 0 | 1 (0.04%) |
| **분류 완료율 (확실)** | **64.8%** | **73.0%** | **72.2%** |
| **분류 완료율 (+ 타입 추적)** | **83.2%** | **83.2%** | **81.6%** |
| **분류 완료율 (+ 이름 추정)** | **96.2%** | **92.5%** | **86.1%** |
| **미확정 합계** | **18건 (3.8%)** | **70건 (7.5%)** | **364건 (13.9%)** |

> 저장소 루트는 `tests/`가 포함되어 미확정이 많습니다. (v2.2 측정 기준) `local_callable` 218건의 90%, `unknown_receiver` 221건의 72%가 `tests/`에서 나왔고, 대표적으로 매개변수로 주입되는 pytest 픽스처(`httpbin` 155건, `monkeypatch`)입니다. 벤치마크 비교에는 패키지 폴더(`src/requests`) 수치를, 실제 사용 방식(저장소 루트)의 점검에는 오른쪽 열을 보세요.

### 버전별 변화 (requests `src/requests`, Sublist3r)

| | v2 | v2.1 | v2.2 | v2.3 |
|---|---|---|---|---|
| Sublist3r 미확정 | 64건 (13.6%) | 37건 (7.9%) | 37건 (7.9%) | **18건 (3.8%)** |
| requests 미확정 | 118건 (12.7%) | 82건 (8.8%) | 82건 (8.8%) | **70건 (7.5%)** |
| requests 내부 연결 | 260 | 266 | 266 | **275** |
| requests 저장소 루트 내부 연결 | - | 339 | 970 | **1032** |
| 사라진 간선 | - | 0 | 0 | 0 |

v2.2는 패키지 폴더 단위 수치를 바꾸지 않고(이미 풀려 있던 영역) 저장소 루트 단위에서 효과가 났고, v2.3은 타입 정보(반환 힌트·모듈 변수·반환표)로 양쪽의 남은 미확정을 줄였습니다.

### Sublist3r 개선 과정 (내부 연결 수)

| 단계 | 내부 연결 |
|---|---|
| 최초 (단일 파일 방식을 프로젝트 전체로 확장) | 79 |
| + 상속 체인 탐색 (`self.method()`, `Parent.method(self)`) | 107 |
| + 지역 변수 타입 추적 (`x = ClassName()`) | 109 |
| + `super().method()` | **117** |

### 핵심 발견

- **상속 로직이 프로젝트에 한정되지 않음**: Sublist3r에서 만든 상속 처리가 구조가 더 복잡한 requests(다중 상속, 상대 import)에서도 `self.method()` 미해결을 거의 만들지 않았습니다.
- **재수출이 분류를 왜곡함**: requests는 `compat.py`가 `urllib.parse`를 가져와 다시 내보내고 다른 파일이 `from .compat import urlparse`로 씁니다. 이를 따라가지 않으면 표준 라이브러리 호출 64건이 "프로젝트 안"으로 잘못 분류됩니다.
- **타입힌트가 있는 코드일수록 효과가 큼**: requests의 새 내부 연결 6건 중 4건이 매개변수 타입힌트에서 나왔고, 타입힌트가 거의 없는 Sublist3r에서는 0건이었습니다.
- **`project_unresolved`는 경보로 동작함**: 저장소 루트 실행에서 492건이 이 항목으로 올라왔고, 원인을 추적해 src 레이아웃·재수출 문제를 찾아냈습니다. 고친 뒤 11건으로 줄었습니다.

---

## 테스트

```bash
python tests/test_analyzer.py                     # 전체 실행 (pytest 가 있으면 pytest tests 도 가능)
python tests/test_analyzer.py --update-baseline   # 분석기를 일부러 개선했을 때만 기준선 갱신
```

| 종류 | 내용 |
|---|---|
| **정답지** `fixtures/mini_project` | 정답을 손으로 설계한 작은 프로젝트. **있어야 하는 연결**, **있으면 안 되는 연결**(재대입, 루프 변수, 튜플 풀기, 중첩 함수 인자, `Union`), 외부 분류 결과를 확인 |
| **정답지** `fixtures/src_layout_project` | 패키지가 `src/` 아래 있고 `tests/`가 절대 import로 쓰는 구조. import 루트 보정, 재수출, 점 표기, 생성자 직후 메서드, **이름 가림**, 패키지 폴더 단독 실행을 확인 |
| **정답지** `fixtures/typed_project` (v2.3) | 반환 타입힌트(`self.send()`, `c.send().json()`, `-> Self`, `-> "Client"`, `Optional`), 모듈 변수, 표준 라이브러리 반환표, `Final`/`Any`/`Callable` 처리. **함정**: async·`@property`·알 수 없는 데코레이터·힌트 없는 함수, 재대입/`global`로 바뀌는 모듈 변수, 매개변수·지역 변수가 모듈 변수를 가림, 튜플 풀기, `next()`/`getattr()` 결과 |
| **구조 간선** | `imports`/`extends`/`contains` 정답, 모든 노드가 부모를 하나만 가지는지, 자기 import 없음, 외부 부모 기록 |
| **정답지** `fixtures/refs_project` | 중첩 함수·클래스(호출 귀속, 총 호출 수 불변), 참조·암묵 호출 간선을 **개수까지** 비교, 노드 메타데이터(줄 범위·복잡도·kind·docstring), `skipped_files`, `schema_version`. **함정**: 다시 대입된 중첩 함수 이름, 이름이 겹치는 조건부 정의, 안쪽 매개변수가 바깥 변수를 가림, 중첩 클래스의 부모 |
| **정답지** `fixtures/graph_project` | import 종류 4가지와 한 간선에 섞인 경우, 순환 3종류(`import_time`/`deferred`/`type_only`)와 고리 경로, 반복형 Tarjan(6000개 사슬), `from x import *`(`__all__` 유무·확정 불가·자기 정의 우선), 진입점 전 종류(console script·`__main__`·Flask/FastAPI/click/Typer/Celery/pytest)와 **함정**(`Cache().get` 데코레이터, 직접 만든 `Flask` 클래스, 밑줄 이름 재수출, `__all__` 밖, 확정 못 하는 `__all__`, `Test*` 아닌 클래스), `entry_depth`, 계층 간선·지표·보존 법칙·`rollup_packages` |
| **기준선** `baseline.json` | Sublist3r, requests(`src/requests`), requests 저장소 루트에서 내부 연결이 줄거나 미확정이 늘면 실패 |

- 연결은 **개수까지** 비교합니다 (같은 연결이 두 번 있어야 하는데 한 번만 있는 경우를 잡기 위해)
- 정답지 테스트는 **일부러 분석기를 망가뜨려서** 실제로 잡아내는지 확인했습니다: 타입 추적 제거, 재대입 검사 제거, `Union` 느슨화, 오버라이드 제거, import 루트 보정 제거, 재수출 주입 제거, 재수출 import 정규화 제거, 이름 가림 검사 제거, 점 표기 제거, 생성자 직후 메서드 제거 (모두 실패로 잡힘). v2.3에서는 반환 타입 제거, 모듈 변수 제거, async/데코레이터 검사 제거, `global`·재대입 검사 제거, 내장 반환표 느슨화, `typing` 이름 처리 제거, 표준 라이브러리 반환표 제거, 선언 타입 오버라이드 제거, `contains` 제거를 시험했고 모두 테스트가 실패로 잡았습니다. 참조·중첩 기능도 같은 방식으로 13가지(중첩 수집, 값/콜백, `__init__`, property, `with`, 데코레이터, 클로저 타입 상속, 호출 중복 집계, 이름 재바인딩 검사, 복잡도, 중첩 클래스 부모 보호, `skipped_files` 등)를 시험했고, 처음에 못 잡은 2가지(클로저 타입 상속, 중첩 클래스 부모 보호)는 정답지를 보강해서 잡게 했습니다 (처음에는 내장 반환표 느슨화와 `typing` 처리 제거를 못 잡아서 함정 케이스를 추가했습니다). 그래프용 정보도 같은 방식으로 28가지(import scope 우선순위, `TYPE_CHECKING` 판별, star 확장의 `__all__`·밑줄·자기 정의 보호, 프레임워크 출처 확인, 순환 중복 보고, 테스트 진입점의 깊이 제외, 계층 집계의 가중치·상속·import 반영, console script 탐색 등)를 시험했고, 처음에 못 잡은 3가지(상위 폴더의 설정 파일 탐색, 확정 못 하는 `__all__`을 가진 `__init__.py`, 라우트 기본 메서드)는 테스트를 보강해서 잡게 했습니다
- 실제 프로젝트 테스트는 폴더가 없거나 기준 버전과 다르면 **건너뜁니다** (기준 커밋은 `baseline.json`에 기록). Windows 줄바꿈 차이는 무시합니다
- `tests/conftest.py`가 `fixtures/` 안의 파일을 pytest가 테스트로 수집하지 않게 합니다
- 새 기능을 추가하면 **정답지에 케이스를 먼저 추가**하고, 개선되면 `--update-baseline`으로 기준선을 올립니다 (기준선은 나빠지는 것만 막는 용도)

---

## 알려진 한계

- **남은 미확정 (Sublist3r 18건, `src/requests` 70건, 저장소 루트 364건)** 의 주요 유형
  - 매개변수로 주입되는 pytest 픽스처: `httpbin`, `monkeypatch` (저장소 루트 미확정의 대부분)
  - 타입힌트 없는 매개변수를 그대로 저장한 속성: `self.in_q = in_q` 후 `self.in_q.put`
  - 재대입된 매개변수, 반환 힌트가 없거나 `Any`인 프로젝트 함수의 결과 (`requests.post(url).json().get`: `json()` 이 `-> Any`)
  - `OrderedDict()` 와 `Mapping` 처럼 구체 타입과 추상 타입이 섞인 속성 (`self.adapters.items`): 추상 타입은 프로젝트 클래스일 수도 있어서 포기
  - 프로젝트 모듈이 외부 모듈을 재수출하는 경우 (`compat.cookielib.CookieJar`) — 저장소 루트의 `project_unresolved` 1건의 정체
  - `threading.local()` 같은 소문자 클래스, `self.__dict__`
- **반환표에 없는 외부 함수**: 표에 넣은 것만 타입을 압니다. 외부 메서드의 반환 타입(`conn.cursor()`)도 모릅니다
- **모듈 변수를 다른 모듈에서 바꾸는 경우**: `models.mystery = Foo()` 처럼 같은 모듈 밖에서 대입하면 보이지 않습니다
- **`__init__.py`가 없는 패키지**: `src/` 아래 패키지에 `__init__.py`가 없으면(네임스페이스 패키지) import 루트 보정 대상이 아닙니다
- **추정 라벨 오분류**: 무작위 30건 중 3건이 틀렸습니다 (`Thread.join`을 `str.join`으로 등). 모두 외부 호출이라 내부 연결을 숨기지는 않으며 모호한 이름은 `weak`로 표시합니다
- **수집되지 않는 정의**: 중첩 함수·클래스는 이제 수집하지만, `if/try` 안의 조건부 정의(모듈·클래스 수준)와 이름이 겹치는 중첩 정의는 어느 쪽인지 알 수 없어 수집하지 않습니다 (requests 9개). 호출은 `local_callable`로 분류됩니다
- **중첩 정의 안의 변수는 단순화**: 로컬 클래스 메서드는 바깥 함수의 변수를 물려받지 않고, 중첩 함수의 클로저 타입 상속도 바깥 함수에서 타입이 확정된 변수에 한합니다
- **파싱 실패는 조용히 지나가지 않지만 복구하지는 않음**: 실행 중인 Python보다 새로운 문법(3.10+ `match` 등)을 쓴 파일은 `skipped_files`에 기록되고 분석에서 빠집니다. 최신 코드를 분석할 때는 최신 Python으로 실행하세요
- **동적 호출**: `getattr(obj, "method")()` 처럼 이름이 실행 시점에 정해지는 호출은 정적 분석으로 추적할 수 없습니다
- **`self.속성`의 외부 대입**: `obj.attr = ...` 처럼 클래스 밖에서 대입하는 경우는 보이지 않습니다
- **상속 탐색 순서**: Python의 정확한 MRO(C3)가 아니라 깊이 우선 탐색이라, 복잡한 다이아몬드 상속에서는 다를 수 있습니다
- **조건부 import**: `try: import a / except: import b`처럼 같은 이름이 여러 번 import되면 마지막 것을 사용합니다 (간선에는 둘 다 `conditional` 로 남음)
- **진입점**: 앱 팩토리(`def create_app(): app = Flask(); @app.route ...`)처럼 함수 안에서 만든 객체의 라우트, 클래스 기반 뷰(`MethodView`, Django), `typer.run(main)`·`argparse` 만 쓰는 스크립트, 문자열로 등록하는 `entry_points` 플러그인은 찾지 못합니다. 테스트는 이름·폴더 관례(`heuristic`)로만 판별합니다
- **`entry_depth`**: 호출·참조 간선 기준이라 연산자 오버로딩·`__call__`·동적 호출로 닿는 코드는 `null` 로 남습니다. `null` 이 곧 "안 쓰는 코드"는 아닙니다 (라이브러리는 사용자가 부르는 함수 대부분이 진입점 후보)
- **star import**: 프로젝트 밖 모듈의 `from x import *` 는 이름을 알 수 없어 확장하지 않고, `__all__` 이 동적이면 그 모듈의 star 는 연결하지 않습니다
- **계층 집계**: 폴더 층은 모듈이 직접 들어 있는 폴더 단위이고 상위 폴더로의 합산은 `rollup_packages` 로 합니다. 함수 층에는 클래스가 끝점인 간선이 없습니다
- **미검증**: 데코레이터(`@app.route` 등 외부 데코레이터)가 함수를 바꿔치는 경우의 실제 호출 흐름. Windows(Python 3.9.7)에서 v2.2의 테스트 15개 통과와 `is_stdlib` 자체점검을 확인했고, 현재 테스트(36개)는 Python 3.9와 3.13(리눅스)에서 통과, Windows 실행은 확인 전입니다

---

## 진행 예정

- [x] 매개변수 타입힌트, 외부 호출 결과를 담은 변수, `self.속성` 타입 추적 (v2.1)
- [x] 정답지·기준선 회귀 테스트 (v2.1)
- [x] 저장소 루트·src 레이아웃 분석, 패키지 재수출·점 표기 추적 (v2.2)
- [x] 그래프용 구조 간선: 모듈 `imports`, 클래스 `extends`, 소속(`contains`/`parent`) (v2.3)
- [x] 프로젝트 함수의 반환 타입힌트 활용 (`resp = self.send()`, `requests.post(url).json()`) (v2.3)
- [x] 모듈 수준 변수의 타입 (`codes = LookupDict(...)` 후 `requests.codes.get`) (v2.3)
- [x] 자주 쓰는 표준 라이브러리 함수의 반환 타입 표 (`re.compile`, `socket.socket` 등) (v2.3)
- [ ] **재현율(recall) 측정**: 지금 수치는 "분류 완료율"이지 정확도가 아닙니다. 테스트 실행 중 실제 호출을 기록(동적 트레이스)해서 정적 분석이 놓친 간선을 세고, 정답이 있는 Sublist3r 논문 호출 그래프와 비교
- [ ] 분석 결과의 정밀도(precision) 표본 점검 자동화 (새 간선 N개를 무작위로 뽑아 소스 줄과 함께 출력)
- [ ] **테스트 관계**: `tests → 코드` 연결과 pytest 픽스처(매개변수 주입)를 이용해 "이 코드를 고치면 돌려야 할 테스트" 계산
- [x] 중첩 함수·클래스 수집, `Foo()` → `Foo.__init__` 연결, `@property` 접근, 콜백으로 넘기는 함수, 데코레이터·`with` 연결 (v2.3)
- [x] 노드 메타데이터(줄 범위·시그니처·docstring·복잡도), 건너뛴 파일 기록, 스키마 버전 (v2.3)
- [x] **진입점 탐지** (`__main__`, console script, 라우트·click·Typer·Celery 데코레이터, `__all__`)와 `entry_depth`
- [x] import 간선에 종류 표시 (최상단 / 조건부 / 함수 안 / `TYPE_CHECKING`)와 순환 import 탐지, `from x import *` 확장
- [x] 패키지 → 모듈 → 클래스 → 함수 계층으로 집계한 그래프(간선 가중치)
- [ ] 앱 팩토리·클래스 기반 뷰의 라우트, `entry_depth` 를 쓴 "읽는 순서" 추천
- [ ] 연산자 오버로딩·`__call__` 연결, 본문에서 반환 타입 추론, MRO(C3), 동적 호출 표시
- [ ] 같은 프로젝트를 PyCG와 비교해 간선 일치율 측정
- [ ] Flask, FastAPI 등으로 일반화 검증

---

## 테스트 대상 프로젝트

분석 대상은 이 저장소에 포함하지 않습니다 (`.gitignore` 등록). 직접 재현하려면 저장소 바로 아래에 clone 하세요. (기준선 테스트가 이 위치를 사용합니다)

```bash
git clone https://github.com/aboul3la/Sublist3r.git
git clone https://github.com/psf/requests.git
```

```bash
python analyze_project.py Sublist3r
python analyze_project.py requests/src/requests     # 패키지 폴더
python analyze_project.py requests                  # 저장소 루트 (tests/ 포함)
```

---

## 파일 구조

```
knit_code/
├─ analyze_project.py       # 분석기 (v2.3)
├─ README.md
├─ tests/
│  ├─ test_analyzer.py      # 회귀 테스트
│  ├─ conftest.py           # fixtures 를 pytest 수집에서 제외
│  ├─ baseline.json         # 실제 프로젝트 기준선
│  └─ fixtures/
│     ├─ mini_project/        # 정답지: 상속·타입 추적·외부 분류
│     ├─ src_layout_project/  # 정답지: src 레이아웃·재수출·이름 가림
│     ├─ typed_project/       # 정답지: 반환 타입힌트·모듈 변수·반환표·함정 (v2.3)
│     ├─ refs_project/        # 정답지: 중첩 정의·참조/암묵 호출 간선·메타데이터·건너뛴 파일
│     └─ graph_project/       # 정답지: import 종류·순환·star·진입점·계층 집계
├─ sample_project/          # 테스트용 소규모 샘플
└─ graph_*.json             # 실행 결과 (자동 생성, 커밋하지 않음)
```

---

## 변경 이력

**v2.3**
- 그래프용 정보 추가: import 간선의 종류(`scope`)와 순환 import(`import_cycles`), `from x import *` 확장, 진입점(`entry_points`, 노드의 `entry`·`entry_depth`), 패키지 → 모듈 → 클래스 → 함수 집계 그래프(`hierarchy`, `rollup_packages`)
- 정답지 `graph_project` 추가, 테스트 총 36개. 기존 호출 간선은 세 프로젝트 모두 변화 없음
- 구조 간선 추가: 모듈 `imports`, 클래스 `extends`, 소속 `contains`, 모듈 노드와 `parent`, 외부 부모는 `external_bases`
- 프로젝트 함수의 반환 타입힌트 활용: `resp = self.send()`, `requests.post(url).json()`, `-> Self`, 문자열·`Optional` 힌트 (`via: return_type`). async·데코레이터·중복 정의 함수는 제외
- 모듈 수준 변수의 타입: `codes = LookupDict(...)` 후 `requests.codes.get`, 재수출된 변수도 추적 (`via: module_var`). 재대입·`global`·이름 겹침은 포기
- 표준 라이브러리 반환 타입표 (`re.compile`, `socket.socket`, `os.path.join` ...)
- 선언된 타입(타입힌트·반환 힌트)은 자식 클래스 오버라이드를 함께 연결
- 정밀도 개선: `Any`/`object`/`Callable` 등을 타입으로 취급하지 않고 `typing`으로 분류하던 문제 수정, `Final`/`ClassVar`/`Annotated` 벗기기, `next()`/`getattr()` 결과를 내장 타입으로 보던 문제 수정, 내장 별칭 판별에 '한 번만 대입' 규칙 적용
- 타입 정답지(`typed_project`, 함정 포함)와 구조 간선 테스트 추가, 기준선 갱신
- 노드 메타데이터 추가: `end_lineno`, `loc`, `signature`, `docstring`, `decorators`, `is_async`, `visibility`, `kind`, `complexity`, `bases`, `parent`. 출력 JSON에 `schema_version`, `skipped_files` 추가
- 중첩 함수·클래스를 노드로 수집 (호출은 가장 안쪽 함수에 귀속, 클로저 호출 `via: closure`, 바깥 함수의 변수 타입 상속)
- 호출이 아닌 연결 추가: `references`(콜백·값·데코레이터), `calls_implicit`(`__init__`·property·`with`)
- 정답지 `refs_project` 추가, 테스트 총 27개
- v2.2 대비 사라진 호출 간선 0건

**v2.2**
- 저장소 루트와 src 레이아웃 지원: `__init__.py` 체인으로 import 루트를 보정 (`requests` → `src.requests`)
- 패키지 재수출 추적 (`requests.get`, `from requests import Session`), 점 표기 모듈 트리 해석
- 생성자 호출 직후 메서드 해석 (`Request(...).prepare()`)
- 지역 이름이 import한 이름을 가릴 때 잘못 연결하던 결함 수정
- 정답지(`src_layout_project`)와 저장소 루트 기준선 추가, 연결을 개수까지 비교하도록 테스트 강화

**v2.1**
- 타입 추적 추가: 매개변수 타입힌트, 외부 호출 결과를 담은 변수, `self.속성` 대입
- 확실하지 않으면 포기하는 안전장치 (재대입, 루프 변수, 튜플 풀기, 중첩 함수 인자, `Union` 등)
- 타입힌트로 받은 객체에도 오버라이드 후보 간선 추가, 타입 추적으로 찾은 간선에 `via` 표시
- 회귀 테스트 추가 (정답지 + 기준선)
- 성과 지표를 신뢰도별 3단계로 세분화

**v2**
- 미해결 호출을 원인별로 분류 (내장 / 표준 라이브러리 / 외부 패키지 / 외부 부모 / 추정 / 미확정)
- 오버라이드 후보 간선(`calls_override`) 추가
- 재수출(re-export) 추적, 부모 클래스 해석 보강 (`as`, `Base[T]`, 점 표기)
- 지역 변수 추적 버그 수정 (함수 호출 결과를 클래스 인스턴스로 오인)
- 결과 파일명을 `graph_<프로젝트명>.json`으로 변경, 분류 기능 내장

**v1**
- 프로젝트 전체 분석, 고유 ID, import 해석, 호출 관계 추출
- 상속 체인 탐색 (`self`, `Parent.method(self)`, `super()`), 지역 변수 타입 추적
