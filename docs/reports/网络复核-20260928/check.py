"""Bounded read-only diagnostics; no remote config/selection writes; no credentials in output."""
from pathlib import Path
import sys, importlib.util, json, subprocess, concurrent.futures, datetime, csv
sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).parent
spec = importlib.util.spec_from_file_location('baseline', ROOT/'docs/reports/统一优化-20260927/M00-1647/collect_baseline.py')
b = importlib.util.module_from_spec(spec)
spec.loader.exec_module(b)
def now(): return datetime.datetime.now().astimezone().isoformat()
def save(name, data):
    (OUT/name).write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
def remote(tag):
    c = b.rconnect('athena') if tag=='athena' else b.vconnect(*({'do':('159.223.36.119',28722,'root','id_ed25519_digitalocean'),'tx':('43.134.46.44',22,'ubuntu','id_ed25519_tencent')}[tag]))
    try:
        r={'start':now(),'basic':b.cmd(c,"date -Iseconds; uptime; cat /proc/loadavg; grep -E 'MemTotal|MemAvailable' /proc/meminfo; df -h / | tail -1"),'tcp':b.cmd(c,'cat /proc/net/snmp | grep -A1 "^Tcp:"')}
        if tag=='athena':
            r['network']=b.cmd(c,'ip -4 route; ip -s link show wan; cat /proc/sys/net/netfilter/nf_conntrack_count; cat /proc/sys/net/netfilter/nf_conntrack_max')
            r['proxies']={k:{f:v.get(f) for f in ('type','now','alive','history')} for k,v in json.loads(b.cmd(c,'curl -fsS -m 5 http://127.0.0.1:9090/proxies')).get('proxies',{}).items()}
            r['probe_source']=b.cmd(c,'cat /etc/routermgmt/netprobe.sh')
            r['netprobe']=b.cmd(c,'tail -n 2300 /etc/routermgmt/logs/netprobe.log')
            r['ping']=b.cmd(c,'ping -c 10 -W 1 -q 10.1.2.254; ping -c 10 -W 1 -q 43.134.46.44; ping -c 10 -W 1 -q 159.223.36.119',timeout=45)
            r['snr1_neighbor']=b.cmd(c,'ip neigh show 192.168.2.158')
            r['log_counts']=b.cmd(c,"logread | awk '/mihomo/ && /timeout/{t++} /Out of memory|oom-kill/{o++} /wan/ && /link.*down|Link.*Down/{w++} END{print \"mihomo_timeout_lines=\"t,\"oom_lines=\"o,\"wan_down_lines=\"w}'")
        else:
            r['services']=b.cmd(c,'systemctl is-active sing-box; systemctl show sing-box -p NRestarts -p ActiveEnterTimestamp; ss -s; ss -ltn | head -25')
            r['errors']=b.cmd(c,"journalctl -u sing-box --since '2 hours ago' --no-pager -o cat | awk '/timeout/{t++} /error|ERROR/{e++} END{print \"timeout_lines=\"t,\"error_lines=\"e}'")
            r['http']=b.cmd(c,"for u in https://www.gstatic.com/generate_204 https://www.google.com/generate_204 https://www.baidu.com/; do curl --noproxy '*' -s -o /dev/null --connect-timeout 4 -m 10 -w 'code=%{http_code} tcp=%{time_connect} tls=%{time_appconnect} total=%{time_total}\\n' \"$u\"; echo curl_exit=$?; done",timeout=40)
        r['end']=now(); save(tag+'.json',r)
        return tag,'saved'
    finally: c.close()
URLS={'gstatic':'https://www.gstatic.com/generate_204','google':'https://www.google.com/generate_204','baidu':'https://www.baidu.com/','github':'https://github.com/robots.txt','youtube':'https://www.youtube.com/generate_204'}
def pc_site(site):
    rows=[]
    for i in range(10):
        p=subprocess.run(['curl.exe','--noproxy','*','--http1.1','-s','-o','NUL','--connect-timeout','4','--max-time','10','-w','%{json}',URLS[site]],capture_output=True,text=True,timeout=14)
        try: raw=json.loads(p.stdout)
        except Exception: raw={}
        rows.append({'ts':now(),'exit':p.returncode,**{k:raw.get(k) for k in ('http_code','remote_ip','time_namelookup','time_connect','time_appconnect','time_starttransfer','time_total')}})
    save('pc-'+site+'.json',rows)
    return site,{'n':len(rows),'timeouts':sum(x['exit']==28 for x in rows),'errors':sum(x['exit']!=0 for x in rows),'max':max(x.get('time_total') or 0 for x in rows)}
if __name__=='__main__':
    if any((OUT/(name+'.json')).exists() for name in ('athena','do','tx')):
        raise SystemExit('Evidence exists; use a new reviewed output directory. Do not overwrite historical snapshots.')
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as ex:
        jobs=[ex.submit(remote,t) for t in ('athena','do','tx')]+[ex.submit(pc_site,t) for t in URLS]
        for f in concurrent.futures.as_completed(jobs):
            try: print(f.result(),flush=True)
            except Exception as e: print('collection_error',type(e).__name__,flush=True)
    rows=list(csv.DictReader((ROOT/'webprobe/webprobe.csv').open(encoding='utf-8')))
    save('webprobe-recent.json',[r for r in rows if r.get('ts','')>='2026-09-27 17:00:00'])
