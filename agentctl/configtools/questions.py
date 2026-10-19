"""nexus --config's questions, each kind in one place, asked through the Prompter (on a terminal, by piped lines,
or answered by their defaults under --yes): the VM settings, a persona's flavour, context, name, statusline,
projects root and ssh, its sign-in and git identity, and the launcher, mkcert and image questions.

They change the Session (its personas, its staged files) and return what was decided; saving is the
Session's, and what happens after a save is after.py's.
"""
import os
import shutil

from agentctl.adapters import ssh
from agentctl.adapters.docker import Docker
from agentctl.configtools.host import env_flavour, free_gb, git_config, pad, readlink_f, set_kv
from agentctl.core import dotenv
from agentctl.core.errors import NexusError
from agentctl.core.shellcompat import mkdir_p
from agentctl.domain import persona as rules
from agentctl.domain.persona import Persona

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


class Questions:
    def __init__(self, session):
        self.s = session
        self.ask = session.ask

    # --- the VM settings, shared by every persona ---
    def shared(self):
        """The projects root, the image registry and the Claude Code version, into the staged .env; a "keep
        them" answered yes takes the three as they are."""
        s, o, env_new = self.s, self.s.o, self.s.env_new
        root = o.root or dotenv.get(env_new, "PROJECTS_ROOT") or s.home + "/projects"
        registry = o.registry or dotenv.get(env_new, "REGISTRY") or "local"
        cv = o.claude or dotenv.get(env_new, "CLAUDE_VERSION") or "latest"
        s.section("VM settings, shared by every persona")
        if not o.yes and not s.first and os.path.exists(".env") and self.ask.yesno(
                "Keep them? projects root %s, image registry %s%s" % (root, registry, ", Claude Code " + cv if s.has("claude") else ""), "y"):
            self.ask.defaults = True
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
        if s.has("claude"):
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
        self.ask.defaults = False
        s.root, s.registry = root, registry

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
        s.put(Persona(n, f, c, rules.next_colour(s.personas), sl, r, k))
        return n

    def change(self, name):
        s = self.s
        p = s.personas[s.idx(name)]
        f, c = self.flavour_context(p.flavour, p.context)
        sl = self.ask.yesno("%s %s?" % (SL_Q, name), "y" if p.statusline else "n")
        r = self.root(name, p.root)
        k = self.ssh(name, p.ssh)
        s.put(Persona(name, f, c, p.colour, sl, r, k))

    def pick(self, verb):
        s = self.s
        if not s.personas:
            s.say("  no persona to " + verb)
            return None
        opts = ["%s %s, %s" % (pad(p.name, 31), p.flavour, p.context) for p in s.personas]
        n = self.ask.choose("Which persona to %s?" % verb, "-", opts + ["-        none: back to the menu"])
        return None if n == "-" else n

    def persona_menu(self):
        s = self.s
        while True:
            s.say("")
            s.list()
            act = self.ask.choose("What next?", "done", MENU)
            if act == "add":
                self.add()
            elif act == "change":
                n = self.pick("change")
                if n:
                    self.change(n)
            elif act == "remove":
                n = self.pick("remove")
                if n:
                    s.drop(n)
            else:
                return

    def adopt(self):
        s = self.s
        names = os.listdir("personas") if os.path.isdir("personas") else []
        for file in sorted(f for f in names if f.endswith(".env") and not f.startswith(".")):   # personas/*.env
            path = "personas/" + file
            n = file[:-len(".env")]
            if not os.path.isfile(path) or not rules.valid_name(n) or n in s.loaded or s.idx(n) is not None:
                continue
            f = env_flavour(path)
            if not self.ask.yesno("Found %s%s: set it up as persona %s?" % (path, " (flavour %s)" % f if f else "", n),
                                  "y" if s.first else "n"):
                continue
            if not f:
                f = self.ask.choose("Flavour:", "claude", FLAVOURS)
            c = self.ask.choose(CONTEXT_Q, s.default_context, CONTEXTS)
            sl = self.ask.yesno("%s %s?" % (SL_Q, n), "y")
            s.put(Persona(n, f, c, rules.next_colour(s.personas), sl))

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
                for p in s.personas:
                    k = "%s-%s" % (p.flavour, p.context)
                    if k in seen:
                        continue
                    seen.add(k)
                    cmds.append("%s %s %s" % (s.launcher, p.name, verb))
                s.later.append("images: skipped for lack of disk space; after freeing some, run: " + "; ".join(cmds))
                return False
        return True
