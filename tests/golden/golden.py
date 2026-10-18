#!/usr/bin/env python3
"""Golden scenarios: what nexus and the setup tool print, call and write, recorded from the bash
scripts (the oracle) and compared byte for byte with the tree under test.

  golden.py record [--oracle-ref REF | --tree DIR] [--only GLOB]…   record cases/ from REF (default main)
  golden.py verify [--tree DIR] [--tool nexus|config] [--only GLOB]…  compare DIR (default: this checkout)
  golden.py accept --tree DIR --reason TEXT CASE…                   re-record CASEs from DIR, on purpose
  golden.py list                                                    the scenarios

Each scenario runs in a sandbox of its own: a copy of the tree, a scratch HOME, TMPDIR and projects root,
and a bin/ dir that is the whole PATH: the real tools a script may use, and fakes (fake.py) for docker,
ssh-add, sudo, df, and ddev or mkcert where a scenario wants them. A case is three files:
  transcript.txt  each recorded run: its command, mode, exit code, stdout and stderr
  calls.jsonl     the fake commands' calls, per run
  tree.txt        what changed in the sandbox: files added, removed or changed (content, mode, inode)
Paths, ids and random names are normalised (<A>, <HOME>, <TMP>, <PUBKEY>, …). A tree that still has
config.sh (recorded or verified) gets that name mapped to config-tools, so the cases name the tool as it is
called now; a tree with config-tools never does, so a "config.sh" left in its output shows.
"""
import argparse
import concurrent.futures
import contextlib
import difflib
import fcntl
import fnmatch
import importlib
import hashlib
import io
import json
import os
import pkgutil
import pwd
import re
import shutil
import socket
import stat
import subprocess
import sys
import tarfile
import tempfile

sys.dont_write_bytecode = True   # no __pycache__ in the checkout, even when run without -B
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import drive  # noqa: E402

CASES = os.path.join(HERE, "cases")
REPO = os.path.dirname(os.path.dirname(HERE))

# The real tools on a scenario's PATH; anything else is either faked or missing.
REAL_TOOLS = """bash sh env python3 git ssh-keygen stat readlink realpath dirname basename mktemp cat cp mv rm
mkdir rmdir chmod ln cmp sed awk gawk mawk grep head tail sort tr cut paste id flock setsid timeout nproc wc
sleep ls touch date uname tee printf test true false xargs find uniq expr tput infocmp hostname whoami getent
curl od base64 sha256sum install diff kill ps printenv""".split()
FAKES = ("docker", "ssh-add", "sudo", "df")
FAKE_ON_REQUEST = ("ddev", "mkcert")


