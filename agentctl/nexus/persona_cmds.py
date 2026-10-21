"""nexus <persona> …: everything but ls, top, upgrade and headless's own work. In the bash launcher's order,
which decides which error wins:

  0. --skip-bypass-permissions or --bypass-permissions, taken out wherever they are before a `--` (and from a
     headless run's arguments): only for an attach or a headless run of a claude persona;
  1. the subcommand: ls, stop/logs/build/pull (compose, exec'd: no checks), start, restart, upgrade,
     headless, or an attach (the default: [SESSION] [-- CMD…]);
  2. preflight: the projects root must be absolute and creatable; a ddev persona gets ~/.ddev and the
     mkcert dir, and its CA if there is one; a persona with ssh= of its own gets no agent;
  3. upgrade, if that was asked;
  4. a restart, or a persona that isn't running: the checks that keep Docker from making root-owned
     files (its ssh key, the settings files its service mounts, its telemetry files and the collector's CA),
     then that compose.personas.yml is what personas.json gives (a hand edit isn't used to create one), the
     ddev telemetry bundle written (the CA, then mkcert's), the warnings (no mkcert CA, an empty or stale
     ssh-agent), then compose up and the Claude defaults; a running one: the projects root it was created with,
     if that isn't the persona's now, and a warning if compose.personas.yml is out of step;
  5. start stops here; otherwise where to start (domain.roots.place), then headless or the attach:
     `docker exec -it … tmux new-session -A`, which takes this process over. With one of the two words, a
     probe in the persona first (mode_env): the session's NEXUS_PERMISSIONS, which the image's claude
     (claude-mode.sh) turns into --permission-mode for every claude started in it.

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
- the two words: both refused together, and for anything but an attach or a headless run, and for grok; every
  other -x is still a session name (golden nexus/perm-flag-refused, golden nexus/perm-dash-session-unchanged)
- a session that is there already runs in the mode it started in: one in the other mode, or whose mode can't be
  read, is refused, never attached; a new one gets NEXUS_PERMISSIONS whatever the persona's own mode, and no -A
  (golden nexus/perm-flag-sessions, golden nexus/perm-flag-main)
- an image without claude-mode.sh is refused, not run in the persona's mode (golden nexus/perm-flag-old-image)
- telemetry: a file its service mounts that isn't there (or a CA that isn't a certificate) is refused before Docker
  makes it a root-owned directory; a ddev claude persona's bundle written at every start, so a later `mkcert
  -install` and a restart reach it; once the container is made, a telemetry file its service no longer names (a
  neutral drop-in left for the old container) removed (golden nexus/telemetry-start)
- telemetry, running in a container made before it changed: a warning at a start, an attach and a headless run, as
  for one that asks: a claude one by its drop-in's mount, and by what that drop-in holds there against the one in
  the checkout (tool details turned off, say: it would go on sending them), a grok one by its token header in the
  container's environment (golden nexus/telemetry-not-restarted)
"""
import os
import shutil

from agentctl.adapters import checks, docker
from agentctl.adapters import telemetry as tele_io
from agentctl.core import proc
from agentctl.core.errors import Exit, NexusError
from agentctl.core.shellcompat import bash_basename, mkdir_p, realpath_e, utf8_locale
from agentctl.domain import telemetry as tele
from agentctl.domain.roots import moved, place
from agentctl.nexus import headless, ls, upgrade
from agentctl.nexus.context import Persona, is_socket

MODES = {"--skip-bypass-permissions": "ask", "--bypass-permissions": "bypass"}
NOT_FOR = ("ls", "stop", "logs", "build", "pull", "start", "restart", "upgrade")
SAYS = {"ask": "asks before running tools", "bypass": "runs its tools without asking"}
ASK_FILE = "personas/claude.ask.json"
ASK_TARGET = "/etc/claude-code/managed-settings.d/16-ask.json"


def die(msg):
    raise NexusError(msg)


