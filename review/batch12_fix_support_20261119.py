"""Audited transport helpers. No I/O on import; SCP framing, never shell data injection."""
from pathlib import Path
import hashlib
import importlib.util
import shlex

ROOT=Path(__file__).resolve().parents[1]
SESSION='Codex-Batch12-fix-20261119'
BACKUP=ROOT/'backups/batch12-fix-20261119-221752'

def helpers():
    p=ROOT/'review/cf_postdeploy_audit_20261118.py'
    s=importlib.util.spec_from_file_location('batch12_ro',p);m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
    return m.helpers()

def require_scope(scope):
    import json
    from datetime import datetime
    data=json.loads((ROOT/'backups/.session-locks/registry.json').read_text(encoding='utf-8'))
    if not any(x.get('session')==SESSION and x.get('scope')==scope for x in data['locks']):
        raise RuntimeError('required session scope not held')

def read(c,path):
    i,o,e=c.exec_command('cat '+shlex.quote(path),timeout=15)
    raw=o.read();e.read()
    if o.channel.recv_exit_status()!=0:raise RuntimeError('controlled remote read failed')
    return raw

def scp_put(c,directory,name,data,mode=0o600):
    if '/' in name or '\n' in name or not name:raise ValueError('invalid SCP basename')
    ch=c.get_transport().open_session(timeout=15);ch.settimeout(20)
    ch.exec_command('scp -t '+shlex.quote(directory))
    def ack():
        v=ch.recv(1)
        if v!=b'\x00':
            # Do not print remote error bodies: filenames may contain secrets.
            raise RuntimeError('SCP receiver rejected transfer')
    try:
        ack();ch.sendall(('C%04o %d %s\n'%(mode,len(data),name)).encode());ack()
        ch.sendall(data);ch.sendall(b'\x00');ack();ch.shutdown_write()
        if ch.recv_exit_status()!=0:raise RuntimeError('SCP receiver failed')
    finally:ch.close()
    remote=read(c,directory.rstrip('/')+'/'+name)
    if hashlib.sha256(remote).digest()!=hashlib.sha256(data).digest():raise RuntimeError('SCP SHA mismatch')
