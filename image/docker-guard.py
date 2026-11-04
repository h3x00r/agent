#!/usr/bin/python3 -I
"""The Docker API socket of a persona: /run/nexus/docker.sock (the image's DOCKER_HOST), in front of the VM's daemon
(/var/run/docker.sock). Personas share that daemon, so what one creates is labelled nexus.persona=<persona>, its
prunes reach only what carries that label, and stopping or removing a container, volume or network that doesn't,
mounting another persona's volume, or removing or re-tagging an image that is a persona image, another persona's or
used by a container not the persona's, is refused with a message that says what to do.

Not a wall: the raw socket stays mounted, and a client (a sibling given `-v /var/run/docker.sock:…`, testcontainers'
Ryuk) can reach the daemon without the guard. It stops accidents, `docker rm -f $(docker ps -aq)` or `docker system
prune` run in the wrong place, and its refusals say so.

The entrypoint starts it, and starts it again when it dies. Environment: AGENT_PERSONA (required), NEXUS_GUARD_LISTEN
and NEXUS_GUARD_UPSTREAM (the two socket paths, for the tests), NEXUS_GUARD_DEBUG=1 (log every rewrite and check,
not only the refusals and errors). One line on stderr each. Python 3.11 in the image, but it parses as 3.8; stdlib
only. SIGTERM: it stops listening, removes its socket and exits 0.

Per connection (a thread, plus one for the other direction): the first request's head is read (64 KiB at most; no
Host needed) and checked; then bytes are copied both ways, never parsed, until both sides are done. Only HTTP/1.x
goes through (505 otherwise): the daemon speaks HTTP/2 with prior knowledge too, and one HTTP/2 connection would carry
every request on it past the check. A request goes on with `Connection: close`, so the daemon ends the connection
after its answer and the client's next request comes on a new one, checked in turn. An upgrade, a POST with an
Upgrade header to containers/X/attach, exec/X/start or buildx's /grpc and /session, goes on as it came, with its body
and alone: what the client sends after it goes on once the daemon has answered 101, and any other answer ends the
connection. An Upgrade header anywhere else is dropped. A body the guard changes, or reads to check, is read whole
(16 MiB at most; Content-Length or chunked) and sent on with a Content-Length. The guard's own calls (inspect, the
container list, a volume create) go to the daemon on connections of their own, with the client's version prefix. The
log has a
request's method and path, never its query (a legacy build's buildargs may carry a secret) or body.

The path is matched as the daemon routes it: %-escapes decoded, slashes collapsed, version prefix (/v1.56, /v1)
stripped, an object's name taken whole, "/"s and all (moby routes {name:.*}: a legacy link's alias parent/alias names
the linked container, and a network's name may hold a "/"):
- POST {containers,volumes,networks,services,secrets,configs}/create: Labels gets nexus.persona=<persona>. A body
  the daemon would read otherwise than the guard is answered 400: not UTF-8, a key twice (or two spellings of a field
  the guard reads), more after its JSON, nested too deep. One the daemon can't read either goes on as it came; none,
  or null, is labelled as {} would be.
- containers/create, its volumes. A volume's name is global on the daemon, so a named one (-v name:/x, --mount
  source=name) labelled another persona's is refused, as is --volumes-from a container that is: the two would share
  its data. ddev's shared ones by exact name, unlabelled ones and this persona's go on. Once every check has passed,
  each named volume that isn't there yet is made first, labelled (the daemon would make it unlabelled). The daemon
  makes an anonymous one (-v /path, --mount with no source, an image's VOLUME) unlabelled too: each Mounts volume
  gets VolumeOptions.Labels, and the top-level Volumes paths and the image's (when it is here) that nothing else
  mounts become such Mounts; not with --volumes-from, whose volumes a Mount would hide. volumes/create of a name
  another persona's volume has is refused (the daemon would answer with that volume).
- POST {containers,volumes,networks,images}/prune: label=nexus.persona=<persona> added to the filters.
- POST build/prune: refused. Docker can't label the build cache, ignores a label filter on it, and every persona
  shares it. (`docker builder prune` goes over /grpc, out of the guard's sight.)
- POST swarm/…, plugins/{pull,create}, plugins/X/{enable,disable,upgrade,set,push}, DELETE plugins/X: refused. The
  daemon's swarm mode and plugins are every persona's.
- DELETE containers/X, POST containers/X/{stop,kill,restart,pause,unpause,update,rename}, DELETE volumes/X, DELETE
  networks/X, DELETE {services,secrets,configs,nodes}/X, and POST networks/N/{connect,disconnect} for the container
  its body names: X inspected. Gone (404): on to the daemon, which says so. Labelled this persona's: on, to the Id
  the inspect gave (a volume's name), so the daemon acts on what was checked. One of ddev's shared ones by exact name
  (DDEV): the same for what ddev does to them (stop, kill, restart, remove, connect, disconnect), not pause, unpause,
  update, rename or unlink. Else refused. DELETE containers/P/A?link=1 removes P's link A: P is checked, and the path
  goes on as it came. (`docker attach` to a container not the persona's: the CLI turns Ctrl-C into a kill, refused
  like any other, and stays attached without a word; three Ctrl-C end it.)
- DELETE images/X: X inspected. A tag of an image with more tags is an untag: on. Else the image goes: refused when
  it is labelled another persona's and, unless it is labelled this persona's (a commit of its own container), when it
  is a persona image (label agent.flavour, which an image built FROM one takes too) or a compose project not this
  persona's (<persona>-…; ddev's, ddev-…, go) built it; refused too when a container not labelled this persona's uses
  it. On otherwise, -f included: an image a plain `docker build` or a pull makes has no nexus.persona label (buildx
  goes over gRPC). An id goes on as the image's full Id, a bare name as name:latest.
- POST images/X/tag, POST commit: the repo:tag they put on an image (the query's, and a commit's form body's), when an
  image has it now, may move only off an image that could go (above), or when X is that image. (`docker load`,
  `docker build -t` and a pull move tags out of the guard's sight.)
- Anything else: on, unchanged (lists, inspect, start, exec, logs, build, pull, events, services/X/update…).
A refusal is a 403 with a JSON {"message": "nexus guard: …"}: the CLI prints "Error response from daemon: …".

Pitfalls (each pinned where named):
- buildx sends /grpc and /session with no Host header and no version prefix: neither is needed
  (test_guard.Wire.test_no_host_and_unversioned)
- an upgrade's head goes on as it came, then raw both ways after the 101; what the client sends before the 101
  (`docker run -i` stdin) waits for it (test_guard.Wire.test_upgrade_passes_through)
- a client's Upgrade is no upgrade until the daemon's 101: dockerd keeps the connection alive after one it doesn't
  take (an exec start of an unknown id, an Upgrade on /_ping), and a request sent after it would never be checked:
  such an answer ends the connection, and an Upgrade off the four upgrade routes is dropped, with Connection: close
  (test_guard.Wire.test_an_upgrade_the_daemon_doesnt_take_ends_the_connection,
  test_guard.Wire.test_upgrade_header_elsewhere_is_dropped)
- an Upgrade header on a rule path (DELETE containers/X …) doesn't skip the check: the rule comes first, and the
  request goes on with Connection: close, its Upgrade dropped (test_guard.Policy.test_upgrade_on_a_rule_path_is_checked)
- dockerd serves the whole API over HTTP/2 with prior knowledge (PRI * HTTP/2.0): anything but HTTP/1.x is answered
  505 here, never sent on (test_guard.Wire.test_http2_prior_knowledge_refused)
- `docker run -i` with stdin at EOF shuts its side for writing and still reads: an EOF goes on as shutdown(SHUT_WR),
  never as a close (test_guard.Wire.test_half_close)
- a client that never closes after the daemon's EOF would hold its two threads forever: quiet for LINGER seconds, it
  is cut off (test_guard.Wire.test_a_client_that_never_closes)
- keep-alive would carry a second request past the check: Connection: close on every request but an upgrade,
  replacing the client's (test_guard.Wire.test_connection_close_replaces_the_clients)
- a body the guard reads is re-sent whole with Content-Length: chunked decoded, Expect answered here (100 Continue)
  and dropped (test_guard.Wire.test_chunked_body_and_expect)
- an answer from the guard to a client still sending: what it sends is read and dropped first, or the close would
  reset the connection before the client reads the answer (test_guard.Wire.test_an_answer_drains_the_client)
- the daemon routes /v1.50.9/…, /v1/… and unversioned paths alike (its prefix is [0-9.]+), and %-escapes decoded
  (test_guard.Wire.test_version_prefixes)
- an empty or null filters is no filter at all to the daemon, and a label in the legacy list form mixes with no map
  (test_guard.Policy.test_prune_filters)
- Go matches a struct's JSON keys case-insensitively (ſ as s, the Kelvin sign as k) and keeps the last of a key
  given twice, or merges two objects: the guard would check one and the daemon act on another, so such a create body
  is refused, as is one that isn't UTF-8 (Go reads a bad byte as U+FFFD) or has more after its JSON
  (test_guard.Policy.test_create_labels, test_guard.Policy.test_create_bodies_read_otherwise_are_refused)
- the build cache ignores a label filter (moby builder-next: "TODO: handle label"): build/prune is refused, not
  filtered (test_guard.Policy.test_build_prune_refused)
- the CLI names a container by name, compose by full id, a user by short id: the label and the ddev exception come
  from the inspect, never from the path, and a refusal of an id names it by name (id) too
  (test_guard.Policy.test_ownership, test_guard.Policy.test_ddev_by_exact_name)
- ddev's shared router serves every persona's ddev sites: any persona may do to it what ddev does (stop, restart,
  remove), not pause, update or rename it (test_guard.Policy.test_ddev_by_exact_name)
- swarm mode and plugins are the daemon's, every persona's: refused, a plugin's name with its "/"s
  (test_guard.Policy.test_daemon_wide_refused); a swarm object's labels are in its Spec, its id is "ID"
  (test_guard.Policy.test_swarm_objects)
- a refusal says whose: a persona's own container has agent.persona (`nexus X stop`), one run from a persona image
  agent.flavour alone, an anonymous volume com.docker.volume.anonymous (test_guard.Policy.test_refusals_say_whose)
- a rename between the inspect and the daemon's turn could make the ref another container's name or id prefix: what
  goes on names the inspected Id (test_guard.Policy.test_forwarded_by_id)
- moby routes {name:.*}: a ref with a "/" (a legacy link's alias, a network's name) is one object, checked whole,
  and `docker rm --link P/A` changes P (test_guard.Policy.test_slashed_refs)
- unlabelled images stay removable, -f too, but not one a container not this persona's uses (told by ImageID, not
  the ancestor filter, which matches its children too); an untag goes on; only an image's removal is checked; an id
  goes on as the full Id, a bare name with its :latest (untagged meanwhile, it would be read as an id prefix)
  (test_guard.Policy.test_images)
- an image takes its parent's labels (FROM, a commit takes the container's): a persona image's agent.flavour and
  the persona images' compose project, agents, come with a build FROM one, so only the persona's own label lets
  such an image go; compose labels what it builds with its project (test_guard.Policy.test_images)
- `docker tag` onto a persona image's tag would swap what personas run on at their next start: a tag on an image
  moves only as the image could go; Go's Form.Get reads a form body before the query (test_guard.Policy.test_retag)
- `docker run -v name:/x` makes the volume inside the daemon, unlabelled: made first, labelled, with the container's
  volume driver (test_guard.Policy.test_implicit_volumes)
- a volume's name is global and the daemon answers a create of one that is there with it as it is: mounting,
  --volumes-from or creating one of another persona's would share its data, so it is refused (ddev's shared ones
  aside), before anything is made (test_guard.Policy.test_mounting_another_personas_volume)
- `-v /path` is in the top-level Volumes, not Binds, and an image's VOLUME in neither: the daemon makes those
  unlabelled, so they become labelled Mounts, never on a path something else mounts (a duplicate mount point) nor
  beside --volumes-from, whose volume a Mount would hide (test_guard.Policy.test_anonymous_volumes)
- network connect and disconnect name the container in the body, not the path (test_guard.Policy.test_network_endpoints)
- an inspect that fails (not a 404) refuses: the guard can't tell whose it is (test_guard.Policy.test_inspect_failure)
- a refusal is JSON with "message", or the CLI shows "provided no error-message" (test_guard.Wire.test_refusal_shape)
- the log never has a query: a legacy build's buildargs may hold a secret (test_guard.Wire.test_daemon_unreachable)
- Go dials a unix socket non-blocking: with the queue full, a burst of parallel commands gets "Cannot connect to the
  Docker daemon". The backlog is the daemon's, 4096 (test_guard.Wire.test_a_burst_of_connections_waits)
- out of threads (the container's pids limit), one connection is dropped, not the guard
  (test_guard.Wire.test_out_of_threads)
- a socket left by a guard that died is in the way of bind: removed first; the socket is the image user's alone
  (umask 077) (test_guard.Main.test_serves_and_stops_on_sigterm)
- a guard that stops after a newer one has bound the path leaves the newer one's socket
  (test_guard.Main.test_a_newer_guards_socket_stays)
- the image ships /run/nexus/docker.sock as a link to the raw socket (the guard off): the link goes, never what it
  points to (test_guard.Main.test_a_symlink_in_the_way_is_replaced)
"""
import errno
import http.client
import json
import os
import posixpath
import re
import signal
import socket
import sys
import threading
import time
import urllib.parse

