import os
import shutil
import subprocess
import unittest

from agentctl.core import shellcompat as sc


class Lexical(unittest.TestCase):
    def test_cleans_like_realpath_ms(self):
        self.assertEqual(sc.lexical("//a//b/./c/../"), "/a/b")
        self.assertEqual(sc.lexical("/"), "/")
        self.assertEqual(sc.lexical("//"), "/")
        self.assertEqual(sc.lexical("/x/../.."), "/")

    def test_relative_against_the_cwd(self):
        self.assertEqual(sc.lexical("rel/x", cwd="/tmp"), "/tmp/rel/x")

    def test_empty_stays_empty(self):
        self.assertEqual(sc.lexical(""), "")


class Paths(unittest.TestCase):
    def test_under_is_a_string_test(self):
        self.assertTrue(sc.under("/p", "/p"))
        self.assertTrue(sc.under("/p/app", "/p"))
        self.assertFalse(sc.under("/pq", "/p"))

    def test_basename_ignores_trailing_slashes(self):
        for path, want in (("/a/b/", "b"), ("/", "/"), ("//", "/"), ("a", "a"), ("", "")):
            self.assertEqual(sc.bash_basename(path), want, path)

    def test_realpath_e_needs_every_component(self):
        self.assertEqual(sc.realpath_e("/"), "/")
        self.assertIsNone(sc.realpath_e("/no/such/dir/here"))
        self.assertIsNone(sc.realpath_e(""))


class Read(unittest.TestCase):
    # Each expectation was measured with bash 5.0's read.
    def test_tab_ifs_collapses_and_the_last_takes_the_rest(self):
        self.assertEqual(sc.read_fields("\tx\t\ty\tz\t\tw\t", "\t", 3), ["x", "y", "z\t\tw"])

    def test_other_ifs_keeps_empty_fields(self):
        self.assertEqual(sc.read_fields("n\037\0372\037p\037", "\037", 5), ["n", "", "2", "p", ""])

    def test_one_trailing_delimiter_after_the_last_field_goes(self):
        self.assertEqual(sc.read_fields("x:y:", ":", 2), ["x", "y"])
        self.assertEqual(sc.read_fields("x:y:z:", ":", 2), ["x", "y:z:"])
        self.assertEqual(sc.read_fields("x:y::", ":", 2), ["x", "y::"])
        self.assertEqual(sc.read_fields("a:claude:docker:/x:", ":", 4), ["a", "claude", "docker", "/x"])
        self.assertEqual(sc.read_fields("a:claude:docker:/x::", ":", 4), ["a", "claude", "docker", "/x::"])

    def test_missing_fields_are_empty(self):
        self.assertEqual(sc.read_fields("a:b", ":", 4), ["a", "b", "", ""])

    def test_only_the_first_line(self):
        self.assertEqual(sc.read_fields("a\tb\nc", "\t", 3), ["a", "b", ""])

    def test_array(self):
        self.assertEqual(sc.read_array("\ta\t\tb\t", "\t"), ["a", "b"])
        self.assertEqual(sc.read_array("  x  flavour=y\tz ", " \t\n"), ["x", "flavour=y", "z"])


class PrintfQ(unittest.TestCase):
    CASES = ["", "abc", "a b", "a'b", 'a"b', "$x", "a~b", "~a", "a=b", "a,b", "a@b:c%d+e", "tab\tx", "nl\nx",
             "é", "a*b", "a#b", "#a", "a!b", "-x", "a{b}", "a^b", "a\x01b", "a\x7fb", "a\x1bb", "\\x",
             "/home/u/projects/agents/personas/c1.env", "a\udcffb", "\xc2\x9d".encode("latin-1").decode("utf-8"),
             "x\u2028y", "x\u0378y", "é\tx"]

    def test_measured(self):
        want = {"": "''", "a b": "a\\ b", "a'b": "a\\'b", "$x": "\\$x", "~a": "\\~a", "a,b": "a\\,b",
                "tab\tx": "$'tab\\tx'", "a\x01b": "$'a\\001b'", "a\x1bb": "$'a\\Eb'", "#a": "\\#a",
                "a=b": "a=b", "é": "é"}
        for s, q in want.items():
            self.assertEqual(sc.printf_q(s, utf8=True), q, repr(s))
        self.assertEqual(sc.printf_q("x\u2028y", utf8=True), "$'x\\342\\200\\250y'")   # not printable
        self.assertEqual(sc.printf_q("é", utf8=False), "$'\\303\\251'")                # outside UTF-8: bytes

    @unittest.skipUnless(shutil.which("bash"), "no bash here")
    def test_as_bash_writes_it(self):
        for locale, utf8 in (("C.UTF-8", True), ("C", False)):
            for s in self.CASES:
                arg = s.encode("utf-8", "surrogateescape")
                got = subprocess.run(["bash", "-c", 'printf %q "$1"', "bash", arg], stdout=subprocess.PIPE,
                                     env=dict(os.environ, LC_ALL=locale)).stdout.decode("utf-8", "surrogateescape")
                self.assertEqual(sc.printf_q(s, utf8=utf8), got, "%s %r" % (locale, s))


