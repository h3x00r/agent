"""Review scenarios for `nexus <persona> upgrade` (bash 6d95be3 vs the Python port): the corners the
nexus_upgrade scenarios don't reach."""
import os
import re
import socket
import ssl
import subprocess
import threading
import time

from scen import before_personas_json, case, move_lines  # noqa: F401
from scen.nexus_upgrade import (CLAUDE_REPO, OLD, image, no_curl, old_claude, personas,
                                pin, sessions, templates, world)


def set_line(sb, key, line, rel="a/.env"):
    """Every KEY line of .env (active, exported or commented) replaced by LINE (bytes), in place."""
    p = sb.path(rel)
    with open(p, "rb") as f:
        data = f.read()
    pat = re.compile(rb"^[ \t]*(#[ \t]*)?(export[ \t]+)?" + key.encode() + rb"[ \t]*=[^\n]*\n", re.M)
    if not pat.search(data):
        raise RuntimeError("no %s line in %s" % (key, rel))
    data = pat.sub(lambda m: b"", data, count=0)
    data = data + line
    with open(p, "wb") as f:
        f.write(data)


def put(sb, rel, data):
    """REL's content replaced by DATA (bytes), in place (inode kept)."""
    with open(sb.path(rel), "wb") as f:
        f.write(data)


def cat(sb, rel="a/.env"):
    sb.run("cat", sb.path(rel), label="cat <S>/%s" % rel)


def signals(sb, *rules):
    """The fake's signal rules (fake.py maybe_signal), for the next run only."""
    sb.load_world()
    sb.world["signals"] = [dict(r) for r in rules]
    sb.save_world()


def no_signals(sb):
    sb.load_world()
    sb.world.pop("signals", None)
    sb.save_world()


# --- grammar ---------------------------------------------------------------------------------------
@case("nexus/edge-upgrade-channel-space", norm=no_curl)
def _(sb):
    # One argument that holds both channels, with the space between them.
    personas(sb, "c1:claude:docker", "g1:grok:docker", extra=("--ssh", "g1:new"))
    templates(sb)
    sb.run("nexus", "c1", "upgrade", "latest stable", label="nexus c1 upgrade 'latest stable'")
    sb.run("nexus", "g1", "upgrade", "stable alpha", "-y", label="nexus g1 upgrade 'stable alpha' -y")


@case("nexus/edge-upgrade-grammar", norm=no_curl)
def _(sb):
    # Forms that should be the same in both: matched here for the record.
    personas(sb, "c1:claude:docker")
    templates(sb)
    sb.run("nexus", "c1", "upgrade", "vlatest", label="nexus c1 upgrade vlatest  (v dropped: a channel)")
    sb.run("nexus", "c1", "upgrade", "--", "2.1.400")
    sb.run("nexus", "c1", "upgrade", "2.1.400\n", label="nexus c1 upgrade $'2.1.400\\n'")
    sb.run("nexus", "c1", "upgrade", "2.1.400 ", label="nexus c1 upgrade '2.1.400 '")
    sb.run("nexus", "c1", "upgrade", "*", label="nexus c1 upgrade '*'")
    sb.run("nexus", "c1", "upgrade", "-Y")
    sb.run("nexus", "c1", "upgrade", "--yes", "1.2.3-rc.1", "--yes", label="nexus c1 upgrade --yes 1.2.3-rc.1 --yes")
    sb.run("nexus", "c1", "upgrade", "２.1.400", label="nexus c1 upgrade <fullwidth 2>.1.400")


# --- .env ------------------------------------------------------------------------------------------
@case("nexus/edge-upgrade-env-unreadable")
def _(sb):
    # .env there but unreadable; PROJECTS_ROOT exported so start-up doesn't read it, CLAUDE_TAG so the fake doesn't.
    personas(sb, "c1:claude:docker")
    templates(sb)
    pin(sb)   # the first run takes the tree's snapshot, which must read every file
    os.chmod(sb.path("a/.env"), 0o200)
    try:
        sb.run("nexus", "c1", "upgrade", "2.1.400", "-y", env={"PROJECTS_ROOT": sb.projects, "CLAUDE_TAG": "latest"},
               label="nexus c1 upgrade 2.1.400 -y  (.env mode 200)")
    finally:
        os.chmod(sb.path("a/.env"), 0o644)


