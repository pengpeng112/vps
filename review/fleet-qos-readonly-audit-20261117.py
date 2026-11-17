"""Read-only fleet QoS audit. No API login, writes, module loads or proxy PUTs.

Controlled credentials are parsed in memory. Only selected, non-secret fields
are written locally. Never run the existing deployment/preflight drivers here.
"""
from pathlib import Path
import concurrent.futures
import datetime
import hashlib
import json
import re
import time

import paramiko
import yaml

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "review/fleet-qos-live-audit-20261117.json"
BT = chr(96)


def controlled_row(path, name):
    for line in (ROOT / path).read_text(encoding="utf-8-sig").splitlines():
        cells = [x.strip() for x in line.strip().strip("|").split("|")]
        if line.startswith("|") and cells[0] == name:
            return "|".join(cells[1:])
    raise ValueError("Controlled credential field missing")


def quoted(value):
    return re.findall(BT + "([^" + BT + "]+)" + BT, value)


DEV_PW = quoted(controlled_row("backups/SN-R系列凭证总览.md", "**root/SSH 口令**"))[0]
GIA_PW = quoted(controlled_row("backups/US666-CN2GIA-凭证档案-20261026.md", "SSH"))[-1]
BWG_PW = quoted(controlled_row("backups/搬瓦工东京-20260930.md", "临时 root 密码"))[0]
BWG_PORT = int(re.search(r"\d+", controlled_row("backups/搬瓦工东京-20260930.md", "SSH 端口")).group())


def connect(host, port, user, pw, jump=None):
    c = paramiko.SSHClient()
    c.load_system_host_keys()
    # Memory-only trust for hosts absent from the local known_hosts; never save.
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    sock = None
    if jump:
        sock = jump.get_transport().open_channel("direct-tcpip", (host, port), ("127.0.0.1", 0), timeout=12)
    try:
        c.connect(host, port=port, username=user, password=pw, sock=sock,
                  timeout=12, banner_timeout=12, auth_timeout=12,
                  look_for_keys=False, allow_agent=False)
    except Exception:
        c.close()
        if sock:
            sock.close()
        raise
    return c


def run(c, script, timeout=25):
    i, o, e = c.exec_command("sh -s", timeout=timeout)
    i.write(script)
    i.channel.shutdown_write()
    stdout = o.read().decode("utf-8", "replace")
    stderr = e.read().decode("utf-8", "replace")
    return {"rc": o.channel.recv_exit_status(), "stdout": stdout.strip(), "stderr": stderr[:500].strip()}


def get_json(c, command):
    r = run(c, command)
    if r["rc"] == 0:
        try:
            return json.loads(r["stdout"])
        except ValueError:
            pass
    return {"unavailable": True, "rc": r["rc"]}


result = {"started_local": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
          "production_configuration_writes": False, "api_login": False,
          "scope": ["GZ", "R2", "GIA", "BWG"], "hosts": {}}
connections = []


def save():
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


