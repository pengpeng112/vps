"""CF04 (idempotent node create) + CF05 (bind R2 only, apply=false)."""
from pathlib import Path
import json
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from path_a_gz_api import GzApi  # noqa: E402

DEV = "rtr-a9bcba"
OUT = Path("backups/cf2d-golive-20261118/cf04-cf05.json")


def main() -> int:
    params = json.loads(Path("backups/.cf-node-params.json").read_text(encoding="utf-8-sig"))
    nodes_spec = [
        {"name": "CF2D-HK-01", "host": "104.18.32.7", "sni": "hk.6849271.xyz"},
        {"name": "CF2D-BWG-01", "host": "162.159.135.42", "sni": "usbwg.6849271.xyz"},
    ]
    api = GzApi()
    record = {"steps": []}
    try:
        api.login()

        # CF04: 幂等——先查同名
        code, listing = api.get("/api/v1/nodes?size=100")
        existing = {n["name"]: n for n in listing.get("items", [])}
        created = {}
        for spec in nodes_spec:
            name = spec["name"]
            if name in existing:
                n = existing[name]
                ok = (n["host"] == spec["host"] and n["port"] == 2053
                      and n.get("proto") == "vless-ws" and n.get("purpose") == "ordinary")
                print(f"  EXISTS {name} id={n['id']} host={n['host']} 一致性={'OK' if ok else 'CONFLICT'}")
                if not ok:
                    print("ABORT: 同名节点参数不匹配，停下登记冲突")
                    return 1
                created[name] = n["id"]
            else:
                body = {
                    "name": name, "proto": "vless-ws", "purpose": "ordinary",
                    "host": spec["host"], "port": 2053, "priority": -100, "enabled": True,
                    "params": {
                        "uuid": params["uuid"], "sni": spec["sni"], "ws_host": spec["sni"],
                        "ws_path": params["ws_path"], "flow": "", "fp": "chrome",
                    },
                }
                code, resp = api.post("/api/v1/nodes", body)
                if code != 200:
                    print(f"  CREATE {name} HTTP {code}: {resp}")
                    return 1
                print(f"  CREATED {name} id={resp.get('id')} host={resp.get('host')} port={resp.get('port')} purpose={resp.get('purpose')}")
                record["steps"].append({"op": "node_create", "name": name, "id": resp.get("id"),
                                        "host": resp.get("host"), "port": resp.get("port"), "http": code})
                created[name] = resp["id"]

        # CF05: 只绑 R2，apply=false（绑定前集合从 nodes 列表 bound_devices 侧无法取单台，
        # R2 旧绑定已在 CF01 基线记录；apply=false 不触发发布）
        code, before = api.get("/api/v1/nodes?size=100")
        for name, nid in created.items():
            code, resp = api.post("/api/v1/assignments/batch",
                                  {"device_ids": [DEV], "node_id": nid, "is_primary": False, "apply": False})
            items = resp.get("items", [])
            item = items[0] if items else {}
            ok = item.get("ok") is True and item.get("device_id") == DEV and not item.get("task_id")
            print(f"  BIND {name}->{DEV} apply=false: http={code} ok={item.get('ok')} task={item.get('task_id','无')} {'OK' if ok else 'FAIL'}")
            if not ok:
                print("ABORT: 绑定未按预期成功")
                return 1
            record["steps"].append({"op": "bind", "node": name, "node_id": nid, "http": code, "item": item})

        OUT.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
        print("CF04+CF05 OK ->", OUT)
        return 0
    finally:
        api.close()


if __name__ == "__main__":
    sys.exit(main())
