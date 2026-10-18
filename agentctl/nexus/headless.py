"""nexus <persona> headless [PROMPT] [FLAGS…]: one prompt through the persona's CLI in its one-shot mode,
in this directory, without tmux. Only the CLI writes to stdout; its exit code is nexus's.

- PROMPT is the first argument unless that is a flag (-x, --opt or --opt=value), so a task file may
  start with "---". Input piped or redirected in goes with it, after the prompt as `claude -p` takes it;
  without a PROMPT it is the prompt. Next to a PROMPT, input from a pipe or socket counts only if it
  starts within 3 s, as claude -p decides for itself, so a caller that leaves stdin open doesn't hold
  the run.
- Piped input is read to its end first, into a temp file the CLI then reads at once; only then does
  docker exec get -i (given a terminal, the CLI would wait for it). Never -t. The temp file (in $TMPDIR,
  else /tmp, as mktemp) is removed as soon as it is made and used through its fd, so no ending, however
  abrupt, leaves the input behind.
- docker exec doesn't pass signals on, and its client runs in a session of its own, so Ctrl-C or a
  timeout's TERM reach nexus only. nexus sends them on, inside the persona, to the CLI's process group
  (the process docker exec started, marked with NEXUS_RUN), as a terminal's Ctrl-C would: daemons the run
  detached, in groups of their own, are left alone. It then waits for the CLI to end, and exits 130
  (INT) or 143 (TERM, HUP), whatever the CLI returned. A signal that comes again while its kill runs is
  dropped (a different one is sent on after it); docker exec itself ignores INT and QUIT, as a bash `&`
  job did.

Pitfalls (each pinned where named):
- never -t, on a terminal too (golden nexus/edge-headless-int-tty)
- the NEXUS_RUN id is digits and dashes only: the kill script greps for it as a regex (golden
  nexus/edge-headless-sigs)
- the temp file (golden nexus/edge-headless-tmpdir, run.sh "headless runs left no temp files")
- a signal while the input is read ends the run, the temp file already gone (golden
  nexus/edge-headless-sig-spool)
- the 3 s wait skips NUL bytes, as read does (golden nexus/edge-headless-nul, run.sh "an idle pipe next to a
  prompt: 3 s, then without it")
- a non-blocking stdin is read as a blocking one (golden nexus/edge-headless-nonblock)
- in a UTF-8 locale an argument with a byte that isn't UTF-8 is no flag (golden nexus/edge-headless-badutf8)
- 130 or 143 whatever the CLI returned (golden nexus/edge-headless-sigs)
- the same signal again (golden nexus/edge-headless-second-sig)
- SIGPIPE ignored while the kill runs: stderr's reader may have died of the same Ctrl-C (golden
  nexus/edge-headless-dead-stderr)
- docker exec's INT and QUIT (golden nexus/edge-headless-sigign)
- daemons left alone (run.sh "a daemon it detached keeps running")
"""
import os
import random
import select
import signal
import stat
import subprocess
import tempfile

from agentctl.adapters import docker
from agentctl.core import proc, sigs
from agentctl.core.errors import NexusError
from agentctl.core.shellcompat import ere
from agentctl.domain import flavour as flavours

FLAG = r"-[^ \t\n\r\f\v=]*(=.*)?"


def die(msg):
    raise NexusError(msg)


def _piped():
    try:
        m = os.fstat(0).st_mode
    except OSError:
        return False
    return stat.S_ISFIFO(m) or stat.S_ISREG(m) or stat.S_ISSOCK(m)


def _first_char(timeout):
    """bash's `read -r -t T -N 1`: (rc, bytes): 0 and one character, 1 at the end, 142 on time-out. NUL
    bytes are skipped, as read skips them."""
    b = b"\0"
    while b == b"\0":
        r, _, _ = select.select([0], [], [], timeout)
        if not r:
            return 142, b""
        b = os.read(0, 1)
        if not b:
            return 1, b""
    n = 0
    if b[0] >= 0xC0:
        n = 1 if b[0] < 0xE0 else 2 if b[0] < 0xF0 else 3
    while n:
        more = os.read(0, 1)
        if not more:
            break
        b += more
        n -= 1
    return 0, b.replace(b"\0", b"")   # bash drops NUL bytes


def _write_all(fd, data):
    while data:
        data = data[os.write(fd, data):]


def _as_a_job():
    """In the child: INT and QUIT ignored, as bash's `cmd &` had them in a script."""
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    signal.signal(signal.SIGQUIT, signal.SIG_IGN)


