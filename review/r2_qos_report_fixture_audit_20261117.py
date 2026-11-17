"""Local-only reproductions: copied scripts, fake tc/curl/iptables, fixture state.

Never executes deployed device controllers. The controller's fixed filesystem
paths and managed-device guard are redirected only in the disposable copy.
"""
from pathlib import Path
import datetime
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
GIT = Path(shutil.which("git")).resolve().parent.parent
BASH = GIT / "bin/bash.exe"
FIX = ROOT / "review/r2-qos-report-fixtures-20261117-v2"
FIX.mkdir(exist_ok=False)
BIN = FIX / "bin"
BIN.mkdir()
RESULT = {"local_only": True, "production_commands": False,
          "started_local": datetime.datetime.now().astimezone().isoformat(timespec="seconds")}


def write(path, text):
    path.write_bytes(text.encode("utf-8"))


calls = FIX / "mock-calls.log"
counter = FIX / "counter.json"
write(BIN / "tc", '#!/bin/sh\nprintf "tc %s\\n" "$*" >> "$MOCK_CALLS"\nexit 0\n')
write(BIN / "curl", '#!/bin/sh\nprintf "curl %s\\n" "$*" >> "$MOCK_CALLS"\nprintf 204\n')
write(BIN / "flock", "#!/bin/sh\nexit 0\n")
write(BIN / "python3", "#!/bin/sh\nexec " + shlex.quote(Path(sys.executable).as_posix()) + ' "$@"\n')
write(BIN / "iptables", """#!/bin/sh
v=$("$MOCK_PYTHON" -c "import json,os;d=json.load(open(os.environ['MOCK_COUNTER']));print(d['up' if os.environ['MOCK_DIRECTION']=='up' else 'down'])" 2>/dev/null)
case "$2" in
 PROXY_METER_OUT) v=$MOCK_UP; tag=ep_up ;;
 PROXY_METER_IN) v=$MOCK_DOWN; tag=ep_down ;;
 *) exit 1 ;;
esac
printf '1 %s /* %s */\\n' "$v" "$tag"
""")
env = os.environ.copy()
env.pop("RATE", None)
env["PATH"] = BIN.as_posix() + os.pathsep + env["PATH"]
env.update(MOCK_CALLS=calls.as_posix(), MOCK_COUNTER=counter.as_posix(),
           MOCK_PYTHON=Path(sys.executable).as_posix(), MOCK_DIRECTION="up")


def run(script, *args, extra=None):
    e = env.copy()
    if extra:
        e.update(extra)
    # PATH converted explicitly by bash; every side-effect command is a local fake.
    e["FIXTURE_BIN"] = BIN.as_posix()
    command = 'export PATH="$FIXTURE_BIN:$PATH"; exec bash "$@"'
    p = subprocess.run([str(BASH), "-c", command, "fixture", script.as_posix(), *map(str, args)],
                       cwd=FIX, env=e, capture_output=True, text=True, timeout=30)
    return {"rc": p.returncode, "stdout": p.stdout[-3000:], "stderr": p.stderr[-1500:]}


# Meter: only lock path is redirected, algorithm is unchanged.
meter_source = (ROOT / "logs/proxy-meter-v2.sh").read_text(encoding="utf-8")
meter = FIX / "meter-copy.sh"
write(meter, meter_source.replace("exec 9>/tmp/pm-v2.lock",
                                 "exec 9>" + shlex.quote((FIX / "meter.lock").as_posix())))
rules = FIX / "rules.conf"
write(rules, "ep_up|up|203.0.113.1|tcp|443\nep_down|down|203.0.113.1|tcp|443\n")
meter_env = {"PM_STATE": (FIX / "meter.json").as_posix(),
             "PM_PREV": (FIX / "meter.prev").as_posix(),
             "PM_DAY": (FIX / "daily.json").as_posix(),
             "PM_LOG": (FIX / "meter.log").as_posix(),
             "PM_RULES": rules.as_posix()}
