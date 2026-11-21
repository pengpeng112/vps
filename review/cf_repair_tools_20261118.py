"""Explicit post-deployment repair helpers; importing this module performs no I/O."""
from pathlib import Path
import datetime
import hashlib
import http.client
import importlib.util
import json
import re
import shlex
import time
import yaml

ROOT=Path(__file__).resolve().parents[1]
BACKUP=ROOT/'backups/codex-postdeploy-repair-20261118'
SESSION='Codex-CF-meter-review-20261118'


def require_lock():
    d=json.loads((ROOT/'backups/.session-locks/registry.json').read_text(encoding='utf-8'))
    assert any(x['session']==SESSION and x['scope']=='gz-prod' for x in d['locks']), 'Repair lock missing'


def ns_helpers():
    p=ROOT/'review/fleet-qos-readonly-audit-20261117.py'
    a,sep,_=p.read_text(encoding='utf-8-sig').partition('\ntry:\n')
    assert sep
    ns={'__file__':str(p)};exec(compile(a,'<audited-helper-prefix>','exec'),ns)
    return ns


class Api:
    def __init__(self,gz):self.gz=gz;self.token=None
    def request(self,method,path,body=None,key=None):
        assert '\r' not in path and '\n' not in path
        raw=json.dumps(body,ensure_ascii=False).encode() if body is not None else b''
        headers={'Host':'localhost','Content-Type':'application/json','Content-Length':str(len(raw)),'Connection':'close'}
        if self.token:headers['Authorization']='Bearer '+self.token
        if key:headers['Idempotency-Key']=key
        c=self.gz.get_transport().open_channel('direct-tcpip',('127.0.0.1',8080),('127.0.0.1',0),timeout=10)
        c.settimeout(40)
        try:
            c.sendall((method+' '+path+' HTTP/1.1\r\n'+''.join(k+': '+v+'\r\n' for k,v in headers.items())+'\r\n').encode()+raw)
            r=http.client.HTTPResponse(c);r.begin();data=r.read();code=r.status;r.close()
            return code,json.loads(data or b'{}')
        finally:c.close()
    def login(self):
        p=ROOT/'review/path_a_gz_api.py';spec=importlib.util.spec_from_file_location('audited_patha_api',p)
        m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
        user,pw,secret=m._ledger_pw()
        code,d=self.request('POST','/api/v1/auth/login',{'username':user,'password':pw})
        assert code==200 and d.get('ticket'), 'Login failed (response withheld)'
        code,d=self.request('POST','/api/v1/auth/totp',{'ticket':d['ticket'],'code':m.totp(secret)})
        assert code==200 and d.get('token'), 'TOTP failed (response withheld)'
        self.token=d['token']


def read_bytes(client,path):
    _,o,e=client.exec_command('cat '+shlex.quote(path),timeout=15)
    data=o.read();e.read()
    if o.channel.recv_exit_status()!=0:raise OSError('Controlled file read failed')
    return data


def put_bytes(client,path,data,mode=0o600):
    i,o,e=client.exec_command('umask 077; cat > '+shlex.quote(path)+' && chmod '+format(mode,'o')+' '+shlex.quote(path),timeout=20)
    i.channel.sendall(data);i.channel.shutdown_write();o.read();e.read()
    if o.channel.recv_exit_status()!=0:raise OSError('Controlled file write failed')


def processes(client,run):
    result=[]
    pids=run(client,'pidof mihomo')['stdout'].split()
    for pid in pids:
        if not pid.isdigit():continue
        try:
            argv=[x.decode() for x in read_bytes(client,'/proc/'+pid+'/cmdline').split(b'\0') if x]
            stat=read_bytes(client,'/proc/'+pid+'/stat').decode().rsplit(')',1)[1].split()
            result.append({'pid':int(pid),'argv':argv,'start':stat[19]})
        except OSError:pass
    return result


def terminate_owned(client,run,proc):
    check=read_bytes(client,'/proc/'+str(proc['pid'])+'/stat').decode().rsplit(')',1)[1].split()[19]
    assert check==proc['start'],'Process changed; refusing to signal'
    return run(client,"test \"$(awk '{print $22}' /proc/"+str(proc['pid'])+"/stat)\" = "+shlex.quote(proc['start'])+' && kill -TERM '+str(proc['pid']))


