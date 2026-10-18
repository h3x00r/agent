"""The fake commands of a golden scenario: docker (with compose), ssh-add, ddev, mkcert, df and sudo.

Each one is called through a wrapper in the scenario's bin/ dir, as `python3 fake.py SANDBOX NAME ARGS…`.
It appends what it was asked to SANDBOX/calls.jsonl and answers from SANDBOX/world.json, the scenario's
"docker world" (containers, images, sessions, exit codes), which docker calls also change, as the real
daemon would (an up starts a container, an image rm removes a tag). A call it doesn't know is logged too,
and fails with exit 97, so a new call shows up in the goldens instead of passing unseen.
"""
import fcntl
import hashlib
import json
import os
import stat
import subprocess
import sys

SB, NAME, ARGS = sys.argv[1], sys.argv[2], sys.argv[3:]
WORLD = os.path.join(SB, "world.json")
LOG = os.path.join(SB, "calls.jsonl")
ENV_KEYS = ("PROJECTS_ROOT", "AGENT_UID", "AGENT_GID", "DOCKER_GID", "SSH_AUTH_SOCK", "AGENT_CA_CERTS",
            "REGISTRY", "CLAUDE_VERSION", "CLAUDE_TAG", "GROK_VERSION", "GROK_TAG", "COMPOSE_PROJECT_NAME")


def short(a):
    """An argument as logged: a long one (a script given to -c) as its sha256."""
    return a if len(a) <= 160 else "sha256:" + hashlib.sha256(a.encode("utf-8", "surrogateescape")).hexdigest()[:16]


def stdin_kind():
    try:
        st = os.fstat(0)
    except OSError:
        return "closed"
    if os.isatty(0):
        return "tty"
    if stat.S_ISFIFO(st.st_mode):
        return "pipe"
    if stat.S_ISREG(st.st_mode):
        return "file"
    if stat.S_ISSOCK(st.st_mode):
        return "socket"
    return "chr"   # /dev/null


def lock_fds():
    """The fds this process got that point at the tools' lock file: a leaked lock shows up here."""
    held = []
    for fd in sorted(os.listdir("/proc/self/fd"), key=int):
        try:
            target = os.readlink("/proc/self/fd/" + fd)
        except OSError:
            continue
        if os.path.basename(target) in ("config.sh", "config-tools") and int(fd) > 2:
            held.append(int(fd))
    return held


class World:
    def __init__(self):
        self.fd = os.open(WORLD + ".lock", os.O_RDWR | os.O_CREAT, 0o600)
        fcntl.flock(self.fd, fcntl.LOCK_EX)
        with open(WORLD) as f:
            self.w = json.load(f)
        self.w.setdefault("containers", {})
        self.w.setdefault("images", {})
        self.w.setdefault("counter", 0)

    def save(self):
        with open(WORLD + ".tmp", "w") as f:
            json.dump(self.w, f, indent=1, sort_keys=True)
        os.replace(WORLD + ".tmp", WORLD)

    def close(self):
        os.close(self.fd)


def log(entry):
    with open(LOG, "a") as f:
        f.write(json.dumps(entry, sort_keys=True) + "\n")


def out(text):
    sys.stdout.write(text)
    sys.stdout.flush()


def err(text):
    sys.stderr.write(text)
    sys.stderr.flush()


# --- docker ----------------------------------------------------------------------------------------
def dotenv_value(key):
    """KEY from the environment, else from the checkout's .env as compose reads it (enough for the fake)."""
    if os.environ.get(key):
        return os.environ[key]
    path = os.path.join(SB, "a", ".env")
    val = ""
    if os.path.exists(path):
        with open(path) as f:
            for line in f:
                line = line.strip()
                if line.startswith("export "):
                    line = line[7:].lstrip()
                if line.startswith(key + "="):
                    val = line[len(key) + 1:].strip().strip("'\"")
    return val


def service_image(w, service):
    tmpl = w.get("service_images", {}).get(service)
    if tmpl is None:
        return None
    tag = dotenv_value("CLAUDE_TAG") or dotenv_value("CLAUDE_VERSION") or "latest"
    if "{grok_tag}" in tmpl:
        tag = dotenv_value("GROK_TAG") or dotenv_value("GROK_VERSION") or "latest"
        return tmpl.replace("{grok_tag}", tag)
    return tmpl.replace("{tag}", tag)


def new_id(w, ref):
    w["counter"] += 1
    return "sha256:%064x" % (int(hashlib.sha256(("%s#%d" % (ref, w["counter"])).encode()).hexdigest(), 16))


