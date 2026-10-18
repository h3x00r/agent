import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

from agentctl.adapters import compose_render
from agentctl.core import lock, proc
from agentctl.domain.persona import Persona

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
GOLDEN = os.path.join(REPO, "tests", "golden", "cases", "config", "cli-yes-first-setup", "tree.txt")


def golden_file(path, name):
    """A file's content as a golden case's tree.txt shows it."""
    out, on = [], False
    with open(path) as f:
        for line in f:
            if line.startswith(("+ ", "~ ", "- ")):
                on = line.split()[1] == name
                continue
            if on and line.startswith("    | "):
                out.append(line[6:])
    return "".join(out)


class Render(unittest.TestCase):
    @unittest.skipUnless(os.path.exists(GOLDEN), "the golden case isn't recorded")
    def test_as_the_bash_wrote_it(self):
        ps = [Persona("c1", "claude", "docker", "colour25", True), Persona("g1", "grok", "docker", "colour28", True,
                                                                           ssh="personas/g1.key")]
        self.assertEqual(compose_render.render(ps, managed=True), golden_file(GOLDEN, "a/compose.personas.yml"))

    def test_shapes(self):
        ps = [Persona("n1", "grok", "ddev", "colour90", False, ssh="none"),
              Persona("k1", "claude", "docker", "colour25", False, ssh="/keys/k")]
        out = compose_render.render(ps, managed=False, env_files={"n1": "personas/n1.env.AbC123"})
        self.assertIn("service: _agent-ddev }", out)
        self.assertIn("env_file: personas/n1.env.AbC123\n", out)
        self.assertNotIn("/etc/grok/statusline", out)
        self.assertIn("source: ./personas/n1.grok.toml,", out)
        self.assertIn("50-nexus-none.conf", out)
        self.assertIn("        source: '/keys/k'\n", out)
        self.assertNotIn("15-personas.json", out)
        self.assertIn('environment: { AGENT_PERSONA: "k1", AGENT_COLOUR: colour25, SSH_AUTH_SOCK: "" }', out)
        self.assertTrue(out.endswith('volumes:\n  "n1-home":\n  "k1-home":\n'))


class Lock(unittest.TestCase):
    def test_one_holder_at_a_time(self):
        with tempfile.TemporaryDirectory() as d:
            open(os.path.join(d, "config-tools"), "w").close()
            holder = subprocess.Popen([sys.executable, "-c", "import fcntl, os, sys, time\n"
                                       "fd = os.open(sys.argv[1], os.O_RDONLY); fcntl.flock(fd, fcntl.LOCK_EX)\n"
                                       "print('held', flush=True); time.sleep(30)", os.path.join(d, "config-tools")],
                                      stdout=subprocess.PIPE)
            try:
                holder.stdout.readline()
                self.assertFalse(lock.take(d))
            finally:
                holder.kill()
                holder.wait()
                holder.stdout.close()
            self.assertTrue(lock.take(d))

    @unittest.skipUnless(shutil.which("flock"), "no flock(1) here")
    def test_excludes_flock1(self):
        with tempfile.TemporaryDirectory() as d:
            f = os.path.join(d, "config-tools")
            open(f, "w").close()
            holder = subprocess.Popen(["flock", "-n", f, "sleep", "30"])
            try:
                for _ in range(50):
                    if subprocess.call(["flock", "-n", f, "true"]) != 0:
                        break
                    time.sleep(0.05)
                self.assertFalse(lock.take(d))
            finally:
                holder.kill()
                holder.wait()


class Proc(unittest.TestCase):
    def test_signal_exit_codes_are_the_shells(self):
        self.assertEqual(proc.run(["sh", "-c", "kill -TERM $$"]).rc, 143)
        self.assertEqual(proc.exit_code(-2), 130)

    def test_timeout_keeps_what_came(self):
        r = proc.run(["sh", "-c", "echo early; sleep 5"], timeout=0.5)
        self.assertEqual((r.rc, r.out), (124, "early\n"))

    def test_undecodable_bytes_survive(self):
        r = proc.run(["printf", "a\\377b"])
        self.assertEqual(r.out.encode("utf-8", "surrogateescape"), b"a\xffb")

    def test_missing_command(self):
        self.assertEqual(proc.run(["no-such-command-here"]).rc, 127)


if __name__ == "__main__":
    unittest.main()
