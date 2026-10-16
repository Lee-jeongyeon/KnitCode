# KnitCode — 코드 구조 분석기 (v2.3)

Python 프로젝트를 읽어서 **함수·클래스 호출 관계 그래프(JSON)** 를 만드는 분석 엔진입니다.
앞으로 Git 이력·테스트 분석과 합쳐 "이 코드를 고치면 어디까지 영향이 가는가"를 보여주는 것이 목표입니다.

## 지금까지 된 것

| 기능 | 내용 |
|---|---|
| 호출 그래프 | 함수·메서드·클래스 사이 호출을 AST 로 추출. 상속(오버라이드), 생성자, 연산자, `with`, property 호출까지 연결 |
| 타입 추적 | 타입힌트·대입·반환 타입으로 `obj.method()` 가 어느 클래스의 메서드인지 해석 (확실할 때만 연결) |
| 구조 간선 | 모듈 `imports`, 클래스 `extends`, 소속 `contains`, 순환 import 탐지 |
| 외부 호출 분류 | 연결하지 못한 호출을 내장 / 표준 라이브러리 / 외부 패키지 / 미확정 등으로 **이유와 함께** 기록 |
| 진입점·읽는 순서 | `__main__`, 웹 라우트, CLI 등 진입점 탐지, "어디부터 읽을지" 추천 (`reading_order`) |
| 그래프 뷰어 | 결과를 HTML 한 장으로 보여줌 (파일 구조 / 함수 호출 화면) |
| 검증 도구 | 테스트를 실제로 실행해 호출을 기록하고 분석 결과와 비교(재현율), PyCG 와 비교 |

## 실행

```bash
python analyze_project.py <프로젝트 폴더>     # 분석 -> graph_<이름>.json
python knit_viewer.py <프로젝트 폴더>         # 뷰어 -> knit_<이름>.html
python -m pytest tests                        # 테스트 66개
```

- Python 3.9 이상, 설치할 패키지 없음 (분석 대상 프로젝트의 의존 패키지도 필요 없음)
- 저장소 루트를 그대로 넣어도 되고, `src/` 레이아웃도 지원

## 결과 JSON (다른 파트와 합칠 때 쓰는 인터페이스)

| 필드 | 내용 |
|---|---|
| `nodes` | 모듈·클래스·함수·메서드. id 는 `파일경로::클래스.메서드` 형식. 줄 범위, 시그니처, 복잡도, 중요도(`importance`) 포함 |
| `edges` | `calls`, `calls_override`(오버라이드 후보), `calls_implicit`(암묵 호출), `references`, `imports`, `extends`, `contains` |
| `entry_points` | 진입점 목록 |
| `reading_order` | 처음 읽을 파일·함수 순서 |
| `external_edges` / `unresolved` | 프로젝트 밖으로 나가는 호출 / 끝까지 못 푼 호출 (원인 분류 포함) |
| `schema_version` | 구조가 바뀌면 올리는 버전 (현재 1) |

## 정확도

**재현율** = 프로젝트 테스트를 실제로 돌려 기록한 호출 중, 분석기가 찾은 비율 (함수·메서드 사이)

| 프로젝트 | 재현율 | 비고 |
|---|---|---|
| Flask | **90.5%** | 개선 대상으로 삼은 프로젝트 |
| requests | **91.8%** | 개선 대상 |
| httpx | 87.0% | 맞춰 고치지 않은 프로젝트로 일반화 확인 |
| click | 82.6% | 일반화 확인, 목표(85%) 미달 |

- 새로 만든 연결 중 실제 실행에서 어긋난 것은 없었음 (Flask 24/24, requests 5/5, click 18/18, httpx 56/56)
- Sublist3r, FastAPI, Django 도 분석 확인 (Django 전체 36초)
- 호출 분류 완료율: Flask 87.2%, requests 86.1%, click 87.2%, FastAPI 89.8%, httpx 84.7%

## 한계와 다음 단계

- **정적 분석의 한계**: `getattr(...)()`, 콜백으로 받은 함수 호출은 따라갈 수 없어 `dynamic_calls` 로 표시만 함
- **남은 놓침**: `if` 로 갈리는 같은 이름 함수 정의, `A | B` 타입 변수, 타입 없는 매개변수, `iter()`/`sorted()` 가 부르는 특수 메서드
- Python 3.9 로는 3.10+ 문법을 쓴 파일(click 일부)이 분석에서 빠짐 (`skipped_files` 에 기록)
- **다음**: Git co-change(같이 커밋되는 파일) 분석과 뷰어 연결, 테스트 → 코드 연결(고치면 돌릴 테스트 계산)

## 파일

```
analyze_project.py   분석기          trace_recall.py   재현율·정밀도 측정
knit_viewer.py       뷰어 생성기      compare_pycg.py   PyCG 비교
viewer_template.html 뷰어 화면        tests/            테스트와 정답지(fixtures), 기준선(baseline.json)
docs/상세기록.md      설계·측정·변경 이력 전체 (필요할 때만 참고)
```

분석 대상 프로젝트는 저장소에 포함하지 않고 직접 `git clone` 해서 씁니다. 재현 방법은 `docs/상세기록.md` 의 "테스트 대상 프로젝트" 참고.
