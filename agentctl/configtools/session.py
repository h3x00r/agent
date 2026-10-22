"""What `nexus --config` holds for a run, and saving it. Nothing here asks a question (questions.py does).

A Session reads the checkout (open(): the root refusal, the lock, personas.json or the `# persona:` lines
from before it, the telemetry settings in .env), checks the VM (preflight()), applies the flags (--remove,
--persona, --ssh, --permissions, the --telemetry ones), and saves:
- stage_env(), then env_vm(): .env staged, with what the VM decides (DDEV_VERSION, AGENT_CPUS);
- prepare(): the settings files checked, each grok persona's config, the telemetry files of the personas that
  send it (and the collector's CA, copied in), personas.json and the compose file generated from it staged,
  and all of it checked with `docker compose config`;
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
- a grok config whose comments alone changed (they named config-tools) brings no note (golden config/cfg-legacy-grok)
- a persona that asks: personas/claude.ask.json there, readable and setting a mode that asks, or nothing is
  written (golden config/perm-file-missing)
- a persona that sends telemetry with no collector set, an address or token .env holds that the options would
  refuse, a CA that isn't a certificate (or holds a key), a claude persona with a NODE_EXTRA_CA_CERTS of its own the
  drop-in would replace, or personas/grok.toml with a [telemetry] of its own: nothing written (golden
  config/telemetry-refused)
- a CA file named in .env by hand: checked before it is copied in, whoever sends (golden config/telemetry-hand-edits)
- the telemetry files hold the token: mode 600; a claude persona's written by a rename, so a running container keeps
  the drop-in it was made with until the restart asked for; when it stops sending (or turns grok), a neutral
  drop-in rather than none, as its container still mounts the file (nexus removes it when it next creates the
  container); a grok persona's env file removed; .env made 600 once it holds the token (golden
  config/telemetry-flags)
- a restart asked of a persona that sends when a file its container reads at creation changed (its drop-in, its env
  file, the CA's mount, a ddev one's CA), and only then: settings changed by hand in .env too; a grok one whose
  [telemetry] table alone changed reads it at its next grok (golden config/telemetry-flags, golden
  config/telemetry-hand-edits)
- a removed persona's telemetry files named among what stays only when they are there (golden
  config/telemetry-flags)
- tool details and tool content (tele.EXTRAS), each on its own: for every persona that sends
  (TELEMETRY_TOOL_DETAILS=on, set with all:on, which clears each persona's own) or per persona ("tool_details":
  true, cleared when it stops sending); none once the collector is off (emptied by hand too); the all:… flags before
  the NAME:… ones, whatever their order; --tool-details NAME:on for one that doesn't send, all:on with no collector,
  NAME:off under all, and a TELEMETRY_TOOL_DETAILS that isn't on or off: refused, nothing written (golden
  config/telemetry-details-flags, golden config/telemetry-content-flags, golden config/telemetry-hand-edits)
- a persona that comes to send tool content without tool details is told so once: a claude one that what Claude
  Code writes to files then stays out, a grok one that grok sends its commands all the same (golden
  config/telemetry-content-flags)
"""
import json
import os
import pwd
import shutil
import signal

from agentctl.adapters import checks, compose_render, personas_store, ssh
from agentctl.adapters import telemetry as tele_io
from agentctl.adapters.docker import Docker, socket_gid
from agentctl.configtools import staging
from agentctl.configtools.host import (cpus_ok, ddev_version, nproc, pad, read_bytes, read_or_empty, service_in,
                                       set_kv, short_host)
from agentctl.core import dotenv, lock
from agentctl.core.errors import NexusError
from agentctl.core.shellcompat import mkdir_p, printf_q, read_fields
from agentctl.domain import persona as rules
from agentctl.domain import telemetry as tele
from agentctl.domain.persona import Persona
from agentctl.ui.prompts import Prompter

PERSONAS_FILE = "compose.personas.yml"
JSON_FILE = personas_store.JSON
TELE_KEYS = (("endpoint", "TELEMETRY_ENDPOINT"), ("token", "TELEMETRY_TOKEN"), ("ca", "TELEMETRY_CA")) \
    + tuple((x.key, x.env) for x in tele.EXTRAS)


def die(msg):
    raise NexusError(msg)


def _same_settings(a, b):
    """Whether two grok configs hold the same settings: their lines but the comments (which named config-tools,
    in the header and in the tracked files copied in, before it was `nexus --config`)."""
    def settings(path):
        return [l for l in read_or_empty(path).split("\n") if not l.lstrip().startswith("#")]
    return settings(a) == settings(b)