@case("nexus/edge-upgrade-dir-readonly")
def _(sb):
    # The checkout not writable: env_set can't make its temp file.
    personas(sb, "c1:claude:docker")
    templates(sb)
    os.chmod(sb.a, 0o555)
    try:
        sb.run("nexus", "c1", "upgrade", "2.1.400", "-y", label="nexus c1 upgrade 2.1.400 -y  (<A> mode 555)")
    finally:
        os.chmod(sb.a, 0o755)


@case("nexus/edge-upgrade-env-bytes")
def _(sb):
    # A pin with a byte that isn't UTF-8 (LANG=C.UTF-8): what is read as the old pin, and put back. CLAUDE_TAG
    # exported so the fake never reads .env; the build fails, so the old pin goes back.
    personas(sb, "c1:claude:docker")
    templates(sb)
    world(sb, compose={"build_rc": 4})
    e = {"CLAUDE_TAG": "latest"}
    set_line(sb, "CLAUDE_VERSION", b'CLAUDE_VERSION="2.1.\xff"\n')
    sb.run("nexus", "c1", "upgrade", "2.1.400", "-y", env=e, label="nexus c1 upgrade 2.1.400 -y  (CLAUDE_VERSION=\"2.1.\\xff\")")
    pin_l(sb)
    set_line(sb, "CLAUDE_VERSION", b"CLAUDE_VERSION=2.1.300 #\xff note\n")
    sb.run("nexus", "c1", "upgrade", "2.1.400", "-y", env=e, label="nexus c1 upgrade 2.1.400 -y  (CLAUDE_VERSION=2.1.300 #\\xff note)")
    pin_l(sb)
    # Text again, so tree.txt shows .env (a binary one is shown by a hash of its unnormalised bytes).
    set_line(sb, "CLAUDE_VERSION", b"CLAUDE_VERSION=latest\n")


def pin_l(sb):
    """The CLAUDE_VERSION lines, unambiguously (sed's l: \\377 for a byte that isn't text)."""
    sb.run("sed", "-n", "/CLAUDE_VERSION=/l", sb.path("a/.env"), label="sed -n /CLAUDE_VERSION=/l <A>/.env")


@case("nexus/edge-upgrade-env-forms")
def _(sb):
    # The pin in .env's other forms: the build fails each time, so the pin is written and put back.
    personas(sb, "c1:claude:docker")
    templates(sb)
    world(sb, compose={"build_rc": 4})
    forms = [
        b"export CLAUDE_VERSION=2.1.301\n",
        b'CLAUDE_VERSION="2.1.302" # pinned\n',
        b"CLAUDE_VERSION='2.1.303'\n",
        b"CLAUDE_VERSION = 2.1.304\n",
        b"# CLAUDE_VERSION=2.1.305\n#CLAUDE_VERSION=\n",
        b"CLAUDE_VERSION=2.1.1\nX=1\nCLAUDE_VERSION=2.1.306\n",
        b"CLAUDE_VERSION=\n",
        b"\tCLAUDE_VERSION=2.1.307\t# tab\n",
    ]
    for f in forms:
        set_line(sb, "CLAUDE_VERSION", f)
        sb.run("nexus", "c1", "upgrade", "2.1.400", "-y", label="nexus c1 upgrade 2.1.400 -y  (%r)" % f)
        cat(sb)
    # CRLF throughout.
    p = sb.path("a/.env")
    with open(p, "rb") as fh:
        data = fh.read()
    put(sb, "a/.env", data.replace(b"\n", b"\r\n"))
    sb.run("nexus", "c1", "upgrade", "2.1.400", "-y", label="nexus c1 upgrade 2.1.400 -y  (CRLF .env)")
    cat(sb)
    # No newline at the end, the pin last.
    put(sb, "a/.env", b"REGISTRY=local\nCLAUDE_VERSION=2.1.308")
    sb.run("nexus", "c1", "upgrade", "2.1.400", "-y", label="nexus c1 upgrade 2.1.400 -y  (no newline at the end)")
    cat(sb)
    put(sb, "a/.env", b"")
    sb.run("nexus", "c1", "upgrade", "2.1.400", "-y", label="nexus c1 upgrade 2.1.400 -y  (empty .env)")
    cat(sb)


