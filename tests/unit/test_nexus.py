import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest

from agentctl.adapters import docker
from agentctl.nexus import top

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
