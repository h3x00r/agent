"""nexus --config without --yes: the configuration, by section.

A first run (no .env yet) asks the settings every persona shares, then where to link the launcher, then the
first persona; any other run first brings what it generates up to date (as a re-run of the setup always did),
saying which files it wrote, if any. Every run then shows what is set up, and a menu: list, add, change or remove a persona, the
shared settings, telemetry, the launcher, the images. Each action is saved as soon as it is done (the Session's stage,
check and commit, as one --yes run saves), so a run stopped in the middle of one loses that one only; one the
check refuses is said, nothing of it written, and the menu comes back. Options given with it (--persona,
--projects-root, …) are saved first. A checkout still on the `# persona:` lines has them moved first, so what
the menu shows is personas.json.

Pitfalls (each pinned where named):
- each action saved on its own: ^C at the menu keeps what was saved (golden config/menu-add-then-int)
- ^C in the middle of one: nothing of it written (golden config/menu-int-mid-add)
- the end of input at the menu is a quit; within an action, the run stops, what it hadn't saved unsaved
  (golden config/menu-eof)
- an action the check refuses: said, nothing of it written, the personas and shared settings as saved, and
  none of its notes (golden config/menu-refused)
- the options' shared settings saved first, then .env's are the defaults (golden config/menu-flags-shared); the
  options refused, the telemetry settings as .env has them too (golden config/telemetry-hand-edits)
- a first persona refused: the menu all the same (golden config/menu-first-refused)
- a run brings the generated files up to date first, saying which it wrote; one the check refuses, the menu all
  the same (golden config/menu-refresh, golden config/menu-refused)
- a save that writes nothing says so (golden config/menu-shared-kept)
- a new persona's ssh key told as a new one's, not a restart (golden config/menu-add-key)
- a build that failed makes the exit code 1, at quit too (golden config/menu-images)
- telemetry: the collector tried with a test export after a save that changed its settings or made a persona send,
  and when the telemetry action changed nothing (to try it again); not otherwise (golden config/telemetry-menu,
  golden config/telemetry-flags)
"""
import copy
import os
import shutil

from agentctl.configtools import after, staging
from agentctl.configtools.host import env_flavour, pad, readlink_f
from agentctl.configtools.questions import Questions
from agentctl.core.errors import NexusError
from agentctl.domain import persona as rules
from agentctl.domain import telemetry as tele
from agentctl.ui.prompts import EOF_MESSAGE

ACTIONS = ["list      the shared settings and the personas, again",
           "add       a new persona",
           "change    one persona's flavour, context, statusline, projects root, ssh, permissions or telemetry",
           "remove    one persona (its env file and home volume stay)",
           "shared    the settings every persona shares: projects root, image registry, Claude Code version",
           "telemetry send OpenTelemetry to a collector: its address, token and CA, and which personas send",
           "launcher  link nexus into a directory on your PATH",
           "images    build (or pull) the image of every persona",
           "quit"]
SHARED_FIRST = "Shared by every persona (asked once; `shared` in the menu changes them)"
SHARED = "Shared by every persona"


def run(s):
    return Menu(s).run()


