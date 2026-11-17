"""Fleet game-DNS P0 rollout driver (manual 4.3 order).
Per device: precheck(DB) -> check -> backup+sha -> apply -> supplemental acceptance.
Serial; any failure stops the batch. R9 = check only. Athena uses its own password."""
import os
import sys
import time
from pathlib import Path

import paramiko

PW = os.environ.get("DEVPW")
if not PW:
    sys.exit("DEVPW not set")
ATH_PW = os.environ.get("ATH_PW", "[REDACTED]")

script_text = Path(r"F:\vps\review\fleet-game-dns-reference-20261116.sh").read_text(encoding="utf-8")
bk_root = Path(r"F:\vps\backups\game-dns-20261116")
bk_root.mkdir(parents=True, exist_ok=True)

# order per manual 4.3
DEVICES = [
    ("R3", "rtr-38aa54", 13227, "SN-R3-AX3000安丘网警01", PW),
    ("R4", "rtr-cd473f", 13224, "SN-R4-AX3000 坊子网警", PW),
    ("R5", "rtr-c46a25", 13225, "SN-R5-AX3000 昌乐网警", PW),
    ("R6", "rtr-673568", 13226, "SN-R6-AX3000日照国保2已付", PW),
    ("R8", "rtr-1f6f0f", 13230, "SN-R8-AX3000 安丘2", PW),
    ("R1", "rtr-d3847d", 13222, "SN-R1-AX3000 临朐网警", PW),
    ("GL", "rtr-e9ccdd", 13234, "GL-MT3600BE", PW),
    ("雅典娜", "rtr-5e1a8d", 13228, "雅典娜-AX6600", ATH_PW),
]

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


def run(g, cmd, t=40):
    _, o, e = g.exec_command(cmd, timeout=t)
    return o.read().decode("utf-8", "replace").strip(), e.read().decode("utf-8", "replace").strip()


