import paramiko, sys

cli = paramiko.SSHClient()
cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
cli.connect("192.168.1.1", username="root", password="password", timeout=8, allow_agent=False, look_for_keys=False)

cmds = [
    ("release", "cat /etc/openwrt_release | grep -E 'DISTRIB_(ID|RELEASE|ARCH)'"),
    ("kernel/arch", "uname -m; uname -r"),
    ("storage", "df -h / /overlay /tmp 2>/dev/null | grep -v Filesystem"),
    ("memory", "free | head -2"),
    ("pkgmgr", "opkg --version 2>/dev/null || apk --version 2>/dev/null"),
    ("feeds", "grep -v '^#' /etc/opkg/distfeeds.conf 2>/dev/null || cat /etc/apk/repositories 2>/dev/null"),
    ("deps", "opkg list-installed 2>/dev/null | grep -E '^(bash|curl|ca-bundle|ca-certificates|ipset|ip-full|libcap|kmod-tun|unzip|kmod-inet-diag|resolveip|dnsmasq|coreutils|coreutils-nohup|ruby|ruby-yaml|jsonfilter|kmod-nft-tproxy|nftables) ' || true"),
    ("wan", "uci show network.wan 2>/dev/null | head -6"),
    ("github-reach", "curl -sI --max-time 8 https://github.com 2>&1 | head -1"),
    ("koolshare-reach", "curl -sI --max-time 8 https://dl.openwrt.ai 2>&1 | head -1"),
]
for name, cmd in cmds:
    stdin, stdout, stderr = cli.exec_command(cmd, timeout=20)
    out = stdout.read().decode(errors="replace").strip()
    err = stderr.read().decode(errors="replace").strip()
    print(f"===== {name} =====")
    print(out if out else f"(empty{' | ' + err if err else ''})")
cli.close()
