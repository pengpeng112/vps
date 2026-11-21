#!/bin/sh
# stdin: payload json; mode: <fixdir>/fx-mode = ok|fail|timeout|badjson|video_ok_download_fail
PAYLOAD=$(cat)
D=$(dirname "$0")
echo "$PAYLOAD" >> "$D/fx-calls.log"
python3 - "$PAYLOAD" "$(cat "$D/fx-mode" 2>/dev/null || echo ok)" <<'PY'
import json, sys
p = json.loads(sys.argv[1]); mode = sys.argv[2]
cls, op = p.get("cls", "?"), p.get("op", "?")
def ok(job):
    print(json.dumps({"ok": True, "job_id": job, "effective": True, "evidence": "fake-executor"})); sys.exit(0)
if op == "release":
    ok("released")
if mode == "fail":
    print(json.dumps({"ok": False})); sys.exit(1)
if mode == "timeout":
    import time; time.sleep(6)
if mode == "badjson":
    print("this is not json"); sys.exit(0)
if mode == "video_ok_download_fail" and cls == "download":
    print(json.dumps({"ok": False})); sys.exit(1)
ok("job-%s-1" % cls)
PY