@case("nexus/edge-upgrade-env-symlink")
def _(sb):
    # .env a symlink to a file elsewhere: written through, the link kept; a successful pin, then a failed one.
    personas(sb, "c1:claude:docker")
    templates(sb)
    with open(sb.path("a/.env"), "rb") as fh:
        data = fh.read()
    sb.write("conf/env", data)
    os.unlink(sb.path("a/.env"))
    sb.symlink("a/.env", sb.path("conf/env"))
    sb.run("nexus", "c1", "upgrade", "2.1.400", "-y")
    world(sb, compose={"build_rc": 4})
    sb.run("nexus", "c1", "upgrade", "2.1.401", "-y", label="nexus c1 upgrade 2.1.401 -y  (the build fails)")
    # A dangling link.
    os.unlink(sb.path("conf/env"))
    sb.run("nexus", "c1", "upgrade", "2.1.401", "-y", label="nexus c1 upgrade 2.1.401 -y  (.env links to nothing)")


# --- signals in the pin-to-check window (fake.py maybe_signal) ----------------------------------------
INSPECT_ID = ["image", "inspect", "{{.Id}}"]


@case("nexus/edge-upgrade-term-prebuild")
def _(sb):
    # TERM while the last (only) builder's image id is taken, just before the build: bash's trap restores then;
    # c1's image_id calls: 1 the peers' look, 2 the one before the build, 3 after it.
    personas(sb, "c1:claude:docker")
    templates(sb)
    old_claude(sb)
    signals(sb, {"has": INSPECT_ID, "nth": 2, "sig": "TERM"})
    sb.run("nexus", "c1", "upgrade", "2.1.400", "-y", label="nexus c1 upgrade 2.1.400 -y  (TERM at the pre-build image id)")
    no_signals(sb)
    pin(sb)


@case("nexus/edge-upgrade-term-postbuild")
def _(sb):
    # TERM at the image id after the build (3rd image_id call).
    personas(sb, "c1:claude:docker")
    templates(sb)
    old_claude(sb)
    signals(sb, {"has": INSPECT_ID, "nth": 3, "sig": "TERM"})
    sb.run("nexus", "c1", "upgrade", "2.1.400", "-y", label="nexus c1 upgrade 2.1.400 -y  (TERM at the post-build image id)")
    no_signals(sb)
    pin(sb)


@case("nexus/edge-upgrade-term-noimage")
def _(sb):
    # c2's image is in another repo, so after the build it has none; TERM at that very lookup (5th image_id:
    # c1, c2 before; c1 pre-build; c1, c2 after).
    personas(sb, "c1:claude:docker", "c2:claude:docker", extra=("--ssh", "c2:new"))
    templates(sb, c2="local/agent-claude-mine:{tag}")
    signals(sb, {"has": INSPECT_ID, "nth": 5, "sig": "TERM"})
    sb.run("nexus", "c1", "upgrade", "2.1.400", "-y", label="nexus c1 upgrade 2.1.400 -y  (TERM at c2's missing image)")
    no_signals(sb)
    pin(sb)


@case("nexus/edge-upgrade-term-restore")
def _(sb):
    # The build fails; TERM while its restore removes the tag the run made (the trap is still set).
    personas(sb, "c1:claude:docker")
    templates(sb)
    old_claude(sb)
    world(sb, compose={"build_rc": 4})
    signals(sb, {"has": ["image", "rm"], "nth": 1, "sig": "TERM"})
    sb.run("nexus", "c1", "upgrade", "2.1.400", "-y", label="nexus c1 upgrade 2.1.400 -y  (build fails; TERM in the restore)")
    no_signals(sb)
    pin(sb)


@case("nexus/edge-upgrade-term-verfail")
def _(sb):
    # The version check fails (2.1.399); TERM while its restore points the old tag back.
    personas(sb, "c1:claude:docker")
    templates(sb)
    old_claude(sb)
    image(sb, CLAUDE_REPO + ":2.1.400", "sha256:" + "e" * 64, "2.1.400 (Claude Code)")
    image(sb, "mirror.invalid/agent-claude-docker:2.1.400", "sha256:" + "e" * 64, "2.1.400 (Claude Code)")
    world(sb, built_version="2.1.399 (Claude Code)")
    signals(sb, {"has": ["tag"], "nth": 1, "sig": "TERM"})
    sb.run("nexus", "c1", "upgrade", "2.1.400", "-y", label="nexus c1 upgrade 2.1.400 -y  (2.1.399 built; TERM at the tag back)")
    no_signals(sb)
    pin(sb)


