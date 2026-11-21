"""CF06 stage2: pause self-heal -> preview+assert -> apply v40 -> poll task ->
verify device file/groups/cache -> unpause. 单次 apply，409 则重新 preview（不无限重试）。"""
from pathlib import Path
import hashlib
import json
import sys
import time
import uuid as uuidlib

sys.path.insert(0, str(Path(__file__).resolve().parent))
from path_a_gz_api import GzApi  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
CTRL = ROOT / "backups/cf2d-golive-20261118"
HELPER = ROOT / "review/fleet-qos-readonly-audit-20261117.py"
DEV = "rtr-a9bcba"


def main() -> int:
    ns = {"__file__": str(HELPER)}
    prefix, sep, _ = HELPER.read_text(encoding="utf-8-sig").partition("\ntry:\n")
    exec(compile(prefix, "<helper>", "exec"), ns)
    connect, run, get_json = ns["connect"], ns["run"], ns["get_json"]

    gz = connect("134.175.151.213", 28722, "ubuntu", ns["DEV_PW"])
    r2 = connect("127.0.0.1", 13223, "root", ns["DEV_PW"], gz)
    record = {"steps": []}
    api = GzApi()
    try:
        # 1) pause self-heal
        ps = run(r2, "[ -e /tmp/selfheal.pause ] && echo PAUSE_EXISTS || { touch /tmp/selfheal.pause && echo PAUSED; }")
        print("selfheal:", ps["stdout"].strip())
        if "PAUSED" not in ps["stdout"] and "PAUSE_EXISTS" not in ps["stdout"]:
            print("ABORT: 无法暂停 self-heal")
            return 1

        # 2) preview + 快断言
        api.login()
        code, body = api.post(f"/api/v1/devices/{DEV}/configs/preview")
        assert code == 200, body
        masked = body["masked_yaml"]
        rev = body["revision"]
        ver = body["next_version"]
        checks = {
            "version40": ver == 40,
            "cf2d_hk_in": '"CF2D-HK-01"' in masked,
            "cf2d_bwg_in": '"CF2D-BWG-01"' in masked,
            "hk_sni": "hk.6849271.xyz" in masked,
            "usbwg_sni": "usbwg.6849271.xyz" in masked,
            "no_cold_domain": "8364927" not in masked,
            "fallback_domestic": "- 223.5.5.5" in masked and "dns.google" not in masked,
            "match_direct_tail": masked.rstrip().endswith("- MATCH,DIRECT"),
        }
        print("preview checks:", checks)
        if not all(checks.values()):
            print("ABORT: preview 断言未过")
            run(r2, "rm -f /tmp/selfheal.pause")
            return 1

        # 3) apply（唯一幂等键）
        idem = "cf2d-r2-v40-" + uuidlib.uuid4().hex[:8]
        code, resp = api.post(f"/api/v1/devices/{DEV}/configs/apply",
                              {"revision": rev}, headers={"Idempotency-Key": idem})
        print(f"apply: http={code} resp={json.dumps(resp, ensure_ascii=False)[:200]}")
        if code != 200:
            run(r2, "rm -f /tmp/selfheal.pause")
            return 1
        task_id = resp.get("task_id") or resp.get("taskId") or resp.get("id")
        version = resp.get("version") or resp.get("config_version")
        record["steps"].append({"op": "apply", "idem": idem, "task": task_id, "version": version})

        # 4) poll task
        status = None
        for _ in range(40):
            code, tl = api.get("/api/v1/tasks?size=5")
            for t in tl.get("items", []):
                if str(t.get("id")) == str(task_id):
                    status = t.get("status")
            if status in ("ok", "failed", "expired"):
                break
            time.sleep(5)
        print("task status:", status)
        if status != "ok":
            print("ABORT: 任务未 ok；保留 self-heal pause 以便检查")
            return 1

        # 5) 设备侧验证
        cand = (CTRL / "r2-v40-candidate-real.yaml").read_text(encoding="utf-8")
        cand_sha = hashlib.sha256(cand.encode()).hexdigest()
        v = run(r2, f"""sleep 3
sha256sum /etc/mihomo/config.yaml
ls -la --time-style=+%H:%M:%S /etc/mihomo/config.yaml
cmp -s /etc/mihomo/config.yaml /root/cf2d-candidate.yaml && echo FILE_EQUALS_CANDIDATE || echo FILE_DIFFERS
grep -c $'\\r' /etc/mihomo/config.yaml || true
""", timeout=60)
        print("device file:\n", v["stdout"])
        file_sha = v["stdout"].splitlines()[0].split()[0] if v["stdout"] else ""
        file_ok = file_sha == cand_sha or "FILE_EQUALS_CANDIDATE" in v["stdout"]

        groups = {}
        for name in ("PROXY", "AUTO", "NODE-PICK", "US-MEDIA", "DOWNLOAD"):
            groups[name] = get_json(r2, "curl --noproxy '*' -fsS -m 5 http://127.0.0.1:9090/proxies/" + name)
        np_members = groups["NODE-PICK"].get("all", [])
        proxy_now = groups["PROXY"].get("now")
        auto_now = groups["AUTO"].get("now")
        dl_now = groups["DOWNLOAD"].get("now")
        us_now = groups["US-MEDIA"].get("now")
        print("NODE-PICK:", np_members)
        print(f"now: PROXY={proxy_now} AUTO={auto_now} US-MEDIA={us_now} DOWNLOAD={dl_now}")
        np_ok = "BWG-HY2-JPTY-01" in np_members[:1] and all(x in np_members for x in ("CF2D-HK-01", "CF2D-BWG-01"))
        proxy_ok = "BULK" not in (groups["PROXY"].get("all") or [])

        # 新节点强制 delay（9090 delay 接口，走真实节点测 gstatic 204）
        delays = {}
        for name in ("CF2D-HK-01", "CF2D-BWG-01"):
            d = get_json(r2, f"curl --noproxy '*' -fsS -m 25 'http://127.0.0.1:9090/proxies/{name}/delay?timeout=8000&url=https%3A%2F%2Fwww.gstatic.com%2Fgenerate_204'")
            delays[name] = d
            print("delay", name, "->", d.get("delay", d))

        hb = run(r2, "date '+%F %T %z'")
        print("device time:", hb["stdout"].strip())

        ok_all = file_ok and np_ok and proxy_ok and dl_now == "DIRECT"
        print("VERIFY:", "PASS" if ok_all else "FAIL", f"(file={file_ok} np={np_ok} proxy_bulk_free={proxy_ok} download={dl_now})")

        # 6) unpause
        up = run(r2, "rm -f /tmp/selfheal.pause && echo PAUSE_CLEARED")
        print(up["stdout"].strip())

        record["steps"].append({"op": "verify", "file_ok": bool(file_ok), "np_ok": bool(np_ok),
                                "proxy_bulk_free": bool(proxy_ok), "download": dl_now,
                                "delays": {k: v.get("delay") for k, v in delays.items()},
                                "version": version, "task": task_id})
        (CTRL / "cf06-stage2.json").write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n",
                                               encoding="utf-8", newline="\n")
        return 0 if ok_all else 1
    finally:
        try:
            api.close()
        finally:
            r2.close()
            gz.close()


if __name__ == "__main__":
    sys.exit(main())
