"""nexus <persona> …: everything but ls, top, upgrade and headless's own work. In the bash launcher's order,
which decides which error wins:

  1. the subcommand: ls, stop/logs/build/pull (compose, exec'd: no checks), start, restart, upgrade,
     headless, or an attach (the default: [SESSION] [-- CMD…]);
  2. preflight: the projects root must be absolute and creatable; a ddev persona gets ~/.ddev and the
     mkcert dir, and its CA if there is one; a persona with ssh= of its own gets no agent;
  3. upgrade, if that was asked;
  4. a restart, or a persona that isn't running: the checks that keep Docker from making root-owned
     files (its ssh key, the settings files its service mounts), then that compose.personas.yml is what
     personas.json gives (a hand edit isn't used to create one), the warnings (no mkcert CA, an empty or
     stale ssh-agent), then compose up and the Claude defaults; a running one: the projects root it was
     created with, if that isn't the persona's now, and a warning if compose.personas.yml is out of step;
  5. start stops here; otherwise where to start (domain.roots.place), then headless or the attach:
     `docker exec -it … tmux new-session -A`, which takes this process over.

Pitfalls (each pinned where named):
- the order (golden nexus/edge-persona-refusal-order)
- stop, logs, build and pull: no checks, and an empty SSH_AUTH_SOCK is /dev/null for compose (golden
  nexus/edge-persona-mgmt-extra)
- the projects root made as mkdir -p makes it, under a tight umask too (golden nexus/edge-persona-umask)
- ~/.ddev and the mkcert dir both tried, then exit 1 with mkdir's own line, ‘…’ in a UTF-8 locale (golden
  nexus/edge-persona-ddev-home-file)
- the defaults merge: 20 s, then 124 with what it printed (golden nexus/edge-persona-defaults-timeout, golden
  nexus/edge-persona-defaults-timeout-child)
- personas/claude.json unreadable: the redirect's error, the merge's exit 1, and the start goes on (golden
  nexus/edge-persona-defaults-unreadable)
- the session name as $(basename …) gives it, trailing newlines gone, then . and : as _ (golden
  nexus/edge-persona-session-newline, golden nexus/life-attach-names)
- no TERM: infocmp is asked for dumb, as bash set it (golden nexus/life-attach-term)
- before creating a container: the key, claude.managed.json, claude.ask.json for one that asks (there, and setting
  a mode that asks), the grok.toml, then compose.personas.yml in step with personas.json (golden
  nexus/edge-persona-refusal-order, golden nexus/life-legacy-claude-json, golden nexus/perm-ask-file-missing)
- out of step at an attach to a running one: a warning, not a refusal (golden nexus/life-legacy-statusline)
- one that asks, running in a container made before it did (no 16-ask.json mount): a warning, at a start and an
  attach (golden nexus/perm-ask-not-restarted)
"""
import os
import shutil

from agentctl.adapters import checks, docker
from agentctl.core import proc
from agentctl.core.errors import Exit, NexusError
from agentctl.core.shellcompat import bash_basename, mkdir_p, realpath_e, utf8_locale
from agentctl.domain.roots import moved, place
from agentctl.nexus import headless, ls, upgrade
from agentctl.nexus.context import Persona, is_socket

ASK_FILE = "personas/claude.ask.json"
ASK_TARGET = "/etc/claude-code/managed-settings.d/16-ask.json"


def die(msg):
    raise NexusError(msg)