@case("nexus/edge-upgrade-signals-match")
def _(sb):
    # Stages where both should agree.
    personas(sb, "c1:claude:docker", "c2:claude:docker", extra=("--ssh", "c2:new"))
    templates(sb)
    old_claude(sb)
    sb.container("c2", state="exited", image=OLD)
    stages = [
        ("TERM before the pin (the peers' image id)", {"has": INSPECT_ID, "nth": 1, "sig": "TERM"}),
        ("TERM in the build", {"has": ["compose", "build"], "nth": 1, "sig": "TERM"}),
        ("HUP in the build", {"has": ["compose", "build"], "nth": 1, "sig": "HUP"}),
        ("Ctrl-C in the build", {"has": ["compose", "build"], "nth": 1, "sig": "INT", "to": "group"}),
        ("Ctrl-C at the version check", {"has": ["run", "--version"], "nth": 2, "sig": "INT", "to": "group"}),
        ("Ctrl-C before the pin (the version now)", {"has": ["run", "--version"], "nth": 1, "sig": "INT", "to": "group"}),
        ("Ctrl-C at the pre-build image ref", {"has": ["config", "--images"], "nth": 3, "sig": "INT", "to": "group"}),
        ("TERM at removing the stopped c2", {"cmd": "docker", "has": ["rm", "agent-c2"], "nth": 1, "sig": "TERM"}),
    ]
    for i, (what, rule) in enumerate(stages):
        signals(sb, rule)
        sb.run("nexus", "c1", "upgrade", "2.1.%d" % (400 + i), "-y", label="nexus c1 upgrade 2.1.%d -y  (%s)" % (400 + i, what))
        no_signals(sb)
        pin(sb)


def short_agent(sb):
    """agent(sb), its socket bound through /proc/self/fd, so a long sandbox path is no matter (AF_UNIX's 108)."""
    p = sb.path("ssh-agent/sock")
    os.makedirs(os.path.dirname(p))
    fd = os.open(os.path.dirname(p), os.O_RDONLY)
    try:
        s = socket.socket(socket.AF_UNIX)
        s.bind("/proc/self/fd/%d/sock" % fd)
        s.close()
    finally:
        os.close(fd)
    sb.env["SSH_AUTH_SOCK"] = p
    world(sb, ssh_add_rc=0)


@case("nexus/edge-upgrade-prompt-answers")
def _(sb):
    personas(sb, "c1:claude:docker")
    templates(sb)
    old_claude(sb)
    short_agent(sb)
    sb.container("c1", image=OLD, sessions=sessions(sb))
    q = "Recreate them now? [y/N] "
    sb.run("nexus", "c1", "upgrade", "2.1.401", mode="tty-in", steps=[(q, " y ")], label="nexus c1 upgrade 2.1.401  (' y ')")
    sb.run("nexus", "c1", "upgrade", "2.1.402", mode="tty-in", steps=[(q, "nope")], label="nexus c1 upgrade 2.1.402  (nope)")
    sb.run("nexus", "c1", "upgrade", "2.1.403", mode="tty-in", steps=[(q, "Yes")], label="nexus c1 upgrade 2.1.403  (Yes)")
    sb.run("nexus", "c1", "upgrade", "2.1.404", mode="tty-in", steps=[(q, "<INT>")], label="nexus c1 upgrade 2.1.404  (^C)")
    pin(sb)
    sb.run("nexus", "c1", "upgrade", "2.1.405", mode="tty-in", steps=[(q, "\\y")], label="nexus c1 upgrade 2.1.405  (\\y)")


# --- the release lookup, through a local proxy (no network) ------------------------------------------
def _head(s):
    buf = b""
    while b"\r\n\r\n" not in buf:
        d = s.recv(1)
        if not d:
            break
        buf += d
    return buf


