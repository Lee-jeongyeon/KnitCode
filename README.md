# KnitCode — Static Call Graph Prototype (v2.9 + JSE 2026-10-22 + Jeff_Park 통합)

## JSE clean 정확성·성능 개선 (2026-10-22)

`with ... as` 반환 객체 추적, 근거가 확인된 클래스 데코레이터의 직접 메서드 탐색, 실제 수신 매개변수에 따른 상속 탐색, 키워드 전용 객체 전달 및 정적 해석 캐시를 개선했습니다. 최종 305개 테스트와 같은 소스의 5개 프로젝트 비교 결과, 제한 사항은 [ACCURACY_PERFORMANCE_REPORT_KO.md](ACCURACY_PERFORMANCE_REPORT_KO.md)를 참고하세요.

## JSE v2.5 추가 — 구조적 재정의 관계 (2026-10-22)

부모의 실제 메서드 정의에서 자식의 직접 정의로 `OVERRIDDEN_BY` 간선을 추가합니다. 기존 C3 MRO를 재사용하며 CALLS와 독립적입니다. 역조회는 `override_relations.overridden_methods(graph, child_method_id)`로 제공합니다. 의미, JSON, 검증 결과와 한계는 [OVERRIDE_RELATIONS_KO.md](OVERRIDE_RELATIONS_KO.md)를 참고하세요.

## Jeff_Park 변경 통합 (2026-10-22, [jbag6033-ai/knitcode](https://github.com/jbag6033-ai/knitcode))

세은님 v2.5(`06422ca`)에서 출발한 Jeff_Park님의 분석 개선과 VS Code 확장을 얹었습니다.

- **이름 범위:** 메서드 안에서는 바깥 클래스 본문의 이름이 보이지 않는 Python 규칙을 반영했습니다(`Resolver.lexical_scopes`).
- **바꿔치기 감지:** `Cls.m = ...`, `del Cls.m`, `setattr(Cls, 'm', ...)`가 있으면 원래 메서드로의 연결을 확정하지 않습니다. 조건문 안의 바꿔치기는 `certainty=conditional_namespace_may_call`로 표시합니다. 외부·내장 부모 클래스(`external_bases.py`)도 그 패키지에 무조건 쓰기가 있으면 모델을 믿지 않고, 조건부 쓰기면 연결하되 `conditionally_patched_bases`로 표시합니다(예: Sublist3r의 `if sys.platform.startswith('win'): multiprocessing.Process = threading.Thread`).
- **분석 결과는 분석하는 PC의 OS와 무관합니다.** `sys.platform` 조건은 알려진 모든 OS에서 참이거나 모두 거짓일 때만 확정하고, 나머지는 "실행될 수 있음"으로 봅니다. Windows와 Linux에서 5개 저장소와 PyCG 벤치마크 결과가 같음을 확인했습니다.
- **데코레이터 붙은 함수:** `classmethod`/`staticmethod` 외의 데코레이터(`@lru_cache`, `@contextmanager` 등)는 원래 함수를 바꿀 수 있어 연결하지 않습니다. 클래스 데코레이터는 기존 JSE 정책을 그대로 따릅니다.
- **객체 흐름 보강:** property getter의 반환값 추적(`self.worker.run()`), 기본 인자값, `and`/`or` 단락 평가, `try/finally` 반환, 알 수 없는 호출로 넘어간 객체·컨테이너 값 무효화, `match` 문, PEP 263 인코딩 선언.
- **콜백:** `sorted(key=f)`의 `f`를 암묵 호출(`implicit_kind=callback`)로 연결합니다. `sorted` 호출 자체는 계속 외부 호출입니다.
- **외부 호출 그래프:** 프로젝트 밖으로 나가는 호출을 `external_call_graph.json`(`CALLS_EXTERNAL`)으로 따로 저장합니다(`external_calls.py`).
- **VS Code 확장:** `vscode-extension/`과 `vscode_export.py`. 함수 중심 호출 흐름과 Git 공동 변경 이력을 보여 줍니다. 설치 파일은 `python scripts/package_extension.py`로 만듭니다(`dist/`는 Git에 올리지 않음).
- **통합 시 맞춘 것:** property 접근과 콜백은 `edges`가 아니라 `implicit_edges`에 둡니다. `multiprocessing.Process` 처리는 `external_bases.py`가 맡습니다(`stdlib_mro.py` 제외). PyCG 평가는 `pycg_benchmark.py` 하나로 합쳤습니다.
- **검증:** 테스트 162개(통합본 28 + Jeff_Park 132 + OS 무관성 2) 통과, 세은님 v2.5 원래 테스트 265개도 통합 코드에서 모두 통과. 의미 검사(`scripts/evaluate_engine.py`)는 22개 중 17개 일치(Jeff_Park 원본 16개).

