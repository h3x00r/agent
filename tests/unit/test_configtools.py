import os
import shutil
import stat
import tempfile
import unittest
from unittest import mock

from agentctl.adapters import compose_render
from agentctl.configtools import after, cli, menu, session, staging
from agentctl.configtools.host import cpus_ok, env_flavour
from agentctl.configtools.session import Session
from agentctl.core import proc
from agentctl.core.errors import NexusError
from agentctl.domain import notes
from agentctl.domain.persona import Persona


def read(path):
    with open(path) as f:
        return f.read()


def put(path, text):
    with open(path, "w") as f:
        f.write(text)


class Parse(unittest.TestCase):
    def test_flags(self):
        o = cli.parse(["--yes", "--persona", "c1:claude:docker", "--persona", "g1:grok:ddev", "--ssh", "g1:new",
                       "--no-link", "--no-build", "--statusline", "--no-statusline", "--registry", "a", "--registry", "b"])
        self.assertEqual((o.yes, o.add, o.ssh, o.link, o.build, o.statusline, o.registry),
                         (True, ["c1:claude:docker", "g1:grok:ddev"], ["g1:new"], "none", False, "no", "b"))
        self.assertEqual(cli.parse(["--permissions", "c1:ask", "--permissions", "c2:bypass"]).permissions,
                         ["c1:ask", "c2:bypass"])
        o = cli.parse(["--telemetry", "c1:on", "--telemetry", "g1:off", "--telemetry-endpoint", "https://c.lan:4318",
                       "--telemetry-token-file", "/t", "--telemetry-ca", "none"])
        self.assertEqual((o.telemetry, o.tele_endpoint, o.tele_token_file, o.tele_ca, o.tele_token),
                         (["c1:on", "g1:off"], "https://c.lan:4318", "/t", "none", None))
        self.assertEqual(cli.parse(["--tool-details", "all:on", "--tool-details", "c1:off"]).tool_details,
                         ["all:on", "c1:off"])
        o = cli.parse(["--tool-content", "c1:on", "--tool-details", "all:off"])
        self.assertEqual((o.tool_content, o.tool_details), (["c1:on"], ["all:off"]))
        self.assertEqual(cli.parse(["--ports", "c1:20000-20099", "--ports", "g1:x"]).ports, ["c1:20000-20099", "g1:x"])
        with self.assertRaises(NexusError) as c:
            cli.parse(["--ports"])
        self.assertEqual(c.exception.message, "--ports needs NAME:FROM-TO (see --help)")

    def test_help_stops_where_it_is_read(self):
        self.assertTrue(cli.parse(["--help", "--bogus"]).help)
        with self.assertRaises(NexusError) as c:
            cli.parse(["--bogus", "--help"])
        self.assertEqual(c.exception.message, "unknown option '--bogus' (see --help)")

    def test_a_value_is_the_next_argument_whatever_it_is(self):
        self.assertEqual(cli.parse(["--persona", "--no-link"]).add, ["--no-link"])
        for argv, msg in ((["--persona"], "--persona needs NAME:FLAVOUR:CONTEXT[:ROOT] (see --help)"),
                          (["--registry", ""], "--registry needs local or a HOST (see --help)")):
            with self.assertRaises(NexusError) as c:
                cli.parse(argv)
            self.assertEqual(c.exception.message, msg)