class Headless:
    def __init__(self, run, dir, args):
        self.r, self.p, self.out = run, run.p, run.out
        self.dir = dir
        self.args = list(args)
        self.sig = ""
        self.busy = False
        self.pending = []

    def notice_and_kill(self, name):
        first = not self.sig
        self.sig = name
        prev = signal.signal(signal.SIGPIPE, signal.SIG_IGN)   # a stderr reader that died of the same Ctrl-C
        try:
            proc.run(["docker", "exec", self.p.container, "sh", "-c", docker.probe("headless_kill.sh"), "sh",
                      self.run_id, name], new_session=True)
            if first:
                try:
                    os.write(2, ("nexus: %s: stopping the run in %s\n" % (name, self.p.name)).encode())
                except OSError:
                    pass
        finally:
            signal.signal(signal.SIGPIPE, prev)

    def on_signal(self, signum, frame):
        if self.busy:                  # one at a time, as bash ran its traps; the same one again is dropped
            if signum != self.busy and signum not in self.pending:
                self.pending.append(signum)
            return
        try:
            while True:
                self.busy = signum
                self.notice_and_kill("INT" if signum == signal.SIGINT else "TERM")
                if not self.pending:
                    break
                signum = self.pending.pop(0)
        finally:
            self.busy = False

    def run(self):
        self.nonblocking = None
        try:
            return self._run()
        finally:
            self._give_back_stdin()

    def _give_back_stdin(self):
        """A stdin that came in non-blocking goes back to that, on every way out: the caller shares it."""
        if self.nonblocking is not None:
            try:
                os.set_blocking(self.nonblocking, False)
            finally:
                os.close(self.nonblocking)
                self.nonblocking = None

    def _run(self):
        args = self.args
        if args and args[0] == "--":
            args = args[1:]
        prompt = ""
        if args and not ere(FLAG, args[0]):
            prompt, args = args[0], args[1:]
        piped = _piped()
        if piped:
            try:
                if not os.get_blocking(0):   # a caller's non-blocking pipe: read it as bash's read and cat waited
                    self.nonblocking = os.dup(0)
                    os.set_blocking(0, True)
            except OSError:
                pass
        if not prompt and not piped:
            die('headless: give the prompt first, e.g. nexus %s headless "list the TODOs" --output-format json; or pipe it in'
                % self.p.name)
        first = b""
        if piped and prompt and not stat.S_ISREG(os.fstat(0).st_mode):
            rc, first = _first_char(3)
            if rc > 128:
                self.out.warn("headless: no input on stdin within 3 s, so running without it (</dev/null skips the wait)")
                piped = False
            elif rc:
                piped = False
        f = flavours.get(self.p.flavour)
        if f is None:
            die("headless: no one-shot mode known for %s's flavour '%s'" % (self.p.name, self.p.flavour))
        argv, feed = f.headless_argv(prompt, piped, args)
        if piped:
            try:
                fd, spool = tempfile.mkstemp(prefix="tmp.", dir=os.environ.get("TMPDIR") or "/tmp")
            except OSError:
                die("headless: can't make a temp file for the input")
            os.unlink(spool)
            try:
                if feed:
                    _write_all(fd, feed.encode("utf-8", "surrogateescape") + b"\n")
                _write_all(fd, first)
                while True:
                    chunk = os.read(0, 65536)
                    if not chunk:
                        break
                    _write_all(fd, chunk)
                os.lseek(fd, 0, os.SEEK_SET)
                os.dup2(fd, 0)
            except OSError as e:
                die("headless: can't keep the input piped in: %s" % e.strerror)
            finally:
                os.close(fd)
            if not prompt and os.fstat(0).st_size == 0:
                die("headless: no prompt, and the input piped in was empty")
        self._give_back_stdin()
        self.run_id = "nexus-%d-%d%d" % (os.getpid(), random.randint(0, 32767), random.randint(0, 32767))
        dx = ["docker", "exec", "-e", "NEXUS_RUN=" + self.run_id, "-w", self.dir]
        if piped:
            dx.append("-i")
        dx += [self.p.container] + argv
        old = sigs.trap((signal.SIGINT, signal.SIGTERM, signal.SIGHUP), self.on_signal)
        try:
            proc.flush()
            try:
                child = subprocess.Popen(dx, start_new_session=True, preexec_fn=_as_a_job)
            except OSError as e:
                raise NexusError("docker: %s" % e.strerror, 127 if isinstance(e, FileNotFoundError) else 126)
            rc = proc.exit_code(child.wait())
        finally:
            sigs.untrap(old)
        if self.sig == "INT":
            return 130
        if self.sig == "TERM":
            return 143
        return rc
