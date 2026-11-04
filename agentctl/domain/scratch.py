"""The shared scratch dir: each persona's own <root>/<name>, mounted at the identical path inside it, so a container
it starts (on the VM's Docker daemon) can bind-mount files from there. No I/O here.

The root is NEXUS_SCRATCH_ROOT in your shell, else in .env, else /srv/nexus-scratch, as PROJECTS_ROOT is found (an
empty one counts as unset). nexus --config creates the root once (with sudo when it must: /srv is root's); nexus
creates a persona's dir in it when it starts the persona, mode 755. The compose file names it as
${NEXUS_SCRATCH_ROOT:-/srv/nexus-scratch}/<name>, interpolated by compose, so .env doesn't change its bytes; nexus
passes compose the root it resolved, so the dir it made and the one compose mounts are the same.

Pitfalls (each pinned where named):
- an empty value is unset; an absolute one is cleaned as the daemon cleans a mount's path (repeated and trailing /,
  . and .., a leading //), or a container made with it would never show the mount nexus looks for
  (test_domain.Scratch.test_root)
- the root is mounted as ROOT/NAME:ROOT/NAME and named in commands: absolute, not /, no ':' and no whitespace
  (test_domain.Scratch.test_root_ok)
- the command that makes it is pasted into a shell: the root quoted, as it may hold $, ; or & (test_domain.Scratch.test_root)
"""
import posixpath

from agentctl.core.shellcompat import printf_q
from agentctl.domain.persona import persona_root_ok

DEFAULT = "/srv/nexus-scratch"
VAR = "NEXUS_SCRATCH_ROOT"
RULE = "an absolute directory other than /, without ':' or whitespace"
COMPOSE = "${%s:-%s}" % (VAR, DEFAULT)     # the root as the compose file has it


def root(shell, dotenv):
    """The root: SHELL (NEXUS_SCRATCH_ROOT in the shell), else DOTENV (in .env), else the default. An absolute one
    cleaned as the daemon cleans a mount's destination (normpath keeps a leading //, the daemon doesn't); one that
    isn't stays as it is, but for a trailing /, for root_ok to refuse."""
    r = shell or dotenv or DEFAULT
    if r.startswith("/"):
        return "/" + posixpath.normpath(r).lstrip("/")
    return r[:-1] if len(r) > 1 and r.endswith("/") else r


def root_ok(r):
    return persona_root_ok(r)


def path(root_dir, name):
    """NAME's scratch dir under ROOT_DIR."""
    return root_dir + "/" + name


def install_cmd(root_dir):
    """The command that makes ROOT_DIR yours, as the user runs it on the VM (or makes one that is there yours: install
    -d sets the owner and mode of a directory that is there too). Quoted: root_ok lets $, ; and & through."""
    return "sudo install -d -o $(id -u) -g $(id -g) -m 755 %s" % printf_q(root_dir)


# Where the messages that name the root send a user who can't (or won't) use sudo, or whose root is in the way.
ELSEWHERE = "or point NEXUS_SCRATCH_ROOT (.env, or your shell) at a directory of your own"
NOT_DIR = "move it aside (nexus --config then creates the root), " + ELSEWHERE
