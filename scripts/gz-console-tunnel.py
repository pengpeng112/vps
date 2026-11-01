"""Pinned SSH tunnel to the existing management console through Guangzhou."""
import argparse
import json
import pathlib
import select
import socketserver
import ssl
import threading
import urllib.request

import paramiko

ROOT = pathlib.Path(__file__).resolve().parents[1]
PORT = 8445


class Forwarder(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="Check health/UI and exit; no login or configuration writes")
    args = parser.parse_args()
    credentials = ROOT / "backups/.gz-ops-credentials.json"
    known = ROOT / "backups/maintenance-20261029/known_hosts"
    if not credentials.is_file() or not known.is_file():
        raise SystemExit("Controlled Guangzhou credentials or pinned host identity missing; no connection attempted.")
    cfg = json.loads(credentials.read_text(encoding="utf-8"))
    client = paramiko.SSHClient()
    client.load_host_keys(str(known))
    client.set_missing_host_key_policy(paramiko.RejectPolicy())
    client.connect(cfg["GZ_HOST"], port=28722, username=cfg["GZ_USER"], password=cfg["GZ_PASS"],
                   timeout=10, auth_timeout=10, allow_agent=False, look_for_keys=False)
    transport = client.get_transport()
    transport.set_keepalive(30)

    class Handler(socketserver.BaseRequestHandler):
        def handle(self):
            channel = None
            try:
                channel = transport.open_channel("direct-tcpip", ("127.0.0.1", 8443), self.client_address, timeout=10)
                while True:
                    ready, _, _ = select.select([self.request, channel], [], [], 30)
                    if not transport.is_active():
                        break
                    if self.request in ready:
                        data = self.request.recv(65536)
                        if not data:
                            break
                        channel.sendall(data)
                    if channel in ready:
                        data = channel.recv(65536)
                        if not data:
                            break
                        self.request.sendall(data)
            except (OSError, paramiko.SSHException, EOFError):
                pass
            finally:
                if channel is not None:
                    channel.close()

    try:
        with Forwarder(("127.0.0.1", PORT), Handler) as server:
            if args.check:
                worker = threading.Thread(target=server.serve_forever, daemon=True)
                worker.start()
                # Only loopback health/UI probes. SSH server identity remains pinned.
                opener = urllib.request.build_opener(urllib.request.ProxyHandler({}),
                    urllib.request.HTTPSHandler(context=ssl._create_unverified_context()))
                try:
                    for path in ["/api/v1/health", "/"]:
                        with opener.open(f"https://127.0.0.1:{PORT}{path}", timeout=10) as response:
                            print(f"{path} HTTP {response.status}", flush=True)
                finally:
                    server.shutdown()
                    worker.join(timeout=3)
            else:
                print(f"Connected through Guangzhou. Open https://127.0.0.1:{PORT}", flush=True)
                print("Keep this window open. Close with Ctrl+C. Console login is unchanged.", flush=True)
                try:
                    server.serve_forever()
                except KeyboardInterrupt:
                    pass
    finally:
        client.close()


if __name__ == "__main__":
    main()
