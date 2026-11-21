"""Read-only post-deployment audit; selected metadata only, no API login or writes."""
from pathlib import Path
import concurrent.futures
import datetime
import json
import yaml

ROOT = Path(__file__).resolve().parents[1]
PORTS = {"R1":13222,"R2":13223,"R3":13227,"R4":13224,"R5":13225,"R6":13226,"R8":13230,"R9":13231,"Athena":13228}


def helpers():
    p=ROOT/'review/fleet-qos-readonly-audit-20261117.py'
    prefix, sep, _=p.read_text(encoding='utf-8-sig').partition('\ntry:\n')
    if not sep: raise RuntimeError('Helper boundary changed')
    ns={'__file__':str(p)}
    exec(compile(prefix,'<read-only-definitions>','exec'),ns)
    return ns


def collect_device(ns,gz,name,port):
    c=None
    result={'name':name,'port':port}
    try:
        c=ns['connect']('127.0.0.1',port,'root',ns['DEV_PW'],gz)
        run,get_json=ns['run'],ns['get_json']
        result['runtime']=run(c,"""date '+%F %T %z'
uname -m
mihomo -v 2>/dev/null | head -1
pidof mihomo
sha256sum /etc/routermgmt/routermgmt-agent.sh /etc/routermgmt/handlers/switch_node.sh /etc/routermgmt/self-heal.sh /etc/routermgmt/proxy-meter-v3.sh /etc/routermgmt/proxy-meter-endpoints.sh 2>/dev/null
test -e /tmp/selfheal.pause && echo PAUSE_PRESENT || echo NO_PAUSE
netstat -lnt 2>/dev/null | awk '$4 ~ /:(17891|19090|7894|7895|7896|7897)$/ {print}'
""")
        if name=='Athena':
            result['scope']='Runtime metadata only; heterogeneous production configuration untouched'
            return result
        raw=run(c,'cat /etc/mihomo/config.yaml')
        cfg=yaml.safe_load(raw['stdout']) or {}
        result['config']={'cf_nodes':[{k:p.get(k) for k in ('name','server','port','servername','udp')} for p in cfg.get('proxies',[]) if p.get('network')=='ws'],
                          'cold_domain_present':'8364927.xyz' in raw['stdout'],
                          'tail':cfg.get('rules',[])[-3:],
                          'fallback':cfg.get('dns',{}).get('fallback'),
                          'provider_paths':[v.get('path') for v in cfg.get('proxy-providers',{}).values()]}
        result['groups']={}
        for group in ('PROXY','NODE-PICK','US-MEDIA','DOWNLOAD'):
            r=get_json(c,'curl --noproxy \'*\' -fsS -m 4 http://127.0.0.1:9090/proxies/'+group)
            result['groups'][group]={k:r[k] for k in ('type','now','all','unavailable') if k in r}
        result['cf_delay']={}
        for node in ('CF2D-HK-01','CF2D-BWG-01'):
            r=get_json(c,"curl --noproxy '*' -fsS -m 9 'http://127.0.0.1:9090/proxies/"+node+"/delay?timeout=6500&url=https%3A%2F%2Fwww.gstatic.com%2Fgenerate_204'")
            result['cf_delay'][node]={k:r[k] for k in ('delay','unavailable','rc') if k in r}
        result['meter']=run(c,"""crontab -l 2>/dev/null | awk '/proxy-meter/ {print}'
for f in /etc/routermgmt/state/proxy-meter-v3.json /etc/routermgmt/state/proxy-meter-daily-v3.json /etc/routermgmt/proxy-meter-rules-v3.conf /etc/routermgmt/proxy-meter-coverage-v3.json; do
  if [ -f "$f" ]; then echo "FILE=$f"; cat "$f"; fi
done
iptables -S OUTPUT 2>/dev/null | awk '/PROXY_METER|PM4|PM3/ {print}'
iptables -S INPUT 2>/dev/null | awk '/PROXY_METER|PM4|PM3/ {print}'
iptables -L PROXY_METER_OUT -v -x -n 2>/dev/null
iptables -L PROXY_METER_IN -v -x -n 2>/dev/null
tail -n 4 /etc/routermgmt/logs/proxy-meter-v3.log 2>/dev/null
""")
        # Subscription labels may include account metadata; export counts only.
        clean=[]
        for line in result['meter']['stdout'].splitlines():
            if '"unresolved_names"' in line:
                data=json.loads(line)
                names=data.pop('unresolved_names')
                data['unresolved_record_count']=len(names.split(',')) if names else 0
                data['unresolved_names_withheld']=True
                line=json.dumps(data,ensure_ascii=False)
            clean.append(line)
        result['meter']['stdout']='\n'.join(clean)
        result['protected_checks']=run(c,"""sha256sum /etc/config/dhcp /etc/routermgmt/tunnel.conf /etc/mihomo/config.yaml 2>/dev/null
tc qdisc show dev wan 2>/dev/null
curl --noproxy '' -sS -m 12 -x http://127.0.0.1:7890 -o /dev/null -w 'g204=%{http_code} t=%{time_total}\\n' https://www.gstatic.com/generate_204
curl --noproxy '' -sS -m 8 -x http://127.0.0.1:7890 -o /dev/null -w 'baidu=%{http_code} t=%{time_total}\\n' https://www.baidu.com
""")
    except Exception as exc:
        result['error_type']=type(exc).__name__
    finally:
        if c:c.close()
    return result


def main():
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('--output',required=True);parser.add_argument('--include-athena',action='store_true');args=parser.parse_args()
    ns=helpers();gz=None
    out=Path(args.output)
    if out.exists():raise SystemExit('Refusing to overwrite evidence')
    report={'started':datetime.datetime.now().astimezone().isoformat(timespec='seconds'),'production_writes':False,'api_login':False}
    try:
        gz=ns['connect']('134.175.151.213',28722,'ubuntu',ns['DEV_PW'])
        report['gz']=ns['run'](gz,"""date -Is
systemctl is-active gz-api nginx postgresql
sha256sum /opt/mgmt/server
sudo -u postgres psql routermgmt -v ON_ERROR_STOP=1 -X -t -A -F '|' <<'SQL'
BEGIN READ ONLY;
SELECT 'schema',max(version) FROM schema_migrations;
SELECT id,current_config_version,desired_config_version,highest_accepted_version,firmware_ver,round(extract(epoch FROM now()-last_heartbeat_at)),vps_limit_kbps FROM devices ORDER BY id;
SELECT a.device_id,n.id,n.host,n.port,n.proto,n.purpose,a.is_primary,a.failover_priority,n.priority FROM node_assignments a JOIN nodes n ON n.id=a.node_id WHERE n.id IN (8,16,17) ORDER BY a.device_id,n.id;
SELECT id,device_id,type,status FROM tasks WHERE status IN ('pending','running');
COMMIT;
SQL
""")
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            fs={pool.submit(collect_device,ns,gz,n,p):n for n,p in PORTS.items() if n!='Athena' or args.include_athena}
            report['devices']={}
            for f in concurrent.futures.as_completed(fs):
                report['devices'][fs[f]]=f.result()
        report['finished']=datetime.datetime.now().astimezone().isoformat(timespec='seconds')
        out.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        print(json.dumps({'output':str(out),'gz':report.get('gz'),'devices':{n:{'error':v.get('error_type'),'delay':v.get('cf_delay'),'runtime':v.get('runtime',{}).get('stdout','').splitlines()[:5]} for n,v in report.get('devices',{}).items()}},ensure_ascii=False,indent=2))
    finally:
        if gz:gz.close()


if __name__=='__main__':main()
