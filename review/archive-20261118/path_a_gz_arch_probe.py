"""Read-only: GZ arch + migrate env presence check."""
from pathlib import Path
import json

ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "review/fleet-qos-readonly-audit-20261117.py"
ns = {"__file__": str(HELPER)}
prefix, sep, _ = HELPER.read_text(encoding="utf-8-sig").partition("\ntry:\n")
exec(compile(prefix, "<readonly-helper>", "exec"), ns)
connect, run = ns["connect"], ns["run"]

gz = connect("134.175.151.213", 28722, "ubuntu", ns["DEV_PW"])
try:
    out = run(gz, """uname -m
systemctl cat gz-api | grep -E 'Environment=|ExecStart' | sed 's/=[A-Za-z0-9+/_:-]\\{8,\\}/=<redacted>/' | head -20
ls -la /opt/mgmt/server
ps -o pid,lstart,cmd -C server --no-headers | head -3
ls /opt/mgmt/
""")
    print(out["stdout"])
    if out["stderr"]:
        print("STDERR:", out["stderr"][:300])
finally:
    gz.close()
