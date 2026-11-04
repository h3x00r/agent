"""image/docker-guard.py, run in this process on temporary unix sockets in front of a stand-in for dockerd (Fake),
which records what reaches it and answers the guard's inspects from what each test gives it."""
import importlib.util
import json
import os
import re
import shutil
import signal
import socket
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.parse
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
GUARD = os.path.join(REPO, "image", "docker-guard.py")


def _load():
    spec = importlib.util.spec_from_file_location("docker_guard", GUARD)
    mod = importlib.util.module_from_spec(spec)
    was, sys.dont_write_bytecode = sys.dont_write_bytecode, True     # no image/__pycache__
    try:
        spec.loader.exec_module(mod)
    finally:
        sys.dont_write_bytecode = was
    return mod


guard = _load()

FAIL = object()             # an object whose inspect gets a 500
ID_MINE, ID_THEIRS, ID_BARE, ID_ROUTER, ID_AGENT, ID_OTHER = ("%02x" % n * 32 for n in (0xa1, 0xb2, 0xc3, 0xd4, 0xe5,
                                                                                       0xf6))
HIJACK = re.compile(r"^/(containers/.+/attach|exec/.+/start|grpc|session)$")     # moby's, POST each


def _tmpdir():
    """A temporary dir short enough for its sockets: a unix socket path has ~107 bytes at most, and TMPDIR may be
    long (run.sh exports one)."""
    return tempfile.mkdtemp(prefix="guard-", dir="/tmp" if len(tempfile.gettempdir()) > 60 else None)


def _read_request(c, buf=b""):
    """One request off a socket (the fake's side), buf what came of it already: its head, headers, body
    (Content-Length or chunked), and what came after it ("extra")."""
    while b"\r\n\r\n" not in buf:
        data = c.recv(65536)
        if not data:
            return None
        buf += data
    head, _, rest = buf.partition(b"\r\n\r\n")
    lines = head.decode("latin-1").split("\r\n")
    method, target, version = lines[0].split(" ")
    headers = [tuple(x.strip() for x in line.split(":", 1)) for line in lines[1:]]
    low = [(k.lower(), v) for k, v in headers]
    h = dict(low)
    body = b""
    if h.get("transfer-encoding") == "chunked":
        while True:
            while b"\r\n" not in rest:
                rest += c.recv(65536)
            line, _, rest = rest.partition(b"\r\n")
            n = int(line.split(b";")[0], 16)
            while len(rest) < n + 2:
                rest += c.recv(65536)
            body, rest = body + rest[:n], rest[n + 2:]
            if n == 0:
                break
    else:
        n = int(h.get("content-length", "0"))
        while len(rest) < n:
            rest += c.recv(65536)
        body, rest = rest[:n], rest[n:]
    return {"method": method, "target": target, "version": version, "headers": headers, "low": low, "h": h,
            "body": body, "raw": head + b"\r\n\r\n", "extra": rest}


def _send(c, code, obj, close=True):
    body = json.dumps(obj).encode()
    c.sendall(b"HTTP/1.1 %d X\r\nContent-Type: application/json\r\nContent-Length: %d\r\n%s\r\n"
              % (code, len(body), b"Connection: close\r\n" if close else b"") + body)


class Fake:
    """A stand-in for dockerd on a unix socket. Records each request ((method, target, headers, body, raw head) in
    .got) and answers: an inspect GET from the objects added (404 for none, 500 for FAIL); GET containers/json with
    .listed (FAIL: a 500); an upgrade (a POST with
    Upgrade to attach, exec start, /grpc, /session) with a 101, "hello", then an echo of each read (what came with the
    head first) and "bye" at the client's EOF, or a 404 when its path has "nosuch"; a path ending /until-eof after
    reading to the client's EOF; anything else 200 (201 a volumes/create). Like dockerd it keeps the connection
    alive, and reads the next request on it, unless the request said Connection: close (or there was an upgrade)."""
    def __init__(self, path):
        self.path, self.got, self.objects, self.lock = path, [], [], threading.Lock()
        self.listed = []
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.bind(path)
        self.sock.listen(64)
        threading.Thread(target=self._serve, daemon=True).start()

    def add(self, kind, obj, *aliases):
        self.objects.append((kind, set(aliases), obj))

    def close(self):
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        self.sock.close()

    def find(self, method, path):
        """What came for method and path (the target without its query, %-decoded)."""
        return [r for r in self.got if r["method"] == method and
                urllib.parse.unquote(r["target"].split("?")[0]) == path]

    def _serve(self):
        while True:
            try:
                c, _ = self.sock.accept()
            except OSError:
                return
            threading.Thread(target=self._one, args=(c,), daemon=True).start()

    def _one(self, c):
        c.settimeout(5)
        try:
            buf = b""
            while True:
                r = _read_request(c, buf)
                if r is None:
                    return
                with self.lock:
                    self.got.append(r)
                if not self._answer(c, r):
                    return
                buf = r["extra"]
        except OSError:
            pass
        finally:
            c.close()

    def _answer(self, c, r):
        """Answer r: -> whether the connection stays alive for another request."""
        path = urllib.parse.unquote(r["target"].split("?")[0])
        parts = path.split("/")
        if parts[1].startswith("v") and parts[1][1:2].isdigit():
            parts = parts[:1] + parts[2:]
        route = "/".join(parts)
        close = "close" in [t.strip().lower() for k, v in r["low"] if k == "connection" for t in v.split(",")]
        hijack = r["method"] == "POST" and HIJACK.match(route)
        if hijack and "upgrade" in r["h"] and "nosuch" not in route:
            c.sendall(b"HTTP/1.1 101 UPGRADED\r\nConnection: Upgrade\r\nUpgrade: tcp\r\n\r\nhello\n")
            if r["extra"]:                      # what came with the head
                c.sendall(b"echo:" + r["extra"])
            while True:
                data = c.recv(65536)
                if not data:
                    break
                c.sendall(b"echo:" + data)
            c.sendall(b"bye\n")
            return False
        if route.endswith("/until-eof"):
            more = r["extra"]
            while True:
                data = c.recv(65536)
                if not data:
                    break
                more += data
            _send(c, 200, {"got": (r["body"] + more).decode()})
            return False
        if hijack and "nosuch" in route:        # the daemon answers, takes no upgrade, stays alive
            _send(c, 404, {"message": "No such exec instance: nosuch"}, close)
        elif r["method"] == "GET" and parts[1:] == ["containers", "json"]:
            if self.listed is FAIL:
                _send(c, 500, {"message": "boom"}, close)
            else:
                _send(c, 200, self.listed, close)
        elif r["method"] == "GET" and (parts[1] in ("containers", "images") and len(parts) >= 4 and
                                       parts[-1] == "json" or parts[1] in ("volumes", "networks", "services", "secrets",
                                                                           "configs", "nodes") and len(parts) >= 3):
            kind = parts[1]
            ref = "/".join(parts[2:-1]) if kind in ("containers", "images") else "/".join(parts[2:])
            obj = next((o for k, refs, o in self.objects if k == kind and ref in refs), None)
            if obj is None:
                _send(c, 404, {"message": "No such %s: %s" % (kind[:-1], ref)}, close)
            elif obj is FAIL:
                _send(c, 500, {"message": "boom"}, close)
            else:
                _send(c, 200, obj, close)
        elif path.endswith("/volumes/create"):
            _send(c, 201, {"Name": json.loads(r["body"].decode()).get("Name")}, close)
        else:
            _send(c, 200, {"ok": True}, close)
        return not close


def _response(data):
    head, _, body = data.partition(b"\r\n\r\n")
    lines = head.decode("latin-1").split("\r\n")
    headers = [tuple(x.strip() for x in line.split(":", 1)) for line in lines[1:]]
    return int(lines[0].split(" ")[1]), lines[0], headers, body


def _req(method, target, body=None, headers=(("Host", "api.moby.localhost"),)):
    head = "%s %s HTTP/1.1\r\n" % (method, target) + "".join("%s: %s\r\n" % h for h in headers)
    if body is not None:
        if not isinstance(body, bytes):
            body = json.dumps(body).encode()
        head += "Content-Type: application/json\r\nContent-Length: %d\r\n" % len(body)
    return (head + "\r\n").encode() + (body or b"")


def _exactly(s, n):
    out = b""
    while len(out) < n:
        data = s.recv(n - len(out))
        if not data:
            break
        out += data
    return out


def _until_eof(s):
    out = b""
    while True:
        data = s.recv(65536)
        if not data:
            return out
        out += data


class _Rig(unittest.TestCase):
    """A guard for persona p1 (debug on, its log in self.lines, linger 0.2 s) in front of a Fake."""
    def setUp(self):
        self.dir = _tmpdir()
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.fake = Fake(os.path.join(self.dir, "up.sock"))
        self.addCleanup(self.fake.close)
        self.lines = []
        self.start(self.fake.path)

    def start(self, upstream):
        self.guard = guard.Guard("p1", upstream, debug=True, log=self.lines.append, linger=0.2)
        self.started = getattr(self, "started", 0) + 1
        self.path = os.path.join(self.dir, "g%d.sock" % self.started)
        lsock = guard.listen(self.path)
        t = threading.Thread(target=self.guard.serve, args=(lsock,), daemon=True)
        t.start()
        self.addCleanup(lsock.close)
        self.addCleanup(t.join, 5)
        self.addCleanup(self.guard.stop, lsock)

    def connect(self):
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(5)
        s.connect(self.path)
        return s

    def ask(self, raw):
        """-> (status, status line, headers, body) of the answer to raw."""
        s = self.connect()
        try:
            s.sendall(raw)
            return _response(_until_eof(s))
        finally:
            s.close()

    def sent(self, method, path):
        """The one request that reached the fake for method and path, its body decoded."""
        got = self.fake.find(method, path)
        self.assertEqual(len(got), 1, (method, path, [(r["method"], r["target"]) for r in self.fake.got]))
        return got[0]

    def refused(self, raw, *words):
        code, _, _, body = self.ask(raw)
        self.assertEqual(code, 403, raw)
        msg = json.loads(body.decode())["message"]
        for w in words:
            self.assertIn(w, msg)
        return msg


