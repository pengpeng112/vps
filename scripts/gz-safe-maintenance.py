"""Checkpointed maintenance. Never restarts SSH, networking, the proxy, or tunnels.

Credentials and raw backups stay in ignored backups/. Explicit subcommands only.
"""
import argparse
import hashlib
import io
import json
import logging
import pathlib
import re
import shlex
import time
import yaml

import paramiko

ROOT = pathlib.Path(__file__).resolve().parents[1]
PRIVATE = ROOT / "backups" / "maintenance-20261029"
PRIVATE.mkdir(exist_ok=True)
logging.getLogger("paramiko").setLevel(logging.CRITICAL)


def credentials():
    return json.loads((ROOT / "backups/.gz-ops-credentials.json").read_text(encoding="utf-8"))


def connect_gz():
    c = credentials()
    s = paramiko.SSHClient()
    known = PRIVATE / "known_hosts"
    if known.exists():
        s.load_host_keys(str(known))
    # First run pins the same host inspected in the preceding session.
    s.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    s.connect(c["GZ_HOST"], port=28722, username=c["GZ_USER"], password=c["GZ_PASS"],
              timeout=10, auth_timeout=10, look_for_keys=False, allow_agent=False)
    s.save_host_keys(str(known))
    return s


def run(client, cmd, timeout=30, check=True):
    _, out, err = client.exec_command(cmd, timeout=timeout)
    data = out.read().decode("utf-8", "replace")
    error = err.read().decode("utf-8", "replace")
    rc = out.channel.recv_exit_status()
    if check and rc:
        raise RuntimeError(f"remote command failed rc={rc}: {error[:160]}")
    return data


def device(gz, port, via_gia=False):
    parent = gz
    if via_gia:
        with gz.open_sftp() as ftp:
            with ftp.open("/home/ubuntu/.ssh/id_ed25519_gia_relay") as f:
                key = paramiko.Ed25519Key.from_private_key(io.StringIO(f.read().decode()))
        ch = gz.get_transport().open_channel("direct-tcpip", ("154.36.137.54", 28722), ("127.0.0.1", 0), timeout=8)
        parent = paramiko.SSHClient()
        parent.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        parent.connect("154.36.137.54", username="root", pkey=key, sock=ch, timeout=8, look_for_keys=False, allow_agent=False)
    try:
        ch = parent.get_transport().open_channel("direct-tcpip", ("127.0.0.1", port), ("127.0.0.1", 0), timeout=8)
    except paramiko.ChannelException:
        if not via_gia:
            raise
        # The relay credential permits only forwarding 8443 but also explicitly
        # permits root shell administration. Use that existing operator path;
        # do not widen the relay credential's permitopen list.
        ch = parent.get_transport().open_session(timeout=8)
        ch.exec_command("exec /usr/bin/nc 127.0.0.1 " + str(port))
    d = paramiko.SSHClient()
    d.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    d.connect("127.0.0.1", username="root", password=credentials()["DEV_PASS"], sock=ch,
              timeout=8, banner_timeout=6, auth_timeout=8, look_for_keys=False, allow_agent=False)
    return d, parent if via_gia else None


def snapshot(d, label):
    ident = run(d, "grep '^DEVICE_ID=' /etc/routermgmt/agent.conf").strip()
    if not re.fullmatch(r"DEVICE_ID=rtr-[0-9a-f]+", ident):
        raise RuntimeError("unexpected device identity")
    result = {"identity": ident, "time": run(d, "date; uptime").strip()}
    for group in ["PROXY", "AUTO", "NODE-PICK", "US-MEDIA"]:
        value = json.loads(run(d, "curl -fsS -m 4 http://127.0.0.1:9090/proxies/" + group))
        result[group] = {k: value.get(k) for k in ["type", "now", "all", "alive"]}
    result["cron"] = run(d, "crontab -l", check=False)
    result["health"] = run(d, "for p in 18443 18444 18445 18446 18447; do printf '%s=' \"$p\"; curl -fsk -m 2 -o /dev/null -w '%{http_code}\\n' https://127.0.0.1:$p/api/v1/health; done; true", timeout=20)
    (PRIVATE / (label + ".json")).write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=True), flush=True)
    return ident


def bytes_read(d, path):
    _, o, _ = d.exec_command("cat " + shlex.quote(path), timeout=15)
    data = o.read()
    if o.channel.recv_exit_status():
        raise RuntimeError("cannot read checkpoint file " + path)
    return data


