"""03-b step1: upload candidate binary, create scratch DB, restore dump (isolated)."""
from pathlib import Path
import json
import time

ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "review/fleet-qos-readonly-audit-20261117.py"
ns = {"__file__": str(HELPER)}
prefix, sep, _ = HELPER.read_text(encoding="utf-8-sig").partition("\ntry:\n")
exec(compile(prefix, "<readonly-helper>", "exec"), ns)
connect, run = ns["connect"], ns["run"]

BIN_LOCAL = ROOT / "backups/builds/gz-api-20261118-204026/server"
DUMP = "/tmp/cf2d-backup-1403979/routermgmt.dump"

gz = connect("134.175.151.213", 28722, "ubuntu", ns["DEV_PW"])
try:
    # 1) upload binary via SFTP
    sftp = gz.open_sftp()
    sftp.put(str(BIN_LOCAL), "/tmp/server-cf2d-candidate")
    sftp.close()
    st = run(gz, "chmod 755 /tmp/server-cf2d-candidate && sha256sum /tmp/server-cf2d-candidate")
    print("UPLOAD:", st["stdout"], st["stderr"][:200])
    assert "1d0d88299ea25ba6964bcc32f4f1b01a9684282a2fb9ba5820aca03ef7a01a05" in st["stdout"], "SHA mismatch!"

    # 2) scratch DB + restore
    r = run(gz, f"""set -e
sudo -u postgres dropdb --if-exists cf2d_rehearsal
sudo -u postgres createdb cf2d_rehearsal
time sudo -u postgres pg_restore -d cf2d_rehearsal {DUMP} 2>/tmp/restore-err.txt || true
tail -3 /tmp/restore-err.txt
sudo -u postgres psql cf2d_rehearsal -X -t -A -c "select 'schema',max(version) from schema_migrations" -c "select 'nodes',count(*) from nodes" -c "select 'devices',count(*) from devices" -c "select 'assign',count(*) from node_assignments"
""", timeout=300)
    print("RESTORE:", r["stdout"])
    if r["stderr"]:
        print("STDERR:", r["stderr"][:300])
finally:
    gz.close()
