"""Task-specific read-only SSH collector. Credentials stay in memory; output allowlisted.
Router pins were first observed on the local LAN on 2026-09-27; not independently attested.
Does not write remote files, execute project scripts, or mutate proxy selections.
"""
from pathlib import Path
import base64, hashlib, json, re, concurrent.futures
import paramiko, yaml

ROOT = Path(__file__).resolve().parents[3]
PINS = {'192.168.2.1':'9kjT5GtvqKLBPgzi+TfVI/Im7ytktwsV1MaaMrzW4uM',
        '192.168.132.1':'ok3TGs34zTRpyJjV3qE1v7x5Bt6VmgfqoIdjelNK+rQ'}
class PinPolicy(paramiko.MissingHostKeyPolicy):
    def missing_host_key(self, client, host, key):
        actual = base64.b64encode(hashlib.sha256(key.asbytes()).digest()).decode().rstrip('=')
        if PINS.get(host) != actual: raise RuntimeError('host key mismatch')
        client.get_host_keys().add(host,key.get_name(),key)

def connect(name):
    host,title,row = {'athena':('192.168.2.1','## 3.','| SSH'),
                      'snr1':('192.168.132.1','## 3b.','| 管理页')}[name]
    source=(ROOT/'登录信息与配置总表-20260917.md').read_text(encoding='utf-8')
    start=source.index(title); end=source.find('\n## ',start+1)
    section=source[start:end if end!=-1 else len(source)]
    password=re.findall(r'`([^`]+)`',next(l for l in section.splitlines() if l.startswith(row)))[-1]
    c=paramiko.SSHClient(); c.load_system_host_keys(); c.set_missing_host_key_policy(PinPolicy())
    c.connect(host,username='root',password=password,look_for_keys=False,allow_agent=False,timeout=12)
    return c

def cmd(c, text, timeout=45):
    _,o,e=c.exec_command(text,timeout=timeout)
    return (o.read()+e.read()).decode(errors='replace')

def snapshot(name):
    c=connect(name)
    try:
        paths=cmd(c,"tr '\\0' ' ' < /proc/$(pidof mihomo)/cmdline").strip()
        path=re.search(r'(?:^| )-f (\S+)',paths).group(1)
        # Raw YAML is never saved or printed.
        config=yaml.safe_load(cmd(c,'cat '+path))
        allowed=['mixed-port','redir-port','mode','ipv6','dns','proxy-groups','rules']
        result={'device':name,'path':path,'config':{k:config.get(k) for k in allowed}}
        for endpoint in ['version','proxies']:
            value=json.loads(cmd(c,'curl -fsS --max-time 5 http://127.0.0.1:9090/'+endpoint))
            if endpoint=='proxies':
                value={k:{a:v.get(a) for a in ['type','now','all','alive','history']} for k,v in value.get('proxies',{}).items()}
            result[endpoint]=value
        result['network']=cmd(c,"date '+%Y-%m-%dT%H:%M:%S%z'; ip -4 route; ip -s link show wan; cat /etc/resolv.conf; nslookup www.gstatic.com; nslookup www.baidu.com; /usr/bin/mihomo -v")
        result['firewall']=cmd(c,"nft list ruleset 2>/dev/null" if name=='athena' else 'iptables-save -c')
        result['probe_source']=cmd(c,'cat /etc/routermgmt/netprobe.sh')
        result['probe_cron']=cmd(c,"crontab -l | grep netprobe")
        result['probe_log']=cmd(c,'tail -n 1800 /etc/routermgmt/logs/netprobe.log')
        return result
    finally:c.close()

if __name__=='__main__':
    # Historical evidence must never be silently replaced on a later run.
    if any((Path(__file__).parent/(name+'-snapshot.json')).exists() for name in ['athena','snr1']):
        raise SystemExit('Snapshot already exists. Prepare a new reviewed collection batch; do not overwrite historical evidence.')
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:
        for result in ex.map(snapshot,['athena','snr1']):
            target=Path(__file__).parent/(result['device']+'-snapshot.json')
            target.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
            print(result['device'],json.dumps({k:v for k,v in result.items() if k not in ['firewall','probe_source','probe_log']},ensure_ascii=False))