class Session:
    def __init__(self, checkout, opts, out):
        self.a = checkout
        self.o = opts
        self.out = out
        self.ask = Prompter(out, yes=opts.yes)
        self.home = os.environ.get("HOME", "")
        self.launcher = os.path.join(checkout, "nexus")
        self.personas = []
        self.loaded = {}          # name → (flavour, context, statusline, root, ssh, permissions, telemetry) as read
        self.staged = {}          # name → its staged env file
        self.gstaged = {}         # name → its staged grok config
        self.backup = set()       # names whose env file is replaced, the old one kept as a .bak
        self.later = []
        self.first = False
        self.unchecked = []
        self.env_new = None       # .env, staged
        self.json_new = self.personas_new = None
        self.root = self.registry = self.claude_version = ""
        self.warned = set()       # the warnings said once a run, however often it saves
        self.wrote = []           # the files the last commit changed
        self.tele = {k: "" for k, _ in TELE_KEYS}   # the telemetry settings: as .env has them, or as given
        self.tele_saved = {}      # … as last saved: a change asks the personas that send for a restart
        self.ca_from = ""         # a CA file to copy in as personas/telemetry-ca.pem at the next save
        self.tstaged = {}         # name → its staged telemetry file (a claude drop-in, or a grok env file)
        self.tneutral = {}        # a drop-in no persona sends with any more → its neutral replacement, staged
        self.tgone = []           # the grok telemetry env files no persona uses any more: removed at the commit
        self.tele_newly = False   # whether a persona sends now that didn't when it was read
        self.tele_check = False   # whether to try the collector after the save: what it is sent with changed
        self.extras_saved = {}    # name → (flavour, the extras it sends) as last saved: a note when that changes
        self.ca_new = None        # the CA's copy, staged

    # --- small helpers ---
    def say(self, line=""):
        self.out.say(line)

    def section(self, title):
        if not self.o.yes:
            self.say("")
            self.say("== " + title)

    def warn(self, msg):
        self.out.warn(msg)

    def warn_once(self, msg):
        if msg not in self.warned:
            self.warned.add(msg)
            self.warn(msg)

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
        return (p.flavour, p.context, "yes" if p.statusline else "no", p.root, p.ssh, p.permissions,
                "yes" if p.telemetry else "no")

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
        asking = [p.name for p in self.personas if p.permissions == "ask"]
        if asking:
            self.say("  asks before running tools (not bypassPermissions): " + ", ".join(asking))
        sending = [p.name for p in self.personas if p.telemetry]
        if sending:
            self.say("  sends telemetry to %s: %s" % (self.tele["endpoint"] or "no collector (TELEMETRY_ENDPOINT is empty)",
                                                      ", ".join(sending)))
        for x in tele.EXTRAS:
            names = [p.name for p in self.personas if tele.extra_on(p, x, self.all_on(x))]
            if names:
                self.say("  sends %s too (%s): %s" % (x.name, x.what, ", ".join(names)))

    def restarts(self):
        """The personas this run's notes tell to restart: `nexus NAME restart` is the mark the notes go by."""
        told = "\n".join(self.later)
        return [p.name for p in self.personas if "`nexus %s restart`" % p.name in told]

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
        (one told already, by a change of its own, isn't). The managed mount, there or not, doesn't count, nor
        the telemetry CA's (whether .env names a CA is told apart by the telemetry notes)."""
        told = "\n".join(self.later)
        for p in self.personas:
            if p.name not in self.loaded or "`nexus %s restart`" % p.name in told:
                continue
            if any(service_in(compose_render.service(p, m, ca), old) for m in (True, False) for ca in (True, False)):
                continue
            self.later.append("%s: its service in %s wasn't what personas.json gives (edited by hand?); `nexus %s restart` "
                              "applies the one written now (that ends its tmux sessions)" % (p.name, PERSONAS_FILE, p.name))

    # --- the flags, before anything is asked ---
    def check_flags(self):
        for spec in self.o.add:
            self.check_spec(spec)
        for spec in self.o.ssh:
            self.check_ssh_flag(spec)
        for spec in self.o.permissions:
            n, _, v = spec.partition(":")
            if not rules.valid_name(n) or v not in rules.PERMISSIONS:
                die("--permissions %s: it takes NAME:ask or NAME:bypass" % spec)
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
        self.check_tele_flags()

    def check_tele_flags(self):
        o = self.o
        for spec in o.telemetry:
            n, _, v = spec.partition(":")
            if not rules.valid_name(n) or v not in ("on", "off"):
                die("--telemetry %s: it takes NAME:on or NAME:off" % spec)
        for x in tele.EXTRAS:
            for spec in getattr(o, x.field):
                n, _, v = spec.partition(":")
                if not (n == "all" or rules.valid_name(n)) or v not in ("on", "off"):
                    die("%s %s: it takes NAME:on, NAME:off, all:on or all:off" % (x.flag, spec))
        if o.tele_endpoint and o.tele_endpoint != "none":
            o.tele_endpoint = tele.norm_endpoint(o.tele_endpoint)
            if not tele.valid_endpoint(o.tele_endpoint):
                die("--telemetry-endpoint takes %s, or none; not '%s'" % (tele.ENDPOINT_RULE, o.tele_endpoint))
        o.tele_token = None
        if o.tele_token_file:
            f = self.norm_dir(o.tele_token_file)
            if not f.startswith("/"):
                die("--telemetry-token-file %s: it takes an absolute path (or ~/…)" % o.tele_token_file)
            try:
                with open(f, "rb") as fh:
                    text = fh.read().decode("utf-8", "surrogateescape")
            except OSError as e:
                die("--telemetry-token-file %s: can't read it (%s)" % (f, e.strerror))
            words = text.split()
            if len(words) > 1 or (words and not tele.valid_token(words[0])):
                die("--telemetry-token-file %s: it takes a file holding the token alone (%s)" % (f, tele.TOKEN_RULE))
            o.tele_token = words[0] if words else ""
        if o.tele_ca and o.tele_ca != "none":
            o.tele_ca = self.norm_dir(o.tele_ca)
            if not o.tele_ca.startswith("/"):
                die("--telemetry-ca %s: it takes an absolute path (or ~/…), or none" % o.tele_ca)
            why = tele_io.ca_problem(o.tele_ca)
            if why:
                die("--telemetry-ca %s %s" % (o.tele_ca, why))

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
            col, keep, key, sl, perm, tel = old.colour, old.root, old.ssh, old.statusline, old.permissions, old.telemetry
            det, con = old.tool_details, old.tool_content
        else:
            col, keep, key, sl, perm, tel = rules.next_colour(self.personas), "", "", True, "bypass", False
            det = con = False
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
        self.put(Persona(n, f, c, col, sl, r, key, perm if f == "claude" else "bypass", tel, det, con))

    def ssh_flag(self, spec):
        n, _, k = spec.partition(":")
        i = self.idx(n)
        if i is None:
            die("--ssh %s: no persona %s (add it with --persona)" % (spec, n))
        k = {"agent": "", "none": "none", "new": "personas/%s.key" % n}.get(k) if k in ("agent", "none", "new") \
            else self.norm_dir(k)
        self.personas[i].ssh = k

    def permissions_flag(self, spec):
        n, _, v = spec.partition(":")
        i = self.idx(n)
        if i is None:
            die("--permissions %s: no persona %s (add it with --persona)" % (spec, n))
        if v == "ask" and self.personas[i].flavour != "claude":
            die("--permissions %s: %s is a grok persona, and grok takes its approval mode from personas/grok.toml" % (spec, n))
        self.personas[i].permissions = v

    def telemetry_flag(self, spec):
        n, _, v = spec.partition(":")
        i = self.idx(n)
        if i is None:
            die("--telemetry %s: no persona %s (add it with --persona)" % (spec, n))
        p = self.personas[i]
        p.telemetry = v == "on"
        for x in tele.EXTRAS:
            setattr(p, x.field, getattr(p, x.field) and p.telemetry)

    def all_on(self, x):
        """Whether every persona that sends telemetry sends the extra X too (TELEMETRY_TOOL_DETAILS=on, say)."""
        return self.tele[x.key] == "on"

    def extras_on(self, p):
        """The extras persona P sends, in tele.EXTRAS order."""
        return tuple(x for x in tele.EXTRAS if tele.extra_on(p, x, self.all_on(x)))

    def extra_flag(self, x, spec):
        """--tool-details SPEC (or --tool-content: X), checked against the personas as the flags before it left them."""
        n, _, v = spec.partition(":")
        if n == "all":
            if v == "on" and not self.tele["endpoint"]:
                die("%s all:on: nothing was written, because there is no collector, so no persona can send %s. To set "
                    "one up:\n        nexus --config --yes --telemetry-endpoint URL %s all:on" % (x.flag, x.name, x.flag))
            self.tele[x.key] = "on" if v == "on" else ""
            for p in self.personas:      # all of them or none: a persona's own says nothing more
                setattr(p, x.field, False)
            return
        i = self.idx(n)
        if i is None:
            die("%s %s: no persona %s (add it with --persona)" % (x.flag, spec, n))
        p = self.personas[i]
        if v == "on" and not p.telemetry:
            die("%s %s: nothing was written, because %s doesn't send telemetry, so it can't send %s. To send both:\n"
                "        nexus --config --yes --telemetry %s:on %s %s:on" % (x.flag, spec, n, x.name, n, x.flag, n))
        if v == "off" and self.all_on(x):
            rest = "".join(" %s %s:on" % (x.flag, q.name) for q in self.personas if q.telemetry and q.name != n)
            die("%s %s: nothing was written, because every persona that sends telemetry sends %s now (%s=on in %s/.env). "
                "To leave %s out, turn it off for all%s:\n        nexus --config --yes %s all:off%s"
                % (x.flag, spec, x.name, x.env, self.a, n,
                   ", then turn %s on for each of the others" % x.name if rest else "", x.flag, rest))
        if v == "on" and self.all_on(x):
            self.later.append("%s: it sends %s already, as every persona that sends telemetry does (%s=on)"
                              % (n, x.name, x.env))
        setattr(p, x.field, v == "on" and not self.all_on(x))

    def tele_off(self):
        """No collector any more: no persona sends (nor any extra), and the ones that did are named."""
        self.tele["endpoint"] = ""
        for x in tele.EXTRAS:
            self.tele[x.key] = ""
        off = [p.name for p in self.personas if p.telemetry]
        for p in self.personas:
            p.telemetry = False
            for x in tele.EXTRAS:
                setattr(p, x.field, False)
        if off:
            self.later.append("telemetry: off; %s no longer send%s it" % (", ".join(off), "s" if len(off) == 1 else ""))

    def ca_changed(self):
        """Whether a CA was given whose bytes aren't those of the one copied in (or none is there)."""
        return bool(self.ca_from) and not staging.same(self.ca_from, tele.CA_FILE)

    def apply_flags(self):
        for n in self.o.remove:
            if self.idx(n) is None:
                die("--remove %s: no such persona" % n)
            self.drop(n)
        for spec in self.o.add:
            self.add_flag(spec)
        for spec in self.o.ssh:
            self.ssh_flag(spec)
        for spec in self.o.permissions:
            self.permissions_flag(spec)
        o = self.o
        if o.tele_endpoint == "none":
            self.tele_off()
        elif o.tele_endpoint:
            self.tele["endpoint"] = o.tele_endpoint
        if o.tele_token is not None:
            self.tele["token"] = o.tele_token
        if o.tele_ca == "none":
            self.tele["ca"], self.ca_from = "", ""
        elif o.tele_ca:
            self.tele["ca"], self.ca_from = tele.CA_FILE, o.tele_ca
        for spec in o.telemetry:
            self.telemetry_flag(spec)
        # all:… first, then NAME:…, whatever their order: all:off then c2:on is c2 alone, either way round.
        for x in tele.EXTRAS:
            for spec in sorted(getattr(o, x.field), key=lambda spec: not spec.startswith("all:")):
                self.extra_flag(x, spec)

    # --- reading the checkout, and the VM ---
    def open(self):
        """The root refusal, the lock (not for --list), and the personas as they are."""
        if os.geteuid() == 0:
            die("run this as the VM user who will use the personas, not as root (it asks for sudo only to link the launcher)")
        if not self.o.list and not lock.take(self.a):
            die("another `nexus --config`, or a `nexus <persona> upgrade`, is running in %s; let it finish first" % self.a)
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
            die("%s/%s %s: fix it%s, then run nexus --config again" % (self.a, JSON_FILE, e, ", or " + hint if hint else ""))
        except personas_store.BadPersonasFile as e:
            die("%s: persona '%s' has a bad line (edited by hand?): '# persona: %s'; it takes flavour=claude|grok "
                "context=ddev|docker colour=colourN statusline=yes|no [root=/dir] [ssh=none|/path|personas/NAME.key]"
                % (PERSONAS_FILE, e.name, e.line))
        self.loaded = {p.name: self.state(p) for p in self.personas}
        self.read_tele()
        self.extras_saved = {p.name: (p.flavour, self.extras_on(p)) for p in self.personas}

    def read_tele(self):
        """The telemetry settings as .env has them, and so what is saved, for the restart notes. A TELEMETRY_CA that
        names a file of its own (set by hand) is that file to copy in, as --telemetry-ca's is."""
        self.tele = {k: dotenv.get(".env", var) for k, var in TELE_KEYS}
        self.tele_saved, self.ca_from = dict(self.tele), ""
        self.tele["endpoint"] = tele.norm_endpoint(self.tele["endpoint"])
        for x in tele.EXTRAS:     # off is empty; no collector (emptied by hand, say): none either, as with none
            if not self.tele["endpoint"] or self.tele[x.key] == "off":
                self.tele[x.key] = ""
        if self.tele["ca"] and self.tele["ca"] != tele.CA_FILE:
            self.tele["ca"], self.ca_from = tele.CA_FILE, self.norm_dir(self.tele["ca"])

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
        self.tele_newly = any(p.telemetry and (p.name not in self.loaded or self.loaded[p.name][6] != "yes")
                              for p in self.personas)
        for p in self.personas:
            if p.name in self.loaded and self.loaded[p.name] != self.state(p):
                self.later.append("%s: changed; `nexus %s restart` applies it (that ends its tmux sessions)" % (p.name, p.name))
        for n in old_names:
            if self.idx(n) is not None:
                continue
            what = "env file" + (", ssh key" if os.path.exists("personas/%s.key" % n) else "")
            exts = ["env", "key", "key.pub", "grok.toml"] + [e for e in ("telemetry.json", "telemetry.env")
                                                            if os.path.exists("personas/%s.%s" % (n, e))]
            files = " ".join(printf_q("%s/personas/%s.%s" % (self.a, n, ext)) for ext in exts)
            self.later.append("%s: removed. Its %s and home volume stay; to delete them:" % (n, what))
            self.later.append("    docker rm -f agent-%s; docker volume rm agents_%s-home; rm -f %s " % (n, n, files))

    # --- the shared settings, as .env has them ---
    def read_shared(self):
        """The projects root, the registry and the Claude Code version, as .env has them (or their defaults)."""
        self.root = dotenv.get(".env", "PROJECTS_ROOT") or self.home + "/projects"
        self.registry = dotenv.get(".env", "REGISTRY") or "local"
        self.claude_version = dotenv.get(".env", "CLAUDE_VERSION") or "latest"
        self.vm_root_hint = "now " + self.root

    def env_flags(self):
        """--projects-root, --registry and --claude-version into the staged .env, without a question."""
        o = self.o
        if o.root:
            try:
                mkdir_p(o.root)
            except OSError:
                die("can't create the projects root '%s'" % o.root)
            set_kv(self.env_new, "PROJECTS_ROOT", o.root)
            self.root, self.vm_root_hint = o.root, "now " + o.root
        if o.registry:
            set_kv(self.env_new, "REGISTRY", o.registry)
            self.registry = o.registry
        if o.claude:
            set_kv(self.env_new, "CLAUDE_VERSION", o.claude)
            self.claude_version = o.claude

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
                self.warn_once("ddev isn't installed on this VM: the ddev personas get the newest ddev; install the same on "
                               "the VM (pkg.ddev.com) so both agree, and for mkcert")
            set_kv(self.env_new, "DDEV_VERSION", dv)
        cpus, n = dotenv.get(self.env_new, "AGENT_CPUS"), nproc()
        if not cpus_ok(cpus, n):
            set_kv(self.env_new, "AGENT_CPUS", str(n if n < 4 else 4))
        self.env_tele()

    def env_tele(self):
        """The telemetry settings into the staged .env: each that changed (an empty one only over one there)."""
        for k, var in TELE_KEYS:
            if dotenv.get(self.env_new, var) != self.tele[k]:
                set_kv(self.env_new, var, self.tele[k])

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
        asking = [p.name for p in self.personas if p.permissions == "ask"]
        why, fix = checks.ask_problem("personas/claude.ask.json") if asking else ("", "")
        if why:
            die("%s/personas/claude.ask.json %s, and %s %s before running tools, so nothing was written: %s"
                % (self.a, why, " and ".join(asking), "asks" if len(asking) == 1 else "ask", fix))
        self.tele_problem()
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
            text = ("# /etc/grok/managed_config.toml in grok persona %s, written by nexus --config from personas/grok.toml\n"
                    "# (grok settings for every grok persona here) and, with the statusline on, statusline/grok/settings.toml.\n"
                    "# Edit those and re-run nexus --config, not this file. A key in the persona's ~/.grok/config.toml wins.\n"
                    % p.name).encode()
            if os.path.isfile("personas/grok.toml"):
                text += b"\n" + read_bytes("personas/grok.toml") + b"\n"
            if p.statusline and not checks.grok_sets_statusline("personas/grok.toml"):
                text += b"\n" + read_bytes("statusline/grok/settings.toml")
            if p.telemetry:
                text += tele.grok_table(self.tele["endpoint"], bool(self.tele["ca"]), self.extras_on(p)).encode()
            with open(tmp, "wb") as fh:
                fh.write(text)
            why = self.settings_problem(tmp, "toml", "personas/grok.toml")
            if why:
                adds = " and ".join(([] if not p.statusline or checks.grok_sets_statusline("personas/grok.toml")
                                     else ["the statusline"]) + (["the [telemetry] table"] if p.telemetry else [])) or "what"
                die("personas/grok.toml is TOML, but with %s nexus --config adds for %s it isn't (does it set ui.status_line "
                    "or telemetry in a way nexus --config missed?), so nothing was written; at personas/%s.grok.toml as it "
                    "would be: %s" % (adds, p.name, p.name, why))
            self.gstaged[p.name] = tmp
        for f in sorted(set(self.unchecked)):
            self.warn_once("couldn't check %s (no python3 for it here, nor a persona image yet): a broken one stops the CLI; "
                      "re-run nexus --config after the build to check it" % f)

        self.stage_tele()

        # compose.personas.yml, checked with the rest
        files = ["compose.yml"]
        if self.personas:
            managed = os.path.isfile("personas/claude.managed.json")
            ca = bool(self.tele["ca"])
            self.json_new = staging.stage(os.path.realpath(JSON_FILE))   # a link's target, renamed onto in its own dir
            os.chmod(self.json_new, 0o644)
            staging.write(self.json_new, personas_store.dumps(self.personas))
            self.personas_new = staging.stage(PERSONAS_FILE)
            os.chmod(self.personas_new, 0o644)
            staging.write(self.personas_new, compose_render.render(self.personas, managed, ca=ca))
            check = staging.stage(PERSONAS_FILE)
            grok_tele = {n: t for n, t in self.tstaged.items() if self.personas[self.idx(n)].flavour == "grok"}
            staging.write(check, compose_render.render(self.personas, managed, env_files=self.staged, ca=ca,
                                                       tele_files=grok_tele))
            files.append(check)
        env = dict(os.environ)
        env.pop("PROJECTS_ROOT", None)
        env.update(AGENT_UID=str(os.getuid()), AGENT_GID=str(os.getgid()), DOCKER_GID=socket_gid(),
                   SSH_AUTH_SOCK="/dev/null", AGENT_CA_CERTS="")
        rc, err = Docker(self.a).compose_check(self.env_new, files, env)
        if rc:
            die("the new settings don't pass docker compose config, so nothing was written:\n" + err)

    def tele_problem(self):
        """Why the telemetry settings can't be saved as they are (nothing is written then), if they can't. A CA file to
        copy in is checked whoever sends (it is copied in either way); the rest only matters for one that sends."""
        why = tele_io.ca_problem(self.ca_from) if self.ca_from else ""
        if why:
            die("the collector's CA file %s %s, so nothing was written: give the CA's certificate (nexus --config, then "
                "telemetry; or --telemetry-ca FILE), or none (--telemetry-ca none)%s"
                % (self.ca_from, why, "; it came from TELEMETRY_CA in %s/.env" % self.a
                   if self.tele_saved.get("ca") not in ("", tele.CA_FILE) else ""))
        sending = [p for p in self.personas if p.telemetry]
        if not sending:
            return
        names = " and ".join(p.name for p in sending)
        verb = "sends" if len(sending) == 1 else "send"
        so = "and %s %s telemetry, so nothing was written" % (names, verb)
        ep = self.tele["endpoint"]
        if not ep:
            die("%s %s telemetry, but no collector is set, so nothing was written: give one (nexus --config, then "
                "telemetry; or --telemetry-endpoint URL), or turn it off (--telemetry NAME:off)" % (names, verb))
        if not tele.valid_endpoint(ep):
            die("TELEMETRY_ENDPOINT in %s/.env isn't %s, %s: set it with nexus --config, then telemetry (or "
                "--telemetry-endpoint URL)" % (self.a, tele.ENDPOINT_RULE, so))
        if self.tele["token"] and not tele.valid_token(self.tele["token"]):
            die("TELEMETRY_TOKEN in %s/.env has characters a token can't have (it takes %s), %s: set it with nexus "
                "--config, then telemetry (or --telemetry-token-file FILE)" % (self.a, tele.TOKEN_RULE, so))
        why = tele_io.ca_problem(self.tele["ca"]) if self.tele["ca"] and not self.ca_from else ""
        if why:
            die("TELEMETRY_CA in %s/.env names %s, which %s, %s: give the collector's CA file again (nexus --config, then "
                "telemetry; or --telemetry-ca FILE)" % (self.a, self.tele["ca"], why, so))
        for x in tele.EXTRAS:
            if self.tele[x.key] not in ("", "on", "off"):
                die("%s in %s/.env is neither on nor off (nor empty), %s: set it with nexus --config, then telemetry "
                    "(or %s all:on or all:off)" % (x.env, self.a, so, x.flag))
        grok = [p.name for p in sending if p.flavour == "grok"]
        if grok and checks.grok_sets_telemetry("personas/grok.toml"):
            die("personas/grok.toml has a [telemetry] table of its own, and nexus --config writes one for %s, so nothing "
                "was written: take that table out of personas/grok.toml (the collector is set with nexus --config, then "
                "telemetry)" % " and ".join(grok))
        if self.tele["ca"]:
            self.own_ca_problem(sending)

    def own_ca_problem(self, sending):
        """A claude persona's own NODE_EXTRA_CA_CERTS, which the telemetry drop-in's would replace for all of Claude's
        TLS: in personas/claude.managed.json's env, or (a docker one: compose gives a ddev one mkcert's, and that
        wins over its env file) in its env file."""
        managed = ""
        try:
            with open("personas/claude.managed.json", "rb") as f:
                env = json.loads(f.read().decode("utf-8", "replace")).get("env")
            managed = env.get("NODE_EXTRA_CA_CERTS", "") if isinstance(env, dict) else ""
        except (OSError, ValueError, AttributeError):
            pass
        for p in sending:
            if p.flavour != "claude":
                continue
            where = "personas/claude.managed.json" if managed else \
                "personas/%s.env" % p.name if p.context == "docker" and dotenv.get("personas/%s.env" % p.name,
                                                                                    "NODE_EXTRA_CA_CERTS") else ""
            if where:
                die("%s sets NODE_EXTRA_CA_CERTS, and %s's telemetry drop-in would replace it with the collector's CA "
                    "for all of Claude's TLS, so nothing was written: put both CAs in one file (a PEM file may hold "
                    "several certificates), give that as the collector's CA (nexus --config, then telemetry), and take "
                    "NODE_EXTRA_CA_CERTS out of %s" % (where, p.name, where))

    def stage_tele(self):
        """The telemetry files of the personas that send it (they hold the token: mode 600, as staged), a neutral
        drop-in for a claude one that no longer does, and the collector's CA, copied in when a new one was given."""
        host = tele.safe_host(short_host())
        ep, tok, ca = self.tele["endpoint"], self.tele["token"], bool(self.tele["ca"])
        for p in self.personas:
            keep = self.tele_target(p.name) if p.telemetry else None
            d, e = tele.dropin_file(p.name), tele.env_file(p.name)
            if os.path.lexists(e) and e != keep:
                self.tgone.append(e)
            if os.path.isfile(d) and d != keep and staging.read(d) != tele.NEUTRAL:
                self.tneutral[d] = staging.stage(d)
                staging.write(self.tneutral[d], tele.NEUTRAL)
            if not p.telemetry:
                continue
            if p.flavour == "claude":
                text = tele.claude_dropin(p.name, host, ep, tok, ca, self.extras_on(p))
            else:
                text = tele.grok_env(p.name, host, tok)
            self.tstaged[p.name] = staging.stage(keep)
            staging.write(self.tstaged[p.name], text)
        if self.ca_from:
            self.ca_new = staging.stage(tele.CA_FILE, self.ca_from)
            os.chmod(self.ca_new, 0o644)

    def tele_target(self, n):
        return tele.dropin_file(n) if self.personas[self.idx(n)].flavour == "claude" else tele.env_file(n)

    def commit(self):
        """Every staged file in place, all of it or none of it: signals are ignored until it is done."""
        for t in [".", "personas", ".env", JSON_FILE, PERSONAS_FILE] + ["personas/%s.env" % n for n in self.staged] \
                + ["personas/%s.grok.toml" % n for n in self.gstaged] + [self.tele_target(n) for n in self.tstaged] \
                + list(self.tneutral) + ([tele.CA_FILE] if self.ca_new else []):
            if os.path.exists(t) and not os.access(t, os.W_OK):
                die("can't write %s/%s (check its owner and mode), so nothing was written" % (self.a, t))
        for t in (JSON_FILE, JSON_FILE + ".bak"):
            if os.path.lexists(t) and not os.path.isfile(t):
                die("%s/%s isn't a file (a directory, or a dangling link?), so nothing was written" % (self.a, t))
        handlers = {s: signal.signal(s, signal.SIG_IGN) for s in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)}
        wrote = self.wrote = []     # the files it changed

        def put(tmp, target, backup=None, how=staging.commit):
            if how(tmp, target, backup):
                wrote.append(target)
        try:
            for n, tmp in self.staged.items():
                put(tmp, "personas/%s.env" % n, staging.bakname("personas/%s.env" % n) if n in self.backup else None)
            if os.path.isfile("personas/claude.managed.json") and "./personas/claude.managed.json," not in read_or_empty(PERSONAS_FILE):
                for p in self.personas:
                    if p.flavour == "claude" and p.name in self.loaded:
                        self.later.append("%s: it gets personas/claude.managed.json as managed settings now; `nexus %s restart` "
                                          "brings them in" % (p.name, p.name))
            put(self.env_new, ".env")
            if self.tele["token"] and os.stat(".env").st_mode & 0o077:
                os.chmod(".env", 0o600)
                self.later.append(".env: it holds the telemetry token now, so only you can read it (mode 600)")
            self.commit_tele(put)
            for n, tmp in self.gstaged.items():
                t = "personas/%s.grok.toml" % n
                p = self.personas[self.idx(n)]
                told = "`nexus %s restart`" % n in "\n".join(self.later)
                if n in self.loaded and not os.path.exists(t):
                    what = "grok settings" + (" and statusline" if p.statusline else "")
                    self.later.append("%s: its %s come through %s now; `nexus %s restart` brings them in" % (n, what, t, n))
                elif n in self.loaded and self.loaded[n] == self.state(p) and not _same_settings(tmp, t) and not told:
                    self.later.append("%s: its grok settings changed; the next grok started in it reads them" % n)
                put(tmp, t)
            # A compose.personas.yml that isn't one nexus --config generated (the `# persona:` lines from before
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
                if staging.replace(self.json_new, os.path.realpath(JSON_FILE), JSON_FILE + ".bak"):
                    wrote.append(JSON_FILE)
                put(self.personas_new, PERSONAS_FILE)
            else:
                if os.path.isfile(JSON_FILE):
                    shutil.copy2(JSON_FILE, JSON_FILE + ".bak")
                    os.unlink(os.path.realpath(JSON_FILE))
                    if os.path.lexists(JSON_FILE):     # it was a link: that goes too, not left dangling
                        os.unlink(JSON_FILE)
                    wrote.append(JSON_FILE)
                if os.path.isfile(PERSONAS_FILE):
                    os.unlink(PERSONAS_FILE)
                    wrote.append(PERSONAS_FILE)
            if self.migrating:
                self.moved_notes(old_compose, kept)
            elif self.where == "json" and self.personas:
                self.rewritten_notes(before)
            elif kept:
                self.later.append("personas: the %s that was here isn't one nexus --config wrote; it is kept as %s"
                                  % (PERSONAS_FILE, kept))
        finally:
            for s, h in handlers.items():
                signal.signal(s, h)

    def commit_tele(self, put):
        """The telemetry files in place: a persona's own by a rename (a running container keeps the one it was made
        with, as the restart note says), the CA through (a running container sees the new one). Then a restart asked
        of each persona that sends whose container reads, at creation, a file that changed, and whether to try the
        collector."""
        changed = set()
        for n, tmp in self.tstaged.items():
            if staging.replace(tmp, self.tele_target(n)):
                self.wrote.append(self.tele_target(n))
                changed.add(n)
        for d, tmp in self.tneutral.items():
            if staging.replace(tmp, d):
                self.wrote.append(d)
        for f in self.tgone:      # the token, in a file no container is made with any more
            os.unlink(f)
            self.wrote.append(f)
        ca_new = bool(self.ca_new) and staging.commit(self.ca_new, tele.CA_FILE)
        if ca_new:
            self.wrote.append(tele.CA_FILE)
        mount = bool(self.tele["ca"]) != bool(self.tele_saved.get("ca"))
        told = "\n".join(self.later)
        for p in self.personas:
            if not (p.telemetry and p.name in self.loaded and self.loaded[p.name] == self.state(p)):
                continue
            if "`nexus %s restart`" % p.name in told:
                continue
            if p.name in changed or mount or (ca_new and tele.ca_source(p, True) == tele.DDEV_BUNDLE):
                self.later.append("%s: its telemetry settings changed; `nexus %s restart` applies them (that ends its tmux "
                                  "sessions)" % (p.name, p.name))
        self.tele_check = bool(self.tele["endpoint"]) and bool(self.tele_newly or changed or ca_new or mount
                                                               or self.tele != self.tele_saved)
        for p in self.personas:     # tool content without tool details: what that leaves out, or doesn't
            on = self.extras_on(p)
            if on != (tele.CONTENT,) or self.extras_saved.get(p.name) == (p.flavour, on):
                continue
            if p.flavour == "claude":
                self.later.append("%s: it sends tool content but not tool details, so what Claude Code writes to files "
                                  "(with Write and Edit) isn't sent. Tool details would send it, along with every "
                                  "command, file path and URL:" % p.name)
                self.later.append("    nexus --config --yes --tool-details %s:on" % p.name)
            else:
                self.later.append("%s: grok sends each tool's full command and input with its tool content, so its "
                                  "commands are sent even without tool details" % p.name)

    def saved(self):
        """After a commit: what is saved is what the next save compares with."""
        self.loaded = {p.name: self.state(p) for p in self.personas}
        self.extras_saved = {p.name: (p.flavour, self.extras_on(p)) for p in self.personas}
        self.tele_saved, self.ca_from = dict(self.tele), ""
        self.where = "json" if self.personas else "none"
        self.migrating = self.first = False
        self.unstage()

    def unstage(self):
        self.staged, self.gstaged, self.backup, self.unchecked = {}, {}, set(), []
        self.env_new = self.json_new = self.personas_new = None
        self.tstaged, self.ca_new, self.tgone, self.tneutral = {}, None, [], {}
