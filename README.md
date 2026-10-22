# KnitCode — Static Call Graph Prototype (v2.9)

Python 3.10+ / Windows PowerShell / **표준 라이브러리만 사용**. 기존 정적 구문 분석기에 보완하고 있는 중입니다.

## v2.6 — 그래프 구성 속도 개선 + 오버라이드 후보 분류 (2026-10-17)

**속도** — 결과는 한 글자도 바꾸지 않고 반복 계산만 제거했습니다. 5개 저장소의 출력 JSON·CSV·HTML이 기존과 바이트 단위로 같습니다.

| 저장소 | 이전 | 이후 |
|---|---|---|
| click | 25.1초 | 6.9초 |
| httpx | 16.3초 | 6.3초 |
| requests | 14.5초 | 4.3초 |
| flask | 10.3초 | 3.6초 |

- 주요 원인: `ast.get_source_segment`가 호출마다 파일 전체를 다시 줄 단위로 나눔, `imports_at`·`project_symbols`가 호출마다 전체를 다시 훑음.

**오버라이드 후보 (`CALLS_OVERRIDE`)** — `override_dispatch.py`

- `self.m()` / `cls.m()` / 타입 힌트로만 선언된 매개변수 `x.m()`은 자식 클래스가 재정의한 메서드가 실행될 수 있으므로, 프로젝트 안의 자식 클래스 재정의를 **후보**로 기록합니다.
- `c = Child(); c.run()`처럼 생성자로 만든 객체는 타입이 확정되므로 후보를 만들지 않습니다.
- 후보는 `graph_result.json`의 **`override_edges`에 별도로** 저장하며 `edges`, 해결/미해결 수, 호출 상태는 바꾸지 않습니다. `override_summary`와 `override_calls.csv`도 생성합니다.
- 분류 필드: `dispatch_receiver`(self/cls/선언 타입), `override_kind`(extends=super 호출 / replaces), `base_method_kind`(concrete / abstract / missing / unknown), `subclass_location`(source / tests), `receiver_mro`.
- 실행 검증: click·flask 테스트를 실제로 실행해 호출을 기록한 결과, 후보 중 click 58/92, flask 15/19가 실제로 일어났습니다. 나머지는 테스트가 실행하지 않은 경로일 수 있으므로 오류라는 뜻은 아닙니다.
- 한계: 분석 대상 밖의 자식 클래스(다른 패키지, 실행 중 생성되는 클래스)는 알 수 없습니다.

## KnitCode Flow 뷰어 (`flow_viewer.py`, 2026-10-22)

전체 그래프를 한꺼번에 그리지 않고, **누른 함수를 중심으로 국소 그래프**를 보여 주는 오프라인 HTML입니다. `python main.py <저장소>`를 실행하면 `output/flow.html`이 함께 생기고, 이미 분석한 폴더는 `python flow_viewer.py output`으로 다시 만들 수 있습니다. 브라우저에서 파일을 열면 되며 인터넷 연결이 필요 없습니다.

- **왼쪽:** 파일·함수 검색과 트리. 누르면 그 함수가 중심이 됩니다.
- **가운데:** 호출자는 왼쪽, 호출 대상은 오른쪽. `− 단계 / + 단계`로 1~3단계. 열마다 8개까지 보여 주고 나머지는 "더 보기"로 접습니다. 확실한 호출(명시적·생성자)이 먼저, 후보(암묵·오버라이드)와 모듈 최상위 호출은 뒤에 옵니다.
- **선 클릭:** 오른쪽 패널에 그 관계의 호출 위치(파일:줄), 소스, 확실성, 판정 규칙이 나옵니다. 명시적 호출(실선), 생성자, 암묵 호출(점선), 오버라이드 후보(짧은 점선)는 범례에서 켜고 끌 수 있습니다.
- **아래:** 선택한 함수 안의 모든 호출과 상태(내부 연결 / 외부 / 미해결, 추정 출처).
- **테스트 코드 숨기기**가 기본으로 켜져 있어 테스트 호출자가 많은 함수도 깔끔하게 보입니다. 주소창의 `#함수ID`로 같은 화면을 공유할 수 있습니다.
- Git 이력·함께 변경된 파일 패널은 자리만 잡아 두었고 아직 연결하지 않았습니다.

## 실행 기반 검증 도구 (`trace_recall.py`, 2026-10-22)

분석 대상의 테스트를 실제로 실행해 관찰한 함수 간 호출과 정적 그래프를 비교합니다. 팀원 누구나 같은 커밋·같은 명령으로 같은 숫자를 재현할 수 있습니다. **절차·지표 정의·기준 수치: [`RUNTIME_VALIDATION_KO.md`](RUNTIME_VALIDATION_KO.md)**

```powershell
python trace_recall.py prepare --dest targets --venv .venv-validate
python trace_recall.py run targets\rich --output-dir out\rich --tests tests --python .venv-validate\Scripts\python.exe
```

