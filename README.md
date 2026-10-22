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

## v2.4 보완 — `join` 호출 출처 판정 (2026-10-05)

- `"-".join(values)` / `b",".join(values)` / f-string 리터럴의 `join`은 AST 수신 객체가 `str`/`bytes`인 것을 확인해 **내장 메서드(BUILTIN)**로 분류합니다.
- `t = threading.Thread(...); t.join()`처럼 흐름 분석상 생성자와 표준 라이브러리 import 출처가 일치하는 경우 및 **모든 유한한 후보 클래스가 `multiprocessing.Process.join`을 상속**하는 일부 경우에 **추론된 표준 라이브러리 호출(STDLIB)**로 분류합니다. 재할당·불명확한 상속이나 로컬 `join` 구현 등은 보수적으로 남깁니다.
- 이 보완은 **내부 Call Graph 간선을 추가하지 않으며**, 호출 상태와 일부 미해결 분류만 개선합니다. 모든 `.join` 호출을 해결한다는 뜻이 아닙니다.
- Sublist3r: 514개 호출에서 내부 연결 **124개 유지**, `KNOWN_NON_INTERNAL` **181 → 190**, `UNKNOWN` **209 → 200**. 13개의 직접적인 `.join()`은 전부 내부가 아닌 출처로 분류하지만, `join(...).strip()`의 바깥쪽 `strip`은 미해결로 남습니다.
- 테스트 **104개 통과** (기존 93개 + 신규 11개), Sublist3r 및 Click 정적 검증 통과. 세부 한계 및 근거: [`JOIN_FIX_NOTES_KO.md`](JOIN_FIX_NOTES_KO.md).

## v2.4 — 매개변수 타입 힌트 기반 메서드 추론 (NEW)

- `def go(ctx: Context): ctx.invoke()` 등 **프로젝트 내부 클래스로 확인되는 단순 타입 힌트**를 통해 기존에 미해결이던 메서드를 연결합니다.
- 문자열 전방 참조(`"Context"`), 명시적 import 별칭, 상속 메서드, 키워드 전용/비동기 함수의 타입 힌트를 지원합니다.
- AST 데이터 흐름에서 파라미터가 **재할당·삭제·다른 객체로 변경**되면 기존 타입 힌트를 그대로 확신하지 않습니다. 분기에서 가능한 후보가 여러 개라면 `MAY_CALL`로만 기록합니다.
- 타입 힌트는 Python에서 실행 시 강제되지 않으므로, 타입 힌트 기반 호출은 그래프에 `instance_parameter_type_hint`와 `declared_parameter_type_may_call`, 호출 상태에 `MAY_CALL`로 표시합니다.
- `Optional[Context]`, `Context | None`, `list[Context]`, 복잡한 제네릭, 실제 다형성 디스패치 등은 자동 추측하지 않습니다.
- `verify_v24.py`와 `tests/test_v24.py`를 추가했습니다. 테스트 예제는 `examples/v24_types`에서 직접 실행할 수 있습니다.
- **전체 실행법·동일 소스 비교·실측 성과·제약사항:** [`V24_CHANGES_KO.md`](V24_CHANGES_KO.md).
- 참고: 아래 v2.3.1 기록의 “간선을 바꾸지 않았다”는 문장은 **그 당시 버전의 변경점**을 뜻합니다. v2.4는 새 간선을 추가합니다.

## v2.3.1 — 전체 호출 상태 분리 (NEW)

- 전체 AST 호출을 `RESOLVED_INTERNAL` / `KNOWN_NON_INTERNAL` / `UNKNOWN`으로 나눕니다.
- 호출 대상 출처·재할당 충돌·확실성·판정 규칙은 `call_statuses.json`, `call_statuses.csv`에 기록합니다.
- 오프라인 `report.html`에서 **All call statuses** 탭으로 전체 호출과 상태별 필터를 열 수 있습니다.
- 새 `verify_v231.py`로 기존 v2.3 16개 무결성 검사와 신규 호출 상태 10개 검사를 실행합니다.
- 기존 `graph_result.json` 간선, v2.3 호출 카운트와 미해결 재분류는 바꾸지 않았습니다.
- **전체 실행 지침과 한계는 [`V231_CHANGES_KO.md`](V231_CHANGES_KO.md)를 먼저 읽으세요.**