class Menu:
    def __init__(self, s):
        self.s, self.q = s, Questions(s)
        self.failed = 0
        self.committed = False      # whether the action under way has saved

    # --- the run ---
    def run(self):
        s, o = self.s, self.s.o
        s.check_flags()
        s.open()
        s.preflight()
        first = not os.path.exists(".env")
        s.read_shared()
        ddev = " (ddev %s on this VM)" % s.vm_ddev if s.vm_ddev else ""
        if first:
            s.say("nexus --config: setting up %s for the first time%s" % (s.a, ddev))
        else:
            s.say("nexus --config: %s%s" % (s.a, ddev))
        flags = bool(o.add or o.ssh or o.permissions or o.remove or o.root or o.registry or o.claude or o.telemetry
                     or o.tele_endpoint or o.tele_token_file or o.tele_ca or o.tool_details or o.tool_content)
        before = set(p.name for p in s.personas)
        read = copy.deepcopy(s.personas)
        s.apply_flags()
        if s.tele_unsaid:
            self.q.collector(s.tele_unsaid)
        if first:
            s.stage_env()
            self.q.shared(SHARED_FIRST, claude=True)
            self.save("the shared settings, in .env" + (", and what the options said" if flags else ""), env_staged=True)
        else:
            # Every run brings what it generates up to date first, as a re-run of the setup always did (the
            # grok configs, compose.personas.yml, .env's DDEV_VERSION, …), and moves the `# persona:` lines and
            # saves the options given; it says so only if it changed a file. One the check refuses is said, and
            # the menu comes all the same, on the personas as they were read.
            try:
                s.stage_env()
                s.env_flags()
                self.save("the personas, moved into personas.json" if s.migrating and not flags else
                          "what the options said" if flags else "", env_staged=True)
                o.root = o.registry = o.claude = ""   # saved: from now on, .env has them (the defaults of `shared`)
            except NexusError as e:
                if e.message == EOF_MESSAGE:
                    raise
                s.out.note(e.message)
                s.personas = read
                s.read_shared()
                s.read_tele()
                staging.cleanup()
                s.unstage()
                self.failed = 1
                del s.later[:]
        if first:
            o.root = o.registry = o.claude = ""   # saved: from now on, .env has them (the defaults of `shared`)
        self.notes()
        for p in s.personas:                   # added by --persona: its image and CA offered, as an add's are
            if p.name not in before:
                self.image(p)
                self.mkcert(p)
        self.notes()
        if first:
            if o.link != "none":
                s.section("The launcher")
                self.link()
                self.notes()
            if not s.personas:
                self.do("first")
        self.overview()
        while True:
            s.say("")
            try:
                act = s.ask.choose("What next?", "quit" if s.personas else "add", ACTIONS)
            except NexusError as e:
                if e.message != EOF_MESSAGE:
                    raise
                break
            if act == "quit":
                break
            self.do(act)
        s.say("")
        if s.personas:
            s.say("Start one from a project directory:  cd %s/<project> && nexus %s"
                  % (s.personas[0].root or s.root, s.personas[0].name))
        s.say("Day-to-day use, upgrades and troubleshooting: runbook.md")
        return self.failed

    def do(self, act):
        """One action; one the check refuses (or that fails otherwise) is said, and the personas and the shared
        settings go back to what is saved. The end of input stops the run (nothing is left to answer the menu),
        and ^C does; either way, an action stopped before its save says nothing of what it would have done."""
        s = self.s
        kept = (copy.deepcopy(s.personas), s.root, s.registry, s.claude_version, s.vm_root_hint, dict(s.tele), s.ca_from)
        mark, self.committed, done = len(s.later), False, False
        try:
            getattr(self, "act_" + act)()
            done = True
        except NexusError as e:
            if e.message == EOF_MESSAGE:
                raise
            s.out.note(e.message)
            s.personas, s.root, s.registry, s.claude_version, s.vm_root_hint, s.tele, s.ca_from = kept
            staging.cleanup()
            s.unstage()
            self.failed = 1
        finally:
            if not (done or self.committed):   # stopped short of its save: its notes (a restart, a key to add,
                del s.later[mark:]             # the removal's commands) were for a save that didn't happen
            self.notes()

    def notes(self):
        for line in self.s.later:
            self.s.say(line)
        del self.s.later[:]

    def save(self, what, env_staged=False):
        """What the action changed, saved: .env (staged already, for the shared settings), the env files a
        persona still needs, the rest; then the ssh keys asked for. The Session is then as saved."""
        s = self.s
        s.change_notes(list(s.loaded))
        if not env_staged:
            s.stage_env()
        s.env_vm()
        self.q.env_files()
        s.prepare()
        s.commit()
        self.committed = True
        if what and s.wrote:
            s.say("-- saved: " + what)
        elif what:
            s.say("-- %s: as it was already, nothing to write" % what)
        elif s.wrote:
            s.say("-- brought up to date: " + ", ".join(s.wrote))
        after.ssh_keys(s)
        after.telemetry_check(s)
        s.saved()

    # --- what is set up ---
    def overview(self):
        s = self.s
        s.say("")
        s.say(SHARED)
        rows = (("projects root", s.root),
                ("image registry", "%s (%s)" % (s.registry, "images built on this VM" if s.registry == "local"
                                                 else "images pulled from it")),
                ("Claude Code", s.claude_version),
                ("telemetry", self.telemetry_state()),
                ("launcher", self.launcher_state()))
        for k, v in rows:
            s.say("  %s %s" % (pad(k, 15), v))
        s.say("Personas")
        s.list()
        for n, f in self.leftovers():
            s.say("  personas/%s.env%s is no persona's (one removed earlier?): add one named %s to use it again"
                  % (n, " (a %s one)" % f if f else "", n))

    def telemetry_state(self):
        t = self.s.tele
        if not t["endpoint"]:
            return "off"
        extras = " and ".join(x.name for x in tele.EXTRAS if self.s.all_on(x))
        return "%s (%s, %s%s)" % (t["endpoint"], "a token" if t["token"] else "no token",
                                  "its CA in " + t["ca"] if t["ca"] else "a publicly trusted certificate",
                                  ", %s from every persona that sends" % extras if extras else "")

    def launcher_state(self):
        s = self.s
        a = shutil.which("nexus") or ""
        if a and readlink_f(a) == readlink_f(s.launcher):
            return a
        if a:
            return "`nexus` on your PATH is %s, another one: link this one with launcher, or run %s" % (a, s.launcher)
        return "not on your PATH: link it with launcher, or run %s" % s.launcher

    def leftovers(self):
        """The personas/*.env files of no persona: (name, flavour) each."""
        s = self.s
        names = os.listdir("personas") if os.path.isdir("personas") else []
        out = []
        for file in sorted(f for f in names if f.endswith(".env") and not f.startswith(".")):
            n = file[:-len(".env")]
            path = "personas/" + file
            if os.path.isfile(path) and rules.valid_name(n) and s.idx(n) is None:
                out.append((n, env_flavour(path)))
        return out

    # --- the actions ---
    def act_list(self):
        self.overview()

    def act_add(self):
        self.s.section("A new persona")
        self.add()

    def act_first(self):
        self.s.section("The first persona")
        self.add()

    def add(self):
        s = self.s
        n = self.q.add()
        self.save(n + ", a new persona")
        p = s.personas[s.idx(n)]
        self.image(p)
        self.mkcert(p)

    def act_change(self):
        s = self.s
        n = self.q.pick("change")
        if not n:
            return
        old = s.personas[s.idx(n)]
        before, extras = s.state(old), (old.tool_details, old.tool_content)    # not in state(): no restart of their own
        s.section("Change " + n)
        self.q.change(n)
        p = s.personas[s.idx(n)]
        if s.state(p) == before and (p.tool_details, p.tool_content) == extras:
            s.say("  %s is as it was" % n)
            return
        self.save(n + "'s changes")
        if (p.flavour, p.context) != before[:2]:
            self.image(p)
        if p.context != before[1]:
            self.mkcert(p)

    def act_remove(self):
        s = self.s
        n = self.q.pick("remove")
        if not n:
            return
        if not s.ask.yesno("Remove %s? Its env file and home volume stay" % n, "n"):
            s.say("  %s stays" % n)
            return
        s.drop(n)
        self.save(n + ", removed")

    def act_shared(self):
        s = self.s
        before = (s.registry, s.claude_version)
        s.stage_env()
        self.q.shared(SHARED, claude=True)
        self.save("the shared settings, in .env", env_staged=True)
        # A registry changed: every image comes from it now; Claude Code's version: the Claude ones change.
        which = s.personas if s.registry != before[0] else \
            [p for p in s.personas if p.flavour == "claude"] if s.claude_version != before[1] else []
        if which and s.o.build:
            verb = self.verb()
            if s.ask.yesno("%s the %simages again, for %s? (seconds for one that is up to date)"
                           % (verb.capitalize(), "" if s.registry != before[0] else "Claude ",
                              "the new registry" if s.registry != before[0] else "Claude Code " + s.claude_version),
                           "y") and self.q.room(verb, which):
                self.build(verb, which)

    def act_telemetry(self):
        s = self.s
        s.section("Telemetry (OpenTelemetry to a collector)")
        before = (dict(s.tele), [(p.name, p.telemetry, p.tool_details, p.tool_content) for p in s.personas])
        self.q.telemetry()
        if (dict(s.tele), [(p.name, p.telemetry, p.tool_details, p.tool_content) for p in s.personas]) == before \
                and not s.ca_changed():
            s.say("  telemetry is as it was")
            s.tele_check = True       # gone through to try the collector again, maybe
            after.telemetry_check(s)
            return
        self.save("the telemetry settings")

    def act_launcher(self):
        self.s.section("The launcher")
        self.link()

    def link(self):
        s, o = self.s, self.s.o
        if o.link == "none":
            o.link = "/usr/local/bin"
        target = self.q.launcher()
        if not target and o.link != "none" and readlink_f(o.link.rstrip("/") + "/nexus") == s.launcher:
            s.say("-- %s/nexus is this launcher already" % o.link.rstrip("/"))
        after.link(s, target)
        if target and readlink_f(target) == s.launcher:
            s.say("-- linked %s" % target)

    def act_images(self):
        s = self.s
        if not s.personas:
            s.say("  no persona yet to build an image for: add one first")
            return
        verb = self.verb()
        if self.q.room(verb, s.personas):
            self.build(verb, s.personas)

    # --- images and mkcert, after an action ---
    def verb(self):
        return "build" if self.s.registry == "local" else "pull"

    def image(self, p):
        """The image of P, offered (unless --no-build): built or pulled now, if it is to be."""
        s = self.s
        verb = self.verb()
        if s.o.build and s.ask.yesno("%s agent-%s-%s now, for %s? (seconds for one that is up to date; a first "
                                     "build takes a few minutes)" % (verb.capitalize(), p.flavour, p.context, p.name),
                                     "y") and self.q.room(verb, [p]):
            self.build(verb, [p])

    def build(self, verb, personas):
        self.s.say("-- %sing; one that is already up to date takes seconds" % verb)
        self.failed |= after.images(self.s, verb, personas)

    def mkcert(self, p):
        if p.context == "ddev" and self.q.mkcert():
            after.mkcert(self.s)
