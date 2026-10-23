"""
SBL DDR 설정 "손잡이"(knob) - 소스에서 지금 값을 읽고, 값을 바꾼 소스를 만든다.
sdram_campaign.py 가 후보를 자동으로 만들 때 쓴다.

값 목록은 소스에 있는 것만 쓴다 (sphy_v9r8_functions.c / mr_config.h 의 주석과 후보 줄):
  adj    : gate/read-adj 후보 줄의 태그 (// test-0, // current, // test-1 ~ 6) - 순서 없는 선택지
           + 합성 태그 gABrCD (채널별·gate/read 따로, ADJ_CH 설명 참고. 새 줄을 후보 줄 뒤에 넣음)
  dsvref : PHY DQ VREF (DVFS0/1 DS0/DS1), 주석 % 표 10~80% (90% 줄은 '58 0xaa' 로 숫자가 안 맞아 제외)
  cavref : CBT CA VREF, 주석 % 표 10~90%
  pdds   : LPDDR4_PDDS (MR3 OP[5:3]), 1~6 = RZQ/1~RZQ/6 = 240~40Ω (Winbond W66DP2RQT 데이터시트 24쪽)
  dqodt  : LPDDR4_DQ_ODT (MR11 OP[2:0]), 0=끔, 1~6 = RZQ/1~6 (데이터시트 27쪽). 3733 동작점(1865MHz)은
           timing_params cfg_1 의 write_odt=1 이라 MR11 에 실제로 들어감 (orbit_init.c)
  socodt : LPDDR4_SOC_ODT (MR22 OP[2:0], 데이터시트 35쪽) - DRAM 에 알려주는 "컨트롤러 ODT 값"
  phyodt : PHY_ODT (mr_config.h) - PHY 쪽 실제 ODT. 값의 PHY 쪽 의미(인코딩)는 PHY 문서가 없어 미확인 → 1~6 만 씀
           원본은 둘 다 4. 10/07 첫 ODT 캠페인은 둘을 같은 값으로 같이 바꿨다 (그 결과는 phyodt = socodt 로 본다)
dsvref/adj/cavref 의 물리적 의미는 PHY 문서가 없어 미확인.
dsvref/cavref/pdds/adj 변경은 #if defined(DDR_WINBOND_DFS_ADJ) 쪽(지금 빌드되는 쪽)에만 한다.
CA ODT(LPDDR4_CA_ODT) 는 cfg->ca_odt 가 켜질 때만 쓰이는데 이 소스에서 켜는 곳을 못 찾아 제외.
  vddq   : base/sys/main.c ddr_init() 의 #if defined(DDR_WINBOND) 쪽 pmic_vcc_mcp_set(VCC_MCP_0_xxx) (mV).
           부팅 로그에 'vddq=N(0.xxx)' 로 찍히는 레일. 원본은 0.700 인데 같은 줄 주석이 //NG, 0.650 줄은 //OK.
           Winbond 데이터시트 285쪽 LPDDR4X VDDQ 0.57~0.65V (typ 0.6) → 0.700 은 범위 밖.
           이 레일이 DRAM VDDQ 볼인지는 회로도로 미확인 (로그 라벨 'vddq' 와 레일 이름 'DCDC_0.6V_MCP' 기준).
           main.c 가 캠페인 --files 에 있을 때만 바꿀 수 있음
  refx   : 리프레시 배율. test_lpddr4.c 의 omc_cfg.t_refiab/t_refipb (원본 3904000/488000 ps = 데이터시트 114쪽 1x tREFI)
           2 = 두 배 자주 리프레시 (tREFI 를 줄이는 건 데이터시트상 항상 허용, 85°C 초과 시 필요한 값)
  tsense : test_lpddr4.c 의 omc_cfg.temp_sensing (원본 0). 1 = OMC 가 MR4 를 주기적으로 읽어 리프레시를 온도에 맞춤
  pucal  : LPDDR4_PUCAL (MR3 OP[0], mr_config.h, 원본 1). LPDDR4X 모드 274쪽: 0 = VDDQ*0.6, 1 = VDDQ*0.5
  caodt  : CA ODT (MR11 OP[6:4]). 원본은 cfg->ca_odt 를 켜는 곳이 없어 MR11 CA ODT = 0(끔) 으로 써짐 (orbit_init.c).
           LPDDR4X 모드에서는 ODT_CA 핀이 무시되고 MR11/MR22 로만 CA/CK/CS 종단이 정해짐 (데이터시트 8쪽, 279쪽).
           test_lpddr4.c 'Memory Feature - The following parameters can be modified for tests' 의 omc_cfg.ca_odt = 0.
           1~6 = 그 줄을 1 로 바꾸고 LPDDR4_CA_ODT 를 그 값으로 (RZQ/1~6 = 240~40Ω)
  (refx/tsense/caodt 는 test_lpddr4.c 가 캠페인 --files 에 있을 때만 바꿀 수 있음)
  제약: pucal=0 (VDDQ*0.6) 은 SoC ODT RZQ/2, RZQ/4 일 때만 유효 (280쪽 Table 133, 279쪽 MR22) → valid() 에서 거름
"""
from __future__ import annotations