## v2.3 — 중복 심볼·재수출 import 검증

- `@overload` 타입 선언은 별도로 보관하고 실행 구현만 그래프 노드로 생성합니다.
- 상대 import 및 프로젝트 모듈을 통한 표준/외부/내부 함수 재수출을 추적합니다.
- 그래프 중복 노드·간선을 정리하고, 소스 위치를 교차 확인하는 범용 `verify_v23.py`를 추가했습니다.
- 이전 v2.2에 대한 설명과 Sublist3r 전용 `verify_v22.py`는 과거 버전 기록 및 비교를 위해 남겨 두었습니다.

## v2.2 — 함수 밖 내부 호출 + 클래스 컬렉션 추적

이 버전은 **v2.1을 기반으로 정적 호출 관계를 실제로 추가**하는 업데이트입니다.

1. **MODULE 노드**: 함수 밖에서 수행하는 프로젝트 내부 함수 호출(예: `if __name__ == "__main__": interactive()`)을 `MODULE:sublist3r → FUNCTION:sublist3r.interactive`와 같이 연결합니다. 모듈 안에서 정의되기 **이전**의 함수로 호출 간선을 추측하지 않습니다. `evidence.module_level=true`와 `evidence.certainty=module_initialization_may_execute`로 나타냅니다.
2. **클래스 컬렉션 추적**: 유한한 리터럴 클래스 리스트/튜플/집합, 딕셔너리의 클래스 값, 리스트의 `append`, `if/else` 분기 병합, `for`와 **리스트 컴프리헨션**의 반복 변수에 전달되는 클래스 참조를 근사 추적합니다. `supported_engines` 딕셔너리 → `chosenEnums` 리스트 → `enum()`으로 전달된 클래스 후보를 해석할 수 있습니다.
3. **may-call**: 여러 클래스를 선택할 수 있으면 하나를 임의로 확정하지 않고 후보별 `INSTANTIATES`(+ 정의된 `__init__`에 대한 `CALLS`) 간선을 만들고 `ambiguous_calls`와 `evidence.certainty=multiple_possible_targets`에 명시합니다. 외부 또는 알 수 없는 값이 섞이면 `conditional_may_call`로 표시합니다.
4. **기존 재분류 유지**: 미해결 호출의 `category`, `reason`, `scope_context`, `confidence`, CSV/HTML 보고서를 유지합니다. 새로 해결된 호출은 미해결 목록에서 빠집니다.

**주의:** v2.1 결과를 단순히 `reclassify_existing.py`로 갱신해도 새로운 간선은 만들어지지 않습니다. **`python main.py <Sublist3r 폴더>`로 전체 재분석**해야 합니다. 기존 v2.1 `output` 폴더는 비교용으로 보존하세요.

## v2.1 — 유지된 미해결 호출 진단

- `reason`: 호출 대상 해석을 실패한 코드상의 이유.
- `category`: 내장 함수/메서드, 표준/외부 라이브러리, 내부 후보, 간접 호출, 필드 체인 등의 10가지 휴리스틱 범주.
- `scope_context`: 함수 내부와 모듈 최상위를 별도 축으로 구분.
- `classification_evidence`와 `candidate_targets`: 분류 근거 및 미검증 후보. 함수 간 관계와는 별개입니다.
- JSON과 UTF-8 BOM CSV 및 오프라인 HTML로 열람할 수 있습니다.

## Windows 실행 — 기존 Sublist3r 저장소 재사용

압축을 풀고 `main.py`가 있는 폴더에서 아래 명령어를 실행합니다.

```powershell
# v1에서 이미 다운로드한 원본 Sublist3r 경로를 그대로 사용해도 됩니다.
python main.py "C:\Users\c\Downloads\knitcode_sublist3r_v1\knitcode_sublist3r_v1\Sublist3r"
python -m unittest discover -s tests -v
start output\report.html
```

새 폴더에서 Sublist3r를 다시 클론한 경우에는 `python main.py Sublist3r`로 실행합니다. **실제 Sublist3r 프로그램을 실행하거나 네트워크 스캔할 필요는 없습니다.** Git commit은 `output`의 JSON에 자동 기록합니다.

## 무엇이 달라졌나?

