#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
BWG 新 IP 一键落地脚本（2026-10-26 备好，新 IP 出来后执行）
用法: python scripts/bwg-ip-swap.py <新IP>
步骤:
  1) 平台 nodes 表节点4 host -> 新IP
  2) 触发全设备配置重发布（批量 apply 当前主用 LAX，幂等）
  3) 各设备 tunnel.conf VPS3_HOST -> 新IP（单行 sed，安全模式）+ 重启隧道
  4) DMIT 侧 backup.sh 等处 BWG 地址更新
  5) 三站点直连新IP 443 实测 + 优先级复原
前置: 管理台隧道可用（mgmt_api_test.ensure_tunnel）
"""
import sys, time, subprocess, requests, urllib3
sys.path.insert(0, r"F:\vps\scripts")
import mgmt_api_test as M
urllib3.disable_warnings()

OLD_IP = "74.82.203.175"
DEVS = {  # rssh 端口
    "rtr-d3847d": 13222, "rtr-a9bcba": 13223, "rtr-cd473f": 13224,
    "rtr-c46a25": 13225, "rtr-673568": 13226, "rtr-38aa54": 13227,
}
DM = ['ssh', '-p', '28722', '-i', 'C:/Users/Administrator/.ssh/id_rsa_dmit',
      '-o', 'IdentitiesOnly=yes', 'root@179.255.137.64']

def dm(sh):
    return subprocess.run(DM + [sh], capture_output=True, text=True, timeout=90)

def rssh(port, sh):
    r = dm("sshpass -p '[REDACTED]' ssh -o StrictHostKeyChecking=no -o ConnectTimeout=12 "
           f"-p {port} root@127.0.0.1 '{sh}'")
    return (r.stdout or "").strip()

def main(new_ip):
    assert new_ip.count(".") == 3 and not new_ip.endswith(".175"), "IP 参数可疑，中止"
    M.ensure_tunnel(); H = M.H()
    # 1) nodes 表
    r = dm(f'docker exec routermgmt-db-1 psql -U postgres -d routermgmt -c '
           f'"UPDATE nodes SET host=\'{new_ip}\', health_status=\'unknown\' WHERE id=4;"')
    assert "UPDATE 1" in r.stdout, "nodes 更新失败: " + r.stdout
    print("[1/5] nodes 表已更新 ->", new_ip)
    # 2) 重发布（批量 apply 幂等，主用仍 LAX）
    online = [d for d, p in DEVS.items() if p in (13222, 13223, 13224, 13225, 13226, 13227)]
    rr = requests.post(M.BASE + "/api/v1/assignments/batch", headers=H,
                       json={"device_ids": online, "node_id": 5, "is_primary": True, "apply": True},
                       verify=False, timeout=30)
    tids = [it["task_id"] for it in rr.json()["items"]]
    t0 = time.time()
    while time.time() - t0 < 240:
        tl = requests.get(M.BASE + "/api/v1/tasks?page_size=100", headers=H, verify=False, timeout=15).json()
        st = {t: next((x["status"] for x in (tl.get("items") or []) if x["id"] == t), "?") for t in tids}
        if all(s in ("ok", "failed", "rolled_back", "expired", "timeout") for s in st.values()):
            break
        time.sleep(10)
    print("[2/5] 配置重发布:", st)
    # 3) 设备 tunnel.conf VPS3_HOST（单行替换，安全 sed）+ 隧道重启
    for did, port in DEVS.items():
        out = rssh(port, f"sed -i 's/^VPS3_HOST=.*/VPS3_HOST={new_ip}/' /etc/routermgmt/tunnel.conf && "
                        "grep -c '^VPS3_HOST=" + new_ip + "' /etc/routermgmt/tunnel.conf")
        print(f"  {did} VPS3_HOST:", out or "FAIL(设备不在线?)")
    print("[3/5] 设备温备端点已更新（重启隧道在验证后统一做）")
    # 4) DMIT 侧脚本地址
    r = dm(f"grep -rl '{OLD_IP}' /opt/mgmt/*.sh /root/bin/*.sh 2>/dev/null")
    files = (r.stdout or "").split()
    for f in files:
        dm(f"sed -i 's/{OLD_IP}/{new_ip}/g' {f}")
    print("[4/5] DMIT 侧引用更新:", files or "无")
    # 5) 三站点直连验证
    for port in (13222, 13224, 13223):
        out = rssh(port, f"curl -sk -m 6 -o /dev/null -w '%{{http_code}}' https://{new_ip}/ 2>/dev/null")
        print(f"  端口{port} 直连 {new_ip}:443 -> {out}")
    print("[5/5] 完成。验证全 4xx/2xx 后：设备 /etc/init.d/mgmt-tunnel restart（逐台），"
          "并把 BWG 用 assignments/batch 试切一台验证代理出口。")
    # 优先级复原
    for d in online:
        dm(f'docker exec routermgmt-db-1 psql -U postgres -d routermgmt -c "'
           f'UPDATE node_assignments SET failover_priority=0 WHERE device_id=\'{d}\' AND node_id=5; '
           f'UPDATE node_assignments SET failover_priority=1 WHERE device_id=\'{d}\' AND node_id=3; '
           f'UPDATE node_assignments SET failover_priority=2 WHERE device_id=\'{d}\' AND node_id=2;"')
    print("优先级已复原（5主用/3→1/2→2）")

if __name__ == "__main__":
    main(sys.argv[1])
