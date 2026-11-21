"""Offline tests for the exact deployed handler AUTO-restore block."""
from pathlib import Path
import hashlib,json,os,shutil,subprocess,tempfile
ROOT=Path(__file__).resolve().parents[1]
BASH=Path(shutil.which('git')).resolve().parent.parent/'bin/bash.exe'
src=(ROOT/'mgmt/agent/handlers/switch_node.sh').read_text(encoding='utf-8')
a=src.index('    # Restore automatic failover before committing')
b=src.index('\n    if [ "$_healthy" != "1" ]; then',a)
block=src[a:b]
fixture=Path(tempfile.mkdtemp(prefix='auto-restore-',dir=ROOT/'backups'))
tests=[]
for case,env,expect in [('normal',{},'1 AUTO'),('put-failure',{'PUT_FAIL':'1'},'0 AUTO'),
                        ('readback-mismatch',{'BAD_READBACK':'1'},'0 BWG'),
                        ('probe-failure',{'PROBE_FAIL':'1'},'0 AUTO'),
                        ('reload-keeps-selection',{'TASK_TYPE':'reload_proxy'},'1 BWG')]:
    script='''set -u
_healthy=1; _type=${TASK_TYPE:-switch_node}; _node=BWG; selected=BWG
oc_set_group() { selected=$2; [ "${PUT_FAIL:-0}" != 1 ]; }
oc_get_group() { if [ "${BAD_READBACK:-0}" = 1 ]; then echo BWG; else echo "$selected"; fi; }
probe_egress() { [ "${PROBE_FAIL:-0}" != 1 ] && echo 203.0.113.1; }
sleep() { return 0; }
'''+block+'\nprintf "%s %s" "$_healthy" "$_grp"\n'
    p=fixture/(case+'.sh');p.write_text(script,encoding='utf-8',newline='\n')
    r=subprocess.run([str(BASH),str(p)],env={**os.environ,**env},capture_output=True,text=True,timeout=10)
    tests.append({'case':case,'expected':expect,'actual':r.stdout,'pass':r.returncode==0 and r.stdout==expect})
assert all(x['pass'] for x in tests),tests
assert b<src.index('rm -f "$_txn/uncommitted"',b)
out={'source_sha256':hashlib.sha256((ROOT/'mgmt/agent/handlers/switch_node.sh').read_bytes()).hexdigest(),
     'local_only':True,'calls_mocked':True,'restore_before_transaction_commit':True,'tests':tests}
(ROOT/'review/AUTO恢复处理器隔离测试-20261118-Codex.json').write_text(json.dumps(out,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
print(json.dumps(out,ensure_ascii=False,indent=2))
