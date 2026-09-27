"""统一优化 M00 基线采集（只读）。凭据仅在内存中；输出白名单字段，不含节点凭证/密码。
复用 09-27 晨间采集器的路由器指纹 pin；VPS 走本机 known_hosts 严格校验。
"""
from pathlib import Path
import base64, hashlib, json, re, subprocess, sys, concurrent.futures
import paramiko

HERE = Path(__file__).parent
ROOT = HERE.parents[3]
PINS = {'192.168.2.1': '9kjT5GtvqKLBPgzi+TfVI/Im7ytktwsV1MaaMrzW4uM',
        '192.168.132.1': 'ok3TGs34zTRpyJjV3qE1v7x5Bt6VmgfqoIdjelNK+rQ'}

class PinPolicy(paramiko.MissingHostKeyPolicy):
    def missing_host_key(self, client, host, key):
        actual = base64.b64encode(hashlib.sha256(key.asbytes()).digest()).decode().rstrip('=')
        if PINS.get(host) != actual:
            raise RuntimeError('host key mismatch: ' + host)
        client.get_host_keys().add(host, key.get_name(), key)

def router_pw(which):
    src = (ROOT / '登录信息与配置总表-20260917.md').read_text(encoding='utf-8')
    title, row = {'athena': ('## 3.', '| SSH'), 'snr1': ('## 3b.', '| 管理页')}[which]
    start = src.index(title); end = src.find('\n## ', start + 1)
    sec = src[start:end if end != -1 else len(src)]
    return re.findall(r'`([^`]+)`', next(l for l in sec.splitlines() if l.startswith(row)))[-1]

def rconnect(name):
    host = {'athena': '192.168.2.1', 'snr1': '192.168.132.1'}[name]
    c = paramiko.SSHClient(); c.set_missing_host_key_policy(PinPolicy())
    c.connect(host, username='root', password=router_pw(name), look_for_keys=False,
              allow_agent=False, timeout=15)
    return c

def vconnect(host, port, user, keyfile):
    c = paramiko.SSHClient(); c.load_system_host_keys()
    c.set_missing_host_key_policy(paramiko.RejectPolicy())
    c.connect(host, port=port, username=user, key_filename=str(Path.home() / '.ssh' / keyfile),
              look_for_keys=False, allow_agent=False, timeout=20)
    return c

def cmd(c, text, timeout=60):
    _, o, e = c.exec_command(text, timeout=timeout)
    return (o.read() + e.read()).decode(errors='replace')