meter_runs = []
for up, down in [(100, 200), (150, 280), (180, 350)]:
    write(counter, json.dumps({"up": up, "down": down}))
    meter_runs.append(run(meter, extra={**meter_env, "MOCK_UP": str(up), "MOCK_DOWN": str(down)}))
RESULT["meter"] = {"runs": meter_runs, "expected_deltas_after_baseline": [130, 100],
                   "daily": json.loads((FIX / "daily.json").read_text()),
                   "expected_prev_exists": (FIX / "meter.prev").exists(),
                   "wrong_curly_file_exists": (FIX / "{}").exists()}

# Controller: all paths + guard redirected, tc/curl are fakes.
ctl_source = (ROOT / "logs/ratelimit-ctl.sh").read_text(encoding="utf-8")
base = FIX / "controller"
base.mkdir()
agent = FIX / "agent.conf"
write(agent, "DEVICE_ID=fixture-device\n")
ctl_text = ctl_source.replace("BASE=/etc/routermgmt/ratelimit", "BASE=" + shlex.quote(base.as_posix()))
ctl_text = ctl_text.replace("DAILY=/etc/routermgmt/state/proxy-meter-daily.json",
                            "DAILY=" + shlex.quote((FIX / "controller-daily.json").as_posix()))
ctl_text = ctl_text.replace("[ -f /etc/routermgmt/agent.conf ]",
                            "[ -f " + shlex.quote(agent.as_posix()) + " ]")
# Shell functions with unique names avoid any inherited curl function or
# Windows executable lookup taking precedence over a PATH shim.
ctl_text = re.sub(r"(?<![A-Za-z0-9_])tc(?=\s)", "fixture_tc", ctl_text)
ctl_text = re.sub(r"(?<![A-Za-z0-9_])curl(?=\s)", "fixture_curl", ctl_text)
mock_functions = """
fixture_tc() { printf 'tc %s\\n' "$*" >> "$MOCK_CALLS"; return 0; }
fixture_curl() { printf 'curl %s\\n' "$*" >> "$MOCK_CALLS"; printf 204; return 0; }
"""
ctl_text = ctl_text.replace("set -u\n", "set -u\n" + mock_functions, 1)
ctl = FIX / "controller-copy.sh"
write(ctl, ctl_text)
write(FIX / "controller-daily.json", '{"total":0}\n')
policy = json.loads((ROOT / "logs/policy-r2-v1.json").read_text())
policy["device_id"] = "fixture-device"
observe = FIX / "observe.json"
write(observe, json.dumps(policy))
policy["enforcement"] = "limited"
limited = FIX / "limited.json"
write(limited, json.dumps(policy))

observe_result = run(ctl, "apply", observe.as_posix(), "--enforce", "--expected-version", "0")
RESULT["observe_apply"] = {"result": observe_result,
                          "version_after": (base / "policy.version").read_text().strip(),
                          "mock_calls": calls.read_text() if calls.exists() else ""}
write(calls, "")
emergency = run(ctl, "emergency-stop")
latch = json.loads((base / "latch.json").read_text())
after_emergency = run(ctl, "apply", limited.as_posix(), "--enforce",
                      "--expected-version", (base / "policy.version").read_text().strip())
RESULT["emergency_then_limited"] = {"emergency": emergency, "latch": latch,
                                    "apply_result": after_emergency,
                                    "daily_total": 0,
                                    "version_after": (base / "policy.version").read_text().strip(),
                                    "mock_calls": calls.read_text()}
RESULT["source_sha256"] = {f: hashlib.sha256((ROOT / f).read_bytes()).hexdigest()
                           for f in ["logs/proxy-meter-v2.sh", "logs/ratelimit-ctl.sh"]}
RESULT["finished_local"] = datetime.datetime.now().astimezone().isoformat(timespec="seconds")
out = ROOT / "review/R2v36报告隔离复现结果-v2-20261117.json"
assert not out.exists()
out.write_text(json.dumps(RESULT, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(json.dumps(RESULT, ensure_ascii=False, indent=2))
