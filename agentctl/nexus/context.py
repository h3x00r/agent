"""What every nexus command starts from: the checkout, where you are, the VM's projects root, the identity
exported for compose, the ssh-agent, and one persona's settings.

As the bash launcher did:
- AGENTS_DIR is the checkout: $AGENTS_DIR if set, else the directory of the launcher itself (through the
  /usr/local/bin symlink); nexus works from there (compose does), and spells it as bash's logical `cd`
  would: by name first (a relative one from HERE, a symlink's path kept, a leading // too), else as given.
- HERE is the directory you ran it from, as your shell's $PWD has it (a symlink's path stays as it is);
  with no PWD and that directory deleted, there is nothing to go by, and nexus stops (bash's set -u did).
- AGENT_UID, AGENT_GID and DOCKER_GID are exported (yours, unless set), so compose and every child see them.
- The VM's projects root: PROJECTS_ROOT in your shell, else in .env, else ~/projects (an error without
  HOME); a persona with a root= of its own uses that. Compose gets the persona's per call; this process
  keeps the shell's.
- SSH_AUTH_SOCK: yours if it is a socket, else the first of the usual agent sockets, else "" (exported).

Pitfalls (each pinned where named):
- AGENTS_DIR by name: .. after a symlink taken lexically, a leading // kept (golden
  nexus/edge-ls-agents-dir-dotdot-link, golden nexus/edge-ls-agents-dir-dslash)
- a relative AGENTS_DIR from the logical HERE (golden nexus/edge-ls-agents-dir-rel-linked-cwd)
- AGENTS_DIR is never an option to cd, nor looked up in CDPATH (golden nexus/edge-ls-agents-dir-option, golden
  nexus/edge-ls-agents-dir-cdpath)
- HERE as $PWD spells it (golden nexus/edge-persona-cwd-spellings)
- a deleted dir and no PWD (golden nexus/edge-persona-pwd-unset-deleted)
- the deleted-dir warning's `cd` quoted with printf_q(), by the locale (golden
  nexus/edge-ls-deleted-cwd-quote)
- the ids, exported (golden nexus/edge-ls-ids-roots)
- an empty PROJECTS_ROOT in the shell counts as unset (golden nexus/edge-ls-dotenv-forms)
- no HOME (golden nexus/edge-ls-home-unset)
- the usual agent sockets, in their order (golden nexus/life-ssh-agent-found)
"""
import os
import stat

from agentctl.adapters import personas_store
from agentctl.core import dotenv
from agentctl.core.errors import NexusError
from agentctl.core.shellcompat import logical, printf_q


def die(msg):
    raise NexusError(msg)


def is_socket(p):
    try:
        return bool(p) and stat.S_ISSOCK(os.stat(p).st_mode)
    except OSError:
        return False


class Context:
    def __init__(self, out, repo):
        self.out = out
        env = os.environ
        given = env.get("AGENTS_DIR") or repo
        self.here = env.get("PWD", "") if env.get("PWD", "").startswith("/") else _getcwd_or("")
        if not self.here:
            die("can't tell which directory this is: it was deleted, and PWD isn't set")
        try:
            os.getcwd()
        except FileNotFoundError:
            out.warn("this shell sits in a deleted directory; leave it, e.g. cd %s" % printf_q(self.here))
        self.agents_dir = _cd(given, self.here)
        self.personas_file = os.path.join(self.agents_dir, "compose.personas.yml")
        env["AGENT_UID"] = env.get("AGENT_UID") or str(os.getuid())
        env["AGENT_GID"] = env.get("AGENT_GID") or str(os.getgid())
        if not is_socket("/var/run/docker.sock"):
            die("no Docker socket at /var/run/docker.sock")
        env["DOCKER_GID"] = env.get("DOCKER_GID") or str(os.stat("/var/run/docker.sock").st_gid)
        dot = os.path.join(self.agents_dir, ".env")
        if env.get("PROJECTS_ROOT"):
            self.vm_root, self.vm_from = env["PROJECTS_ROOT"], "PROJECTS_ROOT in your shell"
        else:
            self.vm_root, self.vm_from = dotenv.get(dot, "PROJECTS_ROOT"), "PROJECTS_ROOT in %s" % dot
        if not self.vm_root:
            if "HOME" not in env:
                die("HOME isn't set, and there is no PROJECTS_ROOT in your shell or %s" % dot)
            self.vm_root = env["HOME"] + "/projects"
            self.vm_from = "the default: no PROJECTS_ROOT in your shell or %s" % dot
        if not is_socket(env.get("SSH_AUTH_SOCK", "")):
            uid = env["AGENT_UID"]
            rt = env.get("XDG_RUNTIME_DIR") or "/run/user/" + uid
            env["SSH_AUTH_SOCK"] = next((s for s in ("/run/user/%s/ssh-agent.sock" % uid, rt + "/openssh_agent",
                                                     rt + "/keyring/ssh", "/run/user/%s/gnupg/S.gpg-agent.ssh" % uid)
                                         if is_socket(s)), "")

    def env_get(self, key):
        return dotenv.get(os.path.join(self.agents_dir, ".env"), key)

    def compose_argv(self):
        return ["docker", "compose", "-p", "agents", "-f", os.path.join(self.agents_dir, "compose.yml"),
                "-f", self.personas_file]

    def personas_lines(self, unterminated=False):
        """The `# persona:` lines (CRLF-safe): as ls reads them, or as top does (unterminated=True)."""
        return personas_store.lines(self.personas_file, unterminated)

    def need_personas(self):
        if not os.path.isfile(self.personas_file):
            die("no personas set up yet: run %s/config-tools" % self.agents_dir)


def _getcwd_or(default):
    try:
        return os.getcwd()
    except OSError:
        return default


def _cd(given, here):
    """bash's `cd GIVEN; echo $PWD`: the path by name first, else GIVEN as it is (then $PWD is physical)."""
    by_name = logical(given, here)
    try:
        os.chdir(by_name)
        return by_name
    except OSError:
        pass
    try:
        os.chdir(given)
        return os.getcwd()
    except OSError:
        die("can't cd to %s" % given)


class Persona:
    """One persona as nexus reads it: its first `# persona:` line, fields looked up as meta_get did."""

    def __init__(self, ctx, name):
        self.name = name
        self.container = "agent-" + name
        self.meta = personas_store.meta(ctx.personas_file, name)
        if self.meta is None:
            names = " ".join(personas_store.names(ctx.personas_file))
            die("no persona '%s' (set up: %s); add it with %s/config-tools" % (name, names, ctx.agents_dir))
        own = self.get("root")
        self.root = (own or ctx.vm_root)
        if self.root.endswith("/"):
            self.root = self.root[:-1]
        self.root_from = "" if own else " (%s)" % ctx.vm_from
        self.flavour = self.get("flavour")
        self.context = self.get("context")
        self.ssh = self.get("ssh")

    def get(self, key):
        return personas_store.meta_get(self.meta, key)

    def has(self, field):
        """Whether the meta line has FIELD (e.g. " flavour=claude "), as bash's [[ $meta == *" … "* ]]."""
        return field in self.meta
