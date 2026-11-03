# gct-board-tester 기능 목록

기능을 만들거나 바꿀 때마다 이 표를 같이 고쳐서 커밋한다. 자세한 규칙은 [TEST_SPEC.md](TEST_SPEC.md).
명령은 모두 gct-board-tester 폴더에서 실행. 기본은 `bash gbt.sh <명령>` (`bash gbt.sh help` = 이 표)

| # | 기능 | 명령 | 하는 일 | 입력 → 출력 | 상태 |
|---|---|---|---|---|---|
| 0 | 보드 현황 | `bash gbt.sh status` | 보드마다 Tapo / UART / fastboot / 종류 / 사용 중인지(누가, 언제부터) | `rig.json` + `logs/locks/` | 사용 가능 (2026-11-03) |
| 1 | 장비 자동 구분 (페어 찾기) | `bash gbt.sh discover` (진단: `bash gbt.sh probe <Tapo IP>`) | Tapo / UART / fastboot USB 짝을 자동으로 찾고 페어마다 보드 이름을 붙임. 사용 중인 보드는 건너뜀 | `config/rig_tapo.txt` → `rig.json` (+ `logs/rig/<시각>.json`) | 시험 중 (2026-11-03) |
| 2 | 이미지 빌드 | `bash gbt.sh build <이미지...>` | sbl / uboot(+tfa) 빌드. linux / openwrt 는 등록 예정 | `config/images.json` → 이미지 파일 + `logs/flash/*_build_*.log` | sbl·uboot 시험 중 (2026-11-03) |
| 3 | fastboot 기록 | `bash gbt.sh flash <보드> <이미지...> [--no-build]` | rig.json 으로 보드 찾기 → lock → 빌드 → fastboot 진입 → preload 부팅 → 기록 → 부팅 로그 커밋 = 소스 HEAD 확인 | `rig.json`, `config/images.json` → `logs/flash/<시각>_<보드>_<이미지>.log` | 시험 중 (2026-11-03) |
| 4 | 부팅 반복 시험 | (예정) | kern / 전원 반복, DM> 확인, 실패·DDR 재시도 횟수 집계 | | 예정 |
| 9 | 커밋 + push | `bash gbt.sh commit "<무엇을 왜>"` | 정해진 폴더(gbt/ config/ patches/ docs/ tools/ reports/ gbt.sh .gitignore)만 커밋 → push (거부되면 pull --rebase 후 다시). 정리 전 폴더(sdram/ 등)·logs/·rig.json 은 안 넣고 목록만 보여줌 | | 사용 가능 (2026-11-03) |
| 5 | 결과 정리 | (예정) | 보드별 결과 → 엑셀 | `logs/` → `reports/*.xlsx` | 예정 (지금은 수동 생성) |

## 소스 구조

```
gbt.sh                 명령 입구 (bash gbt.sh <명령>) → tools/gbt.py
gbt/                   공통 모듈 (기능끼리 같은 코드를 다시 쓰지 않게 여기 모음)
  rig.py               보드 정보(rig.json) + 사용 중 관리(logs/locks/<보드>.lock). 모든 도구는 rig.acquire(보드, 용도) 뒤에만 장비 사용
  board.py             보드 한 대 제어: Tapo 전원, UART, fastboot 진입, fastboot 명령, 부팅 로그
  images.py            이미지 등록 표 + 빌드
  util.py              로그
tools/                 명령마다 실행 파일 하나 (얇게, 실제 동작은 gbt/ 를 부름)
  gbt.py               명령 나눠주기 (status / discover / probe / build / flash / help)
  discover_rig.py      1. 장비 자동 구분
  build_flash.py       2·3. 빌드 / 기록
patches/<이미지>/       빌드할 때만 적용하는 패치 (예: patches/sbl/afe_power_down_skip.patch)
config/                사람이 고치는 설정 (커밋함)
  rig_tapo.txt         Tapo IP 목록
  images.json          이미지 등록 표: 소스 폴더, 빌드 명령, 결과 파일, fastboot 파티션, 부팅 로그 버전 정규식, 보드 종류별 preload
docs/                  FEATURES.md (이 표), TEST_SPEC.md (규칙)
rig.json               장비 상태 (discover 가 만듦, 커밋 안 함)
logs/                  실행 기록 (커밋 안 함)
reports/               시험 결과 엑셀 (커밋함)
sdram/                 기존 SBL DDR 시험 스크립트 (정리 전)
기존 GUI/엔진          main_app.py, step_engine.py, serial_session.py, tapo_control.py, presets/ ...
```

## 작업 방식 (매번 설명하지 않아도 되게)

1. 원하는 걸 말하면 Claude 가 gbt/ tools/ config/ patches/ docs/ 를 고쳐서 테스트 PC 에 넣고, 실행할 명령 한 줄을 줌
2. 실행해서 결과(화면 출력)를 붙여 주면 Claude 가 로그(logs/)를 직접 읽고 판단
3. 됐으면 `bash gbt.sh commit "<무엇을 왜>"` 한 줄로 커밋 + push
4. 소스(SDK)를 고쳐야 하면 SDK 는 건드리지 않고 patches/<이미지>/ 에 패치 파일로 저장, 빌드할 때만 `--patch` 로 적용
5. 보드는 항상 이름(00, 08, 02 …)으로 부름. 연결이 바뀌면 `bash gbt.sh discover`, 상태는 `bash gbt.sh status`

새 기능을 만들 때 규칙: 장비를 다루는 코드는 gbt/ 에, 명령 입구는 tools/ 에, 바꿀 수 있는 값은 config/ 에. 그리고 이 표와 TEST_SPEC.md 를 같이 고쳐서 커밋

## 기존 스크립트 (정리 전, 계속 사용 중 — 아직 `sdram/boards.conf` + `logs/.lock_B*` 사용, rig.py 가 이 lock 도 "사용 중"으로 같이 봄)

| 스크립트 | 하는 일 | 비고 |
|---|---|---|
| `sdram/run_nokia_sweep.sh` | Winbond(Nokia) SBL DDR sweep (ORDER / STAGES / CANDS) | 보드 자리는 `sdram/boards.conf` 의 N 줄. 1번 기능으로 옮길 예정 |
| `sdram/coop_start.sh` + `sdram/coplan.py` | Keymos 보드 2대 연동 sweep | `sdram/boards.conf` 01 / 02 |
| `sdram/run_dds_sweep.sh` | 위 두 개가 부르는 후보별 빌드·기록·부팅 반복 | 프리셋 `sbl-dds-cycle(-nokia)` |
| `sdram/build_winbond_preload.sh` | Winbond 3200 2ch preload 빌드 → `sdram/preload_winbond_3200.bin` | Keymos preload 와 따로 둠 |
| 그 밖의 `sdram/run_*.sh` | 예전 시험용 | 정리 대상 |

## 시험 결과

- `reports/Winbond_DDR_시험결과.xlsx`, `reports/Keymos_DDR_시험결과.xlsx` (2026-11-03 기준)