results = []
for name, dev_id, port, db_name, dev_pw in DEVICES:
    print(f"@@@@@@ {name} ({dev_id})")
    g = gz_conn()
    try:
        # 1) precheck: heartbeat age + pending tasks
        out, _ = run(g,
            f"sudo -u postgres psql routermgmt -t -A -F'|' -c \"BEGIN READ ONLY;"
            f"SELECT round(extract(epoch FROM now()-last_heartbeat_at)) FROM devices WHERE id='{dev_id}';"
            f"SELECT count(*) FROM tasks WHERE device_id='{dev_id}' AND status IN ('pending','running');"
            f"COMMIT;\" 2>&1", t=30)
        lines = [l for l in out.splitlines() if l.strip() and l.strip() not in ("BEGIN", "COMMIT")]
        hb_age, pending = (lines + ["?", "?"])[:2] if len(lines) >= 2 else ("?", "?")
        print(f"precheck: hb_age={hb_age}s pending={pending}")
        if not hb_age.isdigit() or int(hb_age) > 90 or pending != "0":
            print(f"PRECHECK_FAIL — SKIP {name}, stop batch")
            results.append((name, "PRECHECK_FAIL"))
            break

        # 2) device connection
        chan = g.get_transport().open_channel("direct-tcpip", ("127.0.0.1", port), ("127.0.0.1", 0))
        d = paramiko.SSHClient()
        d.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        d.connect("127.0.0.1", port=port, username="root", password=dev_pw, sock=chan,
                  timeout=20, look_for_keys=False, allow_agent=False)

        # 3) check
        stdin, stdout, stderr = d.exec_command(f"sh -s -- {dev_id} check", timeout=120)
        stdin.channel.sendall(script_text.encode("utf-8"))
        stdin.channel.shutdown_write()
        chk = stdout.read().decode("utf-8", "replace")
        chk_rc = stdout.channel.recv_exit_status()
        first_line = next((l for l in chk.splitlines() if l.startswith("DEVICE=")), "")
        print(chk.strip().splitlines()[-1] if chk.strip() else "no output", f"(rc={chk_rc})")
        if chk_rc != 0 or "DHCP_SHA=" not in first_line:
            print(f"CHECK_FAIL — SKIP {name}, stop batch")
            results.append((name, "CHECK_FAIL"))
            d.close(); g.close(); break
        sha = first_line.split("DHCP_SHA=")[1].split()[0]
        missing = int(first_line.split("MISSING_RULES=")[1].split()[0])
        if missing == 0 and "CHECK_REAL_DNS_OK" in chk:
            print(f"{name}: already OK, no apply needed")
            results.append((name, "ALREADY_OK"))
            d.close(); g.close(); continue

        # 4) backup DHCP to PC
        out, err = run(g,
            f"SSHPASS='{dev_pw}' sshpass -e scp -O -P {port} -o StrictHostKeyChecking=yes "
            f"root@127.0.0.1:/etc/config/dhcp /tmp/{dev_id}-dhcp-before && "
            f"sha256sum /tmp/{dev_id}-dhcp-before", t=45)
        sha_gz = out.split()[0] if out else ""
        if sha_gz != sha:
            print(f"BACKUP_SHA_MISMATCH gz={sha_gz} — SKIP {name}, stop batch")
            results.append((name, "BACKUP_MISMATCH"))
            d.close(); g.close(); break
        sftp = g.open_sftp()
        sftp.get(f"/tmp/{dev_id}-dhcp-before", str(bk_root / f"{dev_id}-dhcp.before"))
        sftp.close()
        import hashlib
        local_sha = hashlib.sha256((bk_root / f"{dev_id}-dhcp.before").read_bytes()).hexdigest()
        if local_sha != sha:
            print(f"LOCAL_SHA_MISMATCH — SKIP {name}, stop batch")
            results.append((name, "LOCAL_SHA_MISMATCH"))
            d.close(); g.close(); break
        print(f"backup OK (PC {bk_root / (dev_id + '-dhcp.before')})")

        # 5) apply
        stdin, stdout, stderr = d.exec_command(f"sh -s -- {dev_id} apply {sha}", timeout=150)
        stdin.channel.sendall(script_text.encode("utf-8"))
        stdin.channel.shutdown_write()
        ap = stdout.read().decode("utf-8", "replace")
        ap_rc = stdout.channel.recv_exit_status()
        ap_tail = [l for l in ap.splitlines() if l.startswith(("APPLIED", "ALREADY", "ERROR", "ROLLED_BACK"))]
        print("apply:", ap_tail[-1] if ap_tail else f"no-marker rc={ap_rc}")
        if ap_rc != 0 or not any(l.startswith(("APPLIED", "ALREADY_APPLIED_NO_RELOAD")) for l in ap_tail):
            print(f"APPLY_FAIL — SKIP {name}, stop batch (device backup kept on device)")
            results.append((name, "APPLY_FAIL"))
            d.close(); g.close(); break

        # 6) supplemental acceptance
        stdin, stdout, stderr = d.exec_command("sh -s", timeout=90)
        stdin.channel.sendall(ACC.encode())
        stdin.channel.shutdown_write()
        acc = stdout.read().decode("utf-8", "replace")
        fake_leak = "198.18." in acc.split("BAIDU")[0]
        baidu_fake = "198.18." in acc.split("BAIDU")[1] if "BAIDU" in acc else False
        ok = (not fake_leak) and baidu_fake
        print(acc.strip())
        results.append((name, "OK" if ok else "ACC_REVIEW"))
        d.close()
    except Exception as exc:
        print("FAIL:", exc)
        results.append((name, f"EXC:{exc}"))
        try:
            g.close()
        except Exception:
            pass
        break
    try:
        g.close()
    except Exception:
        pass
    sys.stdout.flush()
    time.sleep(3)

print("@@@@@@ R9 check-only")
try:
    g = gz_conn()
    chan = g.get_transport().open_channel("direct-tcpip", ("127.0.0.1", 13231), ("127.0.0.1", 0))
    d = paramiko.SSHClient()
    d.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    d.connect("127.0.0.1", port=13231, username="root", password=PW, sock=chan,
              timeout=15, look_for_keys=False, allow_agent=False)
    stdin, stdout, stderr = d.exec_command("sh -s -- rtr-101be0 check", timeout=120)
    stdin.channel.sendall(script_text.encode("utf-8"))
    stdin.channel.shutdown_write()
    print(stdout.read().decode("utf-8", "replace").strip())
    d.close(); g.close()
except Exception as exc:
    print("R9 FAIL:", exc)

print("@@@@@@ SUMMARY")
for r in results:
    print(r)
print("ROLLOUT_DONE")