try:
    gz = connect("134.175.151.213", 28722, "ubuntu", DEV_PW)
    connections.append(gz)
    result["hosts"]["GZ"] = {"connected": True}
    result["hosts"]["GZ"]["services"] = run(gz, "date -Is\nsystemctl is-active gz-api nginx postgresql\ncurl --noproxy '*' -fsS -m 5 http://127.0.0.1:8080/api/v1/health\n")
    sql = """sudo -u postgres psql routermgmt -X -t -A -F '|' <<'SQL'
BEGIN READ ONLY;
SELECT id,name,current_config_version,desired_config_version,highest_accepted_version,
round(extract(epoch FROM now()-last_heartbeat_at)),vps_limit_kbps
FROM devices ORDER BY id;
SELECT a.device_id,n.id,n.name,n.host,n.port,n.proto,a.is_primary,a.failover_priority
FROM node_assignments a JOIN nodes n ON n.id=a.node_id
WHERE a.device_id='rtr-a9bcba' ORDER BY a.is_primary DESC,a.failover_priority,n.id;
SELECT id,device_id,type,status FROM tasks WHERE status IN ('pending','running') ORDER BY id;
COMMIT;
SQL
"""
    result["hosts"]["GZ"]["database_readonly"] = run(gz, sql)
    result["hosts"]["GZ"]["api_binary"] = run(gz, "sha256sum /opt/mgmt/server\n")
    save()
    print("GZ_READ_ONLY_COLLECTED", flush=True)

    r2 = connect("127.0.0.1", 13223, "root", DEV_PW, gz)
    connections.append(r2)
    r = {"connected": True}
    result["hosts"]["R2"] = r
    r["system"] = run(r2, """date '+%F %T %z'
uname -r
cat /proc/uptime
ip -4 route show default
ip rule show
tc -V
tc qdisc show
tc class show dev wan
tc filter show dev wan
df -h /overlay /tmp
""")
    r["module_files"] = run(r2, r"""find /lib/modules -type f \( -name '*ifb*' -o -name '*police*' -o -name '*mirred*' -o -name '*htb*' -o -name '*ingress*' \) -print 2>/dev/null
ls /sys/module/ifb /sys/module/sch_htb /sys/module/sch_ingress /sys/module/act_police /sys/module/act_mirred 2>/dev/null
opkg list-installed 2>/dev/null | awk '/^(tc|kmod-ifb|kmod-sched)/ {print}'
""")
    r["file_hashes"] = run(r2, """sha256sum /etc/routermgmt/proxy-meter.sh /etc/config/dhcp /etc/config/wireless /etc/mihomo/config.yaml /etc/routermgmt/tunnel.conf /etc/routermgmt/self-heal.sh 2>/dev/null
""")
    r["meter_cron_and_logs"] = run(r2, """crontab -l 2>/dev/null | awk '/proxy-meter/ {print}'
wc -l /etc/routermgmt/logs/proxy-meter.log 2>/dev/null
head -n 2 /etc/routermgmt/logs/proxy-meter.log 2>/dev/null
tail -n 30 /etc/routermgmt/logs/proxy-meter.log 2>/dev/null
cat /etc/routermgmt/state/proxy-meter.state 2>/dev/null
""")
    r["meter_rules_before"] = run(r2, """iptables -L PROXY_METER_OUT -v -x -n
iptables -L PROXY_METER_IN -v -x -n
""")
    groups = {}
    for name in ["PROXY", "AUTO", "NODE-PICK", "US-MEDIA", "DOWNLOAD"]:
        g = get_json(r2, "curl --noproxy '*' -fsS -m 5 http://127.0.0.1:9090/proxies/" + name)
        groups[name] = {k: g[k] for k in ["type", "now", "all", "unavailable", "rc"] if k in g}
    r["groups"] = groups
    # Parse secret-bearing YAML only in memory. Export endpoint/group metadata.
    raw_config = run(r2, "cat /etc/mihomo/config.yaml")
    config = yaml.safe_load(raw_config["stdout"]) or {}
    r["config_metadata"] = {
        "mode": config.get("mode"), "ipv6": config.get("ipv6"),
        "proxies": [{k: x.get(k) for k in ["name", "type", "server", "port"]} for x in config.get("proxies", [])],
        "groups": [{k: x.get(k) for k in ["name", "type", "proxies"]} for x in config.get("proxy-groups", [])],
        "provider_count": len(config.get("proxy-providers", {})),
        "bulk_rules_count": sum("BULK" in str(x) or "QOS-" in str(x) for x in config.get("rules", []))}
    del raw_config, config
    r["transactions"] = run(r2, """test -e /tmp/selfheal.pause && echo SELFHEAL_PAUSE_PRESENT || echo SELFHEAL_PAUSE_ABSENT
find /etc/routermgmt/state -maxdepth 4 -name uncommitted -print 2>/dev/null
curl --noproxy '*' -sk -m 5 -o /dev/null -w 'health=%{http_code}\\n' https://127.0.0.1:18443/api/v1/health
""")
    # One bounded 64KiB GET via the current proxy. No group selection changes.
    r["bounded_get"] = run(r2, """curl -4 -sS -m 25 --max-filesize 65536 -x http://127.0.0.1:7890 -o /dev/null -w 'code=%{http_code} bytes=%{size_download} seconds=%{time_total}\\n' 'https://speed.cloudflare.com/__down?bytes=65536'
""", 35)
    r["meter_rules_after"] = run(r2, """iptables -L PROXY_METER_OUT -v -x -n
iptables -L PROXY_METER_IN -v -x -n
""")
    save()
    print("R2_READ_ONLY_COLLECTED", flush=True)

    servers = {}
    for name, host, port, pw in [("GIA", "154.36.139.155", 22, GIA_PW), ("BWG", "74.82.193.113", BWG_PORT, BWG_PW)]:
        attempts = []
        c = None
        options = [("direct", None, port)]
        if name == "GIA" and port != 28722:
            options.append(("direct_alternate_port", None, 28722))
        if name == "BWG" and "GIA" in servers:
            options.append(("via_GIA", servers["GIA"], port))
        for route, jump, candidate_port in options:
            try:
                c = connect(host, candidate_port, "root", pw, jump)
                attempts.append({"route": route, "port": candidate_port, "success": True})
                break
            except paramiko.AuthenticationException:
                attempts.append({"route": route, "port": candidate_port, "error": "authentication_failed"})
                break
            except Exception as exc:
                attempts.append({"route": route, "port": candidate_port, "error_type": type(exc).__name__})
        result["hosts"][name] = {"connected": bool(c), "attempts": attempts}
        if not c:
            save()
            print(name + "_CONNECTION_UNAVAILABLE", flush=True)
            continue
        connections.append(c)
        servers[name] = c
        x = result["hosts"][name]
        x["system_qdisc"] = run(c, """date -Is
uname -r
ip -4 route show default
tc -V
tc -s qdisc show
ip -br link
df -h / /var
""")
        x["listeners_services"] = run(c, """ss -H -ltnu | awk '{print $1,$5}'
systemctl list-units --type=service --all --no-pager --no-legend | awk '/s-box|sing-box|bulk|qos|meter/ {print $1,$3,$4}'
systemctl show s-box-bulk.service -p LoadState -p ActiveState -p FragmentPath 2>/dev/null
""")
        x["meter_files"] = run(c, """sha256sum /usr/local/bin/gia-meter-sample.sh 2>/dev/null
crontab -l 2>/dev/null | awk '/gia-meter|bulk|qos/ {print}'
ls -ln /var/lib/gia-meter/samples.jsonl* 2>/dev/null
""")
        if name == "GIA":
            nft = get_json(c, "nft -j list table inet gia_usage_meter")
            items = nft.get("nftables", [])
            x["gia_meter"] = {"available": bool(items),
                "counters": [{k: z["counter"].get(k) for k in ["name", "packets", "bytes"]} for z in items if "counter" in z],
                "rule_count": sum("rule" in z for z in items),
                "chain_names": [z["chain"].get("name") for z in items if "chain" in z]}
            # Rule expressions contain public addresses, not credentials; keep for actual overlap analysis.
            x["gia_meter"]["rules"] = [z["rule"] for z in items if "rule" in z]
            x["sample_window"] = run(c, """python3 - <<'PY'
import json,pathlib
p=pathlib.Path('/var/lib/gia-meter/samples.jsonl')
lines=p.read_text().splitlines() if p.exists() else []
valid=[]; bad=0; errors=0
for line in lines:
 try:
  z=json.loads(line)
  if 'error' in z: errors+=1
  else: valid.append(z)
 except Exception: bad+=1
print(json.dumps({'lines':len(lines),'valid':len(valid),'bad':bad,'error_samples':errors,'first_ts':valid[0].get('ts') if valid else None,'last_ts':valid[-1].get('ts') if valid else None,'boot_ids':list(set(z.get('boot') for z in valid))}))
PY
""")
        x["vnstat"] = run(c, "vnstat --json m 1 2>/dev/null\n")
        save()
        print(name + "_READ_ONLY_COLLECTED", flush=True)
    result["finished_local"] = datetime.datetime.now().astimezone().isoformat(timespec="seconds")
    save()
    print("AUDIT_COMPLETE " + str(OUT), flush=True)
finally:
    for c in reversed(connections):
        c.close()