import re

SPHY = "base/drivers/orbit_ddram/sphy_v9r8_functions.c"
MAIN = "base/sys/main.c"
TEST = "base/drivers/orbit_ddram/test_lpddr4.c"
MR = "base/drivers/orbit_ddram/mr_config.h"

ADJ_BASE = ["test-0", "current", "test-1", "test-2", "test-3", "test-4", "test-5", "test-6"]
# 10/12 채널별/gate·read 따로: 소스 확인 결과 배열은 sphy_v9r8_lp4_dfs_gateadj[cfg->num_ch][dvfs_level]
# (test_lpddr4.c: omc_cfg.num_ch=0 → CH0, omc_cfg_1.num_ch=1 → CH1. 3732(t_ck 536) 은 dvfs_level 3).
# 태그 'gABrCD' = gate CH0=A CH1=B, read CH0=C CH1=D (네 DVFS 레벨 모두 같은 값, 기존 test-N 줄과 같은 방식).
# 값은 소스 후보 줄에 있는 0/1/9 만 씀. 값의 물리적 의미(단위/부호)는 PHY 문서가 없어 미확인.
# 기존 태그와 같은 것: test-0=g00r00 test-1=g10r10 test-2=g01r01 test-3=g11r11 test-4=g09r09 test-5=g90r90 test-6=g99r99
#   (current 는 3732 에서 test-4 와 같고 dvfs_level 0 의 read CH0 만 1)
ADJ_CH = ["g11r00", "g00r11", "g99r00", "g00r99", "g19r19", "g91r91", "g10r00", "g00r10", "g01r00", "g00r01"]
ADJ = ADJ_BASE + ADJ_CH
_SYN_RE = re.compile(r"^g([019])([019])r([019])([019])$")


def _syn(tag):
    """합성 adj 태그 → {"gateadj": (CH0, CH1), "readadj": (CH0, CH1)} (아니면 None)."""
    m = _SYN_RE.match(tag or "")
    if not m:
        return None
    a, b, c, d = (int(x) for x in m.groups())
    return {"gateadj": (a, b), "readadj": (c, d)}