**v1:** 조건문 내부의 변수 대입을 `control_depth > 0`이라 판단하여 안전을 위해 해석하지 않았습니다. 따라서 같은 `if` 블록 안에서 `pscan = portscan(...)` 후 `pscan.run()`도 놓쳤습니다.

**v2 (v2.2에서도 유지):** `flow_analysis.py`를 추가했습니다. AST 문장 순서에 따라 추상 변수 상태를 전달하고, `if/else` 각 경로를 독립 분석한 뒤 가능한 타입을 합칩니다.

```python
if ports:
    pscan = portscan(subdomains, ports)
    pscan.run()   # portscan.run로 연결 (블록 안에서 유효)
```

```python
if flag:
    obj = A()
else:
    obj = B()
obj.run()  # may-call: A.run, B.run 2개 후보
```

```python
if flag:
    obj = A()
obj.run()  # A.run의 조건부 may-call, may_be_unbound=true
```

조건부 호출은 **무조건 발생한다고 확정하지 않습니다**. `output/graph_result.json`의 `conditional_calls`, Edge evidence `certainty` 및 `may_be_unbound` 필드로 별도 표시합니다. 호출 가능 대상이 복수이면 `ambiguous_calls`로 별도 저장합니다. 특정 호출에 근거가 없으면 `unresolved_calls`에 남깁니다.

## 출력에 추가된 통계

- `module_level_resolved_sites`: MODULE를 호출자로 연결한 call-site 수.
- `collection_indirect_resolved_sites`: 컬렉션 원소를 추적해 해결한 간접 call-site 수.
- `node_count`와 `edge_count`는 MODULE 노드 및 새 호출 간선 때문에 **v2.1과 달라질 수 있습니다**. 내부 함수의 정확도를 개수만으로 판단할 수는 없습니다.

## 산출물

- `output/ast_result.json`: 원본 AST facts 및 호출 위치의 흐름 스냅샷
- `output/symbol_result.json`: 심볼·import·assignment 및 flow snapshot 전달
- `output/graph_result.json`: `CONTAINS`, `INHERITS`, `CALLS`, `INSTANTIATES`, 호출 근거, 다중 후보, 조건부 호출, 미해결
- `output/report.html`: 인터넷 없이 열리는 HTML 보고서 (카테고리 버튼과 필터 포함)
- `output/unresolved_classification.json`: 재분류 결과 전체 + 요약
- `output/unresolved_classification.csv`: Excel에서 열어 정렬·검토 가능한 UTF-8 BOM CSV

## v2.1 ↔ v2.2 결과 비교

두 분석이 같은 Git commit인지 확인한 다음, v2 디렉터리에서 다음 명령을 실행하면 새로 해결한 호출, 잃어버린 연결 및 변경된 호출 대상을 출력합니다.

```powershell
python compare_results.py "C:\Users\c\Downloads\knitcode_sublist3r_v2_1 (1)\knitcode_sublist3r_v2_1\output\graph_result.json" output\graph_result.json
```

**개수가 늘어났다는 사실만으로 성능 개선이 입증되지는 않습니다.** 새 관계를 원본 코드와 검증해야 합니다.

## v2.2까지의 자동 테스트

`python -m unittest discover -s tests -v` — **46개 테스트 (v2.1 33개 + v2.2 13개)**: 기존 v1 기능 13개에서 기존 if 분기 기대 결과를 v2 의미에 맞게 변경하고 9개 회귀 테스트를 추가했습니다. 함수별 스코프, 변수 재할당, 조건문 안/밖, 두 분기 병합, 불확실한 외부 값, 중첩 함수, 함수 import와 상속 등을 검증합니다.

## 범위와 한계