def probe_hk_alternate(client,run):
    require_lock()
    cfg=yaml.safe_load(read_bytes(client,'/etc/mihomo/config.yaml'))
    node=next(p.copy() for p in cfg['proxies'] if p['name']=='CF2D-HK-01')
    node['server']='162.159.135.42'
    test={'mixed-port':17892,'allow-lan':False,'bind-address':'127.0.0.1',
          'external-controller':'127.0.0.1:19092','mode':'rule','log-level':'silent',
          'proxies':[node],'rules':['MATCH,CF2D-HK-01']}
    path=run(client,'umask 077; mktemp -d /tmp/rmg-cf-probe.XXXXXX')['stdout']
    assert re.fullmatch(r'/tmp/rmg-cf-probe\.[A-Za-z0-9]+',path)
    pid=None;result={}
    try:
        put_bytes(client,path+'/config.yaml',yaml.safe_dump(test,allow_unicode=True,sort_keys=False).encode())
        r=run(client,'nohup /usr/bin/mihomo -d '+shlex.quote(path)+' -f '+shlex.quote(path+'/config.yaml')+' >'+shlex.quote(path+'/log')+' 2>&1 < /dev/null & echo $!')
        assert r['stdout'].isdigit()
        pid=int(r['stdout']);time.sleep(2)
        proc=next(x for x in processes(client,run) if x['pid']==pid)
        assert proc['argv']==['/usr/bin/mihomo','-d',path,'-f',path+'/config.yaml']
        result['samples']=[]
        for _ in range(3):
            result['samples'].append(run(client,"curl --noproxy '' -fsS -m 12 -x http://127.0.0.1:17892 -o /dev/null -w '%{http_code} %{time_total}' https://www.gstatic.com/generate_204"))
        result['exit']=run(client,"curl --noproxy '' -fsS -m 15 -x http://127.0.0.1:17892 https://www.cloudflare.com/cdn-cgi/trace | sed -n 's/^ip=//p'")
        result['download']=run(client,"curl --noproxy '' -fsS -m 25 -x http://127.0.0.1:17892 -o /dev/null -w '%{http_code} %{size_download} %{time_total}' 'https://speed.cloudflare.com/__down?bytes=65536'",timeout=30)
        result['pass']=all(r['rc']==0 and r['stdout'].startswith('204 ') for r in result['samples']) and result['exit']['stdout']=='191.223.215.12' and result['download']['stdout'].startswith('200 65536 ')
    finally:
        if pid:
            candidates=[x for x in processes(client,run) if x['pid']==pid and path in x['argv']]
            if candidates:terminate_owned(client,run,candidates[0]);time.sleep(1)
        run(client,'rm -f '+ ' '.join(shlex.quote(path+'/'+x) for x in ('config.yaml','log','cache.db'))+'; rmdir '+shlex.quote(path)+' 2>/dev/null || true')
    return result


def main():
    import argparse
    ap=argparse.ArgumentParser();ap.add_argument('op',choices=['probe','cleanup-malformed-tests']);args=ap.parse_args()
    require_lock();ns=ns_helpers();gz=ns['connect']('134.175.151.213',28722,'ubuntu',ns['DEV_PW'])
    out={'started':datetime.datetime.now().astimezone().isoformat(timespec='seconds'),'operation':args.op,'devices':{}}
    targets={'R2':13223,'R4':13224} if args.op=='probe' else {'R1':13222,'R2':13223,'R3':13227,'R4':13224,'R5':13225,'R6':13226,'R8':13230,'R9':13231}
    try:
        for name,port in targets.items():
            c=ns['connect']('127.0.0.1',port,'root',ns['DEV_PW'],gz)
            try:
                if args.op=='probe':out['devices'][name]=probe_hk_alternate(c,ns['run'])
                else:
                    before=processes(c,ns['run']);killed=[]
                    for p in before:
                        if p['argv']==['/usr/bin/mihomo','-d','-f','/config.yaml','-t']:
                            terminate_owned(c,ns['run'],p);killed.append(p['pid'])
                    time.sleep(1)
                    out['devices'][name]={'removed_exact_malformed_test_pids':killed,'remaining':processes(c,ns['run']),
                                          'proxy_health':ns['run'](c,"curl --noproxy '' -fsS -m 10 -x http://127.0.0.1:7890 -o /dev/null -w '%{http_code}' https://www.gstatic.com/generate_204")}
            finally:c.close()
            print(json.dumps({name:out['devices'][name]},ensure_ascii=False),flush=True)
    finally:
        gz.close();out['finished']=datetime.datetime.now().astimezone().isoformat(timespec='seconds')
        path=ROOT/('review/CF修复-'+args.op+'-20261118-Codex.json')
        if path.exists():path=path.with_name(path.stem+'-'+str(int(time.time()))+'.json')
        path.write_text(json.dumps(out,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')


if __name__=='__main__':main()