LISTEN = "/run/nexus/docker.sock"
UPSTREAM = "/var/run/docker.sock"
LABEL = "nexus.persona"
FLAVOUR = "agent.flavour"   # the label of a persona image (the Dockerfile's claude and grok stages)
PERSONA = "agent.persona"   # the label of a persona's own container (compose.personas.yml)
PROJECT = "com.docker.compose.project"      # compose's, on what it makes and on the images it builds
ANONYMOUS = "com.docker.volume.anonymous"   # the daemon's, on a volume it made for a mount with no name

MAX_HEAD = 64 * 1024
MAX_BODY = 16 * 1024 * 1024
WAIT = 120.0          # seconds a client may stay quiet before its request head and body are in
LINGER = 10.0         # seconds a client may stay quiet once the daemon is done, before the pair is cut
API_TIMEOUT = 60.0    # the guard's own calls to the daemon
CHUNK = 65536

# ddev's shared ones, by exact name: every ddev project on the daemon uses them, and ddev removes or recreates them
# when it needs to (a new router image, poweroff). They're whoever's started ddev first, so never labelled anyone's.
DDEV = {
    "containers": ("ddev-router", "ddev-ssh-agent"),
    "networks": ("ddev_default",),
    "volumes": ("ddev-global-cache", "ddev-ssh-agent_socket_dir", "ddev-router-traefik"),
}
# What ddev does to them, and so any persona may: never pause, unpause, update, rename or unlink.
DDEV_VERBS = ("stop", "kill", "restart", "remove", "connect", "disconnect")
KIND = {"containers": "container", "volumes": "volume", "networks": "network", "images": "image",
        "services": "service", "secrets": "secret", "configs": "config", "nodes": "node"}
SWARM_KINDS = ("services", "secrets", "configs", "nodes")     # their labels are in Spec