# 10-02/06 결과: 0x26(60%) 은 두 번 모두 첫 부팅 DDR 초기화 실패(DDR RETRY FAIL 5/5) → 그 이상은 뺐다.
# 0x1d, 0x23 은 주석 표에 없는 중간값 (레지스터 범위 0x00~0x3F 안, 의미는 미확인).
# 10/12: 3일 결과에서 0x06(목록 최솟값)이 가장 좋아 아래쪽(0x02, 0x04)과 0x06~0x0d 사이(0x08, 0x0a)를 추가.
# 소스 주석상 범위 0x00~0x3F 안이지만 0x06 아래는 주석 % 표에도 없는 값 (의미 미확인).
# 10/12 PHY TRM 7.4.1 권장 초기값 0x1F(19.3%) 추가. TRM 에 나온 값: 0x1D=20.1%, 0x1F=19.3%, 0x37=36.9%
# → 소스 주석의 % 표(0x14=30% 등 선형)는 TRM 과 맞지 않음 (전체 코드표는 미확인)
DSVREF = [0x02, 0x04, 0x06, 0x08, 0x0a, 0x0d, 0x14, 0x1a, 0x1d, 0x1f, 0x20, 0x23]
CAVREF = [0x1a, 0x33, 0x4d, 0x66, 0x80, 0x99, 0xb3, 0xcc, 0xe6]
PDDS = [1, 2, 3, 4, 5, 6]
DQODT = [0, 1, 2, 3, 4, 5, 6]
SOCODT = [1, 2, 3, 4, 5, 6]
PHYODT = [1, 2, 3, 4, 5, 6]
VDDQ = [575, 600, 625, 650, 675, 700, 725]   # mV. 575~650 = 데이터시트 LPDDR4X 범위 안, 700 = 원본, 675/725 = 범위 밖(사용자 요청 10/08)
# 옛 캠페인 결과(ODT 를 안 바꾼 것)는 원본 값으로 본다 (mr_config.h 원본: DQ_ODT 4, SOC_ODT 4, PHY_ODT 4)
REFX = [1, 2]
TSENSE = [0, 1]
PUCAL = [0, 1]
CAODT = [0, 1, 2, 3, 4, 5, 6]
# 10/12 트레이닝 켜기 (test_lpddr4.c 'Training control - can be modified for tests' 블록, 원본 모두 0)
#   cbt   : omc_cfg.command_bus_training. CBT 코드는 #if !defined(DDR_OPTI_SZ) 안 → 빌드 인자 DDR_OPTI_SZ=0 필요
#   pvref : omc_cfg.phy_vref_trn_mode. PHY VREF 트레이닝도 #if !defined(DDR_OPTI_SZ) 안 → DDR_OPTI_SZ=0 필요
#   dvref : omc_cfg.dram_vref_trn_mode. DRAM VREF 트레이닝 (DDR_OPTI_SZ 와 무관). 1 이면 MR14 고정값을 안 씀 (orbit_init.c)
#   세 값 모두 0/1 만 씀 (1 이 '켜기' 라는 건 sphy_v9r8_functions.c 의 'if (... == 1)' 기준). 자동 이웃으로는 안 만듦 (--seed 로만)
CBT = PVREF = DVREF = [0, 1]
# 10/12 DRAM VREF (mr_config.h). LPDDR4X 매크로가 꺼져 있어 orbit_init.c 의 LPDDR4 경로로 MR12/MR14 를 씀.
#   mr14 : LPDDR4_VREF_DQ (MR14 OP[5:0]). write_odt=1(1865MHz) 이면 range 0 → 10% + 0.4%*code (Winbond 표 확인:
#          17=16.8%(원본), 23=19.2%(PHY TRM 7.4.1 권장, DQ ODT RZQ/4 일 때))
#   mr12 : LPDDR4_VREF_CA (MR12 OP[5:0]), range 1 (LPDDR4_VR_CA=1 고정) → 13=27.2%(원본, TRM: CA ODT RZQ/1 일 때),
#          29=33.6%(TRM: CA ODT off 일 때)
MR14 = [17, 20, 23, 26]
MR12 = [13, 29]
TRAIN = ("cbt", "pvref", "dvref")
EXPLICIT = TRAIN + ("mr14", "mr12")      # 자동 이웃으로는 안 만드는 손잡이 (--explore 에 적었을 때만)
DEFAULTS = {"dqodt": 4, "socodt": 4, "vddq": 700, "refx": 1, "tsense": 0, "pucal": 1, "caodt": 0,
            "cbt": 0, "pvref": 0, "dvref": 0, "mr14": 17, "mr12": 13}