class Releases:
    """An https proxy on 127.0.0.1 that answers CONNECT itself, with a certificate of its own for the release
    hosts: ROUTES maps "host/path" to (status, body bytes, seconds per byte or 0, extra header lines)."""

    def __init__(self, sb, routes):
        d = sb.path("tls")
        os.makedirs(d)
        self.cert, key = os.path.join(d, "cert.pem"), os.path.join(d, "key.pem")
        subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", key, "-out", self.cert,
                        "-days", "2", "-subj", "/CN=downloads.claude.ai",
                        "-addext", "subjectAltName=DNS:downloads.claude.ai,DNS:x.ai"],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        self.ctx.load_cert_chain(self.cert, key)
        self.routes = routes
        self.ls = socket.socket()
        self.ls.bind(("127.0.0.1", 0))
        self.ls.listen(16)
        self.port = self.ls.getsockname()[1]
        threading.Thread(target=self._loop, daemon=True).start()
        sb.env["https_proxy"] = "http://127.0.0.1:%d" % self.port
        sb.env["SSL_CERT_FILE"] = self.cert

    def _loop(self):
        while True:
            try:
                c, _ = self.ls.accept()
            except OSError:
                return
            threading.Thread(target=self._one, args=(c,), daemon=True).start()

    def _one(self, c):
        try:
            line = _head(c).split(b"\r\n", 1)[0].decode()
            method, target, _ = line.split(" ", 2)
            if method != "CONNECT":
                c.sendall(b"HTTP/1.1 405 No\r\nContent-Length: 0\r\n\r\n")
                return
            c.sendall(b"HTTP/1.1 200 Connection established\r\n\r\n")
            s = self.ctx.wrap_socket(c, server_side=True)
            path = _head(s).split(b" ", 2)[1].decode()
            status, body, trickle, extra = self.routes.get(target.rsplit(":", 1)[0] + path, (404, b"not here\n", 0, b""))
            s.sendall(b"HTTP/1.1 %d X\r\nContent-Length: %d\r\nConnection: close\r\n%s\r\n" % (status, len(body), extra))
            for i in range(0, len(body), 1 if trickle else len(body) or 1):
                if trickle:
                    time.sleep(trickle)
                s.sendall(body[i:i + 1] if trickle else body)
            s.close()
        except Exception:  # noqa: BLE001
            pass


LATEST = "downloads.claude.ai/claude-code-releases/latest"
STABLE = "downloads.claude.ai/claude-code-releases/stable"


@case("nexus/edge-upgrade-lookup-ok", norm=no_curl)
def _(sb):
    # The proxy works for both: the newest of latest is looked up and pinned; HTTP errors and junk are refused.
    personas(sb, "c1:claude:docker", "g1:grok:docker", extra=("--ssh", "g1:new"))
    templates(sb)
    Releases(sb, {LATEST: (200, b" 2.1.400\r\n", 0, b""), STABLE: (404, b"no\n", 0, b""),
                  "x.ai/cli/stable": (200, b"", 0, b""), "x.ai/cli/alpha": (200, b"<html>0.9.1</html>\n", 0, b"")})
    sb.run("nexus", "c1", "upgrade", "-y", label="nexus c1 upgrade -y  (latest: ' 2.1.400\\r\\n')")
    sb.run("nexus", "c1", "upgrade", "stable", label="nexus c1 upgrade stable  (404)")
    sb.run("nexus", "g1", "upgrade", label="nexus g1 upgrade  (stable: empty)")
    sb.run("nexus", "g1", "upgrade", "alpha", label="nexus g1 upgrade alpha  (html)")
    pin(sb)


@case("nexus/edge-upgrade-lookup-nul", norm=no_curl)
def _(sb):
    personas(sb, "c1:claude:docker")
    templates(sb)
    Releases(sb, {LATEST: (200, b"2.1.400\x00\n", 0, b"")})
    sb.run("nexus", "c1", "upgrade", "-y", label="nexus c1 upgrade -y  (the body: 2.1.400\\0)")
    pin(sb)


@case("nexus/edge-upgrade-lookup-bytes", norm=no_curl)
def _(sb):
    personas(sb, "c1:claude:docker")
    templates(sb)
    Releases(sb, {LATEST: (200, b"2.1.400\xff\n", 0, b"")})
    sb.run("nexus", "c1", "upgrade", "-y", label="nexus c1 upgrade -y  (the body: 2.1.400\\xff)")


