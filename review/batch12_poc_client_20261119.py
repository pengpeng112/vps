"""R2 sandbox test driver. Config already staged; starts only its own gate process."""
import argparse
import concurrent.futures
import hashlib
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import time

def main():
    p=argparse.ArgumentParser();p.add_argument('--dir',required=True);p.add_argument('--phase',choices=['release','rates','steady','resources'],required=True);a=p.parse_args()
    work=Path(a.dir);params=json.loads((work/'params.json').read_text());result={}
    (work/'driver.pid').write_text(str(os.getpid()))
    pattern=bytes(range(256))*16
    def expected(size):return hashlib.sha256((pattern*((size+4095)//4096))[:size]).hexdigest()
    def ctl(cmd):
        with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as c:
            c.settimeout(3);c.connect(str(work/'ctl.sock'));c.sendall((json.dumps(cmd)+'\n').encode());raw=b''
            while b'\n' not in raw:raw+=c.recv(65536)
        r=json.loads(raw)
        if not r.get('ok'):raise RuntimeError('control command rejected')
        return r
    def resume():ctl({'cmd':'clear-emergency','expected_generation':ctl({'cmd':'status'})['generation']})
    def curl(size,port,ident,timeout=120):
        dest=work/(ident+'.bin');url=params['base']+'/'+str(size)
        start=time.monotonic()
        c=subprocess.run(['curl','--noproxy','','-fsS','-m',str(timeout),'--socks5-hostname','127.0.0.1:'+str(port),'-o',str(dest),'-w','%{http_code} %{size_download} %{time_starttransfer}',url],capture_output=True,text=True,timeout=timeout+5)
        elapsed=time.monotonic()-start;raw=dest.read_bytes() if dest.exists() else b''
        out={'rc':c.returncode,'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest(),'expected_sha256':expected(size),'seconds':elapsed,'bytes_per_second':len(raw)/elapsed,'http':c.stdout.strip().split()[:1]}
        result['last_payload']=out
        dest.unlink(missing_ok=True)
        if c.returncode or len(raw)!=size or out['sha256']!=out['expected_sha256']:raise RuntimeError('incomplete or corrupt fixed payload')
        return out
    def launch_gate():
        log=open(work/'gate.log','ab')
        proc=subprocess.Popen([str(work/'qos-gate'),'-config',str(work/'gate.json')],stdout=log,stderr=log,start_new_session=True);log.close()
        (work/'gate.pid').write_text(str(proc.pid))
        for _ in range(80):
            if (work/'ctl.sock').exists():
                try:ctl({'cmd':'status'});return proc
                except Exception:pass
            time.sleep(.05)
        raise RuntimeError('gate did not start')
    try:
        if a.phase=='release':
            gate=launch_gate();resume();ctl({'cmd':'set-rate','class':'video','down_bps':8})
            dest=work/'blocked.bin';url=params['base']+'/524288'
            c=subprocess.Popen(['curl','--noproxy','','-fsS','-m','20','--socks5-hostname','127.0.0.1:7895','-o',str(dest),url],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            (work/'curl.pid').write_text(str(c.pid))
            try:
                for _ in range(100):
                    if ctl({'cmd':'status'})['video']['waiting']>0:break
                    time.sleep(.02)
                else:raise RuntimeError('no blocked connection before release')
                before=dest.stat().st_size if dest.exists() else 0
                start=time.monotonic();r=ctl({'cmd':'release'});command_time=time.monotonic()-start
                for _ in range(150):
                    current=dest.stat().st_size if dest.exists() else 0
                    if current>before:break
                    time.sleep(.02)
                recovery=time.monotonic()-start;c.wait(timeout=15)
                raw=dest.read_bytes()
                if c.returncode or hashlib.sha256(raw).hexdigest()!=expected(524288) or recovery>3:raise RuntimeError('same-connection release failed')
                result['same_connection_release']={'command_seconds':command_time,'payload_recovery_seconds':recovery,'bytes':len(raw),'waiting_before_release':True,'same_curl_pid':c.pid}
            finally:
                if c.poll() is None:c.terminate();c.wait(timeout=5)
                dest.unlink(missing_ok=True)
            ctl({'cmd':'reload'})
            os.kill(gate.pid,signal.SIGHUP);time.sleep(.1)
            if not ctl({'cmd':'status'})['video']['emergency']:raise RuntimeError('reload/SIGHUP cleared lock')
            generation=ctl({'cmd':'status'})['generation'];gate.terminate();gate.wait(timeout=5);gate=launch_gate()
            st=ctl({'cmd':'status'})
            if not st['video']['emergency'] or st['generation']!=generation:raise RuntimeError('restart lost emergency/generation')
            result['restart_lock_preserved']=True;resume();result['baseline']=curl(524288,7894,'baseline',30)
        elif a.phase=='rates':
            resume();result['rounds']=[]
            for n in (1,2,4):
                per=524288//n
                def one_class(port,label):
                    start=time.monotonic()
                    with concurrent.futures.ThreadPoolExecutor(n) as pool:items=list(pool.map(lambda i:curl(per,port,label+str(i)),range(n)))
                    seconds=time.monotonic()-start;rate=sum(x['bytes'] for x in items)/seconds
                    print('RATE_CLASS '+json.dumps({'class':label,'connections':n,'bytes_per_second':rate,'complete_bytes':sum(x['bytes'] for x in items)}),flush=True)
                    if not 6200<=rate<=8500:raise RuntimeError('aggregate rate outside declared tolerance')
                    return {'class':label,'connections':n,'total_bytes':sum(x['bytes'] for x in items),'seconds':seconds,'bytes_per_second':rate,'files':items}
                with concurrent.futures.ThreadPoolExecutor(2) as pool:
                    v=pool.submit(one_class,7895,'video');d=pool.submit(one_class,7896,'download');rounds=[v.result(),d.result()]
                result['rounds'].extend(rounds)
                print('ROUND_DONE connections='+str(n),flush=True)
            result['gate_stats']=ctl({'cmd':'status'})['stats']
        elif a.phase=='resources':
            resume();workers=[];peaks={};previous={};hz=os.sysconf('SC_CLK_TCK')
            try:
                for i in range(8):
                    workers.append(subprocess.Popen(['curl','--noproxy','','-fsS','-m','20','--socks5-hostname','127.0.0.1:'+str(7895+i%2),'-o','/dev/null',params['base']+'/67108864'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL))
                for _ in range(40):
                    now=time.monotonic()
                    for label in ('gate','mihomo'):
                        pid=int((work/(label+'.pid')).read_text());proc=Path('/proc')/str(pid)
                        fields=proc.joinpath('stat').read_text().rsplit(')',1)[1].split();ticks=int(fields[11])+int(fields[12])
                        status={ln.split(':')[0]:ln.split(':',1)[1].strip() for ln in proc.joinpath('status').read_text().splitlines() if ':' in ln}
                        data=peaks.setdefault(label,{'rss_kib_peak':0,'fd_peak':0,'one_core_cpu_percent_peak':0})
                        data['rss_kib_peak']=max(data['rss_kib_peak'],int(status['VmRSS'].split()[0]));data['fd_peak']=max(data['fd_peak'],len(list(proc.joinpath('fd').iterdir())))
                        if label in previous:
                            t0,v0=previous[label];data['one_core_cpu_percent_peak']=max(data['one_core_cpu_percent_peak'],100*(ticks-v0)/hz/(now-t0))
                        previous[label]=(now,ticks)
                    time.sleep(.25)
                result['sampled_peaks']=peaks;result['parallel_connections']=8;result['sample_seconds']=10
                if peaks['gate']['rss_kib_peak']>16384 or peaks['gate']['fd_peak']>48:raise RuntimeError('gate exceeds declared PoC resource budget')
            finally:
                for c in workers:
                    if c.poll() is None:c.terminate()
                for c in workers:c.wait(timeout=5)
            time.sleep(.5)
            result['post_cancel_gate_stats']=ctl({'cmd':'status'})['stats']
        else:
            resume();dest=work/'steady.bin'
            c=subprocess.Popen(['curl','--noproxy','','-fsS','-m','260','--socks5-hostname','127.0.0.1:7895','-o',str(dest),params['base']+'/67108864'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            (work/'curl.pid').write_text(str(c.pid));result['curl_pid']=c.pid
            # Local orchestrator reads counters after the warmup marker and
            # before the finish marker; same uninterrupted connection throughout.
            time.sleep(35);result['warmup_stats']=ctl({'cmd':'status'});result['warmup_epoch']=time.time()
            print('STEADY_WARM',flush=True)
            (work/'steady-warmup.json').write_text(json.dumps(result))
            time.sleep(180);result['finish_stats']=ctl({'cmd':'status'});result['finish_epoch']=time.time()
            result['window_payload_bytes']=result['finish_stats']['stats']['video']['down']-result['warmup_stats']['stats']['video']['down']
            result['window_seconds']=result['finish_epoch']-result['warmup_epoch']
            (work/'steady-finish.json').write_text(json.dumps(result));print('STEADY_END',flush=True)
            c.terminate();c.wait(timeout=5);dest.unlink(missing_ok=True)
        result['ok']=True
    except Exception as e:
        result['ok']=False;result['error_type']=type(e).__name__
        if type(e) is RuntimeError:result['error_code']=str(e)
    (work/(a.phase+'-result.json')).write_text(json.dumps(result))
    print('PHASE_RESULT '+json.dumps(result),flush=True)
    return 0 if result['ok'] else 1

if __name__=='__main__':raise SystemExit(main())
