#!/bin/bash
# 出口 VPS 加固（可重复执行）。不改 Reality 密钥 / UUID / 监听端口。
# 适配 sing-box 1.14（禁止 inbound sniff 旧字段，改用 route action）。
# 警告：服务端会拒绝访问本机 IP / 私网。客户端必须把节点 IP 设为 DIRECT，
# 否则 Clash TUN 会把 SSH 绕回节点，表现为 Connection closed。
set -euo pipefail
TS=$(date +%Y%m%d%H%M%S)
CFG=/usr/local/etc/sing-box/config.json
SELF_IP=$(curl -4 -fsS --max-time 5 https://ifconfig.me || echo "159.223.36.119")

echo "[1] backup"
cp -a "$CFG" "${CFG}.bak-${TS}"

echo "[2] remove leftover xray"
rm -f /usr/local/bin/xray /etc/systemd/system/xray.service

echo "[3] patch sing-box (1.14 rule actions)"
python3 - "$CFG" "$SELF_IP" <<'PY'
import json, sys
path, self_ip = sys.argv[1], sys.argv[2]
with open(path) as f:
    d = json.load(f)
d["log"] = {"level": "warn", "timestamp": True}
ib = d["inbounds"][0]
for k in ("sniff", "sniff_override_destination", "domain_strategy"):
    ib.pop(k, None)
d["outbounds"] = [{"type": "direct", "tag": "direct"}]
d["route"] = {
    "rules": [
        {"action": "sniff"},
        {"ip_is_private": True, "action": "reject"},
        {
            "ip_cidr": [f"{self_ip}/32", "169.254.169.254/32", "10.104.0.0/16"],
            "action": "reject",
        },
    ],
    "final": "direct",
}
with open(path, "w") as f:
    json.dump(d, f, indent=2)
    f.write("\n")
PY
/usr/local/bin/sing-box check -c "$CFG"

echo "[4] systemd sandbox"
mkdir -p /etc/systemd/system/sing-box.service.d
cat > /etc/systemd/system/sing-box.service.d/hardening.conf <<'EOF'
[Service]
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
PrivateTmp=true
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectControlGroups=true
RestrictSUIDSGID=true
RestrictRealtime=true
LockPersonality=true
SystemCallArchitectures=native
RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX AF_NETLINK
CapabilityBoundingSet=CAP_NET_BIND_SERVICE CAP_NET_ADMIN
EOF

echo "[5] sshd drop-in"
cat > /etc/ssh/sshd_config.d/99-hardening.conf <<'EOF'
Port 28722
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitRootLogin prohibit-password
PubkeyAuthentication yes
X11Forwarding no
AllowTcpForwarding no
PermitTunnel no
PermitEmptyPasswords no
MaxAuthTries 3
LoginGraceTime 20
MaxSessions 4
MaxStartups 10:30:60
ClientAliveInterval 300
ClientAliveCountMax 2
DebianBanner no
AuthorizedKeysFile .ssh/authorized_keys
EOF
sshd -t

echo "[6] sysctl"
cat > /etc/sysctl.d/99-hardening.conf <<'EOF'
net.ipv4.conf.all.send_redirects = 0
net.ipv4.conf.default.send_redirects = 0
net.ipv4.conf.all.accept_redirects = 0
net.ipv4.conf.default.accept_redirects = 0
net.ipv6.conf.all.accept_redirects = 0
net.ipv6.conf.default.accept_redirects = 0
net.ipv6.conf.all.accept_ra = 0
net.ipv6.conf.default.accept_ra = 0
net.ipv4.conf.all.log_martians = 1
net.ipv4.conf.default.log_martians = 1
net.ipv4.icmp_echo_ignore_broadcasts = 1
net.ipv4.conf.all.rp_filter = 1
net.ipv4.conf.default.rp_filter = 1
fs.suid_dumpable = 0
kernel.kptr_restrict = 2
kernel.dmesg_restrict = 1
net.ipv4.tcp_syncookies = 1
EOF
sysctl --system >/tmp/sysctl-apply.log 2>&1 || true

echo "[7] ufw deny 22"
ufw deny 22/tcp >/dev/null || true

echo "[8] disable unused"
systemctl disable --now ModemManager 2>/dev/null || true
systemctl disable --now multipathd 2>/dev/null || true

echo "[9] fail2ban (Ubuntu 24.04 unit is ssh.service)"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq fail2ban
cat > /etc/fail2ban/filter.d/sshd.local <<'EOF'
[Definition]
journalmatch = _SYSTEMD_UNIT=ssh.service + _COMM=sshd
EOF
cat > /etc/fail2ban/jail.d/sshd.local <<'EOF'
[sshd]
enabled = true
port = 28722
filter = sshd
backend = systemd
maxretry = 4
findtime = 10m
bantime = 1h
EOF
systemctl enable --now fail2ban
systemctl restart fail2ban

echo "[10] restart sing-box, reload ssh"
systemctl daemon-reload
systemctl reload ssh
systemctl restart sing-box
sleep 1
systemctl is-active sing-box
systemctl is-active ssh
systemctl is-active fail2ban
ss -tlnp | grep -E '443|28722'
echo OK
