"""What every nexus command starts from: the checkout, where you are, the VM's projects root, the identity
exported for compose, the ssh-agent, the personas (personas.json), and one persona's settings.

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
- The personas: personas.json, read once and held to its rules (personas_store.load_json). A checkout
  still on compose.personas.yml's `# persona:` lines is told to run config-tools --yes, which moves them;
  one whose personas.json is gone, to put back its .bak. compose.personas.yml is what config-tools
  generated from it: step_problem() renders it again and compares the bytes (a hand edit makes it out of
  step).

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
- personas.json read once, held to its rules, a refusal naming it (golden config/personas-json-refused)
- a checkout still on the `# persona:` lines is told to run config-tools --yes (golden config/migrate-older-layout);
  one with no line at all has no personas (golden nexus/basic-personas-empty)
- step_problem(): the bytes of what personas.json renders, with or without the managed mount (golden
  nexus/life-legacy-statusline)
- step_problem(): with or without the managed mount, as the file comes and goes (golden
  nexus/m4-grok-managed-gone, golden nexus/m4-managed-back); a missing file says so (golden nexus/m4-compose-gone)
"""
import os
import stat

from agentctl.adapters import compose_render, personas_store
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
        self.json_file = os.path.join(self.agents_dir, personas_store.JSON)
        self._personas = None
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

    def need_personas(self):
        w = personas_store.where(self.agents_dir)
        if w == "json":
            return
        if w == "lines":
            die("the personas are still in %s, from before personas.json: run %s/config-tools --yes (it keeps every "
                "setting)" % (self.personas_file, self.agents_dir))
        if w == "lost":
            die(personas_store.lost(self.agents_dir))
        die("no personas set up yet: run %s/config-tools" % self.agents_dir)

    def personas(self, fresh=False):
        """The personas, in their order (the domain's Persona); personas.json read once, or again (FRESH: top's
        next frame)."""
        if self._personas is None or fresh:
            self.need_personas()
            try:
                self._personas = personas_store.load_json(self.json_file)
            except personas_store.BadPersonasJson as e:
                hint = personas_store.restore_hint(self.json_file)
                die("%s %s: fix it%s" % (self.json_file, e, ", or " + hint if hint else ""))
        return self._personas

    def step_problem(self):
        """Why compose.personas.yml isn't what config-tools generates from personas.json, or "" if it is. It may
        have the managed-settings mount or not: personas/claude.managed.json comes and goes with a checkout (a
        git pull, a config-tools run without it), and a start that needs it says so on its own."""
        try:
            with open(self.personas_file, "rb") as f:
                have = f.read()
        except OSError:
            return "%s isn't there: %s/config-tools --yes writes it from personas.json" % (self.personas_file,
                                                                                          self.agents_dir)
        if any(have == compose_render.render(self.personas(), m).encode("utf-8", "surrogateescape") for m in (True, False)):
            return ""
        return ("%s is out of step with personas.json (edited by hand, or written by an older config-tools): "
                "%s/config-tools --yes writes it again from personas.json" % (self.personas_file, self.agents_dir))


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
    """One persona as nexus runs it: its settings from personas.json, and the projects root it gets (its own,
    else the VM's) with where that came from, for the messages."""

    def __init__(self, ctx, name):
        ps = ctx.personas()
        rec = next((p for p in ps if p.name == name), None)
        if rec is None:
            die("no persona '%s' (set up: %s); add it with %s/config-tools"
                % (name, " ".join(p.name for p in ps), ctx.agents_dir))
        self.name, self.container = name, rec.container
        self.flavour, self.context, self.ssh, self.own_root = rec.flavour, rec.context, rec.ssh, rec.root
        self.root = rec.root or ctx.vm_root
        if self.root.endswith("/"):
            self.root = self.root[:-1]
        self.root_from = "" if rec.root else " (%s)" % ctx.vm_from