HEAD_END = re.compile(rb"\n\r?\n")
HTTP1 = re.compile(r"^HTTP/1\.[0-9]+$")             # Go takes HTTP/1.2 as 1.x too
SWITCHED = re.compile(rb"^HTTP/1\.[0-9]+ 101[ \r\n]")
VERSION = re.compile(r"^/v[0-9.]+(?=/)")           # moby's router: /v{version:[0-9.]+}
# moby's routes name an object {name:.*}: a name may hold a "/" (a legacy link's alias parent/alias, a network's).
CREATE = re.compile(r"^/(containers|volumes|networks|services|secrets|configs)/create$")
PRUNE = re.compile(r"^/(containers|volumes|networks|images)/prune$")
ACT = re.compile(r"^/containers/(.+)/(stop|kill|restart|pause|unpause|update|rename)$")
ENDPOINT = re.compile(r"^/networks/(.+)/(connect|disconnect)$")
RETAG = re.compile(r"^/images/(.+)/tag$")
ONE = re.compile(r"^/(containers|volumes|networks|services|secrets|configs|nodes)/(.+)$")
IMAGE = re.compile(r"^/images/(.+)$")
SWARM = re.compile(r"^/swarm/.")                                          # init, join, leave, update, unlock
PLUGINS = re.compile(r"^/plugins/(pull|create|.+/(enable|disable|upgrade|set|push))$")     # POST; DELETE: any
UPGRADE = re.compile(r"^/(containers/.+/attach|exec/.+/start|grpc|session)$")     # POST, each, and hijacked
VOLUME_NAME = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]+$")
DIGITS = re.compile(r"^[0-9]+$")
HEX = re.compile(rb"^[0-9a-fA-F]+$")
# Go matches a JSON key to a struct field case-insensitively, folding as unicode.ToUpper(unicode.ToLower(r)): these
# four fold to an ASCII letter as well.
FOLD = {0x17f: "s", 0x212a: "k", 0x130: "i", 0x131: "i"}

REASONS = {400: "Bad Request", 403: "Forbidden", 413: "Payload Too Large", 431: "Request Header Fields Too Large",
           501: "Not Implemented", 502: "Bad Gateway", 505: "HTTP Version Not Supported"}


class Answer(Exception):
    """A request the guard answers itself: code and message."""
    def __init__(self, code, message):
        Exception.__init__(self, message)
        self.code = code


class Gone(Exception):
    """The client went away before its request was in."""


# ---- the request ------------------------------------------------------------------------------------------------

