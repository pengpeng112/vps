"""Offline meter/registry regression matrix. All counters and HTTP replies are fake."""
from pathlib import Path
import copy
import datetime as dt
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import types
import unittest
import contextlib
import io
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT=Path(__file__).resolve().parents[1]
def module(name,path):
    spec=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m
IO=module('meter_io_test',ROOT/'scripts/qos-r2/proxy-meter-io.py')
FIX=module('meter_fixture_test',ROOT/'logs/batch1_meter_isotest_20261119.py')
MID=int(dt.datetime(2026,11,20,tzinfo=dt.timezone(dt.timedelta(hours=8))).timestamp())

class MeterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path=='/connections':data={'connections':[]}
                else:data={'proxies':{'PROXY':{'now':cls.active},'US-MEDIA':{'now':'EP1'},'DOWNLOAD':{'now':'DIRECT'},'EP1':{'type':'Hysteria2'},cls.active:{'type':'Hysteria2'}}}
                if self.path=='/proxies':data['proxies']['PROXY']={'now':cls.active}
                self.send_response(200);self.end_headers();self.wfile.write(json.dumps(data).encode())
            def log_message(self,*args):pass
        cls.active='EP1';cls.server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        cls.thread=threading.Thread(target=cls.server.serve_forever,daemon=True);cls.thread.start()
    @classmethod
    def tearDownClass(cls):cls.server.shutdown();cls.server.server_close();cls.thread.join()
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='meter-fix-',dir=ROOT/'logs')
        self.dir=Path(self.tmp.name).resolve();assert self.dir.is_relative_to((ROOT/'logs').resolve())
        self.addCleanup(self.tmp.cleanup);self.cover_mode='normal';self.active='EP1'
        type(self).active='EP1'
        def run(args,**kwargs):
            env=kwargs['env'];args=['D:/Program Files/Git/bin/sh.exe',*args[1:]]
            env['PM_PYTHON']=Path(sys.executable).as_posix();env['PM_IO_HELPER']=(ROOT/'scripts/qos-r2/proxy-meter-io.py').as_posix()
            source=Path(env['PM_AGENT']);cover={'coverage_schema':2,'rules_sha256':hashlib.sha256(Path(env['PM_RULES']).read_bytes()).hexdigest(),
             'source_sha256':{str(source):hashlib.sha256(source.read_bytes()).hexdigest()},'registered_nodes':{'EP1':['10.0.0.1','tcp','443']},'unresolved_nodes':[],'coverage_complete':True}
            target=Path(env['PM_COVER']);target.write_text(json.dumps(cover),encoding='utf-8')
            if self.cover_mode=='bad':target.write_text('{broken',encoding='utf-8')
            if self.cover_mode=='stale':source.write_text('changed',encoding='utf-8')
            if self.cover_mode=='incomplete':cover.update(coverage_complete=False,unresolved_nodes=['airport']);target.write_text(json.dumps(cover),encoding='utf-8')
            if self.cover_mode=='missing':target.unlink()
            if self.cover_mode in ('active','bad'):
                env['PM_CHECK_ACTIVE']='1';env['PM_API']='http://127.0.0.1:%d'%self.server.server_port
            return subprocess.run(args,**kwargs)
        FIX.subprocess=types.SimpleNamespace(run=run)
    def prev(self,**extra):
        p={'schema':3,'counter_key_format':'direction-v1','meter_id':'rtr-test','seq':9,'boot_id':'B','continuity_id':'C','prev_continuity':'C',
         'prev_epoch':MID+300,'day_key':'2026-11-20','day_total':1000,'day_first_at':'2026-11-20 00:00:00+0800','health':'ok',
         'partial':False,'quota_eligible':True,'endpoints':{'EP1_up':100,'EP1_down':200}}
        p.update(extra);return p
    def sample(self,prev=None,now=MID+600,boot='B',up=150000,down=200000,name='case'):
        out=f' 150 {up} 0 0 tcp dpt:443 /* EP1 */\n'
        inn=f' 200 {down} 0 0 tcp dpt:443 /* EP1 */\n' if down is not None else ''
        return FIX.run_case(self.dir,name,prev or self.prev(),now,boot,out,inn)
    def test_normal_delta_130(self):
        r=self.sample(up=150,down=280);self.assertEqual(r['daily']['delta_last'],130);self.assertTrue(r['state']['quota_eligible'])
    def test_true_midnight_settles_old_day(self):
        r=self.sample(self.prev(day_key='2026-11-19',prev_epoch=MID-300,partial=True),now=MID)
        self.assertEqual(r['state']['day_total'],0);self.assertTrue(r['state']['quota_eligible'])
        archived=list((self.dir/'case/proxy-meter-days').glob('*.json'));self.assertEqual(len(archived),1)
        self.assertEqual(json.loads(archived[0].read_text())['lower_bound'],350700)
    def test_cross_midnight_unknown_not_new_day_usage(self):
        r=self.sample(self.prev(day_key='2026-11-19',prev_epoch=MID-180),now=MID+120)
        self.assertEqual(r['state']['day_total'],0);self.assertEqual(r['state']['crossday_unassigned'],349700)
        self.assertFalse(r['state']['quota_eligible']);self.assertTrue(r['state']['partial'])
    def test_unknown_stays_explicit_next_sample(self):
        r=self.sample(self.prev(day_key='2026-11-19',prev_epoch=MID-180),now=MID+120)
        r=self.sample(r['state'],now=MID+420,up=150100,down=200100,name='next')
        self.assertEqual(r['state']['day_total'],200);self.assertEqual(r['state']['crossday_unassigned'],349700)
        self.assertFalse(r['state']['quota_eligible'])
    def test_same_day_partial_sticky(self):self.assertFalse(self.sample(self.prev(partial=True))['state']['quota_eligible'])
    def test_gap_fails_qualification(self):self.assertFalse(self.sample(now=MID+1000)['state']['quota_eligible'])
    def test_reboot_fails_qualification(self):self.assertFalse(self.sample(boot='NEW') ['state']['quota_eligible'])
    def test_new_counter_unknown(self):
        r=self.sample(self.prev(endpoints={'EP1_up':100}));self.assertEqual(r['state']['health'],'degraded');self.assertFalse(r['state']['quota_eligible'])
    def test_read_failure_preserves_good_counter(self):
        r=self.sample(down=None);self.assertEqual(r['state']['endpoints']['EP1_down'],200);self.assertFalse(r['state']['quota_eligible'])
    def test_counter_reset_fails_qualification(self):self.assertFalse(self.sample(up=1)['state']['quota_eligible'])
    def test_bad_cover_with_available_local_api(self):
        self.cover_mode='bad';self.assertFalse(self.sample()['state']['quota_eligible'])
    def test_missing_cover(self):self.cover_mode='missing';self.assertFalse(self.sample()['state']['quota_eligible'])
    def test_stale_provider_snapshot(self):self.cover_mode='stale';self.assertFalse(self.sample()['state']['quota_eligible'])
    def test_unresolved_standby_disables_quota(self):self.cover_mode='incomplete';self.assertFalse(self.sample()['state']['quota_eligible'])
    def test_active_quoted_unknown_node_safe_json(self):
        self.cover_mode='active';type(self).active='airport "quoted" \\ path\nUnicode节点'
        r=self.sample();self.assertFalse(r['state']['quota_eligible']);self.assertEqual(r['state']['health'],'degraded')
    def test_day_key_inconsistent_with_epoch(self):self.assertFalse(self.sample(self.prev(day_key='2026-11-18'))['state']['quota_eligible'])

