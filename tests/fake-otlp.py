#!/usr/bin/env python3
# tests/fake-otlp.py PORT_FILE LOG CERT KEY [WORD…] — a stand-in OpenTelemetry collector (OTLP/HTTP over TLS),
# for tests/run.sh.
#
# Listens on a free port of 127.0.0.1 with the certificate CERT (and its key KEY), writes the port to PORT_FILE,
# and answers every export as a collector does ({"partialSuccess":{}}, 200). Each request is one JSON line in
# LOG: its path, its Authorization header, its Content-Type, and which WORDs its body holds (a protobuf body
# carries strings, attribute values among them, as they are). Standard library only: it runs in a persona image.
import json
import ssl
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT_FILE, LOG, CERT, KEY = sys.argv[1:5]
WORDS = sys.argv[5:]


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get('content-length', 0)))
        with open(LOG, 'a') as f:
            f.write(json.dumps({'path': self.path, 'auth': self.headers.get('Authorization', ''),
                                'type': self.headers.get('Content-Type', ''),
                                'has': [w for w in WORDS if w.encode() in body]}) + '\n')
        out = b'{"partialSuccess":{}}'
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(out)))
        self.end_headers()
        self.wfile.write(out)


srv = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
ctx.load_cert_chain(CERT, KEY)
srv.socket = ctx.wrap_socket(srv.socket, server_side=True)
with open(PORT_FILE, 'w') as f:
    f.write(str(srv.server_address[1]))
srv.serve_forever()
