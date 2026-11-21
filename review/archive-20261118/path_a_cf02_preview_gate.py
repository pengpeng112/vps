"""CF02 preview gate: R2 full assertions vs field baseline; R5/R9 template invariants.

Run after new gz-api deployed. No apply here — preview only.
"""
from pathlib import Path
import json
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from path_a_gz_api import GzApi  # noqa: E402

OUT = Path("backups/cf2d-golive-20261118/cf02-preview-gate.json")


def parse_yaml_light(text: str) -> dict:
    """Minimal extraction for assertions (avoid yaml dep weight): group members via regex."""
    groups = {}
    pattern = re.compile(r'^\s*- name: "(PROXY|AUTO|NODE-PICK|US-MEDIA|DOWNLOAD|CN-MEDIA)"')
    current = None
    for line in text.splitlines():
        m = pattern.match(line)
        if m:
            current = m.group(1)
            groups[current] = []
            continue
        if current:
            m2 = re.match(r'^\s+- "?([A-Za-z0-9_.-]+)"?\s*$', line)
            if m2:
                groups[current].append(m2.group(1))
    return groups


def check(name: str, ok: bool, detail: str = ""):
    print(f"  {'PASS' if ok else 'FAIL'} {name} {detail}")
    return ok


def main() -> int:
    api = GzApi()
    results = {}
    try:
        api.login()
        print("login OK")
        all_ok = True
        for dev, note in (("rtr-a9bcba", "R2 试点（全断言）"), ("rtr-c46a25", "R5"), ("rtr-101be0", "R9")):
            code, body = api.post(f"/api/v1/devices/{dev}/configs/preview")
            if code != 200:
                print(f"{dev}: preview HTTP {code} {body}")
                all_ok = False
                continue
            y = body.get("masked_yaml", "")
            entry = {"http": code, "revision": body.get("revision"), "next_version": body.get("next_version"),
                     "sha256": body.get("sha256"), "diff_lines": [d.get("new", "") for d in body.get("diff", [])]}
            print(f"\n== {dev} ({note}) next_version={entry['next_version']} diff={len(body.get('diff', []))} 行 ==")
            ok = True
            ok &= check("crlf=0", "\r" not in y)
            ok &= check("fallback 国产", re.search(r"^  fallback:\n    - 223\.5\.5\.5\n    - 119\.29\.29\.29", y, re.M) is not None)
            ok &= check("无国外DoH残留", "dns.google" not in y and "cloudflare-dns.com" not in y)
            rules = re.findall(r"^\s*- (MATCH,\w+|GEOSITE,geolocation-!cn,PROXY|GEOIP,CN,\w+)$", y, re.M)
            ok &= check("尾二条=geolocation-!cn,PROXY", "GEOSITE,geolocation-!cn,PROXY" in rules, str(rules[-3:]))
            ok &= check("末条=MATCH,DIRECT", rules and rules[-1] == "MATCH,DIRECT")
            groups = parse_yaml_light(y)
            bulk_in = [g for g, m in groups.items() if g in ("PROXY", "AUTO", "NODE-PICK") and any("BULK" in x for x in m)]
            ok &= check("PROXY/AUTO/NODE-PICK 无 BULK", not bulk_in, str(bulk_in))
            ok &= check("US-MEDIA 尾部 NODE-PICK", groups.get("US-MEDIA", [""])[-1] in ("NODE-PICK", "AIRPORT"))
            ok &= check("DOWNLOAD=[DIRECT,PROXY]", groups.get("DOWNLOAD") == ["DIRECT", "PROXY"])
            ok &= check("无冷备域", "8364927" not in y and "83649271" not in y)
            ok &= check("无QoS/新协议夹带", "AnyTLS" not in y and "port hopping" not in y.lower() and "hop_ports" not in y)
            if dev == "rtr-a9bcba":
                # 旧主用顺序：BWG-HY2 在 NODE-PICK 首位（primary 不变）
                np = groups.get("NODE-PICK", [])
                ok &= check("R2 NODE-PICK 首位=BWG-HY2-JPTY-01", np and np[0] == "BWG-HY2-JPTY-01", str(np[:4]))
                # 与现场 diff 仅 DNS fallback 段（masked 与现场原件逐行比）
                field = Path("backups/cf2d-golive-20261118/r2-config-orig-v39.yaml").read_text(encoding="utf-8")
                field_lines = [l for l in field.splitlines() if l.strip() and not l.strip().startswith("#")]
                new_lines = [l for l in y.splitlines() if l.strip() and not l.strip().startswith("#")]
                import difflib
                delta = [l for l in difflib.unified_diff(field_lines, new_lines, lineterm="", n=0) if l[:1] in "+-"]
                entry["vs_field_delta_count"] = len(delta)
                print("   vs 现场 diff 行（掩码前后凭据行会不同，仅计数）:", len(delta))
            results[dev] = {**entry, "checks_ok": bool(ok)}
            all_ok &= ok
        OUT.write_text(json.dumps(results, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
        print(f"\nCF02 GATE: {'ALL PASS' if all_ok else 'FAIL'} （证据 {OUT}）")
        return 0 if all_ok else 1
    finally:
        api.close()


if __name__ == "__main__":
    sys.exit(main())
