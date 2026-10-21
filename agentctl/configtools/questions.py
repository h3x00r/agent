"""nexus --config's questions, each kind in one place, asked through the Prompter (on a terminal, by piped lines,
or answered by their defaults under --yes): the VM settings, a persona's flavour, context, name, statusline,
projects root, ssh, permissions, telemetry, tool details and tool content, its sign-in and git identity, the
telemetry collector and whose tool details and content it gets, and the launcher, mkcert and image questions.

They change the Session (its personas, its staged files) and return what was decided; saving is the
Session's, and what happens after a save is after.py's.
"""
import os
import shutil

from agentctl.adapters import ssh
from agentctl.adapters import telemetry as tele_io
from agentctl.adapters.docker import Docker
from agentctl.configtools.host import env_flavour, free_gb, git_config, pad, readlink_f, set_kv
from agentctl.core import dotenv
from agentctl.core.errors import NexusError
from agentctl.core.shellcompat import mkdir_p
from agentctl.domain import persona as rules
from agentctl.domain import telemetry as tele
from agentctl.domain.persona import Persona

MIN_FREE_GB = 3

FLAVOURS = ["claude   Claude Code (Anthropic)", "grok     Grok Build (xAI)"]
CONTEXTS = ["ddev     ddev projects: ddev inside, sharing the VM's ddev state and mkcert CA",
            "docker   projects run with docker compose / Dockerfiles: no ddev"]
CONTEXT_Q = "Context: which kind of projects does it work on?"
SL_Q = "Statusline (statusline/: model, context, cost; what nexus top shows) in"
PERM_Q = "Let %s run its tools without asking (bypassPermissions; no: it asks first, as personas/claude.ask.json says)?"
TELE_Q = "Send %s's telemetry (OpenTelemetry: tokens, cost, sessions, tool use; no prompts) to %s?"
TELE_EP_Q = "The collector's OTLP/HTTP address, e.g. https://collector.lan:4318 (none: no telemetry)"
TELE_CA_Q = "The CA file its certificate is signed by, PEM (-: none, a publicly trusted certificate)"
# Tool details and tool content (tele.EXTRAS): whose the collector gets, then (some) each persona's own.
EXTRA_Q = "%s too (%s, which can hold secrets): from which personas?"
EXTRA_HOW = ["none     from no persona",
             "all      every persona that sends telemetry, and any that sends later",
             "some     the personas you pick next"]
EXTRA_ONE_Q = "Send %s's %s too (%s, which can hold secrets)?"
EXTRA_ALL = ("  %s: on, as for every persona that sends telemetry. To leave one out: telemetry in the menu, then some at "
             "\"%s too\"")
SSH_HOW = ["agent    your ssh-agent, forwarded: your own keys; starting it then needs an agent in your shell",
           "none     no ssh: it commits, and you pull and push from the VM, in the same directory",
           "new      a key of its own, made as personas/NAME.key: add its public half as a deploy key",
           "path     a private key you already have, without a passphrase"]
AUTH = ["login    interactively, inside the persona (a Claude subscription; kept in its home volume)",
        "apikey   an Anthropic API key (Console / API billing)",
        "token    a long-lived token from `claude setup-token` (Pro/Max/Team/Enterprise plan)"]


def die(msg):
    raise NexusError(msg)


