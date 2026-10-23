# RTL8363SC 스위치 Flow Control 임계값 튜닝

GDM7275x 보드의 RTL8363SC 스위치에서 flow control(802.3x PAUSE) 임계값을 바꿔
dual iperf 합계 처리량을 올리는 작업 기록 (2026-09-30 ~ 10-01).

## 결론

**추천 세팅: a4_04**

| 레지스터 | PauseAll 0x121E | Sys-On 0x121F | Sys-Off 0x1220 | Shared-On 0x1221 | Shared-Off 0x1222 | Port-On 0x1227 | Port-Off 0x1228 |
|---|---|---|---|---|---|---|---|
| def | 496 | 240 | 204 | 190 | 154 | 112 | 100 |
| **a4_04** | **496** | **470** | **430** | **450** | **410** | **180** | **120** |

```
kern rtl8363 wr 0x121e 496
kern rtl8363 wr 0x121f 470
kern rtl8363 wr 0x1220 430
kern rtl8363 wr 0x1221 450
kern rtl8363 wr 0x1222 410
kern rtl8363 wr 0x1227 180
kern rtl8363 wr 0x1228 120
```

iperf3 `-t10 -w4M`, A+B 합계(Mbps), 세팅당 50회씩 이틀:

| 세팅 | 회수 | 1800 이상 | 느린 회차 중앙값 | 1400 미만 | Min | Avg |
|---|---|---|---|---|---|---|
| def (09-30 대조군 3회) | 150 | 51% | **~1070** | 다수 | 963 | 1475 |
| **a4_04** (09-30 + 10-01) | 100 | 60% | **~1500** | **0** | 1420 | **1710** |

- 처리량은 두 모드뿐: 빠름 ≈1853 / 느림. 임계값은 **느린 회차의 바닥**을 올린다
  (def ~1070 → a4_04 ~1500). 빠른 회차 비율(대략 40~60%)은 세팅으로 바뀌지 않았다.
- a4_04 근처(PauseAll 496, Sys 450~490/410~470, Shared 450/410, Port 148~212/108~172)는
  모두 느린 회차 ~1500 으로 비슷함. a6_03, a6_06, a1_02 도 100회 기준 거의 같음.
- 이틀 결과가 재현됨 (`results/fc_focus_0930_recheck_1001.xlsx` 의 `합계_100` 시트).

## 관찰 / 미확인 사항

- **멈춤(TRAFFIC_DEAD)은 세팅 값과 무관해 보임.** 집중 테스트에서 대조군 def_c2 도 멈췄고,
  탐색 때 멈췄던 a1_02 / a4_04 는 50회씩 두 번 모두 안 멈춤. 멈춤 12번 중 11번이
  새 세팅의 레지스터 7개를 쓴 직후 첫 iperf 에서 발생 →
  가설(미확인): 동작 중에 임계값을 바꾸면 가끔 멈춤. 부팅 때 한 번만 쓰면 문제없을 수 있음.
  확인 필요: 한 세팅 고정으로 장시간 테스트.
- **PauseAll = 512 는 5/5 즉시 멈춤.** PauseAll ≥ 512 는 전부 성능 나쁨.
  필드 폭(9비트?)은 데이터시트로 확인 안 됨.
- **Shared-On 이 PauseAll 에 가까우면 처리량 급락** (예: PA 496 / Shared-On 470).
- a4_04 영역의 느린 회차에는 TCP 재전송이 ~60개 붙음 (def 느린 회차는 0).
  느린 모드의 성격이 다른 것으로 보이나 원인 미확인.
- a4_05, a6_19 는 Shared 값이 같은데 느린 회차가 ~1370 (B 그룹 수준). 원인 미확인.
- `e.rx_pause_frames` 는 `-t10` 에서 대부분 65535 → 포화로 보임(미확인), 비교에 못 씀.
- flow control 을 끄면 loss 발생, A / B 단독으로는 느린 회차가 안 나옴 (사용자 확인).

## 테스트 환경

| 항목 | 값 |
|---|---|
| 보드 | GDM7275x, 192.168.5.1, CPU 4개 online |
| 스트림 A | `taskset -c 1 iperf3 -c 192.168.5.113 -p 5113 -t10 -i1 -w4M` |
| 스트림 B | `taskset -c 3 iperf3 -c 192.168.5.112 -p 5112 -t10 -i1 -w4M` (B 는 Windows PC) |
| 판정 | A+B 합계, 1500 이상 pass |
| 테스트 PC | Ubuntu Test27-DT, 코드는 `~/gbt` (노트북 공유 cifs 마운트) |

## 방법 / 도구

1. **탐색**: 세팅마다 `-t3` × 10회 → min 순으로 후보 선정
2. **집중**: 후보를 `-t10` × 50회, 대조군 def 를 10개마다 끼워 넣음
3. 멈추면 Tapo 로 전원 재인가 후 다음 세팅부터 이어감 (재부팅 횟수 기록)

| 파일 | 역할 |
|---|---|
| `scripts/fc_sweep4.sh` | 보드에서 도는 스윕 스크립트 (레지스터 쓰기 → iperf A/B 동시 → 카운터) |
| `presets/fc-sweep-2stage.json` | 부팅 → CPU online → ifconfig → 스크립트 업로드/실행 |
| `cli/sweep_campaign.py` | 캠페인 실행기 (멈춤 시 재부팅·이어하기, explore/focus 모드, 중복 제거) |
| `cli/fc_db.py` | 누적 결과 DB `data/fc_db.csv` |
| `cli/gen_sets.py` | 세팅 목록 생성기 |
| `cli/post_fc_sweep4.py` | 로그 → CSV / 요약 |
| `scripts/sets/*.txt` | 세팅 목록 (`이름 PA SOn SOff ShOn ShOff POn POff`) |

실행 예 (테스트 PC, `~/gbt`):

```
# 탐색 (-t3 x10), 끝나도 대기하며 add.txt / focus.txt / stop 감시
~/.venv-gbt/bin/python cli/sweep_campaign.py --mode explore --sets scripts/sets/explore_0930_all.txt --control def --control-every 10 --open
# 집중 (-t10 x50)
~/.venv-gbt/bin/python cli/sweep_campaign.py --mode focus --sets scripts/sets/recheck_1001.txt --control def
# 누적 DB
~/.venv-gbt/bin/python cli/fc_db.py report --t 10 --sort min
```

## 결과 파일 (`results/`)

| 파일 | 내용 |
|---|---|
| `fc_focus_0930_recheck_1001.xlsx` | 09-30 집중 28개 세팅, 10-01 재확인 4개, 이틀 합계, raw |
| `summary_focus_0930.txt` | 09-30 집중 캠페인 요약 (회차/멈춤 기록 포함) |
| `summary_explore_0930.txt` | 09-30 탐색 캠페인 요약 (-t3, 약 180개 세팅) |

원본 로그는 `logs/campaign_20260930_192614`, `logs/campaign_20261001_095254`
(logs/ 는 git 제외).

## 다음 할 일

- a4_04 고정, 레지스터 한 번만 쓰고 장시간 → 멈춤 가설 확인
- 느린 회차의 iperf3 초당 기록(처리량 / Retr / Cwnd) 수집 → 느린 모드 원인
- 0x121E 비트 폭 데이터시트 확인
- 드라이버 기본값을 a4_04 로 반영할지 결정
