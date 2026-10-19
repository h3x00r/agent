"""Signal handlers the way bash set its traps.

A non-interactive bash can't trap or reset a signal that was ignored when it started (a job started with
`&` from a script has INT and QUIT ignored, nohup HUP), and its children inherit the ignore. IGNORED_AT_START
is read when this module is first imported, which _boot does before any handler changes (Python itself
leaves an ignored SIGINT ignored); trap() leaves those signals alone.

Pitfalls (each pinned where named):
- one ignored at start is never trapped or reset
  (test_adapters.SignalsAtStart.test_one_ignored_at_start_is_never_trapped, golden
  nexus/edge-persona-sigint-ignored)
"""
import signal

IGNORED_AT_START = frozenset(s for s in (signal.SIGINT, signal.SIGQUIT, signal.SIGTERM, signal.SIGHUP)
                             if signal.getsignal(s) == signal.SIG_IGN)


def trap(signals, handler):
    """HANDLER for each of SIGNALS that wasn't ignored at start; the old handlers, for untrap()."""
    return {s: signal.signal(s, handler) for s in signals if s not in IGNORED_AT_START}


def untrap(old):
    for s, h in old.items():
        signal.signal(s, h)
