# KnitCode — 코드 구조 분석 프로토타입

Python AST를 기반으로 프로젝트 안의 함수·클래스 호출 관계를 추출해서, 파일 경로가 포함된 고유 ID를 가진 그래프(JSON)로 만드는 분석기입니다. 이후 Git 이력 분석, 테스트 연관 분석과 결합해 "코드 수정 시 영향 범위"를 보여주는 KnitCode의 핵심 분석 엔진 파트입니다.

## 실행 방법

```bash
python analyze_project.py <분석할 프로젝트 폴더 경로>
```

결과는 터미널 출력과 함께 `graph.json`으로 저장됩니다.

미해결 호출을 유형별로 분류해서 보고 싶으면:

```bash
python classify_unresolved.py
```

## 분석 원리

1. **파일 파싱**: 대상 폴더를 순회하며 `.py` 파일을 찾아 AST로 파싱 (`.git`, `venv` 등 제외)
2. **정의 수집**: 함수·클래스·메서드마다 `파일경로::클래스명.함수명` 형태의 고유 ID 부여
3. **import 해석**: `import x`, `from x import y`가 실제로 어느 파일을 가리키는지 매핑
4. **호출 연결**: 호출문을 찾아 어느 함수를 가리키는지 추적. 해석 실패 시 "미해결"로 따로 기록

## 진행 상황

### 완료
- 단일 파일 → 프로젝트 전체(여러 파일) 분석으로 확장
- 파일 간 `import` 해석 (절대/상대 import 모두 지원)
- 클래스 상속 체인 추적
  - `self.method()`가 부모 클래스에 정의된 경우
  - `ParentClass.__init__(self, ...)` 직접 호출 (구식 패턴)
  - `super().method()` 패턴
  - 다단계 상속(A → B → C) 및 다중 상속 지원
- 지역 변수 타입 추적 (`x = ClassName()` 형태 대입 후 `x.method()` 호출 해석)
- 미해결 호출 자동 분류 (내장/표준 라이브러리, self 상속 의심, 속성 체이닝, 변수.메서드(), 기타)

### 검증 결과

실제 오픈소스 프로젝트 2개에 적용해 해석률과 한계를 확인했습니다.

| 프로젝트 | 파일 수 | 노드 수 | 해석된 호출 | 미해결 호출 | 해석률 |
|---|---|---|---|---|---|
| [Sublist3r](https://github.com/aboul3la/Sublist3r) | 4 | 115 | 109 | 360 | 약 23.2% |
| [requests](https://github.com/psf/requests) | 19 | 283 | 260 | 669 | 약 28.0% |

**핵심 발견**: 두 프로젝트 모두에서 상속 관련 미해결(`self.메서드()`가 부모 클래스에 정의된 경우)이 **거의 0건**으로 나와, 상속 추적 로직이 특정 프로젝트에 한정되지 않고 일반화됨을 확인했습니다.

**남은 미해결 호출의 원인**은 분류 결과 대부분 아래 범주였습니다.
- Python 내장 타입(`str`, `list` 등)의 메서드 — 변수 타입 추론이 필요한 영역
- 외부 라이브러리 호출 (`requests.Session`, `urllib3.util.Timeout` 등)
- 함수 안에 정의된 중첩 함수가 변수에 담겨 나중에 호출되는 경우 (발생 빈도 낮음, 2개 프로젝트 합쳐 소수 건)

### 진행 중 / 예정
- `self.속성` 타입 추적 (`self.session = requests.Session()` 형태, 생성자에서 설정된 객체 속성)
- 함수 파라미터 타입 힌트(`annotation`) 활용한 타입 추론
- 미해결 호출을 "external 노드"로 그래프에 명시적으로 포함 (그래프 시각화 시 끊김 방지)

## 테스트 대상 프로젝트

아래 프로젝트들로 분석기를 검증했습니다. 직접 테스트하려면 이 저장소 바로 아래에 clone 받아주세요 (`.gitignore`에 등록되어 있어 커밋되지 않습니다).

```bash
git clone https://github.com/aboul3la/Sublist3r.git
git clone https://github.com/psf/requests.git
```

```bash
python analyze_project.py Sublist3r
python analyze_project.py requests/src/requests
```

## 알려진 한계

- Python 내장 타입(str/list 등)에 대한 타입 추론은 범위 밖으로 둠 (AST 패턴 매칭 수준을 넘어서는 작업)
- 동적 호출(`getattr(obj, "method")()`)은 정적 분석으로 원천적으로 추적 불가
- 조건부 함수 정의(`if PY2: def f(): ... else: def f(): ...`)는 아직 미대응
- 데코레이터로 감싸인 함수의 호출 흐름은 아직 검증 안 됨

## 파일 구조

```
knit_code/
├─ analyze_project.py       # 메인 분석기
├─ classify_unresolved.py   # 미해결 호출 분류 스크립트
├─ sample_project/          # 테스트용 소규모 샘플 (3개 파일)
└─ graph.json               # 분석 결과 (실행할 때마다 재생성)
```
