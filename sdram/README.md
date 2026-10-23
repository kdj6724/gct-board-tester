# SDRAM (SBL DDR 설정) 안정화 테스트

SBL 의 DDR 설정 후보를 하나씩 적용해서 `kern reboot` 100번 연속 성공하는 후보를 찾는다.

## 흐름 (후보 하나)

1. SBL 소스의 대상 파일 2개를 캠페인 시작 때 보관한 원본으로 되돌림
   - `base/drivers/orbit_ddram/sphy_v9r8_functions.c`
   - `base/drivers/orbit_ddram/mr_config.h`
2. 후보 패치 적용 (`git apply`)
3. `presets/sdram-sbl-cycle` 실행: SBL 빌드(`./m2-wbd`) → SBL 다운로드(실패 시 전원 off 후 최대 5번) → 부팅 → `kern reboot` 100번
4. 100번 연속 성공 → 그 후보로 끝 (`--confirm 1` 이면 같은 설정으로 100번 한 번 더 확인).
   한 번이라도 실패 → 로그·패치·사유 저장 후 다음 후보
5. 끝나면 SBL 소스는 원본으로 되돌려 둔다 (통과한 패치는 결과 폴더에 있음)
6. 어떻게 끝나든(통과, 후보 소진, `--until`, ERROR 연속, Ctrl-C, 터미널 닫힘) 보드 전원을 끈다 (`presets/power-off`, `--no-power-off` 로 끔)

빌드·다운로드·장비(Tapo 등) 단계 실패는 DDR 결과가 아니므로 `ERROR` 로 보고, 기록하지 않고
5분 뒤 같은 후보를 다시 시도한다. 6번 연속이면 멈춘다 (`--max-errors`, `--error-wait`).

## 후보가 떨어지면 자동으로 새 후보 (`cli/sdram_knobs.py`)

`ORDER.txt` 목록을 다 쓰면 새 후보를 만들어 계속 간다 (`--no-auto` 로 끔).
- 점수 = 실패 전까지 성공한 `kern reboot` 횟수 (통과면 100, 첫 부팅 실패면 0)
- 지금까지 점수가 가장 높은 후보부터, 손잡이 하나만 한 칸 바꾼 이웃 중 아직 안 해본 것을 시험
- 손잡이와 값 범위 (소스 주석 기준):

| 손잡이 | 값 | 기준 |
|---|---|---|
| dsvref (PHY DQ VREF) | 0x06 0x0d 0x14 0x1a 0x20 0x26 0x2d 0x33 (10~80%) | 0x14 |
| cavref (CBT CA VREF) | 0x1a 0x33 0x4d 0x66 0x80 0x99 0xb3 0xcc 0xe6 (10~90%) | 0x4d |
| pdds (MR3) | 1~6 | 1 |
| adj (gate/read-adj 후보 줄) | test-0, current, test-1~6 | test-0 |

- 자동 후보 이름 예: `a012_ds0d_ca66_pd6_test0`. 패치 머리에 어떤 후보의 이웃인지 적힌다.
- dsvref 주석의 90% 줄은 `58 0xaa` 로 숫자가 안 맞아 제외했다.
- `--until "2026-10-06 09:00"` 이면 그 시각 이후 새 후보를 시작하지 않는다.

## 실행 (테스트 PC)

```
tmux new -s sdram
cd ~/gbt && ~/.venv-gbt/bin/python cli/sdram_campaign.py --port /dev/ttyUSB0 --baud 921600 --confirm 1 --until "2026-10-06 09:00"
# 이어하기
cd ~/gbt && ~/.venv-gbt/bin/python cli/sdram_campaign.py --resume logs/sdram_<시각> --port /dev/ttyUSB0 --baud 921600
```

- 중간에 멈추기: 캠페인 폴더에 `stop` 파일 → 지금 후보까지 하고 멈춤 (Ctrl-C 는 즉시)
- 후보 추가/순서 변경: `sdram/variants/ORDER.txt` (도중에 고쳐도 다음 후보부터 반영)

## 결과 (`logs/sdram_<시각>/`)

| 파일 | 내용 |
|---|---|
| `summary.txt` | 후보별 결과 (PASS/FAIL, 버틴 reboot 수, 설정, 사유) + 가장 오래 버틴 후보 |
| `results.csv` | 같은 내용 CSV |
| `<후보>/<후보>.patch` | 적용한 패치 |
| `<후보>/applied.diff` | 실제 적용된 변경 (원본 대비) |
| `<후보>/*_FAIL.log` | 실패 로그: 이전 부팅 / 실패한 부팅 / 실패 이후 |
| `orig/` | 시작 때 보관한 원본 |

## 후보 (`sdram/variants/`)

기준 = `gct-board-tester/sbl` master (git pull 후, DDR_WINBOND_DFS_ADJ 쪽 설정).
값은 소스에 이미 있는 후보나 주석의 범위만 썼다.

| 후보 | 바꾸는 것 | 근거 |
|---|---|---|
| v00_baseline | 없음 (기준 실패율 확인) | |
| v01_adj_current | gate/read-adj → 소스의 `// current` | 소스에 남아 있는 후보 |
| v02~v07_adj_test1~6 | gate/read-adj → `// test-1` ~ `// test-6` | 소스에 남아 있는 후보 |
| v08_dsvref_0x0d / v09_dsvref_0x1a | PHY DQ VREF DVFS0/1 DS0/DS1 0x14(30%) → 20% / 40% | 소스 주석의 % 표 |
| v10_cavref_0x33 / v11_cavref_0x66 | CBT CA VREF 0x4d(30%) → 20% / 40% | 소스 주석의 % 표 |
| v12~v14_pdds_6/4/2 | LPDDR4 PDDS (MR3) 1 → 6 / 4 / 2 | mr_config.h 주석 (1~6, default 6) |

미확인 (문서 없음):
- `W:\sbl\pdf` 의 문서 5개는 OMC(메모리 컨트롤러) 문서라 PHY 값(gate/read-adj, DS/CA VREF)의 정의가 없다.
- DVFS 배열의 몇 번째 칸이 실제 동작 주파수(3732)에 쓰이는지, adj 한 칸이 얼마인지 모른다.
- CA VREF 가 이후 CBT 트레이닝으로 덮어써지는지 모른다.
- PDDS 값의 의미는 JEDEC LPDDR4 기준이고, Winbond 칩 데이터시트로는 확인 안 함.

## 근거가 된 실패 (10-01)

`kern reboot` 2번째: 커널에서 modem 이미지를 MMC 에서 읽다가 19초간 출력 없음 →
워치독 리셋(`rr=0x4(WD0)`). DDR 초기화와 6ms 메모리 테스트는 통과했었다. DDR 원인인지는 미확인.
