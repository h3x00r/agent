"""Running other programs the way the bash tools did.

- Output is flushed before every child starts, so the tool's own lines and the child's come out in the
  order they were written (Python 3.8 buffers stderr when it isn't a terminal).
- A child's output is text decoded with surrogateescape: bytes that aren't UTF-8 survive, to be cleaned
  (ui.text.clean) or passed on as they were. NUL bytes are dropped, as bash's $(…) and `read` dropped them.
- An exit by signal N is 128+N, as the shell reports it, never Popen's -N.
- run(..., timeout=S) is `timeout S cmd`: the child runs in a process group of its own, and on time-out
  that whole group gets TERM (a wrapper's children too); what it printed so far is kept, and the exit code
  is 124.
- A program that isn't there, or can't be run, is 127 or 126, and says so on stderr unless stderr is
  captured (then it is the captured err), as the shell said "docker: command not found".
- execvp() replaces this process, as bash's `exec` did: the terminal, the pid and the signals go to the
  child (docker exec -it, compose).

Pitfalls (each pinned where named):
- flushed first (golden nexus/life-ddev-ca)
- undecodable bytes kept, NUL bytes dropped (test_adapters.Proc.test_undecodable_bytes_survive, golden
  nexus/edge-persona-defaults-nul)
- 128+N (test_adapters.Proc.test_signal_exit_codes_are_the_shells)
- 124 with the partial output (test_adapters.Proc.test_timeout_keeps_what_came, golden
  nexus/edge-persona-defaults-timeout)
- the whole group gets the time-out's TERM (golden nexus/edge-ls-probe-hang-child)
- a child under a time-out starts with INT, QUIT, HUP and TERM at their defaults, even one ignored at start
  (golden nexus/edge-persona-sigint-ignored)
- 127 or 126 with the shell's message (golden nexus/edge-ls-docker-missing, golden
  nexus/edge-ls-docker-noexec)
- execvp() puts SIGPIPE back at its default for the program it becomes
  (test_boot.Proc.test_execvp_gives_the_new_program_sigpipe_at_its_default)
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
    return b.replace(b"\0", b"").decode("utf-8", "surrogateescape") if b is not None else ""


def _as_timeout_child():
    """In a child under a time-out, as timeout(1) leaves its command: in a process group of its own (so the
    time-out reaches all of it), with INT, QUIT, HUP and TERM at their defaults (timeout catches them for
    itself, so even one that came in ignored is reset)."""
    if os.getpgrp() != os.getpid():
        os.setpgrp()
    for s in (signal.SIGINT, signal.SIGQUIT, signal.SIGHUP, signal.SIGTERM):
        signal.signal(s, signal.SIG_DFL)


def _cant_run(argv, e):
    """(rc, message) for a program that couldn't be started, as the shell put it."""
    if isinstance(e, FileNotFoundError):
        return 127, "%s: command not found\n" % argv[0]
    return 126, "%s: %s\n" % (argv[0], e.strerror or "Permission denied")


def run(argv, stdin=None, input=None, timeout=None, env=None, cwd=None, capture=True, merge=False,
        new_session=False, stdout_to_stderr=False):
    """Run ARGV and wait. stdin: None (this process's), a file object or fd, or subprocess.DEVNULL; input:
    text or bytes to feed instead. capture=False lets the output through (stdout_to_stderr: its stdout to
    our stderr, as `cmd >&2`); capture="stdout" takes stdout only; merge folds stderr into out."""
    flush()
    if isinstance(input, str):
        input = input.encode("utf-8", "surrogateescape")
    kw = dict(env=env, cwd=cwd, start_new_session=new_session)
    if timeout is not None:
        kw["preexec_fn"] = _as_timeout_child
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
    except OSError as e:
        rc, msg = _cant_run(argv, e)
        if capture is True:     # $(cmd 2>&1) had the shell's message in the output; $(cmd) in its stderr
            return Completed(rc, msg, "") if merge else Completed(rc, "", msg)
        _say(msg)
        return Completed(rc)
    try:
        out, err = p.communicate(input, timeout=timeout)
        rc = exit_code(p.returncode)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(p.pid, signal.SIGTERM)
        except OSError:
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
    """Become ARGV, as bash's exec: flushed first, and with SIGPIPE back at its default for the child. A
    program that can't be run ends this one with 127 or 126 (Exit), saying so."""
    from agentctl.core.errors import Exit
    flush()
    signal.signal(signal.SIGPIPE, signal.SIG_DFL)
    try:
        if env is None:
            os.execvp(argv[0], argv)
        os.execvpe(argv[0], argv, env)
    except OSError as e:
        rc, msg = _cant_run(argv, e)
        _say(msg)
        raise Exit(rc)


def _say(msg):
    try:
        os.write(2, msg.encode("utf-8", "surrogateescape"))
    except OSError:
        pass
