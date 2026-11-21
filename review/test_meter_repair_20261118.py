"""Local-only meter regression tests. All iptables operations are a fake Python DB."""
from pathlib import Path
import datetime
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile

ROOT=Path(__file__).resolve().parents[1]
BASH=Path(shutil.which('git')).resolve().parent.parent/'bin/bash.exe'
BASE=Path(tempfile.mkdtemp(prefix='meter-repair-',dir=ROOT/'backups'))
FAKE=BASE/'fake_iptables.py'
FAKE.write_text('''import json,os,sys
from pathlib import Path
p=Path(os.environ['FAKE_DB']);d=json.loads(p.read_text())
a=sys.argv[1:];op=a[0];ch=a[1] if len(a)>1 else ''
def save():p.write_text(json.dumps(d))
if op=='-N':
 d.setdefault(ch,[]);save();raise SystemExit
if op=='-S':
 for rule in d.get(ch,[]):print('-A '+ch+' '+' '.join(rule))
elif op=='-C':raise SystemExit(0 if a[2:] in d.get(ch,[]) else 1)
elif op=='-A':d.setdefault(ch,[]).append(a[2:]);save()
elif op=='-I':d.setdefault(ch,[]).insert(0,a[3:]);save()
elif op=='-n':raise SystemExit(0 if a[-1] in d else 1)
elif op=='-L':
 for rule in d.get(ch,[]):
  tag=rule[rule.index('--comment')+1]
  direction='up' if ch.endswith('OUT') else 'down'
  v=json.loads(Path(os.environ['FAKE_VALUES']).read_text())[direction]
  print('1 %s /* %s */'%(v,tag))
else:raise SystemExit(2)
''',encoding='utf-8')
source=(ROOT/'scripts/qos-r2/proxy-meter-v3.sh').read_text(encoding='utf-8')
prefix='''fixture_iptables() { "$FIX_PYTHON" "$FIX_FAKE" "$@"; }
fixture_flock() { return 0; }
'''
source=re.sub(r'\biptables\b','fixture_iptables',source)
source=re.sub(r'\bflock\b','fixture_flock',source)
copy=BASE/'meter.sh';copy.write_text(source.replace('set -u\n','set -u\n'+prefix,1),encoding='utf-8',newline='\n')
checks=[]


def scenario(name,values,initial=None,jump_missing=False):
    case=BASE/name;case.mkdir()
    (case/'agent').write_text('DEVICE_ID=fixture\n')
    (case/'rules').write_text('ep|up|203.0.113.1|tcp|443\nep|down|203.0.113.1|tcp|443\n')
    (case/'coverage').write_text('{"unresolved_names":""}\n')
    db={'PROXY_METER_OUT':[],'PROXY_METER_IN':[],
        'OUTPUT':[] if jump_missing else [['-j','PROXY_METER_OUT']],
        'INPUT':[] if jump_missing else [['-j','PROXY_METER_IN']]}
    (case/'db').write_text(json.dumps(db))
    if initial:(case/'state').write_text(json.dumps(initial,separators=(',',':'))+'\n')
    results=[]
    for i,(up,down) in enumerate(values):
        (case/'values').write_text(json.dumps({'up':up,'down':down}))
        env={**os.environ,'FIX_PYTHON':Path(sys.executable).as_posix(),'FIX_FAKE':FAKE.as_posix(),
             'FAKE_DB':(case/'db').as_posix(),'FAKE_VALUES':(case/'values').as_posix(),
             'PM_AGENT':(case/'agent').as_posix(),'PM_RULES':(case/'rules').as_posix(),
             'PM_COVER':(case/'coverage').as_posix(),'PM_STATE':(case/'state').as_posix(),
             'PM_DAY':(case/'daily').as_posix(),'PM_LOG':(case/'log').as_posix(),
             'PM_LOCK':(case/'lock').as_posix(),'PM_CHECK_ACTIVE':'0','PM_BOOT_ID':'fixture-boot',
             'PM_NOW_EPOCH':str(1795000000+i*300),'PM_NOW_ISO':'2026-11-18 00:00:00+0800'}
        r=subprocess.run([str(BASH),str(copy)],env=env,capture_output=True,text=True,timeout=30)
        if r.returncode not in (0,3):raise RuntimeError(name+':'+r.stderr+r.stdout)
        results.append({'rc':r.returncode,'daily':json.loads((case/'daily').read_text()),'state':json.loads((case/'state').read_text())})
    final=json.loads((case/'db').read_text())
    return results,final


