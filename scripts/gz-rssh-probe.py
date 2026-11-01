#!/usr/bin/env python3
"""Observe reverse SSH banners. Never kills, authenticates, or repairs sessions."""
import json
import pathlib
import re
import socket
import subprocess
import syslog

state = pathlib.Path("/run/gz-rssh-probe.json")
try:
    previous = json.loads(state.read_text())
except (OSError, ValueError):
    previous = {}
listeners = subprocess.run(["ss", "-Hln", "-t"], capture_output=True, text=True, check=True).stdout
ports = sorted(set(int(p) for p in re.findall(r"127\.0\.0\.1:(132\d\d)\b", listeners)))
current = {}
for port in ports:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=3) as connection:
            connection.settimeout(3)
            banner = connection.recv(256)
        current[str(port)] = "ssh_banner_ok" if banner.startswith(b"SSH-2.0-") else "invalid_banner"
    except OSError:
        current[str(port)] = "listener_without_ssh_banner"
for port in sorted(set(previous) | set(current)):
    status = current.get(port, "listener_missing")
    if previous.get(port) != status:
        syslog.openlog("gz-rssh-probe")
        syslog.syslog(syslog.LOG_INFO if status == "ssh_banner_ok" else syslog.LOG_WARNING,
                      f"port={port} status={status} previous={previous.get(port, 'unknown')}")
state.write_text(json.dumps(current, sort_keys=True))
print(json.dumps(current, sort_keys=True))
