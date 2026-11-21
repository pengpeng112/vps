"""CF11 serial rollout driver: R1→R3→R4→R5→R6→R8→R9.

Per device (fail-stop):
  backup field config -> bind 16/17 apply=false -> preview assertions ->
  real candidate (mask-restore) -> on-device mihomo -t -> pause selfheal ->
  apply -> task ok -> file sha == candidate -> groups/delay/domestic -> unpause -> hb.
Single API login for the whole run (TOTP limiter friendly).
LAN-source verification: NOT_TESTED per device (recorded; R2 checkpoint likewise).
"""
from pathlib import Path
import hashlib
import json
import re
import sys
import time
import uuid as uuidlib

sys.path.insert(0, str(Path(__file__).resolve().parent))
from path_a_gz_api import GzApi  # noqa: E402
from path_a_cf06_stage1 import mask_line  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
CTRL = ROOT / "backups/cf2d-golive-20261118"
HELPER = ROOT / "review/fleet-qos-readonly-audit-20261117.py"

DEVICES = [  # (id, label, jump port)
    ("rtr-101be0", "R9", 13231),
]
NEW_NODES = {"CF2D-HK-01": 16, "CF2D-BWG-01": 17}


def build_candidate(masked: str, field_text: str, uuid_new: str) -> tuple[str, int]:
    import yaml
    field = yaml.safe_load(field_text)
    real = {}
    for p in field.get("proxies", []):
        vals = {}
        if "uuid" in p:
            vals["uuid"] = str(p["uuid"])
        if "password" in p:
            vals["password"] = str(p["password"])
        ro = p.get("reality-opts") or {}
        if ro.get("public-key"):
            vals["public-key"] = str(ro["public-key"])
        if ro.get("short-id"):
            vals["short-id"] = str(ro["short-id"])
        if vals:
            real[p["name"]] = vals
    for n in NEW_NODES:
        real[n] = {"uuid": uuid_new}

    out_lines, current, sub = [], None, 0
    for line in masked.splitlines():
        m_node = re.match(r'\s*- name: "([^"]+)"', line)
        if m_node and m_node.group(1) in real:
            current = m_node.group(1)
        m_sec = re.match(r"^(\s+(uuid|password|public-key|short-id): )(\S+)$", line)
        if m_sec and current:
            key, masked_val = m_sec.group(2), m_sec.group(3)
            real_val = real[current].get(key)
            if real_val and mask_line(key, real_val) == masked_val:
                out_lines.append(m_sec.group(1) + real_val)
                sub += 1
                continue
        out_lines.append(line)
    return "\n".join(out_lines) + "\n", sub


def remask(candidate: str) -> str:
    lines = []
    for line in candidate.splitlines():
        m_sec = re.match(r"^(\s+(uuid|password|public-key|short-id): )(\S+)$", line)
        if m_sec:
            lines.append(m_sec.group(1) + mask_line(m_sec.group(2), m_sec.group(3)))
        else:
            lines.append(line)
    return "\n".join(lines)


