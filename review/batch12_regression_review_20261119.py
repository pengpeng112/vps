"""Run missing negative cases in copied Go source / fake meter only. No production I/O."""
from pathlib import Path
import datetime as dt
import importlib.util
import json
import os
import shutil
import subprocess
import tempfile
import types
import sys
import hashlib

ROOT = Path(__file__).resolve().parents[1]

GO_CASES = r'''
package main
import("testing";"path/filepath";"net";"io";"time";"os")
func reviewGate(t *testing.T)(*Gate,*GateCtl){
 d:=t.TempDir(); cfgPath=filepath.Join(d,"cfg.json")
 writeCfg(t,cfgPath,"127.0.0.1:0","127.0.0.1:0","127.0.0.1:0","127.0.0.1:7894",60000,d)
 g:=NewGate(mustCfg(t,cfgPath)); t.Cleanup(func(){g.video.Stop();g.dl.Stop();g.elephant.Stop()})
 return g,NewGateCtl(g)
}
func TestReviewEmergencySurvivesReload(t *testing.T){
 g,c:=reviewGate(t);c.dispatch(ctlReq{Cmd:"release"});r:=c.dispatch(ctlReq{Cmd:"reload"})
 if !g.video.Stats()["emergency"].(bool){t.Fatalf("release -> reload: emergency=false, disk_emergency=%v response=%v",LoadState(g.cfg.StateFile).Emergency,r)}
}
func TestReviewEmergencySurvivesSetRate(t *testing.T){
 g,c:=reviewGate(t);c.dispatch(ctlReq{Cmd:"release"});c.dispatch(ctlReq{Cmd:"set-rate",Class:"video",DownBPS:60000})
 if !g.video.Stats()["emergency"].(bool){t.Fatal("release -> set-rate silently reinstates cap without clear-emergency")}
}
func TestReviewGenerationSurvivesRestart(t *testing.T){
 g,_:=reviewGate(t);if err:=SaveState(g.cfg.StateFile,GateState{Emergency:true,Generation:77});err!=nil{t.Fatal(err)}
 g2:=NewGate(g.cfg);defer g2.video.Stop();defer g2.dl.Stop();defer g2.elephant.Stop()
 c2:=NewGateCtl(g2);c2.dispatch(ctlReq{Cmd:"release"});if st:=LoadState(g.cfg.StateFile);st.Generation<=77{t.Fatalf("persisted generation rolled back from 77 to %d",st.Generation)}
}
func reviewTCPPair(t *testing.T)(*net.TCPConn,*net.TCPConn){
 ln,e:=net.ListenTCP("tcp",&net.TCPAddr{IP:net.ParseIP("127.0.0.1")});if e!=nil{t.Fatal(e)};defer ln.Close()
 a,e:=net.DialTCP("tcp",nil,ln.Addr().(*net.TCPAddr));if e!=nil{t.Fatal(e)}
 b,e:=ln.AcceptTCP();if e!=nil{t.Fatal(e)};t.Cleanup(func(){a.Close();b.Close()});return a,b
}
func TestReviewHalfCloseStillReceivesResponse(t *testing.T){
 peer,client:=reviewTCPPair(t);up,server:=reviewTCPPair(t)
 bucket:=NewBucket("halfclose",0,0);defer bucket.Stop();stats:=&ClassStats{}
 go pipeConn(client,up,bucket,stats)
 go func(){io.ReadAll(server);time.Sleep(80*time.Millisecond);server.Write([]byte("response-after-request-eof"));server.CloseWrite()}()
 peer.Write([]byte("request"));peer.CloseWrite();peer.SetReadDeadline(time.Now().Add(time.Second))
 got,_:=io.ReadAll(peer);if string(got)!="response-after-request-eof"{t.Fatalf("half-close discarded response: got %q",got)}
}
// The original defect was in the release test itself, not Take capping grants.
func TestReviewReleaseTestActuallyWaits(t *testing.T){TestEmergencyReleaseWakes(t)}
func TestReviewCorruptStateCannotReinstateCap(t *testing.T){
 g,_:=reviewGate(t);os.WriteFile(g.cfg.StateFile,[]byte("{corrupt"),0600)
 g2:=NewGate(g.cfg);defer g2.video.Stop();defer g2.dl.Stop();defer g2.elephant.Stop()
 if !g2.video.Stats()["unlimited"].(bool){t.Fatal("corrupt persistent state silently defaults emergency=false and enables cap")}
}
'''


