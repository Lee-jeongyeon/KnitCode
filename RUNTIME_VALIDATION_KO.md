# 실행 기반 검증 (`trace_recall.py`)

정적 호출 그래프가 **실제 실행과 얼마나 맞는지**를 재는 도구입니다. 분석 대상 저장소의 테스트를 pytest로 실행하면서 함수 사이의 실제 호출을 기록하고, KnitCode 결과와 비교합니다. 팀원 누구나 같은 커밋·같은 명령으로 같은 숫자를 다시 얻는 것이 목적입니다.

> **주의:** `record`/`run`은 분석 대상의 테스트를 **실제로 실행**합니다. KnitCode 분석기(`main.py`)는 대상 코드를 실행하지 않지만, 이 검증 도구는 의도적으로 실행합니다. 신뢰할 수 있는 저장소에만 쓰고, 별도 가상환경에서 돌리세요.

## 1. 지표

함수·메서드 → 함수·메서드 호출만 셉니다 (모듈 최상위 호출자는 제외).

| 지표 | 정의 | 해석 |
|---|---|---|
| **recall (재현율)** | 실행 중 관찰된 호출 중 그래프에 있는 비율 | 그래프가 실제 호출을 얼마나 담는가 |
| **confirmed (확인 비율)** | 그래프 간선 중 실제로 관찰된 비율. 분모는 **실행된 위치**의 간선만 (호출한 함수가 실행됐고, 어떤 실행이 그 줄까지 도달함) | **정밀도의 하한**입니다. 테스트가 모든 분기를 타지 않으므로 관찰되지 않은 간선이 곧 오류는 아닙니다 |

간선 묶음은 따로, 그리고 합쳐서 보고합니다.

- `explicit`: `edges`의 명시적 호출 (`f()`, `obj.m()`)
- `implicit_only`: `implicit_edges` (property, 연산자, `with`, `for`, `len()` 등)
- `explicit+implicit`: 합집합

`missed_by_kind`는 그래프가 놓친 호출을 property / special_method / context_manager / other로 나눕니다. `missed_examples`에 예시 50개가 들어 있습니다.

## 2. 재현 절차

대상 저장소와 커밋은 `validation/runtime_targets.json`에 고정되어 있습니다 (10/17 3종 비교와 같은 커밋). Sublist3r는 테스트가 없어 제외합니다.

### Windows PowerShell

```powershell
# KnitCode 저장소 폴더에서
# 1) 대상 저장소를 고정 커밋으로 받고, 검증용 venv에 pytest와 의존성 설치
python trace_recall.py prepare --dest targets --venv .venv-validate

# 2) 분석 + 테스트 실행 기록 + 비교를 한 번에 (저장소마다)
python trace_recall.py run targets\schedule --output-dir out\schedule --tests test_schedule.py --python .venv-validate\Scripts\python.exe
python trace_recall.py run targets\requests --output-dir out\requests --tests tests --python .venv-validate\Scripts\python.exe
python trace_recall.py run targets\click    --output-dir out\click    --tests tests --python .venv-validate\Scripts\python.exe
python trace_recall.py run targets\rich     --output-dir out\rich     --tests tests --python .venv-validate\Scripts\python.exe
```

### macOS / Linux

```bash
python3 trace_recall.py prepare --dest targets --venv .venv-validate
python3 trace_recall.py run targets/rich --output-dir out/rich --tests tests --python .venv-validate/bin/python
```

`run`은 `out/<이름>/`에 분석 결과, `trace.json`(실행 기록), `runtime_validation.json`(비교 보고서)을 남깁니다. `targets/`, `out/`, `.venv-validate/`는 `.gitignore`에 들어 있습니다.

### 단계별로 실행하기

```bash
python main.py targets/rich --output-dir out/rich
python trace_recall.py record targets/rich --out out/rich/trace.json --python .venv-validate/bin/python
python trace_recall.py compare --output-dir out/rich --trace out/rich/trace.json --report out/rich/runtime_validation.json
```

- 이전 버전 결과와 비교하려면 **같은 커밋**을 이전 코드로 분석한 폴더를 `--baseline-dir`로 줍니다. 추가/손실 간선과 그중 실제로 관찰된 수가 나옵니다.
- pytest 옵션은 `--` 뒤에 넘깁니다. 예: `... record targets/rich --out t.json -- -p no:warnings -k console`

## 3. 기준 수치 (통합본 + Jeff_Park 통합, 2026-10-22)

환경: Linux, Python 3.13.16, pytest 9.1.1. `--baseline-dir`는 원본 JSE v2.5(`06422ca`) 결과입니다. 수치는 `explicit+implicit` 기준입니다. 재정의 관계(`OVERRIDDEN_BY`)는 호출이 아닌 구조 관계라 이 측정에 포함하지 않습니다.

| 저장소 | 테스트 | 관찰된 호출 | 원본 JSE recall | 현재 recall | 현재 confirmed | 추가 간선 확인 | 손실 간선 |
|---|---|---|---|---|---|---|---|
| schedule | 81 (실패 0) | 1,159 | 40.6% | **92.8%** | 98.1% | 605 / 626 | 0 |
| Requests | 635 (실패 209) | 823 | 69.1% | **83.2%** | 92.2% | 116 / 137 | 10 |
| Click | 2,287 (실패 0) | 5,367 | 46.2% | **48.7%** | 98.4% | 138 / 152 | 20 |
| Rich | 981 (실패 8) | 5,284 | 51.2% | **68.3%** | 97.2% | 914 / 930 | 31 |

간선 묶음별 (현재 버전):

| 저장소 | explicit recall | + implicit | implicit confirmed |
|---|---|---|---|
| schedule | 60.2% | 92.8% | 378 / 385 |
| Requests | 71.7% | 83.2% | 95 / 100 |
| Click | 46.7% | 48.7% | 109 / 115 |
| Rich | 54.7% | 68.3% | 717 / 733 |

손실 간선은 데코레이터가 붙은 함수(`@contextmanager`, `@lru_cache` 등)를 연결하지 않는 엄격한 규칙과, Rich에서 진입점별 분석 예산에 걸린 흐름 때문입니다(README **현재 성능** 참고).

## 4. 숫자가 다르게 나올 수 있는 이유

- **실패·건너뛴 테스트:** 실행되지 않은 경로는 관찰되지 않습니다. Requests는 많은 테스트가 네트워크나 로컬 httpbin을 요구해 이 환경에서 209개가 실패했습니다. schedule은 `pytz`가 없으면 시간대 테스트가 건너뛰어지므로 `prepare`가 설치합니다.
- **Python 버전:** 내장/표준 라이브러리 부모 클래스 판정은 분석을 실행하는 Python의 정보를 씁니다. 함수 이름은 소스 AST로 찾으므로 3.10과 3.12+에서 같게 처리되고, 3.12 미만에서 별도 프레임인 컴프리헨션·람다는 감싸는 함수로 계산합니다.
- **테스트의 바꿔치기(monkeypatch):** 테스트가 런타임에 클래스를 가짜로 바꾸면(예: Click의 `subprocess.Popen`) 정적 분석과 다른 호출이 관찰됩니다.
- **줄 번호 기준:** 호출 위치는 호출 식이 시작하는 줄입니다. 여러 줄에 걸친 호출은 드물게 줄이 어긋날 수 있습니다.
- 이 수치는 **테스트가 실행한 범위**에 대한 측정이며, 전체 Precision·Recall·F1이 아닙니다.
