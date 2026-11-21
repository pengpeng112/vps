"""Explicit R2 pure-meter repair; no rate limit, YAML edits, restart or routing changes."""
from pathlib import Path
import datetime
import hashlib
import json
import re
import shlex
import time
from cf_repair_tools_20261118 import ROOT,BACKUP,require_lock,ns_helpers,read_bytes,put_bytes


def main():
    require_lock()
    evidence=json.loads((ROOT/'review/计量修复隔离测试-20261118-Codex.json').read_text(encoding='utf-8'))
    for name,key in [('proxy-meter-v3.sh','source_sha256'),('proxy-meter-endpoints.sh','generator_sha256')]:
        assert hashlib.sha256((ROOT/'scripts/qos-r2'/name).read_bytes()).hexdigest()==evidence[key]
    assert all(x['pass'] for x in evidence['checks'])
    ns=ns_helpers();gz=ns['connect']('134.175.151.213',28722,'ubuntu',ns['DEV_PW'])
    c=ns['connect']('127.0.0.1',13223,'root',ns['DEV_PW'],gz);run=ns['run']
    report={'started':datetime.datetime.now().astimezone().isoformat(timespec='seconds'),'device':'R2','limiting_enabled':False}
    out=ROOT/'review/R2计量修复部署实证-20261118-Codex.json'
    assert not out.exists()
    local=BACKUP/'R2-meter';local.mkdir(exist_ok=False)
    try:
        paths=['/etc/routermgmt/proxy-meter-v3.sh','/etc/routermgmt/proxy-meter-endpoints.sh',
               '/etc/routermgmt/proxy-meter-rules-v3.conf','/etc/routermgmt/proxy-meter-coverage-v3.json',
               '/etc/routermgmt/state/proxy-meter-v3.json','/etc/routermgmt/state/proxy-meter-daily-v3.json']
        before={p:read_bytes(c,p) for p in paths}
        expected=(BACKUP/'local-before/scripts/qos-r2/proxy-meter-v3.sh').read_bytes()
        assert before[paths[0]]==expected,'Live meter drift; refusing to replace'
        for p,b in before.items():(local/Path(p).name).write_bytes(b)
        report['protected_before']=run(c,'sha256sum /etc/mihomo/config.yaml /etc/config/dhcp /etc/routermgmt/tunnel.conf /etc/routermgmt/self-heal.sh\npidof mihomo\ntc qdisc show dev wan')
        bk=run(c,'umask 077; mktemp -d /etc/routermgmt/codex-meterfix.XXXXXX')['stdout']
        assert re.fullmatch(r'/etc/routermgmt/codex-meterfix\.[A-Za-z0-9]+',bk)
        report['remote_backup']=bk
        for p in paths:
            r=run(c,'cp -p '+shlex.quote(p)+' '+shlex.quote(bk+'/'+Path(p).name))
            assert r['rc']==0
        snapshots={ch:run(c,'iptables -S '+ch)['stdout'] for ch in ('PROXY_METER_OUT','PROXY_METER_IN')}
        (local/'iptables-before.json').write_text(json.dumps(snapshots,indent=2)+'\n',encoding='utf-8')
        commands=[];removed={}
        for ch,text in snapshots.items():
            seen=set();duplicates=[];index=0
            for line in text.splitlines():
                args=shlex.split(line)
                if args[:2]!=['-A',ch]:continue
                index+=1
                if '-j' in args or '--jump' in args or '--comment' not in args:continue
                key=tuple(args[2:])
                if key in seen:duplicates.append(index)
                else:seen.add(key)
            removed[ch]=len(duplicates)
            commands.extend('iptables -D '+ch+' '+str(i)+' || exit 8' for i in reversed(duplicates))
        report['duplicate_counter_rules_removed']=removed
        newmeter=(ROOT/'scripts/qos-r2/proxy-meter-v3.sh').read_bytes()
        newgen=(ROOT/'scripts/qos-r2/proxy-meter-endpoints.sh').read_bytes()
        put_bytes(c,bk+'/meter.new',newmeter,0o755);put_bytes(c,bk+'/generator.new',newgen,0o755)

        # Real BusyBox shell/awk compatibility test, with all firewall calls mocked.
        testdir=run(c,'umask 077; mktemp -d /tmp/rmg-meter-test.XXXXXX')['stdout']
        assert re.fullmatch(r'/tmp/rmg-meter-test\.[A-Za-z0-9]+',testdir)
        mock=r'''fixture_iptables() {
 case "$1:$2" in
  -S:OUTPUT) echo '-A OUTPUT -j PROXY_METER_OUT' ;;
  -S:INPUT) echo '-A INPUT -j PROXY_METER_IN' ;;
  -L:PROXY_METER_OUT) echo "1 $TEST_UP /* ep */" ;;
  -L:PROXY_METER_IN) echo "1 $TEST_DOWN /* ep */" ;;
  *) return 0 ;;
 esac
}
fixture_flock() { return 0; }
'''
        source=newmeter.decode();source=re.sub(r'\biptables\b','fixture_iptables',source);source=re.sub(r'\bflock\b','fixture_flock',source)
        source=source.replace('set -u\n','set -u\n'+mock,1)
        put_bytes(c,testdir+'/test.sh',source.encode(),0o700)
        put_bytes(c,testdir+'/rules',b'ep|up|203.0.113.1|tcp|443\nep|down|203.0.113.1|tcp|443\n')
        put_bytes(c,testdir+'/coverage',b'{"unresolved_names":""}\n');put_bytes(c,testdir+'/agent',b'DEVICE_ID=fixture\n')
        report['busybox_fixture']=[]
        for i,(up,down) in enumerate([(100,200),(150,280),(180,350)]):
            env={'PM_STATE':testdir+'/state','PM_DAY':testdir+'/day','PM_LOG':testdir+'/log','PM_RULES':testdir+'/rules',
                 'PM_COVER':testdir+'/coverage','PM_AGENT':testdir+'/agent','PM_LOCK':testdir+'/lock','PM_CHECK_ACTIVE':'0',
                 'PM_BOOT_ID':'fixture','PM_NOW_EPOCH':str(1795000000+i*300),'TEST_UP':str(up),'TEST_DOWN':str(down)}
            cmd=' '.join(k+'='+shlex.quote(v) for k,v in env.items())+' sh '+shlex.quote(testdir+'/test.sh')+'; cat '+shlex.quote(testdir+'/day')
            r=run(c,cmd);day=json.loads(r['stdout'].splitlines()[-1]);report['busybox_fixture'].append(day)
        assert [x['delta_last'] for x in report['busybox_fixture']]==[0,130,100]
        assert report['busybox_fixture'][-1]['total']==230
        for f in ('meter.new','generator.new'):
            assert run(c,'sh -n '+shlex.quote(bk+'/'+f))['rc']==0

        # Serialize with the existing sampler; assert rule layout has not drifted.
        script='set -eu\nexec 9>/etc/routermgmt/state/proxy-meter-v3.lock\nflock -n 9 || exit 9\n'
        for ch,text in snapshots.items():
            # sha256sum reads the same newline-normalized iptables -S output.
            digest=hashlib.sha256((text+'\n').encode()).hexdigest()
            script+='test "$(iptables -S '+ch+' | sha256sum | cut -c1-64)" = '+shlex.quote(digest)+' || exit 7\n'
        script+='\n'.join(commands)+'\n'
        script+='mv '+shlex.quote(bk+'/meter.new')+' /etc/routermgmt/proxy-meter-v3.sh\n'
        script+='mv '+shlex.quote(bk+'/generator.new')+' /etc/routermgmt/proxy-meter-endpoints.sh\n'
        script+='/etc/routermgmt/proxy-meter-endpoints.sh\necho INSTALLED\n'
        report['install']=run(c,script,timeout=40)
        (local/'install-unredacted.json').write_text(json.dumps(report['install'],ensure_ascii=False,indent=2),encoding='utf-8')
        report['install']['stdout']=re.sub(r'unresolved=[^\n]*','unresolved=<provider names withheld; domain coverage incomplete>',report['install']['stdout'])
        assert report['install']['rc']==0,'Meter install stopped; inspect checkpoint'
        r=run(c,'/etc/routermgmt/proxy-meter-v3.sh; cat /etc/routermgmt/state/proxy-meter-v3.json',timeout=45)
        state1=json.loads(r['stdout'].splitlines()[-1]);report['state_first']=state1
        assert state1['counter_key_format']=='direction-v1' and state1['partial'] and state1['quota_eligible'] is False
        report['bounded_proxy_get']=run(c,"curl --noproxy '' -fsS -m 25 -x http://127.0.0.1:7890 -o /dev/null -w '%{http_code} %{size_download} %{time_total}' 'https://speed.cloudflare.com/__down?bytes=65536'",timeout=30)
        time.sleep(2)
        r=run(c,'/etc/routermgmt/proxy-meter-v3.sh; cat /etc/routermgmt/state/proxy-meter-v3.json; cat /etc/routermgmt/state/proxy-meter-daily-v3.json',timeout=45)
        lines=r['stdout'].splitlines();state2=json.loads(lines[-2]);day=json.loads(lines[-1]);report['state_second']=state2;report['day_second']=day
        expected_delta=sum(state2['endpoints'][k]-v for k,v in state1['endpoints'].items())
        assert day['delta_last']==expected_delta and expected_delta>0
        assert state2['day_total']-state1['day_total']==expected_delta
        report['both_direction_delta_verified']=True
        report['rule_counts']=run(c,"iptables -S PROXY_METER_OUT | awk '/^-A /{n++}END{print n}'; iptables -S PROXY_METER_IN | awk '/^-A /{n++}END{print n}'")
        report['protected_after']=run(c,'sha256sum /etc/mihomo/config.yaml /etc/config/dhcp /etc/routermgmt/tunnel.conf /etc/routermgmt/self-heal.sh\npidof mihomo\ntc qdisc show dev wan')
        assert report['protected_after']['stdout']==report['protected_before']['stdout']
        report['final_hashes']=run(c,'sha256sum /etc/routermgmt/proxy-meter-v3.sh /etc/routermgmt/proxy-meter-endpoints.sh')
        report['success']=True
    finally:
        c.close();gz.close();report['finished']=datetime.datetime.now().astimezone().isoformat(timespec='seconds')
        out.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