| 저장소 | 원본 JSE recall | 현재 recall | 현재 confirmed (정밀도 하한) |
|---|---|---|---|
| schedule | 40.6% | 72.0% | 99.2% |
| Requests | 69.1% | 82.8% | 91.8% |
| Click | 46.2% | 49.5% | 96.9% |
| Rich | 51.2% | 73.0% | 96.8% |

`record`/`run`은 대상 테스트를 **실행**합니다(분석기 자체는 실행하지 않음). 신뢰할 수 있는 저장소와 별도 venv에서만 사용하세요.

## v2.9 — 암묵 호출 연결 (2026-10-21, LJY `calls_implicit` 이식)

호출문 없이 Python이 실행하는 메서드를 `CALLS_IMPLICIT`로 연결합니다. 받는 객체의 타입은 기존 객체 흐름 분석 결과를 그대로 쓰며, **타입을 아는 프로젝트 객체에 대해서만** 기록합니다.

| `implicit_kind` | 예시 → 실행되는 메서드 |
|---|---|
| `property_get` / `property_set` / `property_delete` | `v.x`, `v.x = 1`, `del v.x` → property getter/setter/deleter |
| `operator` | `a + b` → `__add__` (반대쪽 `__radd__` 포함), `==`, `<`, `in` → `__contains__`, `-a`, `a += b` → `__iadd__` 또는 `__add__` |
| `subscript` | `a[k]`, `a[k] = v`, `del a[k]` → `__getitem__` / `__setitem__` / `__delitem__` |
| `context_manager` | `with m:` → `__enter__`, `__exit__` (async 포함) |
| `iteration` | `for x in a`, 컴프리헨션 → `__iter__` (async 포함) |
| `protocol_builtin` | `len(a)`, `iter(a)`, `str(a)`, `repr(a)`, `bool(a)` 등 → 대응하는 특수 메서드 |

- 결과는 `graph_result.json`의 **`implicit_edges`에 별도로** 저장하며 `edges`, 해결/미해결 수, 호출 상태는 바뀌지 않습니다. `implicit_summary`와 `implicit_calls.csv`도 생성합니다.
- 내장/표준 라이브러리 부모가 그 메서드를 먼저 제공하는 경우(`class Table(dict)`의 `t[k]`), 타입을 모르는 객체, 같은 이름의 프로젝트 함수(`def len`)는 연결하지 않습니다.
- 객체 흐름의 분석 예산을 건드리지 않도록 구현해, 5개 저장소의 기존 그래프가 완전히 같음을 확인했습니다.

**실행 검증** (각 저장소 테스트 실행 중 기록한 함수 간 호출 기준, 보고서와 같은 커밋)

| 저장소 | 재현율: 명시적 호출만 → 암묵 호출 포함 | 암묵 간선 중 실제 호출로 확인 |
|---|---|---|
| Rich | 59.1% → **74.0%** | 776 / 812 (95.6%) |
| Requests | 72.6% → **83.8%** | 91 / 133 |
| Click | 47.2% → **49.3%** | 107 / 119 |

Requests의 미확인 사례는 `__ne__` 안의 `self == other` → `__eq__`처럼 테스트가 실행하지 않은 경로입니다(이 환경에서 Requests 테스트 205개가 네트워크 의존으로 실행되지 않음).

## v2.8 — 외부 호출 분류 보완 (2026-10-21, LJY 분류 규칙 이식)

LJY 프로토타입의 외부 호출 분류 규칙을 JSE의 엄격한 상태 체계에 맞게 옮겼습니다. **근거가 확실한 규칙만 `KNOWN_NON_INTERNAL`로 올리고**, 추정은 `UNKNOWN` 상태를 유지한 채 `likely_origin` 필드로만 붙입니다.

| 새 규칙 | 상태 | 근거 |
|---|---|---|
| `INHERITED_FROM_EXTERNAL_BASE` | KNOWN | 메서드 탐색이 프로젝트 정의보다 먼저 내장/표준 라이브러리 부모(속성 목록 확인됨)에 닿음. 예: `self.assertRaises` → `unittest.TestCase` |
| `EXTERNAL_CLASS_INSTANCE` | KNOWN | 표준 라이브러리 클래스 생성자로 만든 객체이고 그 클래스 소스에 해당 속성이 있음. 예: `q = queue.Queue(); q.put()` |
| `FLOW_ALIAS_OF_IMPORT` | KNOWN | 변수의 모든 도달 값이 외부 import의 별칭. 예: `k = ctypes.windll.kernel32; k.OpenProcess()` |
| `RESULT_OF_EXTERNAL_CALL` | UNKNOWN + 추정(inferred) | 외부 함수가 돌려준 값의 메서드. `copy.copy`처럼 호출자 객체를 돌려줄 수 있는 함수는 제외 |
| `POSSIBLY_INHERITED_FROM_EXTERNAL_BASE` | UNKNOWN + 추정(inferred) | 서드파티 부모가 있어 MRO가 열려 있고 프로젝트 어디에도 그 메서드가 없음 |
| `BUILTIN_METHOD_NAME` | UNKNOWN + 추정(heuristic) | 이름이 str/list/dict 등의 메서드와 같고 같은 이름의 프로젝트 함수가 없음 |