def take_mode(args):
    """--skip-bypass-permissions (ask) or --bypass-permissions (bypass) out of ARGS: wherever it is before a
    `--`, and anywhere in a headless run's (whose arguments are never a command). (mode or "", the word, the
    rest.)"""
    headless = next((a for a in args if a not in MODES), "") == "headless"
    mode, word, rest, cmd = "", "", [], False
    for a in args:
        if a == "--" and not headless:
            cmd = True
        if not cmd and a in MODES:
            if mode and MODES[a] != mode:
                die("pick one of --skip-bypass-permissions and --bypass-permissions")
            mode, word = MODES[a], a
            continue
        rest.append(a)
    return mode, word, rest


class Run:
    def __init__(self, ctx, name, args):
        self.ctx, self.out = ctx, ctx.out
        self.p = Persona(ctx, name)
        self.args = list(args)
        self.mode = self.word = ""
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

    def mode_env(self, session):
        """With one of the two words: the NEXUS_PERMISSIONS for the new SESSION (or the headless run: ""), or ""
        when SESSION is there already, in that mode. Refused: a session there in the other mode (or one whose mode
        can't be read), and an image whose claude doesn't read NEXUS_PERMISSIONS. A new session gets it even in the
        persona's own mode, which the probe only guesses (settings it can't see may differ)."""
        p = self.p
        if not self.mode:
            return ""
        rc, out = docker.exec_probe(p.container, "perm_probe.sh", session)
        got = dict(l.partition("=")[::2] for l in out.split("\n") if "=" in l)
        own = got.get("config")
        if rc or own not in SAYS:
            die("can't read %s's permission settings inside it (exit %d); try again, or `nexus %s restart`" % (p.name, rc, p.name))
        if got.get("session") == "?":
            die("can't read the mode session %s runs in: name a new session, nexus %s %s NAME" % (session, p.name, self.word))
        if "session" in got:
            now = got["session"] if got["session"] in SAYS else own
            if now != self.mode and session == "main" and got["session"] not in SAYS:
                die("session main is %s's shell, in its own mode (it %s): name a new session, nexus %s %s NAME, or change "
                    "%s's setting (nexus --config, change)" % (p.name, SAYS[now], p.name, self.word, p.name))
            if now != self.mode:
                how = "it was started with %s" % next(w for w, m in MODES.items() if m == now) \
                    if got["session"] in SAYS else "%s's own setting" % p.name
                die("session %s already %s (%s): end it, or name a new session: nexus %s %s NAME"
                    % (session, SAYS[now], how, p.name, self.word))
            return ""
        if got.get("wrapper") != "yes":
            # build or pull as upgrade does: pull with a registry other than local.
            verb = "build" if (os.environ.get("REGISTRY") or self.ctx.env_get("REGISTRY") or "local") == "local" else "pull"
            die("nothing was started: %s's image was built before nexus had %s.\n"
                "       Run `nexus %s %s`, then `nexus %s restart` (this closes its tmux sessions)."
                % (p.name, self.word, p.name, verb, p.name))
        return self.mode

    def main(self):
        p, a = self.p, self.ctx.agents_dir
        self.mode, self.word, args = take_mode(self.args)
        sub = args[0] if args else ""
        if self.mode and sub in NOT_FOR:
            die("%s is for a session or a headless run, not for %s" % (self.word, sub))
        if self.mode and p.flavour != "claude":
            die("%s is for claude personas: %s is a grok one, which takes its approval mode from personas/grok.toml"
                % (self.word, p.name))
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
            mine = service_text(text, p.name)
            for f in (tele.dropin_file(p.name), tele.env_file(p.name)):
                if f in mine and not os.path.isfile(os.path.join(a, f)):
                    die("%s/%s isn't there: re-run %s/nexus --config, which writes it" % (a, f, a))
            if "./%s," % tele.CA_FILE in mine or "./%s," % tele.DDEV_BUNDLE in mine:
                why = tele_io.ca_problem(os.path.join(a, tele.CA_FILE))
                if why:
                    die("%s/%s %s, and %s sends telemetry to a collector with a CA of its own: give the CA file again "
                        "with %s/nexus --config, then telemetry" % (a, tele.CA_FILE, why, p.name, a))
            why = self.ctx.step_problem()
            if why:
                die(why)
            if "./%s," % tele.DDEV_BUNDLE in mine:
                why = tele_io.write_bundle(a, self.env.get("HOME", ""))
                if why:
                    die("%s's telemetry needs the collector's CA and mkcert's in one file, but nexus %s" % (p.name, why))
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
                tele_leftovers(a, p.name, mine)
                self.claude_defaults()
                return 0
            argv, e = self.compose("up", "-d", p.name)
            rc = proc.run(argv, env=e, capture=False, stdout_to_stderr=True).rc
            if rc:
                return rc
            tele_leftovers(a, p.name, mine)
            self.claude_defaults()
        else:
            old = ls.old_root(p)
            if p.flavour == "claude" and p.permissions == "ask" and ASK_TARGET not in docker.mounts(p.container):
                self.out.warn("%s asks before running tools now, but its container was made before that and doesn't: "
                              "`nexus %s restart` applies it (that ends its tmux sessions)" % (p.name, p.name))
            self.tele_made_before()
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

    def tele_made_before(self):
        """A running container made before the persona's telemetry changed: said, with the restart that applies it. A
        claude one sends while its container mounts the drop-in, as that drop-in was when the container was made (a new
        one is written by a rename: other settings, tool details on or off); a grok one has the token header in its
        environment (with no table in its managed config, which is rewritten in place, a grok stops sending at once)."""
        p, a = self.p, self.ctx.agents_dir
        if p.flavour == "claude" and (p.telemetry or os.path.isfile(os.path.join(a, tele.dropin_file(p.name)))):
            made = tele.DROPIN_TARGET in docker.mounts(p.container)
        elif p.flavour == "grok" and p.telemetry:
            made = tele.GROK_HEADERS[0] in docker.env_keys(p.container)
        else:
            return
        if p.telemetry and not made:
            self.out.warn("%s sends telemetry now, but its container was made before that and doesn't: `nexus %s restart` "
                          "applies it (that ends its tmux sessions)" % (p.name, p.name))
        elif p.telemetry and p.flavour == "claude" and self.dropin_stale():
            self.out.warn("%s's telemetry settings changed after its container was made, and it still sends with the old "
                          "ones: `nexus %s restart` applies the new ones (that ends its tmux sessions)" % (p.name, p.name))
        elif made and not p.telemetry:
            self.out.warn("%s no longer sends telemetry, but its container was made while it did and still sends it: `nexus "
                          "%s restart` stops it (that ends its tmux sessions)" % (p.name, p.name))

    def dropin_stale(self):
        """Whether the running container's telemetry drop-in isn't the one in the checkout (a read that fails: not)."""
        p = self.p
        try:
            with open(os.path.join(self.ctx.agents_dir, tele.dropin_file(p.name)), encoding="utf-8",
                      errors="surrogateescape") as f:
                mine = f.read()
        except OSError:
            return False
        theirs = docker.read_file(p.container, tele.DROPIN_TARGET)
        return theirs is not None and theirs != mine

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
        perm = self.mode_env(session)
        # bash set TERM=dumb itself when it was unset, so ${TERM:-xterm-256color} only caught an empty one.
        term = self.env.get("TERM", "dumb") or "xterm-256color"
        if not proc.ok(["docker", "exec", p.container, "infocmp", term]):
            term = "xterm-256color"
        # A new session in a mode of its own isn't attached to one that is there after all (made since the probe,
        # or named so that tmux, which expands # and \ in -s, found it under another name): tmux refuses it then.
        proc.execvp(["docker", "exec", "-it", "-e", "TERM=" + term, "-w", wd, p.container,
                     "tmux", "new-session"] + ([] if perm else ["-A"]) + ["-s", session, "-c", wd]
                    + (["-e", "NEXUS_PERMISSIONS=" + perm] if perm else []) + cmd)


def service_text(text, name):
    """NAME's service in compose.personas.yml's TEXT: from its name to the blank line after it ("" if none)."""
    start = text.find('\n  "%s":\n' % name)
    if start < 0:
        return ""
    end = text.find("\n\n", start + 1)
    return text[start:] if end < 0 else text[start:end]


def tele_leftovers(a, name, mine):
    """Once NAME's container is made from its service MINE: a telemetry file of its that the service doesn't name any
    more goes (a neutral drop-in, left for the container before, which mounted it)."""
    for f in (tele.dropin_file(name), tele.env_file(name)):
        if f not in mine:
            try:
                os.unlink(os.path.join(a, f))
            except OSError:
                pass
