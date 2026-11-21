"""CF09 remaining: domestic/game/mgmt checks + cache persistence + cold-start equivalent + observation.

- Domestic: baidu/antdu/bboluo via R2 proxy-path and direct-path; game 5-domain dnsmasq UCI intact
- Mgmt: platform heartbeat + 排灾口 alive (implicit)
- Cold start: pause selfheal -> restart mihomo -> verify up/config/groups/AUTO persisted -> unpause
"""
from pathlib import Path
import json
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "review/fleet-qos-readonly-audit-20261117.py"
ns = {"__file__": str(HELPER)}
prefix, sep, _ = HELPER.read_text(encoding="utf-8-sig").partition("\ntry:\n")
exec(compile(prefix, "<helper>", "exec"), ns)
connect, run, get_json = ns["connect"], ns["run"], ns["get_json"]

GAME_DOMAINS = ("lol.qq.com", "gamesafe.qq.com", "anticheatexpert.com", "ied-tqos-tgp.qq.com", "ied-tqos.qq.com")


def main() -> int:
    results = {}
    gz = connect("134.175.151.213", 28722, "ubuntu", ns["DEV_PW"])
    try:
        r2 = connect("127.0.0.1", 13223, "root", ns["DEV_PW"], gz)
        try:
            # 1) 国内业务（设备侧显式代理 + 直连两条路径）
            v = run(r2, r"""echo "-- proxy path --"
for u in https://www.baidu.com https://www.antdu.net https://www.bboluo.com; do
  code=$(curl -s -o /dev/null -w '%{http_code}' -m 10 -x http://127.0.0.1:7890 $u)
  echo "$u|$code"
done
echo "-- direct path --"
code=$(curl -s -o /dev/null -w '%{http_code}' -m 10 --noproxy '*' https://www.baidu.com)
echo "baidu-direct|$code"
echo "-- game dnsmasq --"
uci show dhcp | grep -c "server='/"
nslookup lol.qq.com 127.0.0.1 2>/dev/null | grep -A1 "lol.qq.com" | head -2
""", timeout=90)
            print(v["stdout"])
            results["domestic_raw"] = v["stdout"]

            # 2) 管理面：心跳（平台侧稍后统一查）+ 排灾口即本通道（存活）
            v2 = run(r2, "cat /etc/routermgmt/state/hb.json 2>/dev/null | head -c 200; echo; date '+%F %T'")
            print("hb:", v2["stdout"][:200])

            # 3) 缓存持久化 + 冷启动等效（selfheal 暂停护栏）
            v3 = run(r2, """touch /tmp/selfheal.pause && echo PAUSED
ls -la --time-style=+%H:%M:%S /etc/mihomo/cache.db 2>/dev/null
/etc/init.d/mihomo restart >/dev/null 2>&1 || service mihomo restart >/dev/null 2>&1
sleep 8
pgrep -f 'mihomo' | head -3
curl --noproxy '*' -fsS -m 5 http://127.0.0.1:9090/proxies/PROXY | head -c 300
echo
sha256sum /etc/mihomo/config.yaml | cut -c1-16
rm -f /tmp/selfheal.pause && echo UNPAUSED
""", timeout=90)
            print("coldstart:\n", v3["stdout"])

            # 4) 冷启动后组选择与两新节点 delay（观察快照1）
            groups = {}
            for name in ("PROXY", "AUTO", "NODE-PICK", "US-MEDIA", "DOWNLOAD"):
                groups[name] = get_json(r2, "curl --noproxy '*' -fsS -m 5 http://127.0.0.1:9090/proxies/" + name)
            sel = {k: g.get("now") for k, g in groups.items()}
            print("after coldstart selections:", sel)
            results["coldstart_selections"] = sel

            d1 = {}
            for name in ("CF2D-HK-01", "CF2D-BWG-01"):
                d = get_json(r2, f"curl --noproxy '*' -fsS -m 25 'http://127.0.0.1:9090/proxies/{name}/delay?timeout=8000&url=https%3A%2F%2Fwww.gstatic.com%2Fgenerate_204'")
                d1[name] = d.get("delay", d)
            print("delay snapshot1:", d1)
            results["delay_snap1"] = d1

            # 5) 业务无扰动：外网 204 + baidu
            v5 = run(r2, """curl -s -o /dev/null -w 'g204|%{http_code}\\n' -m 12 -x http://127.0.0.1:7890 https://www.gstatic.com/generate_204
curl -s -o /dev/null -w 'baidu|%{http_code}\\n' -m 10 -x http://127.0.0.1:7890 https://www.baidu.com""")
            print("traffic:", v5["stdout"])
            results["traffic"] = v5["stdout"]

            (ROOT / "backups/cf2d-golive-20261118/cf09-rest.json").write_text(
                json.dumps(results, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
            print("saved cf09-rest.json")
            return 0
        finally:
            r2.close()
    finally:
        gz.close()


if __name__ == "__main__":
    sys.exit(main())
