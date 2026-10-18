# The entry point the shims `nexus` and `config-tools` exec: python3 -I -B -S agentctl/_boot.py TOOL ARGS…
# Its first lines run on any Python 3, so an older one still gets a clear message; the rest needs 3.8.
import os
import sys

if sys.version_info < (3, 8):
    sys.stderr.write("%s: needs Python 3.8 or newer; python3 here is %d.%d\n"
                     % (sys.argv[1] if len(sys.argv) > 1 else "agentctl", sys.version_info[0], sys.version_info[1]))
    sys.exit(1)

sys.dont_write_bytecode = True
REPO = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
sys.path.insert(0, REPO)


def _streams():
    """stdout and stderr pass undecodable bytes through (surrogateescape), as bash printed them."""
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(errors="surrogateescape")
        except (AttributeError, ValueError):
            pass


def main():
    import signal
    import agentctl
    if not os.path.realpath(agentctl.__file__).startswith(REPO + os.sep):
        sys.stderr.write("agentctl: imported from %s, not from %s\n" % (agentctl.__file__, REPO))
        return 1
    from agentctl.core.errors import Exit, Interrupted, NexusError
    from agentctl.ui.output import Output
    _streams()
    signal.signal(signal.SIGPIPE, signal.SIG_DFL)   # a closed pipe ends the run quietly, as it ended bash
    tool, argv = (sys.argv[1], sys.argv[2:]) if len(sys.argv) > 1 else ("", [])
    if tool == "config":
        out = Output("config")

        def interrupted(signum, frame):
            raise Interrupted(130)
        # As the bash tool's `trap 'exit 130' INT TERM HUP`: staged files are removed on the way out.
        for s in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            signal.signal(s, interrupted)
        from agentctl.configtools import setup
        run = lambda: setup.main(REPO, argv, out)  # noqa: E731
    elif tool == "nexus":
        out = Output("nexus")
        signal.signal(signal.SIGINT, signal.SIG_DFL)   # bash's default: Ctrl-C ends the run where no trap says otherwise
        from agentctl.nexus import cli
        run = lambda: cli.main(argv, out)  # noqa: E731
    else:
        sys.stderr.write("agentctl: run it as nexus or config-tools\n")
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