def compose(world, args):
    w = world.w
    files, profile, i = [], None, 0
    while i < len(args) and args[i].startswith("-"):
        if args[i] in ("-p", "-f", "--env-file", "--profile"):
            if args[i] == "-f":
                files.append(args[i + 1])
            if args[i] == "--profile":
                profile = args[i + 1]
            i += 2
        else:
            i += 1
    sub, rest = (args[i], args[i + 1:]) if i < len(args) else ("", [])
    c = w.get("compose", {})
    if sub == "version":
        if c.get("version_rc"):
            err("docker: 'compose' is not a docker command.\n")
            return c["version_rc"]
        out("Docker Compose version v2.35.1\n")
        return 0
    if sub == "config":
        if "-q" in rest:
            if c.get("config_out"):
                err(c["config_out"] + "\n")
            return c.get("config_rc", 0)
        if "--images" in rest:
            ref = service_image(w, rest[-1])
            if ref is None:
                err("fake compose: no image for %s\n" % rest[-1])
                return 1
            out(ref + "\n")
            return 0
    if sub == "up":
        service = rest[-1]
        rc = c.get("up_rc", 0)
        if rc:
            err(" Container agent-%s  Error\n" % service)
            return rc
        ref = service_image(w, service) or "local/agent-%s:latest" % service
        img = w["images"].get(ref, {}).get("id") or new_id(w, ref)
        name = "agent-" + service
        w["containers"][name] = {
            "state": "running", "status": "Up Less than a second (health: starting)", "image": img,
            "env": {"PROJECTS_ROOT": os.environ.get("PROJECTS_ROOT", "")},
            "workdir": os.path.dirname(files[0]) if files else "",
            "sessions": [{"name": "main", "windows": 1, "attached": 0, "path": os.environ.get("PROJECTS_ROOT", "")}],
            "home": w["containers"].get(name, {}).get("home") or os.path.join(SB, "ctr", service, "home"),
        }
        world.save()
        err(" Container %s  %s\n" % (name, "Recreated" if "--force-recreate" in rest else "Started"))
        return 0
    if sub in ("build", "pull"):
        service = rest[-1]
        rc = c.get(sub + "_rc", 0)
        if rc:
            err("fake compose: the %s of %s failed\n" % (sub, service))
            return rc
        ref = service_image(w, service) or "local/agent-%s:latest" % service
        version = ref.rsplit(":", 1)[-1]
        w["images"][ref] = {"id": new_id(w, ref), "version": w.get("built_version", version + " (Claude Code)")}
        world.save()
        err(" %s %s  done\n" % (service, "Built" if sub == "build" else "Pulled"))
        return 0
    if sub == "stop":
        ctr = w["containers"].get("agent-" + rest[-1])
        if ctr is not None:
            ctr["state"], ctr["status"] = "exited", "Exited (0) Less than a second ago"
            world.save()
        err(" Container agent-%s  Stopped\n" % rest[-1])
        return 0
    if sub == "logs":
        return 0
    return unknown()


def unknown():
    err("fake %s: unhandled call\n" % NAME)
    return 97