- **구조화된 `if/else`와 단순 반복문 추상 상태를 다루는 경량 데이터 흐름 분석**이지, Python 전체를 대상으로 한 완전한 CFG·고정점 분석이나 일반적인 타입 추론기가 아닙니다.
- 반복 실행 간 상태 고정점, 예외를 포함한 복잡한 실행 경로, `break`/`continue`, 동적 호출(`getattr`, `eval`, monkey patch), 복잡한 comprehension·generator 표현식, 다중상속 C3 MRO 및 외부 라이브러리 객체의 정확한 메서드 타입은 보장하지 않습니다. `try/except`, `while`은 근사 병합만 지원합니다.
- `resolved_call_sites`/`unresolved_call_sites`는 자체 분류 결과이며, **Ground Truth 기반 Precision/Recall/F1 값이 아닙니다**. 다른 팀원과 비교하려면 같은 commit, 같은 내부 호출 정답 집합과 동일한 평가 규칙이 필요합니다.
- 사용자 PC에서 확인한 기존 v2.1 결과는 Sublist3r 호출 514개 중 117개 resolved/397개 unresolved입니다. **v2.2의 실제 Sublist3r 결과는 해당 저장소를 로컬에서 재분석한 뒤 검증**해야 합니다. 이 패키지는 그 원본 Sublist3r 파일을 포함하지 않습니다.
- `flow_analysis.py`는 리스트 원소의 **가능한 클래스 집합**을 추적합니다. `append`로 다른 리스트의 별칭을 통해 수정하는 경우, `extend`, 동적 dict 갱신, 복잡한 loop fixed-point는 지원하지 않습니다. 딕셔너리 키를 정적으로 모르면 가능한 값을 넓게 포함하므로 **false positive 가능성을 평가**해야 합니다.
- 전체 모듈 초기화의 조건/예외와 모듈 간 import 수행 순서를 완전히 실행 분석하지 않습니다. 클래스 본문 안에서 실행되는 호출은 별도 한계입니다.
- 자식 클래스 오버라이딩으로 발생하는 다형성 동적 디스패치(C3 MRO 등)는 **이번 단계에서는 변경하지 않았습니다**.


## v2.2 검증 도구 (추가)

`verify_v22.py`는 동일한 Sublist3r 커밋의 v2.1/v2.2 결과를 비교해 회귀 오류를 찾습니다. 원본 코드 실행 없이 JSON/AST를 검사합니다.

```powershell
python verify_v22.py --old "C:\Users\c\Downloads\knitcode_sublist3r_v2_1 (1)\knitcode_sublist3r_v2_1\output\graph_result.json" --new output\graph_result.json --repo "C:\Users\c\Downloads\knitcode_sublist3r_v1\knitcode_sublist3r_v1\Sublist3r"
```

`[PASS]`는 검사 통과, `[FAIL]`는 예상과 다른 결과이며 종료 코드 1을 반환합니다. `--repo`로 소스 AST 대조를 추가할 수 있습니다. `--old` 생략 시 그래프 무결성 및 7개 예제 검증만 수행합니다.

더 나아가 `gold_annotations.template.json`의 7개 사례를 원본 코드로 **직접 검토한 뒤** `reviewed: true`로 수정하면:

```powershell
python verify_v22.py --new output\graph_result.json --gold gold_annotations.template.json
```

검토 완료된 호출에 한해 TP/FP/FN과 Precision·Recall·F1을 계산합니다. **초안은 전부 `reviewed=false`이며, 이 값은 Sublist3r 전체 정확도가 아닙니다.** v2.1 대비 회귀검증과 정확도 평가를 혼동하지 마세요.

---

## v2.3: Requests cross-project hardening

v2.3 extends v2.2 and retains its static-only, no-target-execution behavior.
The following were changed:

1. **`@overload` typing stubs** are kept in `symbol_result.json` as
   `overload_declarations` metadata, and only an actual implementation (if
   present) creates a callable FUNCTION/METHOD node. Unimplemented overload-only
   stubs are not assumed callable. Non-overload repeated definitions have a
   deterministic last-definition fallback; dynamic conditional redefinition
   remains a limitation.
2. **Relative imports and chained re-exports** are traced through concrete
   module import facts (`from .compat import urlparse` ->
   `urllib.parse.urlparse`). Imports resolved to the stdlib/external dependencies
   do **not** fabricate internal call-graph edges. Conditional imports that
   point to mixed kinds are reported as `DYNAMIC_UNKNOWN`, with candidate
   origins in `classification_evidence`.
3. **Stable identities**: duplicate node IDs, `CONTAINS` relations and
   repeated call-site edges are deduplicated. Added static graph integrity
   checks and 11 cross-project fixture tests.

### Recommended Windows commands

