"""CF06 stage1: build REAL candidate config, prove masked-equality, mihomo -t on R2.

Real-value sources: R2 field config (existing nodes) + .cf-node-params.json (new CF nodes).
No service impact: config test only (-t), temp file, self-heal untouched.
"""
from pathlib import Path
import json
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from path_a_gz_api import GzApi  # noqa: E402
import yaml  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
CTRL = ROOT / "backups/cf2d-golive-20261118"
HELPER = ROOT / "review/fleet-qos-readonly-audit-20261117.py"


def mask_mid(value: str, keep_head: int, keep_tail: int) -> str:
    if value == "":
        return ""
    if len(value) <= keep_head + keep_tail + 2:
        return "****"
    return value[:keep_head] + "..." + (value[len(value) - keep_tail:] if keep_tail else "")


def mask_line(key: str, value: str) -> str:
    k = key.lower()
    if k in ("sid", "short-id"):
        return mask_mid(value, 2, 2)
    if k in ("uuid", "pbk", "public-key"):
        return mask_mid(value, 4, 4)
    if k in ("password", "hysteria2-password"):
        return mask_mid(value, 2, 2)
    return value


def main() -> int:
    # 1) preview（拿权威结构）
    api = GzApi()
    try:
        api.login()
        code, body = api.post("/api/v1/devices/rtr-a9bcba/configs/preview")
        if code != 200:
            print("preview fail", code, body)
            return 1
        masked = body["masked_yaml"]
        revision = body["revision"]
        next_version = body["next_version"]
        print(f"preview: next_version={next_version} revision={revision[:16]}…")
    finally:
        api.close()

    # 2) 从现场原件建立 真实值映射（按节点名）
    field = yaml.safe_load((CTRL / "r2-config-orig-v39.yaml").read_text(encoding="utf-8"))
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
    params = json.loads((ROOT / "backups/.cf-node-params.json").read_text(encoding="utf-8-sig"))
    real["CF2D-HK-01"] = {"uuid": params["uuid"]}
    real["CF2D-BWG-01"] = {"uuid": params["uuid"]}

    # 3) 逐行还原掩码：proxies 块内按当前节点名归属
    out_lines = []
    current_node = None
    substituted = set()
    for line in masked.splitlines():
        m_node = re.match(r'\s*- name: "([^"]+)"', line)
        if m_node and m_node.group(1) in real:
            current_node = m_node.group(1)
        m_sec = re.match(r"^(\s+(uuid|password|public-key|short-id): )(\S+)$", line)
        if m_sec and current_node:
            key, masked_val = m_sec.group(2), m_sec.group(3)
            real_val = real[current_node].get(key)
            if real_val and mask_line(key, real_val) == masked_val:
                out_lines.append(m_sec.group(1) + real_val)
                substituted.add((current_node, key))
                continue
        out_lines.append(line)
    candidate = "\n".join(out_lines) + "\n"

    # 4) 掩码等价证明：mask(candidate) == masked
    re_masked = []
    for line in candidate.splitlines():
        m_sec = re.match(r"^(\s+(uuid|password|public-key|short-id): )(\S+)$", line)
        if m_sec:
            re_masked.append(m_sec.group(1) + mask_line(m_sec.group(2), m_sec.group(3)))
        else:
            re_masked.append(line)
    equal = "\n".join(re_masked) == masked.rstrip("\n")
    print(f"掩码等价: {'PASS' if equal else 'FAIL'}；还原字段数={len(substituted)}")
    if not equal:
        for a, b in zip(re_masked, masked.rstrip('\n').splitlines()):
            if a != b:
                print("  DIFF:", a[:60], "|", b[:60])
                break
        return 1
    missing = [k for k in (("CF2D-HK-01", "uuid"), ("CF2D-BWG-01", "uuid")) if k not in substituted]
    if missing:
        print("FAIL: 新节点 uuid 未还原", missing)
        return 1

    # 4b) 无残留掩码值断言（任何未还原的 ... 掩码都意味着凭据映射失败）
    leftover = [l for l in out_lines if re.match(r"^\s+(uuid|password|public-key|short-id): \S*\.\.\.\S*$", l)]
    if leftover:
        print(f"FAIL: {len(leftover)} 行掩码未还原（映射失败）")
        for l in leftover[:5]:
            print("   ", re.sub(r"\S+\.\.\.\S+", "<masked>", l))
        return 1

    # 5) 落受控目录 + 真机 mihomo -t
    cand_path = CTRL / "r2-v40-candidate-real.yaml"
    cand_path.write_text(candidate, encoding="utf-8", newline="")
    print("candidate 已存受控目录:", cand_path.name)

    ns = {"__file__": str(HELPER)}
    prefix, sep, _ = HELPER.read_text(encoding="utf-8-sig").partition("\ntry:\n")
    exec(compile(prefix, "<helper>", "exec"), ns)
    connect, run = ns["connect"], ns["run"]
    gz = connect("134.175.151.213", 28722, "ubuntu", ns["DEV_PW"])
    try:
        sftp = gz.open_sftp()
        sftp.put(str(cand_path), "/tmp/r2-v40-candidate.yaml")
        sftp.close()
        r2 = connect("127.0.0.1", 13223, "root", ns["DEV_PW"], gz)
        try:
            r = run(r2, """umask 077
mv /tmp/r2-v40-candidate.yaml /root/cf2d-candidate.yaml
sha256sum /root/cf2d-candidate.yaml | cut -c1-16
mihomo -t -d /etc/mihomo -f /root/cf2d-candidate.yaml 2>&1 | tail -3
echo "TEST_RC=$?"
""", timeout=60)
            print("R2 mihomo -t:\n", r["stdout"], r["stderr"][:200])
            if "TEST_RC=0" not in r["stdout"] or "successful" not in r["stdout"]:
                print("FAIL: 真机语法验证未通过")
                return 1
            (CTRL / "cf06-stage1.json").write_text(
                json.dumps({"revision": revision, "next_version": next_version,
                            "masked_equivalence": True, "syntax_test": "PASS",
                            "candidate_sha256_head": __import__("hashlib").sha256(candidate.encode()).hexdigest()[:16]},
                           ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
            print("CF06 STAGE1 PASS（掩码等价 + 真机语法）")
            return 0
        finally:
            r2.close()
    finally:
        gz.close()


if __name__ == "__main__":
    sys.exit(main())