class Policy(_Rig):
    def test_create_labels(self):
        """Labels gets the persona on each create; what else the body has stays; Labels spelled otherwise is Labels;
        label keys alike but for case are two."""
        p = "nexus.persona"
        for target, body, want in (
                ("/v1.56/containers/create?name=c1", {"Image": "busybox", "Labels": {"a": "1"}}, {"a": "1", p: "p1"}),
                ("/v1.56/volumes/create", {"Driver": "local", "Name": "v1"}, {p: "p1"}),
                ("/v1.56/networks/create", {"Name": "n1", "Labels": None}, {p: "p1"}),
                ("/v1.56/networks/create?x=1", {"Name": "n2", "Labels": {p: "p2"}}, {p: "p1"}),
                ("/v1.56/volumes/create?y=2", {"labels": {"b": "2", "B": "3"}}, {"b": "2", "B": "3", p: "p1"}),
                ("/v1.56/services/create", {"Name": "s1", "TaskTemplate": {}}, {p: "p1"}),
                ("/v1.56/secrets/create", {"Name": "k1", "Data": "eA==", "Labels": {"a": "1"}}, {"a": "1", p: "p1"}),
                ("/v1.56/configs/create", {"Name": "g1", "Data": "eA=="}, {p: "p1"})):
            self.fake.got = []
            code, _, _, _ = self.ask(_req("POST", target, body))
            self.assertEqual(code, 201 if "volumes" in target else 200, target)
            r = self.sent("POST", target.split("?")[0])
            self.assertEqual(r["target"], target)                       # the query as it came
            got = json.loads(r["body"].decode())
            self.assertEqual(got.get("Labels"), want, target)
            self.assertEqual([k for k in got if k.lower() == "labels"], ["Labels"])
            self.assertEqual({k: v for k, v in got.items() if k.lower() != "labels"},
                             {k: v for k, v in body.items() if k.lower() != "labels"})
            self.assertEqual(r["h"]["content-length"], str(len(r["body"])))
            self.assertEqual([v for k, v in r["low"] if k == "connection"], ["close"])
        # A body it can't read, or Labels that isn't a map: on as it came (the daemon says what's wrong).
        for body in (b"{nope", b'{"Labels": ["a=1"]}', b"[1]", b" "):
            self.fake.got = []
            self.ask(_req("POST", "/v1.56/containers/create", body))
            self.assertEqual(self.sent("POST", "/v1.56/containers/create")["body"], body)
        self.assertTrue(any(l.startswith("docker-guard: rewrote POST /v1.56/containers/create: Labels "
                                         "nexus.persona=p1") for l in self.lines), self.lines)

    def test_implicit_volumes(self):
        """Each named volume the container would make is made first, labelled (once; the container's volume driver,
        or Mounts' driver, its options and labels, kept), after every check: one that is there, a host path, an
        anonymous volume and a bind are left alone, and each Mounts volume carries the label for the daemon."""
        self.fake.add("volumes", {"Name": "there", "Labels": None}, "there")
        spec = {"Image": "busybox", "HostConfig": {
            "VolumeDriver": "foo", "Binds": ["new1:/a", "/host:/b", "there:/c:ro", "new1:/d:rw", "/anon"],
            "Mounts": [{"Type": "volume", "Source": "new2", "Target": "/e", "VolumeOptions": {
                "DriverConfig": {"Name": "local", "Options": {"type": "tmpfs", "device": "tmpfs"}},
                "Labels": {"x": "y"}}},
                {"Type": "volume", "Source": "", "Target": "/f"},
                {"Type": "bind", "Source": "/x", "Target": "/g"}]}}
        code, _, _, _ = self.ask(_req("POST", "/v1.44/containers/create?name=c", spec))
        self.assertEqual(code, 200)
        made = [json.loads(r["body"].decode()) for r in self.fake.find("POST", "/v1.44/volumes/create")]
        self.assertEqual(made, [
            {"Name": "new1", "Labels": {"nexus.persona": "p1"}, "Driver": "foo"},
            {"Name": "new2", "Labels": {"x": "y", "nexus.persona": "p1"}, "Driver": "local",
             "DriverOpts": {"type": "tmpfs", "device": "tmpfs"}}])
        self.assertEqual([(r["method"], urllib.parse.unquote(r["target"])) for r in self.fake.got], [
            ("GET", "/v1.44/volumes/new1"), ("GET", "/v1.44/volumes/there"), ("GET", "/v1.44/volumes/new2"),
            ("GET", "/v1.44/images/busybox/json"),                 # its VOLUMEs: none here (a 404)
            ("POST", "/v1.44/volumes/create"), ("POST", "/v1.44/volumes/create"),
            ("POST", "/v1.44/containers/create?name=c")])          # the create comes last
        host = json.loads(self.fake.got[-1]["body"].decode())["HostConfig"]
        mounts = spec["HostConfig"]["Mounts"]
        self.assertEqual(host, dict(spec["HostConfig"], Mounts=[
            dict(mounts[0], VolumeOptions=dict(mounts[0]["VolumeOptions"], Labels={"x": "y", "nexus.persona": "p1"})),
            dict(mounts[1], VolumeOptions={"Labels": {"nexus.persona": "p1"}}), mounts[2]]))
        self.assertIn("docker-guard: rewrote POST /v1.44/containers/create: Labels nexus.persona=p1; volumes made, "
                      "labelled: new1, new2", self.lines)

    def test_mounting_another_personas_volume(self):
        """A named volume (Binds, Mounts) labelled another persona's, or --volumes-from a container that is, is
        refused, and nothing is made (a mixed spec included); ddev's shared ones by exact name, unlabelled ones,
        this persona's and gone ones go on; a lookup that fails refuses. volumes/create of a name another persona's
        volume has is refused; of one that is there otherwise, on, with no "rewrote" (the daemon answers with it as
        it is)."""
        p2 = {"nexus.persona": "p2"}
        self.fake.add("volumes", {"Name": "theirs", "Labels": p2}, "theirs")
        self.fake.add("volumes", {"Name": "vmine", "Labels": {"nexus.persona": "p1"}}, "vmine")
        self.fake.add("volumes", {"Name": "there", "Labels": None}, "there")
        self.fake.add("volumes", {"Name": "ddev-global-cache", "Labels": p2}, "ddev-global-cache")
        self.fake.add("volumes", {"Name": "ddev-site-db", "Labels": p2}, "ddev-site-db")
        self.fake.add("volumes", FAIL, "broken")
        self.fake.add("containers", {"Id": ID_THEIRS, "Name": "/c2", "Config": {"Labels": p2}}, "c2", ID_THEIRS,
                      ID_THEIRS[:12])
        self.fake.add("containers", {"Id": ID_MINE, "Name": "/c1", "Config": {"Labels": {"nexus.persona": "p1"}}},
                      "c1")
        self.fake.add("containers", {"Id": ID_BARE, "Name": "/cbare", "Config": {}}, "cbare")
        self.fake.add("containers", {"Id": ID_AGENT, "Name": "/ddev-ssh-agent", "Config": {"Labels": p2}},
                      "ddev-ssh-agent")
        self.fake.add("containers", FAIL, "cbroken")

        def create(host, *words):
            self.fake.got = []
            target = "/v1.56/containers/create?name=x"
            if not words:
                self.assertEqual(self.ask(_req("POST", target, {"Image": "busybox", "HostConfig": host}))[0], 200, host)
                return self.sent("POST", "/v1.56/containers/create")
            msg = self.refused(_req("POST", target, {"Image": "busybox", "HostConfig": host}), *words)
            self.assertEqual([r["method"] for r in self.fake.got], ["GET"] * len(self.fake.got), host)   # nothing made
            return msg

        for host in ({"Binds": ["theirs:/x"]}, {"Binds": ["new9:/a", "theirs:/b:ro"]},
                     {"Mounts": [{"Type": "volume", "Source": "theirs", "Target": "/x"}]},
                     {"Binds": ["new9:/a"], "Mounts": [{"Type": "volume", "Source": "theirs", "Target": "/x"}]}):
            msg = create(host, "nexus guard: volume theirs isn't labelled nexus.persona=p1 (it is persona p2's",
                         "a volume's name is global", "e.g. p1-theirs")
        self.assertEqual(self.lines[-1], "docker-guard: refused POST /v1.56/containers/create: " + msg[13:])
        create({"Binds": ["ddev-site-db:/x"]}, "volume ddev-site-db isn't labelled")       # only like ddev's
        create({"Binds": ["broken:/x"]}, "couldn't look up volume broken", "boom")
        for v in ("vmine", "there", "ddev-global-cache"):
            create({"Binds": ["%s:/x" % v], "Mounts": [{"Type": "volume", "Source": v, "Target": "/y"}]})
        for given in (["c2"], ["c2:ro"], [ID_THEIRS[:12] + ":rw"], ["c1", "c2"]):
            create({"VolumesFrom": given}, "container c2", "isn't labelled nexus.persona=p1 (it is persona p2's",
                   "--volumes-from")
        create({"VolumesFrom": ["cbroken"]}, "couldn't look up container cbroken")
        create({"VolumesFrom": ["c1", "cbare:ro", "gone:rw", "ddev-ssh-agent"]})
        self.assertEqual([r["target"] for r in self.fake.got if r["method"] == "GET"],
                         ["/v1.56/containers/c1/json", "/v1.56/containers/cbare/json", "/v1.56/containers/gone/json",
                          "/v1.56/containers/ddev-ssh-agent/json"])     # no image lookup with --volumes-from
        # volumes/create
        self.fake.got = []
        self.lines[:] = []
        self.refused(_req("POST", "/v1.56/volumes/create", {"Name": "theirs", "Labels": {"a": "1"}}),
                     "volume theirs isn't labelled nexus.persona=p1 (it is persona p2's", "e.g. p1-theirs")
        self.refused(_req("POST", "/v1.56/volumes/create", {"Name": "broken"}), "couldn't look up volume broken")
        self.assertEqual([r["method"] for r in self.fake.got], ["GET", "GET"])
        for name in ("there", "vmine", "ddev-global-cache", "new8"):
            self.fake.got = []
            self.assertEqual(self.ask(_req("POST", "/v1.56/volumes/create", {"Name": name}))[0], 201, name)
            self.assertEqual(json.loads(self.sent("POST", "/v1.56/volumes/create")["body"].decode()),
                             {"Name": name, "Labels": {"nexus.persona": "p1"}})
            self.assertEqual(self.fake.got[0]["target"], "/v1.56/volumes/" + name)
        self.assertIn("docker-guard: POST /v1.56/volumes/create: volume there is there already: the daemon answers "
                      "with it as it is", self.lines)
        self.assertEqual([l for l in self.lines if "rewrote" in l],
                         ["docker-guard: rewrote POST /v1.56/volumes/create: Labels nexus.persona=p1"])   # new8's

    def test_anonymous_volumes(self):
        """The daemon makes an anonymous volume unlabelled: each Mounts volume gets VolumeOptions.Labels, and each
        path of Volumes (`-v /path`) and of the image's VOLUMEs that nothing else mounts (a bind's or Mount's target,
        a tmpfs, a Binds path alone; trailing slashes aside) becomes such a Mount, in the spelling the body has; none
        with VolumesFrom, whose volumes a Mount would hide. An image that isn't here (or can't be looked up) adds
        none, and nothing is refused for it."""
        self.fake.add("images", {"Id": "sha256:" + ID_MINE, "Config": {"Volumes": {
            "/var/lib/pg": {}, "/c": {}, "/t": {}, "/e/": {}, "/x": {}, "/data": {}}}}, "pg")
        self.fake.add("images", FAIL, "broken")
        labelled = {"Labels": {"nexus.persona": "p1"}}

        def sent(spec):
            self.fake.got = []
            self.assertEqual(self.ask(_req("POST", "/v1.56/containers/create", spec))[0], 200, spec)
            return json.loads(self.sent("POST", "/v1.56/containers/create")["body"].decode())

        bind = {"Type": "bind", "Source": "/h", "Target": "/e"}
        host = sent({"Image": "pg", "Volumes": {"/data": {}, "/b/": {}}, "HostConfig": {
            "Binds": ["/host:/c:ro", "/x"], "Tmpfs": {"/t": ""},
            "Mounts": [{"Type": "volume", "Target": "/b", "VolumeOptions": {"Labels": {"k": "v"}, "NoCopy": True}},
                       bind]}})["HostConfig"]
        self.assertEqual(host["Mounts"], [
            {"Type": "volume", "Target": "/b", "VolumeOptions": {"Labels": {"k": "v", "nexus.persona": "p1"},
                                                                "NoCopy": True}}, bind,
            {"Type": "volume", "Target": "/data", "VolumeOptions": labelled},
            {"Type": "volume", "Target": "/var/lib/pg", "VolumeOptions": labelled}])
        self.assertEqual([r["target"] for r in self.fake.got], ["/v1.56/images/pg/json", "/v1.56/containers/create"])
        self.assertIn("docker-guard: rewrote POST /v1.56/containers/create: Labels nexus.persona=p1; anonymous volumes "
                      "labelled: /data, /var/lib/pg", self.lines)
        # No HostConfig: one only when a Mount is added. An image not here, or a failed lookup: none of its.
        self.assertEqual(sent({"Image": "nosuch", "Volumes": {"/d": {}}})["HostConfig"],
                         {"Mounts": [{"Type": "volume", "Target": "/d", "VolumeOptions": labelled}]})
        self.assertNotIn("HostConfig", sent({"Image": "nosuch"}))
        self.assertEqual(sent({"Image": "broken", "HostConfig": {"Mounts": None}})["HostConfig"], {"Mounts": None})
        self.assertIn("docker-guard: POST /v1.56/containers/create: image broken not looked up (boom): its VOLUMEs, "
                      "if any, are made unlabelled", self.lines)
        # VolumesFrom: the explicit Mounts labelled, nothing added, no image lookup.
        self.fake.add("containers", {"Id": ID_MINE, "Name": "/c1", "Config": {"Labels": {"nexus.persona": "p1"}}},
                      "c1")
        host = sent({"Image": "pg", "Volumes": {"/d": {}}, "HostConfig": {
            "VolumesFrom": ["c1:ro"], "Mounts": [{"Type": "volume", "Target": "/m"}]}})["HostConfig"]
        self.assertEqual(host["Mounts"], [{"Type": "volume", "Target": "/m", "VolumeOptions": labelled}])
        self.assertEqual([r["target"] for r in self.fake.got],
                         ["/v1.56/containers/c1/json", "/v1.56/containers/create"])
        # The body's own spellings: no second one beside them.
        got = sent({"Image": "nosuch", "volumes": {"/n": {}}, "hostconfig": {
            "mounts": [{"type": "volume", "target": "/m", "volumeoptions": {"labels": {"k": "v"}}}]}})
        self.assertEqual(sorted(got), ["Image", "Labels", "hostconfig", "volumes"])
        self.assertEqual(got["hostconfig"], {"mounts": [
            {"type": "volume", "target": "/m", "volumeoptions": {"Labels": {"k": "v", "nexus.persona": "p1"}}},
            {"Type": "volume", "Target": "/n", "VolumeOptions": labelled}]})

    def test_create_bodies_read_otherwise_are_refused(self):
        """400, and nothing reaches the daemon, for a create body it would read otherwise than the guard: a key twice
        at any depth, two spellings of a field the guard reads, not UTF-8, more after the JSON. No body, or null, is
        labelled as {} would be."""
        for target, body, words in (
                ("/v1.56/containers/create", b'{"Image":"x","Labels":{"a":"1"},"Labels":{"b":"2"}}',
                 'key "Labels" twice'),
                ("/v1.56/containers/create", b'{"Image":"x","HostConfig":{"Binds":["v:/a"],"Binds":[]}}',
                 'key "Binds" twice'),
                ("/v1.56/volumes/create", b'{"Name":"v","Labels":{"a":"1","a":"2"}}', 'key "a" twice'),
                ("/v1.56/containers/create", b'{"Image":"x","HostConfig":{"Binds":["new3:/a"]},"hostconfig":{}}',
                 'the keys "HostConfig" and "hostconfig"'),
                ("/v1.56/containers/create", '{"HostConfig":{},"Ho\u017ftConfig":{"Binds":["v:/a"]}}'.encode(),
                 'the keys "HostConfig" and "Ho\\u017ftConfig"'),
                ("/v1.56/networks/create", b'{"Name":"n","labels":{},"Labels":{}}', 'the keys "labels" and "Labels"'),
                ("/v1.56/containers/create", b'{"HostConfig":{"Binds":[],"binds":["v:/a"]}}', '"Binds" and "binds"'),
                ("/v1.56/containers/create", b'{"HostConfig":{"Mounts":[{"Type":"volume","Source":"a","source":"b"}]}}',
                 '"Source" and "source"'),
                ("/v1.56/containers/create", b'{"HostConfig":{"Mounts":[{"VolumeOptions":{"DriverConfig":{"Name":"a",'
                 b'"NAME":"b"}}}]}}', '"Name" and "NAME"'),
                ("/v1.56/containers/create", b'{"Image":"x","Env":["X=caf\xe9"]}', "isn't UTF-8"),
                ("/v1.56/volumes/create", b'{"Name":"v"}}', "more after its JSON"),
                ("/v1.56/containers/create", b'{"Image":"x"} {"Labels":{}}', "more after its JSON"),
                ("/v1.56/containers/create", b'{"Image":"x","Env":' + b"[" * 5000 + b"]" * 5000 + b"}",
                 "nested deeper")):
            self.fake.got = []
            code, _, _, answer = self.ask(_req("POST", target, body))
            self.assertEqual(code, 400, body)
            msg = json.loads(answer.decode())["message"]
            self.assertTrue(msg.startswith("nexus guard: a request body "), msg)
            self.assertIn(words, msg)
            self.assertEqual(self.fake.got, [], body)
        self.assertIn("docker-guard: answered 400 to POST /v1.56/containers/create: a request body with more after its "
                      "JSON (the daemon may read only the first value: send one)", self.lines)
        # Labels' own keys may differ in case alone, and a key may be in two objects.
        self.ask(_req("POST", "/v1.56/volumes/create", b'{"Name":"v","Labels":{"a":"1","A":"2"},"DriverOpts":{"a":3}}'))
        self.assertEqual(json.loads(self.sent("POST", "/v1.56/volumes/create")["body"].decode())["Labels"],
                         {"a": "1", "A": "2", "nexus.persona": "p1"})
        for raw in (b"POST /v1.56/volumes/create HTTP/1.1\r\nHost: x\r\nContent-Length: 0\r\n\r\n",
                    _req("POST", "/v1.56/volumes/create", b"null")):
            self.fake.got = []
            self.assertEqual(self.ask(raw)[0], 201)
            r = self.sent("POST", "/v1.56/volumes/create")
            self.assertEqual((json.loads(r["body"].decode()), r["h"]["content-type"]),
                             ({"Labels": {"nexus.persona": "p1"}}, "application/json"))

    def test_prune_filters(self):
        """label=nexus.persona=p1 joins the filters, in the form they have; none, empty or null get it alone; filters
        it can't read are refused; a body never goes on."""
        mine = "nexus.persona=p1"

        def pruned(query, kind="containers", body=None):
            self.fake.got = []
            code, _, _, _ = self.ask(_req("POST", "/v1.56/%s/prune%s" % (kind, query), body))
            self.assertEqual(code, 200, query)
            r = self.sent("POST", "/v1.56/%s/prune" % kind)
            self.assertEqual((r["body"], r["h"]["content-length"]), (b"", "0"))
            q = urllib.parse.parse_qs(r["target"].split("?", 1)[1], keep_blank_values=True)
            self.assertEqual(len(q.pop("filters")), 1)
            return json.loads(urllib.parse.parse_qs(r["target"].split("?", 1)[1])["filters"][0]), q

        for kind in ("containers", "volumes", "networks", "images"):
            self.assertEqual(pruned("", kind), ({"label": {mine: True}}, {}))
        q = urllib.parse.quote
        self.assertEqual(pruned("?filters=" + q('{"label":{"a=1":true},"dangling":{"true":true}}'), "images"),
                         ({"label": {"a=1": True, mine: True}, "dangling": {"true": True}}, {}))
        self.assertEqual(pruned("?filters=" + q('{"label":["a=1"]}'))[0], {"label": ["a=1", mine]})
        self.assertEqual(pruned("?filters=" + q('{"dangling":["true"]}'), "images")[0],
                         {"dangling": ["true"], "label": [mine]})
        self.assertEqual(pruned("?filters=" + q('{"all":{"true":true}}'), "volumes")[0],
                         {"all": {"true": True}, "label": {mine: True}})
        for empty in ("?filters=", "?filters=null", "?filters=%20", "?filters={}"):
            self.assertEqual(pruned(empty)[0], {"label": {mine: True}}, empty)
        self.assertEqual(pruned("?all=1&filters=" + q('{"until":{"24h":true}}') + "&filters=junk"),
                         ({"until": {"24h": True}, "label": {mine: True}}, {"all": ["1"]}))
        self.assertEqual(pruned("", body=b"filters=%7B%7D")[0], {"label": {mine: True}})
        self.fake.got = []
        for junk in ("nope", "[1]", '{"label":"x"}', '"x"'):
            self.refused(_req("POST", "/v1.56/containers/prune?filters=" + q(junk)), "isn't labelled nexus.persona=p1")
        self.assertEqual(self.fake.got, [])
        self.assertIn('docker-guard: rewrote POST /v1.56/containers/prune: filters {"label":{"nexus.persona=p1":true}}',
                      self.lines)

    def test_build_prune_refused(self):
        for target in ("/v1.56/build/prune?all=1", "/build/prune", "/v1.56/build/prune?filters=" +
                       urllib.parse.quote('{"label":{"x=1":true}}')):
            msg = self.refused(_req("POST", target), "the build cache isn't labelled nexus.persona=p1", "ask the user")
            self.assertTrue(msg.startswith("nexus guard: "), msg)
        self.assertEqual(self.fake.got, [])
        self.assertTrue(any(line.startswith("docker-guard: refused POST /build/prune: the build cache")
                            for line in self.lines))

    def test_ownership(self):
        """By name, short id or full id: labelled this persona's goes on; another's or no one's is refused, for each
        verb; gone goes on (the daemon answers); a request that isn't one of these goes on unchecked."""
        self.fake.add("containers", {"Id": ID_MINE, "Name": "/mine", "Config": {"Labels": {"nexus.persona": "p1"}}},
                      "mine", ID_MINE, ID_MINE[:12])
        self.fake.add("containers", {"Id": ID_THEIRS, "Name": "/theirs", "Config": {
            "Labels": {"nexus.persona": "p2"}}}, "theirs", ID_THEIRS, ID_THEIRS[:12])
        self.fake.add("containers", {"Id": ID_BARE, "Name": "/bare", "Config": {"Labels": None}}, "bare", ID_BARE)
        self.fake.add("volumes", {"Name": "vmine", "Labels": {"nexus.persona": "p1"}}, "vmine")
        self.fake.add("volumes", {"Name": "vbare", "Labels": None}, "vbare")
        self.fake.add("networks", {"Id": ID_MINE, "Name": "nmine", "Labels": {"nexus.persona": "p1"}}, ID_MINE, "nmine")
        self.fake.add("networks", {"Id": ID_THEIRS, "Name": "ntheirs", "Labels": {"nexus.persona": "p2"}}, ID_THEIRS)
        for ref in ("mine", ID_MINE, ID_MINE[:12]):
            self.fake.got = []
            self.assertEqual(self.ask(_req("DELETE", "/v1.56/containers/%s?force=1&v=1" % ref))[0], 200)
            self.sent("DELETE", "/v1.56/containers/" + ID_MINE)
            for verb in ("stop", "kill", "restart", "pause", "unpause", "rename"):
                self.assertEqual(self.ask(_req("POST", "/v1.56/containers/%s/%s" % (ref, verb)))[0], 200)
                self.sent("POST", "/v1.56/containers/%s/%s" % (ID_MINE, verb))
            self.ask(_req("POST", "/v1.56/containers/%s/update" % ref, {"Memory": 1}))
            self.assertEqual(self.sent("POST", "/v1.56/containers/%s/update" % ID_MINE)["body"], b'{"Memory": 1}')
        self.fake.got = []
        for ref, shown, whose in (("theirs", "theirs", "it is persona p2's"),
                                  (ID_THEIRS[:12], "theirs (%s)" % ID_THEIRS[:12], "persona p2"),     # compose's id
                                  ("bare", "bare", "it has no nexus.persona label")):
            self.refused(_req("DELETE", "/v1.56/containers/" + ref),
                         "nexus guard: container %s isn't labelled nexus.persona=p1 (" % shown, whose,
                         "who can remove it on the VM")
            for verb in ("stop", "kill", "restart", "pause", "unpause", "update", "rename"):
                self.refused(_req("POST", "/v1.56/containers/%s/%s?t=1" % (ref, verb)), "may %s only" % verb)
        self.refused(_req("DELETE", "/v1.56/volumes/vbare"), "volume vbare isn't labelled", "no nexus.persona label")
        self.refused(_req("DELETE", "/v1.56/networks/" + ID_THEIRS), "network ntheirs (%s) isn't" % ID_THEIRS[:12],
                     "persona p2's")
        self.assertEqual([r["method"] for r in self.fake.got], ["GET"] * len(self.fake.got))     # inspects only
        self.fake.got = []
        self.ask(_req("DELETE", "/v1.56/volumes/vmine?force=1"))
        self.sent("DELETE", "/v1.56/volumes/vmine")
        self.ask(_req("DELETE", "/v1.56/networks/nmine"))
        self.sent("DELETE", "/v1.56/networks/" + ID_MINE)
        self.ask(_req("DELETE", "/v1.56/containers/gone"))
        self.assertEqual([(r["method"], r["target"]) for r in self.fake.got[-2:]],
                         [("GET", "/v1.56/containers/gone/json"), ("DELETE", "/v1.56/containers/gone")])
        self.fake.got = []
        for method, target in (("POST", "/v1.56/containers/theirs/start"), ("GET", "/v1.56/containers/theirs/json"),
                               ("POST", "/v1.56/containers/theirs/exec"), ("POST", "/v1.56/containers/theirs/wait"),
                               ("GET", "/v1.56/volumes/vbare")):
            self.ask(_req(method, target, {} if method == "POST" else None))
            self.assertEqual([(r["method"], r["target"]) for r in self.fake.got], [(method, target)])
            self.fake.got = []

    def test_forwarded_by_id(self):
        """What goes on, let through by the inspect, names what was inspected: a container or a network by its full
        Id, whatever the ref (name, short id, %-escaped, absolute form), the query and body as they came; a volume
        by its name; something the daemon doesn't have as it came."""
        self.fake.add("containers", {"Id": ID_MINE, "Name": "/mine", "Config": {"Labels": {"nexus.persona": "p1"}}},
                      "mine", ID_MINE[:12], ID_THEIRS[:12])        # named like a prefix of another's id
        self.fake.add("networks", {"Id": ID_OTHER, "Name": "net", "Labels": {"nexus.persona": "p1"}}, "net")
        self.fake.add("volumes", {"Name": "vol", "Labels": {"nexus.persona": "p1"}}, "vol")
        for method, target, to in (
                ("DELETE", "/v1.56/containers/mine?force=1", "/v1.56/containers/%s?force=1" % ID_MINE),
                ("POST", "/v1.56/containers/%s/stop?t=3" % ID_THEIRS[:12], "/v1.56/containers/%s/stop?t=3" % ID_MINE),
                ("POST", "/v1.44/containers/%6Dine/rename?name=x", "/v1.44/containers/%s/rename?name=x" % ID_MINE),
                ("POST", "http://h/containers/mine/kill?signal=HUP", "/containers/%s/kill?signal=HUP" % ID_MINE),
                ("DELETE", "/v1.56/networks/net", "/v1.56/networks/" + ID_OTHER),
                ("DELETE", "/v1.56/volumes/vol?force=1", "/v1.56/volumes/vol?force=1"),
                ("DELETE", "/v1.56/containers/gone?v=1", "/v1.56/containers/gone?v=1")):
            self.fake.got = []
            self.assertEqual(self.ask(_req(method, target))[0], 200, target)
            self.assertEqual([r["method"] for r in self.fake.got], ["GET", method], target)
            self.assertEqual(self.fake.got[1]["target"], to, target)
        self.ask(_req("POST", "/v1.56/containers/mine/update", {"Memory": 1}))
        self.assertEqual(self.sent("POST", "/v1.56/containers/%s/update" % ID_MINE)["body"], b'{"Memory": 1}')
        self.assertIn("docker-guard: DELETE /v1.56/containers/gone: no such container gone, on to the daemon",
                      self.lines)

    def test_slashed_refs(self):
        """moby routes {name:.*}: parent/alias (a legacy link) names the linked container, checked as it; a network's
        name may hold a "/"; DELETE containers/P/A?link=1 removes P's link: P checked, the path on as it came."""
        self.fake.add("containers", {"Id": ID_THEIRS, "Name": "/theirs", "Config": {"Labels": {
            "nexus.persona": "p2"}}}, "theirs", "mine/db", "theirs/al")
        self.fake.add("containers", {"Id": ID_MINE, "Name": "/mine", "Config": {"Labels": {"nexus.persona": "p1"}}},
                      "mine", "mine/self")
        self.fake.add("networks", {"Id": ID_THEIRS, "Name": "a/b", "Labels": {"nexus.persona": "p2"}}, "a/b")
        self.fake.add("networks", {"Id": ID_MINE, "Name": "c/d", "Labels": {"nexus.persona": "p1"}}, "c/d")
        for verb in ("stop", "kill", "restart", "pause", "unpause", "update", "rename"):
            self.refused(_req("POST", "/v1.56/containers/mine/db/" + verb), "container mine/db isn't labelled",
                         "persona p2's")
            self.fake.got = []
            self.assertEqual(self.ask(_req("POST", "/v1.56/containers/mine/self/" + verb))[0], 200)
            self.assertEqual([r["target"] for r in self.fake.got], ["/v1.56/containers/mine/self/json",
                                                                    "/v1.56/containers/%s/%s" % (ID_MINE, verb)])
        for target in ("/v1.56/containers/mine/db?force=1", "/v1.56/containers/mine%2Fdb",
                       "/v1.56/containers/theirs/al?link=0", "/v1.56/containers/theirs/al?link=false&link=1"):
            self.fake.got = []
            self.refused(_req("DELETE", target), "container mine/db" if "mine" in target else "container theirs/al",
                         "may remove only")
            self.assertEqual([r["target"] for r in self.fake.got],
                             ["/v1.56/containers/%s/json" % ("mine/db" if "mine" in target else "theirs/al")])
        self.fake.got = []
        self.refused(_req("DELETE", "/v1.56/containers/theirs/al?link=1"), "container theirs isn't labelled",
                     "may unlink only")
        self.assertEqual([r["target"] for r in self.fake.got], ["/v1.56/containers/theirs/json"])
        self.fake.got = []
        self.assertEqual(self.ask(_req("DELETE", "/v1.56/containers/mine/db?link=yes"))[0], 200)
        self.assertEqual([r["target"] for r in self.fake.got], ["/v1.56/containers/mine/json",
                                                                "/v1.56/containers/mine/db?link=yes"])
        self.fake.got = []
        self.refused(_req("DELETE", "/v1.56/networks/a/b"), "network a/b isn't labelled", "persona p2's")
        self.assertEqual(self.ask(_req("DELETE", "/v1.56/networks/c/d"))[0], 200)
        self.assertEqual([r["target"] for r in self.fake.got], ["/v1.56/networks/a/b", "/v1.56/networks/c/d",
                                                                "/v1.56/networks/" + ID_MINE])
        self.fake.got = []
        self.ask(_req("DELETE", "/v1.56/containers/theirs/extra"))      # no such one: on, the daemon says so
        self.assertEqual([(r["method"], r["target"]) for r in self.fake.got],
                         [("GET", "/v1.56/containers/theirs/extra/json"), ("DELETE", "/v1.56/containers/theirs/extra")])

    def test_ddev_by_exact_name(self):
        """ddev's shared containers, network and volumes go on whoever made them, named by id too, for what ddev
        does to them (stop, kill, restart, remove), not for pause, unpause, update, rename or a link's removal; a
        name that is only like one, or one of another kind, doesn't."""
        self.fake.add("containers", {"Id": ID_ROUTER, "Name": "/ddev-router", "Config": {"Labels": {
            "com.ddev.platform": "ddev", "nexus.persona": "p2"}}}, "ddev-router", ID_ROUTER)
        self.fake.add("containers", {"Id": ID_AGENT, "Name": "/ddev-ssh-agent", "Config": {}}, "ddev-ssh-agent")
        self.fake.add("containers", {"Name": "/ddev-router2", "Config": {}}, "ddev-router2")
        self.fake.add("containers", {"Name": "/ddev_default", "Config": {}}, "ddev_default")
        self.fake.add("networks", {"Id": ID_BARE, "Name": "ddev_default", "Labels": {}}, ID_BARE, "ddev_default")
        for v in ("ddev-global-cache", "ddev-ssh-agent_socket_dir", "ddev-router-traefik", "ddev-site-db"):
            self.fake.add("volumes", {"Name": v, "Labels": None}, v)
        for target, to in (("/v1.56/containers/" + ID_ROUTER, None), ("/v1.56/containers/ddev-ssh-agent", ID_AGENT),
                           ("/v1.56/networks/ddev_default", ID_BARE), ("/v1.56/volumes/ddev-global-cache", None),
                           ("/v1.56/volumes/ddev-ssh-agent_socket_dir", None),
                           ("/v1.56/volumes/ddev-router-traefik", None)):
            self.assertEqual(self.ask(_req("DELETE", target))[0], 200, target)
            self.sent("DELETE", target.rsplit("/", 1)[0] + "/" + to if to else target)
        for verb in ("stop", "kill", "restart"):
            self.assertEqual(self.ask(_req("POST", "/v1.56/containers/ddev-router/" + verb))[0], 200)
            self.sent("POST", "/v1.56/containers/%s/%s" % (ID_ROUTER, verb))
        self.fake.got = []
        for verb in ("pause", "unpause", "update", "rename"):
            self.refused(_req("POST", "/v1.56/containers/ddev-router/%s?name=x" % verb,
                              {} if verb == "update" else None),
                         "nexus guard: container ddev-router isn't labelled nexus.persona=p1 (it is ddev's, shared",
                         "may stop, restart or remove it, as ddev does, but not %s it" % verb)
        self.refused(_req("DELETE", "/v1.56/containers/ddev-router/web?link=1"), "container ddev-router isn't",
                     "but not unlink it")
        self.assertEqual([r["method"] for r in self.fake.got], ["GET"] * 5)
        for target in ("/v1.56/containers/ddev-router2", "/v1.56/containers/ddev_default",
                       "/v1.56/volumes/ddev-site-db"):
            self.refused(_req("DELETE", target), "isn't labelled nexus.persona=p1")

    def test_images(self):
        """An untag goes on, even of a persona image; a removal (by tag, by id, -f) is refused of a persona image, of
        another persona's, of one a compose project not this persona's built (ddev's go), and of one a container
        not labelled this persona's uses; an image labelled this persona's (its own commit) is its own whatever it
        inherited. One that goes, goes on as an id as the full Id, a bare name with its :latest, a name:tag or a
        digest as it came. The messages don't say "only what it made": unlabelled images go."""
        ids = ["%02x" % n * 32 for n in range(0x10, 0x20)]

        def img(tags, labels, *aliases, **kw):
            self.fake.add("images", dict({"Id": "sha256:" + ids.pop(), "RepoTags": tags,
                                          "Config": {"Labels": labels}}, **kw), *(tuple(tags or ()) + aliases))
            return self.fake.objects[-1][2]["Id"]

        self.fake.add("images", {"Id": "sha256:" + ID_MINE, "RepoTags": ["nxw/a:1", "nxw/a:2"],
                                 "Config": {"Labels": {"agent.flavour": "claude"}}},
                      "nxw/a:1", "nxw/a:2", "sha256:" + ID_MINE, ID_MINE[:12])
        self.fake.add("images", {"Id": "sha256:" + ID_THEIRS, "RepoTags": ["local/agent-claude-docker:latest"],
                                 "Config": {"Labels": {"agent.flavour": "claude", "agent.uid": "1000",
                                                       "com.docker.compose.project": "agents"}}},
                      "local/agent-claude-docker:latest", "local/agent-claude-docker", ID_THEIRS)
        self.fake.add("images", {"Id": "sha256:" + ID_AGENT, "RepoTags": ["nxw/o:1"], "Config": {"Labels": {
            "nexus.persona": "p2"}}}, "nxw/o:1")
        self.fake.add("images", {"Id": "sha256:" + ID_BARE, "RepoTags": ["busybox:1.36.1-musl"], "Config": {}},
                      "busybox:1.36.1-musl", "docker.io/library/busybox:1.36.1-musl", ID_BARE)
        self.fake.add("images", {"RepoTags": ["busybox:latest", "nxw/b:1"]}, "busybox", "docker.io/library/busybox")
        self.fake.add("images", {"Id": "sha256:" + ID_OTHER, "RepoTags": None, "Config": {"Labels": None}},
                      ID_OTHER[:12], "busybox@sha256:" + ID_ROUTER)
        img(["nxw/m:1"], {"nexus.persona": "p1"})
        img(["nxw/own:1"], {"nexus.persona": "p1", "agent.flavour": "claude", "com.docker.compose.project": "agents"})
        for project in ("p1-shop", "ddev-site", "ddev-router"):
            img(["nxw/%s:1" % project], {"com.docker.compose.project": project, "com.docker.compose.service": "web"})
        for project in ("p2-shop", "agents", "p1", "p10-shop"):
            img(["nxw/%s:1" % project], {"com.docker.compose.project": project})
        used = [img(["nxw/u1:1", "nxw/u1:2"], None, ids[-1][:12]), img(["nxw/u2:1"], {}), img(["nxw/u3:1"], {}),
                img(["nxw/u4:1"], {}), img(["nxw/u5:1"], {"nexus.persona": "p1"})]
        self.fake.listed = [
            {"Id": "1" * 64, "Names": ["/theirs-db"], "ImageID": used[0], "Labels": {"nexus.persona": "p2"}},
            {"Id": "2" * 64, "Names": ["/agent-lad"], "ImageID": used[1], "Labels": {
                "agent.persona": "lad", "agent.flavour": "claude"}},
            {"Id": "3" * 64, "Names": ["/userweb"], "ImageID": used[2], "Labels": {}},
            {"Id": "4" * 64, "Names": ["/mine-1"], "ImageID": used[3], "Labels": {"nexus.persona": "p1"}},
            {"Id": "5" * 64, "Names": None, "ImageID": used[4], "Labels": None}]
        for ref, to in (("nxw/a:1", None), ("nxw/m:1", None), ("busybox:1.36.1-musl?force=1", None),
                        ("docker.io/library/busybox:1.36.1-musl", None),
                        (ID_BARE + "?force=1&noprune=1", "sha256:" + ID_BARE), ("busybox", "busybox:latest"),
                        ("docker.io/library/busybox", "docker.io/library/busybox:latest"),
                        (ID_OTHER[:12], "sha256:" + ID_OTHER), ("busybox@sha256:" + ID_ROUTER, None),
                        ("nosuch:1", None), ("nxw/own:1?force=1", None), ("nxw/p1-shop:1", None),
                        ("nxw/ddev-site:1", None), ("nxw/ddev-router:1", None), ("nxw/u1:2?force=1", None),
                        ("nxw/u4:1?force=1", None)):
            self.fake.got = []
            self.assertEqual(self.ask(_req("DELETE", "/v1.56/images/" + ref))[0], 200, ref)
            r = self.sent("DELETE", "/v1.56/images/" + (to or ref.split("?")[0]))
            self.assertEqual(r["target"].partition("?")[2], ref.partition("?")[2])
            self.assertEqual(self.fake.got[0]["target"], "/v1.56/images/%s/json" % ref.split("?")[0])
        self.assertEqual([r["target"] for r in self.fake.got], ["/v1.56/images/nxw/u4:1/json",
                                                                "/v1.56/containers/json?all=1",
                                                                "/v1.56/images/nxw/u4:1?force=1"])
        self.fake.got = []
        for ref, why in (("sha256:" + ID_MINE + "?force=1", "it is a persona image, agent.flavour=claude, or built "
                                                            "FROM one"),
                         (ID_MINE[:12], "persona image"),
                         ("local/agent-claude-docker:latest", "persona image"),
                         ("local/agent-claude-docker?force=1", "persona image"), (ID_THEIRS, "persona image"),
                         ("nxw/o:1", "it is persona p2's: leave it, or ask the user, who can remove it on the VM)"),
                         ("nxw/p2-shop:1", "compose project p2-shop built it, not one of this persona's, p1-…"),
                         ("nxw/agents:1?force=1", "compose project agents"), ("nxw/p1:1", "compose project p1 "),
                         ("nxw/p10-shop:1", "compose project p10-shop")):
            shown = ref.split("?")[0]               # an id with a tag of the image first
            shown = {ID_MINE[:12]: "nxw/a:1 (%s)" % ID_MINE[:12], "sha256:" + ID_MINE: "nxw/a:1 (%s)" % ID_MINE[:12],
                     ID_THEIRS: "local/agent-claude-docker:latest (%s)" % ID_THEIRS[:12]}.get(shown, shown)
            msg = self.refused(_req("DELETE", "/v1.56/images/" + ref),
                               "image %s isn't labelled nexus.persona=p1 (" % shown, why)
            self.assertNotIn("only what it made", msg)
        self.assertEqual([r["method"] for r in self.fake.got], ["GET"] * 10)            # inspects only
        self.fake.got = []
        for ref, why in ((used[0][7:19] + "?force=1", "theirs-db, which isn't labelled nexus.persona=p1 (it is "
                                                         "persona p2's"),
                         ("nxw/u2:1", "agent-lad, which isn't labelled nexus.persona=p1 (it is persona lad's own "
                                      "container"),
                         ("nxw/u3:1?force=1", "userweb, which isn't labelled nexus.persona=p1 (it has no "
                                              "nexus.persona label"),
                         ("nxw/u5:1", "container 5555555555")):
            shown = ref.split("?")[0]
            shown = "nxw/u1:1 (%s)" % shown if shown == used[0][7:19] else shown
            msg = self.refused(_req("DELETE", "/v1.56/images/" + ref),
                               "nexus guard: image %s is used by container " % shown, why,
                               "leave it, or ask the user, who can remove it on the VM")
        self.assertEqual([r["target"] for r in self.fake.got[-2:]], ["/v1.56/images/nxw/u5:1/json",
                                                                     "/v1.56/containers/json?all=1"])
        self.assertEqual([r["method"] for r in self.fake.got], ["GET"] * 8)
        self.fake.listed = FAIL
        self.refused(_req("DELETE", "/v1.56/images/busybox:1.36.1-musl"), "couldn't list the containers to see "
                     "whose use image busybox:1.36.1-musl (boom), so it isn't let through")
        self.assertEqual(guard.norm_ref("docker.io/library/busybox"), "busybox:latest")
        self.assertEqual(guard.norm_ref("localhost:5000/x"), "localhost:5000/x:latest")
        self.assertEqual(guard.norm_ref("library/busybox:1"), "busybox:1")

    def test_network_endpoints(self):
        """POST networks/N/{connect,disconnect}: the container the body names is checked as a stop would be (this
        persona's, or ddev's shared one by exact name) and goes on by its Id, the rest of the body as it came; the
        network isn't checked (a "/" in its name too); gone, or no container in the body, goes on as it came."""
        self.fake.add("containers", {"Id": ID_MINE, "Name": "/mine", "Config": {"Labels": {"nexus.persona": "p1"}}},
                      "mine", ID_MINE[:12])
        self.fake.add("containers", {"Id": ID_THEIRS, "Name": "/theirs", "Config": {"Labels": {
            "nexus.persona": "p2"}}}, "theirs", ID_THEIRS)
        self.fake.add("containers", {"Id": ID_BARE, "Name": "/bare", "Config": {}}, "bare")
        self.fake.add("containers", {"Id": ID_ROUTER, "Name": "/ddev-router", "Config": {"Labels": {
            "nexus.persona": "p2"}}}, "ddev-router")
        self.fake.add("containers", FAIL, "broken")
        for verb in ("connect", "disconnect"):
            for ref, net in (("mine", "theirnet"), (ID_MINE[:12], "a/b"), ("ddev-router", "ddev_default")):
                body = {"Container": ref, "EndpointConfig": {"Aliases": ["db"]}} if verb == "connect" else {
                    "Container": ref, "Force": True}
                self.fake.got = []
                self.assertEqual(self.ask(_req("POST", "/v1.56/networks/%s/%s" % (net, verb), body))[0], 200)
                r = self.sent("POST", "/v1.56/networks/%s/%s" % (net, verb))
                self.assertEqual(json.loads(r["body"].decode()),
                                 dict(body, Container=ID_ROUTER if ref == "ddev-router" else ID_MINE))
                self.assertEqual([x["target"] for x in self.fake.got], ["/v1.56/containers/%s/json" % ref,
                                                                        "/v1.56/networks/%s/%s" % (net, verb)])
            self.fake.got = []
            for body, words in (({"Container": "theirs"}, ("nexus guard: container theirs isn't labelled "
                                                           "nexus.persona=p1 (it is persona p2's",
                                                           "may %s only" % verb)),
                                ({"Container": "theirs", "Force": True}, ("persona p2's",)),
                                ({"container": "theirs"}, ("persona p2's",)),
                                ({"Container": ID_THEIRS}, ("container theirs (%s)" % ID_THEIRS[:12],)),
                                ({"Container": "bare"}, ("no nexus.persona label",)),
                                ({"Container": "broken"}, ("couldn't look up container broken",))):
                self.refused(_req("POST", "/v1.56/networks/n/" + verb, body), *words)
            self.assertEqual([x["method"] for x in self.fake.got], ["GET"] * 6)
        for body in ({"Container": "gone"}, {}, b"{nope", None):
            self.fake.got = []
            self.assertEqual(self.ask(_req("POST", "/v1.56/networks/n/disconnect", body))[0], 200)
            self.assertEqual(self.sent("POST", "/v1.56/networks/n/disconnect")["body"],
                             body if isinstance(body, bytes) else b"" if body is None else json.dumps(body).encode())
        code, _, _, answer = self.ask(_req("POST", "/v1.56/networks/n/connect",
                                           b'{"Container":"mine","container":"x"}'))
        self.assertEqual(code, 400)
        self.assertIn('the keys "Container" and "container"', json.loads(answer.decode())["message"])

    def test_retag(self):
        """POST images/SRC/tag and POST commit put repo:tag on an image: a tag no image has, one on an image that
        may go, or one SRC has already goes on; one on a persona image, another persona's, another compose
        project's build, or one a container not this persona's uses is refused, as a removal of it would be. repo
        alone is :latest; a tag replaces repo's; a commit's form body (Go's Form.Get reads it first) is checked too;
        a tag request's body never goes on."""
        ids = ["%02x" % n * 32 for n in range(0x20, 0x30)]

        def img(tag, labels, *aliases):
            self.fake.add("images", {"Id": "sha256:" + ids.pop(), "RepoTags": [tag], "Config": {"Labels": labels}},
                          tag, *aliases)
            return self.fake.objects[-1][2]["Id"]

        src = img("src:1", None, "src:2")
        img("local/agent-claude-docker:latest", {"agent.flavour": "claude"}, "local/agent-claude-docker",
            "docker.io/local/agent-claude-docker")
        img("nxw/pg:1", {"nexus.persona": "p2"})
        img("nxw/pgbuild:1", {"com.docker.compose.project": "p2-app"})
        img("nxw/plain:1", None)
        img("nxw/mine:1", {"nexus.persona": "p1"})
        used = img("nxw/used:1", {})
        self.fake.add("images", FAIL, "nxw/broken:1")
        self.fake.listed = [{"Names": ["/theirs-db"], "ImageID": used, "Labels": {"nexus.persona": "p2"}}]
        q = urllib.parse.quote
        for target, checked in (
                ("/v1.56/images/src:1/tag?repo=nxw/new&tag=1", ["nxw/new:1"]),
                ("/v1.56/images/src:1/tag?repo=nxw/plain&tag=1", ["nxw/plain:1", "src:1", "containers"]),
                ("/v1.56/images/src:1/tag?repo=nxw/plain:1", ["nxw/plain:1", "src:1", "containers"]),
                ("/v1.56/images/src:1/tag?repo=nxw/plain:9&tag=1", ["nxw/plain:1", "src:1", "containers"]),
                ("/v1.56/images/%s/tag?repo=nxw/mine&tag=1" % q("src:1"), ["nxw/mine:1", "src:1", "containers"]),
                ("/v1.56/images/src:1/tag?repo=src&tag=2", ["src:2", "src:1"]),      # it has that tag already
                ("/v1.56/images/src:1/tag?repo=localhost:5000/x", ["localhost:5000/x"]),
                ("/v1.56/commit?container=c&repo=nxw/new2&tag=1", ["nxw/new2:1"]),
                ("/v1.56/commit?container=c", [])):
            self.fake.got = []
            self.assertEqual(self.ask(_req("POST", target))[0], 200, target)
            path = urllib.parse.unquote(target.split("?")[0])
            self.assertEqual(self.sent("POST", path)["target"], target)
            self.assertEqual([r["target"] for r in self.fake.got if r["method"] == "GET"],
                             ["/v1.56/containers/json?all=1" if c == "containers" else "/v1.56/images/%s/json" % c
                              for c in checked], target)
        self.assertEqual(src, "sha256:" + "2f" * 32)
        self.fake.got = []
        for target, words in (
                ("/v1.56/images/src:1/tag?repo=docker.io/local/agent-claude-docker",
                 ("nexus guard: image local/agent-claude-docker:latest isn't labelled nexus.persona=p1 (it is a "
                  "persona image",
                  "tag yours with a name of your own, or ask the user, who can move the tag on the VM")),
                ("/v1.56/images/src:1/tag?repo=nxw/pg&tag=1", ("image nxw/pg:1 isn't", "it is persona p2's")),
                ("/v1.56/images/src:1/tag?tag=1&repo=nxw/pgbuild", ("compose project p2-app",)),
                ("/v1.56/images/src:1/tag?repo=nxw/used&tag=1&repo=x", ("image nxw/used:1 is used by container "
                                                                        "theirs-db", "move the tag")),
                ("/v1.56/images/src:1/tag?repo=nxw/broken&tag=1", ("couldn't look up image nxw/broken:1",)),
                ("/v1.56/commit?container=c&repo=local/agent-claude-docker&tag=latest", ("persona image",)),
                ("/v1.56/commit?container=c&repo=nxw/pg:1", ("it is persona p2's",))):
            self.refused(_req("POST", target), *words)
        self.assertEqual([r["method"] for r in self.fake.got], ["GET"] * len(self.fake.got))
        # A form body: a commit's is checked (the daemon would read repo there first); a tag request's goes nowhere.
        form = (("Host", "x"), ("Content-Type", "application/x-www-form-urlencoded"))
        self.refused(_req("POST", "/v1.56/commit?container=c&repo=nxw/new3", b"repo=nxw/pg&tag=1", headers=form),
                     "image nxw/pg:1 isn't")
        self.fake.got = []
        self.assertEqual(self.ask(_req("POST", "/v1.56/images/src:1/tag?repo=nxw/new4", b"repo=nxw/pg&tag=1",
                                       headers=form))[0], 200)
        r = self.sent("POST", "/v1.56/images/src:1/tag")
        self.assertEqual((r["body"], r["h"]["content-length"]), (b"", "0"))
        self.fake.got = []
        self.ask(_req("POST", "/v1.56/commit?container=c", {"Env": ["A=1"]}))
        self.assertEqual(self.sent("POST", "/v1.56/commit")["body"], b'{"Env": ["A=1"]}')

    def test_daemon_wide_refused(self):
        """Swarm mode and plugins are the daemon's, every persona's: their writes are refused (a plugin's name holds
        "/"s), nothing reaching the daemon; reads go on."""
        for method, target in (("POST", "/v1.56/swarm/init"), ("POST", "/v1.56/swarm/join"),
                               ("POST", "/swarm/leave?force=1"), ("POST", "/v1.56/swarm/update?version=1"),
                               ("POST", "/v1.56/swarm/unlock")):
            self.refused(_req(method, target, {}), "nexus guard: the daemon's swarm mode isn't labelled "
                         "nexus.persona=p1 (it is the VM's", "`docker compose` runs a stack without it", "ask the user")
        for method, target in (("POST", "/v1.56/plugins/pull?remote=vieux/sshfs"),
                               ("POST", "/v1.56/plugins/create?name=x"),
                               ("POST", "/v1.56/plugins/vieux/sshfs:latest/enable"),
                               ("POST", "/v1.56/plugins/vieux/sshfs:latest/disable"),
                               ("POST", "/v1.56/plugins/vieux/sshfs:latest/upgrade?remote=x"),
                               ("POST", "/v1.56/plugins/vieux/sshfs/set"), ("POST", "/v1.56/plugins/x/push"),
                               ("DELETE", "/v1.56/plugins/vieux/sshfs:latest?force=1"),
                               ("DELETE", "/v1.56/plugins/vieux%2Fsshfs")):
            self.refused(_req(method, target, {} if method == "POST" else None), "nexus guard: the daemon's plugins "
                         "aren't labelled nexus.persona=p1 (they are the VM's", "ask the user")
        self.assertEqual(self.fake.got, [])
        for method, target in (("GET", "/v1.56/swarm"), ("GET", "/v1.56/plugins"),
                               ("GET", "/v1.56/plugins/privileges?remote=x"),
                               ("GET", "/v1.56/plugins/vieux/sshfs/json"),
                               ("GET", "/v1.56/swarm/unlockkey"), ("POST", "/v1.56/services/x/update")):
            self.fake.got = []
            self.assertEqual(self.ask(_req(method, target))[0], 200)
            self.assertEqual([(r["method"], r["target"]) for r in self.fake.got], [(method, target)])

    def test_swarm_objects(self):
        """DELETE services/X, secrets/X, configs/X, nodes/X: X inspected, its Spec.Labels read (creates are
        labelled there): this persona's goes on by its ID; another's or no one's is refused; gone goes on; a lookup
        that fails refuses."""
        for n, kind in enumerate(("services", "secrets", "configs", "nodes")):
            mine = "%d%s" % (n, "m" * 24)
            self.fake.add(kind, {"ID": mine, "Spec": {"Name": "mine", "Labels": {"nexus.persona": "p1"}}}, "mine")
            self.fake.add(kind, {"ID": "t" * 25, "Spec": {"Name": "theirs", "Labels": {"nexus.persona": "p2"}}},
                          "theirs")
            self.fake.add(kind, {"ID": "b" * 25, "Spec": {"Name": "bare", "Labels": {}}}, "bare")
            self.fake.add(kind, FAIL, "broken")
            self.fake.got = []
            self.assertEqual(self.ask(_req("DELETE", "/v1.56/%s/mine" % kind))[0], 200)
            self.assertEqual([r["target"] for r in self.fake.got], ["/v1.56/%s/mine" % kind,
                                                                    "/v1.56/%s/%s" % (kind, mine)])
            self.fake.got = []
            self.refused(_req("DELETE", "/v1.56/%s/theirs" % kind),
                         "nexus guard: %s theirs isn't labelled nexus.persona=p1 (it is persona p2's" % kind[:-1])
            self.refused(_req("DELETE", "/v1.56/%s/bare" % kind), "no nexus.persona label")
            self.refused(_req("DELETE", "/v1.56/%s/broken" % kind), "couldn't look up %s broken" % kind[:-1])
            self.assertEqual([r["method"] for r in self.fake.got], ["GET"] * 3)
            self.fake.got = []
            self.assertEqual(self.ask(_req("DELETE", "/v1.56/%s/gone" % kind))[0], 200)
            self.assertEqual([(r["method"], r["target"]) for r in self.fake.got],
                             [("GET", "/v1.56/%s/gone" % kind), ("DELETE", "/v1.56/%s/gone" % kind)])

    def test_refusals_say_whose(self):
        """A refusal says whose the object is, from its labels: another persona's; a persona's own container
        (agent.persona: `nexus X stop` on the VM); one made from a persona image (agent.flavour alone); an anonymous
        volume (`docker rm -v`); no one's. A ref that is an id (compose sends the full one) has the name first."""
        self.fake.add("containers", {"Id": ID_AGENT, "Name": "/agent-lad", "Config": {"Labels": {
            "agent.persona": "lad", "agent.flavour": "claude"}}}, "agent-lad", ID_AGENT)
        self.fake.add("containers", {"Id": ID_OTHER, "Name": "/sib", "Config": {"Labels": {"agent.flavour": "claude"}}},
                      "sib")
        self.fake.add("containers", {"Id": "abc" + ID_BARE[3:], "Name": "/abc", "Config": {}}, "abc")
        self.fake.add("volumes", {"Name": ID_MINE, "Labels": {"com.docker.volume.anonymous": ""}}, ID_MINE)
        self.refused(_req("POST", "/v1.56/containers/agent-lad/stop"), "nexus guard: container agent-lad isn't "
                     "labelled nexus.persona=p1 (it is persona lad's own container, which `nexus lad stop` stops on "
                     "the VM; a persona may stop only what it made: leave it, or ask the user, who can stop it on the "
                     "VM)")
        self.refused(_req("DELETE", "/v1.56/containers/%s?force=1" % ID_AGENT),
                     "container agent-lad (%s) isn't labelled" % ID_AGENT[:12])
        self.refused(_req("POST", "/v1.56/containers/sib/kill"),
                     "container sib isn't labelled nexus.persona=p1 (it was made from a persona image, and not "
                     "through the guard; a persona may kill only")
        self.refused(_req("DELETE", "/v1.56/containers/abc"), "container abc isn't labelled nexus.persona=p1 (it has "
                     "no nexus.persona label: the user's, made before the guard, or made on the raw socket")
        self.refused(_req("DELETE", "/v1.56/volumes/" + ID_MINE),
                     "volume %s isn't labelled nexus.persona=p1 (it is an anonymous volume with no nexus.persona label"
                     % ID_MINE, "`docker rm -v`, `docker run --rm` and `docker compose down -v` remove a container's "
                     "anonymous volumes with it", "ask the user, who can remove it on the VM")
        msg = self.refused(_req("POST", "/v1.56/build/prune"), "the build cache isn't labelled nexus.persona=p1")
        self.assertTrue(msg.endswith("in a `docker system prune` this comes last, and the container, network, volume "
                                     "and image prunes before it went on, narrowed to nexus.persona=p1)"), msg)

    def test_inspect_failure(self):
        self.fake.add("containers", FAIL, "broken")
        self.refused(_req("DELETE", "/v1.56/containers/broken"), "couldn't look up container broken", "boom")
        self.assertEqual([r["method"] for r in self.fake.got], ["GET"])
        self.fake.add("containers", {"Name": "/noid", "Config": {"Labels": {"nexus.persona": "p1"}}}, "noid")
        self.refused(_req("DELETE", "/v1.56/containers/noid"), "couldn't look up container noid", "no Id")
        self.assertEqual([r["method"] for r in self.fake.got], ["GET"] * 2)

    def test_upgrade_on_a_rule_path_is_checked(self):
        """An Upgrade on a rule path doesn't skip the check: refused as any other; let through, it goes on with
        Connection: close and no Upgrade (only one off the rules, to attach, exec start, /grpc, /session, splices)."""
        up = (("Host", "x"), ("Connection", "Upgrade"), ("Upgrade", "tcp"))
        self.fake.add("containers", {"Id": ID_MINE, "Name": "/mine", "Config": {"Labels": {"nexus.persona": "p1"}}},
                      "mine")
        self.fake.add("containers", {"Id": ID_THEIRS, "Name": "/theirs", "Config": {"Labels": {
            "nexus.persona": "p2"}}}, "theirs", "mine/attach")
        for raw in (_req("DELETE", "/v1.56/containers/theirs?force=1", headers=up),
                    _req("POST", "/v1.56/containers/theirs/stop", headers=up),
                    _req("POST", "/v1.56/containers/theirs/kill", headers=(("Upgrade", ""),)),
                    _req("DELETE", "/v1.56/containers/mine/attach", headers=up)):    # a link alias "attach"
            self.refused(raw, "isn't labelled nexus.persona=p1")
        self.assertEqual([r["method"] for r in self.fake.got], ["GET"] * 4)        # inspects only
        self.fake.got = []
        self.assertEqual(self.ask(_req("DELETE", "/v1.56/containers/mine?force=1", headers=up))[0], 200)
        r = self.sent("DELETE", "/v1.56/containers/" + ID_MINE)
        self.assertEqual([v for k, v in r["low"] if k == "connection"], ["close"])
        self.assertNotIn("upgrade", r["h"])
        self.fake.got = []
        self.assertEqual(self.ask(_req("POST", "/v1.56/volumes/create", {"Name": "v"}, headers=up))[0], 201)
        r = self.sent("POST", "/v1.56/volumes/create")
        self.assertEqual((json.loads(r["body"].decode())["Labels"], r["h"]["connection"]),
                         ({"nexus.persona": "p1"}, "close"))
        self.assertNotIn("upgrade", r["h"])