# cavref 는 10-01~02 캠페인에서 패치해도 부팅 로그의 cbt_ca 가 항상 0x4d 로 찍혀(반영 안 됨) 자동 탐색에서 뺀다
KNOBS = {"dsvref": DSVREF, "pdds": PDDS, "adj": ADJ, "dqodt": DQODT, "socodt": SOCODT,
         "phyodt": PHYODT, "vddq": VDDQ, "refx": REFX, "tsense": TSENSE, "pucal": PUCAL,
         "caodt": CAODT, "cbt": CBT, "pvref": PVREF, "dvref": DVREF,
         "mr14": MR14, "mr12": MR12}  # 자동 탐색 순서
REQUIRED = ("dsvref", "pdds", "adj")       # 이게 없으면 설정을 모르는 결과
CAVREF_FIXED = 0x4d
ORDINAL = {"dsvref", "cavref", "pdds", "dqodt", "socodt", "phyodt", "vddq", "caodt", "mr14", "mr12"}                   # ±1칸 이웃이 의미 있는 것

_ADJ_RE = r"^(//)?(uint8_t sphy_v9r8_lp4_dfs_%s\s+\[2\]\[4\] = .*?;)(.*)$"
_DS_RE = re.compile(r"(SPHY_V9R8_DVFS[01]_DS[01]_VREF\s*,\s*)(0x[0-9a-fA-F]+)")
_CA_RE = re.compile(r"(SPHY_V9R8_CBT_CA_VREF,\s*)(0x[0-9a-fA-F]+)")
_PDDS_RE = re.compile(r"(#define LPDDR4_PDDS )(\d+)")
# 주석 처리 안 된 줄만 (원본에 같은 이름의 주석 줄이 있음)
_DQODT_RE = re.compile(r"^(#define LPDDR4_DQ_ODT )(\d+)", re.M)
_MR14_RE = re.compile(r"^(#define LPDDR4_VREF_DQ )(\d+)", re.M)
_MR12_RE = re.compile(r"^(#define LPDDR4_VREF_CA )(\d+)", re.M)
_SOCODT_RE = re.compile(r"^(#define LPDDR4_SOC_ODT )(\d+)", re.M)
_PHYODT_RE = re.compile(r"^(#define PHY_ODT )(\d+)", re.M)
_PUCAL_RE = re.compile(r"^(#define LPDDR4_PUCAL )(\d+)", re.M)
_REFAB_RE = re.compile(r"^(\s*omc_cfg\.t_refiab = )(\d+)(;)", re.M)
_REFPB_RE = re.compile(r"^(\s*omc_cfg\.t_refipb = )(\d+)(;)", re.M)
_TSENSE_RE = re.compile(r"^(\s*omc_cfg\.temp_sensing = )(\d+)(;)", re.M)
REFIAB_1X, REFIPB_1X = 3904000, 488000
_CAODT_RE = re.compile(r"^(#define LPDDR4_CA_ODT )(\d+)", re.M)
_CAEN_RE = re.compile(r"^(\s*omc_cfg\.ca_odt = )(\d+)(;)", re.M)
_TRAIN_RE = {"cbt": re.compile(r"^(\s*omc_cfg\.command_bus_training = )(\d+)(;)", re.M),
             "pvref": re.compile(r"^(\s*omc_cfg\.phy_vref_trn_mode = )(\d+)(;)", re.M),
             "dvref": re.compile(r"^(\s*omc_cfg\.dram_vref_trn_mode = )(\d+)(;)", re.M)}
_MCP_RE = re.compile(r"^(\s*)pmic_vcc_mcp_set\(VCC_MCP_(\d)_(\d{3})\);", re.M)