```powershell
# Before upgrading: back up your v2.2 output elsewhere.
python -m unittest discover -s tests -v

# The repo folder must contain the installed package layout `requests` under `src`.
python main.py "C:\Users\c\Downloads\requests-main\requests-main\src" --output-dir output_requests
python verify_v23.py --graph output_requests\graph_result.json --symbols output_requests\symbol_result.json --repo "C:\Users\c\Downloads\requests-main\requests-main\src"

# Analyze the original pinned Sublist3r for a second cross-project regression.
python main.py "C:\Users\c\Downloads\knitcode_sublist3r_v1\knitcode_sublist3r_v1\Sublist3r" --output-dir output_sublist3r
python verify_v23.py --graph output_sublist3r\graph_result.json --symbols output_sublist3r\symbol_result.json --repo "C:\Users\c\Downloads\knitcode_sublist3r_v1\knitcode_sublist3r_v1\Sublist3r"
```

Optional comparison after saving the v2.2 graph as a separate file:

```powershell
python verify_v23.py --graph output_requests\graph_result.json --symbols output_requests\symbol_result.json --repo "C:\Users\c\Downloads\requests-main\requests-main\src" --old old_requests_graph_result.json
```

**Important:** This is not a true accuracy benchmark. Call resolution count
alone is not Precision, Recall, or F1. For teammate comparisons, use an identical
source commit, package root and call extraction conventions. ZIP downloads do
not carry a `.git` directory and the reported commit will be null. The example
Requests data uploaded for v2.2 consists of graph/classification JSON only;
full v2.3 rescan requires the original Python source checkout on your PC.

## v2.4 확장 실험: 내장 자료형 및 라이브러리 소스 분석

이 ZIP에는 `builtin_receivers.py`, `dependency_analysis.py`, `tests/test_builtin_receivers.py`,
`tests/test_dependency_analysis.py`가 추가되어 있다.

`str`, `list`, `dict` 등 일부 기본 자료형의 메서드 출처를 변수 흐름으로
보수적으로 판정한다. `--analyze-deps` 실행 시에는 표준 라이브러리 및
접근 가능한 외부 패키지의 `.py`/`.pyi`를 AST로 분석해
`dependency_graph.json`, `dependency_calls.csv`를 **별도 생성**한다.

```bash
python main.py ./example_repo --analyze-deps --dep-root ./venv/lib/python3.13/site-packages --output-dir output
```

Windows는 `--dep-root .venv\\Lib\\site-packages` 등 사용 환경에 맞는 경로를 지정한다.
전체 범위·신뢰도·한계·출력 파일은 [`EXTENDED_ANALYSIS_KO.md`](EXTENDED_ANALYSIS_KO.md) 참고.

## v2.5 Object Flow 확장 (2026-10-16)

최신 변경 내용과 정확성 주의사항은 [`STATIC_FLOW_EXPANSION_REPORT_KO.md`](STATIC_FLOW_EXPANSION_REPORT_KO.md)를 참고하세요.

- `fn = obj.method; fn()`의 바운드 메서드 값, `obj()`의 정적으로 추론된 `__call__` 연결 지원.
- `try`/`except`/`finally`, `for`/`while`, `with` 내부에서 Object Flow가 호출을 방문하는 범위 확대. 반복문은 **0~1회 근사**로 분석하므로 모든 반복 횟수의 고정점 분석이 아닙니다.
- 유한한 리스트/튜플/집합 원소의 클래스·객체 값을 구분하여 `for cls in classes: cls()`처럼 호출되는 코드를 추적합니다.
- 이전 프로젝트 전체에 공유하던 50,000 expression-step 제한 대신 독립 진입 컨텍스트마다 6,000 step 제한을 적용하고, `graph_result.json`에 `object_flow_stats`를 기록합니다. 재귀 깊이 제한은 유지됩니다.
- AST 소스를 직접 검사하는 정적 분석이며 **분석 대상 Python 코드는 실행하지 않습니다**. `UNKNOWN → RESOLVED_INTERNAL`이 전부 TP라는 뜻은 아닙니다.
- ZIP에 보관된 `output_*_context`는 이전 실행 자료로 최신 소스의 출력이 아닙니다. 새 결과가 필요하면 `python main.py ... --output-dir ...`로 다시 실행하세요.
