"""GL and Athena game-DNS: SFTP-based backup (dropbear sftp-server), then apply + acceptance."""
import hashlib
import os
import sys
from pathlib import Path

import paramiko

PW = os.environ.get("DEVPW")
ATH_PW = os.environ.get("ATH_PW", "[REDACTED]")
script_text = Path(r"F:\vps\review\fleet-game-dns-reference-20261116.sh").read_text(encoding="utf-8")
bk_root = Path(r"F:\vps\backups\game-dns-20261116")
bk_root.mkdir(parents=True, exist_ok=True)

ACC = r"""
LANIP=$(ip -4 addr show br-lan 2>/dev/null | grep -o 'inet [0-9.]*' | head -1 | cut -d' ' -f2)
echo "lan_ip=$LANIP"
for h in gpp-tj100-1.lol.qq.com tqos.gamesafe.qq.com tqos.anticheatexpert.com ied-tqos-tgp.qq.com ied-tqos.qq.com; do
  a=$(nslookup $h $LANIP 2>/dev/null | grep -oE '198\.18\.[0-9]+\.[0-9]+|[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+' | grep -v "^$LANIP" | head -2 | tr '\n' ' ')
  echo "GAME $h -> ${a:-NO_ANSWER}"
done
b=$(nslookup www.baidu.com $LANIP 2>/dev/null | grep -oE '198\.18\.[0-9]+\.[0-9]+|[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+' | grep -v "^$LANIP" | head -1)
echo "BAIDU -> ${b:-NO_ANSWER}"
echo "uci_pending=$(uci changes dhcp | wc -l)"
"""


def gz_conn():
    g = paramiko.SSHClient()
    g.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    g.connect("134.175.151.213", port=28722, username="ubuntu", password=PW,
              timeout=20, look_for_keys=False, allow_agent=False)
    return g


TARGETS = [
    ("GL", "rtr-e9ccdd", 13234, PW),
    ("雅典娜", "rtr-5e1a8d", 13228, ATH_PW),
]

for name, dev_id, port, dev_pw in TARGETS:
    print(f"@@@@@@ {name} ({dev_id})")
    g = gz_conn()
    try:
        # precheck
        out, _ = run_gz = None, None
        _, o, _ = g.exec_command(
            f"sudo -u postgres psql routermgmt -t -A -F'|' -c \"BEGIN READ ONLY;"
            f"SELECT round(extract(epoch FROM now()-last_heartbeat_at)) FROM devices WHERE id='{dev_id}';"
            f"SELECT count(*) FROM tasks WHERE device_id='{dev_id}' AND status IN ('pending','running');"
            f"COMMIT;\" 2>&1", timeout=30)
        lines = [l for l in o.read().decode().splitlines() if l.strip() and l.strip() not in ("BEGIN", "COMMIT")]
        hb_age, pending = (lines + ["?", "?"])[:2]
        print(f"precheck: hb_age={hb_age}s pending={pending}")
        if not hb_age.isdigit() or int(hb_age) > 90 or pending != "0":
            print(f"PRECHECK_FAIL skip {name}"); g.close(); continue

        chan = g.get_transport().open_channel("direct-tcpip", ("127.0.0.1", port), ("127.0.0.1", 0))
        d = paramiko.SSHClient()
        d.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        d.connect("127.0.0.1", port=port, username="root", password=dev_pw, sock=chan,
                  timeout=20, look_for_keys=False, allow_agent=False)

        # check
        stdin, stdout, stderr = d.exec_command(f"sh -s -- {dev_id} check", timeout=120)
        stdin.channel.sendall(script_text.encode("utf-8"))
        stdin.channel.shutdown_write()
        chk = stdout.read().decode("utf-8", "replace")
        chk_rc = stdout.channel.recv_exit_status()
        first_line = next((l for l in chk.splitlines() if l.startswith("DEVICE=")), "")
        print("check:", chk.strip()[:400])
        if chk_rc != 0 or "DHCP_SHA=" not in first_line:
            print(f"CHECK_FAIL skip {name}"); d.close(); g.close(); continue
        sha = first_line.split("DHCP_SHA=")[1].split()[0]
        missing = int(first_line.split("MISSING_RULES=")[1].split()[0])
        if missing == 0:
            print(f"{name} already OK"); d.close(); g.close(); continue

        # backup via SFTP over the same channel
        try:
            t = d.get_transport()
            sftp = paramiko.SFTPClient.from_transport(t)
            sftp.get("/etc/config/dhcp", str(bk_root / f"{dev_id}-dhcp.before"))
            sftp.close()
        except Exception as sftp_exc:
            print(f"SFTP_FAIL {sftp_exc} — trying dropbear sftp-server path"); 
            d.close(); g.close(); continue
        local_sha = hashlib.sha256((bk_root / f"{dev_id}-dhcp.before").read_bytes()).hexdigest()
        print("backup sha:", local_sha, "match:", local_sha == sha)
        if local_sha != sha:
            print(f"BACKUP_SHA_MISMATCH skip {name}"); d.close(); g.close(); continue

        # apply
        stdin, stdout, stderr = d.exec_command(f"sh -s -- {dev_id} apply {sha}", timeout=150)
        stdin.channel.sendall(script_text.encode("utf-8"))
        stdin.channel.shutdown_write()
        ap = stdout.read().decode("utf-8", "replace")
        ap_rc = stdout.channel.recv_exit_status()
        tail = [l for l in ap.splitlines() if l.startswith(("APPLIED", "ALREADY", "ERROR", "ROLLED_BACK"))]
        print("apply:", tail[-1] if tail else f"rc={ap_rc}")
        if ap_rc != 0 or not any(l.startswith(("APPLIED", "ALREADY_APPLIED_NO_RELOAD")) for l in tail):
            print(f"APPLY_FAIL {name}"); d.close(); g.close(); continue

        # acceptance
        stdin, stdout, stderr = d.exec_command("sh -s", timeout=90)
        stdin.channel.sendall(ACC.encode())
        stdin.channel.shutdown_write()
        print(stdout.read().decode("utf-8", "replace").strip())
        d.close()
    except Exception as exc:
        print("FAIL:", exc)
    try:
        g.close()
    except Exception:
        pass
    sys.stdout.flush()

print("GL_ATHENA_DONE")