def bytes_write(d, path, data, mode=0o600):
    i, o, e = d.exec_command("umask 077; cat > " + shlex.quote(path) + "; chmod " + oct(mode)[2:] + " " + shlex.quote(path), timeout=20)
    i.write(data); i.flush(); i.channel.shutdown_write()
    o.read()
    if o.channel.recv_exit_status():
        raise RuntimeError("staging upload failed")
    digest = run(d, "md5sum " + shlex.quote(path)).split()[0]
    if digest != hashlib.md5(data).hexdigest():
        raise RuntimeError("staging hash mismatch; no activation")


def reverse_pids(d):
    info = json.loads(run(d, "ubus call service list '{\"name\":\"mgmt-tunnel\"}'"))
    instances = info.get("mgmt-tunnel", {}).get("instances", {})
    return {k: v.get("pid") for k, v in instances.items() if k.startswith("rssh")}


def repair_device(d, port):
    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup = "/root/repair-" + stamp
    run(d, "umask 077; mkdir " + backup)
    saved = {}
    manifest = {"port": port, "backup": backup, "files": [], "reverse_before": reverse_pids(d)}
    config = "/etc/mihomo/config.yaml"
    paths = [config, "/etc/routermgmt/mgmt-port-discovery.sh", "/etc/routermgmt/tunnel-watchdog.sh", "/etc/routermgmt/handlers/switch_node.sh", "/etc/crontabs/root"]
    for path in paths:
        data = bytes_read(d, path)
        saved[path] = data
        name = path.replace("/", "_").lstrip("_")
        (PRIVATE / f"{port}-{stamp}-{name}").write_bytes(data)
        run(d, "cp -p " + shlex.quote(path) + " " + backup + "/" + name)
        manifest["files"].append({"path": path, "backup": backup + "/" + name,
                                  "before_sha256": hashlib.sha256(data).hexdigest()})
    journal = PRIVATE / f"{port}-{stamp}-manifest.json"
    journal.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    # Abort before writes if a config transaction is currently in progress.
    pending = run(d, "find /etc/routermgmt/state/txn -name uncommitted 2>/dev/null; true").strip()
    if pending:
        raise RuntimeError("active configuration transaction; checkpoint saved, no activation")
    if not manifest["reverse_before"]:
        raise RuntimeError("no independent reverse SSH instances")
    service_schema = run(d, "ubus -v list service")
    if '"instance":"String"' not in service_schema or '"signal"' not in service_schema:
        raise RuntimeError("per-instance recovery unsupported")
    original = saved[config].decode("utf-8", "replace")
    doc = yaml.safe_load(original)
    nodes = [x["name"] for x in doc["proxies"] if "LAX" in x["name"]]
    media = next(x for x in doc["proxy-groups"] if x["name"] == "US-MEDIA")
    start = re.search(r'^  - name: ["\']?US-MEDIA["\']?\s*$', original, re.M)
    if not start:
        raise RuntimeError("unrecognized media group layout")
    next_group = re.search(r'^  - name:|^rules:', original[start.end():], re.M)
    if not next_group:
        raise RuntimeError("media group boundary not found")
    end = start.end() + next_group.start()
    replacement = ('  - name: "US-MEDIA"\n    type: fallback\n'
                   '    url: https://www.gstatic.com/generate_204\n    interval: 300\n'
                   '    lazy: false\n    timeout: 4000\n    expected-status: 204\n    proxies:\n'
                   + ''.join('      - ' + name + '\n' for name in nodes + ['NODE-PICK']) + '\n')
    updated = (original[:start.start()] + replacement + original[end:]).encode()
    newdoc = yaml.safe_load(updated)
    old_without = dict(doc); old_without["proxy-groups"] = [g for g in doc["proxy-groups"] if g["name"] != "US-MEDIA"]
    new_without = dict(newdoc); new_without["proxy-groups"] = [g for g in newdoc["proxy-groups"] if g["name"] != "US-MEDIA"]
    if new_without != old_without:
        raise RuntimeError("unexpected change outside media group")
    stage = config + ".repair-stage"
    bytes_write(d, stage, updated, 0o644)
    # Validate without starting another proxy or changing traffic.
    i, o, e = d.exec_command("/usr/bin/mihomo -t -d /etc/mihomo -f " + stage, timeout=30)
    validation = o.read() + e.read()
    (PRIVATE / f"{port}-{stamp}-validation.log").write_bytes(validation)
    if o.channel.recv_exit_status():
        raise RuntimeError("proxy validation failed; active config untouched")
    activated = []
    try:
        # Recovery script first; no direct invocation, no service restart.
        for rel, path in [("tunnel-watchdog.sh", paths[2]), ("mgmt-port-discovery.sh", paths[1]), ("handlers/switch_node.sh", paths[3])]:
            data = (ROOT / "mgmt/agent" / rel).read_bytes().replace(b"\r\n", b"\n")
            temp = path + ".repair-stage"
            bytes_write(d, temp, data, 0o600 if "handlers/" in rel else 0o755)
            run(d, "sh -n " + shlex.quote(temp))
            run(d, "mv " + shlex.quote(temp) + " " + shlex.quote(path))
            activated.append(path)
        cron = saved[paths[4]].decode()
        cron = ''.join(line + '\n' for line in cron.splitlines() if '#routermgmt-tunnelwd' not in line)
        if "mgmt-port-discovery.sh" not in cron:
            raise RuntimeError("persistent discovery cron missing")
        bytes_write(d, paths[4] + ".repair-stage", cron.encode(), 0o600)
        run(d, "crontab " + paths[4] + ".repair-stage && rm -f " + paths[4] + ".repair-stage")
        activated.append(paths[4])
        run(d, "mv " + stage + " " + config)
        activated.append(config)
        code = run(d, "curl -sS -m 12 -o /dev/null -w '%{http_code}' -X PUT -H 'Content-Type: application/json' --data-binary '{\"path\":\"/etc/mihomo/config.yaml\"}' 'http://127.0.0.1:9090/configs?force=true'")
        if code not in ["200", "204"]:
            raise RuntimeError("hot reload rejected")
        time.sleep(3)
        runtime = json.loads(run(d, "curl -fsS -m 4 http://127.0.0.1:9090/proxies/US-MEDIA"))
        if runtime.get("all") != nodes + ["NODE-PICK"] or runtime.get("now") == "COMPATIBLE":
            raise RuntimeError("media runtime verification failed")
        if reverse_pids(d) != manifest["reverse_before"]:
            raise RuntimeError("reverse SSH changed unexpectedly; stop and rollback")
        snapshot(d, str(port) + "-" + stamp + "-after")
        manifest["result"] = "passed"
        manifest["reverse_after"] = reverse_pids(d)
        for item in manifest["files"]:
            item["after_sha256"] = hashlib.sha256(bytes_read(d, item["path"])).hexdigest()
        manifest["handler_activation"] = "file staged; existing agent keeps its loaded functions until next natural restart"
    except Exception:
        for path in reversed(activated):
            name = path.replace("/", "_").lstrip("_")
            run(d, "cp -p " + backup + "/" + name + " " + path, check=False)
        if config in activated:
            run(d, "curl -sS -m 10 -o /dev/null -X PUT -H 'Content-Type: application/json' --data-binary '{\"path\":\"/etc/mihomo/config.yaml\"}' 'http://127.0.0.1:9090/configs?force=true'", check=False)
        if paths[4] in activated:
            run(d, "crontab " + paths[4], check=False)
        manifest["result"] = "rolled back; inspect checkpoint"
        raise
    finally:
        journal.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        print(json.dumps({"checkpoint": str(journal), "result": manifest.get("result", "not activated")}), flush=True)


