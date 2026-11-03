# 시험 규격

이 문서가 기준이다. 규칙을 바꾸면 이 문서와 스크립트를 같이 고치고 커밋 메시지에 이유를 적는다.

## 0. 보드 관리 (공통 규칙) — `gbt/rig.py`, `bash gbt.sh status`

- 장비 정보는 `rig.json` 하나가 기준 (1장의 페어 찾기가 만듦). 보드 이름 = 페어 이름
- **Tapo 를 켜고 끄거나 UART 를 열거나 fastboot 를 쓰는 코드는 반드시 `rig.acquire(보드, 용도)` 로 그 보드 lock 을 잡은 뒤에만** 실행
  - lock: `logs/locks/<보드>.lock` (flock) + 안에 `{"purpose", "pid", "since"}`. 프로세스가 죽으면 OS 가 자동으로 풂
  - 이미 누가 쓰면 `rig.Busy` → 시작하지 않음
- 현황: `bash gbt.sh status` → 보드마다 종류 / Tapo / UART / fastboot / PBL / 비어 있음 또는 "사용 중 - 용도 (pid, 시작 시각)"
- 옮기기 전까지: 기존 시험 스크립트의 `logs/.lock_B<자리>` 도 사용 중으로 봄 (`sdram/boards.conf` 의 그 자리 Tapo 로 rig 보드 이름을 찾음)

## 1. 장비 자동 구분 (페어 찾기) — `tools/discover_rig.py`

### 목적
Tapo 플러그 / UART / fastboot USB 가 어떻게 짝지어져 있는지 자동으로 찾고, 페어마다 이름을 붙인다.
**페어 이름 = 그 페어에 꽂힌 보드 이름.** 이후 모든 시험 명령·결과 파일·엑셀은 이 이름을 쓴다.

### 입력
- `config/rig_tapo.txt` : Tapo IP 목록 (계정은 `remote_power.env`)
- UART : `/dev/serial/by-path/*` 전부 (꽂은 USB 자리 기준 이름). 같은 장치가 `usb-0:…` / `usbv2-0:…` 두 이름으로 보이면 실제 장치 기준으로 하나만 (`usb-0` 쪽 이름)
- fastboot : `/home/test/bin/fastboot devices -l` 의 `usb:경로`

### 순서
1. Tapo 전부 OFF(3초) → 전부 ON → 8초 동안 모든 UART 를 읽음. 부팅 출력(`ROM(` 또는 의미 있는 출력)이 있는 UART 를 표시. **참고용이고 후보에서 빼지 않음** (2026-11-03: 전부 동시에 켰을 때 02 보드가 안 올라와서 짝을 놓친 일이 있어 변경). 출력 있던 UART 부터 먼저 시도
2. 전부 OFF(3초). 이때 보이는 fastboot 장치는 비교에서 제외
3. 남은 Tapo 를 하나씩:
   1. ON → 남은 UART 전부에 `u` 를 20ms 마다 연타 (최대 15초). `total 3 char` 가 뜨는 UART = 그 Tapo 의 짝
   2. 여분 `u` 를 `x` 로 3글자 채움 → `usb` 전송 → `usb_dnl_fastboot: ready` (최대 15초)
   3. `fastboot devices -l` 에 새로 생긴 `usb:경로` (최대 10초) = 그 보드의 fastboot
   4. OFF → ON → `Starting kernel` 까지(최대 15초) 정상 부팅 출력에서 PBL / SBL / U-Boot 버전, `CFG=… ch=N`, `ddr=… vddq=…` 읽음 → OFF
   5. 찾은 Tapo / UART / USB 는 목록에서 빼고 다음 Tapo
