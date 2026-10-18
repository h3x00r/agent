"""Running a tool for a golden scenario: on pipes, or with a terminal, typing answers as prompts appear.

Modes:
  pipe     stdin from bytes (or /dev/null), stdout and stderr on pipes: no prompt text, as in bash
           when stdin isn't a terminal.
  tty-in   stdin on a pty (the controlling terminal), stdout and stderr on pipes: bash's `read -p`
           prompts reach stderr only because stdin is a terminal. What the pty echoes is kept apart.
  tty-all  stdin, stdout and stderr all on the pty (colours, Ctrl-C): one combined stream.

Steps (tty modes) are (substring, answer) pairs: wait until the watched output has substring past the
previous match, then type answer. Answers "<EOF>" and "<INT>" send ^D and ^C.
"""
import fcntl
import os
import pty
import select
import signal
import struct
import subprocess
import termios
import time


def _winsize(fd, rows=40, cols=120):
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))


class Result:
    def __init__(self, rc, out, err, echo=b"", timed_out=False):
        self.rc, self.out, self.err, self.echo, self.timed_out = rc, out, err, echo, timed_out


def _rc(status):
    return 128 - status if status < 0 else status   # Popen gives -N for a signal; the shell says 128+N


def run(argv, env, cwd, mode="pipe", stdin=None, steps=(), timeout=30.0, merge=False):
    if mode == "pipe":
        return _run_pipe(argv, env, cwd, stdin, timeout, merge)
    return _run_tty(argv, env, cwd, mode, steps, timeout)


def _default_signals():
    """INT and QUIT back to their defaults in the child: bash can't trap a signal ignored when it started,
    so a harness started in the background (SIGINT ignored) would otherwise test nothing."""
    signal.signal(signal.SIGINT, signal.SIG_DFL)
    signal.signal(signal.SIGQUIT, signal.SIG_DFL)


def _run_pipe(argv, env, cwd, stdin, timeout, merge):
    p = subprocess.Popen(argv, env=env, cwd=cwd, start_new_session=True, preexec_fn=_default_signals,
                         stdin=subprocess.PIPE if stdin is not None else subprocess.DEVNULL,
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT if merge else subprocess.PIPE)
    try:
        out, err = p.communicate(stdin, timeout=timeout)
        return Result(_rc(p.returncode), out, err or b"")
    except subprocess.TimeoutExpired:
        os.killpg(p.pid, signal.SIGKILL)
        out, err = p.communicate()
        return Result(124, out, err or b"", timed_out=True)


def _run_tty(argv, env, cwd, mode, steps, timeout):
    master, slave = pty.openpty()
    _winsize(slave)

    def child_setup():
        os.setsid()
        fcntl.ioctl(0, termios.TIOCSCTTY, 0)
        _default_signals()

    if mode == "tty-in":
        p = subprocess.Popen(argv, env=env, cwd=cwd, stdin=slave, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, preexec_fn=child_setup)
        streams = {p.stdout.fileno(): bytearray(), p.stderr.fileno(): bytearray(), master: bytearray()}
        watched = [p.stdout.fileno(), p.stderr.fileno()]
    else:
        p = subprocess.Popen(argv, env=env, cwd=cwd, stdin=slave, stdout=slave, stderr=slave,
                             preexec_fn=child_setup)
        streams = {master: bytearray()}
        watched = [master]
    os.close(slave)
    deadline = time.monotonic() + timeout
    steps = list(steps)
    pos = 0
    open_fds = set(streams)
    timed_out = False
    seen = bytearray()   # the watched streams, interleaved as they came

    while True:
        if steps:
            want, answer = steps[0]
            at = seen.find(want.encode(), pos)
            if at >= 0:
                pos = at + len(want.encode())
                steps.pop(0)
                time.sleep(0.05)   # the prompt is out; let the reader block on the terminal
                if answer == "<EOF>":
                    os.write(master, b"\x04")
                elif answer == "<INT>":
                    os.write(master, b"\x03")
                else:
                    os.write(master, answer.encode() + b"\r")
                continue
        if not open_fds:
            break
        left = deadline - time.monotonic()
        if left <= 0:
            timed_out = True
            break
        ready, _, _ = select.select(list(open_fds), [], [], min(left, 0.1))
        for fd in ready:
            try:
                data = os.read(fd, 65536)
            except OSError:   # EIO: the pty's other side is closed
                data = b""
            if data:
                streams[fd].extend(data)
                if fd in watched:
                    seen.extend(data)
            else:
                open_fds.discard(fd)
        if p.poll() is not None and not ready:
            # Drain what is left, then stop.
            for fd in list(open_fds):
                while True:
                    r, _, _ = select.select([fd], [], [], 0.05)
                    if not r:
                        break
                    try:
                        data = os.read(fd, 65536)
                    except OSError:
                        data = b""
                    if not data:
                        break
                    streams[fd].extend(data)
            break
    if timed_out:
        try:
            os.killpg(p.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    rc = p.wait()
    os.close(master)
    if mode == "tty-in":
        r = Result(124 if timed_out else _rc(rc), bytes(streams[p.stdout.fileno()]),
                   bytes(streams[p.stderr.fileno()]), echo=bytes(streams[master]), timed_out=timed_out)
        p.stdout.close()
        p.stderr.close()
        return r
    return Result(124 if timed_out else _rc(rc), bytes(streams[master]).replace(b"\r\n", b"\n"), b"",
                  timed_out=timed_out)
