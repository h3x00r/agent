"""The tools' own lines: what they say on stdout, and their notes, warnings and errors on stderr.

Every line is flushed as it is written, so it lands before anything a child prints next (Python 3.8
buffers stderr when it isn't a terminal). Notes and warnings carry the tool's prefix: `nexus: …`,
`nexus: warning: …` (config-tools uses `config`). A write to a closed pipe is dropped quietly.
"""
import sys

from agentctl.core.errors import NexusError


class Output:
    def __init__(self, prefix, out=None, err=None):
        self.prefix = prefix
        self.out = out or sys.stdout
        self.err = err or sys.stderr

    def _write(self, stream, line):
        try:
            stream.write(line + "\n")
            stream.flush()
        except BrokenPipeError:
            pass

    def say(self, line=""):
        self._write(self.out, line)

    def lines(self, lines):
        for line in lines:
            self.say(line)

    def note(self, msg):
        self._write(self.err, "%s: %s" % (self.prefix, msg))

    def warn(self, msg):
        self._write(self.err, "%s: warning: %s" % (self.prefix, msg))

    def error_line(self, msg):
        self._write(self.err, msg)

    def usage(self, text):
        """The usage on stdout. Into a pipe whose reader has gone, exit 141, as the bash tools did (their
        `usage; exit 0` died of SIGPIPE under set -e) rather than being killed by the signal."""
        import signal
        from agentctl.core.errors import Exit
        old = signal.signal(signal.SIGPIPE, signal.SIG_IGN)
        try:
            self.out.write(text if text.endswith("\n") else text + "\n")
            self.out.flush()
        except BrokenPipeError:
            import os
            os.dup2(os.open(os.devnull, os.O_WRONLY), self.out.fileno())   # no second try at exit
            raise Exit(141)
        finally:
            signal.signal(signal.SIGPIPE, old)

    @staticmethod
    def die(msg, code=1):
        raise NexusError(msg, code)
