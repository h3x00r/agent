"""The setup tool moving a checkout's personas from compose.personas.yml's `# persona:` lines into personas.json
(M4), and personas.json refused by both tools. New behaviour: recorded from the Python tree (accept)."""
import os

from scen import NO_LINK, before_personas_json, case  # noqa: F401
from scen.config_cli import grep, persona_lines
from scen.nexus_lifecycle import LEGACY_CLAUDE_JSON, LEGACY_STATUSLINE

YES = ("--yes",) + NO_LINK


def personas_json(sb, text):
    sb.write("a/personas.json", text)


@case("config/migrate-config-sh-era")
def _(sb):
    # A checkout config.sh set up: --list reads the lines and writes nothing; --yes moves them, keeps the old
    # file as .bak, and says so; the next run changes nothing.
    sb.personas("c1:claude:docker", "g1:grok:docker", extra=("--ssh", "g1:new"))
    before_personas_json(sb)
    sb.run("config", "--list")
    sb.run("config", *YES)
    persona_lines(sb)
    sb.run("config", *YES, label="config --yes --no-link --no-build  (again)")
    sb.run("nexus", "ls")


@case("config/migrate-keeps-bak")
def _(sb):
    # An older compose.personas.yml.bak (it may hold a key) survives: the moved file goes to .bak.2.
    sb.personas("c1:claude:docker")
    before_personas_json(sb)
    sb.write("a/compose.personas.yml.bak", "# an older backup\n")
    sb.run("config", *YES)
    grep(sb, "^#", "compose.personas.yml.bak")


@case("config/migrate-older-layout")
def _(sb):
    # A service from before the statusline and Claude settings splits: written anew, so a restart applies it;
    # the other persona's service is the same, so it isn't told to.
    sb.personas("c1:claude:docker", "c2:claude:docker")
    before_personas_json(sb)
    p = sb.path("a/compose.personas.yml")
    with open(p) as f:
        text = f.read()
    with open(p, "w") as f:
        f.write(text.replace("      - c1-home:/home/dev\n", "      - c1-home:/home/dev\n" + LEGACY_STATUSLINE
                             + LEGACY_CLAUDE_JSON, 1))
    sb.run("nexus", "c1", "start", label="nexus c1 start  (before the move)")
    sb.run("config", *YES)
    grep(sb, "statusline:|claude.json", "compose.personas.yml")


@case("config/migrate-remove-all")
def _(sb):
    # Moving with every persona removed: nothing to write; the old file is kept, no personas.json made.
    sb.personas("c1:claude:docker")
    before_personas_json(sb)
    sb.run("config", *YES, "--remove", "c1")
    sb.run("nexus", "ls")


@case("config/personas-json-refused")
def _(sb):
    # personas.json edited by hand into something its rules don't take: both tools refuse, naming it.
    sb.personas("c1:claude:docker")
    with open(sb.path("a/personas.json")) as f:
        good = f.read()
    for bad, what in ((good[:-3], "cut short"),
                      (good.replace('"version": 1', '"version": 2'), "version 2"),
                      (good.replace('"colour": "colour25",', '"colour": "colour25", "colour": "colour28",'), "a key twice"),
                      (good.replace('"statusline": true', '"statusline": true, "theme": "dark"'), "an unknown key"),
                      (good.replace('"statusline": true', '"statusline": "yes"'), "statusline \"yes\""),
                      (good.replace('"c1"', '"C1"'), "a name it doesn't take")):
        personas_json(sb, bad)
        sb.run("config", "--list", label="config --list  (personas.json: %s)" % what)
        sb.run("nexus", "c1", label="nexus c1  (personas.json: %s)" % what)
    personas_json(sb, good)
    sb.run("config", "--list", label="config --list  (personas.json as it was)")