def read_head(sock):
    """The first request head (its blank line included) and what came after it; (None, b"") if the client goes
    before a whole head."""
    buf = b""
    while True:
        m = HEAD_END.search(buf)
        if m:
            if m.end() > MAX_HEAD:
                break
            return buf[:m.end()], buf[m.end():]
        if len(buf) > MAX_HEAD:
            break
        data = sock.recv(CHUNK)
        if not data:
            return None, b""
        buf += data
    raise Answer(431, "the request head is over %d KiB" % (MAX_HEAD // 1024))


def parse_head(head):
    """-> (method, target, version, [[name, value], …]), the lines as they came (CRLF or LF, folded lines joined)."""
    lines = [l[:-1] if l.endswith("\r") else l for l in head.decode("latin-1").split("\n")]
    parts = lines[0].split(" ")
    if len(parts) != 3 or not parts[0] or not parts[1]:
        raise Answer(400, "a request line it can't read")
    headers = []
    for line in lines[1:]:
        if not line:
            continue
        if line[0] in " \t" and headers:
            headers[-1][1] += " " + line.strip()
            continue
        name, sep, value = line.partition(":")
        if not sep or not name.strip():
            raise Answer(400, "a header line it can't read")
        headers.append([name.strip(), value.strip()])
    return parts[0], parts[1], parts[2], headers


def values(headers, name):
    return [v for n, v in headers if n.lower() == name]


def without(headers, *names):
    return [h for h in headers if h[0].lower() not in names]


def render_head(method, target, version, headers):
    lines = ["%s %s %s" % (method, target, version)] + ["%s: %s" % (n, v) for n, v in headers]
    return ("\r\n".join(lines) + "\r\n\r\n").encode("latin-1")


def logged(target):
    """A request target as the log has it: its path alone. A query may hold a secret (a legacy build's buildargs),
    an absolute form a user and password."""
    if target.startswith("/"):
        return target.partition("?")[0]
    try:
        return urllib.parse.urlsplit(target).path or "/"
    except ValueError:
        return "?"


def route(target):
    """-> (raw path, query, version prefix, path as the daemon routes it)."""
    if target.startswith("/"):
        raw, _, query = target.partition("?")
    else:                                       # absolute form: the daemon routes its path all the same
        try:
            u = urllib.parse.urlsplit(target)
        except ValueError:
            raise Answer(400, "a request target it can't read")
        raw, query = u.path or "/", u.query
    path = urllib.parse.unquote(raw)
    if path.startswith("/"):
        path = posixpath.normpath(re.sub(r"/+", "/", path))
    m = VERSION.match(path)
    if m:
        return raw, query, m.group(0), path[m.end():]
    return raw, query, "", path


def fill(sock, buf, n):
    while len(buf) < n:
        data = sock.recv(CHUNK)
        if not data:
            raise Gone()
        buf += data
    return buf


def read_line(sock, buf):
    while b"\n" not in buf:
        if len(buf) > 4096:
            raise Answer(400, "a chunked body it can't read")
        data = sock.recv(CHUNK)
        if not data:
            raise Gone()
        buf += data
    line, _, buf = buf.partition(b"\n")
    return (line[:-1] if line.endswith(b"\r") else line), buf


def read_body(sock, headers, buf):
    """The request body as its headers frame it (chunked, Content-Length, or none), with what is in buf already
    -> (body, what came after it)."""
    buf = bytearray(buf)
    te = values(headers, "transfer-encoding")
    if te:
        if [t.strip().lower() for t in te] != ["chunked"]:
            raise Answer(501, "a Transfer-Encoding other than chunked")
        body = bytearray()
        while True:
            line, buf = read_line(sock, buf)
            size = line.split(b";", 1)[0].strip()
            if not HEX.match(size):
                raise Answer(400, "a chunked body it can't read")
            n = int(size, 16)
            if n == 0:
                while True:                     # trailers, up to the blank line
                    line, buf = read_line(sock, buf)
                    if not line:
                        return bytes(body), bytes(buf)
            if len(body) + n > MAX_BODY:
                raise Answer(413, "the request body is over %d MiB" % (MAX_BODY // 1024 // 1024))
            buf = fill(sock, buf, n + 2)
            if buf[n:n + 2] != b"\r\n":
                raise Answer(400, "a chunked body it can't read")
            body += buf[:n]
            buf = buf[n + 2:]
    cl = set(v.strip() for v in values(headers, "content-length"))
    if not cl:
        return b"", bytes(buf)
    n = cl.pop()
    if cl or not DIGITS.match(n):
        raise Answer(400, "a Content-Length it can't read")
    n = int(n)
    if n > MAX_BODY:
        raise Answer(413, "the request body is over %d MiB" % (MAX_BODY // 1024 // 1024))
    buf = fill(sock, buf, n)
    return bytes(buf[:n]), bytes(buf[n:])


# ---- the policy, on what was asked -----------------------------------------------------------------------------

def match(method, path):
    """The rule a request falls under: (rule, …), or None for one that goes on unchanged."""
    method = method.upper()
    if method == "POST":
        if path == "/build/prune":
            return ("build-prune",)
        if path == "/commit":
            return ("retag", None)
        if SWARM.match(path):
            return ("daemon", "swarm")
        if PLUGINS.match(path):
            return ("daemon", "plugins")
        m = CREATE.match(path)
        if m:
            return ("create", m.group(1))
        m = PRUNE.match(path)
        if m:
            return ("prune", m.group(1))
        m = ACT.match(path)
        if m:
            return ("own", "containers", m.group(1), m.group(2))
        m = ENDPOINT.match(path)
        if m:
            return ("endpoint", m.group(1), m.group(2))
        m = RETAG.match(path)
        if m:
            return ("retag", m.group(1))
    elif method == "DELETE":
        if path.startswith("/plugins/"):
            return ("daemon", "plugins")
        m = ONE.match(path)
        if m:
            return ("own", m.group(1), m.group(2), "remove")
        m = IMAGE.match(path)
        if m:
            return ("image", m.group(1))
    return None


def fold(key):
    """A JSON key as Go matches it to a struct field (Labels, labels, LABELS are one)."""
    return key.translate(FOLD).lower()


def get(obj, key):
    """obj[key] as Go's JSON decoding finds it in a struct: keys match as fold() has them. A body with two spellings
    of a key the guard reads is refused (twice()), so there is one at most."""
    found = None
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(k, str) and fold(k) == fold(key):
                found = v
    return found


def _once(pairs):
    """object_pairs_hook: a JSON object, or Answer(400) for a key it has twice."""
    seen = set()
    for k, _ in pairs:
        if k in seen:
            raise Answer(400, "a request body with the key %s twice (the daemon would keep one, or merge the two, "
                         "where the guard checked another: give each key once)" % json.dumps(k))
        seen.add(k)
    return dict(pairs)


NOJSON = object()          # decode(): a body the daemon can't read either


def decode(body):
    """A JSON request body as the daemon reads it: -> the value; None for no body or null (the daemon decodes nothing,
    and makes what an empty request makes); NOJSON for one the daemon can't read either. Answer(400) for one it would
    read otherwise than the guard: not UTF-8 (Go reads each bad byte as U+FFFD), a key twice at any depth (Go keeps
    the last, or merges two objects), more after the value (moby reads the first value, and takes a } or ] after it
    for the end), nested deeper than Python's decoder goes."""
    if not body:
        return None
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError:
        raise Answer(400, "a request body that isn't UTF-8 (the daemon would read the bytes otherwise than the guard: "
                     "send UTF-8 JSON)")
    start = len(text) - len(text.lstrip(" \t\r\n"))
    try:
        value, end = json.JSONDecoder(object_pairs_hook=_once).raw_decode(text, start)
    except ValueError:
        return NOJSON
    except RecursionError:              # Go reads 10000 levels deep: one it can, the guard can't
        raise Answer(400, "a request body nested deeper than the guard reads")
    if text[end:].strip(" \t\r\n"):
        raise Answer(400, "a request body with more after its JSON (the daemon may read only the first value: send "
                     "one)")
    return value


def twice(spec, kind):
    """The first key of a create body that a Go struct the guard reads has twice in two spellings (Go takes both for
    one field: the last one wins, or two objects merge), or None. Map keys (Labels' own) may differ in case alone."""
    levels = [spec]
    if kind == "containers":
        host = get(spec, "HostConfig")
        levels.append(host)
        mounts = get(host, "Mounts")
        for mount in mounts if isinstance(mounts, list) else []:
            opts = get(mount, "VolumeOptions")
            levels += [mount, opts, get(opts, "DriverConfig")]
    for obj in levels:
        seen = {}
        for k in obj if isinstance(obj, dict) else ():
            if fold(k) in seen:
                return "%s and %s" % (json.dumps(seen[fold(k)]), json.dumps(k))
            seen[fold(k)] = k
    return None


def link_asked(query):
    """A container removal's ?link= as moby reads it (httputils.BoolValue): the first one; "", 0, no, false, none
    are no."""
    for k, v in urllib.parse.parse_qsl(query, keep_blank_values=True):
        if k == "link":
            return v.strip().lower() not in ("", "0", "no", "false", "none")
    return False


def label(obj, persona):
    """obj with Labels[nexus.persona] = persona, the one spelling of Labels it has made Labels; False, obj untouched,
    when Labels isn't a map (the daemon refuses that itself)."""
    keys = [k for k in obj if isinstance(k, str) and fold(k) == "labels"]
    if any(obj[k] is not None and not isinstance(obj[k], dict) for k in keys):
        return False
    merged = {}
    for k in keys:
        merged.update(obj.pop(k) or {})
    merged[LABEL] = persona
    obj["Labels"] = merged
    return True


def implicit_volumes(spec):
    """The named volumes a containers/create body makes the daemon create when they don't exist:
    [(name, driver, driver options, labels)], each name once."""
    host = get(spec, "HostConfig")
    found, seen = [], set()
    driver = get(host, "VolumeDriver")
    for bind in get(host, "Binds") or []:
        src = bind.split(":")[0] if isinstance(bind, str) and ":" in bind else ""
        if VOLUME_NAME.match(src):
            found.append((src, driver if isinstance(driver, str) else "", {}, {}))
    for mount in get(host, "Mounts") or []:
        src = get(mount, "Source")
        if get(mount, "Type") != "volume" or not isinstance(src, str) or not VOLUME_NAME.match(src):
            continue
        opts = get(mount, "VolumeOptions")
        drv = get(opts, "DriverConfig")
        name, dopts, labels = get(drv, "Name"), get(drv, "Options"), get(opts, "Labels")
        found.append((src, name if isinstance(name, str) else "", dopts if isinstance(dopts, dict) else {},
                      labels if isinstance(labels, dict) else {}))
    out = []
    for v in found:
        if v[0] not in seen:
            seen.add(v[0])
            out.append(v)
    return out


def prune_query(query, persona):
    """The query with label=nexus.persona=<persona> in its filters; None when its filters can't be read (the daemon
    might take them for none)."""
    pairs = urllib.parse.parse_qsl(query, keep_blank_values=True)
    given = [v for k, v in pairs if k == "filters"]
    text = given[0].strip() if given else ""
    try:
        filters = json.loads(text) if text else None
    except ValueError:
        return None
    if filters is None:
        filters = {}
    if not isinstance(filters, dict):
        return None
    want = "%s=%s" % (LABEL, persona)
    have = filters.get("label")
    if have is None:
        # The daemon reads the map form or the legacy list form, not the two mixed.
        legacy = any(isinstance(v, list) for v in filters.values())
        filters["label"] = [want] if legacy else {want: True}
    elif isinstance(have, dict):
        have[want] = True
    elif isinstance(have, list):
        have.append(want)
    else:
        return None
    pairs = [(k, v) for k, v in pairs if k != "filters"] + [("filters", json.dumps(filters, separators=(",", ":")))]
    return urllib.parse.urlencode(pairs)


def norm_ref(ref):
    """An image reference as RepoTags spells it: docker.io/library/x → x, no tag → :latest."""
    if "@" in ref:
        return ref
    name, tag = ref, "latest"
    if ":" in ref.rsplit("/", 1)[-1]:
        name, tag = ref.rsplit(":", 1)
    for p in ("docker.io/", "index.docker.io/"):
        if name.startswith(p):
            name = name[len(p):]
    if name.startswith("library/") and name.count("/") == 1:
        name = name[len("library/"):]
    return name + ":" + tag


def labels_of(kind, obj):
    if kind in ("containers", "images"):
        labels = get(get(obj, "Config"), "Labels")
    elif kind in SWARM_KINDS:
        labels = get(get(obj, "Spec"), "Labels")
    else:
        labels = get(obj, "Labels")
    return labels if isinstance(labels, dict) else {}


def name_of(kind, obj):
    """An inspected object's name ("" for none): a container's without its leading "/"."""
    name = get(get(obj, "Spec"), "Name") if kind in SWARM_KINDS else get(obj, "Name")
    return name.lstrip("/") if isinstance(name, str) else ""


def shown(kind, ref, obj):
    """ref as a refusal names it: a ref that is an id (compose sends the full one) with the object's name first."""
    name, ident = name_of(kind, obj), get(obj, "Id")
    if name and name != ref and isinstance(ident, str) and re.match(r"[0-9a-fA-F]+\Z", ref) and \
            ident.lower().startswith(ref.lower()):
        return "%s (%s)" % (name, ref[:12])
    return ref


def not_ours(what, persona, whose, verb):
    return "%s isn't labelled %s=%s (%s; a persona may %s only what it made: leave it, or ask the user, who can %s " \
           "it on the VM)" % (what, LABEL, persona, whose, verb, verb)


def whose(kind, labels):
    """Whose an object is, by its labels, for a refusal."""
    owner = labels.get(LABEL)
    if owner:
        return "it is persona %s's" % owner
    if kind == "containers" and labels.get(PERSONA):
        return "it is persona %s's own container, which `nexus %s stop` stops on the VM" % (
            labels[PERSONA], labels[PERSONA])
    if kind == "containers" and FLAVOUR in labels:
        return "it was made from a persona image, and not through the guard"
    if kind == "volumes" and ANONYMOUS in labels:
        return "it is an anonymous volume with no %s label, made before the guard labelled those, on the raw " \
               "socket, or by the user; `docker rm -v`, `docker run --rm` and `docker compose down -v` remove a " \
               "container's anonymous volumes with it" % LABEL
    return "it has no %s label: the user's, made before the guard, or made on the raw socket" % LABEL


def put(obj, key, value):
    """obj[key] = value, under the spelling of key obj has (Go reads any as the field), else key's."""
    for k in obj:
        if isinstance(k, str) and fold(k) == fold(key):
            obj[k] = value
            return value
    obj[key] = value
    return value


def target(path):
    """A container path as the daemon compares two mounts' targets: "//a/b/" is "/a/b"."""
    return posixpath.normpath(re.sub(r"/+", "/", path))


def volumes_from(host):
    """The containers HostConfig.VolumesFrom names ("name[:ro|:rw]", as moby splits it)."""
    given = get(host, "VolumesFrom")
    return [v.partition(":")[0] for v in given if isinstance(v, str) and v.partition(":")[0]] \
        if isinstance(given, list) else []


def form_first(text, key):
    """The first value of key in a query (or a form body), as Go's Form.Get reads it; None for none."""
    for k, v in urllib.parse.parse_qsl(text, keep_blank_values=True):
        if k == key:
            return v
    return None


def tag_target(repo, tag):
    """The repo:tag a POST images/X/tag or commit makes, as moby's RepoTagReference does: tag replaces repo's;
    "" for none."""
    if not repo:
        return ""
    if tag:
        if ":" in repo.rsplit("/", 1)[-1]:
            repo = repo.rsplit(":", 1)[0]
        return repo + ":" + tag
    return repo


class Upstream:
    """The daemon, for the guard's own calls."""
    def __init__(self, path):
        self.path = path

    def call(self, method, path, body=None):
        """-> (status, decoded JSON or None). OSError and http.client errors go to the caller."""
        conn = _UnixHTTP(self.path)
        try:
            data, headers = None, {}
            if body is not None:
                data = json.dumps(body).encode()
                headers["Content-Type"] = "application/json"
            conn.request(method, path, body=data, headers=headers)
            r = conn.getresponse()
            raw = r.read()
            try:
                obj = json.loads(raw.decode("utf-8")) if raw.strip() else None
            except ValueError:
                obj = None
            return r.status, obj
        finally:
            conn.close()


class _UnixHTTP(http.client.HTTPConnection):
    def __init__(self, path):
        http.client.HTTPConnection.__init__(self, "localhost", timeout=API_TIMEOUT)
        self.unix_path = path

    def connect(self):
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            s.settimeout(self.timeout)
            s.connect(self.unix_path)
        except OSError:
            s.close()
            raise
        self.sock = s


def quote(ref):
    return urllib.parse.quote(ref, safe="/:@")


# ---- the guard --------------------------------------------------------------------------------------------------

class Guard:
    def __init__(self, persona, upstream, debug=False, log=None, linger=LINGER):
        self.persona, self.upstream, self.debug, self.linger = persona, upstream, debug, linger
        self.api = Upstream(upstream)
        self._log = log
        self._lock = threading.Lock()
        self._stopping = False
        self.open = 0           # connections being served, for the tests

    def log(self, line):
        line = "docker-guard: " + line
        if self._log:
            self._log(line)
            return
        with self._lock:
            sys.stderr.write(line + "\n")
            sys.stderr.flush()

    def note(self, line):
        if self.debug:
            self.log(line)

    # -- serving

    def serve(self, listener):
        """Accept until stop() (or an exception in the main thread: SIGTERM)."""
        while not self._stopping:
            try:
                conn, _ = listener.accept()
            except OSError as e:
                if self._stopping or e.errno in (errno.EBADF, errno.EINVAL):
                    return
                self.log("accept: %s" % e)
                time.sleep(0.1)         # out of fds, say: don't spin
                continue
            try:
                threading.Thread(target=self.handle, args=(conn,), name="docker-guard-conn", daemon=True).start()
            except RuntimeError as e:   # out of threads (the container's pids limit): this one goes, the guard stays
                self.log("can't serve a connection: %s" % e)
                conn.close()
                time.sleep(0.1)

    def stop(self, listener):
        self._stopping = True
        try:
            listener.shutdown(socket.SHUT_RDWR)     # wakes accept() (EINVAL)
        except OSError:
            pass

    def handle(self, client):
        with self._lock:
            self.open += 1
        try:
            self._handle(client)
        except Gone:
            pass
        except Exception as e:      # one connection's trouble never ends the guard
            self.log("error: %s: %s" % (type(e).__name__, e))
        finally:
            client.close()
            with self._lock:
                self.open -= 1

    def _handle(self, client):
        """Check the first request and send it on, then splice."""
        client.settimeout(WAIT)
        asked = "a request"
        try:
            try:
                head, rest = read_head(client)
            except socket.timeout:
                return
            if head is None:
                return
            method, target, version, headers = parse_head(head)
            asked = "%s %s" % (method, logged(target))
            if method == "PRI" or not HTTP1.match(version):
                raise Answer(505, "only HTTP/1.x goes through the guard (the daemon speaks HTTP/2 too, and an HTTP/2 "
                             "connection would carry every request on it past the check): use HTTP/1.1")
            raw, query, prefix, path = route(target)
            rule = match(method, path)
            upgrade = None                  # what comes after an upgrade request, held back until the daemon's 101
            if rule:
                body, rest = self.read_body(client, headers, rest)
                where, query, new = self.check(rule, asked, prefix, query, body)
                keep = without(headers, "connection", "content-length", "transfer-encoding", "expect", "upgrade",
                               "http2-settings")
                if new and not body and not values(headers, "content-type"):
                    keep.append(["Content-Type", "application/json"])   # the daemon wants it with a body
                keep += [["Content-Length", str(len(new))], ["Connection", "close"]]
                out = render_head(method, (where or raw) + ("?" + query if query else ""), version, keep) + new + rest
            elif values(headers, "upgrade") and method == "POST" and UPGRADE.match(path):
                body, upgrade = self.read_body(client, headers, rest)
                if values(headers, "transfer-encoding") or values(headers, "expect"):
                    keep = without(headers, "content-length", "transfer-encoding", "expect")
                    head = render_head(method, target, version, keep + [["Content-Length", str(len(body))]])
                out = head + body
            else:
                keep = without(headers, "connection", "upgrade", "http2-settings") + [["Connection", "close"]]
                out = render_head(method, target, version, keep) + rest
        except Answer as a:
            self.log("%s %s: %s" % ("refused" if a.code == 403 else "answered %d to" % a.code, asked, a))
            self.answer(client, a.code, str(a))
            return
        except socket.timeout:
            return
        up = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            try:
                up.connect(self.upstream)
            except OSError as e:
                msg = "can't reach the Docker daemon at %s (%s)" % (self.upstream, e.strerror or e)
                self.log("%s: %s" % (asked, msg))
                self.answer(client, 502, msg)
                return
            client.settimeout(None)
            try:
                up.sendall(out)
            except OSError:
                return
            if upgrade is not None and not self.upgraded(client, up, asked, upgrade):
                return
            self.splice(client, up)
        finally:
            up.close()

    def read_body(self, client, headers, rest):
        """The request's body, read whole, and what came after it; a client that waits for a 100 Continue to send the
        body gets it from here (the Expect doesn't go on)."""
        framed = values(headers, "transfer-encoding") or values(headers, "content-length") not in ([], ["0"])
        if values(headers, "expect") and framed and not rest:
            client.sendall(b"HTTP/1.1 100 Continue\r\n\r\n")
        return read_body(client, headers, rest)

    def upgraded(self, client, up, what, more):
        """An upgrade request is on its way, alone: its answer goes to the client. A 101: what the client sent after
        the request goes on, then the splice (True). Anything else: the daemon is shut for writing, so it ends the
        connection after its answer, and nothing more of the client's goes on (False). The daemon keeps a connection
        alive after an upgrade it didn't take (an exec start of an unknown id): a second request on it would never be
        checked."""
        up.settimeout(WAIT)
        try:
            head, rest = read_head(up)
            if head is not None:
                client.sendall(head + rest)
        except (OSError, Answer) as e:
            self.log("%s: the daemon's answer to the upgrade: %s" % (what, e))
            return False
        if head is None:
            return False
        if SWITCHED.match(head):
            up.settimeout(None)
            try:
                up.sendall(more)
            except OSError:
                return False
            return True
        self.note("%s: no upgrade (%s): the connection ends with that answer" % (
            what, head.split(b"\n", 1)[0].strip().decode("latin-1")))
        shut(up, socket.SHUT_WR)
        pump(up, client, [0])
        shut(client, socket.SHUT_WR)
        drain(client)
        return False

    def check(self, rule, what, prefix, query, body):
        """The policy for a request that falls under a rule (what: "METHOD path", for the log): -> (path to send it
        to, None for its own; query; body) to send on; Answer(403) to refuse."""
        if rule[0] == "build-prune":
            raise Answer(403, "the build cache isn't labelled %s=%s (Docker can't label it and ignores a label filter "
                         "on it, so a prune would empty every persona's: leave it, or ask the user, who can prune it "
                         "on the VM; in a `docker system prune` this comes last, and the container, network, volume "
                         "and image prunes before it went on, narrowed to %s=%s)" % (
                             LABEL, self.persona, LABEL, self.persona))
        if rule[0] == "daemon":
            if rule[1] == "swarm":
                raise Answer(403, "the daemon's swarm mode isn't labelled %s=%s (it is the VM's, every persona's: a "
                             "persona may not change it; `docker compose` runs a stack without it: leave it, or ask "
                             "the user, who can change it on the VM)" % (LABEL, self.persona))
            raise Answer(403, "the daemon's plugins aren't labelled %s=%s (they are the VM's, every persona's: a "
                         "persona may not install, change or remove one: leave them, or ask the user, who can on "
                         "the VM)" % (LABEL, self.persona))
        if rule[0] == "prune":
            new = prune_query(query, self.persona)
            if new is None:
                raise Answer(403, "a prune whose filters it can't read isn't labelled %s=%s (the guard adds that label "
                             "to a prune's filters: give them as JSON, or none)" % (LABEL, self.persona))
            self.note("rewrote %s: filters %s" % (what, urllib.parse.parse_qs(new)["filters"][0]))
            return None, new, b""       # a prune has no body: none goes on
        if rule[0] == "create":
            return None, query, self.create(rule[1], what, prefix, body)
        if rule[0] == "own":
            return self.own(rule[1], rule[2], rule[3], what, prefix, query), query, body
        if rule[0] == "endpoint":
            return None, query, self.endpoint(rule[2], what, prefix, body)
        if rule[0] == "image":
            return self.image(rule[1], what, prefix), query, body
        if rule[0] == "retag":
            # The daemon reads repo and tag with Go's Form.Get: a form body's first, then the query's. A tag request
            # has no body: none goes on. A commit's (a container config) does, and both are checked.
            self.retag(rule[1], [query] if rule[1] is not None else [body.decode("latin-1"), query], what, prefix)
            return None, query, body if rule[1] is None else b""
        return None, query, body

    def create(self, kind, what, prefix, body):
        spec = decode(body)
        if spec is None:
            spec = {}                   # no body, or null: what the daemon makes of {}, labelled
        if isinstance(spec, dict):
            both = twice(spec, kind)
            if both:
                raise Answer(400, "a request body with the keys %s (one field to the daemon, which would keep the "
                             "last or merge the two where the guard checked the other: give it once)" % both)
        if not isinstance(spec, dict) or not label(spec, self.persona):
            self.note("%s: a body it can't read, on as it came" % what)
            return body
        made, anonymous = [], []
        if kind == "volumes":
            name = get(spec, "Name")
            obj = self.inspect("volumes", name, prefix, what, quiet=True) if isinstance(name, str) and name else None
            if obj is not None:
                self.mountable("volumes", name, obj, what)
                self.note("%s: volume %s is there already: the daemon answers with it as it is" % (what, name))
                return json.dumps(spec).encode()
        if kind == "containers":
            made, anonymous = self.volumes(spec, what, prefix)
        self.note("rewrote %s: Labels %s=%s%s%s" % (
            what, LABEL, self.persona, "; volumes made, labelled: " + ", ".join(made) if made else "",
            "; anonymous volumes labelled: " + ", ".join(anonymous) if anonymous else ""))
        return json.dumps(spec).encode()

    def volumes(self, spec, what, prefix):
        """The volumes of a containers/create, checked and labelled: another persona's named volume, or
        --volumes-from another persona's container, refused (ddev's shared ones by exact name, unlabelled ones and
        this persona's go on); each Mounts volume given the label for when the daemon makes it, and the anonymous
        volumes the daemon would make for the Volumes paths and the image's (when it is here) made such Mounts; then
        the named volumes that aren't there yet made first, labelled. Nothing is made before every check passed.
        -> (the volumes made, the anonymous paths labelled)."""
        missing = []
        for v in implicit_volumes(spec):
            obj = self.inspect("volumes", v[0], prefix, what, quiet=True)
            if obj is None:
                missing.append(v)
            else:
                self.mountable("volumes", v[0], obj, what)
        host = get(spec, "HostConfig")
        for ref in volumes_from(host):
            obj = self.inspect("containers", ref.lstrip("/"), prefix, what)
            if obj is not None:
                self.mountable("containers", ref, obj, what)
        anonymous = self.anonymous(spec, what, prefix)
        made = [v[0] for v in missing if self.make_volume(v[0], v[1], v[2], v[3], prefix, what)]
        return made, anonymous

    def mountable(self, kind, ref, obj, what):
        """Refuse a volume (or --volumes-from a container) labelled another persona's: names are global on the
        daemon, and a container would share its data. ddev's shared ones by exact name go on."""
        owner = labels_of(kind, obj).get(LABEL)
        if not owner or owner == self.persona:
            return
        if name_of(kind, obj) in DDEV.get(kind, ()):
            self.note("%s: ddev's shared %s %s" % (what, KIND[kind], name_of(kind, obj)))
            return
        if kind == "volumes":
            raise Answer(403, "volume %s isn't labelled %s=%s (it is persona %s's, and a volume's name is global on "
                         "the daemon: one of yours by that name would be %s's, data and all: name yours otherwise, "
                         "e.g. %s-%s, or ask the user)" % (ref, LABEL, self.persona, owner, owner, self.persona, ref))
        raise Answer(403, "container %s isn't labelled %s=%s (it is persona %s's: --volumes-from would share its "
                     "volumes, data and all: use a container of your own, or ask the user)" % (
                         shown(kind, ref, obj), LABEL, self.persona, owner))

    def anonymous(self, spec, what, prefix):
        """Label the anonymous volumes a containers/create makes (the daemon makes them unlabelled): each Mounts
        volume gets VolumeOptions.Labels (the daemon uses them when it makes the volume); each path of the top-level
        Volumes (`-v /path`) and of the image's VOLUMEs (when the image is here) that nothing else mounts becomes such
        a Mount. Not with VolumesFrom: a Mount would hide the volume it brings. -> the paths added."""
        host = get(spec, "HostConfig")
        given = host is not None
        host = host if given else {}
        mounts = get(host, "Mounts") if isinstance(host, dict) else None
        if not isinstance(host, dict) or not isinstance(mounts, (list, type(None))):
            return []                   # the daemon refuses it itself
        for m in mounts or []:
            if isinstance(m, dict) and get(m, "Type") == "volume":
                opts = get(m, "VolumeOptions")
                if opts is None:
                    opts = put(m, "VolumeOptions", {})
                if isinstance(opts, dict):
                    label(opts, self.persona)
        if volumes_from(host):
            return []
        taken = set()
        for b in get(host, "Binds") or []:
            if isinstance(b, str):
                parts = b.split(":")
                taken.add(target(parts[1] if len(parts) > 1 else parts[0]))
        for m in mounts or []:
            t = get(m, "Target")
            if isinstance(t, str):
                taken.add(target(t))
        tmpfs = get(host, "Tmpfs")
        if isinstance(tmpfs, dict):
            taken.update(target(t) for t in tmpfs if isinstance(t, str))
        paths = get(spec, "Volumes")
        paths = [p for p in paths] if isinstance(paths, dict) else []
        paths += self.image_volumes(get(spec, "Image"), what, prefix)
        added = []
        for p in paths:
            if not isinstance(p, str) or not p.startswith("/") or target(p) in taken:
                continue
            taken.add(target(p))
            if mounts is None:
                mounts = put(host, "Mounts", [])
            if not given:
                put(spec, "HostConfig", host)
                given = True
            mounts.append({"Type": "volume", "Target": p, "VolumeOptions": {"Labels": {LABEL: self.persona}}})
            added.append(p)
        return added

    def image_volumes(self, image, what, prefix):
        """The image's VOLUME paths; none when it isn't here (the CLI pulls it after the daemon's 404 and sends the
        create again) or can't be looked up: its volumes are then made unlabelled."""
        if not isinstance(image, str) or not image:
            return []
        try:
            status, obj = self.api.call("GET", "%s/images/%s/json" % (prefix, quote(image)))
        except (OSError, http.client.HTTPException) as e:
            status, obj = 0, {"message": str(e)}
        if status != 200 or not isinstance(obj, dict):
            self.note("%s: image %s not looked up (%s): its VOLUMEs, if any, are made unlabelled" % (
                what, image, (obj or {}).get("message") if isinstance(obj, dict) else "status %s" % status))
            return []
        paths = get(get(obj, "Config"), "Volumes")
        return [p for p in paths] if isinstance(paths, dict) else []

    def make_volume(self, name, driver, opts, labels, prefix, what):
        """Create a volume a container would make itself (it isn't there), labelled; True if made. Trouble is logged,
        and the create goes on: the daemon then makes the volume, unlabelled, or says what is wrong."""
        try:
            body = {"Name": name, "Labels": dict(labels)}
            body["Labels"][LABEL] = self.persona
            if driver:
                body["Driver"] = driver
            if opts:
                body["DriverOpts"] = opts
            status, out = self.api.call("POST", "%s/volumes/create" % prefix, body)
        except (OSError, http.client.HTTPException) as e:
            status, out = 0, {"message": str(e)}
        if status in (200, 201):
            return True
        self.log("%s: couldn't make volume %s first (%s), so it will be unlabelled" % (
            what, name, (out or {}).get("message") if isinstance(out, dict) else status))
        return False

    def inspect(self, kind, ref, prefix, what, quiet=False):
        """-> the object, or None when there is no such thing (the daemon will say so, or make it)."""
        path = "%s/%s/%s%s" % (prefix, kind, quote(ref), "/json" if kind in ("containers", "images") else "")
        try:
            status, obj = self.api.call("GET", path)
        except (OSError, http.client.HTTPException) as e:
            status, obj = 0, {"message": str(e)}
        if status == 404:
            if not quiet:
                self.note("%s: no such %s %s, on to the daemon" % (what, KIND[kind], ref))
            return None
        if status != 200 or not isinstance(obj, dict):
            detail = obj.get("message") if isinstance(obj, dict) else None
            raise Answer(403, "couldn't look up %s %s to see whose it is (%s), so it isn't let through" % (
                KIND[kind], ref, detail or "status %s" % status))
        return obj

    def owned(self, kind, ref, verb, what, prefix):
        """ref inspected: -> the object when it is this persona's, or one of ddev's shared ones and verb what ddev
        does to them (DDEV_VERBS); None when there is no such object; else Answer(403)."""
        obj = self.inspect(kind, ref, prefix, what)
        if obj is None:
            return None
        labels, name = labels_of(kind, obj), name_of(kind, obj)
        if labels.get(LABEL) == self.persona:
            self.note("%s: %s %s is labelled %s=%s" % (what, KIND[kind], ref, LABEL, self.persona))
            return obj
        if name in DDEV.get(kind, ()):
            if verb in DDEV_VERBS:
                self.note("%s: ddev's shared %s %s" % (what, KIND[kind], name))
                return obj
            raise Answer(403, "%s %s isn't labelled %s=%s (it is ddev's, shared by the ddev projects of every "
                         "persona: a persona may stop, restart or remove it, as ddev does, but not %s it: leave it, "
                         "or ask the user, who can %s it on the VM)" % (
                             KIND[kind], shown(kind, ref, obj), LABEL, self.persona, verb, verb))
        raise Answer(403, not_ours("%s %s" % (KIND[kind], shown(kind, ref, obj)), self.persona, whose(kind, labels),
                                   verb))

    def own(self, kind, ref, verb, what, prefix, query):
        """Whether ref is this persona's, or one of ddev's shared ones: -> the path to send the request to, the object
        by its Id (a volume by its name), so the daemon acts on what was checked and not on what the ref names by
        then (a rename can make a name or an id prefix another container's); None for the client's own path: no such
        object (the daemon says so), or a link's removal (the daemon needs parent/alias: the parent is checked)."""
        link = kind == "containers" and verb == "remove" and "/" in ref and link_asked(query)
        if link:
            ref, verb = ref.rsplit("/", 1)[0], "unlink"
        obj = self.owned(kind, ref, verb, what, prefix)
        if obj is None or link:
            return None
        return "%s/%s/%s%s" % (prefix, kind, quote(self.key(kind, ref, obj, "Name" if kind == "volumes" else "Id")),
                               "" if verb == "remove" else "/" + verb)

    def endpoint(self, verb, what, prefix, body):
        """POST networks/N/{connect,disconnect}: the container the body names must be this persona's (or ddev's);
        it goes on by its Id. The network isn't checked: joining a shared one is fine. A body without a container
        it can read goes on as it came (the daemon says what's wrong)."""
        spec = decode(body)
        if isinstance(spec, dict):
            both = twice(spec, "networks")
            if both:
                raise Answer(400, "a request body with the keys %s (one field to the daemon, which would keep the "
                             "last or merge the two where the guard checked the other: give it once)" % both)
        ref = get(spec, "Container")
        if not isinstance(ref, str) or not ref:
            return body
        obj = self.owned("containers", ref, verb, what, prefix)
        if obj is None:
            return body
        put(spec, "Container", self.key("containers", ref, obj, "Id"))
        return json.dumps(spec).encode()

    def key(self, kind, ref, obj, field):
        """obj's Id (or Name): what the daemon finds it by, whatever else is named alike."""
        value = get(obj, field)
        if not isinstance(value, str) or not value:
            raise Answer(403, "couldn't look up %s %s to see whose it is (no %s in the daemon's answer), so it isn't "
                         "let through" % (KIND[kind], ref, field))
        return value

    def image(self, ref, what, prefix):
        """Whether removing ref may go on: -> the path to send it to, so that the ref can't come to mean another
        image by the time the daemon reads it: an id (or its prefix) as the image's full Id, a bare name with its
        :latest (else, once untagged, the daemon would read it as an id prefix); None for the client's own path: a
        name:tag, a digest, no such image."""
        obj = self.inspect("images", ref, prefix, what)
        if obj is None:
            return None
        tags = [t for t in get(obj, "RepoTags") or [] if isinstance(t, str)]
        tagged = norm_ref(ref) in set(norm_ref(t) for t in tags)
        if tagged:
            where = None if ":" in ref.rsplit("/", 1)[-1] else "%s/images/%s" % (prefix, quote(ref + ":latest"))
        elif "@" in ref:
            where = None
        else:
            where = "%s/images/%s" % (prefix, quote(self.key("images", ref, obj, "Id")))
        if len(tags) > 1 and tagged:
            self.note("%s: an untag (the image keeps its other tags)" % what)
            return where
        if where and not tagged and tags:       # an id: a refusal names the image by a tag too
            ref = "%s (%s)" % (tags[0], self.key("images", ref, obj, "Id").split(":")[-1][:12])
        self.image_ours(ref, obj, "remove", what, prefix)
        self.note("%s: an image no other persona's, and no container but this persona's uses" % what)
        return where

    def image_ours(self, ref, obj, verb, what, prefix):
        """Answer(403) unless the image may go (verb "remove") or its tag move (verb "re-point"): not when it is
        another persona's (its label), a persona image (agent.flavour), built by a compose project not this persona's
        (<persona>-…; ddev's, ddev-…, go), or used by a container not labelled this persona's. Labelled this
        persona's (a commit of its own container), the image is its own, whatever it took from its parent."""
        labels = labels_of("images", obj)
        owner, project = labels.get(LABEL), labels.get(PROJECT)
        tail = "leave it, or ask the user, who can remove it on the VM" if verb == "remove" else \
            "tag yours with a name of your own, or ask the user, who can move the tag on the VM"
        if owner and owner != self.persona:
            raise Answer(403, "image %s isn't labelled %s=%s (it is persona %s's: %s)" % (
                ref, LABEL, self.persona, owner, tail))
        if owner != self.persona and FLAVOUR in labels:
            raise Answer(403, "image %s isn't labelled %s=%s (it is a persona image, %s=%s, or built FROM one, which "
                         "takes that label with it: %s)" % (ref, LABEL, self.persona, FLAVOUR, labels[FLAVOUR], tail))
        if owner != self.persona and isinstance(project, str) and project and \
                not project.startswith((self.persona + "-", "ddev-")):
            raise Answer(403, "image %s isn't labelled %s=%s (compose project %s built it, not one of this persona's, "
                         "%s-…: %s)" % (ref, LABEL, self.persona, project, self.persona, tail))
        ident = self.key("images", ref, obj, "Id")
        try:
            status, listed = self.api.call("GET", "%s/containers/json?all=1" % prefix)
        except (OSError, http.client.HTTPException) as e:
            status, listed = 0, {"message": str(e)}
        if status != 200 or not isinstance(listed, list):
            detail = listed.get("message") if isinstance(listed, dict) else None
            raise Answer(403, "couldn't list the containers to see whose use image %s (%s), so it isn't let "
                         "through" % (ref, detail or "status %s" % status))
        for c in listed:
            if not isinstance(c, dict) or get(c, "ImageID") != ident:
                continue
            labels = get(c, "Labels")
            labels = labels if isinstance(labels, dict) else {}
            if labels.get(LABEL) == self.persona:
                continue
            names = get(c, "Names")
            name = names[0].lstrip("/") if isinstance(names, list) and names and isinstance(names[0], str) else \
                str(get(c, "Id") or "?")[:12]
            raise Answer(403, "image %s is used by container %s, which isn't labelled %s=%s (%s: %s)" % (
                ref, name, LABEL, self.persona, whose("containers", labels), tail))

    def retag(self, src, given, what, prefix):
        """POST images/SRC/tag, or POST commit (src None), with repo and tag in given (the query, a form body): the
        repo:tag it would move, if it names an image now, must be one that image_ours() lets go, or SRC itself."""
        for text in given:
            ref = tag_target(form_first(text, "repo") or "", form_first(text, "tag") or "")
            if not ref:
                continue
            obj = self.inspect("images", ref, prefix, what)
            if obj is None:
                continue                # a new tag
            if src is not None:
                was = self.inspect("images", src, prefix, what)
                if was is not None and get(was, "Id") == self.key("images", ref, obj, "Id"):
                    self.note("%s: %s names that image already" % (what, ref))
                    continue
            self.image_ours(norm_ref(ref), obj, "re-point", what, prefix)
            self.note("%s: %s may move: it names an image no other persona's" % (what, ref))

    # -- answers and the splice

    def answer(self, client, code, message):
        """Answer the request here, then close (after drain())."""
        body = (json.dumps({"message": "nexus guard: " + message}) + "\n").encode()
        head = "HTTP/1.1 %d %s\r\nContent-Type: application/json\r\nContent-Length: %d\r\nConnection: close\r\n\r\n" % (
            code, REASONS.get(code, "Error"), len(body))
        try:
            client.sendall(head.encode() + body)
            client.shutdown(socket.SHUT_WR)
        except OSError:
            return
        drain(client)

    def splice(self, client, up):
        """Copy both ways until both are done. An EOF goes on as an EOF (shutdown(SHUT_WR)), so a half-closed side
        still gets its answer. Once the daemon is done, a client that stays quiet for self.linger seconds is cut off;
        an error either way cuts both."""
        last = [time.monotonic()]
        inward_done = threading.Event()

        def inward():
            try:
                if pump(client, up, last):
                    shut(up, socket.SHUT_WR)
                else:
                    shut(client, socket.SHUT_RDWR)
                    shut(up, socket.SHUT_RDWR)
            finally:
                inward_done.set()

        t = threading.Thread(target=inward, name="docker-guard-in", daemon=True)
        t.start()
        if pump(up, client, last):
            shut(client, socket.SHUT_WR)
        else:
            shut(client, socket.SHUT_RDWR)
            shut(up, socket.SHUT_RDWR)
        while not inward_done.wait(self.linger):
            if time.monotonic() - last[0] >= self.linger:
                shut(client, socket.SHUT_RDWR)     # wakes inward's recv with an EOF
                shut(up, socket.SHUT_RDWR)
        t.join()


def pump(src, dst, last):
    """src → dst until src's EOF (True) or an error either side (False)."""
    while True:
        try:
            data = src.recv(CHUNK)
        except OSError:
            return False
        if not data:
            return True
        last[0] = time.monotonic()
        try:
            dst.sendall(data)
        except OSError:
            return False


def shut(sock, how):
    try:
        sock.shutdown(how)
    except OSError:
        pass


def drain(client):
    """Read and drop what the client still sends (2 s, MAX_BODY at most) once it has its answer: a close with unread
    data would reset the connection before the client reads the answer."""
    try:
        client.settimeout(1.0)
        end, got = time.monotonic() + 2.0, 0
        while time.monotonic() < end and got < MAX_BODY:
            data = client.recv(CHUNK)
            if not data:
                break
            got += len(data)
    except OSError:
        pass


def listen(path):
    """A listening socket at path. What is there is removed first: a socket left by a guard that died, or the
    image's link to the raw socket (the link, not its target)."""
    try:
        if not os.path.isdir(path) or os.path.islink(path):
            os.unlink(path)
    except FileNotFoundError:
        pass
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        s.bind(path)
        s.listen(4096)      # the daemon's: Go dials a unix socket non-blocking, so a full queue is "daemon not running"
    except OSError:
        s.close()
        raise
    return s


def _term(signum, frame):
    raise SystemExit(0)


def main():
    persona = os.environ.get("AGENT_PERSONA", "").strip()
    if not persona:
        sys.exit("docker-guard: AGENT_PERSONA isn't set: the guard labels what a persona creates with its name, so it "
                 "needs one (the persona's container has it)")
    path = os.environ.get("NEXUS_GUARD_LISTEN") or LISTEN
    upstream = os.environ.get("NEXUS_GUARD_UPSTREAM") or UPSTREAM
    guard = Guard(persona, upstream, debug=os.environ.get("NEXUS_GUARD_DEBUG", "") not in ("", "0"))
    os.umask(0o077)                     # the socket is the image user's alone
    signal.signal(signal.SIGTERM, _term)
    try:
        listener = listen(path)
    except OSError as e:
        sys.exit("docker-guard: can't listen on %s: %s" % (path, e.strerror or e))
    ino = os.stat(path).st_ino
    guard.note("listening on %s for persona %s, the daemon at %s" % (path, persona, upstream))
    try:
        guard.serve(listener)
    except (SystemExit, KeyboardInterrupt):
        pass
    finally:
        listener.close()
        try:
            if os.stat(path).st_ino == ino:     # ours still, not a newer guard's
                os.unlink(path)
        except OSError:
            pass


if __name__ == "__main__":
    main()