class Run:
    def __init__(self, ctx, name, args):
        self.ctx, self.out = ctx, ctx.out
        self.p = Persona(ctx, name)
        self.args = list(args)
        self.env = os.environ

    def compose(self, *args, env=None):
        e = dict(self.env if env is None else env)
        e["PROJECTS_ROOT"] = self.p.root
        return self.ctx.compose_argv() + ["--profile", self.p.name] + list(args), e

    def mgmt(self, *args):
        """stop, logs, build, pull: compose itself, with a placeholder for the ssh-agent none of them mounts."""
        self.env["SSH_AUTH_SOCK"] = self.env.get("SSH_AUTH_SOCK") or "/dev/null"
        argv, e = self.compose(*(list(args) + [self.p.name]))
        proc.execvp(argv, e)

    def personas_text(self):
        try:
            with open(self.ctx.personas_file, "rb") as f:
                return f.read().decode("utf-8", "surrogateescape")
        except OSError:
            return ""


    # --- the defaults merge, after compose up ---
    def claude_defaults(self):
        src = os.path.join(self.ctx.agents_dir, "personas", "claude.json")
        if not (self.p.flavour == "claude" and os.path.isfile(src)):
            return
        try:
            f = open(src, "rb")
        except OSError as e:     # bash's `< "$src"`: the redirect fails, the merge doesn't run, exit 1
            self.out.error_line("nexus: %s: %s" % (src, e.strerror))
            r = proc.Completed(1)
        else:
            with f:
                r = proc.run(["docker", "exec", "-i", self.p.container, "python3", "-c",
                              docker.probe("claude_defaults.py")], stdin=f, timeout=20, merge=True)
        out = r.out.rstrip("\n")
        if r.rc == 0:
            if out:
                self.out.note("%s: %s" % (self.p.name, out))
        elif r.rc == 3:
            self.out.warn("%s: %s" % (self.p.name, out))
        else:
            self.out.warn("%s: the defaults in personas/claude.json couldn't be merged (exit %d): %s" % (self.p.name, r.rc, out))

    def main(self):
        p, a, args = self.p, self.ctx.agents_dir, self.args
        sub = args[0] if args else ""
        restart = start_only = is_upgrade = is_headless = False
        if sub == "ls":
            return ls.ls_persona(self.ctx, p)
        if sub in ("stop", "logs", "build", "pull"):
            extra = {"stop": ["stop"], "logs": ["logs", "-f"], "build": ["build"], "pull": ["pull", "--policy", "always"]}
            self.mgmt(*(extra[sub] + args[1:]))
        if sub == "start":
            start_only = True
        elif sub == "restart":
            restart = True
        elif sub == "upgrade":
            is_upgrade, args = True, args[1:]
        elif sub == "headless":
            is_headless, args = True, args[1:]

        # 2. preflight
        if p.own_root:
            where = "%s's own, in %s; change it with nexus --config" % (p.name, self.ctx.json_file)
        else:
            where = "PROJECTS_ROOT from your shell or %s/.env; fix it, or unset it for ~/projects" % a
        if not (len(p.root) >= 2 and p.root.startswith("/")):
            die("the projects root must be an absolute path, not '%s' (%s)" % (p.root, where))
        try:
            mkdir_p(p.root)
        except OSError:
            die("can't create the projects root %s (%s)" % (p.root, where))
        no_ca = False
        if p.context == "ddev":
            if "HOME" not in self.env:
                die("HOME isn't set, so there is no ~/.ddev or mkcert CA to give %s" % p.name)
            home = self.env["HOME"]
            failed = False
            for d in (home + "/.ddev", home + "/.local/share/mkcert"):   # `mkdir -p` both, then fail as set -e did
                try:
                    mkdir_p(d)
                except OSError as e:
                    q = "\u2018%s\u2019" if utf8_locale() else "'%s'"
                    self.out.error_line("mkdir: cannot create directory %s: %s" % (q % d, e.strerror))
                    failed = True
            if failed:
                raise Exit(1)
            ca = home + "/.local/share/mkcert/rootCA.pem"
            if os.path.isfile(ca):
                self.env["AGENT_CA_CERTS"] = ca
            else:
                self.env["AGENT_CA_CERTS"] = ""
                no_ca = True
        own_ssh, key = p.ssh, ""
        if own_ssh:
            if own_ssh != "none":
                key = own_ssh if own_ssh.startswith("/") else os.path.join(a, own_ssh)
            self.env["SSH_AUTH_SOCK"] = self.env.get("SSH_AUTH_SOCK") or "/dev/null"

        # 3. upgrade: it builds from compose.personas.yml and may recreate the containers, so only from one in step
        if is_upgrade:
            upgrade.Upgrade(self, args).run()
            return 0

        # 4. (re)create, or look at the running one
        old = ""
        if restart or not docker.running(p.container):
            if key and not (os.path.isfile(key) and os.access(key, os.R_OK)):
                die("%s's ssh key %s isn't there; nexus --config makes one (SSH: new), or points it at another, none or your "
                    "agent" % (p.name, key))
            text = self.personas_text()
            if "./personas/claude.managed.json," in text and p.flavour == "claude" \
                    and not os.path.isfile(os.path.join(a, "personas", "claude.managed.json")):
                die("%s/personas/claude.managed.json isn't there: restore it (git checkout personas/claude.managed.json), "
                    "or re-run %s/nexus --config without it" % (a, a))
            if p.flavour == "claude" and p.permissions == "ask":
                why, fix = checks.ask_problem(os.path.join(a, ASK_FILE))
                if why:
                    die("%s/%s %s, and %s asks before running tools: %s" % (a, ASK_FILE, why, p.name, fix))
            if "./personas/%s.grok.toml," % p.name in text and not os.path.isfile(os.path.join(a, "personas", p.name + ".grok.toml")):
                die("%s/personas/%s.grok.toml isn't there: re-run %s/nexus --config, which writes it" % (a, p.name, a))
            why = self.ctx.step_problem()
            if why:
                die(why)
            if no_ca:
                self.out.warn("no mkcert CA on this VM yet — run 'mkcert -install' here, then `nexus %s restart`, or "
                              "https://*.ddev.site stays untrusted inside" % p.name)
            sock = self.env.get("SSH_AUTH_SOCK", "")
            if not own_ssh and is_socket(sock) and shutil.which("ssh-add"):
                rc = proc.run(["ssh-add", "-l"]).rc
                if rc == 1:
                    self.out.warn("the ssh-agent at %s holds no keys (ssh-add -l); git over ssh will fail inside" % sock)
                elif rc == 2:
                    self.out.warn("nothing answers at %s (stale ssh-agent socket); git over ssh will fail inside" % sock)
            if restart:
                argv, e = self.compose("up", "-d", "--force-recreate", p.name)
                rc = proc.run(argv, env=e, capture=False).rc
                if rc:
                    return rc
                self.claude_defaults()
                return 0
            argv, e = self.compose("up", "-d", p.name)
            rc = proc.run(argv, env=e, capture=False, stdout_to_stderr=True).rc
            if rc:
                return rc
            self.claude_defaults()
        else:
            old = ls.old_root(p)
            if p.flavour == "claude" and p.permissions == "ask" and ASK_TARGET not in docker.mounts(p.container):
                self.out.warn("%s asks before running tools now, but its container was made before that and doesn't: "
                              "`nexus %s restart` applies it (that ends its tmux sessions)" % (p.name, p.name))
        if start_only:
            if old:
                self.out.warn(moved(p.name, old, p.root + p.root_from))
            return 0

        # 5. where it starts
        here = self.ctx.here
        pl = place(here, p.root, old, realpath_e, headless=is_headless)
        if old and pl.inside:
            if not os.path.isdir(pl.dir):
                die("%s no longer exists; cd to a project dir" % pl.dir)
            self.out.warn(moved(p.name, old, p.root + p.root_from))
        elif old and is_headless and pl.refused:
            die(moved(p.name, old, p.root + p.root_from, "which %s isn't in" % here))
        else:
            if not pl.outside_root and not os.path.isdir(pl.dir):
                die("%s no longer exists; cd to a project dir" % pl.dir)
            if pl.refused:
                die("headless: %s is outside %s's projects root (%s); cd to a project in it" % (here, p.name, p.root))
            if pl.outside_root and not old:
                self.out.error_line("nexus: %s is outside %s's projects root; starting in %s" % (here, p.name, p.root))
        if is_headless:
            return headless.Headless(self, pl.dir, args).run()
        return self.attach(pl, old, args)

    def attach(self, pl, old, args):
        p, a = self.p, self.ctx.agents_dir
        why = self.ctx.step_problem()
        if why:     # a running one was made from some file: a restart makes it from the one written again
            self.out.warn("%s, then `nexus %s restart`" % (why, p.name))
        session, cmd = "", []
        if args and args[0] != "--":
            session, args = args[0], args[1:]
        if args and args[0] == "--":
            cmd = args[1:]
        session = (session or bash_basename(pl.dir).rstrip("\n")).replace(".", "_").replace(":", "_")   # $(basename …)
        wd = pl.dir
        if not pl.inside:
            if not proc.ok(["docker", "exec", p.container, "tmux", "has-session", "-t", "=" + session]):
                die(moved(p.name, old, p.root + p.root_from, "which %s isn't in" % self.ctx.here)
                    + " (until then, a session it runs can be attached to by name: nexus %s ls lists them)" % p.name)
            self.out.warn(moved(p.name, old, p.root + p.root_from,
                                "which %s isn't in; attaching to its session %s" % (self.ctx.here, session)))
            wd = old
        # bash set TERM=dumb itself when it was unset, so ${TERM:-xterm-256color} only caught an empty one.
        term = self.env.get("TERM", "dumb") or "xterm-256color"
        if not proc.ok(["docker", "exec", p.container, "infocmp", term]):
            term = "xterm-256color"
        proc.execvp(["docker", "exec", "-it", "-e", "TERM=" + term, "-w", wd, p.container,
                     "tmux", "new-session", "-A", "-s", session, "-c", wd] + cmd)