4. 페어마다 보드 이름 입력. 이름을 입력할 때까지 그 페어의 Tapo 를 계속 껐다 켬 (켜짐 4초 / 꺼짐 3초, `--blink-on --blink-off` 로 조절). 깜빡이는 보드를 보고 이름 입력 → 끄고 다음 페어. 이전 rig.json 에 같은 Tapo + 같은 UART + 같은 PBL 이 있으면 그 이름을 기본값으로 제안 (`--keep-names` 면 묻지 않음)
5. `rig.json` 저장, 사본 `logs/rig/<시각>.json`. 전원은 전부 OFF 로 끝냄

### 판정 / 표시
- 짝을 다 찾은 페어 : 이름, Tapo, UART, fastboot, 보드 정보
- fastboot 못 찾음 : 페어는 저장하되 `fastboot: null` (`fastboot_ready` 로 usb_dnl_fastboot ready 여부 구분 → USB 선 문제인지 보드 문제인지)
- 짝 없는 Tapo / UART : `unmatched` 에 이유와 함께 저장
- 보드 종류(`type_guess`)는 채널 수로 추정: 2ch → winbond-2ch, 1ch → keymos-1ch. **추정값** (DDR 제조사를 직접 읽는 것은 아님). 2026-11-03 첫 실행에서 Winbond 보드 `CFG=1 ch=2`, Keymos 보드 `ch=1` 로 맞게 나옴

### 하지 않는 것
- **사용 중인 보드는 건드리지 않음** (2026-11-03): 다른 보드가 시험 중이어도 나머지 보드는 찾을 수 있음
  - 사용 중 판단: `rig.status()` (0장). 찾는 동안 비어 있는 기존 보드는 lock 을 잡아 두어 다른 시험이 끼어들지 못함
  - 추가 안전장치: lock 없이 UART 를 연 프로그램(picocom 등, `/proc/*/fd`) → 이전 rig.json, 없으면 boards.conf 로 그 UART 의 Tapo 를 앎
  - 그 Tapo 는 끄지도 켜지도 않음 (1단계 전부 OFF/ON 에서도 빠짐), 그 UART 에는 아무것도 안 보냄, 그 fastboot 는 비교에서 뺌
  - rig.json 에는 이전 값을 `"in_use": true` 로 남김
  - UART 를 누가 쓰는데 어느 Tapo 인지 모르거나(표에 없음) 여러 Tapo 로 걸리면 **시작하지 않음** — 엉뚱한 Tapo 를 끄면 시험이 죽기 때문
  - `--probe` 도 시험 중인 Tapo 는 거부, 시험 중인 UART 는 건너뜀
- 같은 PBL 을 쓰는 보드끼리 바꿔 꽂으면 자동으로 알 수 없음 → 이름은 사람이 확인

### rig.json 형식
```json
{
  "created": "2026-11-03T16:00:00",
  "pairs": {
    "08": {"tapo": "192.168.111.8", "uart": "/dev/serial/by-path/...", "fastboot": "usb:1-5", "fastboot_ready": true,
           "pbl": "7b939ba (2026-10-16 14:18:45)", "sbl": "...", "uboot": "...", "ch": 2, "type_guess": "winbond-2ch",
           "ddr": 3200, "vddq": "6(0.700)"}
  },
  "unmatched": {"tapo": {"<ip>": "<이유>"}, "uart": {"<by-path>": "<이유>"}}
}
```

## 2. 이미지 빌드 / 3. fastboot 기록 — `tools/build_flash.py`

### 이미지 등록 표 `config/images.json`
| 이미지 | 소스 폴더 | 빌드 | 기록 (파티션 ← 파일) | 버전 확인 (부팅 로그) |
|---|---|---|---|---|
| sbl | `~/gdm-autotest/sdk/sbl/7275x` | `./m2 DDR_WINBOND=y` (= m2-wbd, m2-wbd 는 뒤 인자를 버려서 m2 직접) | `sbl` ← `images/sbl_7275x_c.bin` (sbl2 포함) | `SBL: <날짜> <커밋>(` |
| uboot | `~/gdm-autotest/sdk/uboot/7275x` | `./md_dual_sbl` | `uboot` ← `u-boot_7275x.bin`, `tfa` ← `arm-trusted-firmware/tfa_7275x.bin` (항상 같이) | `U-Boot: <날짜> <커밋>(` |
| linux / openwrt | (예정) | | | |

