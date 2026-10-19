"""The one error type the tools stop with.

A NexusError is a message for the user and the exit code that goes with it. Services raise it; only the
entry point (_boot.py) prints it, as `<tool>: <message>` on stderr, and exits with its code.
"""


class NexusError(Exception):
    def __init__(self, message, code=1):
        super().__init__(message)
        self.message = message
        self.code = code


class Interrupted(Exception):
    """A run stopped by a signal it handles itself, ending with CODE: 130 for nexus --config and upgrade,
    whichever of INT, TERM or HUP it was, as their bash traps had it."""

    def __init__(self, code):
        super().__init__(code)
        self.code = code


class Exit(Exception):
    """A run ending with CODE, its message already printed (as bash's `echo … >&2; exit 1`)."""

    def __init__(self, code):
        super().__init__(code)
        self.code = code