class Ere(unittest.TestCase):
    def test_dollar_is_the_end(self):
        self.assertTrue(sc.ere(r"[0-9]+", "12"))
        self.assertFalse(sc.ere(r"[0-9]+", "12\n"))

    def test_dot_matches_a_newline(self):
        self.assertTrue(sc.ere(r"-[^ \t=]*(=.*)?", "--note=a\nb"))

    def test_a_byte_that_isnt_utf8_matches_nothing_in_utf8(self):
        saved = sc.UTF8
        try:
            sc.UTF8 = True
            self.assertFalse(sc.ere(r"-.*", "-\udcff"))
            sc.UTF8 = False
            self.assertTrue(sc.ere(r"-.*", "-\udcff"))
        finally:
            sc.UTF8 = saved


class Dirs(unittest.TestCase):
    def test_lexical_drops_trailing_newlines_as_command_substitution(self):
        self.assertEqual(sc.lexical("/a/b\n\n"), "/a/b")

    def test_logical_keeps_a_leading_double_slash(self):
        self.assertEqual(sc.logical("//a/b/../c/", "/x"), "//a/c")
        self.assertEqual(sc.logical("///a", "/x"), "/a")
        self.assertEqual(sc.logical("../y", "/x/link"), "/x/y")

    def test_mkdir_p_parents_keep_u_wx_under_a_tight_umask(self):
        import stat
        import tempfile
        d = tempfile.mkdtemp()
        old = os.umask(0o177)
        try:
            sc.mkdir_p(os.path.join(d, "a/b/c"))
            sc.mkdir_p(os.path.join(d, "a/b/c"))            # already there: fine
        finally:
            os.umask(old)
        modes = [stat.S_IMODE(os.stat(os.path.join(d, p)).st_mode) for p in ("a", "a/b", "a/b/c")]
        self.assertEqual(modes, [0o700, 0o700, 0o600])
        open(os.path.join(d, "f"), "w").close()
        with self.assertRaises(OSError):
            sc.mkdir_p(os.path.join(d, "f"))


class Sort(unittest.TestCase):
    def test_c_order_is_by_bytes(self):
        env = dict(os.environ, LC_ALL="C")
        out = subprocess.run([__import__("sys").executable, "-I", "-c",
                              "import sys; sys.path.insert(0, sys.argv[1]); from agentctl.core import shellcompat as s; "
                              "print(' '.join(s.sort_lines(['b', 'B', 'a-c', 'ab'])))", ROOT],
                             stdout=subprocess.PIPE, env=env).stdout.decode().split()
        self.assertEqual(out, ["B", "a-c", "ab", "b"])

    def test_a_locale_that_isnt_there_is_c(self):
        env = dict(os.environ, LC_ALL="xx_YY.UTF-8")
        out = subprocess.run([__import__("sys").executable, "-I", "-c",
                              "import sys; sys.path.insert(0, sys.argv[1]); from agentctl.core import shellcompat as s; "
                              "print(' '.join(s.sort_lines(['b', 'B'])))", ROOT],
                             stdout=subprocess.PIPE, env=env).stdout.decode().split()
        self.assertEqual(out, ["B", "b"])


ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


if __name__ == "__main__":
    unittest.main()
