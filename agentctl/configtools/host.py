"""The small helpers of nexus --config that touch the system or the files: a staged file's KEY=value, what an
env file is for, the VM's git identity, ddev, CPUs and free space, readlink -f, and comparing services.

Pitfalls (each pinned where named):
- AGENT_CPUS against `nproc`, which honours OMP_NUM_THREADS where os.cpu_count() doesn't (golden
  config/cli-env-legacy)
- the free space from `df -Pk`, as the bash's awk read it (golden config/ask-build-low-disk)
"""
import os
import re
import shutil

from agentctl.configtools import staging
from agentctl.core import dotenv, proc
from agentctl.core.shellcompat import mkdir_p


def pad(s, width):
    """printf %-Ns: padded to WIDTH bytes, as bash's printf counts."""
    n = len(s.encode("utf-8", "surrogateescape"))
    return s + " " * (width - n) if n < width else s


def set_kv(path, key, value):
    """set_kv on a staged file: KEY=quote(VALUE) where compose reads it, the file's mode kept."""
    text = staging.read(path)
    mode = os.stat(path).st_mode & 0o7777
    tmp = staging.stage(path)
    staging.write(tmp, dotenv.updated(text, key, "%s=%s" % (key, dotenv.quote(value))))
    os.chmod(tmp, mode)
    os.replace(tmp, path)


_SP = " \t\r\f\v"   # [[:space:]] within a line
_GROK_KEY = re.compile(r"[#%s]*(export[%s]+)?XAI_API_KEY[%s]*=" % (_SP, _SP, _SP))
_CLAUDE_KEY = re.compile(r"[#%s]*(export[%s]+)?(ANTHROPIC_API_KEY|CLAUDE_CODE_OAUTH_TOKEN)[%s]*=" % (_SP, _SP, _SP))


def env_flavour(path):
    """claude or grok from an env file's key lines (commented or not), "" if it can't tell."""
    lines = read_or_empty(path).split("\n")
    if any(_GROK_KEY.match(l) for l in lines):
        return "grok"
    if any(_CLAUDE_KEY.match(l) for l in lines):
        return "claude"
    return ""


def git_config(key):
    r = proc.run(["git", "config", "--global", key])
    return r.out.rstrip("\n") if r.ok else ""


def ddev_version():
    if not shutil.which("ddev"):
        return ""
    r = proc.run(["ddev", "--version"])
    m = re.search(r"[0-9]+\.[0-9]+\.[0-9]+", r.out)
    return m.group(0) if m else ""


def nproc():
    r = proc.run(["nproc"])
    try:
        return int(r.out.strip())
    except ValueError:
        return os.cpu_count() or 1


def cpus_ok(cpus, n):
    if not re.fullmatch(r"[0-9]+(\.[0-9]+)?|\.[0-9]+", cpus):
        return False
    c = float(cpus)
    return not (c <= 0 or c > n)


def free_gb(path):
    r = proc.run(["df", "-Pk", path])
    lines = r.out.split("\n")
    if not r.ok or len(lines) < 2:
        return None
    fields = lines[1].split()
    try:
        return int(int(fields[3]) / 1048576)
    except (IndexError, ValueError):
        return None


def readlink_f(path):
    r = proc.run(["readlink", "-f", path])
    return r.out.rstrip("\n") if r.ok else ""


def read_bytes(path):
    with open(path, "rb") as f:
        return f.read()


def read_or_empty(path):
    try:
        return read_bytes(path).decode("utf-8", "surrogateescape")
    except OSError:
        return ""


def _bare(text):
    """Service text without its comment lines and CRs, for comparing two files' services."""
    return [l for l in text.replace("\r", "").split("\n") if not l.lstrip().startswith("#")]


def service_in(service, old):
    """Whether OLD has SERVICE (one persona's, as compose_render writes it) whole: the same lines, from its
    name to the next service or the volumes, comments and CRs aside."""
    want = [l for l in _bare(service) if l]
    lines = _bare(old)
    if want[0] not in lines:
        return False
    i = lines.index(want[0])
    have = []
    for l in lines[i:]:
        if have and (l.startswith('  "') or l == "volumes:"):
            break
        if l:
            have.append(l)
    return have == want


def mkdir_ok(d):
    try:
        mkdir_p(d)
        return True
    except OSError:
        return False


def ln_ok(src, target):
    return proc.ok(["ln", "-sfn", src, target])
