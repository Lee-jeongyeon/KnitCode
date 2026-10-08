# KnitCode — 코드 구조 분석기 (v2)

Python 프로젝트의 함수·클래스 호출 관계를 AST로 추출해서, **파일 경로가 포함된 고유 ID**를 가진 그래프(JSON)로 만드는 분석기입니다.
프로젝트 안으로 이어지는 호출은 연결하고, 프로젝트 밖으로 나가거나 못 푼 호출은 **왜 못 풀었는지 분류**해서 기록합니다.
이후 Git 이력·테스트 분석과 결합해 "코드를 수정하면 어디까지 영향이 가는지"를 보여주는 KnitCode의 분석 엔진 파트입니다.

---

## 빠른 시작

```bash
python analyze_project.py <분석할 프로젝트 폴더>
```

- Python **3.9 이상** (`ast.unparse` 사용), 별도 패키지 설치 없음
- 결과: 터미널 요약 + `graph_<프로젝트명>.json` (프로젝트마다 따로 저장되어 서로 덮어쓰지 않음)
- 표준 라이브러리 판별이 환경에서 제대로 되는지 확인하는 한 줄 (결과가 `[True, True, False, True, True]`이면 정상)

```bash
python -c "import analyze_project as a; print([a.is_stdlib(x) for x in ('os','math','requests','urllib','typing')])"
```

---

## v1 → v2, 무엇이 달라졌나

| 구분 | v1 | v2 |
|---|---|---|
| 미해결 호출 | 한 덩어리 (`unresolved`) | **원인별 8개 분류**. 외부 호출은 외부 노드로 그래프에 연결 |
| 성과 지표 | "해석률" 24.9% / 28.0% | **"분류 완료율"** 65.2% / 73.0% (추정 포함 86.4% / 87.3%) |
| 상속 처리 | 자식 → 부모 방향만 (`self.m()`, `Parent.m(self)`, `super().m()`) | + **부모 → 자식 오버라이드 후보 간선** (`calls_override`) |
| 부모 클래스 해석 | `Base`, `module.Base` | + `as` 별칭, `Base[T]` 제네릭, `a.b.Base` 점 표기 |
| 재수출(re-export) | 못 따라감 (`compat.py` 같은 우회 import를 프로젝트 내부로 오인) | 최대 6단계까지 따라가 **원래 출처**로 분류 |
| 지역 변수 추적 | 함수 호출 결과(`x = func()`)도 클래스 인스턴스로 오인 (버그) | 클래스만 인정하도록 수정 |
| 결과 파일 | `graph.json` (매번 덮어씀) | `graph_<프로젝트명>.json` |
| 분류 스크립트 | `classify_unresolved.py` 별도 실행 | 분석기에 내장 (별도 실행 불필요) |

> **내부 연결 수는 v1과 동일합니다** (Sublist3r 117건, requests 260건). v2는 기존에 연결하던 것을 바꾸지 않고, 못 푼 것의 정체를 밝히고 놓친 연결을 추가하는 방향의 변경입니다.

### "해석률"에서 "분류 완료율"로 바꾼 이유

v1의 해석률은 분모에 `print`, `str.split`처럼 **원래 프로젝트 안 호출이 아닌 것**까지 섞여 있어서 실제보다 훨씬 낮아 보였습니다.

```
분류 완료율 = (프로젝트 내부로 연결된 호출 + 외부로 분류된 호출) / 전체 호출
```

진짜로 남은 숙제는 **미확정(타입 추론이 필요한 호출)** 이며, Sublist3r 13.6%, requests 12.7%입니다.

---

## 분석 원리

1. **파일 파싱**: 폴더를 순회하며 `.py`를 AST로 파싱 (`.git`, `venv` 등 제외)
2. **정의 수집**: 함수·클래스·메서드마다 `파일경로::클래스명.함수명` 형태의 고유 ID 부여
3. **import 해석**: `import x`, `from x import y`, 상대 import가 어느 모듈을 가리키는지 매핑
4. **상속 관계 수집**: 프로젝트 안 부모 / 프로젝트 밖 부모를 나누어 기록
5. **호출 해석**: 호출문이 프로젝트 안의 어느 함수인지 추적
6. **실패한 호출 분류**: 못 푼 호출을 아래 분류 체계로 구분
7. **오버라이드 후보 연결**: `self.method()`를 자식 클래스가 덮어쓴 경우 그 후보까지 연결

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
| **미확정** | `unknown_receiver` | 변수 타입을 알아야 해석 가능 (**타입 추론 영역**) | `parser.add_argument` |
| | `local_callable` | 매개변수·중첩 함수·변수에 담긴 함수 호출 | `hash_utf8(...)`, `dict_class(...)` |
| | `project_unresolved` | 프로젝트 안 이름인데 못 찾음 (**점검 필요**) | (v2 검증 기준 requests 0건) |