def _winbond_ddr_init(text: str):
    """main.c ddr_init() 안 #if defined(DDR_WINBOND) ~ #else 구간 (start, end) 문자 위치."""
    i = text.find("void ddr_init(void)")
    j = text.find("#if defined(DDR_WINBOND)", i)
    k = text.find("#else", j)
    if min(i, j, k) < 0:
        raise ValueError("main.c: ddr_init() 의 DDR_WINBOND 구간을 못 찾음")
    return j, k


def _vddq(text: str):
    a, b = _winbond_ddr_init(text)
    act = [m for m in _MCP_RE.finditer(text[a:b])]
    return int(act[0].group(2)) * 1000 + int(act[0].group(3)) if len(act) == 1 else None


def normalize(s: dict) -> dict:
    """ODT 값이 없는 옛 결과는 원본 값으로 채움. phyodt 가 없으면 socodt 와 같이 바꾸던 때 결과."""
    out = dict(s)
    for k, v in DEFAULTS.items():
        if out.get(k) is None:
            out[k] = v
    if out.get("phyodt") is None:
        out["phyodt"] = out["socodt"]
    return out


def complete(s: dict) -> bool:
    return all(s.get(k) is not None for k in REQUIRED)


def valid(s: dict) -> bool:
    """데이터시트상 같이 쓰면 안 되는 조합이면 False."""
    s = normalize(s)
    if s["pucal"] == 0 and s["socodt"] not in (2, 4):     # 280쪽 Table 133: VDDQ*0.6 은 SoC ODT 120/60Ω 만
        return False
    return True


def in_range(s: dict) -> bool:
    """모든 손잡이 값이 탐색 목록 안에 있나 (예: 0x26 처럼 뺀 값이면 False)."""
    s = normalize(s)
    return all(s.get(k) in v for k, v in KNOBS.items())


def _winbond_spans(lines: list[str]):
    """#if defined(DDR_WINBOND_DFS_ADJ) ~ #else/#endif 사이 줄 번호들."""
    inside = False
    for i, ln in enumerate(lines):
        s = ln.strip()
        if s.startswith("#if defined(DDR_WINBOND_DFS_ADJ)"):
            inside = True
            continue
        if inside and (s.startswith("#else") or s.startswith("#endif")):
            inside = False
        if inside:
            yield i


def _adj_tag(lines, which):
    rx = re.compile(_ADJ_RE % which)
    tags = []
    for ln in lines:
        m = rx.match(ln.rstrip("\r\n"))
        if m and not m.group(1):
            t = re.search(r"//\s*(\S+)\s*$", m.group(3) or "")
            tags.append(t.group(1) if t else "?")
    return tags[0] if len(tags) == 1 else "?"