def main() -> int:
    ns = {"__file__": str(HELPER)}
    prefix, sep, _ = HELPER.read_text(encoding="utf-8-sig").partition("\ntry:\n")
    exec(compile(prefix, "<helper>", "exec"), ns)
    connect, run, get_json = ns["connect"], ns["run"], ns["get_json"]
    params = json.loads((ROOT / "backups/.cf-node-params.json").read_text(encoding="utf-8-sig"))

    api = GzApi()
    report = {"started": time.strftime("%F %T"), "devices": {}, "lan_source": "NOT_TESTED(no controllable LAN client; recorded per governance 06 §4.6.4)"}
    gz = connect("134.175.151.213", 28722, "ubuntu", ns["DEV_PW"])
    rollout_ok = True
    try:
        api.login()
        for dev, label, port in DEVICES:
            print(f"\n===== {label} ({dev}) =====")
            entry = {}
            try:
                r = connect("127.0.0.1", port, "root", ns["DEV_PW"], gz)
            except Exception as exc:
                print(f"  SSH 失败（跳过该台）: {type(exc).__name__}")
                report["devices"][dev] = {"result": "SKIP_SSH_FAIL"}
                continue
            try:
                # 1) 备份现场配置（已存在则校验未漂移，不覆盖原备份）
                cfg = run(r, "cat /etc/mihomo/config.yaml", timeout=30)
                if cfg["rc"] != 0:
                    report["devices"][dev] = {"result": "FAIL_READ_CONFIG"}
                    rollout_ok = False
                    break
                cfg_sha = hashlib.sha256((cfg["stdout"] + "\n").encode()).hexdigest()
                bak = CTRL / f"{dev}-config-orig.yaml"
                if bak.exists():
                    prev = hashlib.sha256(bak.read_text(encoding="utf-8").encode()).hexdigest()
                    if prev != cfg_sha:
                        print("  ABORT: 现场配置与本批首次备份不一致（有漂移），人工核对")
                        entry["result"] = "FAIL_CONFIG_DRIFT"
                        report["devices"][dev] = entry
                        rollout_ok = False
                        break
                else:
                    bak.write_text(cfg["stdout"] + "\n", encoding="utf-8", newline="")
                entry["backup"] = cfg_sha[:16]

                # 2) primary 对齐（US666 TCP 已死→switch 任务 expect_exit_ip 必然失败的根因修复：
                #    对齐到 BWG-HY2=全网当前运行主用；US666 保留 fp=0 二号位，恢复后自动回兜底链。可逆。）
                code, resp = api.post("/api/v1/assignments/batch",
                                      {"device_ids": [dev], "node_id": 8, "is_primary": True, "apply": False})
                item = (resp.get("items") or [{}])[0]
                if code != 200 or item.get("ok") is not True:
                    print(f"  PRIMARY 对齐失败: {code} {item}")
                    entry["result"] = "FAIL_PRIMARY_ALIGN"
                    report["devices"][dev] = entry
                    rollout_ok = False
                    break
                entry["primary_aligned_to"] = 8

                # 2b) 绑定（幂等，apply=false）
                bind_ok = True
                for name, nid in NEW_NODES.items():
                    code, resp = api.post("/api/v1/assignments/batch",
                                          {"device_ids": [dev], "node_id": nid, "is_primary": False, "apply": False})
                    item = (resp.get("items") or [{}])[0]
                    if code != 200 or item.get("ok") is not True:
                        print(f"  BIND FAIL {name}: {code} {item}")
                        bind_ok = False
                        break
                if not bind_ok:
                    entry["result"] = "FAIL_BIND"
                    report["devices"][dev] = entry
                    rollout_ok = False
                    break

                # 3) preview + 断言
                code, body = api.post(f"/api/v1/devices/{dev}/configs/preview")
                if code != 200:
                    print(f"  PREVIEW FAIL {code}: {body}")
                    entry["result"] = "FAIL_PREVIEW"
                    report["devices"][dev] = entry
                    rollout_ok = False
                    break
                masked, rev, ver = body["masked_yaml"], body["revision"], body["next_version"]
                checks = {
                    "cf2d_in": '"CF2D-HK-01"' in masked and '"CF2D-BWG-01"' in masked,
                    "sni": "hk.6849271.xyz" in masked and "usbwg.6849271.xyz" in masked,
                    "no_cold": "8364927" not in masked,
                    "fallback_domestic": "- 223.5.5.5" in masked and "dns.google" not in masked,
                    "tail": masked.rstrip().endswith("- MATCH,DIRECT"),
                    "no_bulk": not re.search(r'name: "PROXY"[\s\S]{0,400}BULK-', masked),
                    "crlf0": "\r" not in masked,
                }
                if not all(checks.values()):
                    print(f"  PREVIEW 断言失败: {checks}")
                    entry["result"] = "FAIL_PREVIEW_ASSERT"
                    entry["checks"] = checks
                    report["devices"][dev] = entry
                    rollout_ok = False
                    break

                # 4) 真实候选 + 掩码等价 + 本机语法
                candidate, sub = build_candidate(masked, cfg["stdout"], params["uuid"])
                eq = remask(candidate) == masked.rstrip("\n")
                if not eq or sub < 2:
                    print(f"  候选构建失败 eq={eq} sub={sub}")
                    entry["result"] = "FAIL_CANDIDATE"
                    report["devices"][dev] = entry
                    rollout_ok = False
                    break
                i, o, e = r.exec_command("cat > /root/cf2d-candidate.yaml && chmod 600 /root/cf2d-candidate.yaml && echo WROTE", timeout=20)
                i.write(candidate)
                i.channel.shutdown_write()
                if "WROTE" not in o.read().decode():
                    entry["result"] = "FAIL_WRITE_CAND"
                    report["devices"][dev] = entry
                    rollout_ok = False
                    break
                t = run(r, "mihomo -t -d /etc/mihomo -f /root/cf2d-candidate.yaml 2>&1 | tail -1", timeout=60)
                if "successful" not in t["stdout"]:
                    print(f"  mihomo -t 失败: {t['stdout'][:150]}")
                    entry["result"] = "FAIL_SYNTAX"
                    report["devices"][dev] = entry
                    rollout_ok = False
                    break

                cand_sha = hashlib.sha256(candidate.encode()).hexdigest()

                # 5) 发布
                run(r, "touch /tmp/selfheal.pause")
                idem = f"cf2d-{dev}-{uuidlib.uuid4().hex[:8]}"
                code, resp = api.post(f"/api/v1/devices/{dev}/configs/apply",
                                      {"revision": rev}, headers={"Idempotency-Key": idem})
                if code != 200:
                    print(f"  APPLY FAIL {code}: {resp}")
                    run(r, "rm -f /tmp/selfheal.pause")
                    entry["result"] = "FAIL_APPLY"
                    report["devices"][dev] = entry
                    rollout_ok = False
                    break
                task_id = resp.get("task_id")
                status = None
                for _ in range(30):
                    code, tl = api.get("/api/v1/tasks?size=10")
                    for tk in tl.get("items", []):
                        if str(tk.get("id")) == str(task_id):
                            status = tk.get("status")
                    if status in ("ok", "failed", "expired"):
                        break
                    time.sleep(4)
                if status != "ok":
                    print(f"  任务未 ok: {status}（保留 selfheal.pause 以便检查）")
                    entry["result"] = f"FAIL_TASK_{status}"
                    report["devices"][dev] = entry
                    rollout_ok = False
                    break

                # 6) 设备验证
                time.sleep(3)
                v = run(r, f"""sha256sum /etc/mihomo/config.yaml | cut -c1-64
cmp -s /etc/mihomo/config.yaml /root/cf2d-candidate.yaml && echo FILE_EQ || echo FILE_NE
""", timeout=30)
                file_ok = cand_sha[:64] in v["stdout"] or "FILE_EQ" in v["stdout"]
                groups_ok, delays, domestic = False, {}, {}
                if file_ok:
                    np = get_json(r, "curl --noproxy '*' -fsS -m 5 http://127.0.0.1:9090/proxies/NODE-PICK")
                    dl = get_json(r, "curl --noproxy '*' -fsS -m 5 http://127.0.0.1:9090/proxies/DOWNLOAD")
                    pr = get_json(r, "curl --noproxy '*' -fsS -m 5 http://127.0.0.1:9090/proxies/PROXY")
                    members = np.get("all", [])
                    groups_ok = (all(x in members for x in ("CF2D-HK-01", "CF2D-BWG-01"))
                                 and members[:1] == ["BWG-HY2-JPTY-01"]
                                 and dl.get("now") == "DIRECT" and pr.get("now") == "AUTO")
                    for name in NEW_NODES:
                        got = []
                        for attempt in range(4):  # 线路不稳时重试；至少一次成功=功能可用，记录
                            d = get_json(r, f"curl --noproxy '*' -fsS -m 30 'http://127.0.0.1:9090/proxies/{name}/delay?timeout=15000&url=https%3A%2F%2Fwww.gstatic.com%2Fgenerate_204'")
                            if isinstance(d.get("delay"), int):
                                got.append(d["delay"])
                                break
                            time.sleep(8)
                        delays[name] = got[0] if got else "FAIL(4/4)"
                    b = run(r, "curl -s -o /dev/null -w '%{http_code}' -m 10 -x http://127.0.0.1:7890 https://www.baidu.com; echo; curl -s -o /dev/null -w '%{http_code}' -m 12 -x http://127.0.0.1:7890 https://www.gstatic.com/generate_204")
                    lines = b["stdout"].split()
                    domestic = {"baidu": lines[0] if lines else "?", "g204": lines[1] if len(lines) > 1 else "?"}
                    domestic_ok = domestic["baidu"] == "200" and domestic["g204"] == "204"
                run(r, "rm -f /tmp/selfheal.pause")
                ok = file_ok and groups_ok and all(isinstance(x, int) for x in delays.values()) and domestic.get("baidu") == "200" and domestic.get("g204") == "204"
                entry.update({"result": "PASS" if ok else "FAIL_VERIFY", "version": ver, "task": task_id,
                              "file_ok": bool(file_ok), "groups_ok": bool(groups_ok), "delays": delays, "domestic": domestic})
                print(f"  v{ver} task={task_id} file={file_ok} groups={groups_ok} delays={delays} domestic={domestic} -> {'PASS' if ok else 'FAIL'}")
                report["devices"][dev] = entry
                if not ok:
                    rollout_ok = False
                    break
                (CTRL / f"{dev}-candidate-real.yaml").write_text(candidate, encoding="utf-8", newline="")
            finally:
                r.close()
        report["finished"] = time.strftime("%F %T")
        report["rollout_ok"] = rollout_ok
        out = CTRL / "cf11-rollout-run2.json"
        if out.exists():
            out = CTRL / f"cf11-rollout-run2-{int(time.time())}.json"
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
        print(f"\nROLLOUT: {'ALL PASS' if rollout_ok else 'STOPPED-ON-FAIL'} -> {out}")
        return 0 if rollout_ok else 1
    finally:
        try:
            api.close()
        finally:
            gz.close()


if __name__ == "__main__":
    sys.exit(main())