def secure_gz(gz):
    active = run(gz, "sudo ss -Htn state established '( sport = :8443 )'").strip()
    if active:
        raise RuntimeError("8443 has active clients; leave ingress unchanged")
    stamp = time.strftime("%Y%m%d-%H%M%S")
    unit = "/etc/systemd/system/gz-relay.service"
    original = run(gz, "sudo cat " + unit).encode()
    if b"-L 0.0.0.0:8443:" not in original:
        raise RuntimeError("relay layout changed; manual re-evaluation required")
    (PRIVATE / ("gz-relay-" + stamp + ".service")).write_bytes(original)
    pid_before = run(gz, "systemctl show -p MainPID --value gz-relay.service").strip()
    guard = "/etc/systemd/system/gz-mgmt-ingress.service"
    if run(gz, "test -e " + guard + "; echo $?", check=False).strip() != "1":
        raise RuntimeError("ingress guard already exists; do not overwrite")
    iptables = run(gz, "command -v iptables").strip()
    rule = "! -i lo -p tcp --dport 8443 -m comment --comment gz-mgmt-loopback-only -j REJECT"
    guard_data = ("[Unit]\nDescription=Restrict management relay ingress to loopback\nBefore=gz-relay.service\n"
                  "[Service]\nType=oneshot\nRemainAfterExit=yes\n"
                  f"ExecStart={iptables} -w -I INPUT 1 {rule}\n"
                  f"ExecStop={iptables} -w -D INPUT {rule}\n"
                  "[Install]\nWantedBy=multi-user.target\n").encode()
    relay_stage = "/home/ubuntu/gz-relay.repair-stage"
    guard_stage = "/home/ubuntu/gz-ingress.repair-stage"
    bytes_write(gz, relay_stage, original.replace(b"-L 0.0.0.0:8443:", b"-L 127.0.0.1:8443:"))
    bytes_write(gz, guard_stage, guard_data)
    remote_backup = unit + ".bak-" + stamp
    run(gz, "sudo cp -p " + unit + " " + remote_backup)
    applied = False
    try:
        run(gz, "sudo install -m 644 " + guard_stage + " " + guard)
        run(gz, "sudo systemctl daemon-reload; sudo systemctl enable --now gz-mgmt-ingress.service")
        applied = True
        # Change the next-start binding without restarting the existing relay.
        run(gz, "sudo install -m 644 " + relay_stage + " " + unit + "; sudo systemctl daemon-reload")
        health = run(gz, "curl -fksS -m 6 -o /dev/null -w '%{http_code}' https://127.0.0.1:8443/api/v1/health")
        pid_after = run(gz, "systemctl show -p MainPID --value gz-relay.service").strip()
        if health != "200" or pid_before != pid_after:
            raise RuntimeError("relay health/pid changed")
        print(json.dumps({"gz_guard": "active", "health": health, "relay_pid_unchanged": pid_after,
                          "backup": remote_backup, "next_start_binding": "127.0.0.1:8443"}), flush=True)
    except Exception:
        if applied:
            run(gz, "sudo systemctl disable --now gz-mgmt-ingress.service", check=False)
        run(gz, "sudo cp -p " + remote_backup + " " + unit + "; sudo systemctl daemon-reload", check=False)
        raise


