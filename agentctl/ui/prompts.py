"""Questions, answered on a terminal or by piped lines, as the bash setup tool asked them.

What bash's `read -r -p` did, and these do the same:
- the prompt goes to stderr, and only when stdin is a terminal: piped answers show no prompts (a
  choose() menu is printed to stdout either way);
- the answer is read byte by byte up to a newline, never more, so a child started later gets the rest of
  the input; a last line without a newline still counts;
- spaces, tabs and newlines around it go (not a \\r), backslashes are literal;
- an empty answer is the default;
- at the end of input with nothing read, a newline goes to stderr and the run stops: "input ended;
  answer on a terminal or use --yes";
- secret() reads with the terminal's echo off, and always ends the prompt line with a newline on stderr.

With yes=True (--yes) every question takes its default without reading.

Pitfalls (each pinned where named):
- prompts on a terminal only (test_prompts.Terminal.test_prompts_on_stderr_and_hidden_answers,
  test_prompts.Piped.test_answers_trimmed_and_defaults)
- never past its line (test_prompts.Piped.test_reads_no_further_than_its_line)
- a last line without a newline (test_prompts.Piped.test_answers_trimmed_and_defaults, golden
  config/ask-last-line-no-newline)
- a \\r stays (test_prompts.Piped.test_a_cr_stays)
- the end of input (test_prompts.Piped.test_eof, golden config/ask-eof-piped)
- secret()'s newline, on a pipe too (test_prompts.Piped.test_secret_ends_the_line)
- the echo back on after a hidden answer (test_prompts.Terminal.test_prompts_on_stderr_and_hidden_answers)
- yes (test_prompts.Piped.test_yes_reads_nothing)
- choose(): a number only in range, else a key (test_prompts.Piped.test_choose, golden
  config/ask-invalid-choices)
"""
import os
import re
import sys
import termios

from agentctl.core.errors import NexusError

EOF_MESSAGE = "input ended; answer on a terminal or use --yes"
_TRIM = " \t\n"


class Prompter:
    def __init__(self, out, yes=False, fd=0, err=None):
        self.out = out          # an ui.output.Output, for menus and notes on stdout
        self.yes = yes
        self.fd = fd
        self.err = err or sys.stderr

    # --- reading ---
    def _tty(self):
        try:
            return os.isatty(self.fd)
        except OSError:
            return False

    def _prompt(self, text):
        if self._tty():
            try:
                self.err.write(text)
                self.err.flush()
            except BrokenPipeError:
                pass

    def _read_line(self):
        """One line from stdin, unbuffered: (text, ended) where ended means the input ran out first."""
        buf = bytearray()
        while True:
            try:
                b = os.read(self.fd, 1)
            except InterruptedError:
                continue
            if not b:
                return buf.decode("utf-8", "surrogateescape"), True
            if b == b"\n":
                return buf.decode("utf-8", "surrogateescape"), False
            buf.extend(b)

    def _eof(self, answer):
        if answer:
            return
        try:
            self.err.write("\n")
            self.err.flush()
        except BrokenPipeError:
            pass
        raise NexusError(EOF_MESSAGE)

    def _read(self, prompt, hidden=False):
        self._prompt(prompt)
        old = None
        if hidden and self._tty():
            old = termios.tcgetattr(self.fd)
            new = termios.tcgetattr(self.fd)
            new[3] &= ~termios.ECHO
            termios.tcsetattr(self.fd, termios.TCSADRAIN, new)
        try:
            text, ended = self._read_line()
        finally:
            if old is not None:
                termios.tcsetattr(self.fd, termios.TCSADRAIN, old)
        answer = text.strip(_TRIM)
        if ended:
            self._eof(answer)
        return answer

    # --- the four kinds of question ---
    def ask(self, question, default=""):
        if self.yes:
            return default
        a = self._read("%s%s: " % (question, " [%s]" % default if default else ""))
        return a or default

    def yesno(self, question, default):
        if self.yes:
            a = default
        else:
            a = self._read("%s [%s]: " % (question, "Y/n" if default == "y" else "y/N")) or default
        return a[:1] in ("y", "Y")

    def secret(self, prompt):
        if self.yes:
            return ""
        a = self._read("%s (hidden; empty to skip): " % prompt, hidden=True)
        try:
            self.err.write("\n")   # the echo was off: the line the answer didn't end
            self.err.flush()
        except BrokenPipeError:
            pass
        return a

    def choose(self, question, default, options):
        """One of OPTIONS ("key   description" strings), by its number or its key; returns the key."""
        keys = [o.split(" ", 1)[0] for o in options]
        while True:
            if self.yes:
                a = default
            else:
                self.out.say(question)
                for i, o in enumerate(options, 1):
                    self.out.say("  %d) %s" % (i, o))
                a = self._read("choice [%s]: " % default) or default
            if re.fullmatch(r"[0-9]{1,9}", a) and 1 <= int(a, 10) <= len(options):
                a = keys[int(a, 10) - 1]
            if a in keys:
                return a
            if self.yes:
                raise NexusError("'%s' is not one of the choices for: %s" % (a, question))
            self.out.say("  pick a number or a name from the list")