class Wire(_Rig):
    def test_connection_close_replaces_the_clients(self):
        raw = _req("GET", "/v1.56/containers/json?all=1", headers=(
            ("Host", "api.moby.localhost"), ("User-Agent", "Docker-Client/29.9.2 (linux)"),
            ("Connection", "keep-alive"), ("connection", "Upgrade-Insecure"), ("Keep-Alive", "timeout=5")))
        code, _, _, body = self.ask(raw)
        self.assertEqual((code, json.loads(body.decode())), (200, []))
        r = self.sent("GET", "/v1.56/containers/json")
        self.assertEqual((r["target"], r["version"]), ("/v1.56/containers/json?all=1", "HTTP/1.1"))
        self.assertEqual([v for k, v in r["low"] if k == "connection"], ["close"])
        self.assertEqual([h for h in r["headers"] if h[0].lower() != "connection"],
                         [("Host", "api.moby.localhost"), ("User-Agent", "Docker-Client/29.9.2 (linux)"),
                          ("Keep-Alive", "timeout=5")])

    def test_no_host_and_unversioned(self):
        """buildx: /grpc and /session with no Host, no version; a bare ping with no Host either."""
        for target, extra in (("/grpc", ()), ("/session", (("X-Docker-Expose-Session-Uuid", "u1"),
                                                           ("X-Docker-Expose-Session-Grpc-Method", "/a/B"),
                                                           ("X-Docker-Expose-Session-Grpc-Method", "/c/D")))):
            self.fake.got = []
            raw = _req("POST", target, headers=(("Upgrade", "h2c"), ("Connection", "Upgrade")) + extra) \
                .replace(b"\r\n\r\n", b"\r\nContent-Length: 0\r\n\r\n")
            s = self.connect()
            s.sendall(raw)
            s.shutdown(socket.SHUT_WR)
            self.assertTrue(_until_eof(s).startswith(b"HTTP/1.1 101 UPGRADED\r\n"))
            s.close()
            self.assertEqual(self.sent("POST", target)["raw"], raw)
        code, _, _, _ = self.ask(b"GET /_ping HTTP/1.1\r\n\r\n")
        self.assertEqual(code, 200)
        self.assertEqual(self.sent("GET", "/_ping")["headers"], [("Connection", "close")])

    def test_upgrade_passes_through(self):
        """attach, exec start: the head byte for byte, with its body, the 101, then bytes both ways as they come; what
        the client sends before the 101 goes on after it."""
        for raw in (_req("POST", "/v1.56/containers/%s/attach?stderr=1&stdin=1&stdout=1&stream=1" % ID_THEIRS, b"",
                         headers=(("Host", "api.moby.localhost"), ("Connection", "Upgrade"), ("Upgrade", "tcp"))),
                    _req("POST", "/v1.56/exec/%s/start" % ID_MINE, {"Detach": False, "Tty": False},
                         headers=(("Connection", "Upgrade"), ("Upgrade", "tcp")))):
            self.fake.got = []
            s = self.connect()
            try:
                s.sendall(raw + b"early")
                want = b"HTTP/1.1 101 UPGRADED\r\nConnection: Upgrade\r\nUpgrade: tcp\r\n\r\nhello\n"
                self.assertEqual(_exactly(s, len(want)), want)
                self.assertEqual(_exactly(s, 10), b"echo:early")
                for word in (b"ping", b"\x00\x01\xff raw"):
                    s.sendall(word)
                    self.assertEqual(_exactly(s, len(word) + 5), b"echo:" + word)
            finally:
                s.close()
            self.assertEqual(self.fake.got[0]["raw"] + self.fake.got[0]["body"], raw)
            self.assertEqual(self.fake.got[0]["extra"], b"")        # "early" came after the 101
        raw = b"POST /exec/x/start HTTP/1.1\r\ncontent-length:  2\r\nupgrade:tcp\r\nConnection: Upgrade\r\n\r\n{}"
        self.fake.got = []
        s = self.connect()
        try:
            s.sendall(raw)
            self.assertTrue(_exactly(s, 20).startswith(b"HTTP/1.1 101 "))
        finally:
            s.close()
        self.assertEqual(self.fake.got[0]["raw"] + self.fake.got[0]["body"], raw)
        # A chunked body, or an Expect, goes on read and framed by Content-Length, the 100 from the guard.
        self.fake.got = []
        s = self.connect()
        try:
            s.sendall(b"POST /exec/x/start HTTP/1.1\r\nUpgrade: tcp\r\nExpect: 100-continue\r\n"
                      b"Transfer-Encoding: chunked\r\n\r\n")
            self.assertEqual(_exactly(s, 25), b"HTTP/1.1 100 Continue\r\n\r\n")
            s.sendall(b"2\r\n{}\r\n0\r\n\r\n")
            self.assertTrue(_exactly(s, 20).startswith(b"HTTP/1.1 101 "))
        finally:
            s.close()
        self.assertEqual((self.fake.got[0]["headers"], self.fake.got[0]["body"]),
                         ([("Upgrade", "tcp"), ("Content-Length", "2")], b"{}"))

    def test_an_upgrade_the_daemon_doesnt_take_ends_the_connection(self):
        """An exec start of an unknown id: the daemon answers 404 and would keep the connection alive. The client gets
        that answer, then the end: a request sent after it, pipelined or on the reused connection, never reaches the
        daemon."""
        start = _req("POST", "/v1.56/exec/nosuch/start", {"Detach": False, "Tty": False}, headers=(
            ("Host", "x"), ("Connection", "Upgrade"), ("Upgrade", "tcp")))
        delete = _req("DELETE", "/v1.56/containers/%s?force=1" % ID_THEIRS)
        out = self.ask(start + delete)
        self.assertEqual(out[0], 404)
        self.assertEqual(json.loads(out[3].decode()), {"message": "No such exec instance: nosuch"})    # one answer
        self.assertEqual([r["method"] for r in self.fake.got], ["POST"])
        self.assertIn("docker-guard: POST /v1.56/exec/nosuch/start: no upgrade (HTTP/1.1 404 X): the connection "
                      "ends with that answer", self.lines)
        self.fake.got = []
        s = self.connect()                          # a pooled client (dockerode): the answer, then the next request
        try:
            s.sendall(start)
            head = b""
            while b"\r\n\r\n" not in head:
                head += s.recv(1)
            self.assertTrue(head.startswith(b"HTTP/1.1 404 "), head)
            _exactly(s, int(dict(_response(head)[2])["Content-Length"]))
            try:
                s.sendall(delete)
                self.assertEqual(_until_eof(s), b"")
            except ConnectionError:                 # the guard may be done with it already: a reset, a broken pipe
                pass
        finally:
            s.close()
        self.assertEqual(self.fake.find("DELETE", "/v1.56/containers/" + ID_THEIRS), [])
        self.assertEqual([r["method"] for r in self.fake.got], ["POST"])

    def test_upgrade_header_elsewhere_is_dropped(self):
        """An Upgrade off the upgrade routes (the daemon ignores it and would keep the connection alive) is dropped,
        HTTP2-Settings with it, and Connection: close goes on: a request pipelined after it never reaches the
        daemon."""
        delete = _req("DELETE", "/v1.56/containers/%s?force=1" % ID_THEIRS, headers=(("Connection", "close"),))
        for method, target, extra in (
                ("GET", "/v1.56/_ping", (("Upgrade", "anything"),)),
                ("GET", "/_ping", (("Upgrade", ""),)),
                ("GET", "/v1.56/info", (("Connection", "Upgrade, HTTP2-Settings"), ("Upgrade", "h2c"),
                                        ("HTTP2-Settings", "AAMAAABkAARAAAAAAAIAAAAA"))),
                ("GET", "/v1.56/containers/x/attach", (("Connection", "Upgrade"), ("Upgrade", "tcp"))),
                ("GET", "/v1.56/containers/x/attach/ws", (("Connection", "Upgrade"), ("Upgrade", "websocket"))),
                ("POST", "/v1.56/containers/x/start", (("Connection", "Upgrade"), ("Upgrade", "tcp")))):
            self.fake.got = []
            out = self.ask(_req(method, target, headers=(("Host", "x"),) + extra) + delete)
            self.assertEqual((out[0], out[3]), (200, b'{"ok": true}'), target)                # one answer
            self.assertEqual([r["target"] for r in self.fake.got], [target])
            r = self.fake.got[0]
            self.assertEqual([v for k, v in r["low"] if k == "connection"], ["close"])
            self.assertEqual([k for k, v in r["low"] if k in ("upgrade", "http2-settings")], [])

    def test_http2_prior_knowledge_refused(self):
        """PRI * HTTP/2.0 (the daemon would speak HTTP/2 on the connection), or any version but HTTP/1.x: a 505 from
        here, nothing to the daemon."""
        preface = b"PRI * HTTP/2.0\r\n\r\nSM\r\n\r\n" + b"\x00\x00\x00\x04\x00\x00\x00\x00\x00"
        for raw in (preface, b"DELETE /v1.56/containers/x HTTP/2.0\r\nHost: x\r\n\r\n",
                    b"GET /_ping HTTP/3\r\n\r\n", b"PRI /_ping HTTP/1.1\r\n\r\n"):
            code, line, _, body = self.ask(raw)
            self.assertEqual((code, line), (505, "HTTP/1.1 505 HTTP Version Not Supported"), raw)
            self.assertIn("only HTTP/1.x goes through the guard", json.loads(body.decode())["message"])
        self.assertEqual(self.fake.got, [])
        self.assertTrue(self.lines[0].startswith("docker-guard: answered 505 to PRI *: only HTTP/1.x"), self.lines)
        for version in (b"HTTP/1.0", b"HTTP/1.2"):
            self.assertEqual(self.ask(b"GET /_ping " + version + b"\r\n\r\n")[0], 200)

    def test_half_close(self):
        """The client shuts its side for writing and the daemon still answers: an EOF goes on as an EOF."""
        s = self.connect()
        s.sendall(_req("POST", "/v1.56/until-eof", b"abc"))
        s.sendall(b"def")
        s.shutdown(socket.SHUT_WR)
        code, _, _, body = _response(_until_eof(s))
        s.close()
        self.assertEqual((code, json.loads(body.decode())), (200, {"got": "abcdef"}))
        s = self.connect()                          # `docker run -i … < file`: stdin's EOF, then the output
        s.sendall(_req("POST", "/v1.56/containers/x/attach?stdin=1&stream=1", b"",
                       headers=(("Connection", "Upgrade"), ("Upgrade", "tcp"))))
        s.sendall(b"input")
        s.shutdown(socket.SHUT_WR)
        out = _until_eof(s)
        s.close()
        self.assertTrue(out.endswith(b"hello\necho:inputbye\n"), out)

    def test_a_client_that_never_closes(self):
        """The daemon answers and closes; the client reads it all and then never closes: the guard's two threads for
        it end all the same, once it has been quiet for the linger (0.2 s here)."""
        s = self.connect()
        try:
            s.sendall(_req("GET", "/v1.56/containers/json"))
            self.assertEqual(_response(_until_eof(s))[0], 200)
            end = time.monotonic() + 3
            while self.guard.open and time.monotonic() < end:
                time.sleep(0.02)
            self.assertEqual(self.guard.open, 0)
        finally:
            s.close()

    def test_chunked_body_and_expect(self):
        """A chunked create with Expect: 100-continue: the 100 comes from the guard, the body goes on whole with a
        Content-Length. A chunked body the guard doesn't read (docker cp in) goes on chunked."""
        body = json.dumps({"Image": "busybox", "Labels": {"a": "1"}}).encode()
        s = self.connect()
        try:
            s.sendall(b"POST /v1.56/containers/create?name=c HTTP/1.1\r\nHost: x\r\nTransfer-Encoding: chunked\r\n"
                      b"Expect: 100-continue\r\nContent-Type: application/json\r\n\r\n")
            want = b"HTTP/1.1 100 Continue\r\n\r\n"
            self.assertEqual(_exactly(s, len(want)), want)
            s.sendall(b"%x;ext=1\r\n%s\r\n" % (10, body[:10]) + b"%x\r\n%s\r\n" % (len(body) - 10, body[10:]) +
                      b"0\r\nX-Trailer: t\r\n\r\n")
            self.assertEqual(_response(_until_eof(s))[0], 200)
        finally:
            s.close()
        r = self.sent("POST", "/v1.56/containers/create")
        self.assertEqual(r["h"]["content-length"], str(len(r["body"])))
        self.assertNotIn("transfer-encoding", r["h"])
        self.assertNotIn("expect", r["h"])
        self.assertEqual(json.loads(r["body"].decode())["Labels"], {"a": "1", "nexus.persona": "p1"})
        self.fake.got = []
        s = self.connect()                          # Expect with a Content-Length: the 100 comes first all the same
        try:
            s.sendall(b"POST /v1.56/containers/create HTTP/1.1\r\nHost: x\r\nContent-Length: %d\r\n"
                      b"Expect: 100-continue\r\nContent-Type: application/json\r\n\r\n" % len(body))
            want = b"HTTP/1.1 100 Continue\r\n\r\n"
            self.assertEqual(_exactly(s, len(want)), want)
            s.sendall(body)
            self.assertEqual(_response(_until_eof(s))[0], 200)
        finally:
            s.close()
        r = self.sent("POST", "/v1.56/containers/create")
        self.assertNotIn("expect", r["h"])
        self.assertEqual(json.loads(r["body"].decode())["Labels"], {"a": "1", "nexus.persona": "p1"})
        self.fake.got = []
        tar = b"x" * 3000
        self.ask(b"PUT /v1.56/containers/c/archive?path=%2Ftmp HTTP/1.1\r\nHost: x\r\n"
                 b"Transfer-Encoding: chunked\r\n\r\n" + b"%x\r\n%s\r\n0\r\n\r\n" % (len(tar), tar))
        r = self.sent("PUT", "/v1.56/containers/c/archive")
        self.assertEqual((r["h"]["transfer-encoding"], r["h"]["connection"], r["body"]), ("chunked", "close", tar))

    def test_version_prefixes(self):
        """Labelled and checked alike with any version prefix the daemon routes, or none; the guard's own calls carry
        the client's; %-escapes, doubled slashes and the absolute form are routed as the daemon would."""
        self.fake.add("containers", {"Name": "/theirs", "Config": {"Labels": {"nexus.persona": "p2"}}}, "theirs")
        for prefix in ("/v1.56", "/v1", "/v1.50.9", ""):
            self.fake.got = []
            self.ask(_req("POST", prefix + "/volumes/create", {"Name": "v"}))
            self.assertEqual(json.loads(self.sent("POST", prefix + "/volumes/create")["body"].decode())["Labels"],
                             {"nexus.persona": "p1"})
            self.fake.got = []
            self.refused(_req("DELETE", prefix + "/containers/theirs"), "container theirs isn't labelled")
            self.assertEqual([r["target"] for r in self.fake.got], [prefix + "/containers/theirs/json"])
        for target in ("/v1.56/%63ontainers/theirs", "/v1.56//containers//theirs/", "http://x/v1.56/containers/theirs",
                       "/v1.56/containers/../containers/theirs"):
            self.refused(_req("DELETE", target), "container theirs isn't labelled")
        self.fake.got = []
        self.ask(_req("GET", "/version"))
        self.assertEqual(self.fake.got[0]["target"], "/version")

    def test_refusal_shape(self):
        """What the CLI needs to print the message: 403, JSON with "message", Content-Length, Connection: close."""
        self.fake.add("containers", {"Name": "/theirs", "Config": {"Labels": {"nexus.persona": "p2"}}}, "theirs")
        code, line, headers, body = self.ask(_req("DELETE", "/v1.56/containers/theirs?force=1"))
        self.assertEqual(line, "HTTP/1.1 403 Forbidden")
        self.assertEqual(headers, [("Content-Type", "application/json"), ("Content-Length", str(len(body))),
                                   ("Connection", "close")])
        self.assertEqual(json.loads(body.decode()), {"message": (
            "nexus guard: container theirs isn't labelled nexus.persona=p1 (it is persona p2's; a persona may remove "
            "only what it made: leave it, or ask the user, who can remove it on the VM)")})
        self.assertEqual(self.lines[-1], "docker-guard: refused DELETE /v1.56/containers/theirs: container theirs "
                                         "isn't labelled nexus.persona=p1 (it is persona p2's; a persona may remove "
                                         "only what it made: leave it, or ask the user, who can remove it on the VM)")

    def test_oversize(self):
        """A head over 64 KiB, or a body to read over 16 MiB (1000 bytes here), is answered here; none goes on."""
        code, _, _, body = self.ask(b"GET /v1.56/info HTTP/1.1\r\nX-Big: " + b"a" * (70 * 1024))
        self.assertEqual(code, 431)
        self.assertIn("over 64 KiB", json.loads(body.decode())["message"])
        with mock.patch.object(guard, "MAX_BODY", 1000):
            self.assertEqual(self.ask(_req("POST", "/v1.56/containers/create", b"x" * 1001))[0], 413)
            self.assertEqual(self.ask(b"POST /v1.56/volumes/create HTTP/1.1\r\nTransfer-Encoding: chunked\r\n\r\n"
                                      b"3e9\r\n" + b"x" * 1001 + b"\r\n0\r\n\r\n")[0], 413)
            self.assertEqual(self.ask(_req("POST", "/v1.56/containers/c/archive", b"x" * 2000))[0], 200)   # not read
        self.assertEqual([r["target"] for r in self.fake.got], ["/v1.56/containers/c/archive"])

    def test_daemon_unreachable(self):
        """A 502, and the log has the method and path, never the query: a legacy build's buildargs may hold a
        secret."""
        self.start(os.path.join(self.dir, "none.sock"))
        code, _, _, body = self.ask(_req("GET", "/v1.56/containers/json"))
        self.assertEqual(code, 502)
        self.assertIn("can't reach the Docker daemon at", json.loads(body.decode())["message"])
        for target in ("/v1.56/build?buildargs=%7B%22T%22%3A%22s3cret%22%7D&t=x",
                       "http://u:s3cret@h/v1.56/build?buildargs=s3cret"):
            self.assertEqual(self.ask(_req("POST", target))[0], 502)
            self.assertTrue(self.lines[-1].startswith("docker-guard: POST /v1.56/build: can't reach the Docker daemon "
                                                      "at "), self.lines[-1])

    def test_an_answer_drains_the_client(self):
        """A 400 to a client still sending its body (1 MiB): what it sends is read and dropped before the close, so it
        reads the answer and an EOF, not a reset, and its writes don't fail."""
        s = self.connect()
        failed = []

        def write():
            try:
                s.sendall(b"POST /v1.56/containers/create HTTP/1.1\r\nHost: x\r\nContent-Length: 1048576\r\n"
                          b"Content-Length: 7\r\n\r\n" + b"x" * 1048576)
            except OSError as e:
                failed.append(e)

        t = threading.Thread(target=write)
        t.start()
        try:
            code, _, _, body = _response(_until_eof(s))
        finally:
            t.join(10)
            s.close()
        self.assertEqual((code, failed), (400, []))
        self.assertIn("a Content-Length it can't read", json.loads(body.decode())["message"])

    def test_a_burst_of_connections_waits(self):
        """300 connections at once, none accepted yet: each waits in the backlog (Go would give up on EAGAIN)."""
        with open("/proc/sys/net/core/somaxconn") as f:
            if int(f.read()) < 300:
                self.skipTest("net.core.somaxconn caps the backlog below 300")
        path = os.path.join(self.dir, "burst.sock")
        lsock = guard.listen(path)
        self.addCleanup(lsock.close)
        clients = []
        try:
            for _ in range(300):
                c = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                clients.append(c)
                c.setblocking(False)
                c.connect(path)                     # BlockingIOError with the queue full
        finally:
            for c in clients:
                c.close()

    def test_out_of_threads(self):
        """A connection whose thread won't start is closed and logged; the guard serves the next one."""
        real, failed = threading.Thread.start, []

        def start(thread):
            if thread.name == "docker-guard-conn" and not failed:
                failed.append(thread)
                raise RuntimeError("can't start new thread")
            return real(thread)

        with mock.patch.object(threading.Thread, "start", start):
            s = self.connect()
            try:
                self.assertEqual(s.recv(100), b"")
            finally:
                s.close()
            self.assertEqual(self.ask(_req("GET", "/v1.56/version"))[0], 200)
        self.assertEqual(len(failed), 1)
        self.assertIn("docker-guard: can't serve a connection: can't start new thread", self.lines)


