#!/usr/bin/env python3
# tests/fake-dockerd.py SOCKET LOG — a stand-in for the Docker daemon's API, for tests/run.sh: the upstream of a
# Docker guard (NEXUS_GUARD_UPSTREAM), so a `docker system prune` sent through the guard reaches nothing real.
#
# Listens on the unix socket SOCKET, logs every request to LOG as one line (the method, then the path with its
# query %-decoded), and answers as a daemon with nothing in it: /_ping and /version as API 1.47, every other request
# 200 with {} (a prune's report with nothing deleted). A request with Connection: close (the guard sends every one
# so) gets it back, as from the daemon: the client then takes a new connection for its next request, which the guard
# checks in turn. Standard library only: it runs in a persona image.
import json
import os
import socketserver
import sys
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler

SOCKET, LOG = sys.argv[1], sys.argv[2]
API = "1.47"
lock = threading.Lock()


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def reply(self, body, kind="application/json"):
        self.send_response(200)
        for k, v in (("Api-Version", API), ("Ostype", "linux"), ("Docker-Experimental", "false"),
                     ("Content-Type", kind), ("Content-Length", str(len(body)))):
            self.send_header(k, v)
        if self.headers.get("Connection", "").lower() == "close":
            self.send_header("Connection", "close")     # as Go's server says it, so the client won't send again on it
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def handle_one(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n:
            self.rfile.read(n)
        with lock, open(LOG, "a") as f:
            f.write("%s %s\n" % (self.command, urllib.parse.unquote(self.path)))
        path = urllib.parse.urlsplit(self.path).path
        if path.endswith("/_ping"):
            self.reply(b"OK", "text/plain")
        elif path.endswith("/version"):
            self.reply(json.dumps({"Version": "fake", "ApiVersion": API, "MinAPIVersion": "1.24", "Os": "linux",
                                   "Arch": "amd64"}).encode())
        else:
            self.reply(b"{}")

    do_GET = do_HEAD = do_POST = do_PUT = do_DELETE = handle_one


class Server(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True


if os.path.exists(SOCKET):
    os.unlink(SOCKET)
Server(SOCKET, Handler).serve_forever()
