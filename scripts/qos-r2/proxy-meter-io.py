#!/usr/bin/env python3
"""Strict meter I/O. Standard library only; never exports credentials or labels.

Supports the block/flow proxy records produced by this project's renderer and
mihomo providers. Unsupported YAML is rejected, preserving the old registry.
"""
import argparse
import datetime as dt
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import socket
from concurrent.futures import ThreadPoolExecutor
import sys
import tempfile
import urllib.request


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def atomic(path, raw):
    path = Path(path)
    fd, tmp = tempfile.mkstemp(prefix='.' + path.name + '-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as f:
            f.write(raw)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def scalar(text):
    text = text.strip()
    if text.startswith('"'):
        # JSON strings are a strict, safe subset of double-quoted YAML scalars.
        value, end = json.JSONDecoder().raw_decode(text)
        if text[end:].strip() and not text[end:].lstrip().startswith('#'):
            raise ValueError('unsupported quoted scalar')
        return str(value)
    if text.startswith("'"):
        match = re.fullmatch(r"'((?:[^']|'')*)'\s*(?:#.*)?", text)
        if not match:
            raise ValueError('unsupported single-quoted scalar')
        return match[1].replace("''", "'")
    text = re.sub(r'\s+#.*$', '', text).strip()
    if not text or text[0] in '&*!|>{[':
        raise ValueError('unsupported scalar')
    return text


def flow_fields(text):
    # Split only commas outside strings and nested collections.
    parts, start, depth, quote, escaped = [], 0, 0, None, False
    for i, ch in enumerate(text):
        if quote:
            if quote == '"' and ch == '\\' and not escaped:
                escaped = True
                continue
            if ch == quote and not escaped:
                quote = None
            escaped = False
        elif ch in "\"'":
            quote = ch
        elif ch in '[{':
            depth += 1
        elif ch in ']}':
            depth -= 1
        elif ch == ',' and depth == 0:
            parts.append(text[start:i]); start = i + 1
    if quote or depth:
        raise ValueError('unsupported multiline flow record')
    return parts + [text[start:]]


def proxies(raw):
    inside, record, record_indent = False, None, None
    result = []
    for line in raw.decode('utf-8-sig').splitlines():
        if not line.strip() or line.lstrip().startswith('#'):
            continue
        if line == 'proxies:':
            inside = True
            continue
        if not inside:
            continue
        indent = len(line) - len(line.lstrip(' '))
        if indent == 0 and not line.startswith('-'):
            if record is not None: result.append(record)
            record = None
            break
        match = re.match(r'^(\s*)-\s+(.*)$', line)
        if match and (record_indent is None or indent <= record_indent):
            if record is not None: result.append(record)
            record, record_indent = {}, indent
            rest = match[2].strip()
            if rest.startswith('{'):
                if not rest.endswith('}'):
                    raise ValueError('unsupported multiline flow record')
                fields = flow_fields(rest[1:-1])
            else:
                fields = [rest]
        elif record is not None and indent == record_indent + 2:
            fields = [line.strip()]
        else:
            continue
        for field in fields:
            key, sep, value = field.partition(':')
            key = key.strip()
            if not sep:
                raise ValueError('invalid field')
            if key in ('name', 'server', 'port', 'type', 'coverage'):
                if key in record:
                    raise ValueError('duplicate field')
                record[key] = scalar(value)
    else:
        if record is not None: result.append(record)
    if not inside:
        raise ValueError('missing proxies section')
    for item in result:
        if 'name' not in item:
            raise ValueError('missing node name')
        if item.get('coverage', '').lower() == 'false':
            continue
        if not all(k in item for k in ('server', 'port', 'type')):
            raise ValueError('incomplete proxy record')
    return result


METADATA_NAME = re.compile(r'剩余|到期|过期|官网|重置|套餐|^(?:流量|订阅)|(?:^|\W)(?:expire|traffic)(?:\W|$)', re.I)


def is_metadata(node):
    return node.get('coverage', '').lower() == 'false' or METADATA_NAME.search(node['name']) is not None


def generate(args):
    paths = [Path(args.config)] + sorted(Path(args.providers).glob('airport-*.yaml'))
    records, snapshots = [], {}
    for path in paths:
        raw = path.read_bytes()
        records.extend(proxies(raw))
        if path.read_bytes() != raw:
            raise ValueError('source changed while reading')
        snapshots[str(path)] = digest(raw)
    if not records:
        raise ValueError('empty registry refused')
    old_tags = {}
    rules_path = Path(args.rules)
    if rules_path.exists():
        for line in rules_path.read_text().splitlines():
            fields = line.split('|')
            if len(fields) == 5 and re.fullmatch(r'[A-Za-z0-9_-]+', fields[0]):
                old_tags[tuple(fields[2:])] = fields[0]
    rows, seen, registered, unresolved, filtered = [], set(), {}, [], 0
    for item in records:
        name = item['name']
        if is_metadata(item):
            filtered += 1
            continue
        try:
            ip = ipaddress.ip_address(item['server'])
            if ip.version != 4:
                raise ValueError('IPv6 registry not yet supported')
            if not 0 < int(item['port']) < 65536:
                raise ValueError('port out of range')
        except ValueError:
            unresolved.append(name)
            continue
        proto = 'udp' if item['type'] in ('hysteria2', 'tuic') else 'tcp'
        endpoint = str(ip), proto, str(int(item['port']))
        registered[name] = list(endpoint)
        if endpoint in seen:
            continue
        seen.add(endpoint)
        tag = old_tags.get(endpoint, 'pm-' + digest('|'.join(endpoint).encode())[:16])
        rows.extend('|'.join((tag, direction, *endpoint)) for direction in ('up', 'down'))
    if not rows:
        raise ValueError('empty resolved registry refused')
    raw_rules = ('\n'.join(rows) + '\n').encode()
    cover = {'coverage_schema': 2, 'generated_at': dt.datetime.now().astimezone().isoformat(),
             'registered_rules': len(rows), 'proxy_nodes': len(seen),
             'rules_sha256': digest(raw_rules), 'source_sha256': snapshots,
             'provider_directory': str(Path(args.providers)),
             'registered_nodes': registered, 'unresolved_nodes': sorted(set(unresolved)),
             'unresolved_names': ','.join(sorted(set(unresolved))), 'tuple_merged': '',
             'filtered_metadata_count': filtered, 'coverage_complete': not unresolved}
    raw_cover = (json.dumps(cover, ensure_ascii=False, separators=(',', ':')) + '\n').encode()
    if args.check:
        old = json.loads(Path(args.cover).read_text()) if Path(args.cover).exists() else {}
        old.pop('generated_at', None); compare = dict(cover); compare.pop('generated_at')
        same = rules_path.exists() and rules_path.read_bytes() == raw_rules and old == compare
        print('CHECK same' if same else 'CHECK differs (no writes; labels withheld)')
        return 0 if same else 1
    # Refuse counter identity changes behind the meter's back. They need a
    # dedicated settle/migrate transaction, not a naked registry replacement.
    if rules_path.exists():
        old = set(rules_path.read_text().splitlines())
        if old != set(rows):
            raise ValueError('registry counter identity change requires explicit settled migration')
        raw_rules = rules_path.read_bytes()
        cover['rules_sha256'] = digest(raw_rules)
        raw_cover = (json.dumps(cover, ensure_ascii=False, separators=(',', ':')) + '\n').encode()
    if getattr(args, 'refresh', False):
        # Caller already holds the meter lock. Existing counter identities and
        # rule bytes were verified above, so only refresh the snapshot.
        if not rules_path.is_file():
            raise ValueError('missing existing rules')
        atomic(args.cover, raw_cover)
        return 0
    stamp = dt.datetime.now().strftime('%Y%m%d-%H%M%S-%f')
    for path in (rules_path, Path(args.cover)):
        if path.exists():
            backup = path.with_name(path.name + '.before-' + stamp)
            with backup.open('xb') as f: f.write(path.read_bytes())
    atomic(rules_path, raw_rules); atomic(args.cover, raw_cover)
    print('OK registered_rules=%d unresolved_count=%d filtered_metadata=%d coverage_complete=%s' %
          (len(rows), len(set(unresolved)), filtered, not unresolved))
    return 0


def coverage(args):
    note, active, complete = 'invalid_cover', 'none', False
    try:
        c = json.loads(Path(args.cover).read_text())
        if c['coverage_schema'] != 2 or digest(Path(args.rules).read_bytes()) != c['rules_sha256']:
            raise ValueError('registry mismatch')
        sources = c['source_sha256']
        if not sources:
            raise ValueError('empty source snapshot')
        stale = any(not Path(p).is_file() or digest(Path(p).read_bytes()) != h for p, h in sources.items())
        if 'provider_directory' in c:
            expected = {str(p) for p in Path(c['provider_directory']).glob('airport-*.yaml')}
            stale |= expected != {p for p in sources if Path(p).name.startswith('airport-')}
        if stale:
            note = 'stale_sources'
            if not args.refresh:
                raise ValueError('source snapshot changed')
            generate(argparse.Namespace(config=args.config, providers=args.providers,
                                        rules=args.rules, cover=args.cover, check=False, refresh=True))
            c = json.loads(Path(args.cover).read_text())
        known = c['registered_nodes']; unresolved = c['unresolved_nodes']
        if not isinstance(known, dict) or not isinstance(unresolved, list):
            raise ValueError('invalid coverage fields')
        complete = bool(c['coverage_complete']) and not unresolved
        note = 'ok'
        if stale:
            # This sampling interval crossed a source change. Keep it partial.
            complete = False
        if args.active:
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            def get(endpoint):
                with opener.open(args.api.rstrip('/') + endpoint, timeout=3) as r:
                    return json.load(r)
            data = get('/proxies')['proxies']
            for group in ('PROXY', 'US-MEDIA', 'DOWNLOAD', 'FOREIGN-DL'):
                if group == 'FOREIGN-DL' and group not in data: continue
                name, visited = group, set()
                while name not in ('DIRECT', 'REJECT', 'REJECT-DROP'):
                    if name in visited: raise ValueError('group cycle')
                    visited.add(name)
                    obj = data[name]
                    if 'now' not in obj:
                        if name not in known: active = 'unregistered'; note = 'active_unregistered'
                        break
                    name = obj['now']
            for conn in get('/connections')['connections']:
                chain = conn.get('chains', [])
                if chain and chain[0] not in known and chain[0] not in ('DIRECT', 'REJECT', 'REJECT-DROP'):
                    active, note = 'unregistered', 'active_unregistered'
        if stale and note == 'ok':
            note = 'snapshot_refreshed'
    except Exception:
        complete = False
        if note == 'ok': note = 'api_or_coverage_error'
    print('COVER_NOTE=' + note)
    print('ACTIVE_UNCOVERED=' + active)
    print('COVER_COMPLETE=' + ('1' if complete else '0'))


def normalize(path):
    state = json.loads(Path(path).read_text())
    for key in ('seq', 'prev_epoch', 'day_total'):
        if type(state[key]) is not int or state[key] < 0: raise ValueError('invalid state integer')
    if state['schema'] != 3 or not isinstance(state['partial'], bool): raise ValueError('invalid schema')
    for key, value in state['endpoints'].items():
        if not re.fullmatch(r'[A-Za-z0-9_-]+', key) or type(value) is not int or value < 0:
            raise ValueError('invalid endpoint state')
    print(json.dumps(state, ensure_ascii=True, separators=(',', ':')))


def inventory(args):
    """Report DNS candidates only. DNS answers alone cannot attest coverage."""
    snapshot = Path(args.provider).read_bytes()
    nodes = [p for p in proxies(snapshot) if not is_metadata(p)]
    domains = {p['server'] for p in nodes if not re.fullmatch(r'[0-9.]+', p['server'])
               and ':' not in p['server']}

    def resolve(host):
        try:
            return {ipaddress.ip_address(r[4][0]).version for r in socket.getaddrinfo(host, None)}
        except (socket.gaierror, ValueError):
            return set()

    with ThreadPoolExecutor(max_workers=6) as pool:
        results = dict(zip(sorted(domains), pool.map(resolve, sorted(domains))))
    if Path(args.provider).read_bytes() != snapshot:
        raise ValueError('provider changed during DNS inventory')
    print(json.dumps({
        'candidate_nodes': len(nodes),
        'unique_domains': len(results),
        'domains_with_A': sum(4 in versions for versions in results.values()),
        'domains_with_AAAA': sum(6 in versions for versions in results.values()),
        'unresolved_domains': sum(not versions for versions in results.values()),
        'coverage_certified': False,
    }))


def main():
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest='command', required=True)
    gen = sub.add_parser('generate')
    for name in ('config', 'providers', 'rules', 'cover'): gen.add_argument('--' + name, required=True)
    gen.add_argument('--check', action='store_true')
    cov = sub.add_parser('coverage')
    for name in ('rules', 'cover'): cov.add_argument('--' + name, required=True)
    cov.add_argument('--api', default='http://127.0.0.1:9090'); cov.add_argument('--active', action='store_true')
    cov.add_argument('--refresh', action='store_true')
    cov.add_argument('--config', default='/etc/mihomo/config.yaml')
    cov.add_argument('--providers', default='/etc/mihomo/proxy_providers')
    norm = sub.add_parser('normalize'); norm.add_argument('path')
    inv = sub.add_parser('inventory'); inv.add_argument('provider')
    a = p.parse_args()
    try:
        if a.command == 'generate': return generate(a)
        if a.command == 'coverage': coverage(a)
        if a.command == 'normalize': normalize(a.path)
        if a.command == 'inventory': inventory(a)
        return 0
    except Exception as exc:
        # No raw parser error: it may quote source YAML, credentials, or labels.
        print('FAIL meter I/O: ' + type(exc).__name__, file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
