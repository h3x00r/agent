"""Colours, and what each one means.

A Palette has the six codes the tools use (bold, dim, red, yellow, green, reset) when stdout is a terminal
and NO_COLOR is unset or empty, and empty strings otherwise, so code that formats never asks. The tests
strip exactly these codes (\\e[NNm and \\e[Nm) to measure rows: use no others.

What a colour stands for is decided here too: a container's state, how full a context or limit is.

Pitfalls (each pinned where named):
- an empty NO_COLOR leaves colour on (test_ui.Palette.test_off_without_a_terminal_or_with_no_color)
- load(): a leading zero is decimal, never octal (golden nexus/edge-top-octal-colour)
"""
import os
import sys

from agentctl.core.shellcompat import ere

_CODES = {"bold": "\033[1m", "dim": "\033[90m", "red": "\033[31m", "yellow": "\033[33m", "green": "\033[32m",
          "reset": "\033[0m"}


class Palette:
    def __init__(self, enabled):
        self.enabled = enabled
        for k, v in _CODES.items():
            setattr(self, k, v if enabled else "")

    @classmethod
    def for_stdout(cls, stream=None, env=None):
        stream = stream or sys.stdout
        env = os.environ if env is None else env
        try:
            tty = stream.isatty()
        except (AttributeError, ValueError):
            tty = False
        return cls(tty and not env.get("NO_COLOR"))

    def state(self, state):
        """A container's state: green while it runs, red unhealthy, yellow on the way, dim otherwise."""
        if state in ("healthy", "running"):
            return self.green
        if state == "unhealthy":
            return self.red
        if state in ("starting", "restarting"):
            return self.yellow
        return self.dim

    def load(self, pct):
        """A percentage used (a context, a limit): green, yellow from 50, red from 80; nothing if not a number."""
        if not ere(r"[0-9]+", pct):
            return ""
        n = int(pct)
        if n >= 80:
            return self.red
        if n >= 50:
            return self.yellow
        return self.green
