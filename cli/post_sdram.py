"""
sdram-sbl-cycle 결과 처리기: SBL 이 부팅 때 찍는 DDR 설정 요약(ddr_sphy_config())을 뽑는다.
  CFG=1 ch=2 PDDS=1 / gate-adj: {..}{..} / read-adj: {..}{..} / cbt_ca=0x4d / dvfs0-ds=0x14,0x14 / dvfs1-ds=..
→ 패치한 값이 실제로 보드에 들어갔는지 확인하는 데 쓴다 (sdram_campaign.py).
부팅마다 같은 값이어야 하므로, 첫 값과 다른 부팅이 있으면 'inconsistent' 에 남긴다.
"""
import re

PATS = {
    "pdds": re.compile(r"CFG=\d+(?: ch=\d+)? PDDS=(\d+)"),
    "gate": re.compile(r"gate-adj: \{([\d, ]+)\}\{([\d, ]+)\}"),
    "read": re.compile(r"read-adj: \{([\d, ]+)\}\{([\d, ]+)\}"),
    "cbt_ca": re.compile(r"cbt_ca=0x([0-9a-fA-F]+)"),
    "dvfs0_ds": re.compile(r"dvfs0-ds=0x([0-9a-fA-F]+),0x([0-9a-fA-F]+)"),
    "dvfs1_ds": re.compile(r"dvfs1-ds=0x([0-9a-fA-F]+),0x([0-9a-fA-F]+)"),
}


def _val(k, m):
    if k == "pdds":
        return int(m.group(1))
    if k in ("gate", "read"):
        return [[int(x) for x in m.group(1).split(",")], [int(x) for x in m.group(2).split(",")]]
    if k == "cbt_ca":
        return int(m.group(1), 16)
    return [int(m.group(1), 16), int(m.group(2), 16)]


def process(lines, result, base):  # noqa: ARG001
    first, seen, bad = {}, {k: 0 for k in PATS}, set()
    for ln in lines:
        for k, rx in PATS.items():
            m = rx.search(ln)
            if m:
                v = _val(k, m)
                seen[k] += 1
                if k not in first:
                    first[k] = v
                elif v != first[k]:
                    bad.add(k)
    ddr = dict(first)
    s = (f"DDR 설정 (부팅 로그): PDDS={ddr.get('pdds')} gate={ddr.get('gate')} read={ddr.get('read')} "
         f"cbt_ca={hex(ddr['cbt_ca']) if 'cbt_ca' in ddr else None} "
         f"ds0={[hex(x) for x in ddr.get('dvfs0_ds', [])]} ds1={[hex(x) for x in ddr.get('dvfs1_ds', [])]}"
         f"  (부팅 {seen['pdds']}회 출력{', 부팅마다 다름: ' + ','.join(sorted(bad)) if bad else ''})")
    return {"ddr": ddr, "boots_seen": seen["pdds"], "inconsistent": sorted(bad), "summary_lines": [s]}
