# KnitCode Static Flow 0.3.1

Python 함수 중심으로 호출자를 왼쪽, 호출 대상을 오른쪽에서 탐색합니다.
`KnitCode Static Flow: Open Call Flow` 명령으로 실행합니다.
기존 `knitcode-local.knitcode-static` 확장 ID와 설정을 유지하므로 이전 버전에서 업데이트할 수 있습니다.

- 현재 편집 중인 파일의 함수에서 시작하며 검색, 1~3단계 확장, 이전 선택을 지원합니다.
- 코드 및 호출 위치로 이동하고, 각 연결의 분석 규칙과 확신 수준을 확인할 수 있습니다.
- 조건부 콜백 후보를 별도로 표시합니다. 내부 호출, 외부·내장 호출, 미확정 호출을 구분합니다.
- Git 공동 변경과 커밋 이력은 별도 패널에서 제공합니다. 공동 변경은 호출 관계를 뜻하지 않습니다.
- 최신 정적 분석 엔진을 포함합니다. 대상 Python 프로젝트를 import하거나 실행하지 않습니다.

VS Code 1.85 이상과 Python 3.10 이상이 필요합니다. VS Code의 Extensions 메뉴에서
`Install from VSIX...`로 설치한 뒤 프로젝트 폴더를 열고 명령 팔레트에서 실행하세요.
Python 경로는 `knitcodeStatic.pythonPath`, Git 범위는 `knitcodeStatic.historyDays` 및
`knitcodeStatic.commitLimit`로 설정합니다. 재분석은 저장된 소스를 기준으로 수행합니다.

정적 후보는 실제 실행이나 전체 호출 관계의 포착을 보장하지 않습니다. 미확정 호출 수는 미탐 수가 아닙니다.
재귀·계산 한도, 동적 디스패치, 런타임 생성 코드 등에는 한계가 있습니다.
Git이 없는 프로젝트에서도 코드 탐색을 사용할 수 있습니다.

패키징: `python3 scripts/package_extension.py` (저장소 루트).

## 0.3.1 변경

- property, 중첩 클래스, classmethod 및 기본 인자 전달 분석 개선을 포함합니다.
- 내장 클래스 상속 메서드와 소비되는 map 콜백 추적을 포함합니다.
- 외부 참조 전달 및 반환 계약을 보완해 잘못된 외부 호출 경로 생성을 줄였습니다.