Python 3.10+ / Windows PowerShell / **표준 라이브러리만 사용**. 기존 정적 구문 분석기에 보완하고 있는 중입니다.

## 현재 성능 (통합본, 2026-10-22 측정)

세 버전을 **같은 커밋·같은 머신**에서 다시 측정했습니다. 원본 JSE = GreatJung1 v2.5(`06422ca`), JSE 최신 = `jse/`(10/22), 통합본 = 이 저장소.
대상 커밋은 `validation/runtime_targets.json`(schedule `82a43db1b938`, Requests `611c6162cbc4`, Click `2247b35ea1c4`, Rich `9d8f9a372cc5`)과 Sublist3r `729d649`입니다. 환경: Linux, Python 3.13.16, pytest 9.1.1, 4코어.

**정적 분석** (해결 = `RESOLVED_INTERNAL` 호출 지점, 분류 완료율 = 내부 연결 + 근거 있는 외부 분류 / 전체 호출)

| 저장소 | 호출 지점 | 해결: 원본 → JSE 최신 → 통합 | 분류 완료율: 원본 / JSE 최신 / **통합** (+추정) | OVERRIDDEN_BY | 암묵 간선 | 분석 시간: 원본 / JSE 최신 / 통합 |
|---|---|---|---|---|---|---|
| schedule | 1,065 | 504 → 504 → **747** | 61.6% / 61.6% / **91.9%** (94.2%) | 0 | 385 | 2.3 / 2.2 / 2.2초 |
| Sublist3r | 514 | 92 → 92 → **124** | 59.1% / 59.1% / **71.0%** (91.4%) | 54 | 2 | 1.2 / 1.1 / 0.9초 |
| Requests | 2,687 | 1,054 → 1,055 → 1,052 | 75.2% / 75.3% / **76.0%** (84.1%) | 13 | 133 | 13.4 / 10.4 / 7.1초 |
| Click | 7,084 | 3,096 → 3,096 → **3,100** | 75.8% / 75.8% / **76.1%** (83.0%) | 131 | 124 | 23.2 / 19.6 / 10.9초 |
| Rich | 9,843 | 5,463 → 5,605 → **5,629** | 80.7% / 82.1% / **82.6%** (84.7%) | 44 | 811 | 126.6 / 53.3 / 72.3초 |

**실행 검증** (`trace_recall.py`: 각 저장소 테스트를 실제로 실행해 기록한 함수 → 함수 호출 기준. 지표 정의는 [`RUNTIME_VALIDATION_KO.md`](RUNTIME_VALIDATION_KO.md))

| 저장소 | 테스트 (실패) | 관찰된 호출 | recall: 원본 / JSE 최신 / 통합 명시적 / **통합 + 암묵** | 통합 confirmed (정밀도 하한) | 암묵 간선 확인 | 원본 대비 추가 간선 확인 / 손실 |
|---|---|---|---|---|---|---|
| schedule | 81 (0) | 1,159 | 40.6% / 40.6% / 60.2% / **92.8%** | 1,076 / 1,097 (98.1%) | 378 / 385 | 605 / 626 · 손실 0 |
| Requests | 635 (209) | 823 | 69.1% / 69.3% / 71.7% / **83.2%** | 685 / 743 (92.2%) | 95 / 100 | 116 / 137 · 손실 10 |
| Click | 2,287 (0) | 5,367 | 46.2% / 46.2% / 46.7% / **48.7%** | 2,615 / 2,659 (98.4%) | 109 / 115 | 138 / 152 · 손실 20 |
| Rich | 981 (8) | 5,284 | 51.2% / 53.4% / 54.7% / **68.3%** | 3,607 / 3,711 (97.2%) | 717 / 733 | 914 / 930 · 손실 31 |

