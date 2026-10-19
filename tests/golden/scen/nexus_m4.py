"""nexus on personas.json at its edges, from the review of M4: claude.managed.json coming and going, top's live
view re-reading the personas, personas.json or compose.personas.yml gone, a hand-written personas.json, and
setups that must read as the bash read them (orphans, own root and key, many personas, links)."""
import json
import os
import re
import shutil

from scen import case, two_personas


def hms(text):
    return re.sub(r"(nexus top(?:\\e\[[0-9;]*m)?  \S*  )\d\d:\d\d:\d\d", r"\1<HH:MM:SS>", text)


def frames(text):
    """hms, and top's frames (and the docker calls each one makes) as they change, not how many of each came:
    that depends on how fast the machine draws them."""
    parts = re.split(r"(?=\\e\[H)|(?=\\e\[\?7h\\e\[\?25h\\e\[\?1049l)", hms(text))
    text = "".join(p for i, p in enumerate(parts) if i == 0 or p != parts[i - 1])
    return re.sub(r'^(\{"argv".*\n)(?:\1)+', r"\1", text, flags=re.M)


MANAGED = "a/personas/claude.managed.json"


@case("nexus/m4-grok-managed-gone")
def _(sb):
    # claude.managed.json gone (a committed file): a claude persona is told to restore it; a grok one
    # never mounted it.
    two_personas(sb)
    os.unlink(sb.path(MANAGED))
    sb.run("nexus", "g1", "start", label="nexus g1 start  (claude.managed.json gone)")
    sb.run("nexus", "c1", "start", label="nexus c1 start  (claude.managed.json gone)")


@case("nexus/m4-managed-back")
def _(sb):
    # The opt-out the claude message offers ("re-run nexus --config without it"), then a git checkout or pull
    # brings claude.managed.json back.
    two_personas(sb)
    with open(sb.path(MANAGED), "rb") as f:
        keep = f.read()
    os.unlink(sb.path(MANAGED))
    sb.setup("config", "--yes", "--no-link", "--no-build")
    sb.write(MANAGED, keep)
    sb.run("nexus", "c1", "start", label="nexus c1 start  (claude.managed.json back)")
    sb.run("nexus", "g1", "start", label="nexus g1 start  (claude.managed.json back)")


@case("nexus/m4-orphans", norm=hms)
def _(sb):
    sb.personas("c1:claude:docker", "c2:claude:docker", "g1:grok:docker", extra=("--ssh", "g1:new"))
    sb.container("c1")
    sb.container("c2")
    sb.setup("config", "--yes", "--no-link", "--no-build", "--remove", "c2")
    sb.run("nexus", "ls")
    sb.run("nexus", "top", "--once")
    sb.run("nexus", "c2", label="nexus c2  (removed, still running)")


@case("nexus/m4-root-and-ssh", norm=hms)
def _(sb):
    own = sb.path("own")
    os.makedirs(own)
    sb.personas("c1:claude:docker:" + own, "g1:grok:docker", extra=("--ssh", "c1:new", "--ssh", "g1:none"))
    sb.run("nexus", "ls")
    sb.run("nexus", "c1", "start")
    sb.container("c1", root=sb.path("older"))
    sb.run("nexus", "ls", label="nexus ls  (c1 running on an older root)")
    sb.run("nexus", "c1", "start", label="nexus c1 start  (running, older root)")
    sb.run("nexus", "c1", cwd="own", label="nexus c1  (running, older root, from own)")
    sb.run("nexus", "top", "--once")


@case("nexus/m4-many", norm=hms)
def _(sb):
    specs = []
    for i in range(1, 11):
        specs.append("%s%d:%s:%s" % ("gc"[i % 2], i, "grok" if i % 2 == 0 else "claude", "ddev" if i % 3 == 0 else "docker"))
    sb.personas(*specs, extra=("--ssh", "c3:none", "--ssh", "g4:new"))
    sb.container("c5")
    sb.container("g10")
    sb.run("nexus", "ls")
    sb.run("nexus", "top", "--once")
    sb.run("nexus", "nope", label="nexus nope  (the list of names)")
    sb.run("nexus", "c7", "start")


@case("nexus/m4-symlink-compose")
def _(sb):
    two_personas(sb)
    os.makedirs(sb.path("elsewhere"))
    shutil.move(sb.path("a/compose.personas.yml"), sb.path("elsewhere/compose.personas.yml"))
    sb.symlink("a/compose.personas.yml", sb.path("elsewhere/compose.personas.yml"))
    sb.setup("config", "--yes", "--no-link", "--no-build", "--persona", "c2:claude:docker")
    sb.run("nexus", "c2", "start", label="nexus c2 start  (compose.personas.yml a symlink)")
    sb.container("c1")
    sb.run("nexus", "c1", label="nexus c1  (running, symlinked file)")


