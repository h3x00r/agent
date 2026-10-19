"""What config-tools holds for a run, and saving it. Nothing here asks a question (questions.py does).

A Session reads the checkout (open(): the root refusal, the lock, personas.json or the `# persona:` lines
from before it), checks the VM (preflight()), applies the flags (--remove, --persona, --ssh), and saves:
- stage_env(), then env_vm(): .env staged, with what the VM decides (DDEV_VERSION, AGENT_CPUS);
- prepare(): the settings files checked, each grok persona's config, personas.json and the compose file
  generated from it staged, and all of it checked with `docker compose config`;
- commit(): every staged file in place at once, signals ignored meanwhile, and the notes on what that
  means for the personas (a restart, the move from the `# persona:` lines).
Until commit() nothing is written but the staged files (removed on the way out, staging.cleanup()) and
the projects roots, which are created as they are named.

Pitfalls (each pinned where named):
- the flags are checked before the lock is taken, and --list takes none (golden config/cli-lock)
- INT, TERM and HUP ignored during the commit, put back after it (not pinned)
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
import shutil
import signal

from agentctl.adapters import checks, compose_render, personas_store, ssh
from agentctl.adapters.docker import Docker, socket_gid
from agentctl.configtools import staging
from agentctl.configtools.host import (cpus_ok, ddev_version, nproc, pad, read_bytes, read_or_empty, service_in,
                                       set_kv)
from agentctl.core import dotenv, lock
from agentctl.core.errors import NexusError
from agentctl.core.shellcompat import mkdir_p, printf_q, read_fields
from agentctl.domain import persona as rules
from agentctl.domain.persona import Persona
from agentctl.ui.prompts import Prompter

PERSONAS_FILE = "compose.personas.yml"
JSON_FILE = personas_store.JSON


def die(msg):
    raise NexusError(msg)


class Session:
    def __init__(self, checkout, opts, out):
        self.a = checkout
        self.o = opts
        self.out = out
        self.ask = Prompter(out, yes=opts.yes)
        self.home = os.environ.get("HOME", "")
        self.launcher = os.path.join(checkout, "nexus")
        self.personas = []
        self.loaded = {}          # name → (flavour, context, statusline, root, ssh) as read
        self.staged = {}          # name → its staged env file
        self.gstaged = {}         # name → its staged grok config
        self.backup = set()       # names whose env file is replaced, the old one kept as a .bak
        self.later = []
        self.first = False
        self.unchecked = []
        self.env_new = None       # .env, staged
        self.json_new = self.personas_new = None
        self.root = self.registry = ""

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
            return "  " + " ".join(pad(c, w) for c, w in zip(cells, (31, 8, 8, 10, 20))) + " " + cells[-1]
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
            if p.name not in self.loaded or service_in(compose_render.service(p, managed), old):
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
            if any(service_in(compose_render.service(p, m), old) for m in (True, False)):
                continue
            self.later.append("%s: its service in %s wasn't what personas.json gives (edited by hand?); `nexus %s restart` "
                              "applies the one written now (that ends its tmux sessions)" % (p.name, PERSONAS_FILE, p.name))

    # --- the flags, before anything is asked ---
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

    def apply_flags(self):
        for n in self.o.remove:
            if self.idx(n) is None:
                die("--remove %s: no such persona" % n)
            self.drop(n)
        for spec in self.o.add:
            self.add_flag(spec)
        for spec in self.o.ssh:
            self.ssh_flag(spec)

    # --- reading the checkout, and the VM ---
    def open(self):
        """The root refusal, the lock (not for --list), and the personas as they are."""
        if os.geteuid() == 0:
            die("run this as the VM user who will use the personas, not as root (it asks for sudo only to link the launcher)")
        if not self.o.list and not lock.take(self.a):
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

    def preflight(self):
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
        self.vm_ddev = ddev_version()
        self.default_context = "ddev" if self.vm_ddev else "docker"

    # --- what a save means for the personas ---
    def change_notes(self, old_names):
        """A restart for each persona changed since it was read; for each one gone, what of it stays."""
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

    # --- saving: .env, the env files, the rest, then all of it in place ---
    def stage_env(self):
        """.env staged from the one there (or .env.example): a GROK_TAG of latest goes."""
        self.env_new = staging.stage(".env", ".env" if os.path.exists(".env") else ".env.example")
        if dotenv.get(self.env_new, "GROK_TAG") == "latest":
            set_kv(self.env_new, "GROK_TAG", "")

    def env_vm(self):
        """What the VM decides in .env: ddev's version for the ddev personas, and AGENT_CPUS within nproc."""
        if self.has("ddev"):
            dv = self.vm_ddev or dotenv.get(self.env_new, "DDEV_VERSION")
            if not dv:
                self.warn("ddev isn't installed on this VM: the ddev personas get the newest ddev; install the same on the VM "
                          "(pkg.ddev.com) so both agree, and for mkcert")
            set_kv(self.env_new, "DDEV_VERSION", dv)
        cpus, n = dotenv.get(self.env_new, "AGENT_CPUS"), nproc()
        if not cpus_ok(cpus, n):
            set_kv(self.env_new, "AGENT_CPUS", str(n if n < 4 else 4))

    def stage_env_file(self, n, f):
        """personas/N.env staged from the example of flavour F, for the sign-in questions to fill."""
        tmp = staging.stage("personas/%s.env" % n, "personas/%s.env.example" % f)
        self.staged[n] = tmp
        return tmp

    def settings_problem(self, path, fmt, name):
        rc, out = checks.check(path, fmt)
        if rc == 3:
            return out          # empty: nothing to say, so nothing stops (as in bash)
        if rc:
            self.unchecked.append(name)
        return ""

    def prepare(self):
        """The settings files checked, the grok configs and the personas staged, and all of it through
        `docker compose config`; nothing is in place yet."""
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

        # compose.personas.yml, checked with the rest
        files = ["compose.yml"]
        if self.personas:
            managed = os.path.isfile("personas/claude.managed.json")
            self.json_new = staging.stage(os.path.realpath(JSON_FILE))   # a link's target, renamed onto in its own dir
            os.chmod(self.json_new, 0o644)
            staging.write(self.json_new, personas_store.dumps(self.personas))
            self.personas_new = staging.stage(PERSONAS_FILE)
            os.chmod(self.personas_new, 0o644)
            staging.write(self.personas_new, compose_render.render(self.personas, managed))
            check = staging.stage(PERSONAS_FILE)
            staging.write(check, compose_render.render(self.personas, managed, env_files=self.staged))
            files.append(check)
        env = dict(os.environ)
        env.pop("PROJECTS_ROOT", None)
        env.update(AGENT_UID=str(os.getuid()), AGENT_GID=str(os.getgid()), DOCKER_GID=socket_gid(),
                   SSH_AUTH_SOCK="/dev/null", AGENT_CA_CERTS="")
        rc, err = Docker(self.a).compose_check(self.env_new, files, env)
        if rc:
            die("the new settings don't pass docker compose config, so nothing was written:\n" + err)

    def commit(self):
        """Every staged file in place, all of it or none of it: signals are ignored until it is done."""
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
                staging.commit(tmp, "personas/%s.env" % n, staging.bakname("personas/%s.env" % n) if n in self.backup else None)
            if os.path.isfile("personas/claude.managed.json") and "./personas/claude.managed.json," not in read_or_empty(PERSONAS_FILE):
                for p in self.personas:
                    if p.flavour == "claude" and p.name in self.loaded:
                        self.later.append("%s: it gets personas/claude.managed.json as managed settings now; `nexus %s restart` "
                                          "brings them in" % (p.name, p.name))
            staging.commit(self.env_new, ".env")
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
                staging.replace(self.json_new, os.path.realpath(JSON_FILE), JSON_FILE + ".bak")
                staging.commit(self.personas_new, PERSONAS_FILE)
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
