import ast
import importlib.util
import io
import os
import re
import subprocess
import sys
import tempfile
import tokenize
import unittest

from agentctl.adapters import personas_store
from agentctl.core import shellcompat

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
BOOT = os.path.join(ROOT, "agentctl", "_boot.py")


def boot_module():
    spec = importlib.util.spec_from_file_location("agentctl_boot_under_test", BOOT)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


class Boot(unittest.TestCase):
    def test_it_parses_on_any_python3(self):
        # It is compiled before its version check runs: no f-string, no :=, nothing newer than 3.4 knows.
        with open(BOOT) as f:
            src = f.read()
        ast.parse(src, BOOT, feature_version=(3, 4))
        prefixes = [re.match(r"[A-Za-z]*", t.string).group(0).lower()
                    for t in tokenize.generate_tokens(io.StringIO(src).readline) if t.type == tokenize.STRING]
        self.assertFalse([p for p in prefixes if "f" in p])

    def test_lc_ctype_goes_back_to_what_the_shim_saw(self):
        m, saved, env = boot_module(), shellcompat.UTF8, dict(os.environ)
        try:
            os.environ.update(AGENTCTL_LOCALE="0", LC_CTYPE="C.UTF-8")      # Python's PEP 538 coercion
            m._locale()
            self.assertEqual((shellcompat.UTF8, "LC_CTYPE" in os.environ, "AGENTCTL_LOCALE" in os.environ),
                             (False, False, False))
            os.environ.update(AGENTCTL_LOCALE="1:en_US.UTF-8", LC_CTYPE="C.UTF-8")
            m._locale()
            self.assertEqual((shellcompat.UTF8, os.environ.get("LC_CTYPE")), (True, "en_US.UTF-8"))
        finally:
            shellcompat.UTF8 = saved
            os.environ.clear()
            os.environ.update(env)

    def shim(self, redirect):
        """nexus ls through the shim, from an empty checkout, with REDIRECT applied to it."""
        d = tempfile.mkdtemp()
        script = 'exec "$0" ls %s' % redirect
        return subprocess.run(["bash", "-c", script, os.path.join(ROOT, "nexus")], cwd=d,
                              env=dict(os.environ, AGENTS_DIR=d, PWD=d), stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE)

    def test_a_closed_or_read_only_stderr_is_no_crash(self):
        # bash running a script with fd 2 closed leaves the script itself there, read-only.
        for redirect in ("2>&-", "2</dev/null"):
            r = self.shim(redirect)
            self.assertEqual(r.returncode, 1, redirect)      # the error it stops with, not Python's 120

    def test_a_closed_stdout_stops_the_run_saying_so(self):
        r = self.shim(">&-")
        self.assertEqual((r.returncode, r.stderr.decode()),
                         (1, "nexus: its output is closed (>&-): nothing it says would show\n"))


class Proc(unittest.TestCase):
    def test_execvp_gives_the_new_program_sigpipe_at_its_default(self):
        code = ("import signal, sys; sys.path.insert(0, sys.argv[1]); signal.signal(signal.SIGPIPE, signal.SIG_IGN); "
                "from agentctl.core import proc; proc.execvp(['grep', '^SigIgn:', '/proc/self/status'])")
        out = subprocess.run([sys.executable, "-I", "-c", code, ROOT], stdout=subprocess.PIPE).stdout.decode()
        self.assertEqual(int(out.split()[1], 16) & (1 << 12), 0)    # bit 13: SIGPIPE


class PersonasLines(unittest.TestCase):
    def test_a_last_line_without_a_newline(self):
        d = tempfile.mkdtemp()
        p = os.path.join(d, "compose.personas.yml")
        with open(p, "w") as f:
            f.write("# persona: c1 flavour=claude\r\n# persona: g1 flavour=grok")
        self.assertEqual(personas_store.lines(p), ["c1 flavour=claude"])                 # as `while read`


class AsciiClasses(unittest.TestCase):
    def test_no_unicode_classes_in_patterns(self):
        # \d and \s match more than bash's [0-9] and [[:space:]], and str.isdigit() more than [0-9].
        bad = []
        for top, _, files in os.walk(os.path.join(ROOT, "agentctl")):
            for n in files:
                if not n.endswith(".py"):
                    continue
                path = os.path.join(top, n)
                with open(path) as f:
                    for t in tokenize.generate_tokens(f.readline):
                        raw = t.type == tokenize.STRING and "r" in re.match(r"[A-Za-z]*", t.string).group(0).lower()
                        if raw and re.search(r"\\[dsDSw]", t.string) or t.type == tokenize.NAME and t.string == "isdigit":
                            bad.append("%s:%d %s" % (os.path.relpath(path, ROOT), t.start[0], t.string[:40]))
        self.assertEqual(bad, [])


if __name__ == "__main__":
    unittest.main()