- 같은 이름의 인스턴스 속성(`self.start = cb`), 프로젝트 자식 클래스의 재정의, 모듈·객체 속성 바꿔치기(`q.put = cb`, `threading.Thread = ...`)가 있으면 KNOWN으로 올리지 않습니다.
- `call_statuses.json` 요약에 `known_non_internal_rules`, `unknown_likely_origins`가, CSV에 `likely_origin` 열이 추가됩니다. `verify_v231.py`의 허용 규칙 목록에 새 KNOWN 규칙 3개를 등록했습니다.

**호출 분류 완료율** (내부 연결 + 근거 있는 외부 분류 / 전체 호출, 보고서와 같은 커밋)

| 저장소 | 원본 JSE | v2.7 | v2.8 | v2.8 + 추정 포함 |
|---|---|---|---|---|
| schedule | 61.6% | 61.6% | **69.1%** | 71.4% |
| Sublist3r | 59.1% | 65.4% | **70.6%** | 91.4% |
| Requests | 75.2% | 75.3% | **76.1%** | 84.2% |
| Click | 75.8% | 75.9% | **76.2%** | 83.1% |
| Rich | 80.7% | 83.1% | **83.4%** | 85.5% |

- 내부 호출 판정은 바뀌지 않았습니다(5개 저장소 모두 RESOLVED_INTERNAL 집합 동일).
- 실행 검증: Requests/Click/Rich 테스트 실행 기록과 대조한 결과, 새로 KNOWN이 된 60개 지점 중 실제로 프로젝트 함수가 호출된 곳은 1곳입니다. Click 테스트가 `subprocess.Popen`을 가짜 클래스로 바꿔치기(monkeypatch)한 경우로, 정적 분석 범위 밖임을 evidence에 명시했습니다.

## v2.7 — 상속 메서드 연결 보완 + Rich 속도 (2026-10-17, 3종 비교 결과 반영)

10/17 프로토타입 비교에서 JSE 개선 대상으로 지적된 **Sublist3r 상속 후보 32개**와 **Rich `self` 메서드 누락**을 고쳤습니다. 원인은 두 가지였습니다.

1. **외부·내장 부모 클래스** (`Exception`, `multiprocessing.Process` 등)가 있으면 MRO 전체를 포기했습니다. → `external_bases.py`가 내장 타입은 `dir()`, 표준 라이브러리는 **소스를 AST로만 읽어** 그 클래스가 가진 이름(인스턴스 속성 포함)을 계산합니다. 외부 클래스는 MRO의 불투명 노드가 되고, **그 이름을 정의하지 않을 때만** 뒤쪽 프로젝트 부모의 메서드로 연결합니다. 근거는 간선의 `external_bases_checked`에 남습니다. 서드파티 패키지 부모는 여전히 미확정입니다.
2. **클래스 데코레이터·메타클래스**가 있으면 같은 클래스 안의 메서드조차 연결하지 않았습니다. → `dataclass`, `total_ordering`, `final`, `metaclass=ABCMeta` 등 클래스를 그대로 돌려주는 것이 확실한 표준 항목은 정상 MRO로 처리합니다. 그 밖의 데코레이터(`@rich_repr` 등)는 **클래스에 직접 정의된 메서드만** 연결하고 `certainty=class_customization_may_change`로 표시합니다.

| 저장소 (보고서와 같은 커밋) | 해결된 호출 지점 이전 → 이후 | 잃은 연결 |
|---|---|---|
| schedule | 504 → 504 | 0 |
| Sublist3r | 92 → **124** | 0 |
| Requests | 1,054 → 1,056 | 0 |
| Click | 3,096 → 3,106 | 0 |
| Rich | 5,463 → **5,707** | 0 |

- 보고서의 Rich 누락 지점 `style.py:712`, `pretty.py:457`, `layout.py:217/305/313` 모두 연결됩니다.
- **실행 검증** (각 저장소 테스트를 실제로 돌려 함수 간 호출을 기록): Rich 재현율 52.0% → **59.1%**, 새 간선 393개 중 365개가 실제 호출로 확인. Click 새 간선 13/13 확인. Requests 19/39 확인, 나머지 20개는 테스트가 실행하지 않은 `raise InvalidURL(...)` → `RequestException.__init__` 경로입니다.
- **속도**: 객체 흐름 상태 병합 최적화로 Rich 분석 시간이 이 머신 기준 242초(원본) → 약 24~27초입니다. 출력은 최적화 전과 같습니다.
