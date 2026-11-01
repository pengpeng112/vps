"""Redeem exactly one explicitly selected Codex reset at its scheduled time.

Uses documented local app-server RPC, never browser clicking or private endpoints.
--check is strictly read-only. No model turn is started.
"""
import argparse
import datetime as dt
import json
import os
import pathlib
import queue
import subprocess
import threading
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
CONFIG = ROOT / "backups/codex-reset-20261030.json"
LOG = ROOT / "logs/codex-reset/20261030.jsonl"
TZ = dt.timezone(dt.timedelta(hours=8))


class RPCError(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__("app-server RPC error")


class RPC:
    def __init__(self, config):
        env = os.environ.copy()
        if config.get("codexHome"):
            env["CODEX_HOME"] = config["codexHome"]
        self.proc = subprocess.Popen([config["codexCommand"], "app-server", "--stdio"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, encoding="utf-8", env=env,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self.messages = queue.Queue()
        self.next_id = 0
        threading.Thread(target=self.read, daemon=True).start()
        try:
            self.call("initialize", {"clientInfo": {"name": "local_reset_scheduler", "title": "Local reset scheduler", "version": "1.0"},
                                     "capabilities": {"experimentalApi": True}})
            self.send({"method": "initialized", "params": {}})
        except Exception:
            self.close()
            raise

    def read(self):
        for line in self.proc.stdout:
            try:
                self.messages.put(json.loads(line))
            except ValueError:
                pass
        self.messages.put(None)

    def send(self, message):
        self.proc.stdin.write(json.dumps(message) + "\n")
        self.proc.stdin.flush()

    def call(self, method, params=None):
        self.next_id += 1
        message = {"id": self.next_id, "method": method}
        if params is not None:
            message["params"] = params
        self.send(message)
        deadline = time.monotonic() + 35
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("RPC response deadline exceeded")
            item = self.messages.get(timeout=remaining)
            if item is None:
                raise RuntimeError("app-server exited")
            if item.get("id") == self.next_id:
                if "error" in item:
                    raise RPCError(item["error"].get("code"))
                return item.get("result")

    def close(self):
        try:
            self.proc.stdin.close()
            self.proc.wait(timeout=4)
        except (OSError, subprocess.TimeoutExpired):
            self.proc.terminate()


def record(event, **fields):
    LOG.parent.mkdir(parents=True, exist_ok=True)
    entry = {"at": dt.datetime.now(TZ).isoformat(), "event": event, **fields}
    with LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=True) + "\n")
    print(json.dumps(entry, ensure_ascii=True), flush=True)


def find_credit(info, credit_id):
    details = info.get("rateLimitResetCredits") or {}
    return next((x for x in details.get("credits") or [] if x.get("id") == credit_id), None)


def limits_summary(info):
    limit = info.get("rateLimits") or {}
    return {"availableCount": (info.get("rateLimitResetCredits") or {}).get("availableCount"),
            "primaryUsedPercent": (limit.get("primary") or {}).get("usedPercent"),
            "secondaryUsedPercent": (limit.get("secondary") or {}).get("usedPercent")}


def main():
    parser = argparse.ArgumentParser()
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--check", action="store_true")
    action.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    now = time.time()
    # Task Scheduler may coalesce trigger seconds. Start the process ahead of
    # time, but do not permit redemption before the exact user-selected time.
    if args.execute and 0 < config["runAt"] - now <= 120:
        time.sleep(config["runAt"] - now)
        now = time.time()
    if args.execute and not config["runAt"] <= now < config["stopAt"]:
        record("outside_execution_window_no_redemption")
        return 2
    if args.execute:
        if LOG.exists():
            for line in LOG.read_text(encoding="utf-8").splitlines():
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if event.get("event") == "redemption" and event.get("outcome") in ["reset", "alreadyRedeemed"]:
                    record("already_completed_no_second_redemption")
                    return 0
    # A single idempotency key is persisted when scheduling and reused on every retry.
    for attempt in range(1, 4):
        rpc = None
        try:
            rpc = RPC(config)
            info = rpc.call("account/rateLimits/read")
            selected = find_credit(info, config["creditId"])
            if args.check:
                if selected is None:
                    record("check_failed_selected_credit_missing", **limits_summary(info))
                    return 3
                record("read_only_check_passed", selectedExpiresAt=selected.get("expiresAt"),
                       targetTime=dt.datetime.fromtimestamp(config["runAt"], TZ).isoformat(),
                       **limits_summary(info))
                return 0
            # If an earlier consume response was lost, retry the SAME opaque credit ID
            # and idempotency key, allowing the server to return alreadyRedeemed.
            if selected is not None:
                if selected.get("status") != "available" or selected.get("expiresAt") != config["expiresAt"]:
                    record("selected_credit_changed_no_redemption")
                    return 4
            elif not config.get("attemptStarted"):
                record("selected_credit_missing_no_redemption", **limits_summary(info))
                return 4
            if not config["runAt"] <= time.time() < config["stopAt"]:
                record("execution_window_closed_no_redemption")
                return 2
            config["attemptStarted"] = True
            temp = CONFIG.with_suffix(".tmp")
            temp.write_text(json.dumps(config, indent=2), encoding="utf-8")
            temp.replace(CONFIG)
            outcome = rpc.call("account/rateLimitResetCredit/consume", {
                "idempotencyKey": config["idempotencyKey"], "creditId": config["creditId"]})
            record("redemption", outcome=outcome.get("outcome"), attempt=attempt)
            # Never pick another credit, even when this one is unavailable or ineligible.
            refreshed = rpc.call("account/rateLimits/read")
            record("after_redemption", **limits_summary(refreshed))
            return 0 if outcome.get("outcome") in ["reset", "alreadyRedeemed"] else 5
        except Exception as exc:
            record("attempt_error", attempt=attempt, errorType=type(exc).__name__,
                   rpcCode=getattr(exc, "code", None))
            if args.check or attempt == 3 or time.time() + 20 >= config["stopAt"]:
                return 6
            time.sleep(20)
        finally:
            if rpc is not None:
                rpc.close()
    return 6


if __name__ == "__main__":
    raise SystemExit(main())
