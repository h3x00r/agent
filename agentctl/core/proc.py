"""Running other programs the way the bash tools did.

- Output is flushed before every child starts, so the tool's own lines and the child's come out in the
  order they were written (Python 3.8 buffers stderr when it isn't a terminal).
- A child's output is text decoded with surrogateescape: bytes that aren't UTF-8 survive, to be cleaned
  (ui.text.clean) or passed on as they were.
- An exit by signal N is 128+N, as the shell reports it, never Popen's -N.
- run(..., timeout=S) is `timeout S cmd`: on time-out the child gets TERM, what it printed so far is kept,
  and the exit code is 124.
- execvp() replaces this process, as bash's `exec` did: the terminal, the pid and the signals go to the
  child (docker exec -it, compose).
"""
import os
import signal
import subprocess
import sys


class Completed:
    def __init__(self, rc, out="", err=""):
        self.rc, self.out, self.err = rc, out, err

    @property
    def ok(self):
        return self.rc == 0


def flush():
    for s in (sys.stdout, sys.stderr):
        try:
            s.flush()
        except (OSError, ValueError):
            pass


def exit_code(returncode):
    return 128 - returncode if returncode < 0 else returncode


def _decode(b):
    return b.decode("utf-8", "surrogateescape") if b is not None else ""


def run(argv, stdin=None, input=None, timeout=None, env=None, cwd=None, capture=True, merge=False,
        new_session=False, stdout_to_stderr=False):
    """Run ARGV and wait. stdin: None (this process's), a file object or fd, or subprocess.DEVNULL; input:
    text or bytes to feed instead. capture=False lets the output through (stdout_to_stderr: its stdout to
    our stderr, as `cmd >&2`); capture="stdout" takes stdout only; merge folds stderr into out."""
    flush()
    if isinstance(input, str):
        input = input.encode("utf-8", "surrogateescape")
    kw = dict(env=env, cwd=cwd, start_new_session=new_session)
    if input is not None:
        kw["stdin"] = subprocess.PIPE
    elif stdin is not None:
        kw["stdin"] = stdin
    if capture == "stdout":            # $(cmd): stdout captured, stderr through to ours
        kw["stdout"] = subprocess.PIPE
    elif capture:
        kw["stdout"] = subprocess.PIPE
        kw["stderr"] = subprocess.STDOUT if merge else subprocess.PIPE
    elif stdout_to_stderr:
        kw["stdout"] = 2
    try:
        p = subprocess.Popen(argv, **kw)
    except FileNotFoundError:
        return Completed(127, "", "%s: command not found\n" % argv[0])
    except PermissionError:
        return Completed(126, "", "%s: Permission denied\n" % argv[0])
    try:
        out, err = p.communicate(input, timeout=timeout)
        rc = exit_code(p.returncode)
    except subprocess.TimeoutExpired:
        p.send_signal(signal.SIGTERM)
        out, err = p.communicate()
        rc = 124
    return Completed(rc, _decode(out), _decode(err))


def ok(argv, **kw):
    """Whether ARGV succeeds, its output dropped (stdin inherited, as in bash, unless given)."""
    flush()
    try:
        return subprocess.call(argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **kw) == 0
    except OSError:
        return False


def execvp(argv, env=None):
    """Become ARGV, as bash's exec: flushed first, and with SIGPIPE back at its default for the child."""
    flush()
    signal.signal(signal.SIGPIPE, signal.SIG_DFL)
    if env is None:
        os.execvp(argv[0], argv)
    os.execvpe(argv[0], argv, env)
