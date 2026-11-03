#!/usr/bin/env python3
"""gbt - gct-board-tester 명령 모음 (bash gbt.sh <명령>)

  status              보드 전체 현황 (rig.json + 사용 중인지)
  discover [옵션]     장비 자동 구분 (tools/discover_rig.py)
  probe <Tapo IP>     그 Tapo 만 껐다 켜고 모든 UART 출력 보기
  help                기능 목록 (docs/FEATURES.md)
"""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
import rig  # noqa: E402


def short(p):
    return (p or "-").replace("/dev/serial/by-path/pci-0000:00:14.0-", "").replace("-port0", "")


def cmd_status():
    data = rig.load()
    rows = rig.status()
    print(f"rig.json: {data.get('created', '(없음 - bash gbt.sh discover 로 먼저 찾기)')}")
    print(f"{'보드':<6} {'종류(추정)':<12} {'Tapo':<15} {'UART':<16} {'fastboot':<11} {'PBL':<10} 상태")
    for name, b, busy in rows:
        st = "비어 있음" if not busy else "사용 중 - " + rig.describe(busy)
        pbl = (b.get("pbl") or "?").split(" ")[0]
        print(f"{name:<6} {b.get('type_guess', '?'):<12} {str(b.get('tapo')):<15} {short(b.get('uart')):<16} "
              f"{str(b.get('fastboot') or '-'):<11} {pbl:<10} {st}")
    um = data.get("unmatched", {})
    for k, v in (um.get("tapo") or {}).items():
        print(f"  (짝 없음) Tapo {k}: {v}")
    for k, v in (um.get("uart") or {}).items():
        print(f"  (짝 없음) UART {short(k)}: {v}")


def main():
    a = sys.argv[1:] or ["help"]
    py = sys.executable
    if a[0] == "status":
        cmd_status()
    elif a[0] == "discover":
        sys.exit(subprocess.call([py, "tools/discover_rig.py", *a[1:]]))
    elif a[0] == "probe" and len(a) == 2:
        sys.exit(subprocess.call([py, "tools/discover_rig.py", "--probe", a[1]]))
    else:
        print(__doc__)
        try:
            print(open("docs/FEATURES.md", encoding="utf-8").read())
        except OSError:
            pass


if __name__ == "__main__":
    main()