class GeneratorTests(unittest.TestCase):
    def test_snapshot_refresh_keeps_counters_and_rejects_changed_tuples(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config, rules, cover = (root / name for name in ('config.yaml', 'rules', 'cover'))
            original = 'proxies:\n  - {name: node, type: ss, server: 192.0.2.9, port: 443}\n'
            config.write_text(original)
            args = types.SimpleNamespace(config=str(config), providers=directory, rules=str(rules),
                                         cover=str(cover), check=False, refresh=False, active=False)
            IO.generate(args)
            rule_bytes = rules.read_bytes()
            config.write_text(original + '# repository metadata changed\n')
            args.refresh = True
            with contextlib.redirect_stdout(io.StringIO()) as output:
                IO.coverage(args)
            self.assertIn('COVER_NOTE=snapshot_refreshed', output.getvalue())
            self.assertIn('COVER_COMPLETE=0', output.getvalue())
            self.assertEqual(rules.read_bytes(), rule_bytes)
            refreshed = cover.read_bytes()
            config.write_text(original.replace('192.0.2.9', '192.0.2.10'))
            with contextlib.redirect_stdout(io.StringIO()) as output:
                IO.coverage(args)
            self.assertIn('COVER_NOTE=stale_sources', output.getvalue())
            self.assertEqual(rules.read_bytes(), rule_bytes)
            self.assertEqual(cover.read_bytes(), refreshed)

    def test_quotes_backslashes_unicode_and_newline_roundtrip(self):
        names=['名字 "引号" \\ path\nnewline','literal\\n','comma,name',"single'quote"]
        raw='proxies:\n'+'\n'.join('  - {name: '+json.dumps(n,ensure_ascii=False)+', type: ss, server: sample.invalid, port: 443}' for n in names)+'\n'
        self.assertEqual([p['name'] for p in IO.proxies(raw.encode())],names)
        self.assertEqual(json.loads(json.dumps(names,ensure_ascii=False)),names)
    def test_block_key_order_and_nested_fields(self):
        raw=b'proxies:\n  - type: vless\n    name: node\n    server: 1.2.3.4\n    port: 443\n    ws-opts:\n      path: /private\nrules:\n  - MATCH,DIRECT\n'
        self.assertEqual(IO.proxies(raw)[0]['server'],'1.2.3.4')
    def test_invalid_yaml_rejected_without_source_content(self):
        with self.assertRaises(ValueError):IO.proxies(b'proxies:\n  - { name: "unterminated, type: ss }\n')

if __name__=='__main__':unittest.main(verbosity=2)
