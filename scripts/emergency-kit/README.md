# 应急修复工具包（2026-10-31 更新：目标=腾讯云广州）

## 用途
当站点"国内能上、国外不通"（线路国际方向故障）时，通过向日葵远程到站点电脑，
双击脚本自动将路由器管理隧道切到腾讯云广州（134.175.151.213:28722）、
出国出口切到 154.36.139.155，并部署 respawn 修复 + self-heal 自愈脚本。

## 使用方法（3 个文件拷到目标电脑同一文件夹，双击）
| 文件 | 说明 |
|---|---|
| **fix-router-auto.bat** | **双击运行**（全自动零输入版，推荐） |
| **fix-cmds.txt** | 修复命令集（bat 自动读取，需同目录） |
| **plink.exe** | SSH 工具（已内置） |

备用：fix-router.bat（自动版失败时手动输密码 [REDACTED]）
诊断：diag.bat（只查不改，输出回传即可）——含广州连通性检查

## 成功判读
```
DEVICE: rtr-XXXXXX          ← 确认设备
HEALTH: 200                 ← 远程管理恢复 ✅
EGRESS: 154.36.139.155      ← 国外流量走新出口 ✅
AGENT: 1                    ← 心跳正常 ✅
CRON: 1                     ← self-heal 已部署 ✅
```

## 失败判读
- AUTO MODE FAILED = 再跑一遍；不行换 fix-router.bat（手动输密码）
- HEALTH: 000 = 该线路连广州也不通（纯线路死，等运营商）
- 窗口内看到 EGRESS: FAIL 但 HEALTH: 200 = 隧道通、仅出口节点问题，另报

## 修改 VPS 目标
改 fix-cmds.txt 中的：
```
sed -i 's/^VPS_HOST=.*/VPS_HOST=新IP/; s/^VPS_PORT=.*/VPS_PORT=新端口/'
```
出口节点同理改其中 154.36.139.155 的 sed 行。

## 已实战验证
- 10-29：昌乐 R5 / 安丘 R3 / 临朐 R1（66 云 GIA 版救活）
- 10-31：R8（广州 134.175.151.213 版 + respawn 根因修复 + self-heal + 时钟/心跳修复）

## archive/ 子目录
历史版本与站点专用一次性脚本（R8 时钟/心跳/URL 修复、旧 66 云 ps1、10-30 快照 rar）——
留着备查，不影响使用；新站点只需上面 3 个文件。

## 原理
把路由器 tunnel.conf 的 VPS_HOST 切到广州，重启 mgmt-tunnel；respawn 参数 5→0 根治
反复拉起；self-heal.sh 每分钟只重启死服务（永不重启设备）；原配置自动备份（tunnel.conf.bak）。
