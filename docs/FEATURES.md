# gct-board-tester 기능 목록

기능을 만들거나 바꿀 때마다 이 표를 같이 고쳐서 커밋한다. 자세한 규칙은 [TEST_SPEC.md](TEST_SPEC.md).
명령은 모두 gct-board-tester 폴더에서 실행. 기본은 `bash gbt.sh <명령>` (`bash gbt.sh help` = 이 표)

| # | 기능 | 명령 | 하는 일 | 입력 → 출력 | 상태 |
|---|---|---|---|---|---|
| 0 | 보드 현황 | `bash gbt.sh status` | 보드마다 Tapo / UART / fastboot / 종류 / 사용 중인지(누가, 언제부터) | `rig.json` + `logs/locks/` | 사용 가능 (2026-11-03) |
| 1 | 장비 자동 구분 (페어 찾기) | `bash gbt.sh discover` (진단: `bash gbt.sh probe <Tapo IP>`) | Tapo / UART / fastboot USB 짝을 자동으로 찾고 페어마다 보드 이름을 붙임. 사용 중인 보드는 건너뜀 | `tools/rig_tapo.txt` → `rig.json` (+ `logs/rig/<시각>.json`) | 시험 중 (2026-11-03) |
| 2 | 이미지 빌드 | (예정) `gbt build <이미지>` | preloader / sbl / uboot / linux / openwrt 빌드 | `images.conf` → 이미지 파일 + 빌드 로그 | 예정 |
| 3 | fastboot 기록 | (예정) `gbt flash <보드> <이미지...>` | rig.json 으로 보드 찾기 → fastboot 진입 → preload 부팅 → 기록 → 부팅 로그로 버전 확인 | `rig.json`, `images.conf` | 예정 |
| 4 | 부팅 반복 시험 | (예정) | kern / 전원 반복, DM> 확인, 실패·DDR 재시도 횟수 집계 | | 예정 |
| 5 | 결과 정리 | (예정) | 보드별 결과 → 엑셀 | `logs/` → `reports/*.xlsx` | 예정 (지금은 수동 생성) |

## 공통 모듈

| 파일 | 하는 일 |
|---|---|
| `rig.py` | 보드 정보(`rig.json`)와 사용 중 관리(`logs/locks/<보드>.lock`). 모든 도구는 `rig.acquire(보드, 용도)` 로 lock 을 잡은 뒤에만 Tapo / UART / fastboot 를 씀 |

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
