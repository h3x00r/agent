"""config-tools: one run, from the flags to the summary. The steps, in the order the bash tool took them
(the order decides which error wins, and what a stopped run has already done):

  1. refuse root; take the lock (not for --list); read the personas; --list stops here
  2. preflight: Docker, its compose plugin, the ssh-agent, ddev on the VM
  3. the personas: --remove, --persona, --ssh, then (without --yes) the questions
  4. .env staged: the VM's projects root, the registry, the Claude Code version, DDEV_VERSION, AGENT_CPUS
  5. env files staged for the personas without a fitting one (sign-in and git identity)
  6. the settings files checked (JSON for Claude Code, TOML for grok), each grok persona's config staged
  7. compose.personas.yml staged, and everything checked with `docker compose config`
  8. the last questions: the launcher link, mkcert, the images
  9. commit: every staged file in place, signals ignored meanwhile (all of it or none of it)
 10. after the commit, failures are notes: ssh keys made, the launcher linked, mkcert, the images
 11. the summary: the personas, how to start one, and the notes (LATER)

Pitfalls (each pinned where named):
- the flags are checked before the lock is taken, and --list takes none (golden config/cli-lock)
- a run stopped before the commit leaves nothing staged behind (golden config/ask-int-tty)
- INT, TERM and HUP ignored during the commit, put back after it (not pinned)
- AGENT_CPUS against `nproc`, which honours OMP_NUM_THREADS where os.cpu_count() doesn't (golden
  config/cli-env-legacy)
- the free space from `df -Pk`, as the bash's awk read it (golden config/ask-build-low-disk)
- the `# persona:` lines moved into personas.json once, the old file kept as .bak (or .bak.N), and a restart
  asked for each service that came out different (golden config/migrate-config-sh-era, golden
  config/migrate-older-layout, golden config/migrate-keeps-bak)
- a bad line refuses the move and writes nothing (golden config/cli-list-bad-line)
- personas.json in persona order, by a rename under the lock, then the compose file generated from it
  (run.sh "personas.json: the three in the order given, mode 644; the compose file has no # persona: lines")
- a personas.json its rules refuse: nothing done, the file and the backup named (golden config/personas-json-refused)
- the compose file from before personas.json kept first, before anything is written (golden
  config/m4-crash-window)
- a restart asked for only where a service changed: compared whole, comments and CRs aside (golden
  config/m4-configsh-era-grok, golden config/m4-crlf-restart, golden config/m4-tail-edit-no-note)
- personas.json or its .bak not a file: refused before anything is written (golden config/m4-json-dir, golden
  config/m4-json-bak-dir-zero); a link to it is written through to its target (golden config/m4-json-symlink)
- a service edited by hand, written again: that persona is told to restart (golden config/m4-hand-edit-rewritten)
"""
import os
import pwd
import re
import shutil
import signal
import socket
import subprocess

from agentctl.adapters import checks, compose_render, personas_store, ssh
from agentctl.adapters.docker import Docker, socket_gid
from agentctl.configtools import staging
from agentctl.configtools.cli import USAGE
from agentctl.core import dotenv, lock, proc
from agentctl.core.errors import NexusError
from agentctl.core.shellcompat import mkdir_p, printf_q, read_fields
from agentctl.domain import persona as rules
from agentctl.domain.persona import Persona
from agentctl.ui.prompts import Prompter

PERSONAS_FILE = "compose.personas.yml"
JSON_FILE = personas_store.JSON
MIN_FREE_GB = 3

FLAVOURS = ["claude   Claude Code (Anthropic)", "grok     Grok Build (xAI)"]
CONTEXTS = ["ddev     ddev projects: ddev inside, sharing the VM's ddev state and mkcert CA",
            "docker   projects run with docker compose / Dockerfiles: no ddev"]
CONTEXT_Q = "Context: which kind of projects does it work on?"
SL_Q = "Statusline (statusline/: model, context, cost; what nexus top shows) in"
SSH_HOW = ["agent    your ssh-agent, forwarded: your own keys; starting it then needs an agent in your shell",
           "none     no ssh: it commits, and you pull and push from the VM, in the same directory",
           "new      a key of its own, made as personas/NAME.key: add its public half as a deploy key",
           "path     a private key you already have, without a passphrase"]
AUTH = ["login    interactively, inside the persona (a Claude subscription; kept in its home volume)",
        "apikey   an Anthropic API key (Console / API billing)",
        "token    a long-lived token from `claude setup-token` (Pro/Max/Team/Enterprise plan)"]
MENU = ["add      a new persona",
        "change   one persona's flavour, context, statusline, projects root or ssh",
        "remove   one persona (its env file and home volume stay)",
        "done     go on with the rest of the setup"]


def die(msg):
    raise NexusError(msg)


def _pad(s, width):
    """printf %-Ns: padded to WIDTH bytes, as bash's printf counts."""
    n = len(s.encode("utf-8", "surrogateescape"))
    return s + " " * (width - n) if n < width else s


