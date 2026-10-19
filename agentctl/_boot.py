# The entry point the shim `nexus` execs: python3 -I -B -S agentctl/_boot.py nexus ARGS… (`nexus --config …` is the
# setup tool).
# Its first lines run on any Python 3, so an older one still gets a clear message; the rest needs 3.8.
"""Pitfalls (each pinned where named):
- the whole file is compiled before the version check runs, so all of it must parse on any Python 3 (no
  f-string, no `:=`) (test_boot.Boot.test_it_parses_on_any_python3)
- run as the shims run it, -I -B -S: no PYTHON* setting, site dir or bytecode (run.sh "PYTHONPATH, PYTHONHOME
  and PYTHONSTARTUP don't reach it", run.sh "no Python bytecode in the checkout")
- an agentctl imported from anywhere but this checkout is refused (not pinned)
- core.sigs is imported before any handler changes, so a signal ignored at start stays ignored (golden
  nexus/edge-persona-sigint-ignored)
- SIGPIPE back at its default: a closed pipe ends the run as it ended bash (golden nexus/basic-sigpipe)
- nexus: SIGINT at SIG_DFL, never KeyboardInterrupt and its traceback (golden nexus/edge-headless-sig-spool)
- nexus --config: INT, TERM and HUP exit 130, the staged files removed (golden config/ask-int-tty)
- text is measured by the locale the shim saw (AGENTCTL_LOCALE), not by Python's view of it (golden
  nexus/edge-ls-bad-locale, golden nexus/edge-top-locale-missing)
- LC_CTYPE goes back to the shim's for every child, PEP 538's coercion undone
  (test_boot.Boot.test_lc_ctype_goes_back_to_what_the_shim_saw)
- a stdout that came in closed (`>&-`) stops the run at once (golden nexus/edge-ls-stdout-closed)
- a stderr that came in closed or read-only gets /dev/null, so no file opened later takes fd 2
  (test_boot.Boot.test_a_closed_or_read_only_stderr_is_no_crash)
- stdout and stderr write undecodable bytes back as they came (golden nexus/edge-ls-wide-tty)
"""
import os
import sys

if sys.version_info < (3, 8):
    sys.stderr.write("%s: needs Python 3.8 or newer; python3 here is %d.%d\n"
                     % ("config" if sys.argv[2:3] == ["--config"] else sys.argv[1] if len(sys.argv) > 1 else "agentctl",
                        sys.version_info[0], sys.version_info[1]))
    sys.exit(1)

sys.dont_write_bytecode = True
REPO = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
sys.path.insert(0, REPO)


def _std_out_err():
    """stdout or stderr that came in closed (`>&-`) is opened on /dev/null, so no file this run opens takes
    its number (and a child's output with it). One open only for reading counts as closed: bash running
    the shim with its stderr closed leaves the shim's own file there, read-only. Returns whether stdout
    was the closed one."""
    import fcntl
    closed = []
    for fd in (1, 2):
        try:
            if fcntl.fcntl(fd, fcntl.F_GETFL) & os.O_ACCMODE == os.O_RDONLY:
                closed.append(fd)
        except OSError:
            closed.append(fd)
    for fd in closed:
        null = os.open(os.devnull, os.O_WRONLY)
        if null != fd:
            os.dup2(null, fd)
            os.close(null)
        stream = open(fd, "w", closefd=False)
        if fd == 1:
            sys.stdout = stream
        else:
            sys.stderr = stream
    return 1 in closed


def _streams():
    """stdout and stderr pass undecodable bytes through (surrogateescape), as bash printed them."""
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(errors="surrogateescape")
        except (AttributeError, ValueError):
            pass


def _locale():
    """The locale as the shim's bash saw it (AGENTCTL_LOCALE: 1 or 0 for UTF-8, then `:LC_CTYPE` if that
    was set): text is measured by it, and LC_CTYPE goes back to what it was for every child."""
    from agentctl.core import shellcompat
    v = os.environ.pop("AGENTCTL_LOCALE", None)
    if v is None:
        return
    shellcompat.UTF8 = v[:1] == "1"
    if v[1:2] == ":":
        os.environ["LC_CTYPE"] = v[2:]
    else:
        os.environ.pop("LC_CTYPE", None)


def main():
    import signal
    from agentctl.core import sigs     # first: it notes the signals ignored at start, before any handler changes
    import agentctl
    if not os.path.realpath(agentctl.__file__).startswith(REPO + os.sep):
        sys.stderr.write("agentctl: imported from %s, not from %s\n" % (agentctl.__file__, REPO))
        return 1
    from agentctl.core.errors import Exit, Interrupted, NexusError
    from agentctl.ui.output import Output
    _locale()
    stdout_closed = _std_out_err()
    _streams()
    signal.signal(signal.SIGPIPE, signal.SIG_DFL)   # a closed pipe ends the run quietly, as it ended bash
    tool, argv = (sys.argv[1], sys.argv[2:]) if len(sys.argv) > 1 else ("", [])
    if tool == "nexus" and argv[:1] == ["--config"]:   # the setup tool: its own signals and prefix, from the start
        tool, argv = "config", argv[1:]
    if tool == "config":
        out = Output("config")

        def interrupted(signum, frame):
            raise Interrupted(130)
        # As the bash tool's `trap 'exit 130' INT TERM HUP`: staged files are removed on the way out.
        sigs.trap((signal.SIGINT, signal.SIGTERM, signal.SIGHUP), interrupted)
        from agentctl.configtools import setup
        run = lambda: setup.main(REPO, argv, out)  # noqa: E731
    elif tool == "nexus":
        out = Output("nexus")
        sigs.trap((signal.SIGINT,), signal.SIG_DFL)   # bash's default: Ctrl-C ends the run where no trap says otherwise
        from agentctl.nexus import cli
        run = lambda: cli.main(argv, out)  # noqa: E731
    else:
        sys.stderr.write("agentctl: run it as nexus\n")
        return 1
    if stdout_closed:     # bash's first printf failed there ("write error: Bad file descriptor"); stop first
        out.note("its output is closed (>&-): nothing it says would show")
        return 1
    try:
        return run()
    except NexusError as e:
        out.note(e.message)
        return e.code
    except (Interrupted, Exit) as e:
        return e.code


if __name__ == "__main__":
    sys.exit(main())