@case("nexus/edge-upgrade-lookup-300", norm=no_curl)
def _(sb):
    personas(sb, "c1:claude:docker")
    templates(sb)
    Releases(sb, {LATEST: (300, b"2.1.400\n", 0, b"")})
    sb.run("nexus", "c1", "upgrade", "-y", label="nexus c1 upgrade -y  (HTTP 300, a version in the body)")
    pin(sb)


@case("nexus/edge-upgrade-lookup-ca", norm=no_curl)
def _(sb):
    # The proxy's CA only in CURL_CA_BUNDLE (as for curl behind a TLS-inspecting proxy), not SSL_CERT_FILE.
    personas(sb, "c1:claude:docker")
    templates(sb)
    r = Releases(sb, {LATEST: (200, b"2.1.400\n", 0, b"")})
    del sb.env["SSL_CERT_FILE"]
    sb.env["CURL_CA_BUNDLE"] = r.cert
    sb.run("nexus", "c1", "upgrade", "-y", label="nexus c1 upgrade -y  (CURL_CA_BUNDLE only)")
    pin(sb)


@case("nexus/edge-upgrade-lookup-slow", norm=no_curl)
def _(sb):
    # The body comes a byte every 3 s (24 s in all): over curl's --max-time 20, under 20 s per read.
    personas(sb, "c1:claude:docker")
    templates(sb)
    Releases(sb, {LATEST: (200, b"2.1.400\n", 3, b"")})
    sb.run("nexus", "c1", "upgrade", "-y", timeout=60, label="nexus c1 upgrade -y  (a byte every 3 s)")
    pin(sb)


def no_curl_line(text):
    return re.sub(r"^<A>/nexus: line \d+: curl: command not found\n", "", text, flags=re.M)


@case("nexus/edge-upgrade-lookup-nocurl", norm=no_curl_line)
def _(sb):
    personas(sb, "c1:claude:docker")
    templates(sb)
    Releases(sb, {LATEST: (200, b"2.1.400\n", 0, b"")})
    os.unlink(sb.path("bin/curl"))
    sb.run("nexus", "c1", "upgrade", "-y", label="nexus c1 upgrade -y  (no curl on PATH)")
    pin(sb)


# --- the personas file -----------------------------------------------------------------------------
@case("nexus/edge-upgrade-personas-bytes")
def _(sb):
    # A comment in compose.personas.yml with a byte that isn't UTF-8 (LANG=C.UTF-8), e.g. a Latin-1 é typed by hand.
    personas(sb, "c1:claude:docker", "c2:claude:docker", extra=("--ssh", "c2:new"))
    templates(sb)
    old_claude(sb)
    before_personas_json(sb)
    with open(sb.path("a/compose.personas.yml"), "ab") as f:
        f.write(b"# caf\xe9\n")
    move_lines(sb)
    sb.run("nexus", "c1", "upgrade", "2.1.400", "-y")
    pin(sb)
    # The byte in c2's own persona line now (a note= field nothing reads).
    before_personas_json(sb)
    p = sb.path("a/compose.personas.yml")
    with open(p, "rb") as f:
        data = f.read()
    data = data.replace(b"# caf\xe9\n", b"").replace(b"# persona: c2 ", b"# persona: c2 note=caf\xe9 ", 1)
    with open(p, "wb") as f:
        f.write(data)
    move_lines(sb)
    sb.run("nexus", "c1", "upgrade", "2.1.401", "-y", label="nexus c1 upgrade 2.1.401 -y  (c2's line: note=caf\\xe9)")
    pin(sb)
    # In the upgrading persona's own line (the first).
    before_personas_json(sb)
    with open(p, "rb") as f:
        data = f.read()
    data = data.replace(b"note=caf\xe9 ", b"").replace(b"# persona: c1 ", b"# persona: c1 note=caf\xe9 ", 1)
    with open(p, "wb") as f:
        f.write(data)
    move_lines(sb)
    sb.run("nexus", "c1", "upgrade", "2.1.402", "-y", label="nexus c1 upgrade 2.1.402 -y  (c1's line: note=caf\\xe9)")
    pin(sb)
