"""R2-only observe meter repair. Audited SCP staging, expected SHA, no config publication."""
from pathlib import Path
import datetime as dt
import hashlib
import json
import shlex
import batch12_fix_support_20261119 as support

ROOT=support.ROOT

def main():
    support.require_scope('device:R2')
    results=sorted((ROOT/'review').glob('Batch12计量隔离矩阵-20261119-*-Codex.json'))
    if not results or json.loads(results[-1].read_text(encoding='utf-8'))['rc']!=0:raise RuntimeError('meter matrix not green')
    ns=support.helpers();gz=r2=None
    stamp=dt.datetime.now().strftime('%Y%m%d-%H%M%S')
    stage='/tmp/batch12-meter-'+stamp
    backup='/etc/routermgmt/backup-meter-'+stamp+'-Codex'
    out={'started':dt.datetime.now().astimezone().isoformat(),'scope':'R2 observe meter only','stage':stage,'backup':backup,'phases':[]}
    def run(script,timeout=30):
        r=ns['run'](r2,script,timeout)
        if r['rc']!=0:raise RuntimeError('remote controlled step failed; raw output withheld')
        return r
    try:
        gz=ns['connect']('134.175.151.213',28722,'ubuntu',ns['DEV_PW'])
        r2=ns['connect']('127.0.0.1',13223,'root',ns['DEV_PW'],gz)
        for name in ('proxy-meter-v3.sh','proxy-meter-endpoints.sh'):
            expected=(support.BACKUP/'scripts/qos-r2'/name).read_bytes()
            if hashlib.sha256(support.read(r2,'/etc/routermgmt/'+name)).digest()!=hashlib.sha256(expected).digest():raise RuntimeError('production meter drift')
        if hashlib.sha256(support.read(r2,'/etc/mihomo/config.yaml')).hexdigest()!='73e830e5085eb7dd2b80bbde5bd25efbb9ea19141d798cb917086ba54c584674':raise RuntimeError('production config drift')
        out['before']=run("date -Is\npidof mihomo\nsha256sum /etc/config/dhcp /etc/routermgmt/tunnel.conf /etc/mihomo/config.yaml\n")
        run('test ! -e /etc/routermgmt/proxy-meter-io.py')
        run('test ! -e '+shlex.quote(stage)+' && mkdir -m 700 '+shlex.quote(stage))
        out['scripts']={}
        for name in ('proxy-meter-io.py','proxy-meter-endpoints.sh','proxy-meter-v3.sh'):
            raw=(ROOT/'scripts/qos-r2'/name).read_bytes()
            if b'\r' in raw:raise RuntimeError('CRLF in candidate')
            support.scp_put(r2,stage,name,raw,0o700)
            out['scripts'][name]=hashlib.sha256(raw).hexdigest()
        out['syntax']=run('sh -n '+stage+'/proxy-meter-v3.sh && sh -n '+stage+'/proxy-meter-endpoints.sh && python3 -m py_compile '+stage+'/proxy-meter-io.py')
        run('cp /etc/routermgmt/proxy-meter-rules-v3.conf '+stage+'/rules.conf')
        # No production rules/counters are changed by this staged registry rehearsal.
        out['registry_rehearsal']=run('python3 '+stage+'/proxy-meter-io.py generate --config /etc/mihomo/config.yaml --providers /etc/mihomo/proxy_providers --rules '+stage+'/rules.conf --cover '+stage+'/coverage.json')
        staged=support.read(r2,stage+'/coverage.json');coverage=json.loads(staged)
        out['coverage_summary']={k:coverage[k] for k in ('coverage_schema','registered_rules','proxy_nodes','filtered_metadata_count','coverage_complete')}
        out['coverage_summary']['unresolved_count']=len(coverage['unresolved_nodes'])
        if support.read(r2,stage+'/rules.conf')!=support.read(r2,'/etc/routermgmt/proxy-meter-rules-v3.conf'):raise RuntimeError('registry would change counters')
        check=run('python3 '+stage+'/proxy-meter-io.py coverage --rules '+stage+'/rules.conf --cover '+stage+'/coverage.json --active')
        out['coverage_preflight']=check
        if 'COVER_NOTE=ok' not in check['stdout']:raise RuntimeError('candidate coverage health failed')
        # One lock covers backups and component activation. No cron or core changes.
        install='''set -eu
exec 9>/etc/routermgmt/state/proxy-meter-v3.lock
flock -n 9
mkdir -m 700 BACKUP
for name in proxy-meter-v3.sh proxy-meter-endpoints.sh proxy-meter-coverage-v3.json; do cp -p /etc/routermgmt/$name BACKUP/$name; done
cp -p /etc/routermgmt/state/proxy-meter-v3.json BACKUP/state-before.json
for name in proxy-meter-io.py proxy-meter-endpoints.sh proxy-meter-v3.sh; do
 cp STAGE/$name /etc/routermgmt/$name.new
 chmod 700 /etc/routermgmt/$name.new
 EXPECTED_CASE
 test "$(sha256sum /etc/routermgmt/$name.new | cut -d ' ' -f 1)" = "$expected"
 sha256sum /etc/routermgmt/$name.new
 if [ "$name" != proxy-meter-io.py ]; then sh -n /etc/routermgmt/$name.new; fi
 mv /etc/routermgmt/$name.new /etc/routermgmt/$name
done
cp STAGE/coverage.json /etc/routermgmt/proxy-meter-coverage-v3.json.new
chmod 600 /etc/routermgmt/proxy-meter-coverage-v3.json.new
mv /etc/routermgmt/proxy-meter-coverage-v3.json.new /etc/routermgmt/proxy-meter-coverage-v3.json
'''.replace('BACKUP',backup).replace('STAGE',stage)
        # SCP already verified candidate bytes; compare .new SHA before each mv too.
        expected_case='case "$name" in\n'+'\n'.join(name+') expected='+h+' ;;' for name,h in out['scripts'].items())+'\nesac'
        install=install.replace('EXPECTED_CASE',expected_case)
        out['install']=run(install)
        for name,h in out['scripts'].items():
            if hashlib.sha256(support.read(r2,'/etc/routermgmt/'+name)).hexdigest()!=h:raise RuntimeError('installed SHA mismatch')
        out['first_sample']=run('/etc/routermgmt/proxy-meter-v3.sh')
        state=json.loads(support.read(r2,'/etc/routermgmt/state/proxy-meter-v3.json'))
        out['state']={k:state.get(k) for k in ('seq','day_key','day_total','prev_epoch','health','partial','quota_eligible','coverage_complete','crossday_unassigned')}
        if state['health']!='ok' or state['quota_eligible']:raise RuntimeError('unexpected production meter qualification')
        out['after']=run("date -Is\npidof mihomo\nsha256sum /etc/config/dhcp /etc/routermgmt/tunnel.conf /etc/mihomo/config.yaml\n")
        out['technical_observation_start']=dt.datetime.fromtimestamp(state['prev_epoch'],dt.timezone(dt.timedelta(hours=8))).isoformat()
        out['qualified_observation_start']=None
        out['result']='DEPLOYED_OBSERVE_HEALTHY_QUOTA_BLOCKED_COVERAGE'
    except Exception as exc:
        out['error_type']=type(exc).__name__;out['result']='STOPPED_REVIEW_CHECKPOINT'
        # Leave a deterministic checkpoint; never blindly roll counters/state backwards.
    finally:
        if r2:r2.close()
        if gz:gz.close()
        out['finished']=dt.datetime.now().astimezone().isoformat()
        p=ROOT/'review'/('Batch12计量修复R2部署-'+stamp+'-Codex.json')
        with p.open('x',encoding='utf-8') as f:json.dump(out,f,ensure_ascii=False,indent=2)
        print(json.dumps({'evidence':str(p.relative_to(ROOT)),'result':out.get('result'),'error_type':out.get('error_type'),'state':out.get('state'),'coverage_summary':out.get('coverage_summary')},ensure_ascii=False))

if __name__=='__main__':main()
