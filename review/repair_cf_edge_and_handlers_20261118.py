"""Authorized serial repair: AUTO handler and HK CF edge; no firmware/QoS changes."""
from pathlib import Path
import copy,datetime,hashlib,importlib.util,json,re,shlex,time,uuid
import yaml
from cf_repair_tools_20261118 import ROOT,BACKUP,require_lock,ns_helpers,read_bytes,put_bytes,Api

TARGETS=[('R2','rtr-a9bcba',13223),('R1','rtr-d3847d',13222),('R3','rtr-38aa54',13227),('R4','rtr-cd473f',13224),
         ('R5','rtr-c46a25',13225),('R6','rtr-673568',13226),('R8','rtr-1f6f0f',13230),('R9','rtr-101be0',13231)]


def main():
    require_lock();ns=ns_helpers();run=ns['run'];get_json=ns['get_json']
    tests=json.loads((ROOT/'review/AUTO恢复处理器隔离测试-20261118-Codex.json').read_text(encoding='utf-8'))
    handler=(ROOT/'mgmt/agent/handlers/switch_node.sh').read_bytes()
    assert hashlib.sha256(handler).hexdigest()==tests['source_sha256'] and all(x['pass'] for x in tests['tests'])
    # Only the corrected forced-proxy probe is eligible evidence.
    probes=sorted((ROOT/'review').glob('CF修复-probe-20261118-Codex-*.json'),key=lambda p:p.stat().st_mtime)
    probe=json.loads(probes[-1].read_text(encoding='utf-8'))
    assert all(probe['devices'][n]['pass'] for n in ('R2','R4'))
    spec=importlib.util.spec_from_file_location('audited_candidate_helpers',ROOT/'review/path_a_cf11_rollout.py')
    m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
    params=json.loads((ROOT/'backups/.cf-node-params.json').read_text(encoding='utf-8-sig'))
    gz=ns['connect']('134.175.151.213',28722,'ubuntu',ns['DEV_PW']);api=Api(gz)
    report={'started':datetime.datetime.now().astimezone().isoformat(timespec='seconds'),'devices':{},'firmware_changed':False,'qos_enabled':False}
    out=ROOT/'review/香港CF边缘与AUTO处理器修复-20261118-Codex.json'
    if out.exists():out=out.with_name(out.stem+'-'+str(int(time.time()))+'.json')
    assert not out.exists()
    directory=BACKUP/'edge-and-handler';directory.mkdir(exist_ok=True)
    try:
        api.login()
        code,d=api.request('GET','/api/v1/nodes?page_size=100');assert code==200
        node=next(n for n in d['items'] if n['id']==16)
        assert node['name']=='CF2D-HK-01' and node['host'] in ('104.18.32.7','162.159.135.42') and node['purpose']=='ordinary'
        if node['host']!='162.159.135.42':
            code,d=api.request('PATCH','/api/v1/nodes/16',{'host':'162.159.135.42'});assert code==200 and d.get('host')=='162.159.135.42'
        report['node_patch']={'id':16,'old_host':'104.18.32.7','new_host':'162.159.135.42','sni':'hk.6849271.xyz','primary_unchanged':True}
        for name,device,port in TARGETS:
            c=ns['connect']('127.0.0.1',port,'root',ns['DEV_PW'],gz);entry={};report['devices'][name]=entry
            try:
                field=read_bytes(c,'/etc/mihomo/config.yaml');cfg=yaml.safe_load(field)
                if not (directory/(name+'-config-before.yaml')).exists():(directory/(name+'-config-before.yaml')).write_bytes(field)
                old_handler=read_bytes(c,'/etc/routermgmt/handlers/switch_node.sh')
                if not (directory/(name+'-handler-before.sh')).exists():(directory/(name+'-handler-before.sh')).write_bytes(old_handler)
                assert hashlib.sha256(old_handler).hexdigest() in ('0faf7732202232c89f4bf060affd3500ee20d6a8603390e3d5b1134e15b6cee9','e9729615b01a8b0418eb852dac77deb2924a9bd2f4e9baaa60b6e0c357cdff43',tests['source_sha256'])
                protected=run(c,'sha256sum /etc/config/dhcp /etc/routermgmt/tunnel.conf /etc/routermgmt/self-heal.sh\npidof mihomo')['stdout']
                before_group=get_json(c,"curl --noproxy '*' -fsS -m 4 http://127.0.0.1:9090/proxies/PROXY")
                assert before_group.get('now')=='AUTO'
                assert run(c,'find /etc/routermgmt/state/txn -name uncommitted -type f 2>/dev/null')['stdout']==''
                init=read_bytes(c,'/etc/init.d/routermgmt')
                assert b'procd_set_param command /usr/bin/routermgmt-agent' in init and b'mihomo' not in init
                # Replace just the reviewed handler, then restart only its agent.
                if old_handler!=handler:
                    put_bytes(c,'/etc/routermgmt/handlers/.switch_node.codex.new',handler,0o755)
                    r=run(c,'sh -n /etc/routermgmt/handlers/.switch_node.codex.new && test ! -e /etc/routermgmt/handlers/switch_node.before-codex-20261118 && cp -p /etc/routermgmt/handlers/switch_node.sh /etc/routermgmt/handlers/switch_node.before-codex-20261118 && mv /etc/routermgmt/handlers/.switch_node.codex.new /etc/routermgmt/handlers/switch_node.sh && /etc/init.d/routermgmt restart')
                    assert r['rc']==0
                    time.sleep(2)
                assert run(c,'sha256sum /etc/config/dhcp /etc/routermgmt/tunnel.conf /etc/routermgmt/self-heal.sh\npidof mihomo')['stdout']==protected,'Agent upgrade unexpectedly changed protected runtime'
                entry['handler_sha256']=tests['source_sha256'];entry['agent_only_restart']=True
                code,p=api.request('POST','/api/v1/devices/'+device+'/configs/preview',{})
                assert code==200
                candidate,sub=m.build_candidate(p['masked_yaml'],field.decode(),params['uuid'])
                assert m.remask(candidate)==p['masked_yaml'].rstrip('\n') and sub>=2
                newcfg=yaml.safe_load(candidate);expected=copy.deepcopy(cfg)
                next(x for x in expected['proxies'] if x['name']=='CF2D-HK-01')['server']='162.159.135.42'
                # The renderer also moves this node's explicit endpoint DIRECT
                # protection. Keep every other rule/ordering byte-semantic equal.
                assert len(expected['rules'])==len(newcfg['rules'])
                entry['endpoint_direct_rule_updates']=0
                for i,(old,new) in enumerate(zip(expected['rules'],newcfg['rules'])):
                    if old!=new:
                        assert old=='IP-CIDR,104.18.32.7/32,DIRECT,no-resolve' and new=='IP-CIDR,162.159.135.42/32,DIRECT,no-resolve','Unexpected rule change'
                        expected['rules'][i]=new;entry['endpoint_direct_rule_updates']+=1
                assert newcfg==expected,'Preview contains changes beyond the HK edge; stopped before apply'
                assert '8364927.xyz' not in candidate and '\r' not in candidate
                (directory/(name+'-candidate.yaml')).write_text(candidate,encoding='utf-8',newline='\n')
                path='/tmp/rmg-cf-edge-'+device+'.yaml';put_bytes(c,path,candidate.encode())
                # This firmware has no timeout applet. Bound only our own test
                # child; keep every argument quoted and never signal a service.
                command='/usr/bin/mihomo -t -d /etc/mihomo -f '+shlex.quote(path)
                checklog=path+'.checklog'
                script=command+' >'+shlex.quote(checklog)+' 2>&1 &\ncheckpid=$!\n'
                script+='i=0; while kill -0 "$checkpid" 2>/dev/null && [ "$i" -lt 20 ]; do sleep 1; i=$((i+1)); done\n'
                script+='if kill -0 "$checkpid" 2>/dev/null; then kill -TERM "$checkpid"; wait "$checkpid"; exit 124; fi\n'
                script+='wait "$checkpid"; checkrc=$?; cat '+shlex.quote(checklog)+'; rm -f '+shlex.quote(checklog)+'; exit "$checkrc"\n'
                r=run(c,script,timeout=30)
                assert r['rc']==0 and 'successful' in r['stdout']
                assert run(c,'test ! -e /tmp/selfheal.pause')['rc']==0
                # Pause is scoped to this single apply and is always removed.
                run(c,'touch /tmp/selfheal.pause')
                try:
                    code,a=api.request('POST','/api/v1/devices/'+device+'/configs/apply',{'revision':p['revision']},key='codex-cf-edge-'+uuid.uuid4().hex)
                    assert code==200 and a.get('task_id')
                    entry.update(version=a['version'],task_id=a['task_id'])
                    status=None
                    for _ in range(35):
                        _,tasks=api.request('GET','/api/v1/tasks?page_size=50')
                        task=next((t for t in tasks.get('items',[]) if t['id']==a['task_id']),None)
                        if task:status=task['status']
                        if status in ('ok','failed','rolled_back','expired'):break
                        time.sleep(3)
                    entry['task_status']=status
                    assert status=='ok','Task failed; rollout stopped with checkpoint'
                    actual=read_bytes(c,'/etc/mihomo/config.yaml')
                    assert actual==candidate.encode(),'Active config differs from signed candidate'
                    group=get_json(c,"curl --noproxy '*' -fsS -m 5 http://127.0.0.1:9090/proxies/PROXY")
                    assert group.get('now')=='AUTO','New handler did not restore AUTO'
                    entry['auto_restored_by_handler']=True
                    entry['config_sha256']=hashlib.sha256(actual).hexdigest()
                    entry['hk_delay']=[]
                    for _ in range(2):
                        d=get_json(c,"curl --noproxy '*' -fsS -m 15 'http://127.0.0.1:9090/proxies/CF2D-HK-01/delay?timeout=12000&url=https%3A%2F%2Fwww.gstatic.com%2Fgenerate_204'")
                        entry['hk_delay'].append(d.get('delay'))
                    assert all(isinstance(v,int) for v in entry['hk_delay'])
                    entry['business_health']=run(c,"curl --noproxy '' -fsS -m 12 -x http://127.0.0.1:7890 -o /dev/null -w 'g204=%{http_code}\\n' https://www.gstatic.com/generate_204\ncurl --noproxy '' -fsS -m 8 -x http://127.0.0.1:7890 -o /dev/null -w 'baidu=%{http_code}\\n' https://www.baidu.com")
                    assert entry['business_health']['rc']==0 and 'g204=204' in entry['business_health']['stdout'] and 'baidu=200' in entry['business_health']['stdout']
                    assert run(c,'sha256sum /etc/config/dhcp /etc/routermgmt/tunnel.conf /etc/routermgmt/self-heal.sh\npidof mihomo')['stdout'].splitlines()[:3]==protected.splitlines()[:3]
                    entry['pass']=True
                finally:
                    run(c,'rm -f /tmp/selfheal.pause '+shlex.quote(path))
            finally:c.close()
            out.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
            print(json.dumps({name:entry},ensure_ascii=False),flush=True)
        report['success']=True
    finally:
        report['finished']=datetime.datetime.now().astimezone().isoformat(timespec='seconds');gz.close()
        out.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')


if __name__=='__main__':main()