def repair_auto(d, port):
    """Align nested health checks without altering any proxy or management endpoint."""
    stamp = time.strftime("%Y%m%d-%H%M%S")
    path = "/etc/mihomo/config.yaml"
    original = bytes_read(d, path)
    backup = path + ".before-auto-" + stamp
    run(d, "cp -p " + path + " " + backup)
    (PRIVATE / f"{port}-{stamp}-before-auto.yaml").write_bytes(original)
    text = original.decode("utf-8", "replace")
    block = re.search(r'^  - name: "AUTO"\n.*?(?=^  - name:|^rules:)', text, re.M | re.S)
    if not block:
        raise RuntimeError("AUTO layout unknown")
    updated_block = re.sub(r'^    url:.*$', '    url: https://www.gstatic.com/generate_204', block[0], flags=re.M)
    updated_block = re.sub(r'^    interval:.*$', '    interval: 120', updated_block, flags=re.M)
    if '    expected-status:' not in updated_block:
        updated_block = updated_block.replace('    lazy: false\n', '    lazy: false\n    timeout: 4000\n    expected-status: 204\n')
    updated = (text[:block.start()] + updated_block + text[block.end():]).encode()
    before = yaml.safe_load(original); after = yaml.safe_load(updated)
    before["proxy-groups"] = [x for x in before["proxy-groups"] if x["name"] != "AUTO"]
    after["proxy-groups"] = [x for x in after["proxy-groups"] if x["name"] != "AUTO"]
    if before != after:
        raise RuntimeError("change outside AUTO")
    pids = reverse_pids(d)
    bytes_write(d, path + ".auto-stage", updated, 0o644)
    # Discard validation output because config contains device credentials.
    run(d, "/usr/bin/mihomo -t -d /etc/mihomo -f " + path + ".auto-stage >/dev/null 2>&1")
    run(d, "mv " + path + ".auto-stage " + path)
    try:
        code = run(d, "curl -sS -m 10 -o /dev/null -w '%{http_code}' -X PUT -H 'Content-Type: application/json' --data-binary '{\"path\":\"/etc/mihomo/config.yaml\"}' 'http://127.0.0.1:9090/configs?force=true'")
        if code not in ["200", "204"]:
            raise RuntimeError("AUTO hot load failed")
        for _ in range(3):
            time.sleep(3)
            run(d, "curl -sS -m 8 'http://127.0.0.1:9090/group/AUTO/delay?url=https%3A%2F%2Fwww.gstatic.com%2Fgenerate_204&timeout=4000' >/dev/null",check=False)
            code = run(d, "curl -sS -m 8 -x http://127.0.0.1:7890 -o /dev/null -w '%{http_code}' https://www.gstatic.com/generate_204",check=False)
            if code == "204":
                break
        if code != "204" or reverse_pids(d) != pids:
            raise RuntimeError("AUTO egress or reverse SSH verification failed")
        print(json.dumps({"auto_health_aligned": port, "foreign_http": code,
                          "reverse_pids_unchanged": pids, "backup": backup}),flush=True)
        snapshot(d, f"{port}-{stamp}-auto-after")
    except Exception:
        run(d, "cp -p " + backup + " " + path)
        run(d, "curl -sS -m 10 -o /dev/null -X PUT -H 'Content-Type: application/json' --data-binary '{\"path\":\"/etc/mihomo/config.yaml\"}' 'http://127.0.0.1:9090/configs?force=true'",check=False)
        raise