class Staging(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.cwd = os.getcwd()
        os.chdir(self.d)

    def tearDown(self):
        staging.cleanup()
        os.chdir(self.cwd)

    def test_stage_is_private_and_named_like_mktemp(self):
        put("x.env", "A=1\n")
        t = staging.stage("x.env", "x.env")
        self.assertTrue(t.startswith("x.env.") and "/" not in t)
        self.assertEqual(stat.S_IMODE(os.stat(t).st_mode), 0o600)
        self.assertEqual(read(t), "A=1\n")
        staging.cleanup()
        self.assertFalse(os.path.exists(t))

    def test_commit_writes_through_a_link_and_keeps_the_inode(self):
        os.mkdir("real")
        put("real/f", "old\n")
        os.chmod("real/f", 0o640)
        os.symlink("real/f", "f")
        ino = os.stat("real/f").st_ino
        t = staging.stage("f")
        staging.write(t, "new\n")
        staging.commit(t, "f", "f.bak")
        self.assertTrue(os.path.islink("f"))
        self.assertEqual((read("real/f"), os.stat("real/f").st_ino, stat.S_IMODE(os.stat("real/f").st_mode)),
                         ("new\n", ino, 0o640))
        self.assertEqual(read("f.bak"), "old\n")

    def test_an_unchanged_target_is_left_alone(self):
        put("f", "same\n")
        before = os.stat("f")
        t = staging.stage("f", "f")
        staging.commit(t, "f", "f.bak")
        self.assertEqual(os.stat("f").st_mtime_ns, before.st_mtime_ns)
        self.assertFalse(os.path.exists("f.bak") or os.path.exists(t))

    def test_a_new_target_is_renamed_into_place(self):
        t = staging.stage("n")
        staging.write(t, "x\n")
        staging.commit(t, "n")
        self.assertEqual(stat.S_IMODE(os.stat("n").st_mode), 0o600)

    def test_replace_renames_into_place_and_keeps_the_old_one(self):
        put("p.json", "old\n")
        ino = os.stat("p.json").st_ino
        t = staging.stage("p.json")
        staging.write(t, "new\n")
        staging.replace(t, "p.json", "p.json.bak")
        self.assertEqual((read("p.json"), read("p.json.bak")), ("new\n", "old\n"))
        self.assertNotEqual(os.stat("p.json").st_ino, ino)          # a rename: never half a file
        t = staging.stage("p.json")
        staging.write(t, "new\n")
        staging.replace(t, "p.json", "p.json.bak")                  # unchanged: left alone, no backup of it
        self.assertEqual((read("p.json.bak"), os.path.exists(t)), ("old\n", False))

    def test_bakname_keeps_older_backups(self):
        self.assertEqual(staging.bakname("f"), "f.bak")
        open("f.bak", "w").close()
        os.symlink("nowhere", "f.bak.2")
        self.assertEqual(staging.bakname("f"), "f.bak.3")


class Rules(unittest.TestCase):
    def test_cpus(self):
        self.assertTrue(cpus_ok("4", 6))
        self.assertTrue(cpus_ok(".5", 6))
        self.assertFalse(cpus_ok("0", 6))
        self.assertFalse(cpus_ok("7", 6))
        self.assertFalse(cpus_ok("4x", 6))
        self.assertFalse(cpus_ok("", 6))

    def test_env_flavour(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "x.env")
            for text, want in (("# x\nXAI_API_KEY=\n", "grok"), ("#ANTHROPIC_API_KEY=\n", "claude"),
                               (" export  CLAUDE_CODE_OAUTH_TOKEN =x\n", "claude"), ("GH_TOKEN=\n", ""),
                               ("A=1\n#XAI_API_KEY=x\nANTHROPIC_API_KEY=y\n", "grok")):
                with open(p, "w") as f:
                    f.write(text)
                self.assertEqual(env_flavour(p), want, repr(text))



class _Session:
    """What Menu reads of a Session, for its parts that only look."""
    def __init__(self, checkout, names):
        self.a, self.launcher = checkout, os.path.join(checkout, "nexus")
        self.personas = [type("P", (), {"name": n})() for n in names]

    def idx(self, name):
        return next((i for i, p in enumerate(self.personas) if p.name == name), None)


class Menu(unittest.TestCase):
    def test_every_action_is_a_method(self):
        keys = [o.split(" ", 1)[0] for o in menu.ACTIONS]
        self.assertEqual(keys, ["list", "add", "change", "remove", "shared", "telemetry", "launcher", "images", "quit"])
        for k in keys[:-1]:
            self.assertTrue(callable(getattr(menu.Menu, "act_" + k)), k)

    def test_leftovers_are_env_files_of_no_persona(self):
        with tempfile.TemporaryDirectory() as d:
            os.mkdir(os.path.join(d, "personas"))
            for n, text in (("c1", ""), ("o1", "ANTHROPIC_API_KEY=\n"), ("g2", "# XAI_API_KEY=\n"), ("o3", ""),
                            ("Bad", ""), (".x", ""), ("c1.env", "")):
                put(os.path.join(d, "personas", n + ".env"), text)
            os.mkdir(os.path.join(d, "personas", "dir.env"))
            put(os.path.join(d, "personas", "o1.env.example"), "")
            m = menu.Menu.__new__(menu.Menu)
            m.s = _Session(d, ["c1"])
            old = os.getcwd()
            os.chdir(d)
            try:
                self.assertEqual(m.leftovers(), [("g2", "grok"), ("o1", "claude"), ("o3", "")])
            finally:
                os.chdir(old)

    def test_the_launcher_as_path_finds_it(self):
        with tempfile.TemporaryDirectory() as d:
            put(os.path.join(d, "nexus"), "#!/bin/sh\n")
            os.chmod(os.path.join(d, "nexus"), 0o755)
            for b in ("mine", "other"):
                os.mkdir(os.path.join(d, b))
            os.symlink(os.path.join(d, "nexus"), os.path.join(d, "mine", "nexus"))
            put(os.path.join(d, "other", "nexus"), "#!/bin/sh\n")
            os.chmod(os.path.join(d, "other", "nexus"), 0o755)
            m = menu.Menu.__new__(menu.Menu)
            m.s = _Session(d, [])
            old = os.environ.get("PATH")
            try:
                for path, want in ((os.path.join(d, "mine"), os.path.join(d, "mine", "nexus")),
                                   (os.path.join(d, "other"), "`nexus` on your PATH is %s/other/nexus, another one" % d),
                                   (os.path.join(d, "none"), "not on your PATH: link it with launcher")):
                    os.environ["PATH"] = path + ":/usr/bin:/bin"
                    self.assertTrue(m.launcher_state().startswith(want), (path, m.launcher_state()))
            finally:
                os.environ["PATH"] = old


class _Env:
    """os.environ with KEYS set (None: unset) for the length of a with block."""
    def __init__(self, **keys):
        self.keys, self.old = keys, {}

    def __enter__(self):
        for k, v in self.keys.items():
            self.old[k] = os.environ.pop(k, None)
            if v is not None:
                os.environ[k] = v

    def __exit__(self, *exc):
        for k, v in self.old.items():
            os.environ.pop(k, None)
            if v is not None:
                os.environ[k] = v


class VmSettings(unittest.TestCase):
    """NEXUS_SCRATCH_ROOT and DOCKER_GUARD: set by hand only (in .env, or the shell), checked before a save."""
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.cwd = os.getcwd()
        os.chdir(self.d)
        self.s = Session.__new__(Session)
        self.s.a, self.s.env_new = self.d, os.path.join(self.d, ".env.Staged1")

    def tearDown(self):
        os.chdir(self.cwd)
        shutil.rmtree(self.d)

    def test_refused(self):
        for shell, dot, msg in ((None, "NEXUS_SCRATCH_ROOT=rel/x\n", "NEXUS_SCRATCH_ROOT in %s/.env must be an absolute"),
                                (None, "NEXUS_SCRATCH_ROOT=/\n", "not '/', so nothing was written: fix it there"),
                                ("/a b", "", "NEXUS_SCRATCH_ROOT in your shell must be"),
                                ("/a:b", "", "unset it, or fix it"),
                                (None, "DOCKER_GUARD=yes\n", "DOCKER_GUARD in %s/.env is neither on nor off")):
            put(self.s.env_new, dot)
            with _Env(NEXUS_SCRATCH_ROOT=shell, DOCKER_GUARD=None):
                with self.assertRaises(NexusError) as e:
                    self.s.vm_problem()
            self.assertIn(msg.replace("%s", self.d), e.exception.message)
        with _Env(NEXUS_SCRATCH_ROOT=None, DOCKER_GUARD="maybe"):
            put(self.s.env_new, "")
            with self.assertRaises(NexusError) as e:
                self.s.vm_problem()
            self.assertIn("DOCKER_GUARD in your shell", e.exception.message)
        for dot in ("", "NEXUS_SCRATCH_ROOT=\nDOCKER_GUARD=\n", "NEXUS_SCRATCH_ROOT=/srv/s/\nDOCKER_GUARD=off\n",
                    "#NEXUS_SCRATCH_ROOT=rel\n#DOCKER_GUARD=x\n"):
            put(self.s.env_new, dot)
            with _Env(NEXUS_SCRATCH_ROOT="", DOCKER_GUARD="on"):
                self.s.vm_problem()

    def test_resolved(self):
        put(".env", "NEXUS_SCRATCH_ROOT=/in/dotenv\nDOCKER_GUARD=off\n")
        put(self.s.env_new, "NEXUS_SCRATCH_ROOT=/in/staged/\n")
        with _Env(NEXUS_SCRATCH_ROOT=None, DOCKER_GUARD=None):
            self.assertEqual((self.s.scratch_root(), self.s.guard()), ("/in/staged", "on"))
            os.unlink(self.s.env_new)              # in place now: .env is what counts
            self.assertEqual((self.s.scratch_root(), self.s.guard()), ("/in/dotenv", "off"))
            self.s.env_new = None
            self.assertEqual(self.s.scratch_root(), "/in/dotenv")
            os.unlink(".env")
            self.assertEqual((self.s.scratch_root(), self.s.guard()), ("/srv/nexus-scratch", "on"))
        with _Env(NEXUS_SCRATCH_ROOT="/in/shell", DOCKER_GUARD="off"):
            self.assertEqual((self.s.scratch_root(), self.s.guard()), ("/in/shell", "off"))

    def test_guard_changed(self):
        # A persona's notes there say the guard the other way from DOCKER_GUARD now: written anew, and a restart, as its
        # container's guard is the one it was made with. A new persona, notes another template wrote, notes that say
        # it as it is, or a persona told to restart already: nothing more.
        s = self.s
        os.mkdir("personas")
        c = [Persona(name=n, flavour="claude", context="docker", colour="colour1") for n in ("c1", "c2", "c3", "c4", "c5")]
        s.home, s.personas, s.nstaged, s.nguard = "/h", c, {}, {}
        s.loaded = {p.name: Session.state(p) for p in c[:4]}           # c5 is new
        s.later = ["c4: changed; `nexus c4 restart` applies it (that ends its tmux sessions)"]
        put(s.env_new, "DOCKER_GUARD=off\n")
        tpl = "{{persona}} ({{cli}}): the Docker guard is `{{guard}}` here.\n"
        for n, text in (("c1", "on"), ("c2", "off"), ("c4", "on"), ("c5", "on")):
            put(notes.file(n), "%s (Claude Code): the Docker guard is `%s` here.\n" % (n, text))
        put(notes.file("c3"), "c3 (Claude Code): an older template; the guard is `on`.\n")
        try:
            with _Env(DOCKER_GUARD=None, NEXUS_SCRATCH_ROOT=None):
                s.stage_notes(tpl)
            self.assertEqual(s.nguard, {"c1": "off", "c4": "off"})
            s.guard_notes()
            self.assertEqual(s.later[1:], ["c1: the Docker guard is off now (DOCKER_GUARD), as its notes say; `nexus c1 "
                                           "restart` applies it (that ends its tmux sessions)"])
            self.assertEqual(s.restarts(), ["c1", "c4"])
            self.assertEqual(read(s.nstaged["c1"]), "c1 (Claude Code): the Docker guard is `off` here.\n")
        finally:
            staging.cleanup()


class _Saved:
    """What after.scratch_root reads of a Session."""
    def __init__(self, root, yes=True, personas=("c1",)):
        self.root, self.personas, self.later, self.said = root, list(personas), [], []
        self.scratch_tried = False
        self.o = type("O", (), {"yes": yes})()

    def scratch_root(self):
        return self.root

    def say(self, line):
        self.said.append(line)


class ScratchRoot(unittest.TestCase):
    def test_branches(self):
        with tempfile.TemporaryDirectory() as d:
            ran = []

            def run(argv, capture=True, **kw):
                ran.append(argv)
                return proc.Completed(1 if "fail" in argv[-1] else 0)
            with mock.patch.object(after.proc, "run", run):
                # There already: nothing, not even install -d (it would chmod and chown it).
                after.scratch_root(_Saved(d))
                # Its parent yours: mkdir, mode 755 whatever the umask; no sudo.
                s, old = _Saved(os.path.join(d, "new", "scratch")), os.umask(0o077)
                try:
                    after.scratch_root(s)
                finally:
                    os.umask(old)
                self.assertEqual(stat.S_IMODE(os.stat(s.root).st_mode), 0o755)
                self.assertEqual((ran, s.said, s.later), ([], [], []))
                # There, but you can't write in it (an admin's `sudo mkdir`): a note with the command that makes it
                # yours while a persona's dir is still to be made in it; once they are all there, nothing (one chowned
                # away after works on). Never install -d itself.
                if os.geteuid() != 0:
                    ro = os.path.join(d, "ro")
                    os.mkdir(ro, 0o555)
                    try:
                        s = _Saved(ro)
                        after.scratch_root(s)
                        self.assertEqual((ran, s.said, len(s.later)), ([], [], 1))
                        self.assertIn("you can't write in the shared scratch root %s, so nexus can't make a persona's "
                                      "dir in it; run: sudo install -d -o $(id -u) -g $(id -g) -m 755 %s, or point "
                                      "NEXUS_SCRATCH_ROOT" % (ro, ro), s.later[0])
                        self.assertNotIn("restart", s.later[0])
                        os.chmod(ro, 0o755)
                        os.mkdir(os.path.join(ro, "c1"))
                        os.chmod(ro, 0o555)
                        s = _Saved(ro)
                        after.scratch_root(s)
                        self.assertEqual((ran, s.said, s.later), ([], [], []))
                    finally:
                        os.chmod(ro, 0o755)
                # A file (or a link to nothing) in its place: a note to move it aside, no sudo (install -d would fail).
                put(os.path.join(d, "afile"), "")
                s = _Saved(os.path.join(d, "afile"))
                after.scratch_root(s)
                self.assertEqual((ran, s.said, len(s.later)), ([], [], 1))
                self.assertIn("afile isn't a directory, and every persona needs it to start: move it aside", s.later[0])
                self.assertNotIn("install -d", s.later[0])
                self.assertNotIn("restart", s.later[0])
                # Not: sudo (-n under --yes), as yours (your uid, then your gid); said first.
                with mock.patch.object(after, "mkdir_ok", lambda p: False), \
                        mock.patch.object(after.os, "getuid", lambda: 1234), \
                        mock.patch.object(after.os, "getgid", lambda: 5678):
                    s = _Saved("/srv/ok")
                    after.scratch_root(s)
                    self.assertEqual(ran.pop(), ["sudo", "-n", "install", "-d", "-o", "1234", "-g", "5678", "-m", "755",
                                                 "/srv/ok"])
                    self.assertEqual((s.said, s.later), (["-- creating the shared scratch root /srv/ok needs sudo"], []))
                    after.scratch_root(s)                     # once a run
                    self.assertEqual(ran, [])
                    # sudo failed: a note with the command (and the way without sudo), which never reads as a restart
                    # to do.
                    s = _Saved("/srv/fail", yes=False)
                    after.scratch_root(s)
                    self.assertEqual(ran.pop(), ["sudo", "install", "-d", "-o", "1234", "-g", "5678", "-m", "755",
                                                 "/srv/fail"])
                    self.assertEqual(len(s.later), 1)
                    self.assertIn("run: sudo install -d -o $(id -u) -g $(id -g) -m 755 /srv/fail, or point "
                                  "NEXUS_SCRATCH_ROOT (.env, or your shell) at a directory of your own", s.later[0])
                    self.assertNotIn("restart", s.later[0])
                    # No persona: none needed.
                    s = _Saved("/srv/ok", personas=())
                    after.scratch_root(s)
                    self.assertEqual((ran, s.said), ([], []))



def _ports_session(*personas, argv=()):
    """A Session with PERSONAS as read (loaded) and the options ARGV, for its parts on port ranges."""
    s = Session.__new__(Session)
    s.o, s.home, s.personas, s.later, s.warned = cli.parse(list(argv)), "/h", list(personas), [], set()
    s.loaded = {p.name: Session.state(p) for p in personas}
    s.unranged = {p.name for p in personas if not p.ports}     # as open() leaves them
    s.warnings = []
    s.out = type("Out", (), {"warn": lambda self, m: s.warnings.append(m)})()
    return s


class Ports(unittest.TestCase):
    def test_fill(self):
        """A range for each persona without one, after the highest, in persona order; one read without one brings
        no "changed" note of its own, while a change the flags made still does."""
        c1, c2, g1 = (Persona("c1", "claude", "docker", "colour25"),
                      Persona("c2", "claude", "docker", "colour28", ports="20000-20099"),
                      Persona("g1", "grok", "docker", "colour90"))
        s = _ports_session(c1, c2, g1)
        c1.permissions = "ask"                                   # as --permissions c1:ask leaves it
        s.put(Persona("n1", "claude", "docker", "colour1"))      # a new one, from a question that gave it none
        s.fill_ports()
        self.assertEqual([p.ports for p in s.personas], ["20100-20199", "20000-20099", "20200-20299", "20300-20399"])
        self.assertEqual((s.loaded["c1"][-1], s.loaded["g1"], "n1" in s.loaded), ("20100-20199", s.state(g1), False))
        s.change_notes(["c1", "c2", "g1"])
        self.assertEqual(s.later, ["c1: changed; `nexus c1 restart` applies it (that ends its tmux sessions)"])
        s.fill_ports()                                           # again: nothing more
        self.assertEqual([p.ports for p in s.personas], ["20100-20199", "20000-20099", "20200-20299", "20300-20399"])
        # One read with a range, removed and given again (--remove c1 --persona c1:…): the block after the highest,
        # so a change; the highest one's, given again, gets its own back.
        def again(name):
            s = _ports_session(Persona("c1", "claude", "docker", "colour25", ports="20000-20099"),
                               Persona("c2", "claude", "docker", "colour28", ports="20100-20199"))
            s.drop(name)
            s.add_flag(name + ":claude:docker")
            s.fill_ports()
            s.change_notes(["c1", "c2"])
            return s.personas[-1].ports, s.later
        self.assertEqual(again("c1"), ("20200-20299", ["c1: changed; `nexus c1 restart` applies it (that ends its tmux "
                                                       "sessions)"]))
        self.assertEqual(again("c2"), ("20100-20199", []))
        # None free: none, and a warning that says how to give it one.
        full = [Persona("p%d" % i, "claude", "docker", "colour1", ports="%d-%d" % (a, a + 99))
                for i, a in enumerate(range(20000, 65500, 100))]
        s = _ports_session(*full + [Persona("z1", "grok", "docker", "colour1")])
        s.fill_ports()
        self.assertEqual(s.personas[-1].ports, "")
        self.assertEqual(len(s.warnings), 1)
        self.assertIn("so z1 has no port range: give it one with --ports z1:FROM-TO", s.warnings[0])

    def test_flags(self):
        def refused(s, how, msg):
            with self.assertRaises(NexusError) as c:
                getattr(s, how)()
            self.assertIn(msg, c.exception.message)
        # The syntax, before the lock.
        for spec in ("c1", "c1:", "C1:20000-20099", "c1:20000", "c1:80-180", "c1:30000-20000", "c1:20000-70000"):
            refused(_ports_session(argv=("--ports", spec)), "check_flags",
                    "--ports %s: it takes NAME:FROM-TO, two ports from 1024 to 65535" % spec)
        _ports_session(argv=("--ports", "c1:1024-65535")).check_flags()

        def two():
            return (Persona("c1", "claude", "docker", "colour25", ports="20000-20099"),
                    Persona("c2", "claude", "docker", "colour28", ports="20100-20199"),
                    Persona("g1", "grok", "docker", "colour90"))
        # Two can swap: all in place first, then checked.
        s = _ports_session(*two(), argv=("--ports", "c1:20100-20199", "--ports", "c2:20000-20099"))
        s.ports_flags()
        self.assertEqual([p.ports for p in s.personas], ["20100-20199", "20000-20099", ""])
        refused(_ports_session(*two(), argv=("--ports", "g1:20150-20250")), "ports_flags",
                "--ports g1:20150-20250: that range overlaps c2's (20100-20199), so nothing was written")
        refused(_ports_session(*two(), argv=("--ports", "g1:30000-30099", "--ports", "c1:30099-30100")), "ports_flags",
                "--ports g1:30000-30099: that range overlaps c1's (30099-30100)")
        refused(_ports_session(*two(), argv=("--ports", "x1:30000-30099")), "ports_flags",
                "--ports x1:30000-30099: no persona x1 (add it with --persona)")
        # --persona: an existing one keeps its range; a new one gets none until fill_ports(), after the flags, so a
        # --ports given with it isn't refused for a block the new one was handed on the way.
        s = _ports_session(*two(), argv=("--ports", "c1:20200-20299"))
        s.add_flag("c2:grok:ddev")
        s.add_flag("n1:claude:docker")
        self.assertEqual([p.ports for p in s.personas], ["20000-20099", "20100-20199", "", ""])
        s.ports_flags()
        s.fill_ports()
        self.assertEqual([p.ports for p in s.personas], ["20200-20299", "20100-20199", "20300-20399", "20400-20499"])

    def test_rewritten_notes(self):
        """A service as an older nexus --config wrote it (no scratch dir, ports or notes) brings a note that says so,
        with the restart nexus --update counts; one edited by hand, the hand-edit note; one as it is now, none."""
        ps = [Persona("c1", "claude", "docker", "colour25", ports="20000-20099"),
              Persona("g1", "grok", "ddev", "colour28", ssh="none", ports="20100-20199"),
              Persona("c2", "claude", "docker", "colour90", ports="20200-20299"),
              Persona("c3", "claude", "docker", "colour1", ports="20300-20399"),
              Persona("c4", "claude", "docker", "colour1", ports="20400-20499")]
        old = compose_render.HEADER + "\nservices:\n" + compose_render.service(ps[0], True, older=True) \
            + compose_render.service(ps[1], True, older=True) \
            + compose_render.service(ps[2], True, older=True).replace("/home/dev\n", "/home/dev\n      - /x:/x\n") \
            + compose_render.service(ps[3], True) + compose_render.service(ps[4], False, older=True) + "\nvolumes:\n"
        s = _ports_session(*ps)
        s.later.append("c4: changed; `nexus c4 restart` applies it (that ends its tmux sessions)")
        s.rewritten_notes(old)
        self.assertEqual(s.later[1:], [
            "c1: its service has the shared scratch dir, its port range and the Nexus notes now; `nexus c1 restart` "
            "applies them (that ends its tmux sessions)",
            "g1: its service has the shared scratch dir, its port range and the Nexus notes now; `nexus g1 restart` "
            "applies them (that ends its tmux sessions)",
            "c2: its service in compose.personas.yml wasn't what personas.json gives (edited by hand?); `nexus c2 "
            "restart` applies the one written now (that ends its tmux sessions)"])
        self.assertEqual(s.restarts(), ["c1", "g1", "c2", "c4"])

    def test_paths_alone(self):
        """A grok config whose settings changed by the notes' [paths] table alone, in a compose file that doesn't mount
        the notes yet (the restart its service is told brings both); not when anything else changed, or when it
        does."""
        with tempfile.TemporaryDirectory() as d:
            cwd = os.getcwd()
            os.chdir(d)
            try:
                put("old", "# header\n\n[ui]\nx = 1\n")
                put("new", "# the header, reworded\n\n[ui]\nx = 1\n" + notes.GROK_PATHS)
                put("other", "# header\n\n[ui]\nx = 2\n" + notes.GROK_PATHS)
                put("same", "# header\n\n[ui]\nx = 1\n")
                put("compose.personas.yml", "services:\n  g1:\n      - { type: bind, source: ./personas/g2.notes.md, …\n")
                self.assertTrue(session.paths_alone("g1", "new", "old"))
                self.assertFalse(session.paths_alone("g1", "other", "old"))
                self.assertFalse(session.paths_alone("g1", "same", "old"))
                self.assertFalse(session.paths_alone("g1", "new", "missing"))
                self.assertFalse(session.paths_alone("g2", "new", "old"))      # its notes mounted already
            finally:
                os.chdir(cwd)


if __name__ == "__main__":
    unittest.main()
