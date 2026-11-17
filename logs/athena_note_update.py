"""Update Athena rtr-5e1a8d location note after successful migration."""
import os

import paramiko

PW = os.environ.get("DEVPW")
NOTE = (
    "WAN=医院内网 10.1.2.105(+白名单.18)，LAN=192.168.2.1/24。"
    "管理面 2026-11-05 迁 GZ 完成（13228 回连+心跳+18443 全验；"
    "原三端点 DMIT 被墙/TX 死/BWG 28722 不监听致断）。"
    "代理出口仍 DO 节点(VPS-SG-01)待另案。备份 tunnel.conf.bak-gz-20261105"
)
sql = f"UPDATE devices SET location_note='{NOTE}' WHERE id='rtr-5e1a8d';"

gz = paramiko.SSHClient()
gz.set_missing_host_key_policy(paramiko.AutoAddPolicy())
gz.connect("134.175.151.213", port=28722, username="ubuntu", password=PW,
           timeout=15, look_for_keys=False, allow_agent=False)
cmd = "sudo -u postgres psql routermgmt -t -A -c \"" + sql.replace('"', '\\"') + "\" 2>&1"
_, o, _ = gz.exec_command(cmd, timeout=25)
print(o.read().decode().strip())
_, o, _ = gz.exec_command(
    "sudo -u postgres psql routermgmt -t -A -F'|' -c "
    "\"SELECT id,location_note FROM devices WHERE id='rtr-5e1a8d';\"", timeout=20)
print("---verify---")
print(o.read().decode().strip()[:500])
gz.close()
