#!/bin/bash
# gct-board-tester 명령 모음:  bash gbt.sh status | discover | probe <Tapo IP> | help
cd "$(dirname "$0")" || exit 1
PY=${PY:-$HOME/.venv-gbt/bin/python}; [ -x "$PY" ] || PY=python3
exec "$PY" tools/gbt.py "$@"