class Questions:
    def __init__(self, session):
        self.s = session
        self.ask = session.ask

    # --- the VM settings, shared by every persona ---
    def shared(self, title="VM settings, shared by every persona", claude=False):
        """The projects root, the image registry and the Claude Code version (asked with a Claude persona, or
        CLAUDE), into the staged .env; what it has is the default."""
        s, o, env_new = self.s, self.s.o, self.s.env_new
        root = o.root or dotenv.get(env_new, "PROJECTS_ROOT") or s.home + "/projects"
        registry = o.registry or dotenv.get(env_new, "REGISTRY") or "local"
        cv = o.claude or dotenv.get(env_new, "CLAUDE_VERSION") or "latest"
        s.section(title)
        while True:
            root = s.norm_dir(self.ask.ask("The VM's projects root, for personas without their own (mounted at the same path inside)", root))
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
            s.say("  " + why)
            root = s.home + "/projects"
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
            s.say("  local, or a registry like registry.lan:5000 (or registry.lan:5000/team)")
            registry = "local"
        set_kv(env_new, "REGISTRY", registry)
        if claude or s.has("claude"):
            while True:
                cv = self.ask.ask("Claude Code version, for the claude personas: latest | stable | X.Y.Z", cv) or "latest"
                if rules.valid_claude_version(cv):
                    break
                if o.yes:
                    die("the Claude Code version must be latest, stable or X.Y.Z, not '%s'" % cv)
                s.say("  latest, stable or a version like 2.1.302")
                cv = "latest"
            set_kv(env_new, "CLAUDE_VERSION", cv)
        elif o.claude:
            set_kv(env_new, "CLAUDE_VERSION", o.claude)
        s.root, s.registry, s.claude_version, s.vm_root_hint = root, registry, cv, "now " + root

    # --- a persona ---
    def flavour_context(self, f, c):
        return (self.ask.choose("Flavour:", f, FLAVOURS), self.ask.choose(CONTEXT_Q, c, CONTEXTS))

    def root(self, name, current):
        s = self.s
        r = current or "-"
        while True:
            r = self.ask.ask("Projects root for %s (-: the VM's, %s)" % (name, s.vm_root_hint), r)
            if r in ("", "-"):
                return ""
            r = s.norm_dir(r)
            if not rules.persona_root_ok(r):
                why = "a projects root must be %s, not '%s'" % (rules.ROOT_RULE, r)
            else:
                try:
                    mkdir_p(r)
                    return r
                except OSError:
                    why = "can't create the projects root '%s'" % r
            if s.o.yes:
                die(why)
            s.say("  " + why)
            r = "-"

    def ssh(self, name, current):
        s = self.s
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
                s.say("  making a key needs ssh-keygen (openssh-client) on this VM")
                continue
            k = current if default == "path" else ""
            while True:
                k = self.ask.ask("Path of %s's private key (-: back)" % name, k)
                if k in ("", "-"):
                    break
                k = s.norm_dir(k)
                why = ("the key's path must be %s" % rules.KEY_RULE) if not rules.key_path_ok(name, k) \
                    else ssh.key_problem(k)
                if not why:
                    return k
                s.say("  %s's key: %s" % (name, why))
                k = ""

    def permissions(self, name, f, current):
        """bypass or ask, asked of a claude persona; a grok one bypasses (grok's own setting decides)."""
        if f != "claude":
            return "bypass"
        return "bypass" if self.ask.yesno(PERM_Q % name, "y" if current == "bypass" else "n") else "ask"

    def telemetry_for(self, name, current):
        """Whether NAME sends telemetry, asked only while a collector is set."""
        ep = self.s.tele["endpoint"]
        if not ep:
            return False
        return self.ask.yesno(TELE_Q % (name, ep), "y" if current else "n")

    def extra_for(self, x, name, sends, current):
        """Whether NAME sends its own extra X (tool details or content), asked only while it sends telemetry and X isn't
        on for all."""
        if not sends:
            return False
        if self.s.all_on(x):
            self.s.say(EXTRA_ALL % (x.name, x.name.capitalize()))
            return False
        return self.ask.yesno(EXTRA_ONE_Q % (name, x.name, x.what), "y" if current else "n")

    def add(self):
        s = self.s
        f, c = self.flavour_context("claude", s.default_context)
        while True:
            n = self.ask.ask("Name", rules.suggest_name(s.personas, f, c))
            if rules.valid_name(n) and s.idx(n) is None:
                break
            s.say("  %s; not taken" % rules.NAME_RULE)
        sl = self.ask.yesno("%s %s?" % (SL_Q, n), "y")
        r = self.root(n, "")
        k = self.ssh(n, "")
        perm = self.permissions(n, f, "bypass")
        tel = self.telemetry_for(n, False)
        det, con = (self.extra_for(x, n, tel, False) for x in tele.EXTRAS)
        s.put(Persona(n, f, c, rules.next_colour(s.personas), sl, r, k, perm, tel, det, con))
        return n

    def change(self, name):
        s = self.s
        p = s.personas[s.idx(name)]
        f, c = self.flavour_context(p.flavour, p.context)
        sl = self.ask.yesno("%s %s?" % (SL_Q, name), "y" if p.statusline else "n")
        r = self.root(name, p.root)
        k = self.ssh(name, p.ssh)
        perm = self.permissions(name, f, p.permissions)
        tel = self.telemetry_for(name, p.telemetry)
        det, con = (self.extra_for(x, name, tel, getattr(p, x.field)) for x in tele.EXTRAS)
        s.put(Persona(name, f, c, p.colour, sl, r, k, perm, tel, det, con))

    # --- telemetry: the collector, and who sends to it ---
    def telemetry(self):
        """The collector (its address, token and CA) into the Session, then, for each persona, whether it sends to
        it, and whose tool details and content it gets; none for the address turns telemetry off for all."""
        s, t = self.s, self.s.tele
        while True:
            ep = self.ask.ask(TELE_EP_Q, t["endpoint"] or "none")
            if ep == "none":
                s.tele_off()
                return
            ep = tele.norm_endpoint(ep)
            if tele.valid_endpoint(ep):
                break
            s.say("  it takes " + tele.ENDPOINT_RULE)
        t["endpoint"] = ep
        while True:
            tok = self.ask.secret("Its bearer token (sent as Authorization: Bearer …; %s- for none)"
                                  % ("empty keeps the one in .env, " if t["token"] else ""))
            if tok == "-":
                t["token"] = ""
                break
            if not tok and t["token"] and not tele.valid_token(t["token"]):
                s.say("  the one in .env isn't a token: it takes " + tele.TOKEN_RULE)
                continue
            if not tok or tele.valid_token(tok):
                if tok:
                    t["token"] = tok
                break
            s.say("  a token takes " + tele.TOKEN_RULE)
        cur = t["ca"]
        while True:
            a = self.ask.ask(TELE_CA_Q, cur or "-")
            if a == "-":
                t["ca"], s.ca_from = "", ""
                break
            why = tele_io.ca_problem(s.ca_from or cur) if a == cur else ""
            if a == cur and not why:
                break
            if a == cur:
                s.say("  %s %s: give the CA file again" % (s.ca_from or cur, why))
                t["ca"], s.ca_from, cur = "", "", ""
                continue
            path = s.norm_dir(a)
            why = "isn't an absolute path (or ~/…)" if not path.startswith("/") else tele_io.ca_problem(path)
            if not why:
                t["ca"], s.ca_from = tele.CA_FILE, path
                break
            s.say("  %s %s" % (a, why))
        for p in s.personas:
            p.telemetry = self.ask.yesno(TELE_Q % (p.name, ep), "y" if p.telemetry else "n")
            for x in tele.EXTRAS:
                setattr(p, x.field, getattr(p, x.field) and p.telemetry)
        sending = [p for p in s.personas if p.telemetry]
        for x in tele.EXTRAS if sending else ():
            self.extra(x, sending)

    def extra(self, x, sending):
        """Whose extra X (tool details or content) the collector gets: none, all (every persona that sends,
        TELEMETRY_TOOL_DETAILS=on, say), or some (each of SENDING asked; its own "tool_details", say)."""
        s = self.s
        now = "all" if s.all_on(x) else "some" if any(getattr(p, x.field) for p in sending) else "none"
        how = self.ask.choose(EXTRA_Q % (x.name.capitalize(), x.what), now, EXTRA_HOW)
        s.tele[x.key] = "on" if how == "all" else ""
        for p in sending:
            setattr(p, x.field, how == "some" and self.ask.yesno(EXTRA_ONE_Q % (p.name, x.name, x.what),
                                                                 "y" if getattr(p, x.field) or now == "all" else "n"))

    def pick(self, verb):
        s = self.s
        if not s.personas:
            s.say("  no persona to " + verb)
            return None
        opts = ["%s %s, %s" % (pad(p.name, 31), p.flavour, p.context) for p in s.personas]
        n = self.ask.choose("Which persona to %s?" % verb, "-", opts + ["-        none: back to the menu"])
        return None if n == "-" else n

    # --- the env files: sign-in and identity ---
    def env_files(self):
        """An env file for each persona without a fitting one: a new persona's, unless one is there for its
        flavour (reused), or one whose flavour changed (the old one kept as a .bak)."""
        s = self.s
        for p in s.personas:
            file = "personas/%s.env" % p.name
            if os.path.isfile(file):
                if p.name in s.loaded:
                    if s.loaded[p.name][0] == p.flavour:
                        continue
                    why = "%s changed flavour" % p.name
                else:
                    have = env_flavour(file)
                    if not have or have == p.flavour:
                        s.say("-- %s already exists%s: reusing it" % (file, " (a %s one)" % have if have else ""))
                        continue
                    why = "%s is for a %s persona" % (file, have)
                if s.o.yes:
                    s.say("-- %s: writing a new %s (the old one is kept as a .bak)" % (why, file))
                elif not self.ask.yesno("%s: write a new %s (the old one is kept as a .bak)?" % (why, file), "y"):
                    continue
                s.backup.add(p.name)
            self.sign_in(p.name, p.flavour)

    def sign_in(self, n, f):
        s = self.s
        if s.o.yes:
            s.say("-- personas/%s.env" % n)
        else:
            s.section("%s: sign-in and identity (personas/%s.env)" % (n, n))
        tmp = s.stage_env_file(n, f)
        if f == "claude":
            auth = self.ask.choose("How does %s sign in to Claude?" % n, "login", AUTH)
            key = ""
            if auth in ("apikey", "token"):
                var = "ANTHROPIC_API_KEY" if auth == "apikey" else "CLAUDE_CODE_OAUTH_TOKEN"
                key = self.ask.secret(var)
                if key:
                    set_kv(tmp, var, key)
            if auth != "login" and not key:
                s.later.append("%s: no key entered; add it to %s/personas/%s.env, or log in inside the persona"
                               % (n, s.a, n))
        else:
            key = self.ask.secret("XAI_API_KEY")
            if key:
                set_kv(tmp, "XAI_API_KEY", key)
            else:
                s.later.append("%s: add XAI_API_KEY to %s/personas/%s.env" % (n, s.a, n))
        name = self.ask.ask("git author name for %s" % n, git_config("user.name"))
        email = self.ask.ask("git author email for %s" % n, git_config("user.email"))
        if name and email:
            for var, v in (("GIT_AUTHOR_NAME", name), ("GIT_COMMITTER_NAME", name), ("GIT_AUTHOR_EMAIL", email),
                           ("GIT_COMMITTER_EMAIL", email)):
                set_kv(tmp, var, v)
        else:
            s.later.append("%s: set the git identity in %s/personas/%s.env" % (n, s.a, n))
        gh = self.ask.secret("GH_TOKEN for the gh CLI in %s" % n)
        if gh:
            set_kv(tmp, "GH_TOKEN", gh)

    # --- the launcher, mkcert, the images ---
    def launcher(self):
        """Where the launcher is linked: the target to link, or "" when it is linked already, or isn't to be
        (none, or a file there that isn't to be replaced)."""
        s, o = self.s, self.s.o
        if not o.yes and o.link != "none":
            while True:
                o.link = self.ask.ask("Link the launcher as <dir>/nexus (none: skip)", o.link)
                if o.link == "none":
                    break
                o.link = s.norm_dir(o.link)
                if o.link.startswith("/"):
                    break
                s.say("  an absolute directory, e.g. /usr/local/bin or ~/.local/bin; none to skip")
                o.link = "/usr/local/bin"
        if o.link == "none":
            return ""
        target = (o.link[:-1] if o.link.endswith("/") else o.link) + "/nexus"
        if readlink_f(target) == s.launcher:
            return ""
        if os.path.lexists(target) and not self.ask.yesno(
                "%s already exists (%s); replace it?" % (target, readlink_f(target) or "a file"), "n"):
            s.later.append("launcher: not linked; run it as %s" % s.launcher)
            return ""
        return target

    def ca_missing(self):
        return self.s.has("ddev") and not os.path.isfile(os.path.join(self.s.home, ".local/share/mkcert/rootCA.pem"))

    def mkcert(self):
        """Whether to run mkcert -install now: asked for the ddev personas while the CA isn't there."""
        s = self.s
        if not self.ca_missing():
            return False
        if shutil.which("mkcert") and not s.o.yes and self.ask.yesno("Trust ddev's local CA on this VM now (mkcert -install)?", "y"):
            return True
        s.later.append("ddev: run `mkcert -install` on this VM (it comes with ddev), then `nexus <persona> restart`, "
                       "or https://*.ddev.site stays untrusted inside")
        return False

    def images(self, verb):
        """Whether to build (or pull) the images now; with little room for Docker, asked again."""
        s, o = self.s, self.s.o
        if not (o.build and s.personas and self.ask.yesno(
                "%s the images now? (the first build takes a few minutes)" % ("Build" if verb == "build" else "Pull"), "y")):
            return False
        return self.room(verb, s.personas)

    def room(self, verb, personas):
        """Whether there is room for Docker to build (or pull) the images of PERSONAS: with little, it says so
        and asks (--yes goes on); a no leaves a note of how to, later."""
        s, o = self.s, self.s.o
        droot = Docker.root_dir()
        free = free_gb(droot) if droot and os.path.isdir(droot) else None
        if free is not None and free < MIN_FREE_GB:
            s.say("  Only %d GB is free for Docker (%s); a %s from scratch needs about %d GB, and a full disk"
                  % (free, droot, verb, MIN_FREE_GB))
            s.say("  fails it halfway, often as apt's \"At least one invalid signature was encountered\".")
            s.say("  Free space first: `docker system df` shows what's used; `docker builder prune` and "
                  "`docker image prune` free it.")
            if not o.yes and not self.ask.yesno("%s anyway?" % ("Build" if verb == "build" else "Pull"), "n"):
                cmds, seen = [], set()
                for p in personas:
                    k = "%s-%s" % (p.flavour, p.context)
                    if k in seen:
                        continue
                    seen.add(k)
                    cmds.append("%s %s %s" % (s.launcher, p.name, verb))
                s.later.append("images: skipped for lack of disk space; after freeing some, run: " + "; ".join(cmds))
                return False
        return True
