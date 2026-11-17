#!/bin/bash
SCRIPT_DIR=$(/usr/bin/python3 -c 'import os,sys;print(os.path.dirname(os.path.realpath(sys.argv[1])))' "$0")
cd "$SCRIPT_DIR" || exit 1
if ! /usr/bin/curl -fsS --max-time 1 http://127.0.0.1:8765/ >/dev/null 2>&1; then
  /usr/bin/python3 server.py &
  sleep 1
fi
/usr/bin/open http://127.0.0.1:8765/
