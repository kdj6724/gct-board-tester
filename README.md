# Board Test Automation (블록 기반 리라이트)

기존 `auto_script.py` (UART + Tapo 스마트플러그 기반 보드 테스트 자동화)를
**블록을 드래그해서 조립하는 GUI**로 업그레이드한 버전입니다. UART 모니터링,
로그 저장, Tapo 전원 제어 같은 기존 동작 방식은 그대로 유지하면서, 테스트
순서/설정을 코드 수정 없이 GUI에서 바꿀 수 있게 모듈화했습니다.

## 실행 방법

```bash
pip install -r requirements.txt
cp .env.example .env   # TAPO_EMAIL / TAPO_PASSWORD / TAPO_IP 입력
python main_app.py
```

Windows 는 파이썬 설치 시 tkinter 가 기본 포함되어 있어 추가 설치가
필요 없습니다. (이 저장소를 테스트한 리눅스 환경에서는 `sudo apt-get
install python3-tk` 가 필요했습니다.)

## 파일 구성

| 파일 | 역할 |
|---|---|
| `main_app.py` | 메인 윈도우 (연결 설정, 프로파일 선택, Run/Stop, 로그) |
| `block_editor.py` | 드래그 앤 드롭 블록 시퀀스 에디터 |
| `block_dialogs.py` | 블록별 파라미터 입력 다이얼로그 |
| `step_types.py` | 블록 타입/데이터 모델 (직렬화, 깊이/중첩 계산) |
| `step_engine.py` | 블록 시퀀스 실행 엔진 (하드웨어와 분리되어 있어 테스트 가능) |
| `serial_session.py` | UART 읽기/쓰기, 문자열 대기 유틸리티 |
| `tapo_control.py` | Tapo P115 전원 제어 |
| `config_manager.py` | 설정(`settings.json`) + 프로파일(`profiles/*.json`) 저장/로드, 구버전 `config.json` 자동 이전 |

## 블록 종류

- **Power** — Tapo 플러그를 ON/OFF, 이후 대기 시간(초) 설정
- **String Check** — 지정한 문자열(또는 `|`로 구분한 여러 패턴을 정규식으로)이
  올 때까지 UART 를 읽고 대기. 타임아웃 시 "중단" 또는 "다음 블록 진행" 선택 가능
- **String Input** — UART 로 문자열/키 입력 전송 (Enter 자동 추가 옵션)
- **Save** — 직전 String Check 이후 수신한 내용을 라벨과 함께 결과 로그에 저장.
  PASS/FAIL 판정 문자열을 지정하면 자동으로 PASS/FAIL 태그가 붙음
- **Loop** — 다른 블록들을 감싸서 반복. "N회 반복" 또는 기존 `{para1}` 스윕과
  동일한 "변수 스윕({var})" 모드 지원. 반복 횟수 0 = Stop 버튼 누를 때까지 무한 반복
- **Delay** / **Upload Script** — 보조 블록 (단순 대기 / 기존 스크립트 업로드 기능 유지)

## 사용 방법

1. `블록 편집` 버튼을 눌러 에디터를 연다.
2. 왼쪽 팔레트의 블록을 캔버스로 **드래그**하면 원하는 위치에 삽입되고,
   클릭만 하면 맨 끝에 추가된다. 블록을 추가하면 바로 파라미터 입력창이 뜬다.
3. 이미 놓인 블록도 드래그로 순서를 바꿀 수 있다. **Loop 블록을 드래그하면
   그 안의 블록들이 함께 이동**한다.
4. 여러 블록을 Shift+클릭으로 선택하고 **"선택 구간 Loop로 묶기"** 를 누르면
   그 구간이 반복 블록으로 감싸진다 (예: `power on → String Check → String
   Input → Save → power off` 를 선택해서 통째로 N회/무한 반복시키기).
5. 각 블록의 ✎ 아이콘으로 설정을 다시 열 수 있고, ✕ 로 삭제할 수 있다
   (Loop 를 삭제하면 안의 블록도 함께 삭제됨).
6. "저장" 을 누르면 현재 프로파일에 저장된다. 메인 화면에서 **프로파일**
   드롭다운으로 보드/프로젝트별 시퀀스를 여러 개 만들어 전환할 수 있다.

## 기존 `config.json` 사용자

기존 방식(`commands` + `para1` + `script`)으로 만든 `config.json` 이 있는
폴더에서 처음 실행하면 자동으로 `legacy` 프로파일로 변환되어 그대로
동작한다 (Send + String Check 조합으로 재구성됨).

## 로그

Run 을 누르면 `logs/` 폴더에 타임스탬프 파일명으로 로그가 저장된다
(화면에 보이는 UART 원본 라인 + Save 블록 결과 스냅샷). 기존과 동일하게
"Open Log" 버튼으로 탐색기에서 열 수 있다 (Windows 전용 `os.startfile`).

## 검증

하드웨어 없이도 로직을 검증할 수 있도록 실행 엔진(`step_engine.py`)과
에디터의 리스트 조작 로직(`block_editor.py` 의 `move_range` /
`wrap_range` / `unwrap_loop` / `delete_block`)은 tkinter, serial, Tapo 에
의존하지 않는 순수 함수로 분리되어 있습니다. 가짜(mock) serial/Tapo
객체로 중첩 Loop, 변수 스윕, Stop 중단 등을 단위 테스트했습니다.
