#!/usr/bin/env python3
"""gbt - gct-board-tester 명령 모음 (bash gbt.sh <명령>)

  status              보드 전체 현황 (rig.json + 사용 중인지)
  discover [옵션]     장비 자동 구분 (tools/discover_rig.py)
  probe <Tapo IP>     그 Tapo 만 껐다 켜고 모든 UART 출력 보기
  build <이미지...>   이미지 빌드 (config/images.json: sbl, uboot ...)
  flash <보드> <이미지...> [--no-build]   빌드 → fastboot 기록 → 부팅 로그로 버전 확인
  commit "<메시지>"    정해진 폴더(gbt/ config/ patches/ docs/ tools/ reports/ gbt.sh .gitignore)만 커밋하고 push
  help                기능 목록 (docs/FEATURES.md)
"""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
from gbt import rig  # noqa: E402


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


# commit 에 넣는 것 (정해진 것만 - sdram/ 같은 정리 전 폴더나 logs/, rig.json 은 안 넣음)
COMMIT_PATHS = ["gbt.sh", ".gitignore", "gbt", "config", "patches", "docs", "tools", "reports"]
OLD_FILES = ["rig.py", "tools/rig_tapo.txt"]   # 예전 위치 (지금은 gbt/rig.py, config/rig_tapo.txt)
TRAILER = ("\n\nCo-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>\n"
           "Claude-Session: https://claude.ai/code/session_01F8w4KvCWcHsrNvMpmwMMbA")


def git(*args, check=True):
    r = subprocess.run(["git", *args], text=True, capture_output=True)
    if check and r.returncode != 0:
        print(r.stdout + r.stderr)
        sys.exit(f"git {' '.join(args)} 실패")
    return r


def cmd_commit(msg):
    if not msg.strip():
        sys.exit('사용: bash gbt.sh commit "<무엇을 왜 바꿨는지>"')
    for f in OLD_FILES:
        if os.path.exists(f):
            os.remove(f)
        git("rm", "-q", "--cached", "--ignore-unmatch", f)
    git("add", "-A", "--", *[p for p in COMMIT_PATHS if os.path.exists(p)])
    staged = git("diff", "--cached", "--name-status").stdout.strip()
    if not staged:
        print("커밋할 변경 없음")
    else:
        print("커밋할 파일:")
        print("\n".join("  " + ln for ln in staged.splitlines()))
        git("commit", "-q", "-m", msg.strip() + TRAILER)
        print(git("log", "-1", "--format=커밋 %h %s").stdout.strip())
    other = git("status", "--short", "--", ".", *[f":(exclude){p}" for p in COMMIT_PATHS]).stdout.strip()
    if other:
        print("(이번에 안 넣은 변경 - 정리 전 폴더 등):")
        print("\n".join("  " + ln for ln in other.splitlines()[:15]) + ("\n  ..." if len(other.splitlines()) > 15 else ""))
    ahead = git("rev-list", "--count", "@{u}..HEAD", check=False).stdout.strip() or "?"
    print(f"push (올라갈 커밋 {ahead}개) ...")
    r = git("push", "origin", "HEAD", check=False)
    if r.returncode != 0 and ("rejected" in r.stderr or "fetch first" in r.stderr):
        print("GitHub 에 새 커밋이 있어서 pull --rebase 후 다시 push")
        git("pull", "--rebase", "-q", "origin", "main")
        r = git("push", "origin", "HEAD", check=False)
    if r.returncode != 0:
        print(r.stdout + r.stderr)
        sys.exit("push 실패 (인증이면: 토큰을 Password 에, git config --global credential.helper store)")
    print("push 완료:", git("log", "-1", "--format=%h %s").stdout.strip())


def main():
    a = sys.argv[1:] or ["help"]
    py = sys.executable
    if a[0] == "status":
        cmd_status()
    elif a[0] == "discover":
        sys.exit(subprocess.call([py, "tools/discover_rig.py", *a[1:]]))
    elif a[0] == "commit":
        cmd_commit(" ".join(a[1:]))
    elif a[0] in ("build", "flash"):
        sys.exit(subprocess.call([py, "tools/build_flash.py", *a]))
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
