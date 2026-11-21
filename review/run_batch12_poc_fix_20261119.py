"""Explicitly authorized R2 sandbox PoC. All changes owned by a unique run; cleanup in finally."""
from pathlib import Path
import copy
import datetime as dt
import hashlib
import importlib.util
import ipaddress
import json
import re
import shlex
import time
import yaml
import argparse
import batch12_fix_support_20261119 as support

ROOT=support.ROOT

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--phases',default='release,rates,steady');args=parser.parse_args()
    phases=args.phases.split(',')
    if phases not in (['release','rates','steady'],['release','resources']):raise ValueError('unsupported phase selection')
    for scope in ('device:R2','server:HK','server:BWG'):support.require_scope(scope)
    ns=support.helpers()
    spec=importlib.util.spec_from_file_location('poc_deep_helpers',ROOT/'review/governance_deep_readonly_20261119.py');deep=importlib.util.module_from_spec(spec);spec.loader.exec_module(deep)
    stamp=dt.datetime.now().strftime('%Y%m%d-%H%M%S');tag='q12-'+stamp
    work='/tmp/qos-poc-Codex-'+stamp;server_dir='/tmp/qos-source-Codex-'+stamp
    local=support.BACKUP/('poc-'+stamp);local.mkdir()
    result={'started':dt.datetime.now().astimezone().isoformat(),'device':'R2','production_config_touched':False,'counter_scope':'HK dedicated HTTP port; BWG HY2 to R2 public IPv4 (includes positive background upper bound)','work':work,'server_dir':server_dir,'phases':{}}
    gz=r2=hk=bwg=None;counter_hk=counter_bwg=None
    def run(c,cmd,timeout=30):
        r=ns['run'](c,cmd,timeout)
        if r['rc']!=0:raise RuntimeError('controlled remote command failed')
        return r
    def counter(c,marker):
        if marker.endswith('-hk'):
            data=json.loads(run(c,'nft -j list table inet '+counter_hk['table'])['stdout'])
            rules=[x['rule'] for x in data['nftables'] if 'rule' in x and x['rule'].get('comment')==marker]
            if len(rules)!=1:raise RuntimeError('nft counter ownership mismatch')
            counters=[x['counter'] for x in rules[0]['expr'] if 'counter' in x]
            return int(counters[0]['bytes'])
        r=run(c,'iptables -L OUTPUT -n -v -x');rows=[line for line in r['stdout'].splitlines() if '/* '+marker+' */' in line]
        if len(rows)!=1:raise RuntimeError('counter ownership mismatch')
        fields=rows[0].split();return int(fields[1])
    def cleanup_dir(c,directory,pidfiles):
        if c is None:return
        # Only own unique directory; verify each PID command and children before signals.
        script='''import os,pathlib,signal,time,shutil,json
d=pathlib.Path(DIRECTORY)
assert str(d.resolve())==DIRECTORY and DIRECTORY.startswith('/tmp/qos-') and 'Codex-' in DIRECTORY
targets=[]
for name in PIDFILES:
 p=d/name
 if not p.exists():continue
 try:
  pid=int(p.read_text());cmd=pathlib.Path('/proc/%d/cmdline'%pid).read_bytes().replace(b'\\0',b' ').decode()
 except (ValueError,FileNotFoundError):continue
 if DIRECTORY not in cmd:raise RuntimeError('PID identity mismatch')
 targets.append(pid)
 # Descendants only, preserving any unrelated process.
 changed=True
 while changed:
  changed=False
  for proc in pathlib.Path('/proc').glob('[0-9]*'):
   try:parent=int((proc/'stat').read_text().rsplit(')',1)[1].split()[1]);child=int(proc.name)
   except (OSError,ValueError):continue
   if parent in targets and child not in targets:targets.append(child);changed=True
for pid in reversed(targets):
 try:os.kill(pid,signal.SIGTERM)
 except ProcessLookupError:pass
time.sleep(.3)
for pid in targets:
 try:os.kill(pid,0)
 except ProcessLookupError:continue
 # A retained process must still have this run's identity or its verified parent.
 try:os.kill(pid,signal.SIGKILL)
 except ProcessLookupError:pass
if d.exists():shutil.rmtree(d)
print('OWNED_POC_CLEANED')
'''.replace('DIRECTORY',repr(directory)).replace('PIDFILES',repr(pidfiles))
        return run(c,"python3 - <<'PY'\n"+script+"\nPY\n")
    try:
        gz=ns['connect']('134.175.151.213',28722,'ubuntu',ns['DEV_PW']);r2=ns['connect']('127.0.0.1',13223,'root',ns['DEV_PW'],gz)
        hk=deep.key_connect('191.223.215.12',Path.home()/'.ssh/id_ed25519_dmit_hkg');bwg=deep.key_connect('74.82.193.113',Path.home()/'.ssh/id_ed25519_bwg',hk)
        result['before']=run(r2,"date -Is\npidof mihomo\nsha256sum /etc/mihomo/config.yaml /etc/config/dhcp /etc/routermgmt/tunnel.conf\nnetstat -lnt | awk '$4 ~ /:(7891|7894|7895|7896|7897|9091)$/ {print}'\n")
        if len(result['before']['stdout'].splitlines())!=5:raise RuntimeError('sandbox port not free or production preflight changed')
        raw_config=support.read(r2,'/etc/mihomo/config.yaml')
        if hashlib.sha256(raw_config).hexdigest()!='73e830e5085eb7dd2b80bbde5bd25efbb9ea19141d798cb917086ba54c584674':raise RuntimeError('formal configuration drift')
        formal=yaml.safe_load(raw_config);node=copy.deepcopy(next(n for n in formal['proxies'] if n['name']=='BWG-HY2-JPTY-01'))
        # Fixed-version source confirms these QUIC fields. Scope is this sandbox only.
        node.update({'initial-stream-receive-window':65536,'max-stream-receive-window':65536,'initial-connection-receive-window':524288,'max-connection-receive-window':524288})
        cfg={'mixed-port':7891,'allow-lan':False,'bind-address':'127.0.0.1','mode':'rule','log-level':'warning','ipv6':False,'external-controller':'127.0.0.1:9091','dns':{'enable':False},'tun':{'enable':False},'profile':{'store-selected':False},'proxies':[node],'proxy-groups':[{'name':'AUTO','type':'select','proxies':[node['name']]}],'listeners':[{'name':'q12-upstream','type':'socks','listen':'127.0.0.1','port':7894,'proxy':'AUTO','udp':False}],'rules':['MATCH,AUTO']}
        gate={'listen_video':'127.0.0.1:7895','listen_dl':'127.0.0.1:7896','listen_elephant':'127.0.0.1:7897','upstream':'127.0.0.1:7894','ctl_sock':work+'/ctl.sock','state_file':work+'/state.json','classes':{'video':{'down_bps':60000},'foreign_download':{'down_bps':60000},'elephant':{'down_bps':0,'elephant_mb':0}}}
        run(r2,'test ! -e '+work+' && mkdir -m 700 '+work)
        run(hk,'test ! -e '+server_dir+' && mkdir -m 700 '+server_dir)
        support.scp_put(r2,work,'mihomo.yaml',yaml.safe_dump(cfg,allow_unicode=True,sort_keys=False).encode())
        support.scp_put(r2,work,'gate.json',json.dumps(gate).encode())
        support.scp_put(r2,work,'params.json',json.dumps({'base':'http://191.223.215.12:18443/'+tag}).encode())
        support.scp_put(r2,work,'qos-gate',(support.BACKUP/'qos-gate-linux-arm64').read_bytes(),0o700)
        support.scp_put(r2,work,'driver.py',(ROOT/'review/batch12_poc_client_20261119.py').read_bytes(),0o700)
        # HK supports SFTP; preserve bytes and verify remotely in memory.
        sftp=hk.open_sftp()
        with sftp.file(server_dir+'/server.py','wb') as f:f.write((ROOT/'review/batch12_poc_source_20261119.py').read_bytes())
        sftp.chmod(server_dir+'/server.py',0o700);sftp.close()
        run(hk,'python3 -m py_compile '+server_dir+'/server.py')
        server_cmd='nohup python3 '+server_dir+'/server.py --port 18443 --token '+tag+' --state '+server_dir+'/state.json > '+server_dir+'/server.log 2>&1 &\necho $! > '+server_dir+'/server.pid\n'
        run(hk,server_cmd)
        result['mihomo_syntax']=run(r2,'mihomo -t -d '+work+' -f '+work+'/mihomo.yaml',45)
        run(r2,'nohup mihomo -d '+work+' -f '+work+'/mihomo.yaml > '+work+'/mihomo.log 2>&1 &\necho $! > '+work+'/mihomo.pid\n')
        time.sleep(1)
        # The reverse listener belongs to R2's authenticated SSH transport.
        # Read only that sshd PID's established management socket; do not expose IPs.
        listening=ns['run'](gz,"sudo ss -lntp | awk '$4 ~ /:13223$/ {print}'")['stdout']
        pids=set(re.findall(r'pid=(\d+)',listening))
        if len(pids)!=1:raise RuntimeError('R2 reverse listener ownership ambiguous')
        pid=next(iter(pids))
        sockets=ns['run'](gz,"sudo ss -tnp | awk '/pid="+pid+",/ {print}'")['stdout']
        addresses=[]
        for line in sockets.splitlines():
            fields=line.split()
            if len(fields)>4 and fields[3].endswith(':28722'):
                try:addresses.append(str(ipaddress.IPv4Address(fields[4].rsplit(':',1)[0])))
                except ValueError:pass
        if len(set(addresses))!=1:raise RuntimeError('R2 management peer IPv4 unresolved')
        public_ip=addresses[0]
        result['r2_public_ipv4_sha256']=hashlib.sha256(public_ip.encode()).hexdigest()
        table='q12_'+stamp.replace('-','_')
        existing=ns['run'](hk,'nft list table inet '+table)
        if existing['rc']==0:raise RuntimeError('temporary nft table already exists')
        run(hk,'nft add table inet '+table)
        counter_hk={'table':table,'marker':tag+'-hk'}
        run(hk,shlex.join(['nft','add','chain','inet',table,'output','{','type','filter','hook','output','priority','0',';','policy','accept',';','}']))
        run(hk,shlex.join(['nft','add','rule','inet',table,'output','tcp','sport','18443','counter','comment','"'+tag+'-hk"']))
        counter_bwg=['-p','udp','--sport','34567','-d',public_ip,'-m','comment','--comment',tag+'-bwg']
        run(bwg,shlex.join(['iptables','-I','OUTPUT','1',*counter_bwg]))
        for phase in phases:
            print('START_PHASE '+phase,flush=True)
            cmd='python3 '+work+'/driver.py --dir '+work+' --phase '+phase
            i,o,e=r2.exec_command(cmd,timeout=900);i.channel.shutdown_write()
            count_start=None
            for line in iter(o.readline,''):
                line=line.strip()
                if phase=='steady' and line=='STEADY_WARM':
                    count_start={'hk':counter(hk,tag+'-hk'),'bwg':counter(bwg,tag+'-bwg'),'at':time.monotonic()}
                if phase=='steady' and line=='STEADY_END':
                    count_end={'hk':counter(hk,tag+'-hk'),'bwg':counter(bwg,tag+'-bwg'),'at':time.monotonic()}
                print(line,flush=True)
            rc=o.channel.recv_exit_status();e.read()
            data=json.loads(support.read(r2,work+'/'+phase+'-result.json'));result['phases'][phase]=data
            if rc or not data['ok']:raise RuntimeError('PoC phase failed: '+phase)
            if phase=='steady':
                if count_start is None:raise RuntimeError('steady measurement marker missing')
                payload=data['window_payload_bytes'];elapsed=data['window_seconds']
                metrics={'payload_bytes':payload,'seconds':elapsed,'hk_ip_bytes':count_end['hk']-count_start['hk'],'bwg_ip_bytes_upper_bound':count_end['bwg']-count_start['bwg']}
                metrics['hk_ratio']=metrics['hk_ip_bytes']/payload;metrics['bwg_ratio_upper_bound']=metrics['bwg_ip_bytes_upper_bound']/payload
                result['save_traffic']=metrics
                if not(.8<=metrics['hk_ratio']<=1.5 and .8<=metrics['bwg_ratio_upper_bound']<=1.5):raise RuntimeError('FAIL_SAVE_TRAFFIC')
        result['result']='PASS_SCOPED_TCP_POC_ONLY' if 'steady' in phases else 'PASS_SELECTED_POC_PHASES'
    except Exception as exc:
        result['result']='BLOCK_ENFORCE';result['error_type']=type(exc).__name__
        if type(exc) is RuntimeError:result['error_code']=str(exc)
    finally:
        result['cleanup']={}
        for key,c,rules in [('HK_counter',hk,counter_hk),('BWG_counter',bwg,counter_bwg)]:
            if c and rules:
                try:
                    if isinstance(rules,dict):
                        counter(c,rules['marker'])
                        result['cleanup'][key]=run(c,'nft delete table inet '+rules['table'])
                    else:result['cleanup'][key]=run(c,shlex.join(['iptables','-D','OUTPUT',*rules]))
                except Exception as exc:result['cleanup'][key]={'error_type':type(exc).__name__}
        for key,c,d,pids in [('R2',r2,work,['curl.pid','driver.pid','gate.pid','mihomo.pid']),('HK',hk,server_dir,['server.pid'])]:
            try:result['cleanup'][key]=cleanup_dir(c,d,pids)
            except Exception as exc:result['cleanup'][key]={'error_type':type(exc).__name__}
        if r2:
            try:result['after']=run(r2,"date -Is\npidof mihomo\nsha256sum /etc/mihomo/config.yaml /etc/config/dhcp /etc/routermgmt/tunnel.conf\ncurl --noproxy '' -fsS -m 12 -x http://127.0.0.1:7890 -o /dev/null -w 'g204=%{http_code} time=%{time_total}\\n' https://www.gstatic.com/generate_204\n")
            except Exception as exc:result['after']={'error_type':type(exc).__name__}
        result['finished']=dt.datetime.now().astimezone().isoformat()
        path=ROOT/'review'/('Batch12修复后R2独立PoC-'+stamp+'-Codex.json')
        with path.open('x',encoding='utf-8') as f:json.dump(result,f,ensure_ascii=False,indent=2)
        print('POC_FINAL '+json.dumps({'result':result['result'],'error_code':result.get('error_code'),'save_traffic':result.get('save_traffic'),'cleanup':result['cleanup'],'evidence':path.name}),flush=True)
        for c in (bwg,hk,r2,gz):
            if c:c.close()
    return 0 if result['result'].startswith('PASS_') else 1

if __name__=='__main__':raise SystemExit(main())