def check(name,ok):
    checks.append({'name':name,'pass':bool(ok)})
    if not ok:raise AssertionError(name)


samples,db=scenario('both-directions',[(100,200),(150,280),(180,350)])
check('up+down deltas 130,100; total230',[x['daily']['delta_last'] for x in samples]==[0,130,100] and samples[-1]['daily']['total']==230)
check('same tag is represented by independent direction keys',set(samples[-1]['state']['endpoints'])=={'ep_up','ep_down'})
check('repeated sampling never adds duplicate rules',len(db['PROXY_METER_OUT'])==len(db['PROXY_METER_IN'])==1)
check('new partial baseline remains partial and quota stays disabled',all(x['state']['partial'] and x['state']['quota_eligible'] is False for x in samples))
_,db=scenario('jump-repair',[(0,0),(0,0)],jump_missing=True)
check('absent counter hooks restored once',len(db['INPUT'])==len(db['OUTPUT'])==1)
samples,_=scenario('large-integers',[(0,0),(3221225472,3221225472)])
check('3GiB per direction adds to exact6GiB without saturation',samples[-1]['daily']['total']==6442450944 and samples[-1]['state']['endpoints']['ep_up']==3221225472)
legacy={'schema':3,'meter_id':'fixture','seq':10,'boot_id':'fixture-boot','continuity_id':'old',
        'prev_epoch':1794999700,'day_key':samples[0]['state']['day_key'],'day_total':1000,'endpoints':{'ep':900}}
samples,_=scenario('legacy-state',[(100,200),(150,280)],initial=legacy)
check('migration preserves historical lower bound and counts only new deltas',samples[-1]['state']['day_total']==1130 and samples[0]['state']['partial'])
check('legacy migration explicitly degraded',samples[0]['daily']['health']=='degraded')
gen_case=BASE/'generator';gen_case.mkdir();(gen_case/'providers').mkdir()
(gen_case/'config').write_text('proxies:\n  - name: CF\n    server: 203.0.113.1\n    port: 443\n    type: vless\n  - name: LateHy2\n    server: 203.0.113.2\n    port: 443\n    type: hysteria2\nproxy-groups: []\n')
(gen_case/'providers/airport-g02.yaml').write_text('proxies:\n- name: same tuple\n  type: vless\n  server: 203.0.113.1\n  port: 443\n- name: domain with spaces\n  type: ss\n  server: node.example.org\n  port: 8443\n- name: zero indentation\n  server: 203.0.113.3\n  port: 8443\n  type: ss\n')
env={**os.environ,'EP_CONFIG':(gen_case/'config').as_posix(),'EP_PROVIDERS_DIR':(gen_case/'providers').as_posix(),
     'EP_RULES_OUT':(gen_case/'rules').as_posix(),'EP_COVER_OUT':(gen_case/'coverage').as_posix()}
r=subprocess.run([str(BASH),str(ROOT/'scripts/qos-r2/proxy-meter-endpoints.sh')],env=env,capture_output=True,text=True,timeout=30)
assert r.returncode==0,r.stderr+r.stdout
rules=(gen_case/'rules').read_text().splitlines();coverage=json.loads((gen_case/'coverage').read_text())
check('provider zero-indent records parsed; endpoint tuples deduplicated',len(rules)==6 and any('203.0.113.3' in x for x in rules))
check('protocol parsed after server/port before emitting',any('|203.0.113.2|udp|443' in x for x in rules))
check('unresolved provider names retained including spaces','domain with spaces' in coverage['unresolved_names'])
out={'at':datetime.datetime.now().astimezone().isoformat(timespec='seconds'),'local_only':True,'kernel_calls_mocked':True,
     'source_sha256':hashlib.sha256((ROOT/'scripts/qos-r2/proxy-meter-v3.sh').read_bytes()).hexdigest(),
     'generator_sha256':hashlib.sha256((ROOT/'scripts/qos-r2/proxy-meter-endpoints.sh').read_bytes()).hexdigest(),'fixture':str(BASE),'checks':checks}
(ROOT/'review/计量修复隔离测试-20261118-Codex.json').write_text(json.dumps(out,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
print(json.dumps(out,ensure_ascii=False,indent=2))