- schedule이 크게 늘어난 것(해결 504 → 747)은 Jeff_Park님의 이름 범위·기본 인자 처리 덕분입니다.
- **잃은 연결**은 Jeff_Park님의 엄격한 규칙 때문입니다. 대부분 `@contextmanager`, `@lru_cache`, `@cache`처럼 데코레이터가 붙은 함수입니다(원래 함수가 실행된다는 근거가 없어 연결하지 않음). Rich는 객체 흐름이 더 정밀해진 만큼 분석량이 늘어(expression step 207만 → 288만), 진입점별 예산(6,000 step)에 걸려 놓치는 흐름도 있습니다. 예산을 30,000으로 올리면 잃는 연결이 절반으로 줄지만 분석 시간이 72초 → 105초로 늘어 기본값은 JSE 그대로 둡니다.
- **분석 시간**은 이전 통합본보다 늘었습니다(Rich 38.4초 → 72.3초). 같은 이유로 분석량이 늘었기 때문이며, Jeff_Park님 원본(Requests 30.9초, Click 48.7초)보다는 빠릅니다.
- **클래스 데코레이터는 JSE의 엄격한 정책**(`class_decorators.py`)을 따릅니다. 부모 클래스가 없고 데코레이터가 소스로 안전하다고 증명된 클래스의 직접 정의 메서드만 연결하며, `__init__` 같은 특수 메서드는 제외합니다. 명시적 호출과 암묵 호출 모두 같은 규칙입니다. 이 때문에 Rich의 `@rich_repr` 클래스(Style, Segment, Layout 등) 안 호출은 이전 LJY 통합본(해결 5,707, recall 73.0%)보다 적게 연결됩니다. 생성 호출 자체는 `INSTANTIATES` 간선(클래스 노드)으로 계속 보입니다.
- Requests는 테스트 209개가 네트워크·로컬 httpbin을 요구해 실패하므로 실행되지 않은 경로는 확인되지 않습니다.
- OVERRIDDEN_BY가 JSE 최신보다 많은 저장소(Sublist3r 0 → 54, Requests 11 → 13, Click 104 → 131)는 `external_bases.py`가 내장·표준 라이브러리 부모(`multiprocessing.Process`, `threading.Thread` 등)를 포함한 MRO를 확정할 수 있게 된 결과입니다.

## PyCG 공식 벤치마크 비교 (`pycg_benchmark.py`, 2026-10-22)