def parse(src: dict) -> dict:
    """src: {상대경로: 내용} → {"adj","dsvref","cavref","pdds"} (읽을 수 없으면 None)."""
    sp = src[SPHY].splitlines(keepends=True)
    mr = src[MR].splitlines(keepends=True)
    g, r = _adj_tag(sp, "gateadj"), _adj_tag(sp, "readadj")
    out = {"adj": g if g == r else f"{g}/{r}"}
    ds = {int(m.group(2), 16) for i in _winbond_spans(sp) for m in [_DS_RE.search(sp[i])] if m}
    ca = {int(m.group(2), 16) for i in _winbond_spans(sp) for m in [_CA_RE.search(sp[i])] if m}
    pd = {int(m.group(2)) for i in _winbond_spans(mr) for m in [_PDDS_RE.search(mr[i])] if m}
    out["dsvref"] = ds.pop() if len(ds) == 1 else None
    out["cavref"] = ca.pop() if len(ca) == 1 else None
    out["pdds"] = pd.pop() if len(pd) == 1 else None
    m = src[MR]
    dq = _DQODT_RE.findall(m)
    so = _SOCODT_RE.findall(m)
    ph = _PHYODT_RE.findall(m)
    out["dqodt"] = int(dq[0][1]) if len(dq) == 1 else None
    out["socodt"] = int(so[0][1]) if len(so) == 1 else None
    out["phyodt"] = int(ph[0][1]) if len(ph) == 1 else None
    out["vddq"] = _vddq(src[MAIN]) if MAIN in src else None
    v14, v12 = _MR14_RE.findall(m), _MR12_RE.findall(m)
    out["mr14"] = int(v14[0][1]) if len(v14) == 1 else None
    out["mr12"] = int(v12[0][1]) if len(v12) == 1 else None
    pc = _PUCAL_RE.findall(m)
    out["pucal"] = int(pc[0][1]) if len(pc) == 1 else None
    out["refx"] = out["tsense"] = None
    if TEST in src:
        ab, ts = _REFAB_RE.findall(src[TEST]), _TSENSE_RE.findall(src[TEST])
        if len(ab) == 1 and int(ab[0][1]) and REFIAB_1X % int(ab[0][1]) == 0:
            out["refx"] = REFIAB_1X // int(ab[0][1])
        out["tsense"] = int(ts[0][1]) if len(ts) == 1 else None
        for k in TRAIN:
            f = _TRAIN_RE[k].findall(src[TEST])
            out[k] = int(f[0][1]) if len(f) == 1 else None
        en = _CAEN_RE.findall(src[TEST])
        ca = _CAODT_RE.findall(m)
        if len(en) == 1:
            out["caodt"] = (int(ca[0][1]) if len(ca) == 1 else None) if en[0][1] == "1" else 0
    return out


