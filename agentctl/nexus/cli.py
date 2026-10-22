"""nexus's command line: which command, in the order the bash launcher decided it (it decides which error
wins): no arguments or -h/--help first, then --version (it needs neither Docker nor personas), then the checkout
and the Docker socket, then ls and top, then the personas file, the persona's name and its line, then the
persona's own subcommand (persona_cmds).

Pitfalls (each pinned where named):
- -h/--help counts only first: after a persona it is a session name (golden nexus/basic-help-after-persona)
- top's arguments are checked before the personas file (golden nexus/basic-not-set-up)
- a persona name is matched whole, [a-z0-9][a-z0-9-]* (golden nexus/basic-names)
"""
import os
import re

from agentctl.core.errors import NexusError
from agentctl.nexus import ls, persona_cmds, top, version
from agentctl.nexus.context import Context
from agentctl.nexus.usage import USAGE

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.realpath(__file__))))


def main(argv, out):
    if not argv:
        out.usage(USAGE)
        return 1
    if argv[0] in ("-h", "--help"):
        out.usage(USAGE)
        return 0
    if argv[0] == "--version":
        if argv[1:]:
            raise NexusError("--version takes nothing after it, not '%s'" % " ".join(argv[1:]))
        return version.version(REPO, out)
    ctx = Context(out, REPO)
    name, rest = argv[0], argv[1:]
    if name == "ls":
        return ls.ls_all(ctx)
    if name == "top":
        return top.top(ctx, rest)
    ctx.need_personas()
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", name):
        raise NexusError("not a persona name: '%s'" % name)
    return persona_cmds.Run(ctx, name, rest).main()
