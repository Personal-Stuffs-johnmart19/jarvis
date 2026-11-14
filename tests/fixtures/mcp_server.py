"""Owned offline MCP protocol fixture; never contacts a model or external host."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

args = argparse.ArgumentParser()
args.add_argument('--pids', required=True)
args.add_argument('--calls', required=True)
args.add_argument('--http-port-file')
args.add_argument('--no-initialize', action='store_true')
args.add_argument('--bad-schema', action='store_true')
args.add_argument('--health-log')
options = args.parse_args()
child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
Path(options.pids).write_text(json.dumps([os.getpid(), child.pid]), encoding='utf-8')
write_lock = threading.Lock()

def handle(message):
    method = message.get('method')
    identifier = message.get('id')
    if identifier is None:
        return None
    if method == 'initialize':
        if options.no_initialize:
            time.sleep(120)
        result = {'protocolVersion': message['params']['protocolVersion'], 'capabilities': {'tools': {}}, 'serverInfo': {'name': 'Jarvis offline fixture', 'version': '1'}}
    elif method == 'tools/list':
        if options.health_log:
            with Path(options.health_log).open('a', encoding='utf-8') as health:
                health.write('tools/list\n')
        schema = {'type': 'array'} if options.bad_schema else {'type': 'object', 'properties': {'text': {'type': 'string'}}}
        result = {'tools': [{'name': name, 'description': 'Offline fixture tool', 'inputSchema': schema} for name in ['echo', 'wait']]}
    elif method == 'tools/call':
        name = message['params']['name']
        with write_lock:
            with Path(options.calls).open('a', encoding='utf-8') as log:
                log.write(name + '\n')
        if name == 'wait':
            time.sleep(120)
        result = {'content': [{'type': 'text', 'text': message['params'].get('arguments', {}).get('text', 'ok')}]}
    elif method == 'ping':
        result = {}
    else:
        return {'jsonrpc': '2.0', 'id': identifier, 'error': {'code': -32601, 'message': 'Unknown fixture method'}}
    return {'jsonrpc': '2.0', 'id': identifier, 'result': result}

if options.http_port_file:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_GET(self):
            self.send_response(405)
            self.send_header('Content-Length', '0')
            self.end_headers()
        def do_DELETE(self):
            self.send_response(200)
            self.send_header('Content-Length', '0')
            self.end_headers()
        def do_POST(self):
            if self.headers.get('Authorization') != 'Bearer fixture-token':
                self.send_response(401)
                self.send_header('Content-Length', '0')
                self.end_headers()
                return
            size = int(self.headers.get('Content-Length', '0'))
            if size > 1024 * 1024:
                self.send_error(413)
                return
            response = handle(json.loads(self.rfile.read(size)))
            data = json.dumps(response).encode() if response is not None else b''
            self.send_response(200 if response is not None else 202)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    server.daemon_threads = True
    Path(options.http_port_file).write_text(str(server.server_port), encoding='ascii')
    server.serve_forever()
else:
    for line in sys.stdin:
        response = handle(json.loads(line))
        if response is not None:
            print(json.dumps(response), flush=True)