def install_monitor(gz):
    service = ("[Unit]\nDescription=Observe reverse SSH banners without changing connections\n"
               "[Service]\nType=oneshot\nExecStart=/usr/bin/python3 /usr/local/sbin/gz-rssh-probe.py\n"
               "TimeoutStartSec=60\n")
    timer = ("[Unit]\nDescription=Periodically check actual reverse SSH availability\n"
             "[Timer]\nOnBootSec=45\nOnUnitActiveSec=120\nAccuracySec=5\n"
             "[Install]\nWantedBy=timers.target\n")
    for name, data, target, mode in [
            ("gz-rssh-probe.py", (ROOT / "scripts/gz-rssh-probe.py").read_bytes(), "/usr/local/sbin/gz-rssh-probe.py", "755"),
            ("gz-rssh-probe.service", service.encode(), "/etc/systemd/system/gz-rssh-probe.service", "644"),
            ("gz-rssh-probe.timer", timer.encode(), "/etc/systemd/system/gz-rssh-probe.timer", "644")]:
        exists = run(gz, "sudo test -e " + target + "; echo $?", check=False).strip()
        if exists != "1":
            raise RuntimeError("monitor destination exists; do not overwrite")
        stage = "/home/ubuntu/" + name + ".repair-stage"
        bytes_write(gz, stage, data)
        run(gz, "sudo install -m " + mode + " " + stage + " " + target)
    run(gz, "sudo systemctl daemon-reload; sudo systemctl enable --now gz-rssh-probe.timer; sudo systemctl start gz-rssh-probe.service")
    print(run(gz, "sudo cat /run/gz-rssh-probe.json; systemctl is-active gz-rssh-probe.timer"),flush=True)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("action", choices=["baseline", "repair-device", "repair-auto", "gz-audit", "secure-gz", "install-monitor"])
    p.add_argument("--port", type=int)
    p.add_argument("--expect")
    p.add_argument("--via-gia", action="store_true")
    args = p.parse_args()
    gz = connect_gz()
    try:
        if args.action == "install-monitor":
            install_monitor(gz)
            return
        if args.action in ["gz-audit", "secure-gz"]:
            print(run(gz, "date -Is; sudo ss -lntp; sudo ss -tn state established '( sport = :8443 )'; sudo iptables -S INPUT; sudo systemctl cat gz-relay.service"))
            if args.action == "secure-gz":
                secure_gz(gz)
            return
        if args.port not in range(13222, 13234):
            raise ValueError("explicit fleet port required")
        d, parent = device(gz, args.port, args.via_gia)
        try:
            ident = snapshot(d, str(args.port) + "-" + args.action + "-before")
            if args.action in ["repair-device", "repair-auto"]:
                if ident != "DEVICE_ID=" + (args.expect or ""):
                    raise RuntimeError("identity mismatch; no writes")
                if args.action == "repair-device":
                    repair_device(d, args.port)
                else:
                    repair_auto(d, args.port)
        finally:
            d.close()
            if parent:
                parent.close()
    finally:
        gz.close()


if __name__ == "__main__":
    main()
