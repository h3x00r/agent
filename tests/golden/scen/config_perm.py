"""A claude persona's permissions: bypass (personas/claude.managed.json's bypassPermissions, the default) or ask
(personas/claude.ask.json, a managed drop-in after it): set with --permissions or in the menu's add and change,
kept when the persona is given again, gone with a flavour of grok; and what is refused."""
import os

from scen import case, set_field
from scen.config_cli import YES, grep
from scen.config_interactive import MENU, PICK, perm, sl, tty

ASK = "a/personas/claude.ask.json"


@case("config/perm-ask")
def _(sb):
    sb.run("config", *YES, "--persona", "c1:claude:docker", "--persona", "c2:claude:docker", "--permissions", "c1:ask")
    sb.run("config", "--list")
    grep(sb, "managed-settings.d|^  \"", "compose.personas.yml")
    grep(sb, "permissions", "personas.json")
    sb.run("config", *YES, "--persona", "c1:claude:docker")   # given again: it still asks
    sb.run("config", *YES, "--permissions", "c1:bypass", "--permissions", "c2:ask")   # a restart for each
    grep(sb, "16-ask|^  \"", "compose.personas.yml")
    sb.run("config", *YES, "--persona", "c2:grok:docker")     # grok now: the ask goes with the flavour
    grep(sb, "permissions", "personas.json")


@case("config/perm-change")
def _(sb):
    # In the menu: c1 to ask (a restart, and the overview line), then back to bypass.
    sb.personas("c1:claude:docker", "c2:claude:docker")
    tty(sb, [(MENU, "change"), (PICK, "c1"), ("choice [claude]: ", ""), ("choice [docker]: ", ""), (sl("c1"), ""),
             ("Projects root for c1", ""), ("choice [agent]: ", ""), (perm("c1"), "n"), (MENU, "list"), (MENU, "")],
        label="config --no-link --no-build  (c1 asks)")
    tty(sb, [(MENU, "change"), (PICK, "c1"), ("choice [claude]: ", ""), ("choice [docker]: ", ""), (sl("c1"), ""),
             ("Projects root for c1", ""), ("choice [agent]: ", ""), (perm("c1", "y/N"), "y"), (MENU, "")],
        label="config --no-link --no-build  (c1 bypasses again)")


@case("config/perm-refused")
def _(sb):
    sb.personas("c1:claude:docker", "g1:grok:docker")
    sb.run("config", *YES, "--permissions")
    sb.run("config", *YES, "--permissions", "c1")
    sb.run("config", *YES, "--permissions", "c1:maybe")
    sb.run("config", *YES, "--permissions", "Bad!:ask")
    sb.run("config", *YES, "--permissions", "nobody:ask")
    sb.run("config", *YES, "--permissions", "g1:ask")
    sb.run("config", *YES, "--permissions", "g1:bypass")      # what it has already: nothing to say
    # personas.json edited by hand: a value it doesn't take, ask on a grok persona, not a string.
    for name, value in (("c1", "maybe"), ("c1", True), ("g1", "ask")):
        set_field(sb, name, "permissions", value)
        sb.run("config", "--list", label="config --list  (%s's permissions: %r)" % (name, value))
        set_field(sb, name, "permissions", None)
    sb.run("config", "--list", label="config --list  (as it was)")


@case("config/perm-file-missing")
def _(sb):
    sb.personas("c1:claude:docker", "c2:claude:docker", extra=("--permissions", "c1:ask"))
    with open(sb.path(ASK), "rb") as f:
        keep = f.read()
    os.unlink(sb.path(ASK))
    sb.run("config", *YES, label="config --yes …  (claude.ask.json gone, c1 asks)")
    sb.run("config", *YES, "--permissions", "c1:bypass", label="config --yes … --permissions c1:bypass  (nobody asks)")
    sb.write(ASK, "{ not json\n")
    sb.run("config", *YES, label="config --yes …  (broken, but nobody asks)")
    sb.run("config", *YES, "--permissions", "c2:ask", label="config --yes … --permissions c2:ask  (broken)")
    for text, what in (("{}\n", "no mode"), ('{"permissions": {"defaultMode": "bypassPermissions"}}\n', "bypassPermissions")):
        sb.write(ASK, text)
        sb.run("config", *YES, "--permissions", "c1:ask", "--permissions", "c2:ask",
               label="config --yes … --permissions c1:ask --permissions c2:ask  (%s)" % what)
    sb.write(ASK, keep, 0o000)
    sb.run("config", *YES, "--permissions", "c2:ask", label="config --yes … --permissions c2:ask  (unreadable)")
    os.chmod(sb.path(ASK), 0o644)
    sb.run("config", *YES, "--permissions", "c2:ask", label="config --yes … --permissions c2:ask  (restored)")