- 추정 라벨은 `confidence`로 신뢰도를 구분합니다: `heuristic`, `weak`(`get`, `join`처럼 다른 라이브러리에도 흔한 이름)
- 프로젝트 안 클래스가 같은 이름의 메서드를 가지고 있으면 추정하지 않고 `unknown_receiver`로 남깁니다 (프로젝트 내부 호출을 외부로 숨기지 않기 위해)

---

## 출력 JSON 구조

```
nodes             프로젝트 안의 함수·클래스·메서드
edges             프로젝트 내부 호출 (type: calls | calls_override)
external_nodes    외부 카테고리 노드 (EXTERNAL::stdlib 등 5개)
external_edges    외부로 나가는 호출 (같은 호출은 count로 묶음)
unresolved        끝까지 확정하지 못한 호출 (category 포함)
summary           통계 요약
```

실제 출력 예시 (Sublist3r):

```jsonc
// 오버라이드 후보: 부모의 self.generate_query()가 실제로는 자식 것이 실행될 수 있음
{"from": "sublist3r.py::enumratorBase.enumerate",
 "to": "sublist3r.py::GoogleEnum.generate_query", "type": "calls_override", "line": 227}

// 표준 라이브러리 호출
{"from": "sublist3r.py::parser_error", "to": "EXTERNAL::stdlib", "type": "calls_external",
 "category": "stdlib", "package": "sys", "detail": "sys.exit", "confidence": "certain", "count": 1, "line": 90}

// 외부 부모에게 물려받은 메서드
{"from": "subbrute/subbrute.py::run", "to": "EXTERNAL::inherited_external", "type": "calls_external",
 "category": "inherited_external", "package": "multiprocessing",
 "detail": "verify_nameservers_proc.start <- 부모 multiprocessing.Process", "confidence": "certain", "count": 1, "line": 442}

// 미확정 (타입 추론 필요)
{"from": "sublist3r.py::parse_args", "expr": "parser.add_argument", "category": "unknown_receiver", "line": 98}
```

그래프를 그릴 때는 `edges`는 실선, `calls_override`는 점선, `external_edges`는 회색 외부 노드로 표현하면 됩니다. 외부 노드는 카테고리 단위(5개)라 노드 수가 늘지 않고, 자세한 호출 내용은 간선의 `detail`·`package`에 보존되어 있습니다.

---

## 상속 처리

| 호출 형태 | 처리 방식 | 버전 |
|---|---|---|
| `self.method()` | 현재 클래스에 없으면 부모 클래스로 타고 올라가며 탐색 (다단계·다중 상속 지원) | v1 |
| `ParentClass.__init__(self, ...)` | 클래스 이름으로 직접 호출하는 구식 패턴 | v1 |
| `super().method()` | 현재 클래스를 건너뛰고 부모부터 탐색 | v1 |
| 자식이 오버라이드한 메서드 | `self.method()` 호출 시 자식 클래스의 같은 이름 메서드를 `calls_override`로 추가 | **v2** |
| 외부 부모에게 물려받은 메서드 | 상속 사슬에 프로젝트 밖 부모가 있으면 `inherited_external`로 분류 | **v2** |

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

