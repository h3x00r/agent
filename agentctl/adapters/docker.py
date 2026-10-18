"""The docker CLI, as the tools call it. Every docker command line lives here, spelt as the bash tools
spelt it: the goldens (tests/golden) record each call, and a fake docker there answers only the forms it
knows (`docker images --format …`, not `docker image ls`).

compose(): `docker compose -p agents -f compose.yml -f compose.personas.yml …`. -p, so an exported
COMPOSE_PROJECT_NAME can't switch to fresh, empty home volumes.

Pitfalls (each pinned where named):
- -p agents (golden config/cli-yes-first-setup)
- the formats' `\\t` is a backslash and a t, for docker to read, not a tab (golden nexus/basic-ls-running)
- probe(): the bytes the bash sent, one final newline dropped
  (test_nexus.Probes.test_the_bytes_the_bash_launcher_sent)
- exec_probe(): 10 s, then its whole process group goes (golden nexus/edge-ls-probe-hang, golden
  nexus/edge-top-probe-hang)
- exec_probe(pipe_stdin=True): an empty pipe on stdin, as inside `while read … < <(…)` (golden
  nexus/basic-ls-running)
- States(errors=…): docker ps's errors go into top's live frame (golden nexus/edge-top-ps-fail)
- roots(): each root as lexical() spells it, the container's leading / dropped (golden
  nexus/edge-ls-ids-roots)
"""
import os
import shutil
import subprocess

from agentctl.core import proc

DEVNULL = subprocess.DEVNULL


class Docker:
    def __init__(self, checkout):
        self.checkout = checkout

    # --- the daemon and its plugin ---
    @staticmethod
    def installed():
        return shutil.which("docker") is not None

    @staticmethod
    def reachable():
        return proc.ok(["docker", "info"])

    @staticmethod
    def has_compose():
        return proc.ok(["docker", "compose", "version"])

    @staticmethod
    def root_dir():
        r = proc.run(["docker", "info", "-f", "{{.DockerRootDir}}"])
        return r.out.rstrip("\n") if r.ok else ""

    # --- images ---
    @staticmethod
    def image_names():
        r = proc.run(["docker", "images", "--format", "{{.Repository}}:{{.Tag}}"])
        return r.out.split("\n")[:-1] if r.out else []

    @staticmethod
    def run_python(image, code, stdin_path):
        """python3 -c CODE in IMAGE, with no network, a file on stdin: (rc, output with stderr)."""
        with open(stdin_path, "rb") as f:
            r = proc.run(["docker", "run", "--rm", "-i", "--network", "none", "--entrypoint", "python3", image,
                          "-c", code], stdin=f, merge=True)
        return r.rc, r.out.rstrip("\n")

    # --- compose ---
    def compose_argv(self, *files, env_file=None):
        argv = ["docker", "compose"]
        if env_file:
            argv += ["--env-file", env_file]
        argv += ["-p", "agents"]
        for f in files:
            argv += ["-f", f]
        return argv

    def compose_check(self, env_file, files, env):
        """`docker compose … --profile all config -q` on staged files: (rc, its output)."""
        r = proc.run(self.compose_argv(*files, env_file=env_file) + ["--profile", "all", "config", "-q"],
                     env=env, merge=True)
        return r.rc, r.out.rstrip("\n")


def socket_gid(path="/var/run/docker.sock"):
    return str(os.stat(path).st_gid)


# --- what nexus asks of the persona containers ---
# Docker's formats take \t as a tab itself: these are spelt with a backslash and a t, as the bash launcher's were.
ROOTS_FORMAT = r'{{.Name}}{{range .Config.Env}}{{if eq (index (split . "=") 0) "PROJECTS_ROOT"}}{{"\t"}}{{.}}{{end}}{{end}}'
PROBES = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "probes")


def probe(name):
    """An in-persona script from agentctl/probes, as the bytes the bash launcher sent (no final newline)."""
    with open(os.path.join(PROBES, name), "rb") as f:
        text = f.read().decode("utf-8")
    return text[:-1] if text.endswith("\n") else text


class States:
    """`docker ps` of the persona containers: each one's state (healthy, unhealthy, starting, running,
    stopped, …), which run, and which this checkout made (compose labels each with its directory)."""

    def __init__(self, checkout, errors=None):
        """docker ps's own errors (the daemon down) go to stderr as they did, or, given ERRORS (a list), into
        it: top's live frame shows them where the bash's `$(top_frame 2>&1)` had them."""
        from agentctl.core.shellcompat import read_fields
        self.state, self.up, self.here = {}, set(), set()
        r = proc.run(["docker", "ps", "-a", "--filter", "label=agent.persona", "--format",
                      r'{{.Names}}\t{{.State}}\t{{.Status}}\t{{.Label "com.docker.compose.project.working_dir"}}'],
                     capture=True if errors is not None else "stdout")
        if errors is not None and r.err:
            errors.append(r.err)
        for line in r.out.split("\n")[:-1] if r.out else []:
            c, st, sp, wd = read_fields(line, "\t", 4)
            if wd == checkout:
                self.here.add(c)
            if st == "running":
                self.up.add(c)
                if "(healthy)" in sp:
                    st = "healthy"
                elif "(unhealthy)" in sp:
                    st = "unhealthy"
                elif "(health: starting)" in sp:
                    st = "starting"
            elif st in ("exited", "created", "dead"):
                st = "stopped"
            self.state[c] = st


def running(container):
    r = proc.run(["docker", "inspect", "-f", "{{.State.Running}}", container])
    return r.out.rstrip("\n") == "true"


def roots(containers):
    """The projects root each container was created with (its PROJECTS_ROOT), lexical; one docker inspect."""
    from agentctl.core.shellcompat import lexical, read_fields
    out = {}
    if not containers:
        return out
    r = proc.run(["docker", "inspect", "-f", ROOTS_FORMAT] + list(containers))
    for line in r.out.split("\n")[:-1] if r.out else []:
        c, kv = read_fields(line, "\t", 2)
        if kv.startswith("PROJECTS_ROOT=") and len(kv) > len("PROJECTS_ROOT="):
            out[c[1:] if c.startswith("/") else c] = lexical(kv[len("PROJECTS_ROOT="):])
    return out


def exec_probe(container, script, *args, timeout=10, pipe_stdin=False):
    """`timeout T docker exec C sh -c SCRIPT sh ARGS…`, stderr dropped: (rc, stdout). pipe_stdin: an empty
    pipe for stdin, as a call inside bash's `while read …; done < <(…)` got (docker exec without -i
    doesn't read it; the goldens record what stdin was)."""
    argv = ["docker", "exec", container, "sh", "-c", probe(script)]
    if args:
        argv += ["sh"] + list(args)
    r = proc.run(argv, timeout=timeout, input=b"" if pipe_stdin else None)
    return r.rc, r.out
