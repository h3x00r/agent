import os
import stat
import tempfile
import unittest

from agentctl.configtools import cli, menu, staging
from agentctl.configtools.host import cpus_ok, env_flavour
from agentctl.core.errors import NexusError


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


if __name__ == "__main__":
    unittest.main()
