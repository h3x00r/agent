import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

from agentctl.adapters import docker
from agentctl.nexus import top

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

MERGE = docker.probe("claude_defaults.py")


def merge(home, defaults):
    """The defaults merge as it runs in a persona: python3 -c SCRIPT, personas/claude.json on stdin."""
    r = subprocess.run([sys.executable, "-I", "-c", MERGE], input=json.dumps(defaults).encode(),
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=dict(os.environ, HOME=home))
    return r.returncode, r.stdout.decode().strip()


def settings(home):
    with open(os.path.join(home, ".claude", "settings.json")) as f:
        return json.load(f)


def write(home, name, data):
    os.makedirs(os.path.join(home, ".claude"), exist_ok=True)
    with open(os.path.join(home, ".claude", name), "w") as f:
        f.write(data if isinstance(data, str) else json.dumps(data))


class Probes(unittest.TestCase):
    def test_the_bytes_the_bash_launcher_sent(self):
        # The sha256 the goldens record for each -c argument: the files must stay these bytes. The kill
        # script isn't in a golden (no scenario interrupts a headless run); its hash is the bash's too.
        for name, want in (("sess_probe.sh", "8da22a88df2a1204"), ("top_probe.sh", "3e171a573e332d85"),
                           ("claude_defaults.py", "5d2b156a28385590"), ("headless_kill.sh", "9da5d7538be2706c")):
            self.assertEqual(hashlib.sha256(docker.probe(name).encode()).hexdigest()[:16], want, name)