@case("nexus/m4-latin1-key")
def _(sb):
    d = sb.path("k\udce9ys")
    os.makedirs(d)
    k = os.path.join(d, "id_\udce9d")
    sb.setup(os.path.join(sb.bin, "ssh-keygen"), "-q", "-t", "ed25519", "-N", "", "-C", "nxt-c1", "-f", k)
    sb.personas("c1:claude:docker", extra=("--ssh", "c1:" + k))
    sb.run("nexus", "ls")
    sb.run("nexus", "c1", "start", label="nexus c1 start  (a key path with a latin1 byte)")


@case("nexus/m4-top-live-reread", norm=frames)
def _(sb):
    # nexus --config adds a persona while `nexus top` is open: bash's top read the personas each frame. q goes once
    # c2 is drawn, and the frames count as they change (a slow machine draws fewer in the time).
    sb.personas("c1:claude:docker")
    keep = {}
    for f in ("personas.json", "compose.personas.yml"):
        if os.path.exists(sb.path("a/" + f)):
            with open(sb.path("a/" + f), "rb") as h:
                keep[f] = h.read()
    sb.setup("config", "--yes", "--no-link", "--no-build", "--persona", "c2:claude:docker")
    for f in keep:
        shutil.copy(sb.path("a/" + f), sb.path("a/" + f + ".two"))
        sb.write("a/" + f, keep[f])
    w = sb.script("wrap", "cd %s && (sleep 2; for f in personas.json compose.personas.yml; do [ -e $f.two ] && cp $f.two $f; "
                  "done) & exec %s/nexus top -n 1" % (sb.a, sb.a))
    sb.run(w, mode="tty-all", steps=[("\x1b[1mc2\x1b[0m", "q")], label="nexus top -n 1  (c2 added at 2 s)")


def _json(sb, data, raw=None):
    with open(sb.path("a/personas.json"), "wb") as f:
        f.write(raw if raw is not None else json.dumps(data).encode())


@case("nexus/m4-json-by-hand")
def _(sb):
    # A personas.json written by hand, valid: another key order, CRLF, no newline at the end.
    two_personas(sb)
    raw = (b'{"personas": {\r\n "c1": {"statusline": true, "colour": "colour25", "context": "docker", "flavour": "claude"},'
           b'\r\n "g1": {"ssh": "personas/g1.key", "statusline": true, "flavour": "grok", "context": "docker",'
           b' "colour": "colour28"}}, "version": 1}')
    _json(sb, None, raw)
    sb.run("nexus", "c1", "start", label="nexus c1 start  (hand-written, valid)")
    sb.run("nexus", "g1", "start", label="nexus g1 start  (hand-written, valid)")
    with open(sb.path("a/personas.json"), "rb") as f:
        data = json.loads(f.read())
    data["personas"]["c1"]["ssh"] = "~/.ssh/id_c1"
    _json(sb, data)
    sb.run("nexus", "c1", "start", label="nexus c1 start  (ssh ~/.ssh/id_c1 by hand)")
    del data["personas"]["c1"]["ssh"]
    data["personas"]["c1"]["root"] = "/tmp/x\u0000y"
    _json(sb, data)
    sb.run("nexus", "c1", "start", label="nexus c1 start  (root with a NUL by hand)")
    sb.run("nexus", "ls", label="nexus ls  (root with a NUL by hand)")
    del data["personas"]["c1"]["root"]
    data["personas"]["c1"]["ssh"] = "/k\ud800"
    raw = json.dumps(data).encode()
    _json(sb, None, raw)
    sb.run("nexus", "c1", label="nexus c1  (running; ssh with a lone surrogate escape)")
    sb.run("nexus", "c1", "restart", label="nexus c1 restart  (ssh with a lone surrogate escape)")
    del data["personas"]["c1"]["ssh"]
    data["personas"]["c1"]["root"] = "/r\ud800"
    _json(sb, None, json.dumps(data).encode())
    sb.run("nexus", "c1", "start", label="nexus c1 start  (root with a lone surrogate escape)")


@case("nexus/m4-json-gone")
def _(sb):
    # personas.json removed by mistake (it is gitignored: git clean -X, say), the generated file still there.
    two_personas(sb)
    sb.setup("config", "--yes", "--no-link", "--no-build", "--persona", "c2:claude:docker")
    sb.container("c1")
    os.unlink(sb.path("a/personas.json"))
    sb.run("nexus", "c1", label="nexus c1  (personas.json gone)")
    sb.run("nexus", "ls", label="nexus ls  (personas.json gone)")
    sb.run("config", "--list", label="nexus --config --list  (personas.json gone)")
    sb.run("config", "--yes", "--no-link", "--no-build", label="nexus --config --yes  (as nexus said)")
    sb.run("nexus", "ls", label="nexus ls  (after)")


@case("nexus/m4-compose-gone")
def _(sb):
    two_personas(sb)
    sb.container("g1")
    os.unlink(sb.path("a/compose.personas.yml"))
    sb.run("nexus", "c1", "start", label="nexus c1 start  (compose.personas.yml gone)")
    sb.run("nexus", "g1", label="nexus g1  (running, compose.personas.yml gone)")