class Main(unittest.TestCase):
    def setUp(self):
        self.dir = _tmpdir()
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.env = {k: v for k, v in os.environ.items() if not k.startswith(("AGENT_", "NEXUS_GUARD_"))}
        self.env.update(NEXUS_GUARD_LISTEN=os.path.join(self.dir, "g.sock"),
                        NEXUS_GUARD_UPSTREAM=os.path.join(self.dir, "up.sock"))

    def test_persona_required(self):
        r = subprocess.run([sys.executable, "-B", "-I", GUARD], env=self.env, stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE, timeout=30)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn(b"AGENT_PERSONA isn't set", r.stderr)
        self.assertFalse(os.path.exists(self.env["NEXUS_GUARD_LISTEN"]))

    def start(self):
        """The guard as the entrypoint runs it (debug on), and a connection to it once it listens."""
        env = dict(self.env, AGENT_PERSONA="p1", NEXUS_GUARD_DEBUG="1")
        proc = subprocess.Popen([sys.executable, "-B", "-I", GUARD], env=env, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE)
        self.addCleanup(lambda: proc.poll() is None and (proc.kill(), proc.communicate()))
        path, end = self.env["NEXUS_GUARD_LISTEN"], time.monotonic() + 10
        while time.monotonic() < end:
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            s.settimeout(5)
            try:
                if not os.path.islink(path):
                    s.connect(path)
                    self.addCleanup(s.close)
                    return proc, s
            except OSError:
                pass
            s.close()
            time.sleep(0.02)
        self.fail("the guard didn't listen")

    def test_serves_and_stops_on_sigterm(self):
        """A stale socket in the way is replaced; the new one is the user's alone; SIGTERM: exit 0, socket gone."""
        path = self.env["NEXUS_GUARD_LISTEN"]
        stale = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        stale.bind(path)
        stale.close()                               # its file stays: connecting to it is refused
        fake = Fake(self.env["NEXUS_GUARD_UPSTREAM"])
        self.addCleanup(fake.close)
        proc, s = self.start()
        st = os.stat(path)
        self.assertTrue(stat.S_ISSOCK(st.st_mode))
        self.assertEqual(st.st_mode & 0o077, 0)
        s.sendall(_req("POST", "/v1.56/networks/prune"))
        self.assertEqual(_response(_until_eof(s))[0], 200)
        s.close()
        proc.send_signal(signal.SIGTERM)
        out, err = proc.communicate(timeout=10)
        self.assertEqual(proc.returncode, 0, err)
        self.assertFalse(os.path.exists(path))
        self.assertIn(b'docker-guard: rewrote POST /v1.56/networks/prune: filters {"label":{"nexus.persona=p1":true}}',
                      err)
        self.assertEqual(out, b"")

    def test_a_newer_guards_socket_stays(self):
        """The path is another socket by the time this guard stops (a newer guard's, started over it): it stays."""
        path = self.env["NEXUS_GUARD_LISTEN"]
        proc, s = self.start()
        s.close()
        os.unlink(path)
        newer = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.addCleanup(newer.close)
        newer.bind(path)
        newer.listen(1)
        proc.send_signal(signal.SIGTERM)
        proc.communicate(timeout=10)
        self.assertEqual(proc.returncode, 0)
        self.assertTrue(stat.S_ISSOCK(os.lstat(path).st_mode))

    def test_a_symlink_in_the_way_is_replaced(self):
        """The image ships /run/nexus/docker.sock as a link to the raw socket (what a persona gets with the guard
        off): the guard replaces the link, and the daemon's socket it points to stays."""
        fake = Fake(self.env["NEXUS_GUARD_UPSTREAM"])
        self.addCleanup(fake.close)
        os.symlink(fake.path, self.env["NEXUS_GUARD_LISTEN"])
        proc, s = self.start()
        s.close()
        self.assertTrue(stat.S_ISSOCK(os.lstat(self.env["NEXUS_GUARD_LISTEN"]).st_mode))
        self.assertTrue(stat.S_ISSOCK(os.lstat(fake.path).st_mode))
        proc.send_signal(signal.SIGTERM)
        proc.communicate(timeout=10)
        self.assertEqual(proc.returncode, 0)
        self.assertTrue(os.path.exists(fake.path))


if __name__ == "__main__":
    unittest.main()
