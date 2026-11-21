"""Independent read-only Batch 1/2 audit; no deployment or API login."""
from pathlib import Path
import datetime as dt
import hashlib
import importlib.util
import json
import re

ROOT = Path(__file__).resolve().parents[1]


def main():
    p = ROOT / 'review/cf_postdeploy_audit_20261118.py'
    spec = importlib.util.spec_from_file_location('review_ro_helpers', p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    ns = mod.helpers()
    result = {'started': dt.datetime.now().astimezone().isoformat(),
              'scope': 'read-only production checks; no API login, task or config writes'}
    gz = r2 = None
    try:
        gz = ns['connect']('134.175.151.213', 28722, 'ubuntu', ns['DEV_PW'])
        result['GZ'] = ns['run'](gz, """date -Is
systemctl is-active gz-api nginx postgresql
sha256sum /opt/mgmt/server
sudo -u postgres psql routermgmt -X -v ON_ERROR_STOP=1 -t -A -F '|' <<'SQL'
BEGIN READ ONLY;
SELECT max(version) FROM schema_migrations;
SELECT id,current_config_version,desired_config_version,highest_accepted_version,current_exit_ip,vps_limit_kbps,proxy_mode,access_mode FROM devices WHERE id='rtr-a9bcba';
SELECT id,device_id,type,status FROM tasks WHERE status IN ('pending','running');
COMMIT;
SQL
""")
        result['R2'] = mod.collect_device(ns, gz, 'R2', 13223)
        r2 = ns['connect']('127.0.0.1', 13223, 'root', ns['DEV_PW'], gz)
        result['R2_extra'] = ns['run'](r2, """date '+%F %T %z'
cat /proc/sys/kernel/random/boot_id
crontab -l 2>/dev/null | awk '/reboot|proxy-meter/ {print}'
test -d /tmp/qos-poc && echo POC_DIR_PRESENT || echo POC_DIR_ABSENT
netstat -lnt 2>/dev/null | awk '$4 ~ /:(7891|7894|7895|7896|7897|9091)$/ {print}'
ps w | awk '/[q]os-gate|[m]ihomo.*qos-poc/ {print $1, $2, "POC_PROCESS_MATCH"}'
ls -l /etc/routermgmt/backup-meter-20261119/ 2>/dev/null
sha256sum /etc/routermgmt/backup-meter-20261119/* 2>/dev/null
tail -n 8 /etc/routermgmt/logs/proxy-meter-v3.log 2>/dev/null
""")
    except Exception as exc:
        result['error_type'] = type(exc).__name__
    finally:
        if r2:
            r2.close()
        if gz:
            gz.close()
    rows = []
    manifest = ROOT / 'docs/archive/20261119-manifest.md'
    for line in manifest.read_text(encoding='utf-8').splitlines():
        match = re.fullmatch(r'\| ([^|]+) \| ([^|]+) \| `([0-9a-f]{64})` \| (\d+) \|', line)
        if not match:
            continue
        old, new, expected, size = match.groups()
        dest = ROOT / new
        raw = dest.read_bytes() if dest.exists() else b''
        rows.append({'old': old, 'new': new, 'exists': dest.exists(),
                     'sha_ok': hashlib.sha256(raw).hexdigest() == expected,
                     'size_ok': len(raw) == int(size), 'old_exists': (ROOT / old).exists()})
    result['archive'] = rows
    result['finished'] = dt.datetime.now().astimezone().isoformat()
    out = ROOT / ('review/Batch12独立复核-20261119-' + dt.datetime.now().strftime('%H%M%S') + '-Codex.json')
    with out.open('x', encoding='utf-8', newline='\n') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(out.relative_to(ROOT))
    print(json.dumps({'gz': result.get('GZ'), 'r2_runtime': result.get('R2', {}).get('runtime'),
                      'r2_protected': result.get('R2', {}).get('protected_checks'),
                      'r2_extra': result.get('R2_extra'),
                      'archive_count': len(rows), 'archive_all_ok': all(r['sha_ok'] and r['size_ok'] for r in rows),
                      'error_type': result.get('error_type')}, ensure_ascii=False))


if __name__ == '__main__':
    main()