def build(orig: dict, s: dict) -> dict:
    """원본 내용에 설정 s 를 적용한 새 내용 {상대경로: 내용}. 원본 형식이 예상과 다르면 ValueError."""
    sp = orig[SPHY].splitlines(keepends=True)
    mr = orig[MR].splitlines(keepends=True)
    # adj: 태그 줄만 살리고 나머지 후보 줄은 주석
    syn = _syn(s["adj"])
    for which in ("gateadj", "readadj"):
        rx = re.compile(_ADJ_RE % which)
        hit = 0
        last = None
        for i, ln in enumerate(sp):
            body = ln.rstrip("\r\n")
            m = rx.match(body)
            if not m:
                continue
            last = i
            on = (not syn) and re.search(r"//\s*%s\s*$" % re.escape(s["adj"]), m.group(3) or "") is not None
            hit += on
            sp[i] = ("" if on else "//") + m.group(2) + m.group(3) + ln[len(body):]
        if syn:
            if last is None:
                raise ValueError(f"{which}: 후보 줄을 찾지 못함")
            a, b = syn[which]
            eol = sp[last][len(sp[last].rstrip("\r\n")):] or "\n"
            sp.insert(last + 1, f"uint8_t sphy_v9r8_lp4_dfs_{which}       [2][4] = "
                                f"{{{{0x{a}, 0x{a},0x{a},0x{a}}}, {{0x{b}, 0x{b},0x{b},0x{b}}}}};\t// {s['adj']}{eol}")
        elif hit != 1:
            raise ValueError(f"{which}: '// {s['adj']}' 후보 줄을 찾지 못함")
    for rx, key, lines, expect in ((_DS_RE, "dsvref", sp, 4), (_CA_RE, "cavref", sp, 1),
                                   (_PDDS_RE, "pdds", mr, 1)):
        n = 0
        val = f"0x{s[key]:02x}" if key != "pdds" else str(s[key])
        for i in _winbond_spans(lines):
            new = rx.sub(lambda m: m.group(1) + val, lines[i])
            if rx.search(lines[i]):
                n += 1
            lines[i] = new
        if n != expect:
            raise ValueError(f"{key}: 바꿀 줄 {n}개 (예상 {expect})")
    out_mr = "".join(mr)
    s = normalize(s)
    for rx, key in ((_DQODT_RE, "dqodt"), (_SOCODT_RE, "socodt"), (_PHYODT_RE, "phyodt")):
        out_mr, n = rx.subn(lambda m: m.group(1) + str(s[key]), out_mr)
        if n != 1:
            raise ValueError(f"{key}: 바꿀 줄 {n}개 (예상 1) - {rx.pattern}")
    for rx, key in ((_MR14_RE, "mr14"), (_MR12_RE, "mr12")):
        out_mr, n = rx.subn(lambda m, k=key: m.group(1) + str(s[k]), out_mr)
        if n != 1:
            raise ValueError(f"{key}: 바꿀 줄 {n}개 (예상 1) - {rx.pattern}")
    out_mr, n = _PUCAL_RE.subn(lambda m: m.group(1) + str(s["pucal"]), out_mr)
    if n != 1:
        raise ValueError(f"pucal: 바꿀 줄 {n}개 (예상 1)")
    out = {SPHY: "".join(sp), MR: out_mr}
    if TEST in orig:
        t = orig[TEST]
        for rx, val in ((_REFAB_RE, REFIAB_1X // s["refx"]), (_REFPB_RE, REFIPB_1X // s["refx"]),
                        (_TSENSE_RE, s["tsense"])) + tuple((_TRAIN_RE[k], s[k]) for k in TRAIN):
            t, n = rx.subn(lambda m, v=val: f"{m.group(1)}{v}{m.group(3)}", t)
            if n != 1:
                raise ValueError(f"test_lpddr4.c: 바꿀 줄 {n}개 (예상 1) - {rx.pattern}")
        t, n = _CAEN_RE.subn(lambda m: f"{m.group(1)}{1 if s['caodt'] else 0}{m.group(3)}", t)
        if n != 1:
            raise ValueError(f"caodt: test_lpddr4.c 의 omc_cfg.ca_odt 줄 {n}개 (예상 1)")
        if s["caodt"]:
            out[MR], n = _CAODT_RE.subn(lambda m: m.group(1) + str(s["caodt"]), out[MR])
            if n != 1:
                raise ValueError(f"caodt: LPDDR4_CA_ODT 줄 {n}개 (예상 1)")
        out[TEST] = t
    elif any(s[k] != DEFAULTS[k] for k in ("refx", "tsense", "caodt") + TRAIN):
        raise ValueError("refx/tsense/caodt/cbt/pvref/dvref 를 바꾸려면 캠페인 --files 에 test_lpddr4.c 가 있어야 함")
    if not valid(s):
        raise ValueError(f"데이터시트상 같이 못 쓰는 조합: {key(s)}")
    if MAIN in orig:
        t = orig[MAIN]
        a, b = _winbond_ddr_init(t)
        seg, n = _MCP_RE.subn(lambda m: f"{m.group(1)}pmic_vcc_mcp_set(VCC_MCP_{s['vddq'] // 1000}_{s['vddq'] % 1000:03d});",
                              t[a:b])
        if n != 1:
            raise ValueError(f"vddq: main.c DDR_WINBOND 구간의 활성 pmic_vcc_mcp_set 줄 {n}개 (예상 1)")
        out[MAIN] = t[:a] + seg + t[b:]
    elif s["vddq"] != DEFAULTS["vddq"]:
        raise ValueError("vddq 를 바꾸려면 캠페인 --files 에 base/sys/main.c 가 있어야 함")
    return out


def key(s: dict) -> str:
    # cavref 는 key 에 남겨 둔다 (예전 결과와 구분). 새 후보는 항상 CAVREF_FIXED
    s = normalize(s)
    return (f"adj={s['adj']} ds=0x{s['dsvref']:02x} ca=0x{s['cavref']:02x} pdds={s['pdds']}"
            f" dqodt={s['dqodt']} socodt={s['socodt']}"
            + (f" phyodt={s['phyodt']}" if s["phyodt"] != s["socodt"] else "")
            + (f" vddq={s['vddq']}" if s["vddq"] != DEFAULTS["vddq"] else "")
            + "".join(f" {k}={s[k]}" for k in ("refx", "tsense", "pucal", "caodt") + EXPLICIT if s[k] != DEFAULTS[k]))


def label(s: dict) -> str:
    s = normalize(s)
    out = f"ds{s['dsvref']:02x}_pd{s['pdds']}_{s['adj'].replace('-', '')}"
    if (s["dqodt"], s["socodt"]) != (DEFAULTS["dqodt"], DEFAULTS["socodt"]):
        out += f"_dq{s['dqodt']}_so{s['socodt']}"
    if s["phyodt"] != s["socodt"]:
        out += f"_ph{s['phyodt']}"
    if s["vddq"] != DEFAULTS["vddq"]:
        out += f"_vq{s['vddq']}"
    for k, tag in (("refx", "rf"), ("tsense", "ts"), ("pucal", "pc"), ("caodt", "ca"),
                   ("cbt", "cbt"), ("pvref", "pvt"), ("dvref", "dvt"), ("mr14", "m14_"), ("mr12", "m12_")):
        if s[k] != DEFAULTS[k]:
            out += f"_{tag}{s[k]}"
    return out


def neighbors(s: dict, explore=None, skip=None) -> list[dict]:
    """한 손잡이만 바꾼 이웃. 순서 있는 값은 ±1칸, adj 는 다른 태그 전부.
    explore: 바꿀 손잡이 이름 목록 (None 이면 전부). 목록 밖 값이 들어 있는 설정은 이웃을 안 만듦."""
    out = []
    s = normalize({**s, "cavref": CAVREF_FIXED})   # 효과 없는 손잡이는 기준값으로 되돌려서 시험
    if not in_range(s):
        return out
    for k, vals in KNOBS.items():
        if explore is not None and k not in explore:
            continue
        if explore is None and k in EXPLICIT:   # 트레이닝 켜기 / DRAM VREF 는 명시할 때만
            continue
        i = vals.index(s[k])
        cand = [i - 1, i + 1] if k in ORDINAL else [j for j in range(len(vals)) if j != i]
        if k == "adj":      # 합성 태그(ADJ_CH)는 자동 이웃으로 만들지 않음 (--seed 로만)
            cand = [j for j in cand if vals[j] in ADJ_BASE]
        for j in cand:
            if 0 <= j < len(vals) and vals[j] not in (skip or {}).get(k, ()) and valid({**s, k: vals[j]}):
                out.append({**s, k: vals[j]})
    return out


def adj_values(src: dict, tag: str) -> dict:
    """후보 줄 '// tag' 의 gate/read 배열 값 → {"gate": [[..4],[..4]], "read": [...]} (부팅 로그와 비교용)."""
    out = {}
    syn = _syn(tag)
    if syn:
        return {"gate": [[syn["gateadj"][0]] * 4, [syn["gateadj"][1]] * 4],
                "read": [[syn["readadj"][0]] * 4, [syn["readadj"][1]] * 4]}
    for which, k in (("gateadj", "gate"), ("readadj", "read")):
        rx = re.compile(_ADJ_RE % which)
        for ln in src[SPHY].splitlines():
            m = rx.match(ln)
            if m and re.search(r"//\s*%s\s*$" % re.escape(tag), m.group(3) or ""):
                nums = [int(x, 16) for x in re.findall(r"0x[0-9a-fA-F]+", m.group(2))]
                out[k] = [nums[0:4], nums[4:8]]
                break
    return out


def expected_print(src: dict, s: dict) -> dict:
    """설정 s 로 빌드한 SBL 이 부팅 때 찍어야 할 값 (ddr_sphy_config() 출력 형식)."""
    e = {"pdds": s["pdds"], "cbt_ca": s["cavref"], "dvfs0_ds": [s["dsvref"]] * 2, "dvfs1_ds": [s["dsvref"]] * 2}
    e.update(adj_values(src, s["adj"]))
    return e
