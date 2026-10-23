# SBL DDR kern reboot 튜닝 — 진행 기록 (마지막 갱신 2026-10-12)

## 목표 / 조건
- 보드: GDM7275x + Winbond W66DP2RQT**AHJ** (LPDDR4X, 4267 등급, 칩 사진 확인). Nokia Receiver3 계열로 추정 (미확인).
- 목표: DDRRATE 3732 (SBL 기본, 1865MHz) 에서 `kern reboot` 200회 연속 통과. 3200 은 비교용으로만 (칩이 4267 등급).
- 1회 시험 = SBL 소스 수정 → `./m2 DDR_WINBOND=y <빌드 인자>` → fastboot → 전원 ON → DM> 확인 후 kern reboot 반복, 실패 시 중단.
- 테스트 PC Test27-DT, `~/gbt-local` (노트북 공유 `~/gbt` 를 rsync 한 로컬 복사본) 에서 tmux 로 실행. 끝나면 결과를 `~/gbt/logs` 로 복사.

## 결과 요약 (10-01 ~ 10-11, 3732 805회 / 약 14,600 부팅)
- 200회 통과 0회, 최대 149. 원본 약 9.7%/부팅 → 최고 조합 약 1.35%/부팅 (200회 50% 통과에는 약 0.35% 필요).
- 최고 조합: VDD2 1.100 + VDDQ 0.725 + PU-CAL 0 + DQ VREF 0x06 + adj test-6 → 132/58/30 (3번뿐, 운 영향 큼).
- 같은 조건끼리 비교해서 확실한 것: VDD2 1.100 (0.76배), VDDQ 0.725 (0.70배), DQ VREF 0x0d~0x20 (0.6~0.8배) 좋음.
  PDDS 1, DQ ODT 4, adj test-0 (모두 원본) 이 가장 좋음. 리프레시 2배는 2.3배 나쁨 → 리텐션 문제 아님.
  PU-CAL 0, temp sensing, CA ODT 1~4 는 이득 없음.
- 첫 부팅부터 실패: VDDQ ≤0.650, SoC/PHY ODT 1~3, CA ODT 5~6, DQ VREF 0x26.
- 실패는 부팅마다 독립 무작위 (버틴 횟수 분포 ≈ 기하분포). 유형: oops 37%, 예상치 못한 재부팅 31% (+ CP decompress 13%), hang 16%.
- 3200 원본 설정 144/43/41 (≈1.3%) ≈ 3732 최고 조합.
- 공유용 엑셀: `logs/SBL_DDR_reboot_시험결과_공유.xlsx` (요약 / Knob 설명 / 동일조건 비교 / 상위 조합 / 실패 유형 / 전체 결과).

## NOKIASUB6-275 메일 (9/17~18, KJ Lee / Dean Ahn) 에서 알게 된 것
- main.c 의 `//OK` `//NG` 는 KJ 가 9/18 Board-A 에서 본 결과. Board-A 는 650mV 만 OK, Board-B(1CH 로만 부팅되던 보드)는 650mV 이하 training 실패 → 700mV 로 바꿈.
  우리 보드도 650 이하 첫 부팅 실패 → Board-B 와 같은 양상.
- KJ 검증은 kern reboot 10회 + memtester 16MB 정도. test-0 / test-3 이 mismatch 0, test-3 선택. "보드가 무지 뜨겁다".
- Nokia 는 "GW33 과 길이 같고 stack-up 만 다름 (GW33 L1/L3/L5, Receiver3 L1/L3/L7)" 이라고 했지만,
  Receiver3 길이표 자체 Check 열에 NOK 다수: DDR0 DQ[5], CS[0]/[1], CKE[0], CA[2~5] / DDR1 DQ[3], CKE[0]/[1], CA[2~4](최대 +5.3mm), RESET.
  GW33 은 DQS[1] 차동 24µm, DDR1 RESET 만. byte 안 DQ 길이 차이 GW33 ~2mm vs Receiver3 2.7~5.0mm.

## 소스에서 확인한 사실
- adj 배열 `sphy_v9r8_lp4_dfs_gateadj/readadj[2][4]` = `[cfg->num_ch][dvfs_level]` (byte lane 아님).
  num_ch 0 = CH0 (omc_cfg), 1 = CH1 (omc_cfg_1). 3732(t_ck 536) 은 dvfs_level 3 → 마지막 칸만 쓰임. 3732 에서 current == test-4.
  값 0/1/9 의 물리적 의미는 PHY 문서 없어 미확인.
- `RETRY_DDR_CH=1` (m2 기본 2) → ddr_sweep_ch != 2 → CH0(omc_cfg) 만 초기화.
- `command_bus_training = 0`, `phy_vref_trn_mode = 0`, `dram_vref_trn_mode = 0`. CBT 와 PHY VREF 트레이닝 코드는
  `#if !defined(DDR_OPTI_SZ)` 안이고 Makefile `DDR_OPTI_SZ ?= 1` → 지금 빌드에는 CBT 가 없음.
  CA VREF(CBT_CA_VREF) 를 바꿔도 반영 안 되던 이유로 보임. DRAM VREF 트레이닝은 DDR_OPTI_SZ 와 무관.
- periodic_training = 1 (24ms), periodic ZQ cal = 1 (48ms), reboot_with_training = 1.

