"""nexus --config: the flags, then the run. The parts:

  session.py    what a run holds: the personas read, the lock, the preflight, and saving (stage, check, commit)
  questions.py  every question it asks, on a terminal, by piped lines, or by their defaults under --yes
  after.py      what follows a save: ssh keys, the launcher link, mkcert, the images
  scripted.py   --yes (and --list): the run in one pass, in the bash tool's order
  menu.py       without --yes: a first run's shared settings, then what is set up and a menu, each action saved
  host.py       the small helpers that touch the system

Pitfalls (each pinned where named):
- a run stopped before the commit leaves nothing staged behind (golden config/ask-int-tty)
"""
import os

from agentctl.configtools import menu, scripted, staging
from agentctl.configtools.cli import USAGE
from agentctl.configtools.session import Session


def main(repo, argv, out):
    from agentctl.configtools.cli import parse
    from agentctl.nexus.context import checkout
    opts = parse(argv)
    if opts.help:
        out.usage(USAGE)
        return 0
    checkout = checkout(repo)     # after the flags: --help and a bad option touch nothing
    os.chdir(checkout)
    try:
        return (scripted if opts.yes or opts.list else menu).run(Session(checkout, opts, out))
    finally:
        staging.cleanup()
