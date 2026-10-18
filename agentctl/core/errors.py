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
    """A run stopped by a signal it handles itself (INT gives 130, TERM and HUP 143)."""

    def __init__(self, code):
        super().__init__(code)
        self.code = code