def docker_exec(world, args):
    w = world.w
    i, interactive, workdir, env = 0, False, None, {}
    while i < len(args) and args[i].startswith("-"):
        a = args[i]
        if a in ("-e", "-w", "-u"):
            if a == "-e":
                k, _, v = args[i + 1].partition("=")
                env[k] = v
            if a == "-w":
                workdir = args[i + 1]
            i += 2
            continue
        if "i" in a.lstrip("-"):
            interactive = True
        i += 1
    name, cmd = args[i], args[i + 1:]
    ctr = w["containers"].get(name)
    if ctr is None or ctr["state"] != "running":
        err("Error response from daemon: container %s is not running\n" % name)
        return 1
    if workdir is not None and not (workdir == ctr["env"].get("PROJECTS_ROOT") or
                                    workdir.startswith(ctr["env"].get("PROJECTS_ROOT", "\0") + "/")):
        err("OCI runtime exec failed: exec failed: unable to start container process: chdir to cwd (\"%s\") "
            "set in config.json failed: no such file or directory: unknown\n" % workdir)
        return 126
    prog = cmd[0] if cmd else ""
    if prog == "sh" and len(cmd) >= 3 and cmd[1] == "-c":
        script = cmd[2]
        if "tmux list-sessions" in script and "session_attached" in script:
            if ctr.get("tmux_down"):
                return 1
            for s in ctr.get("sessions", []):
                out("".join(str(s[k]).replace("\n", " ").replace("\t", " ").replace("\037", " ") + "\037"
                            for k in ("name", "windows", "attached", "path")) + "\n")
            return 0
        if "list-panes -a" in script:
            if ctr.get("tmux_down"):
                return 1
            for p in ctr.get("panes", []):
                out("\t".join(p) + "\n")
            return 0
        if "NEXUS_RUN" in script:
            return 0
        return unknown()
    if prog == "infocmp":
        return 0 if cmd[1] in ctr.get("terms", ["xterm-256color", "xterm", "screen-256color"]) else 1
    if prog == "tmux":
        if cmd[1:3] == ["has-session", "-t"]:
            want = cmd[3].lstrip("=")
            return 0 if any(s["name"] == want for s in ctr.get("sessions", [])) else 1
        if cmd[1] == "list-sessions":
            for s in ctr.get("sessions", []):
                out(s["name"] + "\n")
            return 0
        if cmd[1] == "new-session":
            out("[fake attach: %s]\n" % " ".join(cmd[1:]))
            return 0
    if prog == "python3" and cmd[1:2] == ["-c"]:
        home = ctr.get("home")
        if not home:
            err("fake docker: no home for %s\n" % name)
            return 1
        os.makedirs(home, exist_ok=True)
        e = dict(os.environ, HOME=home)
        return subprocess.call([sys.executable, "-I", "-c", cmd[2]], env=e, stdin=0 if interactive else subprocess.DEVNULL)
    if prog in ("claude", "grok"):
        data = sys.stdin.buffer.read() if interactive else b""
        if data:
            out("stdin: %s\n" % data.decode("utf-8", "replace").replace("\n", "|"))
        out(ctr.get("reply", "FAKE-REPLY") + "\n")
        return ctr.get("reply_rc", 0)
    return unknown()


def inspect(world, args):
    w = world.w
    fmt, names = args[2], args[3:]
    rc = 0
    for n in names:
        ctr = w["containers"].get(n)
        if ctr is None:
            err("Error: No such object: %s\n" % n)
            rc = 1
            continue
        if fmt == "{{.State.Running}}":
            out("true\n" if ctr["state"] == "running" else "false\n")
        elif fmt == "{{.Image}}":
            out(ctr["image"] + "\n")
        elif "PROJECTS_ROOT" in fmt and fmt.startswith("{{.Name}}"):
            pr = ctr["env"].get("PROJECTS_ROOT")
            out("/" + n + ("\tPROJECTS_ROOT=" + pr if pr is not None else "") + "\n")
        elif fmt == "{{range .Config.Env}}{{println .}}{{end}}":
            for k, v in sorted(ctr["env"].items()):
                out("%s=%s\n" % (k, v))
            out("\n")
        else:
            return unknown()
    return rc


def image(world, args):
    w = world.w
    if args[0] == "inspect" and args[1] == "-f":
        fmt, ref = args[2], args[3]
        if fmt == "{{.Id}}":
            if ref in w["images"]:
                out(w["images"][ref]["id"] + "\n")
                return 0
            err("Error: No such image: %s\n" % ref)
            return 1
        if fmt == "{{range .RepoTags}}{{.}} {{end}}":
            tags = sorted(r for r, v in w["images"].items() if v["id"] == ref)
            if not tags and ref not in w.get("untagged", []):
                err("Error: No such image: %s\n" % ref)
                return 1
            out("".join(t + " " for t in tags) + "\n")
            return 0
    if args[0] == "rm":
        target = args[-1]
        used = {c["image"] for c in w["containers"].values()}
        if target in w["images"]:
            if w["images"][target]["id"] in used and \
                    sum(1 for v in w["images"].values() if v["id"] == w["images"][target]["id"]) == 1:
                err("Error response from daemon: conflict: unable to remove repository reference\n")
                return 1
            del w["images"][target]
            world.save()
            out("Untagged: %s\n" % target)
            return 0
        if target in w.get("untagged", []):
            if target in used:
                err("Error response from daemon: conflict: unable to delete %s\n" % target[7:19])
                return 1
            w["untagged"].remove(target)
            world.save()
            out("Deleted: %s\n" % target)
            return 0
        err("Error: No such image: %s\n" % target)
        return 1
    return unknown()