class Sandbox:
    """One scenario's world. The scenario sets it up, then makes its recorded run(s)."""

    def __init__(self, tree, root, fakes=()):
        self.tree = tree
        self.s = root
        self.a = os.path.join(root, "a")
        self.home = os.path.join(root, "home")
        self.tmp = os.path.join(root, "tmp")
        self.bin = os.path.join(root, "bin")
        self.projects = os.path.join(root, "projects")
        for d in (self.home, self.tmp, self.bin, self.projects, os.path.join(root, "run"),
                  os.path.join(root, "docker-root")):
            os.makedirs(d)
        copy_tree(tree, self.a)
        self.cfg = "config-tools" if os.path.exists(os.path.join(self.a, "config-tools")) else "config.sh"
        for t in REAL_TOOLS:
            p = shutil.which(t, path="/usr/local/bin:/usr/bin:/bin")
            if p:
                os.symlink(p, os.path.join(self.bin, t))
        for f in FAKES + tuple(fakes):
            self.fake(f)
        with open(os.path.join(self.home, ".gitconfig"), "w") as f:
            f.write("[user]\n\tname = Test User\n\temail = test@example.invalid\n")
        self.world = {"containers": {}, "images": {}, "service_images": {}}
        self.save_world()
        open(os.path.join(root, "calls.jsonl"), "w").close()
        # The tools run Python with -I and never see this; the settings checks they run (python3 -c "import
        # tomllib" …) do, so every host looks like one without tomllib (as Python 3.8 is): the goldens don't
        # depend on the host's Python. tests/run.sh covers the TOML checks with a real persona image.
        hide = os.path.join(root, "pyhide")
        os.makedirs(hide)
        with open(os.path.join(hide, "tomllib.py"), "w") as f:
            f.write("raise ImportError('hidden by tests/golden')\n")
        self.env = {
            "PYTHONPATH": hide, "PYTHONDONTWRITEBYTECODE": "1",
            "HOME": self.home, "PATH": self.bin, "TMPDIR": self.tmp, "LANG": "C.UTF-8",
            "TERM": "xterm-256color", "USER": pwd.getpwuid(os.getuid()).pw_name,
            "LOGNAME": pwd.getpwuid(os.getuid()).pw_name, "GIT_CONFIG_NOSYSTEM": "1",
            "OMP_NUM_THREADS": "3", "XDG_RUNTIME_DIR": os.path.join(root, "run"),
            "https_proxy": "http://127.0.0.1:9", "http_proxy": "http://127.0.0.1:9",
            # Fixed ids for nexus, so the ssh-agent search never finds this VM's own /run/user/<uid>.
            "AGENT_UID": "4242", "AGENT_GID": "4343", "DOCKER_GID": "4444",
        }
        self.runs = []
        self.calls_seen = 0
        self.before = None

    # --- setting up ---
    def fake(self, name):
        w = os.path.join(self.bin, name)
        if os.path.lexists(w):
            os.unlink(w)
        with open(w, "w") as f:
            f.write("#!/bin/sh\nexec %s -I -B %s %s %s \"$@\"\n" % (sys.executable, os.path.join(HERE, "fake.py"),
                                                                    self.s, name))
        os.chmod(w, 0o755)

    def path(self, rel):
        return os.path.join(self.s, rel)

    def write(self, rel, text, mode=None):
        p = self.path(rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "wb") as f:
            f.write(text.encode() if isinstance(text, str) else text)
        if mode is not None:
            os.chmod(p, mode)
        return p

    def symlink(self, rel, target):
        p = self.path(rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        os.symlink(target, p)
        return p

    def script(self, rel, body):
        """An executable sh script in the sandbox, e.g. a wrapper that runs a tool in a certain way."""
        return self.write(rel, "#!/bin/sh\n" + body + "\n", 0o755)

    @contextlib.contextmanager
    def hold_lock(self):
        """The tools' lock, held by this process the way a running setup or upgrade holds it."""
        fd = os.open(os.path.join(self.a, self.cfg), os.O_RDONLY | os.O_CLOEXEC)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            os.close(fd)

    def save_world(self):
        with open(os.path.join(self.s, "world.json"), "w") as f:
            json.dump(self.world, f, indent=1, sort_keys=True)

    def load_world(self):
        with open(os.path.join(self.s, "world.json")) as f:
            self.world = json.load(f)
        return self.world

    def container(self, persona, state="running", root=None, sessions=None, **kw):
        """A container for PERSONA in the docker world, as compose would have made it."""
        self.load_world()
        root = self.projects if root is None else root
        c = {"state": state, "status": kw.pop("status", "Up 2 hours (healthy)" if state == "running" else
                                                "Exited (0) 2 hours ago"),
             "image": kw.pop("image", "sha256:" + "1" * 64), "env": {"PROJECTS_ROOT": root},
             "workdir": self.a, "home": self.path("ctr/%s/home" % persona),
             "sessions": [{"name": "main", "windows": 1, "attached": 0, "path": root}] if sessions is None else sessions}
        c.update(kw)
        self.world["containers"]["agent-" + persona] = c
        self.save_world()

    def agent_socket(self):
        p = self.path("agent.sock")
        s = socket.socket(socket.AF_UNIX)
        s.bind(p)
        s.close()
        self.env["SSH_AUTH_SOCK"] = p
        return p

    def setup(self, tool, *args, stdin=None, ok=(0,), cwd=None, env=None):
        """A run that sets the scenario up: not recorded, and it must succeed."""
        r = self._exec(tool, args, "pipe", stdin, (), cwd, env, False, None, 60)
        if r.rc not in ok:
            raise RuntimeError("setup %s %s: rc %d\n%s%s" % (tool, " ".join(args), r.rc, r.out.decode(errors="replace"),
                                                            r.err.decode(errors="replace")))
        self.calls_seen = self._calls_len()
        return r

    def personas(self, *specs, extra=()):
        args = ["--yes", "--no-link", "--no-build", "--projects-root", self.projects, "--registry", "local"]
        for s in specs:
            args += ["--persona", s]
        return self.setup("config", *(args + list(extra)))

    # --- the recorded runs ---
    def run(self, tool, *args, mode="pipe", stdin=None, steps=(), cwd=None, env=None, merge=False, via=None,
            timeout=30, label=None):
        if self.before is None:
            self.before = snapshot(self.s)
            self.calls_seen = self._calls_len()
        r = self._exec(tool, args, mode, stdin, steps, cwd, env, merge, via, timeout)
        calls = self._calls_since()
        self.runs.append({"label": label or " ".join([tool] + list(args)), "mode": mode, "result": r,
                          "calls": calls, "cwd": cwd})
        return r

    def _exec(self, tool, args, mode, stdin, steps, cwd, env, merge, via, timeout):
        exe = via or os.path.join(self.a, "nexus" if tool == "nexus" else self.cfg)
        if tool not in ("nexus", "config"):
            exe = tool
        cwd = self.path(cwd) if cwd else self.projects
        e = dict(self.env)
        e["PWD"] = cwd
        if env:
            for k, v in env.items():
                if v is None:
                    e.pop(k, None)
                else:
                    e[k] = v
        if isinstance(stdin, str):
            stdin = stdin.encode()
        return drive.run([exe] + list(args), e, cwd, mode=mode, stdin=stdin, steps=steps, timeout=timeout,
                         merge=merge)

    def _calls_len(self):
        with open(self.path("calls.jsonl")) as f:
            return len(f.readlines())

    def _calls_since(self):
        with open(self.path("calls.jsonl")) as f:
            lines = f.readlines()
        new = lines[self.calls_seen:]
        self.calls_seen = len(lines)
        return new


# --- copying, snapshots ---------------------------------------------------------------------------
def copy_tree(src, dst):
    """The tree's files as git sees them (tracked, and untracked not ignored), or all of them without git;
    with the modes git would give them (755 or 644), whatever the tree's own umask made them."""
    _copy_tree(src, dst)
    for top, dirs, files in os.walk(dst):
        for n in files:
            p = os.path.join(top, n)
            if not os.path.islink(p):
                os.chmod(p, 0o755 if os.stat(p).st_mode & 0o100 else 0o644)
        for n in dirs:
            os.chmod(os.path.join(top, n), 0o755)


def _copy_tree(src, dst):
    os.makedirs(dst)
    if os.path.isdir(os.path.join(src, ".git")) or os.path.isfile(os.path.join(src, ".git")):
        names = subprocess.run(["git", "-C", src, "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
                               check=True, stdout=subprocess.PIPE).stdout.split(b"\0")
        for n in filter(None, names):
            n = os.fsdecode(n)
            s, d = os.path.join(src, n), os.path.join(dst, n)
            if not os.path.lexists(s) or n.startswith("tests/golden/cases/"):
                continue
            os.makedirs(os.path.dirname(d), exist_ok=True)
            if os.path.islink(s):
                os.symlink(os.readlink(s), d)
            else:
                shutil.copy2(s, d)
    else:
        for top, dirs, files in os.walk(src):
            dirs[:] = [d for d in dirs if not (top == src and d in (".git",))]
            rel = os.path.relpath(top, src)
            if rel.replace(os.sep, "/").startswith("tests/golden/cases"):
                continue
            os.makedirs(os.path.join(dst, rel), exist_ok=True)
            for n in files:
                s = os.path.join(top, n)
                d = os.path.join(dst, rel, n)
                if os.path.islink(s):
                    os.symlink(os.readlink(s), d)
                else:
                    shutil.copy2(s, d)


def oracle_tree(ref, dst):
    data = subprocess.run(["git", "-C", REPO, "archive", ref], check=True, stdout=subprocess.PIPE).stdout
    os.makedirs(dst)
    with tarfile.open(fileobj=io.BytesIO(data)) as t:
        t.extractall(dst)
    return subprocess.run(["git", "-C", REPO, "rev-parse", "--short", ref], check=True,
                          stdout=subprocess.PIPE).stdout.decode().strip()


SKIP = ("bin", "pyhide", "world.json", "world.json.lock", "world.json.tmp", "calls.jsonl")


def snapshot(root):
    snap = {}
    for top, dirs, files in os.walk(root):
        rel = os.path.relpath(top, root)
        if rel == ".":
            dirs[:] = [d for d in dirs if d not in SKIP]
            files = [f for f in files if f not in SKIP]
        for n in sorted(dirs) + sorted(files):
            p = os.path.join(top, n)
            st = os.lstat(p)
            key = os.path.normpath(os.path.join(rel, n))
            if stat.S_ISLNK(st.st_mode):
                snap[key] = ("l", os.readlink(p), None, st.st_ino)
            elif stat.S_ISDIR(st.st_mode):
                snap[key] = ("d", "%o" % stat.S_IMODE(st.st_mode), None, st.st_ino)
            elif stat.S_ISSOCK(st.st_mode):
                snap[key] = ("s", "", None, st.st_ino)
            else:
                with open(p, "rb") as f:
                    data = f.read()
                snap[key] = ("f", "%o" % stat.S_IMODE(st.st_mode), data, st.st_ino)
    return snap


def show_content(path, data):
    if data.startswith(b"-----BEGIN OPENSSH PRIVATE KEY-----"):
        return ["    (a private key)"]
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return ["    (binary, sha256 %s)" % hashlib.sha256(data).hexdigest()[:16]]
    if len(text) > 16384:
        return ["    (%d bytes, sha256 %s)" % (len(data), hashlib.sha256(data).hexdigest()[:16])]
    lines = text.replace("\r", "\\r").split("\n")
    shown = ["    | " + l for l in lines[:-1]] if text.endswith("\n") else ["    | " + l for l in lines] + ["    (no newline at the end)"]
    return shown or ["    (empty)"]


def tree_diff(before, after):
    out = []
    for key in sorted(set(before) | set(after)):
        b, a = before.get(key), after.get(key)
        if b == a or (b and a and b[:3] == a[:3] and (a[0] != "f" or b[3] == a[3])):
            continue
        if b and a and b[:3] == a[:3]:
            out.append("~ %s  %s %s inode=new (same content)" % (key, a[0], a[1]))
            continue
        if a is None:
            out.append("- %s" % key)
            continue
        kind, meta, data, ino = a
        if b is None:
            out.append("+ %s  %s %s" % (key, kind, meta))
        else:
            same = "inode=same" if b[3] == ino else "inode=new"
            out.append("~ %s  %s %s %s" % (key, kind, meta, same if kind == "f" else ""))
        if kind == "f":
            out.extend(show_content(key, data))
    return "\n".join(out) + ("\n" if out else "")


# --- normalising -----------------------------------------------------------------------------------
def normaliser(sb, rename):
    host = socket.gethostname()
    user = pwd.getpwuid(os.getuid()).pw_name
    uid, gid = str(os.getuid()), str(os.getgid())
    try:
        sock_gid = str(os.stat("/var/run/docker.sock").st_gid)
    except OSError:
        sock_gid = None
    real_s = os.path.realpath(sb.s)
    subs = []
    for base in sorted({sb.s, real_s}, key=len, reverse=True):
        for rel, name in (("/a", "<A>"), ("/home", "<HOME>"), ("/tmp", "<TMP>"), ("/projects", "<P>"), ("", "<S>")):
            # Only a whole path component: <S>/agent.sock is not <A>gent.sock.
            subs.append((re.compile(re.escape(base + rel) + r"(?![A-Za-z0-9_.-])"), name))

    def norm(text):
        for old, new in subs:
            text = old.sub(new, text)
        if rename:
            text = text.replace("config.sh", "config-tools")
        text = re.sub(r"(ssh-ed25519) AAAA[0-9A-Za-z+/=]+", r"\1 <PUBKEY>", text)
        text = re.sub(r"(\.env|\.yml|\.toml|\.json|\.example)\.[A-Za-z0-9_]{6,8}(?![A-Za-z0-9_])", r"\1.<TMP>", text)
        text = re.sub(r"<TMP>/tmp\.[A-Za-z0-9]{10}", "<TMP>/tmp.<RAND>", text)
        text = re.sub(r"nexus-\d+-\d+", "nexus-<RUN>", text)
        text = re.sub(r"(nexus top(?:\\e\[[0-9;]*m)?  \S+  )\d\d:\d\d:\d\d", r"\1<HH:MM:SS>", text)
        if len(host) >= 4:
            text = re.sub(r"(?<![A-Za-z0-9.-])%s(?![A-Za-z0-9-])" % re.escape(host), "<HOST>", text)
        text = re.sub(r"\(uid %s\)" % uid, "(uid <UID>)", text)
        text = re.sub(r"Docker as %s:" % re.escape(user), "Docker as <USER>:", text)
        # fds 9 (config.sh) and 8 (nexus upgrade) are the bash tools' lock, which they left open in every child
        # but the nexus they ran; the Python tools close it in all (ODDITIES.md). Not compared; a lock fd at
        # any other number still is.
        text = re.sub(r', "lock_fds": \[[89]\]', "", text)
        for k, v in (("AGENT_UID", uid), ("AGENT_GID", gid), ("DOCKER_GID", sock_gid)):
            if v:
                text = text.replace('"%s": "%s"' % (k, v), '"%s": "<%s>"' % (k, k))
        return text
    return norm


def render(sb, rename, extra_norm=None):
    norm = normaliser(sb, rename)
    if extra_norm:
        base = norm
        norm = lambda t: extra_norm(base(t))  # noqa: E731
    t, c = [], []
    for i, r in enumerate(sb.runs, 1):
        res = r["result"]
        head = "## run %d: %s  [%s%s]" % (i, r["label"], r["mode"], ", cwd " + r["cwd"] if r["cwd"] else "")
        t.append(head)
        t.append("rc: %d%s" % (res.rc, " (timed out)" if res.timed_out else ""))
        for name, data in (("stdout", res.out), ("stderr", res.err), ("echo", res.echo)):
            if name == "echo" and not data:
                continue
            text = data.decode("utf-8", "backslashreplace")
            text = text.replace("\x1b", "\\e").replace("\r", "\\r").replace("\x07", "\\a")
            t.append("--- %s%s" % (name, "" if text.endswith("\n") or not text else " (no newline at the end)"))
            t.append(text.rstrip("\n") if text else "(empty)")
        c.append("## run %d" % i)
        c.extend(l.rstrip("\n") for l in r["calls"])
    after = snapshot(sb.s)
    files = {"transcript.txt": "\n".join(t) + "\n", "calls.jsonl": "\n".join(c) + "\n",
             "tree.txt": tree_diff(sb.before or {}, after)}
    return {k: norm(v) for k, v in files.items()}


# --- running cases ---------------------------------------------------------------------------------
def run_case(case, tree, rename):
    root = tempfile.mkdtemp(prefix="golden.", dir=os.environ.get("GOLDEN_TMP") or None)
    try:
        sb = Sandbox(tree, root, fakes=case.fakes)
        case.fn(sb)
        if not sb.runs:
            raise RuntimeError("the scenario made no recorded run")
        files = render(sb, rename, case.norm)
        for v in files.values():
            if "command not found" in v:
                raise RuntimeError("a tool the scenario needs isn't on its PATH:\n" + v)
        return files
    finally:
        shutil.rmtree(root, ignore_errors=True)


def selected(cases, only, tool):
    out = []
    for c in cases:
        if only and not any(fnmatch.fnmatch(c.name, p) for p in only):
            continue
        if tool and not c.name.startswith(tool + "/"):
            continue
        out.append(c)
    return out


def parallel(cases, fn):
    workers = min(8, os.cpu_count() or 2)
    with concurrent.futures.ThreadPoolExecutor(workers) as ex:
        futs = {c.name: ex.submit(fn, c) for c in cases}
        return {n: f for n, f in futs.items()}


def cmd_record(args, cases):
    work = tempfile.mkdtemp(prefix="golden-oracle.")
    try:
        if args.tree:
            tree, prov = os.path.abspath(args.tree), "tree"
        else:
            tree = os.path.join(work, "t")
            prov = "oracle@" + oracle_tree(args.oracle_ref, tree)
        rename = not os.path.exists(os.path.join(tree, "config-tools"))
        futs = parallel(cases, lambda c: run_case(c, tree, rename))
        bad = 0
        for c in cases:
            try:
                files = futs[c.name].result()
            except Exception as e:  # noqa: BLE001
                print("FAIL  %s: %s" % (c.name, e))
                bad += 1
                continue
            write_case(c.name, files, {"provenance": prov})
            print("rec   %s" % c.name)
        return 1 if bad else 0
    finally:
        shutil.rmtree(work, ignore_errors=True)


def write_case(name, files, meta):
    d = os.path.join(CASES, name)
    os.makedirs(d, exist_ok=True)
    for k, v in files.items():
        with open(os.path.join(d, k), "w", newline="") as f:
            f.write(v)
    with open(os.path.join(d, "meta.json"), "w") as f:
        json.dump(meta, f, indent=2, sort_keys=True)
        f.write("\n")


def cmd_verify(args, cases):
    tree = os.path.abspath(args.tree or REPO)
    # Only a tree from before the rename gets the name mapped: after it, a "config.sh" left anywhere shows.
    rename = not os.path.exists(os.path.join(tree, "config-tools"))
    futs = parallel(cases, lambda c: run_case(c, tree, rename))
    bad = 0
    for c in cases:
        d = os.path.join(CASES, c.name)
        if not os.path.isdir(d):
            print("FAIL  %s: not recorded yet (golden.py record)" % c.name)
            bad += 1
            continue
        try:
            files = futs[c.name].result()
        except Exception as e:  # noqa: BLE001
            print("FAIL  %s: %s" % (c.name, e))
            bad += 1
            continue
        diffs = []
        for k, v in files.items():
            with open(os.path.join(d, k), newline="") as f:
                want = f.read()
            if want != v:
                diffs.extend(difflib.unified_diff(want.splitlines(True), v.splitlines(True), "golden/" + k, "now/" + k))
        if diffs:
            bad += 1
            print("FAIL  %s" % c.name)
            sys.stdout.write("".join(diffs[:120]))
            if len(diffs) > 120:
                print("      … %d more diff lines" % (len(diffs) - 120))
        else:
            print("ok    %s" % c.name)
    print("%d of %d scenarios match" % (len(cases) - bad, len(cases)))
    return 1 if bad else 0


def cmd_accept(args, cases):
    tree = os.path.abspath(args.tree)
    rename = not os.path.exists(os.path.join(tree, "config-tools"))
    bad = 0
    for c in cases:
        try:
            files = run_case(c, tree, rename)
        except Exception as e:  # noqa: BLE001
            print("FAIL  %s: %s" % (c.name, e))
            bad += 1
            continue
        write_case(c.name, files, {"provenance": "accepted: " + args.reason})
        print("acc   %s" % c.name)
    return 1 if bad else 0


def load_cases():
    import scen
    for m in pkgutil.iter_modules(scen.__path__):
        importlib.import_module("scen." + m.name)
    return sorted(scen.CASES, key=lambda c: c.name)


def main():
    os.umask(0o022)   # modes in tree.txt mustn't depend on the shell this runs from
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("record")
    r.add_argument("--oracle-ref", default="main")
    r.add_argument("--tree")
    r.add_argument("--only", action="append")
    v = sub.add_parser("verify")
    v.add_argument("--tree")
    v.add_argument("--tool", choices=("nexus", "config"))
    v.add_argument("--only", action="append")
    a = sub.add_parser("accept")
    a.add_argument("--tree", required=True)
    a.add_argument("--reason", required=True)
    a.add_argument("names", nargs="+")
    sub.add_parser("list")
    args = p.parse_args()
    cases = load_cases()
    if args.cmd == "list":
        for c in cases:
            print(c.name)
        return 0
    if args.cmd == "record":
        return cmd_record(args, selected(cases, args.only, None))
    if args.cmd == "verify":
        return cmd_verify(args, selected(cases, args.only, args.tool))
    return cmd_accept(args, selected(cases, args.names, None))


if __name__ == "__main__":
    sys.exit(main())