def router_baseline(name):
    c = rconnect(name)
    try:
        r = {'device': name}
        r['basic'] = cmd(c, "date '+%Y-%m-%dT%H:%M:%S%z'; cat /etc/TZ 2>/dev/null; uptime; cat /proc/loadavg")
        r['network'] = cmd(c, "ip -4 addr show | grep -E 'inet |^[0-9]'; ip -4 route; cat /etc/resolv.conf; ifstatus wan 2>/dev/null | head -40")
        r['resources'] = cmd(c, "grep -E 'MemTotal|MemAvailable' /proc/meminfo; cat /proc/sys/net/netfilter/nf_conntrack_count 2>/dev/null; cat /proc/sys/net/netfilter/nf_conntrack_max 2>/dev/null; head -1 /proc/net/dev; awk 'NR>2{print $1,$3,$11}' /proc/net/dev")
        r['services'] = cmd(c, "for s in mihomo routermgmt wan-watch mgmt-tunnel dropbear uhttpd cron; do printf '%s=' $s; [ -x /etc/init.d/$s ] && /etc/init.d/$s enabled >/dev/null 2>&1 && printf 'enabled,' ; /etc/init.d/$s status 2>&1 | head -1; done")
        r['ports'] = cmd(c, "netstat -tlnp 2>/dev/null | grep LISTEN")
        r['mgmt_dir'] = cmd(c, "ls -la /etc/routermgmt/ 2>/dev/null | head -25; ls -la /etc/routermgmt/logs/ 2>/dev/null | head -15")
        r['agent_ver'] = cmd(c, "cat /etc/routermgmt/version 2>/dev/null; md5sum /usr/bin/routermgmt 2>/dev/null || md5sum /etc/routermgmt/routermgmt 2>/dev/null; ps w | grep -E 'routermgmt|mihomo|ssh .*18443|mgmt' | grep -v grep")
        r['mgmt_tunnel'] = cmd(c, "cat /etc/init.d/mgmt-tunnel 2>/dev/null | sed -n '1,30p'; ps w | grep 'ssh ' | grep -v grep")
        r['cron'] = cmd(c, "crontab -l 2>/dev/null")
        r['mihomo_ver'] = cmd(c, "/usr/bin/mihomo -v 2>/dev/null | head -3; PID=$(pidof mihomo); tr '\\0' ' ' < /proc/$PID/cmdline; echo; ls -la /etc/mihomo/run.yaml /etc/mihomo/config.yaml 2>/dev/null")
        r['mihomo_cfg_hash'] = cmd(c, "sha256sum /etc/mihomo/run.yaml /etc/mihomo/config.yaml 2>/dev/null | awk '{print $1, $2}'")
        # API: 运行组选择（不含 proxies 节点凭证）
        try:
            groups = json.loads(cmd(c, 'curl -fsS --max-time 5 http://127.0.0.1:9090/proxies'))
            r['groups_now'] = {k: {'type': v.get('type'), 'now': v.get('now')}
                               for k, v in groups.get('proxies', {}).items() if v.get('type') in ('Selector', 'URLTest', 'Fallback', 'LoadBalance')}
        except Exception as ex:
            r['groups_now'] = 'api_fail: %s' % ex
        # 运行规则条数与 gstatic/google-cn 命中位置（不存全文）
        try:
            rules = json.loads(cmd(c, 'curl -fsS --max-time 5 http://127.0.0.1:9090/rules'))
            rl = rules.get('rules', [])
            r['rules_meta'] = {'count': len(rl),
                               'gstatic_hits': [i for i, x in enumerate(rl) if 'gstatic' in (x.get('payload') or '')][:5],
                               'google_cn_pos': [i for i, x in enumerate(rl) if x.get('payload') == 'google-cn'][:3]}
        except Exception as ex:
            r['rules_meta'] = 'api_fail: %s' % ex
        r['dns_now'] = cmd(c, "curl -fsS --max-time 5 'http://127.0.0.1:9090/dns/query?name=www.gstatic.com&type=A' | head -c 400; echo; curl -fsS --max-time 5 'http://127.0.0.1:9090/dns/query?name=www.baidu.com&type=A' | head -c 400")
        r['backup_state'] = cmd(c, "ls -la /etc/routermgmt/backups/ 2>/dev/null | head; crontab -l 2>/dev/null | grep -i backup")
        r['firewall_head'] = cmd(c, ('nft list ruleset 2>/dev/null | grep -nE "dport (443|22)|gstatic|mihomo|RETURN|redirect" | head -30') if name == 'athena'
                                 else 'iptables-save -c 2>/dev/null | grep -nE "443|DROP|RETURN|REDIRECT|159.223|43.134" | head -40')
        return r
    finally:
        c.close()

def vps_baseline(tag, host, port, user, key):
    c = vconnect(host, port, user, key)
    try:
        r = {'device': tag}
        r['basic'] = cmd(c, "date '+%Y-%m-%dT%H:%M:%S%z'; uptime; timedatectl 2>/dev/null | head -5")
        r['listen'] = cmd(c, "ss -tlnp | grep -E '127.0.0.1|0.0.0.0|::1' | head -30")
        r['rev_ssh'] = cmd(c, "ps aux | grep -E 'sshd: .*@|sshd -R' | grep -v grep | head -15")
        r['resources'] = cmd(c, "free -m | head -3; df -h / | tail -1; cat /proc/loadavg")
        r['services'] = cmd(c, tag == 'do' and
                            "cd /opt/mgmt/deploy && docker compose ps --format '{{.Name}} {{.State}} {{.Status}}' 2>/dev/null; systemctl is-active sing-box fail2ban docker 2>/dev/null; ufw status 2>/dev/null | head -10" or
                            "systemctl is-active sing-box ufw 2>/dev/null; ufw status 2>/dev/null | head -10")
        r['jump'] = cmd(c, "id jump 2>/dev/null; cat /home/jump/.ssh/authorized_keys 2>/dev/null | awk '{print $1, $3}' | head -5; grep -rn PermitListen /etc/ssh/sshd_config /etc/ssh/sshd_config.d/ 2>/dev/null | head")
        r['backup'] = cmd(c, "ls -lt /opt/mgmt/backups/*.tar.gz 2>/dev/null | head -4; tail -3 /opt/mgmt/backups/backup.log 2>/dev/null; crontab -l 2>/dev/null | grep -v '^#' | head")
        r['kernel_net'] = cmd(c, "sysctl net.ipv4.tcp_congestion_control net.core.default_qdisc 2>/dev/null; cat /proc/net/snmp | grep -A1 '^Tcp:' | tail -1")
        return r
    finally:
        c.close()

