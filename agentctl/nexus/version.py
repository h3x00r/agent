"""nexus --version: the commit this checkout is at, from git, with its date and branch, and whether it has changes
of its own (tracked files only: what nexus --config writes is gitignored). Before the Docker socket and the
personas: it says which nexus this is on a VM where nothing else works yet.

Pitfalls (each pinned where named):
- a checkout that isn't a git clone (a copy, a tarball) says so, exit 1, rather than a version it can't know
  (run.sh "--version in a checkout that isn't a git clone: refused, saying why")
"""
import os

from agentctl.core import proc
from agentctl.core.errors import NexusError
from agentctl.nexus.context import checkout


def version(repo, out):
    a = checkout(repo)

    def git(*args):
        r = proc.run(["git", "-C", a] + list(args))
        return r.out.strip() if r.ok else ""
    top = git("rev-parse", "--show-toplevel")
    if not top or os.path.realpath(top) != os.path.realpath(a):     # none, or a repo it sits in, not its own
        raise NexusError("can't tell the version: %s isn't a git clone of its own" % a)
    line = git("log", "-1", "--date=short", "--format=%h (%cd")
    branch = git("symbolic-ref", "--short", "-q", "HEAD") or "no branch"
    mine = git("status", "--porcelain", "--untracked-files=no")
    out.say("nexus %s, %s)%s" % (line, branch, ", with changes of your own" if mine else ""))
    return 0
