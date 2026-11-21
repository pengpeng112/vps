"""Synthetic HTTP source for isolated PoC. No business data; explicit budget and small send buffer."""
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import socket
import threading
import time
from pathlib import Path

PATTERN=bytes(range(256))*16
LOCK=threading.Lock()
STATS={'accepted_bytes':0,'upload_bytes':0,'requests':0,'active':0}

def main():
    p=argparse.ArgumentParser();p.add_argument('--port',type=int,required=True);p.add_argument('--token',required=True);p.add_argument('--state',required=True);a=p.parse_args()
    class Handler(BaseHTTPRequestHandler):
        protocol_version='HTTP/1.1'
        def log_message(self,*args):pass
        def do_GET(self):
            prefix='/'+a.token+'/'
            if not self.path.startswith(prefix):self.send_error(404);return
            route=self.path[len(prefix):]
            if route=='stats':
                with LOCK:raw=json.dumps(STATS).encode()
                self.send_response(200);self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw);return
            try:size=int(route)
            except ValueError:self.send_error(400);return
            if size<1 or size>64*1024*1024:self.send_error(400);return
            self.connection.setsockopt(socket.SOL_SOCKET,socket.SO_SNDBUF,16384)
            self.connection.settimeout(300)
            self.send_response(200);self.send_header('Content-Length',str(size));self.send_header('Connection','close');self.end_headers()
            with LOCK:STATS['requests']+=1;STATS['active']+=1
            try:
                while size:
                    with LOCK:
                        if STATS['accepted_bytes']>=32*1024*1024:return
                    chunk=PATTERN[:min(size,len(PATTERN))]
                    self.wfile.write(chunk);size-=len(chunk)
                    with LOCK:STATS['accepted_bytes']+=len(chunk)
            except (BrokenPipeError,ConnectionResetError,TimeoutError):pass
            finally:
                self.close_connection=True
                with LOCK:
                    STATS['active']-=1
                    Path(a.state).write_text(json.dumps(STATS))
        def do_POST(self):
            if self.path!='/'+a.token+'/upload':self.send_error(404);return
            size=int(self.headers.get('Content-Length','0'))
            if not 0<size<=1024*1024:self.send_error(400);return
            start=time.monotonic();remaining=size
            while remaining:
                chunk=self.rfile.read(min(remaining,16384))
                if not chunk:break
                remaining-=len(chunk)
            with LOCK:STATS['upload_bytes']+=size-remaining
            raw=json.dumps({'received':size-remaining,'receive_seconds':time.monotonic()-start}).encode()
            self.send_response(200);self.send_header('Content-Length',str(len(raw)));self.send_header('Connection','close');self.end_headers();self.wfile.write(raw);self.close_connection=True
    server=ThreadingHTTPServer(('0.0.0.0',a.port),Handler)
    server.daemon_threads=True
    server.serve_forever()

if __name__=='__main__':main()