PyCG 논문(ICSE 2021)의 **macro-benchmark**로 KnitCode를 평가합니다. 실제 프로젝트 5개(Sublist3r, asciinema, autojump, fabric, face_classification)에 사람이 만든 **정답 호출 그래프**가 있어 정밀도와 재현율을 잴 수 있습니다. 데이터와 채점 함수는 PyCG 공식 평가 저장소([vitsalis/pycg-evaluation](https://github.com/vitsalis/pycg-evaluation))의 것을 그대로 씁니다.

| 프로젝트 | KnitCode 정밀도 / 재현율 (F1) | PyCG 정밀도 / 재현율 (F1)¹ |
|---|---|---|
| Sublist3r | 95.5% / **67.5%** (**79.1%**) | 98.8% / 61.6% (75.9%) |
| asciinema | 96.9% / **73.0%** (**83.3%**) | 100.0% / 68.0% (81.0%) |
| autojump | 96.7% / **71.1%** (**82.0%**) | 99.5% / 68.2% (80.9%) |
| fabric | 95.1% / 56.4% (70.8%) | 98.3% / **61.9%** (**76.0%**) |
| face_classification | 98.8% / **91.3%** (**94.9%**) | 99.5% / 89.7% (94.3%) |

¹ 논문 Table 4 값입니다. Linux에서 같은 조건으로 PyCG를 다시 실행해 5개 모두 재현했습니다.

- KnitCode는 5개 중 4개에서 재현율이 더 높고, PyCG는 정밀도가 1~5%p 더 높습니다. fabric은 KnitCode의 재현율이 낮아 다음 개선 대상입니다.
- 두 도구가 함께 찾은 호출은 전체의 74~96%입니다. KnitCode만 찾은 호출의 다수는 정답에 있습니다(Sublist3r 56개 중 46개, autojump 62개 중 50개).

### 측정 기준
- **입력:** 논문 스크립트(`generate_pycg_cgs.sh`)와 같은 파일 범위와 패키지 루트를 써서, 모듈 이름이 정답과 같은 기준으로 만들어집니다.
- **채점:** 논문 저장소의 `measure_precision` / `measure_recall`을 그대로 불러 씁니다. 함수 단위 `호출하는 함수 → 호출되는 함수` 쌍을 셉니다.
- **full(표의 값):** 프로젝트 안 호출(CALLS) + 근거가 있는 외부 호출(`KNOWN_NON_INTERNAL`: 내장 함수, 표준 라이브러리, import). 정답에 외부 호출이 많아서, 프로젝트 안 호출만 세는 **internal** 값은 재현율이 16~32%로 낮습니다. 두 값 모두 출력됩니다.
- **표기 맞춤(정답을 보고 고치지 않음):**
  - `__init__`이 없는 클래스를 만드는 호출은 제외합니다. PyCG 그래프는 함수만 노드로 쓰고, 정답에도 클래스가 호출 대상으로 거의 나오지 않습니다(5개 프로젝트에서 1건). 제외한 수는 `skipped`에 기록합니다.
  - `Manager().dict`처럼 하나의 함수를 가리키지 않는 외부 호출 설명은 제외합니다.
  - 내장 타입 메서드는 PyCG 표기로 바꿉니다(`builtins.len` → `<builtin>.len`, `str.join` → `<**PyStr**>.join`).

### 실행 방법

```powershell
# 1) 벤치마크 받기 (분석기 폴더 밖, 5개 프로젝트만)
cd ..
git clone https://github.com/vitsalis/pycg-evaluation.git
cd pycg-evaluation
git submodule update --init data/macro-benchmark/projects/Sublist3r
git submodule update --init data/macro-benchmark/projects/asciinema
git submodule update --init data/macro-benchmark/projects/autojump
git submodule update --init data/macro-benchmark/projects/fabric
git submodule update --init data/macro-benchmark/projects/face_classification

# 2) KnitCode 평가 (Python 3.10+ 환경, 분석기 폴더에서)
python pycg_benchmark.py --benchmark ..\pycg-evaluation --output out\pycg
```

결과는 `out/pycg/`에 생깁니다(`.gitignore`에 포함).
- `summary.json`: 프로젝트별 정밀도·재현율·F1, 맞은 수(TP)·틀린 수(FP)·놓친 수(FN)
- `<프로젝트>/knitcode_cg.json`: KnitCode 호출 그래프(PyCG 형식)
- `<프로젝트>/result.json`: 프로젝트별 상세 결과

### PyCG도 직접 실행해 겹침 비교하기 (선택)

`--pycg-python`에 PyCG가 설치된 Python을 주면 PyCG도 같은 입력으로 실행하고, 두 도구가 함께 찾은 호출 / 한쪽만 찾은 호출과 그중 정답인 수를 함께 보고합니다.

```powershell
# 논문 버전 PyCG는 Python 3.9 이하에서만 동작합니다. PyPI 버전은 쓰지 마세요.
conda create -n pycg python=3.9 -y
conda activate pycg
pip install "git+https://github.com/vitsalis/PyCG.git@f5ef6921f8609ae2239942f5d65ef777318fbdfa" "setuptools<70"
conda deactivate

python pycg_benchmark.py --benchmark ..\pycg-evaluation --output out\pycg --pycg-python <pycg 환경의 python.exe 경로>
```

> **Windows 주의:** PyCG는 분석하는 PC의 표준 라이브러리로 import를 해석합니다. 그래서 Windows에서는 `os.path`가 `ntpath`로 해석되는 등 OS별 차이가 생겨, 일부 프로젝트(asciinema, face_classification 등)에서 논문보다 2~3%p 낮게 나옵니다. **PyCG 수치는 논문 값(Linux)을 쓰고**, 겹침 비교가 꼭 필요하면 Linux(또는 WSL)에서 실행하세요. KnitCode 수치는 Windows와 Linux에서 같습니다.



## v2.6 — 그래프 구성 속도 개선 (2026-10-17)

**속도** — 결과는 한 글자도 바꾸지 않고 반복 계산만 제거했습니다.

- 주요 원인: `ast.get_source_segment`가 호출마다 파일 전체를 다시 줄 단위로 나눔, `imports_at`·`project_symbols`가 호출마다 전체를 다시 훑음.
- 현재 분석 시간은 위 **현재 성능** 표를 참고하세요.

## KnitCode Flow 뷰어 (`flow_viewer.py`, 2026-10-22)

전체 그래프를 한꺼번에 그리지 않고, **누른 함수를 중심으로 국소 그래프**를 보여 주는 오프라인 HTML입니다. `python main.py <저장소>`를 실행하면 `output/flow.html`이 함께 생기고, 이미 분석한 폴더는 `python flow_viewer.py output`으로 다시 만들 수 있습니다. 브라우저에서 파일을 열면 되며 인터넷 연결이 필요 없습니다.

- **왼쪽:** 파일·함수 검색과 트리. 누르면 그 함수가 중심이 됩니다.
- **가운데:** 호출자는 왼쪽, 호출 대상은 오른쪽. `− 단계 / + 단계`로 1~3단계. 열마다 8개까지 보여 주고 나머지는 "더 보기"로 접습니다. 확실한 호출(명시적·생성자)이 먼저, 후보(암묵 호출)와 모듈 최상위 호출은 뒤에 옵니다.
- **선 클릭:** 오른쪽 패널에 그 관계의 호출 위치(파일:줄), 소스, 확실성, 판정 규칙이 나옵니다. 명시적 호출(실선), 생성자, 암묵 호출(점선)은 범례에서 켜고 끌 수 있습니다.
- **아래:** 선택한 함수 안의 모든 호출과 상태(내부 연결 / 외부 / 미해결, 추정 출처).
- **테스트 코드 숨기기**가 기본으로 켜져 있어 테스트 호출자가 많은 함수도 깔끔하게 보입니다. 주소창의 `#함수ID`로 같은 화면을 공유할 수 있습니다.
- Git 이력·함께 변경된 파일 패널은 자리만 잡아 두었습니다. Git 공동 변경 이력은 VS Code 확장(`vscode-extension/`)에서 볼 수 있습니다.

## 실행 기반 검증 도구 (`trace_recall.py`, 2026-10-22)

분석 대상의 테스트를 실제로 실행해 관찰한 함수 간 호출과 정적 그래프를 비교합니다. 팀원 누구나 같은 커밋·같은 명령으로 같은 숫자를 재현할 수 있습니다. **절차·지표 정의·기준 수치: [`RUNTIME_VALIDATION_KO.md`](RUNTIME_VALIDATION_KO.md)**

```powershell
python trace_recall.py prepare --dest targets --venv .venv-validate
python trace_recall.py run targets\rich --output-dir out\rich --tests tests --python .venv-validate\Scripts\python.exe
```

측정 결과는 위 **현재 성능 → 실행 검증** 표에 있습니다.

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
| `callback` | `sorted(v, key=f)` → `f` (입력이 비어 있지 않을 때) |

- 결과는 `graph_result.json`의 **`implicit_edges`에 별도로** 저장하며 `edges`, 해결/미해결 수, 호출 상태는 바뀌지 않습니다. `implicit_summary`와 `implicit_calls.csv`도 생성합니다.
- 내장/표준 라이브러리 부모가 그 메서드를 먼저 제공하는 경우(`class Table(dict)`의 `t[k]`), 타입을 모르는 객체, 같은 이름의 프로젝트 함수(`def len`)는 연결하지 않습니다.
- 객체 흐름의 분석 예산을 건드리지 않도록 구현해, 5개 저장소의 기존 그래프가 완전히 같음을 확인했습니다.
- 데코레이터가 붙은 클래스는 명시적 호출과 같은 JSE 엄격 정책으로만 연결합니다(위 **현재 성능** 참고).
- property는 진짜 내장 `property`/`functools.cached_property`일 때만 연결하고(같은 이름의 프로젝트 함수 제외), 바꿔치기된 메서드(`Cls.m = ...`)는 연결하지 않습니다.
- 연산자의 반대쪽 메서드(`b.__radd__`, `a == b`의 `b.__eq__`)는 **왼쪽 피연산자가 그 연산 메서드를 갖지 않는 것이 증명될 때만** 기록합니다. 왼쪽이 정방향 메서드를 정의하지 않은 프로젝트 객체이거나 숫자 리터럴(`2 + n`)인 경우입니다. 왼쪽 타입을 모르거나 문자열(`'%s' % n`)이면 기록하지 않습니다.

**실행 검증** (통합본, 2026-10-22 재측정)

| 저장소 | 재현율: 명시적 호출만 → 암묵 호출 포함 | 암묵 간선 중 실제 호출로 확인 |
|---|---|---|
| schedule | 60.2% → **92.8%** | 378 / 385 |
| Rich | 54.7% → **68.3%** | 717 / 733 (97.8%) |
| Requests | 71.7% → **83.2%** | 95 / 100 |
| Click | 46.7% → **48.7%** | 109 / 115 |

미확인 사례는 Requests의 `__ne__` 안 `self == other` → `__eq__`처럼 테스트가 실행하지 않은 경로입니다.

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

**호출 분류 완료율** (내부 연결 + 근거 있는 외부 분류 / 전체 호출, 통합본 2026-10-22 재측정)

| 저장소 | 원본 JSE | 통합본 | 통합본 + 추정 포함 |
|---|---|---|---|
| schedule | 61.6% | **91.9%** | 94.2% |
| Sublist3r | 59.1% | **71.0%** | 91.4% |
| Requests | 75.2% | **76.0%** | 84.1% |
| Click | 75.8% | **76.1%** | 83.0% |
| Rich | 80.7% | **82.6%** | 84.7% |

- 이 분류 규칙 자체는 내부 호출 판정을 바꾸지 않습니다(도입 당시 5개 저장소 모두 RESOLVED_INTERNAL 집합 동일).
- 실행 검증: Requests/Click/Rich 테스트 실행 기록과 대조한 결과, 새로 KNOWN이 된 60개 지점 중 실제로 프로젝트 함수가 호출된 곳은 1곳입니다. Click 테스트가 `subprocess.Popen`을 가짜 클래스로 바꿔치기(monkeypatch)한 경우로, 정적 분석 범위 밖임을 evidence에 명시했습니다.

## v2.7 — 상속 메서드 연결 보완 + Rich 속도 (2026-10-17, 3종 비교 결과 반영)

10/17 프로토타입 비교에서 JSE 개선 대상으로 지적된 **Sublist3r 상속 후보 32개**와 **Rich `self` 메서드 누락**을 고쳤습니다. 원인은 두 가지였습니다.

1. **외부·내장 부모 클래스** (`Exception`, `multiprocessing.Process` 등)가 있으면 MRO 전체를 포기했습니다. → `external_bases.py`가 내장 타입은 `dir()`, 표준 라이브러리는 **소스를 AST로만 읽어** 그 클래스가 가진 이름(인스턴스 속성 포함)을 계산합니다. 외부 클래스는 MRO의 불투명 노드가 되고, **그 이름을 정의하지 않을 때만** 뒤쪽 프로젝트 부모의 메서드로 연결합니다. 근거는 간선의 `external_bases_checked`에 남습니다. 서드파티 패키지 부모는 여전히 미확정입니다.
2. **클래스 데코레이터·메타클래스**가 있으면 같은 클래스 안의 메서드조차 연결하지 않았습니다. → `dataclass`, `total_ordering`, `final`, `metaclass=ABCMeta` 등 클래스를 그대로 돌려주는 것이 확실한 표준 항목은 정상 MRO로 처리합니다. 그 밖의 데코레이터는 JSE 정책을 따라 **소스로 안전성이 확인된 경우에만**(예: `@rich_repr`) 직접 정의된 메서드를 연결하고, 알 수 없는 데코레이터는 연결하지 않습니다(`class_decorators.py`, [ACCURACY_PERFORMANCE_REPORT_KO.md](ACCURACY_PERFORMANCE_REPORT_KO.md)).

| 저장소 (통합본, 2026-10-22 재측정) | 해결된 호출 지점: 원본 JSE → 통합본 | 잃은 연결 |
|---|---|---|
| schedule | 504 → **747** | 0 |
| Sublist3r | 92 → **124** | 0 |
| Requests | 1,054 → 1,052 | 10 |
| Click | 3,096 → 3,100 | 17 |
| Rich | 5,463 → **5,629** | 64 |

- 보고서의 Rich 누락 지점 `style.py:712`, `pretty.py:457`, `layout.py:217/305/313` 모두 연결됩니다(JSE의 증명된 데코레이터 규칙).
- Sublist3r의 상속 후보 32개(92 → 124)는 외부·내장 부모 처리(1번) 덕분이며, JSE 최신만으로는 92개 그대로입니다.
- 잃은 연결은 Jeff_Park님 통합의 데코레이터 함수 규칙과 분석 예산 때문입니다(위 **현재 성능** 참고).
- **속도**: 객체 흐름 상태 병합 최적화로 Rich 분석 시간이 원본 126.6초 → 38.4초가 됐고, Jeff_Park님 통합 후에는 분석량이 늘어 72.3초입니다.