def docker(world, args):
    w = world.w
    if not args:
        return unknown()
    sub = args[0]
    if sub == "info":
        if w.get("info_rc"):
            err("Cannot connect to the Docker daemon\n")
            return w["info_rc"]
        if args[1:3] == ["-f", "{{.DockerRootDir}}"]:
            out(w.get("docker_root", os.path.join(SB, "docker-root")) + "\n")
        return 0
    if sub == "compose":
        return compose(world, args[1:])
    if sub == "ps":
        if w.get("ps_rc"):
            err("Cannot connect to the Docker daemon\n")
            return w["ps_rc"]
        for n in sorted(w["containers"]):
            c = w["containers"][n]
            out("%s\t%s\t%s\t%s\n" % (n, c["state"], c["status"], c.get("workdir", "")))
        return 0
    if sub == "inspect" and args[1] == "-f":
        return inspect(world, args)
    if sub == "image":
        return image(world, args[1:])
    if sub == "images" and args[1:3] == ["--format", "{{.Repository}}:{{.Tag}}"]:
        for ref in sorted(w["images"]):
            out(ref + "\n")
        return 0
    if sub == "exec":
        return docker_exec(world, args[1:])
    if sub == "stats":
        names = [a for a in args[1:] if not a.startswith("-") and not a.startswith("{{")]
        for n in names:
            c = w["containers"].get(n, {})
            out("%s\t%s\t%s\n" % (n, c.get("cpu", "0.50%"), c.get("mem", "120MiB / 7.6GiB")))
        return 0
    if sub == "run":
        rest = args[1:]
        if "--entrypoint" in rest:
            err("fake docker: no python3 in the fake images\n")
            return 125
        i = 0
        while i < len(rest) and rest[i].startswith("-"):
            i += 2 if rest[i] in ("--network", "--name", "-u", "-v", "-e", "-w", "--user") else 1
        ref = rest[i]
        if ref not in w["images"]:
            err("Unable to find image '%s' locally\n" % ref)
            return 125
        out(w["images"][ref].get("version", "") + "\n")
        return 0
    if sub == "tag":
        src, dst = args[1], args[2]
        img = w["images"].get(src) or next(({"id": src, "version": v["version"]} for v in w["images"].values()
                                            if v["id"] == src), None)
        if img is None:
            err("Error response from daemon: No such image: %s\n" % src)
            return 1
        w["images"][dst] = dict(img)
        world.save()
        return 0
    if sub == "rm":
        names = [a for a in args[1:] if not a.startswith("-")]
        for n in names:
            if n in w["containers"]:
                if w["containers"][n]["state"] == "running" and "-f" not in args:
                    err("Error response from daemon: cannot remove a running container\n")
                    return 1
                del w["containers"][n]
                out(n + "\n")
        world.save()
        return 0
    return unknown()


# --- the others ------------------------------------------------------------------------------------
def other(world, name, args):
    w = world.w
    if name == "ssh-add":
        rc = w.get("ssh_add_rc", 1)
        if rc == 0:
            out("256 SHA256:fake test@fake (ED25519)\n")
        elif rc == 1:
            out("The agent has no identities.\n")
        else:
            err("Error connecting to agent: Connection refused\n")
        return rc
    if name == "ddev":
        out("ddev version %s\n" % w.get("ddev_version", "v1.24.7"))
        return 0
    if name == "mkcert":
        if args[:1] == ["-install"]:
            rc = w.get("mkcert_rc", 0)
            if rc == 0:
                ca = os.path.join(os.environ["HOME"], ".local", "share", "mkcert")
                os.makedirs(ca, exist_ok=True)
                with open(os.path.join(ca, "rootCA.pem"), "w") as f:
                    f.write("fake CA\n")
                out("The local CA is now installed in the system trust store!\n")
            else:
                err("ERROR: failed to install the local CA\n")
            return rc
        return unknown()
    if name == "df":
        free_gb = w.get("free_gb", 100)
        out("Filesystem 1024-blocks Used Available Capacity Mounted on\n")
        out("/dev/fake 104857600 1 %d 1%% /\n" % (free_gb * 1048576))
        return 0
    if name == "sudo":
        err("sudo: a password is required\n")
        return 1
    return unknown()


def main():
    world = World()
    entry = {"cmd": NAME, "argv": [short(a) for a in ARGS], "cwd": os.getcwd(),
             "env": {k: os.environ[k] for k in ENV_KEYS if k in os.environ},
             "stdin": stdin_kind()}
    held = lock_fds()
    if held:
        entry["lock_fds"] = held
    log(entry)
    try:
        rc = docker(world, ARGS) if NAME == "docker" else other(world, NAME, ARGS)
    finally:
        world.close()
    sys.exit(rc)


main()
