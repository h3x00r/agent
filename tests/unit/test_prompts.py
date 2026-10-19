import io
import os
import pty
import termios
import unittest

from agentctl.core.errors import NexusError
from agentctl.ui.output import Output
from agentctl.ui.prompts import EOF_MESSAGE, Prompter


def piped(data, yes=False):
    r, w = os.pipe()
    os.write(w, data)
    os.close(w)
    out, err = io.StringIO(), io.StringIO()
    return Prompter(Output("config", out, err), yes=yes, fd=r, err=err), out, err, r


class Piped(unittest.TestCase):
    def setUp(self):
        self.fds = []

    def tearDown(self):
        for fd in self.fds:
            os.close(fd)

    def make(self, data, yes=False):
        p, out, err, fd = piped(data, yes)
        self.fds.append(fd)
        return p, out, err

    def test_answers_trimmed_and_defaults(self):
        p, out, err = self.make(b"  x y \t\n\nlast")
        self.assertEqual(p.ask("Name", "d"), "x y")
        self.assertEqual(p.ask("Name", "d"), "d")
        self.assertEqual(p.ask("Name", "d"), "last")      # no newline at the end: still an answer
        self.assertEqual(err.getvalue(), "")              # no terminal: no prompts

    def test_a_cr_stays(self):
        p, _, _ = self.make(b"claude\r\n")
        self.assertEqual(p.ask("Q"), "claude\r")

    def test_reads_no_further_than_its_line(self):
        p, _, _ = self.make(b"one\ntwo\n")
        p.ask("Q")
        self.assertEqual(os.read(self.fds[0], 10), b"two\n")

    def test_eof(self):
        p, _, err = self.make(b"")
        with self.assertRaises(NexusError) as c:
            p.ask("Q", "d")
        self.assertEqual((c.exception.message, err.getvalue()), (EOF_MESSAGE, "\n"))

    def test_yesno(self):
        p, _, _ = self.make(b"\nno\nYes\n")
        self.assertTrue(p.yesno("Q?", "y"))
        self.assertFalse(p.yesno("Q?", "y"))
        self.assertTrue(p.yesno("Q?", "n"))

    def test_choose(self):
        opts = ["claude   Claude Code", "grok     Grok Build"]
        p, out, _ = self.make(b"abc\n0\n3\n010\n2\n")
        self.assertEqual(p.choose("Flavour:", "claude", opts), "grok")
        self.assertEqual(out.getvalue().count("  pick a number or a name from the list"), 4)
        self.assertIn("  1) claude   Claude Code\n", out.getvalue())

    def test_secret_ends_the_line(self):
        p, _, err = self.make(b"s3\n")
        self.assertEqual(p.secret("TOKEN"), "s3")
        self.assertEqual(err.getvalue(), "\n")

    def test_yes_reads_nothing(self):
        p, _, _ = self.make(b"ignored\n", yes=True)
        self.assertEqual((p.ask("Q", "d"), p.yesno("Q", "n"), p.secret("S"), p.choose("C", "b", ["a x", "b y"])),
                         ("d", False, "", "b"))
        with self.assertRaises(NexusError):
            p.choose("C", "z", ["a x"])

    def test_defaults_covers_ask_only(self):
        p, _, _ = self.make(b"n\n")
        p.defaults = True
        self.assertEqual(p.ask("Q", "d"), "d")
        self.assertFalse(p.yesno("Q", "y"))


class Terminal(unittest.TestCase):
    def test_prompts_on_stderr_and_hidden_answers(self):
        master, slave = pty.openpty()
        try:
            err = io.StringIO()
            p = Prompter(Output("config", io.StringIO(), err), fd=slave, err=err)
            os.write(master, b"visible\nhidden\n")
            self.assertEqual(p.ask("Name", "d"), "visible")
            self.assertEqual(p.secret("TOKEN"), "hidden")
            self.assertEqual(err.getvalue(), "Name [d]: TOKEN (hidden; empty to skip): \n")
            self.assertTrue(termios.tcgetattr(slave)[3] & termios.ECHO)   # echo back on
        finally:
            os.close(master)
            os.close(slave)


if __name__ == "__main__":
    unittest.main()