def main():
    work = Path(tempfile.mkdtemp(prefix='batch12-review-', dir=ROOT/'logs'))
    gate_dir = work/'gate'
    shutil.copytree(ROOT/'mgmt/qos-gate', gate_dir)
    (gate_dir/'review_regression_test.go').write_text(GO_CASES, encoding='utf-8', newline='\n')
    go = subprocess.run(['go','test','-count=1','-run','TestReview','-v','./...'], cwd=gate_dir,
                        capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=45)
    helper_path=ROOT/'logs/batch1_meter_isotest_20261119.py'
    spec=importlib.util.spec_from_file_location('isolated_meter_fixture',helper_path)
    meter=importlib.util.module_from_spec(spec);spec.loader.exec_module(meter)
    def fixture_run(args, **kwargs):
        if args[0] == 'sh':
            args = ['D:/Program Files/Git/bin/sh.exe', *args[1:]]
            env=kwargs['env']
            env['PM_PYTHON']=Path(sys.executable).as_posix()
            env['PM_IO_HELPER']=(ROOT/'scripts/qos-r2/proxy-meter-io.py').as_posix()
            rules=Path(env['PM_RULES']);agent=Path(env['PM_AGENT'])
            cover={'coverage_schema':2,'rules_sha256':hashlib.sha256(rules.read_bytes()).hexdigest(),
                   'source_sha256':{str(agent):hashlib.sha256(agent.read_bytes()).hexdigest()},
                   'registered_nodes':{'EP1':['10.0.0.1','tcp','443']},'unresolved_nodes':[],
                   'coverage_complete':True}
            Path(env['PM_COVER']).write_text(json.dumps(cover),encoding='utf-8')
            if '/bad_cover/' in kwargs.get('env', {}).get('PM_STATE', ''):
                env=kwargs['env']
                Path(env['PM_COVER']).write_text('{invalid-json',encoding='utf-8')
                fakecurl=Path(env['PM_COVER']).parent/'curl'
                fakecurl.write_text('#!/bin/sh\nprintf \'%s\\n\' \'{"now":"BWG-HY2-JPTY-01"}\'\n',encoding='utf-8',newline='\n')
                fakecurl.chmod(0o755)
                env['PM_CHECK_ACTIVE']='1'
        return subprocess.run(args, **kwargs)
    meter.subprocess=types.SimpleNamespace(run=fixture_run)
    midnight=dt.datetime(2026,11,20,0,0,tzinfo=dt.timezone(dt.timedelta(hours=8)))
    epoch=int(midnight.timestamp())
    base={'schema':3,'counter_key_format':'direction-v1','meter_id':'rtr-test','seq':10,
          'boot_id':'BOOT-A','continuity_id':'cont-A','prev_continuity':'cont-A',
          'prev_epoch':epoch-180,'day_key':'2026-11-19','day_total':1000,
          'day_first_at':'2026-11-19 00:00:00+0800','health':'ok','partial':False,
          'quota_eligible':True,'read_fail_eps':'','endpoints':{'EP1_up':100,'EP1_down':200}}
    out=' 150 150000 0 0 tcp dpt:443 /* EP1 */\n'
    inn=' 200 200000 0 0 tcp dpt:443 /* EP1 */\n'
    cases={}
    cases['cross_2357_to_0002']=meter.run_case(work,'midnight',dict(base),epoch+120,'BOOT-A',out,inn)
    same=dict(base,day_key='2026-11-20',day_first_at='2026-11-20 00:00:00+0800',prev_epoch=epoch+300,endpoints={'EP1_up':100})
    cases['new_endpoint_unknown_previous_bytes']=meter.run_case(work,'new_endpoint',same,epoch+600,'BOOT-A',out,inn)
    # An internally inconsistent prior day key must not pass as a clean rollover.
    same=dict(base,day_key='2026-11-18',prev_epoch=epoch+300)
    cases['inconsistent_day_key']=meter.run_case(work,'bad_day_key',same,epoch+600,'BOOT-A',out,inn)
    same=dict(base,day_key='2026-11-20',day_first_at='2026-11-20 00:00:00+0800',prev_epoch=epoch+300)
    cases['corrupt_coverage_json_with_working_api']=meter.run_case(work,'bad_cover',same,epoch+600,'BOOT-A',out,inn)
    result={'at':dt.datetime.now().astimezone().isoformat(),'isolated_workspace':str(work.relative_to(ROOT)),
            'go_regression_returncode':go.returncode,'go_regression_output':go.stdout+go.stderr,
            'meter_cases':cases,'production_touched':False}
    expected_ineligible=('cross_2357_to_0002','new_endpoint_unknown_previous_bytes','inconsistent_day_key','corrupt_coverage_json_with_working_api')
    result['meter_required_assertions']={name:cases[name].get('state',{}).get('quota_eligible') is False
                                         for name in expected_ineligible}
    p=ROOT/('review/Batch12缺失负例隔离复现-20261119-'+dt.datetime.now().strftime('%H%M%S')+'-Codex.json')
    with p.open('x',encoding='utf-8',newline='\n') as f:json.dump(result,f,ensure_ascii=False,indent=2)
    print(go.stdout+go.stderr)
    for name,r in cases.items():
        print(name,json.dumps({'rc':r['rc'],'state':r['state'],'daily':r['daily']},ensure_ascii=False))
    print('evidence:',p.relative_to(ROOT))
    return 0 if go.returncode==0 and all(result['meter_required_assertions'].values()) else 1


if __name__=='__main__':raise SystemExit(main())