| | [Sublist3r](https://github.com/aboul3la/Sublist3r) | [requests](https://github.com/psf/requests) |
|---|---|---|
| 파일 / 노드 / 전체 호출 | 4 / 115 / 469 | 19 / 283 / 929 |
| 내부 연결 | 117 (24.9%) | 260 (28.0%) |
| 오버라이드 후보 간선 | 33 | 1 |
| 내장 `builtin` | 99 (21.1%) | 238 (25.6%) |
| 표준 라이브러리 `stdlib` | 75 (16.0%) | 152 (16.4%) |
| 외부 패키지 `third_party` | 13 (2.8%) | 19 (2.0%) |
| 외부 부모 `inherited_external` | 2 (0.4%) | 9 (1.0%) |
| 추정 `builtin_type_method` | 99 (21.1%) | 133 (14.3%) |
| 미확정 `unknown_receiver` | 63 (13.4%) | 97 (10.4%) |
| 미확정 `local_callable` | 1 (0.2%) | 21 (2.3%) |
| **분류 완료율 (확실한 것만)** | **65.2%** | **73.0%** |
| **분류 완료율 (추정 포함)** | **86.4%** | **87.3%** |

### Sublist3r 개선 과정 (내부 연결 수)

| 단계 | 내부 연결 |
|---|---|
| 최초 (단일 파일 방식을 프로젝트 전체로 확장) | 79 |
| + 상속 체인 탐색 (`self.method()`, `Parent.method(self)`) | 107 |
| + 지역 변수 타입 추적 (`x = ClassName()`) | 109 |
| + `super().method()` | **117** |

미해결 호출을 유형별로 분류하고, 가장 많은 유형부터 고치는 방식으로 진행했습니다.

### 핵심 발견

- **상속 로직이 프로젝트에 한정되지 않음**: Sublist3r에서 만든 상속 처리가 구조가 더 복잡한 requests(다중 상속, 상대 import)에서도 `self.method()` 미해결을 거의 만들지 않았습니다. 남은 2건도 부모가 표준 라이브러리(`MutableMapping`)라 정상적인 외부 호출입니다.
- **재수출이 분류를 왜곡함**: requests는 `compat.py`가 `urllib.parse`를 가져와 다시 내보내고 다른 파일이 `from .compat import urlparse`로 씁니다. 이를 따라가지 않으면 표준 라이브러리 호출 64건이 "프로젝트 안"으로 잘못 분류됩니다.
- **지역 변수 추적 버그 발견**: `x = func()`를 클래스 인스턴스 생성으로 오인하던 문제를 찾아 수정했습니다. 잘못된 연결을 만들지는 않았지만 분류를 흐리고 있었습니다.

---

## 알려진 한계

- **타입 추론이 필요한 호출**: `parser.add_argument`처럼 변수가 어떤 객체인지 알아야 풀리는 호출은 미확정으로 남깁니다 (Sublist3r 63건, requests 97건)
- **추정 라벨 오분류**: 무작위 30건을 확인한 결과 3건이 틀렸습니다 (`Thread.join`을 `str.join`으로, `requests.Session.get`을 `dict.get`으로, codecs 디코더를 `bytes.decode`로). 모두 외부 호출이라 내부 연결을 숨기지는 않으며, 모호한 이름은 `weak`로 표시합니다
- **수집되지 않는 중첩 정의**: 함수·클래스 안에 정의된 함수/클래스 (requests 17개). 이를 호출하는 `hash_utf8(...)` 등은 `local_callable`로 분류됩니다
- **동적 호출**: `getattr(obj, "method")()` 처럼 이름이 실행 시점에 정해지는 호출은 정적 분석으로 추적할 수 없습니다
- **상속 탐색 순서**: Python의 정확한 MRO(C3)가 아니라 깊이 우선 탐색이라, 복잡한 다이아몬드 상속에서는 다를 수 있습니다
- **조건부 import**: `try: import a / except: import b`처럼 같은 이름이 여러 번 import되면 마지막 것을 사용합니다

---

## 진행 예정

- [ ] `self.속성` 타입 추적 (`self.session = requests.Session()` 형태, 생성자에서 정한 객체 속성)
- [ ] 이름 기반 추정 연결 (메서드 이름이 프로젝트에서 유일한 경우, requests 기준 후보 23건. 정확도 표본 검증 후 도입 여부 결정)
- [ ] 함수 매개변수 타입 힌트(`annotation`) 활용
- [ ] 중첩 함수·클래스 수집
- [ ] Flask 등 데코레이터가 많은 프로젝트로 일반화 검증

---

## 테스트 대상 프로젝트

분석 대상은 이 저장소에 포함하지 않습니다 (`.gitignore` 등록). 직접 재현하려면 저장소 바로 아래에 clone 하세요.

```bash
git clone https://github.com/aboul3la/Sublist3r.git
git clone https://github.com/psf/requests.git
```

```bash
python analyze_project.py Sublist3r
python analyze_project.py requests/src/requests
```

---

## 파일 구조

```
knit_code/
├─ analyze_project.py     # 분석기 (v2)
├─ analyze_project_v1.py     # 분석기 (v1)
├─ classify_unresolved_v1.py     # 분류와 요약 (v1)
├─ sample_project/        # 테스트용 소규모 샘플
├─ README.md
└─ graph_*.json           # 실행 결과 (자동 생성, 커밋하지 않음)
```

---

## 변경 이력

**v2**
- 미해결 호출을 원인별로 분류 (내장 / 표준 라이브러리 / 외부 패키지 / 외부 부모 / 추정 / 미확정)
- 오버라이드 후보 간선(`calls_override`) 추가
- 재수출(re-export) 추적, 부모 클래스 해석 보강 (`as`, `Base[T]`, 점 표기)
- 지역 변수 추적 버그 수정 (함수 호출 결과를 클래스 인스턴스로 오인)
- 결과 파일명을 `graph_<프로젝트명>.json`으로 변경, 분류 기능 내장

**v1**
- 프로젝트 전체 분석, 고유 ID, import 해석, 호출 관계 추출
- 상속 체인 탐색 (`self`, `Parent.method(self)`, `super()`), 지역 변수 타입 추적