## 진행 중 / 대기 (10-12)
1. `sdram/run_ch.sh` (실행 중) — 기준 설정 = VDD2 1.100 + VDDQ 0.725 + DQ VREF 0x0d + PU-CAL 0 (2CH 에서 40/49/56).
   - 1CH 비교 (`RETRY_DDR_CH=1`): 원본 설정, 기준 설정. 첫 부팅 실패면 건너뜀. 폴더 이름 `_1ch`.
   - 채널별 / gate·read 따로 adj (태그 `gABrCD` = gate CH0=A CH1=B, read CH0=C CH1=D):
     test-0, test-3, g11r00, g00r11, g99r00, g00r99, g19r19, g91r91. 각 3번.
2. `sdram/run_train.sh` (run_ch 끝나면 자동 시작) — 기준 설정 + adj test-0, 각 3번:
   0) DDR_OPTI_SZ=1/0 빌드 크기 비교, a) DDR_OPTI_SZ=0 대조군, b) +CBT, c) DRAM VREF 트레이닝 (기본 빌드),
   d) CBT + DRAM VREF, e) CBT + DRAM VREF + PHY VREF. a) 가 첫 부팅 실패면 DDR_OPTI_SZ=0 쪽 건너뜀. 폴더 이름 `_opt0`.
   ※ 10-12 확인: DDR_OPTI_SZ=0 은 컴파일 실패 (LP5 함수 선언 순서 implicit declaration, CBT 변수 unused → -Werror).
     검증 안 된 경로라 보류 → SBL DDR 담당에게 CBT 사용 이력 / DDR_OPTI_SZ 기본값 이유 확인 후 결정. run_train.sh 는 c) 만 돔.
4. `sdram/run_arm.sh` (준비, run_ch/run_train 끝나면 시작) — ARMVCC 0.875 / 0.825 (VCC_ARM = ARM+DDR core 디지털 전원,
   GDM7275X 데이터시트 9.2 사양 0.808~0.893V). 기준 설정 각 3번. 폴더 이름 `_arm875` / `_arm825`.
   ※ PT_DDR_SYS 문서의 PHY PLL 사양(Fvco ≤3200MHz)과 3732 설정(≈3731MHz)이 안 맞지만, 다른 보드에서 3732 정상 동작 이력 있음 → 원인 후보에서 제외.
3. 멈춘 것: `sdram/run_focus.sh` (sdram_20261009_001016_v2_1100 이어서, DQ VREF 0x02~0x0a 추가) — 필요하면 다시 실행.

## HW / Nokia 확인 요청할 것
- MCP 레일(DCDC_0.6V_MCP) = DRAM VDDQ 볼인지, 볼 실측 전압. 0.700/0.725 는 LPDDR4X 사양(0.57~0.65V) 밖.
- Receiver3 길이표 Check NOK 항목들 vs "길이 같다" 답변. stack-up L7 비아/stub SI 영향.
- 같은 SBL 로 다른 보드도 200회 못 넘기는지 (보드 개체 문제 분리). CA ODT off 의도. PHY 문서 / 검증된 PHY 설정.
- CP decompress error 가 DDR 원인인지.

## 도구
- `cli/sdram_campaign.py`: --seed / --explore / --skip / --build-args / --repeat / --until / --resume. CP decompress 실패는 1번 재시험.
  끝나면 SBL 소스 원본 복원 + 보드 전원 OFF. 부팅 로그의 ddr= / vdd2= / vddq= / CH(n) 를 기대값과 비교.
- `cli/sdram_knobs.py`: dsvref, pdds, adj(+합성 gABrCD), dqodt, socodt, phyodt, vddq, refx, tsense, pucal, caodt, cbt, pvref, dvref.
- 멈추기: `touch logs/STOP_ALL` (지금 후보까지 하고 멈춤). Ctrl-C 도 원본 복원 + 전원 OFF.

## 10-12 PHY 문서(OpenEdges SPHY v9r8 TRM) 확인 결과
- gate/readadj: bit3=방향(1=빼기), [2:0]=사이클. 0x9=-1, 0x1=+1. TRM Table 7.6 권장(1066<f≤2133MHz) = 0/0 = test-0.
  같은 표의 나머지 항목(gateduradj 4, readduradj 7, auto_dqs_clean 1, testircv 3, rpre_opt 1, gate train OFF, write train ON)은 SBL 과 같음.
- TRM 7.4.1 권장과 다른 것: PHY DQ VREF 0x1F(19.3%) vs 0x14 / MR22 SoC ODT RZQ/6 vs RZQ/4 / MR14 19.2%(code 23) vs 16.8%(17) /
  CA ODT off 인데 MR12 27.2%(CA ODT RZQ/1 일 때 값; ODT off 면 33.6%).
- 소스 주석의 DQ VREF % 표는 TRM 과 안 맞음 (TRM: 0x1D=20.1%, 0x1F=19.3%, 0x37=36.9%).
- PHY 코어 0.85V±5% (= VCC_ARM), PHY IO VDDQ 0.57~0.65V. tDQSCK 4ps/°C, 7ps/mV (Winbond). PLL 지터 ≤4.9ps p-p @4266.
- `sdram/run_ref.sh`: 0) DQ VREF 0x14 대조, a) 0x1F, b) +SoC ODT 6, c) +MR14 23, d) +CA ODT 1, e) c+MR12 29. VDD2 1.100/VDDQ 0.725, 각 3번,
  트레이닝 dump(`sdram/dump/trn_dump_a.patch`) 같이 얹고 전체 로그 저장.
- 트레이닝 dump 단독: `sdram/run_dump.sh` (원본 설정 + dump 출력만).