def pc_baseline():
    def ps(cmdline):
        return subprocess.run(['powershell', '-NoProfile', '-Command', cmdline],
                              capture_output=True, timeout=120,
                              encoding='utf-8', errors='replace').stdout
    r = {'device': 'pc'}
    r['time'] = ps("Get-Date -Format 'yyyy-MM-ddTHH:mm:sszzz'")
    r['route'] = ps("Get-NetRoute -AddressFamily IPv4 -DestinationPrefix '0.0.0.0/0' | Select-Object ifIndex,NextHop,RouteMetric,InterfaceMetric | Format-Table -HideTableHeaders | Out-String")
    r['adapter'] = ps("Get-NetAdapter | Where-Object Status -eq 'Up' | Select-Object Name,InterfaceDescription,LinkSpeed | Format-Table -AutoSize | Out-String")
    r['dns'] = ps("Get-DnsClientServerAddress -AddressFamily IPv4 | Where-Object ServerAddresses | Select-Object InterfaceAlias,ServerAddresses | Format-Table -AutoSize | Out-String")
    r['ip'] = ps("Get-NetIPAddress -AddressFamily IPv4 | Where-Object {$_.IPAddress -notlike '169.254*' -and $_.IPAddress -ne '127.0.0.1'} | Select-Object InterfaceAlias,IPAddress,PrefixLength | Format-Table -AutoSize | Out-String")
    r['tasks'] = ps("Get-ScheduledTask -TaskName 'AIHMS-JumpTunnel*' -ErrorAction SilentlyContinue | Select-Object TaskName,State | Format-Table -AutoSize | Out-String; (Get-ScheduledTaskInfo -TaskName 'AIHMS-JumpTunnel' -ErrorAction SilentlyContinue).LastTaskResult")
    r['tunnel_proc'] = subprocess.run(['powershell', '-NoProfile', '-Command',
                                       "Get-CimInstance Win32_Process -Filter \"Name like '%ssh%'\" | Select-Object ProcessId,CommandLine | Format-List | Out-String"],
                                      capture_output=True, timeout=60,
                                      encoding='utf-8', errors='replace').stdout
    return r

def http_probe(url, n=3):
    out = []
    for _ in range(n):
        p = subprocess.run(['curl.exe', '--noproxy', '*', '--http1.1', '-sS', '-o', 'NUL',
                            '--connect-timeout', '4', '--max-time', '8', '-w',
                            'code=%{http_code} ip=%{remote_ip} tcp=%{time_connect} tls=%{time_appconnect} total=%{time_total}',
                            url], capture_output=True, text=True, timeout=20)
        out.append((p.stdout.strip() or ('exit=%d' % p.returncode)) + (' curl_exit=%d' % p.returncode))
    return out

if __name__ == '__main__':
    result = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as ex:
        futs = {'pc': ex.submit(pc_baseline),
                'athena': ex.submit(router_baseline, 'athena'),
                'snr1': ex.submit(router_baseline, 'snr1'),
                'do': ex.submit(vps_baseline, 'do', '159.223.36.119', 28722, 'root', 'id_ed25519_digitalocean'),
                'tx': ex.submit(vps_baseline, 'tx', '43.134.46.44', 22, 'ubuntu', 'id_ed25519_tencent')}
        for k, f in futs.items():
            try:
                result[k] = f.result()
                print('OK', k)
            except Exception as ex2:
                result[k] = {'error': repr(ex2)}
                print('FAIL', k, repr(ex2))
    # PC 透明路径公开目标复现（每目标3次；20次完整验收在 M02 批次做）
    result['pc_http'] = {u: http_probe(u) for u in
                         ['https://www.gstatic.com/generate_204', 'https://www.google.com/generate_204', 'https://www.baidu.com/']}
    (HERE / 'baseline.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print('saved baseline.json')
