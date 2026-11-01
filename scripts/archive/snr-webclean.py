#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""SN-R 系列网页登录页密码泄露清理（2026-09-30）
问题：JCG 出厂固件把口令明文存 /etc/config/system（password/superpass），
     登录页模板 login.htm 用它渲染 value=<%=password%>（界面/源码可泄露）。
修复：uci 两项置 'disabled'（占位，认证走 shadow 不受影响）+ 删模板渲染行。
用法：接入任意一台已刷设备（LAN 132.1）后运行 python snr-webclean.py
"""
import paramiko, subprocess, time

UNIFIED = "[REDACTED]"
HOST = "192.168.132.1"
CLEAN_SH = r'''
cp /etc/config/system /etc/config/system.bak-webclean 2>/dev/null
cp /usr/lib/lua/luci/view/admin_web/login.htm /usr/lib/lua/luci/view/admin_web/login.htm.bak-webclean 2>/dev/null
uci set system.@system[0].password=disabled
uci set system.@system[0].superpass=disabled
uci commit system
# 删模板里 value=<%=password%> 填充行及其下一行 type="text"（按模式匹配，不依赖行号）
sed -i '/getElementById("password").value="<%/,+1d' /usr/lib/lua/luci/view/admin_web/login.htm 2>/dev/null
echo "== 验证 =="
grep -c "abc@1234\|SnR@" /etc/config/system
curl -s -m 5 http://127.0.0.1/cgi-bin/luci/ | grep -cE 'value="[^"]+"'
echo "== 密码框 =="
curl -s -m 5 http://127.0.0.1/cgi-bin/luci/ | grep -c 'id="password" value=""'
'''

def main():
    for pw in [UNIFIED, "g0ieFPBScDVX8TYdwgyj55", "ZVFqOhQww205pRmJoLyxrs"]:
        try:
            c = paramiko.SSHClient(); c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            c.connect(HOST, username="root", password=pw, timeout=6, allow_agent=False, look_for_keys=False)
        except Exception:
            continue
        _, o, _ = c.exec_command("grep DEVICE_ID /etc/routermgmt/agent.conf", timeout=10)
        devid = o.read().decode(errors="replace").strip().split("=")[-1]
        _, o, e = c.exec_command(CLEAN_SH, timeout=60)
        out = (o.read()+e.read()).decode(errors="replace")
        print(f"设备 {devid}（口令 {pw[:4]}...）")
        print(out)
        # 验证标准：uci 0 处明文；页面非空 value= 应为 0（除 setlang 等非密码项）；密码框空值存在
        c.close()
        return
    print("无法登录（设备未接入或口令未知）")

if __name__ == "__main__":
    main()
