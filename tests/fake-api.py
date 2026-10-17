#!/usr/bin/env python3
# tests/fake-api.py PORT_FILE LOG — a stand-in for the Anthropic Messages API, for tests/run.sh.
#
# Listens on a free port of 127.0.0.1 and writes it to PORT_FILE, logs every request to LOG as one line
# (path, then body), and answers every message with FAKE-REPLY, streamed or not as asked. A request
# whose body says HOLD gets no answer for five minutes, so a test can interrupt the run waiting on it.
# Standard library only: it runs in a persona image.
import json
import os
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT_FILE, LOG = sys.argv[1], sys.argv[2]


def sse(event, data):
    return f"event: {event}\ndata: {json.dumps(data)}\n\n".encode()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def reply(self, kind, body):
        self.send_response(200)
        self.send_header('content-type', kind)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self.reply('application/json', b'{"data": []}')

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get('content-length', 0))).decode('utf-8', 'replace')
        with open(LOG, 'a') as f:
            f.write(self.path + ' ' + body + '\n')
        if 'count_tokens' in self.path:
            return self.reply('application/json', b'{"input_tokens": 10}')
        if 'HOLD' in body:
            time.sleep(300)
        try:
            req = json.loads(body)
        except ValueError:
            req = {}
        msg = {'id': 'msg_fake', 'type': 'message', 'role': 'assistant', 'model': req.get('model', 'fake'),
               'content': [], 'stop_reason': None, 'stop_sequence': None,
               'usage': {'input_tokens': 10, 'output_tokens': 1}}
        if not req.get('stream'):
            msg.update(content=[{'type': 'text', 'text': 'FAKE-REPLY'}], stop_reason='end_turn')
            return self.reply('application/json', json.dumps(msg).encode())
        self.send_response(200)
        self.send_header('content-type', 'text/event-stream')
        self.end_headers()
        for event, data in [
            ('message_start', {'type': 'message_start', 'message': msg}),
            ('content_block_start', {'type': 'content_block_start', 'index': 0,
                                     'content_block': {'type': 'text', 'text': ''}}),
            ('content_block_delta', {'type': 'content_block_delta', 'index': 0,
                                     'delta': {'type': 'text_delta', 'text': 'FAKE-REPLY'}}),
            ('content_block_stop', {'type': 'content_block_stop', 'index': 0}),
            ('message_delta', {'type': 'message_delta', 'delta': {'stop_reason': 'end_turn', 'stop_sequence': None},
                               'usage': {'output_tokens': 5}}),
            ('message_stop', {'type': 'message_stop'}),
        ]:
            self.wfile.write(sse(event, data))
            self.wfile.flush()


server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
server.daemon_threads = True
with open(PORT_FILE + '.tmp', 'w') as f:
    f.write(str(server.server_address[1]))
os.replace(PORT_FILE + '.tmp', PORT_FILE)   # whole, or not there yet
server.serve_forever()