preload (fastboot 기록할 때 RAM 에 올리는 것, 보드 종류별): winbond-2ch → `sdram/preload_winbond_3200.bin`, keymos-1ch → `sdk/uboot/preloader/u-boot_7275x_preload.bin`

### build
- 소스 폴더 HEAD 와 커밋 안 된 변경을 로그에 남기고 빌드 명령 실행. rc=0 이고 결과 파일이 빌드 시작 뒤에 새로 생겨야 성공
- 빌드 로그: `logs/flash/<시각>_build_<이미지>.log`

### 옵션
- `--set <이미지>.<make변수>=<값>` (여러 번): 빌드 명령 뒤에 `VAR=VAL` 로 붙임. make 명령줄 값이 Makefile 의 `VAR=y` 를 덮어씀 → Makefile 은 안 고침 (다른 시험 SBL 에 영향 없음)
  - 확인: SBL Makefile 은 켜진 옵션을 `Makefile:NNN: 이름` 으로 찍음 → `=n` 인데 찍히면 실패. 예) `--set sbl.AFE_POWER_DOWN=n`
- `--preload <파일>`: 보드 종류를 모를 때(type_guess ?) 또는 다른 preload 를 쓸 때
- `--patch <이미지>:<패치 파일>` (여러 번): 빌드 직전에 `git apply`, 빌드가 끝나면(실패해도) `git apply -R` 로 되돌림 → 소스 폴더는 깨끗하게 유지 (sweep 이 "커밋 안 된 변경" 으로 거부하지 않음)
  - 패치 파일은 `patches/<이미지>/` 에 둠. 머리말(첫 diff 줄 전)에 설명과 `gbt-expect: <글자>` → 기록 뒤 부팅 로그에 그 글자가 있어야 성공
  - 빌드하는 동안 소스 폴더 lock(`logs/.lock_sbl_<폴더>`, 기존 sweep 과 같은 파일)을 잡음 → sweep 과 동시에 소스를 건드리지 않음
  - 예) `patches/sbl/afe_power_down_skip.patch`: AFE_POWER_DOWN=n 빌드는 링크 실패(ihs.c 의 #if AFE_POWER_DOWN 안 refx_clock_mhz 를 grf_gpio_ctrl_7259x.c:896 이 참조) → afe_power_down() 초입에서 return

### flash `<보드> <이미지...>`
1. `rig.acquire(보드)` — 다른 도구가 쓰는 중이면 시작 안 함
2. 빌드 (`--no-build` 면 지금 있는 파일로)
3. fastboot 진입: Tapo OFF(2.5초) → ON → `u` 20ms 연타 (최대 15초) → `total 3 char` → 여분 `u` 를 `x` 로 3글자 채움 → `usb` → `usb_dnl_fastboot: ready` (최대 15초)
4. `fastboot -s <보드 fastboot> --preload <preload> boot` → 출력에 `OKAY` (30초)
5. 파일마다 `fastboot -s … flash <파티션> <파일>` → `Writing '<파티션>' … OKAY` (120초)
6. 3~5 중 하나라도 실패하면 3부터 다시, 최대 `--tries`(기본 3)번. 다 실패하면 전원 OFF, 종료코드 1
7. 확인: 전원 껐다 켜서 `Starting kernel` 까지(최대 40초) 부팅 로그에서 version_re 로 커밋을 읽어 소스 HEAD 와 앞 7자리 비교. 다르면 종료코드 2 ("기록은 됐지만 반영 안 됐을 수 있음")
8. 전원은 켠 채로 둠. 전체 로그 `logs/flash/<시각>_<보드>_<이미지>.log`