class Setup:
    def __init__(self, checkout, opts, out):
        self.a = checkout
        self.o = opts
        self.out = out
        self.ask = Prompter(out, yes=opts.yes)
        self.home = os.environ.get("HOME", "")
        self.personas = []
        self.loaded = {}          # name → (flavour, context, statusline, root, ssh) as read
        self.staged = {}          # name → its staged env file
        self.gstaged = {}         # name → its staged grok config
        self.later = []
        self.first = False
        self.unchecked = []

    # --- small helpers ---
    def say(self, line=""):
        self.out.say(line)

    def section(self, title):
        if not self.o.yes:
            self.say("")
            self.say("== " + title)

    def warn(self, msg):
        self.out.warn(msg)

    def idx(self, name):
        for i, p in enumerate(self.personas):
            if p.name == name:
                return i
        return None

    def has(self, word):
        return any(word in "%s %s" % (p.flavour, p.context) for p in self.personas)

    def put(self, p):
        i = self.idx(p.name)
        if i is None:
            self.personas.append(p)
        else:
            self.personas[i] = p

    def drop(self, name):
        i = self.idx(name)
        if i is not None:
            del self.personas[i]

    def norm_dir(self, d):
        return rules.norm_dir(d, self.home)

    @staticmethod
    def state(p):
        return (p.flavour, p.context, "yes" if p.statusline else "no", p.root, p.ssh)

    def list(self):
        if not self.personas:
            self.say("  (none yet)")
            return

        def row(*cells):   # printf '  %-31s %-8s %-8s %-10s %-20s %s'
            return "  " + " ".join(_pad(c, w) for c, w in zip(cells, (31, 8, 8, 10, 20))) + " " + cells[-1]
        self.say(row("NAME", "FLAVOUR", "CONTEXT", "STATUSLINE", "PROJECTS ROOT", "SSH"))
        for p in self.personas:
            self.say(row(p.name, p.flavour, p.context, "yes" if p.statusline else "no", p.root or "the VM's",
                         p.ssh or "your agent"))

    def moved_notes(self, old, kept):
        """After the move from the `# persona:` lines: where the old file is, and which persona's service
        came out different (an older layout), so that it needs a restart; one already told to, isn't. A service
        is compared whole, without its comments and CRs (config.sh named itself in them)."""
        if not self.personas:
            self.later.append("personas: none left; %s from before personas.json is kept as %s" % (PERSONAS_FILE, kept))
            return
        self.later.append("personas: moved from %s's `# persona:` lines into %s/%s; the old file is kept as %s"
                          % (PERSONAS_FILE, self.a, JSON_FILE, kept))
        managed = os.path.isfile("personas/claude.managed.json")
        told = "\n".join(self.later)
        for p in self.personas:
            if p.name not in self.loaded or _service_in(compose_render.service(p, managed), old):
                continue
            if "`nexus %s restart`" % p.name not in told:
                self.later.append("%s: its service is written from personas.json now, a little differently; `nexus %s "
                                  "restart` applies it (that ends its tmux sessions)" % (p.name, p.name))

    def rewritten_notes(self, old):
        """compose.personas.yml written again over one that wasn't what personas.json gives (a hand edit; nexus
        warned at an attach): the personas whose service that changes need a restart for it, and are told so
        (one told already, by a change of its own, isn't). The managed mount, there or not, doesn't count."""
        told = "\n".join(self.later)
        for p in self.personas:
            if p.name not in self.loaded or "`nexus %s restart`" % p.name in told:
                continue
            if any(_service_in(compose_render.service(p, m), old) for m in (True, False)):
                continue
            self.later.append("%s: its service in %s wasn't what personas.json gives (edited by hand?); `nexus %s restart` "
                              "applies the one written now (that ends its tmux sessions)" % (p.name, PERSONAS_FILE, p.name))

    # --- 0. the flags, before anything is asked ---
    def check_flags(self):
        for spec in self.o.add:
            self.check_spec(spec)
        for spec in self.o.ssh:
            self.check_ssh_flag(spec)
        if self.o.root:
            self.o.root = self.norm_dir(self.o.root)
            if not rules.root_ok(self.o.root):
                die("--projects-root must be an absolute directory other than /, without ':', not '%s'" % self.o.root)
        if len(self.o.registry) >= 2 and self.o.registry.endswith("/"):
            self.o.registry = self.o.registry[:-1]
        if self.o.registry and not rules.valid_registry(self.o.registry):
            die("--registry must be local or host[:port][/path] (e.g. registry.lan:5000/team), not '%s'" % self.o.registry)
        if self.o.claude and not rules.valid_claude_version(self.o.claude):
            die("--claude-version must be latest, stable or X.Y.Z, not '%s'" % self.o.claude)
        if self.o.link != "none":
            self.o.link = self.norm_dir(self.o.link)
            if not self.o.link.startswith("/"):
                die("--link must be an absolute directory, not '%s'" % self.o.link)
        if self.o.statusline and not self.o.add:
            self.warn("--statusline / --no-statusline only apply to personas given with --persona")

    def check_spec(self, spec):
        n, f, c, r = read_fields(spec, ":", 4)
        if not rules.valid_name(n):
            die("--persona %s: '%s' is not a usable name (%s)" % (spec, n, rules.NAME_RULE))
        if f not in rules.FLAVOURS:
            die("--persona %s: flavour must be claude or grok" % spec)
        if c not in rules.CONTEXTS:
            die("--persona %s: context must be ddev or docker" % spec)
        if r and not rules.persona_root_ok(self.norm_dir(r)):
            die("--persona %s: its projects root must be %s" % (spec, rules.ROOT_RULE))

    def check_ssh_flag(self, spec):
        n, _, k = spec.partition(":")
        if not k or not rules.valid_name(n):
            die("--ssh %s: it takes NAME:agent, NAME:none, NAME:new or NAME:/path/to/key" % spec)
        if k in ("agent", "none"):
            return
        if k == "new":
            if not ssh.have_keygen():
                die("--ssh %s: making a key needs ssh-keygen (openssh-client) on this VM" % spec)
            return
        k = self.norm_dir(k)
        if not rules.key_path_ok(n, k):
            die("--ssh %s: the key's path must be %s" % (spec, rules.KEY_RULE))
        why = ssh.key_problem(k)
        if why:
            die("--ssh %s: %s" % (spec, why))

    # --- 3. the personas ---
    def add_flag(self, spec):
        n, f, c, r = read_fields(spec, ":", 4)
        i = self.idx(n)
        if i is not None:
            old = self.personas[i]
            col, keep, key, sl = old.colour, old.root, old.ssh, old.statusline
        else:
            col, keep, key, sl = rules.next_colour(self.personas), "", "", True
        if self.o.statusline:
            sl = self.o.statusline == "yes"
        if spec.count(":") >= 3:
            if r:
                r = self.norm_dir(r)
                try:
                    mkdir_p(r)
                except OSError:
                    die("--persona %s: can't create the projects root '%s'" % (spec, r))
        else:
            r = keep
        self.put(Persona(n, f, c, col, sl, r, key))

    def ssh_flag(self, spec):
        n, _, k = spec.partition(":")
        i = self.idx(n)
        if i is None:
            die("--ssh %s: no persona %s (add it with --persona)" % (spec, n))
        k = {"agent": "", "none": "none", "new": "personas/%s.key" % n}.get(k) if k in ("agent", "none", "new") \
            else self.norm_dir(k)
        self.personas[i].ssh = k

    def ask_flavour_context(self, f, c):
        return (self.ask.choose("Flavour:", f, FLAVOURS), self.ask.choose(CONTEXT_Q, c, CONTEXTS))

    def ask_root(self, name, current):
        r = current or "-"
        while True:
            r = self.ask.ask("Projects root for %s (-: the VM's, %s)" % (name, self.vm_root_hint), r)
            if r in ("", "-"):
                return ""
            r = self.norm_dir(r)
            if not rules.persona_root_ok(r):
                why = "a projects root must be %s, not '%s'" % (rules.ROOT_RULE, r)
            else:
                try:
                    mkdir_p(r)
                    return r
                except OSError:
                    why = "can't create the projects root '%s'" % r
            if self.o.yes:
                die(why)
            self.say("  " + why)
            r = "-"

    def ask_ssh(self, name, current):
        default = {"": "agent", "none": "none", "personas/%s.key" % name: "new"}.get(current, "path")
        while True:
            how = self.ask.choose("SSH (git over ssh) for %s:" % name, default,
                                  [o.replace("NAME", name) for o in SSH_HOW])
            if how == "agent":
                return ""
            if how == "none":
                return "none"
            if how == "new":
                if ssh.have_keygen():
                    return "personas/%s.key" % name
                self.say("  making a key needs ssh-keygen (openssh-client) on this VM")
                continue
            k = current if default == "path" else ""
            while True:
                k = self.ask.ask("Path of %s's private key (-: back)" % name, k)
                if k in ("", "-"):
                    break
                k = self.norm_dir(k)
                why = ("the key's path must be %s" % rules.KEY_RULE) if not rules.key_path_ok(name, k) \
                    else ssh.key_problem(k)
                if not why:
                    return k
                self.say("  %s's key: %s" % (name, why))
                k = ""

    def add_interactive(self):
        f, c = self.ask_flavour_context("claude", self.default_context)
        while True:
            n = self.ask.ask("Name", rules.suggest_name(self.personas, f, c))
            if rules.valid_name(n) and self.idx(n) is None:
                break
            self.say("  %s; not taken" % rules.NAME_RULE)
        sl = self.ask.yesno("%s %s?" % (SL_Q, n), "y")
        r = self.ask_root(n, "")
        k = self.ask_ssh(n, "")
        self.put(Persona(n, f, c, rules.next_colour(self.personas), sl, r, k))

    def change_interactive(self, name):
        p = self.personas[self.idx(name)]
        f, c = self.ask_flavour_context(p.flavour, p.context)
        sl = self.ask.yesno("%s %s?" % (SL_Q, name), "y" if p.statusline else "n")
        r = self.ask_root(name, p.root)
        k = self.ask_ssh(name, p.ssh)
        self.put(Persona(name, f, c, p.colour, sl, r, k))

    def pick(self, verb):
        if not self.personas:
            self.say("  no persona to " + verb)
            return None
        opts = ["%s %s, %s" % (_pad(p.name, 31), p.flavour, p.context) for p in self.personas]
        n = self.ask.choose("Which persona to %s?" % verb, "-", opts + ["-        none: back to the menu"])
        return None if n == "-" else n

    def persona_menu(self):
        while True:
            self.say("")
            self.list()
            act = self.ask.choose("What next?", "done", MENU)
            if act == "add":
                self.add_interactive()
            elif act == "change":
                n = self.pick("change")
                if n:
                    self.change_interactive(n)
            elif act == "remove":
                n = self.pick("remove")
                if n:
                    self.drop(n)
            else:
                return

    def adopt_interactive(self):
        names = os.listdir("personas") if os.path.isdir("personas") else []
        for file in sorted(f for f in names if f.endswith(".env") and not f.startswith(".")):   # personas/*.env
            path = "personas/" + file
            n = file[:-len(".env")]
            if not os.path.isfile(path) or not rules.valid_name(n) or n in self.loaded or self.idx(n) is not None:
                continue
            f = env_flavour(path)
            if not self.ask.yesno("Found %s%s: set it up as persona %s?" % (path, " (flavour %s)" % f if f else "", n),
                                  "y" if self.first else "n"):
                continue
            if not f:
                f = self.ask.choose("Flavour:", "claude", FLAVOURS)
            c = self.ask.choose(CONTEXT_Q, self.default_context, CONTEXTS)
            sl = self.ask.yesno("%s %s?" % (SL_Q, n), "y")
            self.put(Persona(n, f, c, rules.next_colour(self.personas), sl))

    # --- 5. env files ---
    def write_env_file(self, n, f):
        if self.o.yes:
            self.say("-- personas/%s.env" % n)
        else:
            self.section("%s: sign-in and identity (personas/%s.env)" % (n, n))
        tmp = staging.stage("personas/%s.env" % n, "personas/%s.env.example" % f)
        self.staged[n] = tmp
        if f == "claude":
            auth = self.ask.choose("How does %s sign in to Claude?" % n, "login", AUTH)
            key = ""
            if auth in ("apikey", "token"):
                var = "ANTHROPIC_API_KEY" if auth == "apikey" else "CLAUDE_CODE_OAUTH_TOKEN"
                key = self.ask.secret(var)
                if key:
                    set_kv(tmp, var, key)
            if auth != "login" and not key:
                self.later.append("%s: no key entered; add it to %s/personas/%s.env, or log in inside the persona"
                                  % (n, self.a, n))
        else:
            key = self.ask.secret("XAI_API_KEY")
            if key:
                set_kv(tmp, "XAI_API_KEY", key)
            else:
                self.later.append("%s: add XAI_API_KEY to %s/personas/%s.env" % (n, self.a, n))
        name = self.ask.ask("git author name for %s" % n, git_config("user.name"))
        email = self.ask.ask("git author email for %s" % n, git_config("user.email"))
        if name and email:
            for var, v in (("GIT_AUTHOR_NAME", name), ("GIT_COMMITTER_NAME", name), ("GIT_AUTHOR_EMAIL", email),
                           ("GIT_COMMITTER_EMAIL", email)):
                set_kv(tmp, var, v)
        else:
            self.later.append("%s: set the git identity in %s/personas/%s.env" % (n, self.a, n))
        gh = self.ask.secret("GH_TOKEN for the gh CLI in %s" % n)
        if gh:
            set_kv(tmp, "GH_TOKEN", gh)

    # --- the run ---
    def run(self):
        o = self.o
        self.check_flags()
        if os.geteuid() == 0:
            die("run this as the VM user who will use the personas, not as root (it asks for sudo only to link the launcher)")
        if not o.list and not lock.take(self.a):
            die("another config-tools, or a `nexus <persona> upgrade`, is running in %s; let it finish first" % self.a)
        # personas.json, or (to move them into it) the `# persona:` lines of a compose.personas.yml from before
        # it, which win over a personas.json beside them (personas_store.where); neither: a first setup.
        self.where = personas_store.where(".")
        if self.where == "lost":
            die(personas_store.lost(self.a))
        self.migrating = self.where == "lines"
        self.first = self.where == "none"
        try:
            if self.where == "json":
                self.personas = personas_store.load_json(JSON_FILE)
            else:
                self.personas = personas_store.load(PERSONAS_FILE)
        except personas_store.BadPersonasJson as e:
            hint = personas_store.restore_hint(os.path.join(self.a, JSON_FILE))
            die("%s/%s %s: fix it%s, then run config-tools again" % (self.a, JSON_FILE, e, ", or " + hint if hint else ""))
        except personas_store.BadPersonasFile as e:
            die("%s: persona '%s' has a bad line (edited by hand?): '# persona: %s'; it takes flavour=claude|grok "
                "context=ddev|docker colour=colourN statusline=yes|no [root=/dir] [ssh=none|/path|personas/NAME.key]"
                % (PERSONAS_FILE, e.name, e.line))
        self.loaded = {p.name: self.state(p) for p in self.personas}
        if o.list:
            self.list()
            return 0
        old_names = [p.name for p in self.personas]

        # 2. preflight
        if not Docker.installed():
            die("Docker isn't installed: Docker Engine + compose plugin, https://docs.docker.com/engine/install/")
        if not Docker.reachable():
            die("can't reach Docker as %s: is it running, and are you in the docker group? (sudo usermod -aG docker "
                "\"$USER\", then log in again)" % pwd.getpwuid(os.getuid()).pw_name)
        if not Docker.has_compose():
            die("the docker compose plugin is missing (docker-compose-plugin)")
        rc = ssh.agent_status()
        if rc == 1:
            self.warn("your ssh-agent holds no keys: `ssh-add` before starting a persona that uses it, or git over ssh fails inside")
        elif rc == 2:
            self.warn("no ssh-agent in this shell: starting a persona that uses one needs it (connect with ssh -A, or run one on the VM)")
        vm_ddev = ddev_version()
        self.default_context = "ddev" if vm_ddev else "docker"

        # 3. the personas
        self.say("config-tools: setting up %s%s" % (self.a, " (ddev %s on this VM)" % vm_ddev if vm_ddev else ""))
        hint = o.root or dotenv.get(".env", "PROJECTS_ROOT")
        self.vm_root_hint = "now " + hint if hint else "asked below"
        for n in o.remove:
            if self.idx(n) is None:
                die("--remove %s: no such persona" % n)
            self.drop(n)
        for spec in o.add:
            self.add_flag(spec)
        for spec in o.ssh:
            self.ssh_flag(spec)
        if not o.yes:
            self.section("Personas")
            self.adopt_interactive()
            if not self.personas:
                self.say("No personas yet; the first one:")
                self.add_interactive()
            self.persona_menu()
        if not self.personas:
            self.warn("no personas configured; add some with ./config-tools (or --persona NAME:FLAVOUR:CONTEXT)")
        for p in self.personas:
            if p.name in self.loaded and self.loaded[p.name] != self.state(p):
                self.later.append("%s: changed; `nexus %s restart` applies it (that ends its tmux sessions)" % (p.name, p.name))
        for n in old_names:
            if self.idx(n) is not None:
                continue
            what = "env file" + (", ssh key" if os.path.exists("personas/%s.key" % n) else "")
            files = " ".join(printf_q("%s/personas/%s.%s" % (self.a, n, ext)) for ext in ("env", "key", "key.pub", "grok.toml"))
            self.later.append("%s: removed. Its %s and home volume stay; to delete them:" % (n, what))
            self.later.append("    docker rm -f agent-%s; docker volume rm agents_%s-home; rm -f %s " % (n, n, files))

        # 4. .env
        env_new = staging.stage(".env", ".env" if os.path.exists(".env") else ".env.example")
        if dotenv.get(env_new, "GROK_TAG") == "latest":
            set_kv(env_new, "GROK_TAG", "")
        root = o.root or dotenv.get(env_new, "PROJECTS_ROOT") or self.home + "/projects"
        registry = o.registry or dotenv.get(env_new, "REGISTRY") or "local"
        cv = o.claude or dotenv.get(env_new, "CLAUDE_VERSION") or "latest"
        self.section("VM settings, shared by every persona")
        if not o.yes and not self.first and os.path.exists(".env") and self.ask.yesno(
                "Keep them? projects root %s, image registry %s%s" % (root, registry, ", Claude Code " + cv if self.has("claude") else ""), "y"):
            self.ask.defaults = True
        while True:
            root = self.norm_dir(self.ask.ask("The VM's projects root, for personas without their own (mounted at the same path inside)", root))
            if not rules.root_ok(root):
                why = "the projects root must be an absolute directory other than /, without ':', not '%s'" % root
            else:
                try:
                    mkdir_p(root)
                    break
                except OSError:
                    why = "can't create the projects root '%s'" % root
            if o.yes:
                die(why)
            self.say("  " + why)
            root = self.home + "/projects"
        set_kv(env_new, "PROJECTS_ROOT", root)
        while True:
            registry = self.ask.ask("Image registry (local = build on this VM)", registry)
            if len(registry) >= 2 and registry.endswith("/"):
                registry = registry[:-1]
            registry = registry or "local"
            if rules.valid_registry(registry):
                break
            if o.yes:
                die("the image registry must be local or host[:port][/path], not '%s'" % registry)
            self.say("  local, or a registry like registry.lan:5000 (or registry.lan:5000/team)")
            registry = "local"
        set_kv(env_new, "REGISTRY", registry)
        if self.has("claude"):
            while True:
                cv = self.ask.ask("Claude Code version, for the claude personas: latest | stable | X.Y.Z", cv) or "latest"
                if rules.valid_claude_version(cv):
                    break
                if o.yes:
                    die("the Claude Code version must be latest, stable or X.Y.Z, not '%s'" % cv)
                self.say("  latest, stable or a version like 2.1.302")
                cv = "latest"
            set_kv(env_new, "CLAUDE_VERSION", cv)
        elif o.claude:
            set_kv(env_new, "CLAUDE_VERSION", o.claude)
        if self.has("ddev"):
            dv = vm_ddev or dotenv.get(env_new, "DDEV_VERSION")
            if not dv:
                self.warn("ddev isn't installed on this VM: the ddev personas get the newest ddev; install the same on the VM "
                          "(pkg.ddev.com) so both agree, and for mkcert")
            set_kv(env_new, "DDEV_VERSION", dv)
        self.ask.defaults = False
        cpus, n = dotenv.get(env_new, "AGENT_CPUS"), nproc()
        if not cpus_ok(cpus, n):
            set_kv(env_new, "AGENT_CPUS", str(n if n < 4 else 4))

        # 5. env files
        backup = set()
        for p in self.personas:
            file = "personas/%s.env" % p.name
            if os.path.isfile(file):
                if p.name in self.loaded:
                    if self.loaded[p.name][0] == p.flavour:
                        continue
                    why = "%s changed flavour" % p.name
                else:
                    have = env_flavour(file)
                    if not have or have == p.flavour:
                        self.say("-- %s already exists%s: reusing it" % (file, " (a %s one)" % have if have else ""))
                        continue
                    why = "%s is for a %s persona" % (file, have)
                if o.yes:
                    self.say("-- %s: writing a new %s (the old one is kept as a .bak)" % (why, file))
                elif not self.ask.yesno("%s: write a new %s (the old one is kept as a .bak)?" % (why, file), "y"):
                    continue
                backup.add(p.name)
            self.write_env_file(p.name, p.flavour)

        # 6. settings files, grok configs
        for f in ("personas/claude.json", "personas/claude.managed.json"):
            if self.has("claude") and os.path.isfile(f):
                why = self.settings_problem(f, "json", f)
                if why:
                    die("%s isn't JSON Claude Code can read, so nothing was written: %s" % (f, why))
        if self.has("grok") and os.path.isfile("personas/grok.toml"):
            why = self.settings_problem("personas/grok.toml", "toml", "personas/grok.toml")
            if why:
                die("personas/grok.toml isn't TOML grok can read, so nothing was written (grok would stop on it): " + why)
        for p in self.personas:
            if p.flavour != "grok":
                continue
            tmp = staging.stage("personas/%s.grok.toml" % p.name)
            text = ("# /etc/grok/managed_config.toml in grok persona %s, written by config-tools from personas/grok.toml\n"
                    "# (grok settings for every grok persona here) and, with the statusline on, statusline/grok/settings.toml.\n"
                    "# Edit those and re-run config-tools, not this file. A key in the persona's ~/.grok/config.toml wins.\n"
                    % p.name).encode()
            if os.path.isfile("personas/grok.toml"):
                text += b"\n" + read_bytes("personas/grok.toml") + b"\n"
            if p.statusline and not checks.grok_sets_statusline("personas/grok.toml"):
                text += b"\n" + read_bytes("statusline/grok/settings.toml")
            with open(tmp, "wb") as fh:
                fh.write(text)
            why = self.settings_problem(tmp, "toml", "personas/grok.toml")
            if why:
                die("personas/grok.toml is TOML, but with the statusline config-tools adds for %s it isn't (does it set "
                    "ui.status_line in a way config-tools missed?), so nothing was written; at personas/%s.grok.toml as it "
                    "would be: %s" % (p.name, p.name, why))
            self.gstaged[p.name] = tmp
        for f in sorted(set(self.unchecked)):
            self.warn("couldn't check %s (no python3 for it here, nor a persona image yet): a broken one stops the CLI; "
                      "re-run config-tools after the build to check it" % f)

        # 7. compose.personas.yml, checked with the rest
        files = ["compose.yml"]
        personas_new = None
        if self.personas:
            managed = os.path.isfile("personas/claude.managed.json")
            json_new = staging.stage(os.path.realpath(JSON_FILE))   # a link's target, renamed onto in its own dir
            os.chmod(json_new, 0o644)
            staging.write(json_new, personas_store.dumps(self.personas))
            personas_new = staging.stage(PERSONAS_FILE)
            os.chmod(personas_new, 0o644)
            staging.write(personas_new, compose_render.render(self.personas, managed))
            check = staging.stage(PERSONAS_FILE)
            staging.write(check, compose_render.render(self.personas, managed, env_files=self.staged))
            files.append(check)
        env = dict(os.environ)
        env.pop("PROJECTS_ROOT", None)
        env.update(AGENT_UID=str(os.getuid()), AGENT_GID=str(os.getgid()), DOCKER_GID=socket_gid(),
                   SSH_AUTH_SOCK="/dev/null", AGENT_CA_CERTS="")
        rc, err = Docker(self.a).compose_check(env_new, files, env)
        if rc:
            die("the new settings don't pass docker compose config, so nothing was written:\n" + err)

        # 8. launcher, mkcert, images
        ca = os.path.join(self.home, ".local/share/mkcert/rootCA.pem")
        if o.link != "none" or (o.build and self.personas) or (self.has("ddev") and not os.path.isfile(ca)):
            self.section("Launcher and images")
        if not o.yes and o.link != "none":
            while True:
                o.link = self.ask.ask("Link the launcher as <dir>/nexus (none: skip)", o.link)
                if o.link == "none":
                    break
                o.link = self.norm_dir(o.link)
                if o.link.startswith("/"):
                    break
                self.say("  an absolute directory, e.g. /usr/local/bin or ~/.local/bin; none to skip")
                o.link = "/usr/local/bin"
        link, target = False, ""
        launcher = os.path.join(self.a, "nexus")
        if o.link != "none":
            target = (o.link[:-1] if o.link.endswith("/") else o.link) + "/nexus"
            if readlink_f(target) == launcher:
                pass
            elif os.path.lexists(target) and not self.ask.yesno(
                    "%s already exists (%s); replace it?" % (target, readlink_f(target) or "a file"), "n"):
                self.later.append("launcher: not linked; run it as %s" % launcher)
            else:
                link = True
        mkcert = False
        if self.has("ddev") and not os.path.isfile(ca):
            if shutil.which("mkcert") and not o.yes and self.ask.yesno("Trust ddev's local CA on this VM now (mkcert -install)?", "y"):
                mkcert = True
            else:
                self.later.append("ddev: run `mkcert -install` on this VM (it comes with ddev), then `nexus <persona> restart`, "
                                  "or https://*.ddev.site stays untrusted inside")
        verb = "build" if registry == "local" else "pull"
        images = False
        if o.build and self.personas and self.ask.yesno(
                "%s the images now? (the first build takes a few minutes)" % ("Build" if verb == "build" else "Pull"), "y"):
            images = True
            droot = Docker.root_dir()
            free = free_gb(droot) if droot and os.path.isdir(droot) else None
            if free is not None and free < MIN_FREE_GB:
                self.say("  Only %d GB is free for Docker (%s); a %s from scratch needs about %d GB, and a full disk"
                         % (free, droot, verb, MIN_FREE_GB))
                self.say("  fails it halfway, often as apt's \"At least one invalid signature was encountered\".")
                self.say("  Free space first: `docker system df` shows what's used; `docker builder prune` and "
                         "`docker image prune` free it.")
                if not o.yes and not self.ask.yesno("%s anyway?" % ("Build" if verb == "build" else "Pull"), "n"):
                    images = False
                    cmds, seen = [], set()
                    for p in self.personas:
                        k = "%s-%s" % (p.flavour, p.context)
                        if k in seen:
                            continue
                        seen.add(k)
                        cmds.append("%s %s %s" % (launcher, p.name, verb))
                    self.later.append("images: skipped for lack of disk space; after freeing some, run: " + "; ".join(cmds))

        # 9. commit
        for t in [".", "personas", ".env", JSON_FILE, PERSONAS_FILE] + ["personas/%s.env" % n for n in self.staged] \
                + ["personas/%s.grok.toml" % n for n in self.gstaged]:
            if os.path.exists(t) and not os.access(t, os.W_OK):
                die("can't write %s/%s (check its owner and mode), so nothing was written" % (self.a, t))
        for t in (JSON_FILE, JSON_FILE + ".bak"):
            if os.path.lexists(t) and not os.path.isfile(t):
                die("%s/%s isn't a file (a directory, or a dangling link?), so nothing was written" % (self.a, t))
        handlers = {s: signal.signal(s, signal.SIG_IGN) for s in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)}
        try:
            for n, tmp in self.staged.items():
                staging.commit(tmp, "personas/%s.env" % n, staging.bakname("personas/%s.env" % n) if n in backup else None)
            if os.path.isfile("personas/claude.managed.json") and "./personas/claude.managed.json," not in read_or_empty(PERSONAS_FILE):
                for p in self.personas:
                    if p.flavour == "claude" and p.name in self.loaded:
                        self.later.append("%s: it gets personas/claude.managed.json as managed settings now; `nexus %s restart` "
                                          "brings them in" % (p.name, p.name))
            staging.commit(env_new, ".env")
            for n, tmp in self.gstaged.items():
                t = "personas/%s.grok.toml" % n
                p = self.personas[self.idx(n)]
                if n in self.loaded and not os.path.exists(t):
                    what = "grok settings" + (" and statusline" if p.statusline else "")
                    self.later.append("%s: its %s come through %s now; `nexus %s restart` brings them in" % (n, what, t, n))
                elif n in self.loaded and self.loaded[n] == self.state(p) and not staging.same(tmp, t):
                    self.later.append("%s: its grok settings changed; the next grok started in it reads them" % n)
                staging.commit(tmp, t)
            # A compose.personas.yml that isn't one config-tools generated (the `# persona:` lines from before
            # personas.json, or one made by hand) is kept first, as .bak or .bak.N: before anything is written, so
            # a run cut short still has it. Then personas.json (the one before as personas.json.bak), then the
            # compose file generated from it.
            kept = None
            if os.path.isfile(PERSONAS_FILE) and self.where in ("lines", "none"):
                kept = staging.bakname(PERSONAS_FILE)
                shutil.copy2(PERSONAS_FILE, kept)
            old_compose = read_or_empty(kept) if kept else ""
            before = read_or_empty(PERSONAS_FILE) if self.where == "json" else ""
            if self.personas:
                staging.replace(json_new, os.path.realpath(JSON_FILE), JSON_FILE + ".bak")
                staging.commit(personas_new, PERSONAS_FILE)
            else:
                if os.path.isfile(JSON_FILE):
                    shutil.copy2(JSON_FILE, JSON_FILE + ".bak")
                    os.unlink(os.path.realpath(JSON_FILE))
                    if os.path.lexists(JSON_FILE):     # it was a link: that goes too, not left dangling
                        os.unlink(JSON_FILE)
                if os.path.isfile(PERSONAS_FILE):
                    os.unlink(PERSONAS_FILE)
            if self.migrating:
                self.moved_notes(old_compose, kept)
            elif self.where == "json" and self.personas:
                self.rewritten_notes(before)
            elif kept:
                self.later.append("personas: the %s that was here isn't one config-tools wrote; it is kept as %s"
                                  % (PERSONAS_FILE, kept))
        finally:
            for s, h in handlers.items():
                signal.signal(s, h)

        # 10. after the commit
        # bash's ${HOSTNAME%%.*}: its own HOSTNAME unless one came in from the environment, even an empty one
        host = (os.environ["HOSTNAME"] if "HOSTNAME" in os.environ else socket.gethostname()).split(".", 1)[0]
        for p in self.personas:
            k = p.ssh
            if k != "personas/%s.key" % p.name or os.path.exists(k):
                continue
            if ssh.make_key(k, "%s@%s (nexus)" % (p.name, host)):
                self.say("-- made %s, the ssh key of %s" % (k, p.name))
                then = "then git over ssh works inside"
                if p.name in self.loaded:
                    then = "then `nexus %s restart` (a running one keeps its old key until then)" % p.name
                self.later.append("%s: add its public key on your git host, as a deploy key (a repo's settings; write access to push)" % p.name)
                self.later.append("    or to a machine user's account, %s:" % then)
                self.later.append("    " + read_or_empty(k + ".pub").rstrip("\n"))
            else:
                self.later.append("%s: making its ssh key failed (see above); retry with: ssh-keygen -t ed25519 -N '' -f %s"
                                  % (p.name, printf_q("%s/%s" % (self.a, k))))
        sudo = ["sudo", "-n"] if o.yes else ["sudo"]
        if link:
            if mkdir_ok(o.link) and ln_ok(launcher, target):
                pass
            else:
                self.say("-- linking %s needs sudo" % target)
                if (os.path.isdir(o.link) or proc.run(sudo + ["mkdir", "-p", o.link], capture=False).ok) and \
                        proc.run(sudo + ["ln", "-sfn", launcher, target], capture=False).ok:
                    pass
                else:
                    retry = "sudo ln -sfn %s %s" % (printf_q(launcher), printf_q(target))
                    if not os.path.isdir(o.link):
                        retry = "sudo mkdir -p %s && %s" % (printf_q(o.link), retry)
                    self.later.append("launcher: couldn't link %s; run: %s" % (target, retry))
        if o.link != "none":
            d = o.link[:-1] if o.link.endswith("/") else o.link
            if d not in os.environ.get("PATH", "").split(":"):
                self.later.append("launcher: %s isn't on your PATH" % d)
            a = shutil.which("nexus") or ""
            if a and readlink_f(a) != launcher:
                self.later.append("launcher: `nexus` runs %s, not this launcher; put %s first in PATH, or use %s" % (a, d, launcher))
            old = d + "/agent"
            if os.path.islink(old) and readlink_f(old) == os.path.join(self.a, "agent"):
                try:
                    os.unlink(old)
                    gone = True
                except OSError:
                    gone = proc.run(sudo + ["rm", "-f", old], capture=False).ok
                if gone:
                    self.say("-- removed %s, the launcher's old name (it is nexus now)" % old)
                else:
                    self.later.append("launcher: remove its old link: sudo rm %s" % printf_q(old))
        if mkcert and not proc.run(["mkcert", "-install"], capture=False).ok:
            self.later.append("ddev: `mkcert -install` failed (see above); run it again on this VM, then `nexus <persona> restart`")
        failed = 0
        if images:
            self.say("-- %sing the image of every persona; one that is already up to date takes seconds" % verb)
            seen = set()
            for p in self.personas:
                k = "%s-%s" % (p.flavour, p.context)
                if k in seen:
                    continue
                seen.add(k)
                users = ", ".join(q.name for q in self.personas if "%s-%s" % (q.flavour, q.context) == k)
                self.say("-- %s agent-%s, for %s" % (verb, k, users))
                if not proc.run([launcher, p.name, verb], capture=False).ok:
                    failed = 1
                    self.later.append("images: the %s of agent-%s failed (see above); retry with: %s %s %s"
                                      % (verb, k, launcher, p.name, verb))

        # 11. the summary
        self.say("")
        self.say("Personas:")
        self.list()
        if self.personas:
            self.say("")
            self.say("Start one from a project directory:  cd %s/<project> && nexus %s"
                     % (self.personas[0].root or root, self.personas[0].name))
        for line in self.later:
            self.say(line)
        self.say("Day-to-day use, upgrades and troubleshooting: runbook.md")
        return failed

    def settings_problem(self, path, fmt, name):
        rc, out = checks.check(path, fmt)
        if rc == 3:
            return out          # empty: nothing to say, so nothing stops (as in bash)
        if rc:
            self.unchecked.append(name)
        return ""


# --- helpers that touch the system ---
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


def _bare(text):
    """Service text without its comment lines and CRs, for comparing two files' services."""
    return [l for l in text.replace("\r", "").split("\n") if not l.lstrip().startswith("#")]


def _service_in(service, old):
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


def read_or_empty(path):
    try:
        return read_bytes(path).decode("utf-8", "surrogateescape")
    except OSError:
        return ""


def mkdir_ok(d):
    try:
        mkdir_p(d)
        return True
    except OSError:
        return False


def ln_ok(src, target):
    return proc.ok(["ln", "-sfn", src, target])


def main(checkout, argv, out):
    from agentctl.configtools.cli import parse
    opts = parse(argv)
    if opts.help:
        out.usage(USAGE)
        return 0
    os.chdir(checkout)
    try:
        return Setup(checkout, opts, out).run()
    finally:
        staging.cleanup()