class DefaultsMerge(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp()

    def test_a_new_persona_gets_them(self):
        rc, out = merge(self.home, {"model": "opus", "modelSettings": {"opus": {"effortLevel": "low"}}})
        self.assertEqual(rc, 0)
        self.assertEqual(out, "Claude defaults from personas/claude.json: model, modelSettings.opus.effortLevel")
        self.assertEqual(settings(self.home), {"model": "opus", "modelSettings": {"opus": {"effortLevel": "low"}}})

    def test_own_values_and_removals_stay(self):
        merge(self.home, {"model": "opus", "theme": "dark", "tui": "x"})
        s = settings(self.home)
        s["theme"] = "light"          # the persona's own now
        del s["tui"]                   # taken out: stays out
        write(self.home, "settings.json", s)
        rc, out = merge(self.home, {"model": "sonnet", "theme": "dark", "tui": "y"})
        self.assertEqual((rc, out), (0, "Claude defaults from personas/claude.json: model"))
        self.assertEqual(settings(self.home), {"model": "sonnet", "theme": "light"})

    def test_a_migrated_model_still_counts_as_the_default(self):
        merge(self.home, {"model": "opus"})
        write(self.home, "settings.json", {"model": "opus[1m]"})
        rc, out = merge(self.home, {"model": "sonnet"})
        self.assertEqual(settings(self.home), {"model": "sonnet"})

    def test_a_dropped_default_goes(self):
        merge(self.home, {"model": "opus", "theme": "dark"})
        rc, out = merge(self.home, {"model": "opus"})
        self.assertEqual(out, "Claude defaults from personas/claude.json: theme (no longer a default)")
        self.assertEqual(settings(self.home), {"model": "opus"})

    def test_broken_settings_are_left_alone(self):
        write(self.home, "settings.json", "{not json")
        rc, out = merge(self.home, {"model": "opus"})
        self.assertEqual((rc, out), (3, "its ~/.claude/settings.json is not valid JSON, so the defaults stayed out of it"))


class TopFigures(unittest.TestCase):
    def test_tokens(self):
        self.assertEqual([top.tok(n) for n in (640, 1000, 38000, 999499, 999500, 1000000, 1250000, 2000000)],
                         ["640", "1k", "38k", "999k", "1000k", "1M", "1.3M", "2M"])

    def test_ages(self):
        self.assertEqual([top.age(s) for s in (-5, 12, 60, 3599, 3600, 86399, 86400 * 4)],
                         ["0s", "12s", "1m", "59m", "1h", "23h", "4d"])

    def test_cuts_by_locale(self):
        self.assertEqual(top._head("éééé", 2, True), "éé")
        self.assertEqual(top._head("éééé", 3, False).encode("utf-8", "surrogateescape"), "é".encode() + b"\xc3")
        self.assertEqual(top._head("abcd", -1, True), "abc")
        self.assertEqual(top._no_controls("a\x1bb\u009dc", True), "abc")
        self.assertEqual(top._no_controls("a\x1bb\u009dc", False), "ab\u009dc")


if __name__ == "__main__":
    unittest.main()


class TakeMode(unittest.TestCase):
    def test_where_the_words_are_taken(self):
        from agentctl.nexus.persona_cmds import take_mode
        skip, bypass = "--skip-bypass-permissions", "--bypass-permissions"
        self.assertEqual(take_mode([]), ("", "", []))
        self.assertEqual(take_mode([skip]), ("ask", skip, []))
        self.assertEqual(take_mode(["s1", bypass, "--", "claude"]), ("bypass", bypass, ["s1", "--", "claude"]))
        self.assertEqual(take_mode([skip, skip, "s1"]), ("ask", skip, ["s1"]))
        # After a --, the command's; in a headless run's arguments, anywhere.
        self.assertEqual(take_mode(["s1", "--", "claude", skip]), ("", "", ["s1", "--", "claude", skip]))
        self.assertEqual(take_mode(["headless", "--", "hi", skip]), ("ask", skip, ["headless", "--", "hi"]))
        self.assertEqual(take_mode([bypass, "headless", "hi"]), ("bypass", bypass, ["headless", "hi"]))
        # Any other -x stays.
        self.assertEqual(take_mode(["-s", "--skip"]), ("", "", ["-s", "--skip"]))

    def test_both_refused(self):
        from agentctl.core.errors import NexusError
        from agentctl.nexus.persona_cmds import take_mode
        with self.assertRaises(NexusError):
            take_mode(["--skip-bypass-permissions", "s1", "--bypass-permissions"])


class Scratch(unittest.TestCase):
    def test_scratch_dir(self):
        from agentctl.core.errors import NexusError
        from agentctl.nexus.persona_cmds import NoScratch, scratch_dir
        with tempfile.TemporaryDirectory() as d:
            for root, msg in (("rel", "the shared scratch root must be an absolute directory other than /"),
                              (d + "/a b", "without ':' or whitespace, not '%s/a b' (from: here)" % d)):
                with self.assertRaises(NexusError) as e:
                    scratch_dir(root, "c1", "from: here", "/a")
                self.assertIn(msg, e.exception.message)
                self.assertNotIsInstance(e.exception, NoScratch)        # refused even at an attach to a running one
            with self.assertRaises(NoScratch) as e:
                scratch_dir(d + "/none", "c1", "from: here", "/a")
            self.assertEqual(e.exception.message,
                             "the shared scratch root %s/none isn't there (from: here): /a/nexus --config creates it, or "
                             "run: sudo install -d -o $(id -u) -g $(id -g) -m 755 %s/none; or point NEXUS_SCRATCH_ROOT "
                             "(.env, or your shell) at a directory of your own" % (d, d))
            # A file, or a link to nothing: install -d would fail on it, so it isn't offered.
            open(os.path.join(d, "file"), "w").close()
            os.symlink(d + "/nowhere", d + "/dangling")
            for f in ("file", "dangling"):
                with self.assertRaises(NoScratch) as e:
                    scratch_dir(d + "/" + f, "c1", "x", "/a")
                self.assertIn("%s isn't a directory (x): move it aside (nexus --config then creates the root), or point "
                              "NEXUS_SCRATCH_ROOT" % f, e.exception.message)
                self.assertNotIn("install -d", e.exception.message)
            # made, mode 755 under a tight umask too; one there keeps its mode
            old = os.umask(0o177)
            try:
                self.assertEqual(scratch_dir(d, "c1", "x", "/a"), d + "/c1")
            finally:
                os.umask(old)
            self.assertEqual(oct(os.stat(d + "/c1").st_mode & 0o777), "0o755")
            os.chmod(d + "/c1", 0o700)
            scratch_dir(d, "c1", "x", "/a")
            self.assertEqual(oct(os.stat(d + "/c1").st_mode & 0o777), "0o700")
            if os.geteuid() != 0:
                # A root you can't write in: said so, with the command that makes it yours.
                os.mkdir(d + "/ro", 0o555)
                with self.assertRaises(NoScratch) as e:
                    scratch_dir(d + "/ro", "c1", "x", "/a")
                self.assertEqual(e.exception.message,
                                 "can't create c1's scratch dir %s/ro/c1 (Permission denied): you can't write in the "
                                 "shared scratch root %s/ro (x); make it yours: sudo install -d -o $(id -u) -g $(id -g) "
                                 "-m 755 %s/ro, or point NEXUS_SCRATCH_ROOT (.env, or your shell) at a directory of your "
                                 "own" % (d, d, d))
                os.chmod(d + "/ro", 0o755)

    def test_made_without(self):
        from agentctl.nexus.persona_cmds import Run
        said = []
        r = Run.__new__(Run)
        r.out = type("Out", (), {"warn": lambda self, m: said.append(m)})()
        r.p = type("P", (), {"name": "c1", "flavour": "claude", "scratch": "/s/c1"})()
        for mounts, want in (({"/home/dev", "/s/c1", "/etc/claude-code/CLAUDE.md"}, None),
                             (set(), None),                                           # can't be inspected: nothing
                             ({"/home/dev", "/old/c1", "/etc/claude-code/CLAUDE.md"},
                              "c1's container was made without its scratch dir /s/c1: `nexus c1 restart` adds it"),
                             ({"/home/dev", "/etc/grok/rules/00-nexus.md"},
                              "made without its scratch dir /s/c1 and the Nexus notes: `nexus c1 restart` adds them"),
                             ({"/home/dev", "/s/c1"},                               # the notes alone: them, not it
                              "c1's container was made without the Nexus notes: `nexus c1 restart` adds them")):
            del said[:]
            r.made_without(mounts)
            if want is None:
                self.assertEqual(said, [], mounts)
            else:
                self.assertEqual(len(said), 1)
                self.assertIn(want, said[0])


SKIP = "--skip-bypass-permissions"


class TakeWrapper(unittest.TestCase):
    def refused(self, args, msg):
        from agentctl.core.errors import NexusError
        from agentctl.nexus.persona_cmds import take_wrapper
        with self.assertRaises(NexusError) as e:
            take_wrapper(args)
        self.assertEqual(e.exception.message, msg, args)

    def test_where_it_is_taken(self):
        from agentctl.nexus.persona_cmds import take_wrapper
        self.assertEqual(take_wrapper([]), ("", []))
        self.assertEqual(take_wrapper(["--wrapper", "F", "headless", "--", "hi"]), ("F", ["headless", "--", "hi"]))
        self.assertEqual(take_wrapper(["--wrapper=F"]), ("F", []))
        self.assertEqual(take_wrapper(["s1", "--wrapper=./-x", "--", "grok"]), ("./-x", ["s1", "--", "grok"]))
        self.assertEqual(take_wrapper([SKIP, "s1", "--wrapper", "F"]), ("F", [SKIP, "s1"]))
        # After a --, the command's: a shim's own word (the -- form) or anything else's.
        self.assertEqual(take_wrapper(["s1", "--", "grok", "--wrapper", "F"]), ("", ["s1", "--", "grok", "--wrapper", "F"]))
        self.assertEqual(take_wrapper(["s1", "--", "echo", "--wrapper=F"]), ("", ["s1", "--", "echo", "--wrapper=F"]))
        self.assertEqual(take_wrapper(["s1", "--", "echo", "--wrapper", "F"]), ("", ["s1", "--", "echo", "--wrapper", "F"]))
        # The shim's own bare -- ends its options: a --wrapper after it is grok's words, so nexus's isn't given twice.
        self.assertEqual(take_wrapper(["--wrapper", "F", "s1", "--", "grok", "--", "--wrapper", "G"]),
                         ("F", ["s1", "--", "grok", "--", "--wrapper", "G"]))
        # In a headless run's arguments, anywhere: after its -- too, and with the mode word or --wrapper before it.
        self.assertEqual(take_wrapper(["headless", "hi", "--wrapper", "F"]), ("F", ["headless", "hi"]))
        self.assertEqual(take_wrapper(["headless", "--", "hi", "--wrapper=F", "--model", "m"]),
                         ("F", ["headless", "--", "hi", "--model", "m"]))
        self.assertEqual(take_wrapper([SKIP, "headless", "--", "hi", "--wrapper", "F"]), ("F", [SKIP, "headless", "--", "hi"]))
        # A -- that only the word came after goes with it, so grok gets no bare -- last; one of the caller's own stays.
        self.assertEqual(take_wrapper(["headless", "hi", "--", "--wrapper=F"]), ("F", ["headless", "hi"]))
        self.assertEqual(take_wrapper(["headless", "hi", "--", "--wrapper", "F"]), ("F", ["headless", "hi"]))
        self.assertEqual(take_wrapper(["headless", "hi", "--wrapper=F", "--"]), ("F", ["headless", "hi", "--"]))
        self.assertEqual(take_wrapper(["headless", "hi", "--"]), ("", ["headless", "hi", "--"]))
        self.assertEqual(take_wrapper(["s1", "--wrapper", "F", "--"]), ("F", ["s1", "--"]))
        # --no-wrapper is no word of nexus's.
        self.assertEqual(take_wrapper(["--no-wrapper"]), ("", ["--no-wrapper"]))
        self.assertEqual(take_wrapper(["headless", "hi", "--no-wrapper"]), ("", ["headless", "hi", "--no-wrapper"]))

    def test_what_it_refuses(self):
        need = "--wrapper needs a file: --wrapper FILE, or --wrapper=FILE"
        self.refused(["s1", "--wrapper"], need)
        self.refused(["headless", "hi", "--wrapper"], need)
        self.refused(["--wrapper", "--"], need)
        self.refused(["--wrapper="], need)
        self.refused(["--wrapper", ""], need)
        self.refused(["--wrapper", "-x", "s1"], "--wrapper needs a file, not '-x': a file whose name starts with - goes as ./-x")
        self.refused(["--wrapper=a\tb"], '--wrapper "a\\tb": a path with a newline or another control character can\'t be '
                     'passed on: rename the file')
        self.refused(["--wrapper", "a\x7fb"], '--wrapper "a\\u007fb": a path with a newline or another control character '
                     'can\'t be passed on: rename the file')
        twice = "--wrapper given twice: give one file"
        self.refused(["--wrapper", "F", "--wrapper=F", "s1"], twice)
        self.refused(["headless", "hi", "--wrapper=F", "--", "--wrapper", "G"], twice)
        self.refused(["--wrapper", "F", "s1", "--", "grok", "--wrapper", "G"],
                     "--wrapper given twice, to nexus and to the grok after --: give it to one of them")
        self.refused([SKIP, "--wrapper", "F", "--", "/usr/bin/agent", "--wrapper=G"],
                     "--wrapper given twice, to nexus and to the agent after --: give it to one of them")
        # With a session name too: the mode word isn't read as it, so the -- command is still found.
        self.refused([SKIP, "--wrapper", "F", "s1", "--", "grok", "--wrapper", "G"],
                     "--wrapper given twice, to nexus and to the grok after --: give it to one of them")

    def test_the_dashdash_form(self):
        # The FILE of a `grok|agent … --wrapper F` command, before the shim's own bare --; "" with none (the shim
        # refuses it, so it is still checked); None for any other command.
        from agentctl.nexus.persona_cmds import dashdash_wrapper
        self.assertEqual(dashdash_wrapper(["grok", "--single", "hi", "--wrapper", "F"]), "F")
        self.assertEqual(dashdash_wrapper(["/usr/local/bin/agent", "--wrapper=F", "--", "--wrapper", "G"]), "F")
        self.assertEqual(dashdash_wrapper(["grok", "--wrapper"]), "")
        self.assertIsNone(dashdash_wrapper(["grok", "--", "--wrapper", "x"]))
        self.assertIsNone(dashdash_wrapper(["echo", "--wrapper", "F"]))
        self.assertIsNone(dashdash_wrapper([]))

    def test_take_mode_still_finds_headless(self):
        # take_mode runs on what take_wrapper left, but the first word is found past --wrapper F all the same.
        from agentctl.nexus.persona_cmds import first_word, take_mode
        self.assertEqual(first_word(["--wrapper", "F", SKIP, "headless", "hi"]), "headless")
        self.assertEqual(first_word(["--wrapper=F", "s1"]), "s1")
        self.assertEqual(first_word(["--wrapper", "headless"]), "")
        self.assertEqual(take_mode(["--wrapper", "F", "headless", "--", "hi", SKIP]),
                         ("ask", SKIP, ["--wrapper", "F", "headless", "--", "hi"]))
        self.assertEqual(take_mode(["--wrapper=F", "headless", "--", "hi", SKIP]),
                         ("ask", SKIP, ["--wrapper=F", "headless", "--", "hi"]))
        # --wrapper headless: headless is its FILE, so the -- ends the words.
        self.assertEqual(take_mode(["--wrapper", "headless", "--", "hi", SKIP]),
                         ("", "", ["--wrapper", "headless", "--", "hi", SKIP]))


class WrapperProbe(unittest.TestCase):
    """wrapper_probe.sh's -- form, run here by sh as docker exec runs it (stderr dropped): grok on PATH a shim (its
    marker line) that refuses a --wrapper file that isn't there."""
    SHIM = ("#!/bin/sh\n# NEXUS-GROK-SHIM v1\nfor a; do case $a in --wrapper=*) f=${a#--wrapper=} ;; esac; done\n"
            "echo on-stdout\n[ -f \"$f\" ] || { echo \"grok: --wrapper $f: no such file here\" >&2; exit 2; }\n"
            "exec \"$NEXUS_GROK_REAL\"\n")

    def setUp(self):
        self.d = os.path.realpath(tempfile.mkdtemp())
        for sub in ("bin", "cwd", "dir"):
            os.makedirs(os.path.join(self.d, sub))
        self.exe("bin/grok", self.SHIM)
        with open(self.d + "/dir/w.md", "w") as f:
            f.write("# Wrapper\n")

    def tearDown(self):
        shutil.rmtree(self.d)

    def exe(self, rel, text):
        with open(os.path.join(self.d, rel), "w") as f:
            f.write(text)
        os.chmod(os.path.join(self.d, rel), 0o755)

    def probe(self, d, *cmd, wrapper=""):
        """The probe for a command, CMD, in D: the -- form's (WRAPPER "": the container's NEXUS_WRAPPER, unset here),
        or the word's (WRAPPER: the NEXUS_WRAPPER to run it with)."""
        env = dict(os.environ, PATH=self.d + "/bin:" + os.environ.get("PATH", "/usr/bin:/bin"))
        env.pop("NEXUS_WRAPPER", None)
        r = subprocess.run(["sh", "-c", docker.probe("wrapper_probe.sh"), "sh", "", d, wrapper] + list(cmd),
                           cwd=self.d + "/cwd",
                           env=env, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        return r.returncode, r.stdout.decode().splitlines()

    def test_found_from_the_dir(self):
        # ./grok2 is the session dir's: a plain script there isn't the shim (and isn't run), whatever is in the cwd.
        self.exe("cwd/grok2", self.SHIM)
        self.exe("dir/grok2", "#!/bin/sh\ntouch %s/ran\n" % self.d)
        self.assertEqual(self.probe(self.d + "/dir", "./grok2", "--wrapper=w.md"), (0, ["shim=yes", "default=", "check=other"]))
        self.assertFalse(os.path.exists(self.d + "/ran"))
        # The shim there, none in the cwd: checked, F read from the dir; one that fails says the shim's line, not stdout.
        self.exe("dir/grok3", self.SHIM)
        self.assertEqual(self.probe(self.d + "/dir", "./grok3", "--wrapper=w.md"), (0, ["shim=yes", "default=", "check=ok"]))
        self.assertEqual(self.probe(self.d + "/dir", "grok", "--wrapper=none.md"),
                         (0, ["shim=yes", "default=", "check=2", "grok: --wrapper none.md: no such file here"]))

    def test_a_dir_that_isnt_there(self):
        # cd's own line, after a check that fails: nexus says it.
        rc, lines = self.probe(self.d + "/nope", "grok", "--wrapper=w.md")
        self.assertEqual((rc, lines[:2]), (0, ["shim=yes", "default="]))
        self.assertRegex(lines[2], r"^check=[1-9][0-9]*$")
        self.assertEqual(len(lines), 4, lines)
        self.assertIn(self.d + "/nope", lines[3])

    def test_the_words_wrapper(self):
        # The word's check: the command run under NEXUS_WRAPPER=<the file as the persona sees it>, with the real
        # grok-mode.sh as grok, so its own limits decide: a file at the limit alone passes, and fails with the
        # command's own --rules; a command that isn't the shim (grok itself) isn't run.
        shutil.copy(os.path.join(ROOT, "grok-mode.sh"), self.d + "/bin/grok")
        with open(self.d + "/dir/max.md", "wb") as f:
            f.write(b"x" * 122878)
        self.assertEqual(self.probe(self.d + "/dir", "grok", "--single", "hi", wrapper=self.d + "/dir/max.md"),
                         (0, ["shim=yes", "default=", "check=ok"]))
        rc, lines = self.probe(self.d + "/dir", "grok", "--rules", "be terse", wrapper=self.d + "/dir/max.md")
        self.assertEqual((rc, lines[:3]), (0, ["shim=yes", "default=", "check=2"]))
        self.assertEqual(len(lines), 4, lines)
        self.assertRegex(lines[3], r"^grok: --wrapper %s/dir/max\.md \(NEXUS_WRAPPER\): is too big: 122878 bytes, 8 of "
                         r"--rules" % re.escape(self.d))
        self.exe("bin/grok-real", "#!/bin/sh\ntouch %s/ran\n" % self.d)
        self.assertEqual(self.probe(self.d + "/dir", self.d + "/bin/grok-real", wrapper=self.d + "/dir/max.md"),
                         (0, ["shim=yes", "default=", "check=other"]))
        self.assertFalse(os.path.exists(self.d + "/ran"))


class DashdashNote(unittest.TestCase):
    """Run.dashdash_check's refusals, the probe's answers given."""

    def setUp(self):
        self.d = os.path.realpath(tempfile.mkdtemp())
        os.makedirs(self.d + "/root/app")
        os.makedirs(self.d + "/other")
        os.symlink(self.d + "/root", self.d + "/link")

    def tearDown(self):
        shutil.rmtree(self.d)

    def said(self, here, f, got=None, lines=("grok: no such file here",), cmd=None):
        from agentctl.core.errors import NexusError
        from agentctl.nexus.persona_cmds import Run
        r = Run.__new__(Run)
        r.p = type("P", (), {"name": "g1"})()
        r.ctx = type("Ctx", (), {"here": here})()
        r.wrapper_probe = lambda *a: (dict(got or {"shim": "yes", "check": "2"}), list(lines))
        with self.assertRaises(NexusError) as e:
            r.dashdash_check("s1", self.d + "/root/app", cmd or ["grok", "--wrapper", f])
        return e.exception.message

    def test_the_note(self):
        wd = self.d + "/root/app"
        plain = "nothing was started: grok --wrapper would stop at once:\n       grok: no such file here"
        note = "nothing was started: grok --wrapper would stop at once (%s is read from " + wd + ", where the session " \
            "starts, not from here):\n       grok: no such file here"
        self.assertEqual(self.said(wd, "x.md"), plain)
        self.assertEqual(self.said(self.d + "/link/app", "x.md"), plain)      # the same dir, through a link
        self.assertEqual(self.said(self.d + "/other", "/abs/x.md"), plain)
        self.assertEqual(self.said(self.d + "/other", "x.md"), note % "./x.md")
        self.assertEqual(self.said(self.d + "/other", "./x.md"), note % "./x.md")
        self.assertEqual(self.said(self.d + "/other", "../x.md"), note % "../x.md")
        # A shim that failed saying nothing: its exit code.
        self.assertEqual(self.said(wd, "x.md", {"shim": "yes", "check": "3"}, ()),
                         "nothing was started: grok --wrapper would stop at once:\n       exit 3")

    def test_the_hint_quoted(self):
        cmd = ["grok", "--wrapper", "sp ace/w.md", "--single=a b"]
        self.assertEqual(self.said(self.d + "/root/app", "", {"shim": "yes", "check": "ok", "session": ""}, cmd=cmd),
                         "session s1 is there already, and tmux would attach to it without running grok --wrapper sp "
                         "ace/w.md --single=a b: name a new session, nexus g1 NAME -- grok --wrapper 'sp ace/w.md' "
                         "'--single=a b'")


class WrapperHints(unittest.TestCase):
    def test_the_copy_gone_hint(self):
        # The commands that give a wrapper file's copy again or none: the checkout's path shell-quoted.
        from agentctl.nexus.persona_cmds import copy_gone
        self.assertEqual(copy_gone("/x/my agents", "personas/g1.wrapper.md", "g1"),
                         "/x/my agents/personas/g1.wrapper.md isn't there, and g1's container mounts it (its wrapper "
                         "file), so nexus won't start it: give it again, '/x/my agents/nexus' --config --wrapper g1:PATH, "
                         "or none, '/x/my agents/nexus' --config --wrapper g1:none")


class WrapperPath(unittest.TestCase):
    """Run.wrapper_path: where the persona sees --wrapper's FILE, else the copy staged in its scratch dir."""

    def setUp(self):
        self.d = os.path.realpath(tempfile.mkdtemp())
        self.root, self.scratch = self.d + "/root", self.d + "/scratch/g1"
        for sub in ("root/rules", "scratch/g1", "out", "old/rules"):
            os.makedirs(os.path.join(self.d, sub))
        self.text = b"# Wrapper\n\nDo the whole task.\n"
        for f in ("root/rules/a.md", "out/w.md", "scratch/g1/s.md", "old/rules/o.md"):
            with open(os.path.join(self.d, f), "wb") as fh:
                fh.write(self.text)
        self.copy = "%s/wrappers/%s-w.md" % (self.scratch, hashlib.sha256(self.text).hexdigest()[:12])

    def tearDown(self):
        shutil.rmtree(self.d)

    def run_for(self, root=None, mounts=None, here=None):
        from agentctl.nexus.persona_cmds import Run
        r = Run.__new__(Run)
        r.notes = []
        r.out = type("Out", (), {"note": lambda _, m: r.notes.append(m)})()
        r.p = type("P", (), {"name": "g1", "flavour": "grok", "root": root or self.root, "scratch": self.scratch})()
        r.ctx = type("Ctx", (), {"here": here or self.root})()
        r.mounts = mounts
        r.copy, r.fresh = None, ""
        return r

    def staged(self, r, given, old):
        """wrapper_path, then the copy written as wrapper_env writes it once the run may go on."""
        path = r.wrapper_path(given, old)
        r.out.note(r.stage_wrapper())
        return path

    def test_inside_the_root(self):
        r = self.run_for()
        self.assertEqual(r.wrapper_path("rules/a.md", ""), self.root + "/rules/a.md")
        self.assertEqual(r.wrapper_path(self.root + "/rules/../rules/a.md", ""), self.root + "/rules/a.md")
        self.assertEqual(r.notes, [])
        self.assertFalse(os.path.exists(self.scratch + "/wrappers"))

    def test_a_symlinked_root(self):
        # The root's own spelling, however the file was named: the link's, or the real path.
        os.symlink(self.root, self.d + "/link")
        r = self.run_for(root=self.d + "/link")
        self.assertEqual(r.wrapper_path(self.root + "/rules/a.md", ""), self.d + "/link/rules/a.md")
        self.assertEqual(r.wrapper_path(self.d + "/link/rules/a.md", ""), self.d + "/link/rules/a.md")
        self.assertEqual(r.notes, [])

    def test_the_scratch_dir(self):
        for mounts in (None, {"/home/dev", self.scratch}, set()):     # made now; mounted; mounts unread
            r = self.run_for(mounts=mounts)
            self.assertEqual(r.wrapper_path(self.scratch + "/s.md", ""), self.scratch + "/s.md", mounts)
        self.assertFalse(os.path.exists(self.scratch + "/wrappers"))

    def test_outside_staged(self):
        r = self.run_for()
        mask = os.umask(0o077)      # the modes set, not left to the umask
        try:
            # wrapper_path only works out where the copy goes: nothing is written (nor said) until stage_wrapper.
            self.assertEqual(r.wrapper_path(self.d + "/out/w.md", ""), self.copy)
            self.assertEqual((os.path.exists(self.scratch + "/wrappers"), r.notes), (False, []))
            r.out.note(r.stage_wrapper())
        finally:
            os.umask(mask)
        self.assertEqual(r.fresh, self.copy)      # made by this run
        with open(self.copy, "rb") as f:
            self.assertEqual(f.read(), self.text)
        self.assertEqual(oct(os.stat(self.copy).st_mode & 0o777), "0o644")
        self.assertEqual(oct(os.stat(self.scratch + "/wrappers").st_mode & 0o777), "0o755")
        self.assertEqual(r.notes, ["--wrapper %s/out/w.md is outside g1's projects root: a copy of it, %s, goes in its "
                                   "place" % (self.d, self.copy)])
        self.assertEqual(sorted(os.listdir(self.scratch + "/wrappers")), [os.path.basename(self.copy)])   # no temp file
        # A link in the root to a file outside it: by its real path, so copied too (named as the link is).
        os.symlink(self.d + "/out/w.md", self.root + "/rules/link.md")
        link = "%s/wrappers/%s-link.md" % (self.scratch, hashlib.sha256(self.text).hexdigest()[:12])
        self.assertEqual(self.staged(r, "rules/link.md", ""), link)
        with open(link, "rb") as f:
            self.assertEqual(f.read(), self.text)
        self.assertEqual(r.notes[1], "--wrapper %s/rules/link.md links to %s/out/w.md, outside g1's projects root: a copy "
                                     "of it, %s, goes in its place" % (self.root, self.d, link))
        # The same bytes again: the same copy, there already, so not one this run made (word_check leaves it).
        self.staged(r, self.d + "/out/w.md", "")
        self.assertEqual(r.fresh, "")

    def test_the_old_root(self):
        # A container made with another root: that root counts, not the persona's now (which it doesn't mount).
        r = self.run_for(mounts={"/home/dev", self.scratch})
        self.assertEqual(r.wrapper_path(self.d + "/old/rules/o.md", self.d + "/old"), self.d + "/old/rules/o.md")
        copy = "%s/wrappers/%s-a.md" % (self.scratch, hashlib.sha256(self.text).hexdigest()[:12])
        self.assertEqual(self.staged(r, self.root + "/rules/a.md", self.d + "/old"), copy)
        self.assertTrue(os.path.isfile(copy))
        self.assertEqual(r.notes, ["--wrapper %s/rules/a.md is in g1's projects root %s, but its container was made with "
                                   "the old root %s/old: a copy of it, %s, goes in its place" % (self.root, self.root, self.d, copy)])
        # Made without its scratch dir too: refused, said once.
        from agentctl.core.errors import NexusError
        r = self.run_for(mounts={"/home/dev"})
        with self.assertRaises(NexusError) as e:
            r.wrapper_path(self.root + "/rules/a.md", self.d + "/old")
        self.assertEqual(e.exception.message,
                         "--wrapper %s/rules/a.md is in g1's projects root %s, but its container was made with the old root "
                         "%s/old, and without its scratch dir %s, where nexus would copy it: `nexus g1 restart` adds that "
                         "(it ends its tmux sessions), or move the file into %s/old"
                         % (self.root, self.root, self.d, self.scratch, self.d))
        # A file outside both roots, or a link in either to one: their own phrase, then what the container was made
        # without, its subject kept.
        os.symlink(self.d + "/out/w.md", self.d + "/old/rules/link.md")
        os.symlink(self.d + "/out/w.md", self.root + "/rules/link.md")
        tail = (", and its container was made without its scratch dir %s, where nexus would copy it: `nexus g1 restart` "
                "adds that (it ends its tmux sessions), or move the file into %s/old" % (self.scratch, self.d))
        for f, why in ((self.d + "/out/w.md", "is outside g1's projects root %s/old" % self.d),
                       (self.d + "/old/rules/link.md", "links to %s/out/w.md, outside g1's projects root %s/old"
                        % (self.d, self.d)),
                       (self.root + "/rules/link.md", "links to %s/out/w.md, outside g1's projects root %s/old"
                        % (self.d, self.d))):
            with self.assertRaises(NexusError) as e:
                r.wrapper_path(f, self.d + "/old")
            self.assertEqual(e.exception.message, "--wrapper %s %s%s" % (f, why, tail))
        # Staged with its scratch dir: the link in the persona's root now is said to lead outside too.
        r = self.run_for(mounts={"/home/dev", self.scratch})
        link = "%s/wrappers/%s-link.md" % (self.scratch, hashlib.sha256(self.text).hexdigest()[:12])
        self.assertEqual(self.staged(r, self.root + "/rules/link.md", self.d + "/old"), link)
        self.assertEqual(r.notes, ["--wrapper %s/rules/link.md links to %s/out/w.md, outside g1's projects root: a copy "
                                   "of it, %s, goes in its place" % (self.root, self.d, link)])

    def test_no_scratch_mount(self):
        from agentctl.core.errors import NexusError
        r = self.run_for(mounts={"/home/dev", "/etc/grok/rules/00-nexus.md"})
        self.assertEqual(r.wrapper_path("rules/a.md", ""), self.root + "/rules/a.md")
        f = self.d + "/out/w.md"
        with self.assertRaises(NexusError) as e:
            r.wrapper_path(f, "")
        self.assertEqual(e.exception.message,
                         "--wrapper %s is outside g1's projects root %s, and its container was made without its "
                         "scratch dir %s, where nexus would copy it: `nexus g1 restart` adds that (it ends its tmux "
                         "sessions), or move the file into %s" % (f, self.root, self.scratch, self.root))
        # One in that scratch dir is said to be there, not outside the root.
        f = self.scratch + "/s.md"
        with self.assertRaises(NexusError) as e:
            r.wrapper_path(f, "")
        self.assertEqual(e.exception.message,
                         "--wrapper %s is in g1's scratch dir %s, and its container was made without it: `nexus g1 "
                         "restart` adds it (it ends its tmux sessions), or move the file into %s" % (f, self.scratch, self.root))
        # A link in the root to a file outside it: said to lead there.
        os.symlink(self.d + "/out/w.md", self.root + "/rules/link.md")
        with self.assertRaises(NexusError) as e:
            r.wrapper_path("rules/link.md", "")
        self.assertEqual(e.exception.message,
                         "--wrapper %s/rules/link.md links to %s/out/w.md, outside g1's projects root %s, and its "
                         "container was made without its scratch dir %s, where nexus would copy it: `nexus g1 restart` "
                         "adds that (it ends its tmux sessions), or move the file into %s"
                         % (self.root, self.d, self.root, self.scratch, self.root))
        self.assertFalse(os.path.exists(self.scratch + "/wrappers"))

    def test_held_to_the_shims_rules_first(self):
        from agentctl.core.errors import NexusError
        r = self.run_for()
        # In the root too, where the path would otherwise be passed on as it is.
        with open(self.root + "/rules/nul.md", "wb") as f:
            f.write(b"# Wrapper\0\n")
        with open(self.root + "/rules/blank.md", "wb") as f:
            f.write(b"\n\n")
        for f, why in (("none.md", "isn't there: check the path"), ("", "isn't a regular file: name a text file"),
                       ("nul.md", "holds a NUL byte, so it isn't text: it takes a UTF-8 text file"),
                       ("blank.md", "is empty (newlines aside): it takes the text to add to the system prompt")):
            path = self.root + "/rules" + ("/" + f if f else "")
            with self.assertRaises(NexusError) as e:
                r.wrapper_path(path, "")
            self.assertEqual(e.exception.message, "--wrapper %s %s" % (path, why))
        self.assertEqual(r.notes, [])
