"""config-tools on personas.json at its edges, from the review of M4: the move from the `# persona:` lines (a
config.sh file, CRLF, a rollback and a roll forward, a run cut short), and personas.json that isn't what it
should be (a directory, a link, a NUL, version true). New behaviour: recorded from the Python (accept)."""
import os
import shutil

from scen import NO_LINK, before_personas_json, case, two_personas  # noqa: F401
from scen.config_cli import grep, persona_lines

YES = ("--yes",) + NO_LINK


def edit(sb, rel, fn):
    p = sb.path(rel)
    with open(p, "rb") as f:
        text = f.read().decode()
    with open(p, "wb") as f:
        f.write(fn(text).encode())


def tool(sb, name):
    return os.path.join(sb.bin, name)


@case("config/m4-version-true")
def _(sb):
    sb.personas("c1:claude:docker")
    edit(sb, "a/personas.json", lambda t: t.replace('"version": 1', '"version": true'))
    sb.run("config", "--list", label="config --list  (version: true)")
    sb.run("nexus", "ls", label="nexus ls  (version: true)")
    edit(sb, "a/personas.json", lambda t: t.replace('"version": true', '"version": 1.0'))
    sb.run("config", "--list", label="config --list  (version: 1.0)")


@case("config/m4-root-nul")
def _(sb):
    sb.personas("c1:claude:docker")
    edit(sb, "a/personas.json", lambda t: t.replace('"statusline": true', '"statusline": true, "root": "/tmp/r\\u0000x"'))
    sb.run("config", "--list")
    sb.run("nexus", "ls")
    sb.run("nexus", "c1", "start", cwd="projects")


@case("config/m4-configsh-era-grok")
def _(sb):
    # A compose file as config.sh (before the rename) wrote it: only comments say config.sh.
    two_personas(sb)
    before_personas_json(sb)
    edit(sb, "a/compose.personas.yml", lambda t: t.replace("written by ./config-tools", "written by ./config.sh")
         .replace("# config-tools and the nexus", "# config.sh and the nexus")
         .replace("written by config-tools: personas/grok.toml", "written by config.sh: personas/grok.toml")
         .replace("written by nexus --config: personas/grok.toml", "written by config.sh: personas/grok.toml"))
    sb.run("config", *YES)
    sb.run(tool(sb, "diff"), "compose.personas.yml.bak", "compose.personas.yml", cwd="a", label="diff .bak new")


@case("config/m4-crlf-restart")
def _(sb):
    two_personas(sb)
    before_personas_json(sb)
    edit(sb, "a/compose.personas.yml", lambda t: t.replace("\n", "\r\n"))
    sb.run("config", *YES)


@case("config/m4-tail-edit-no-note")
def _(sb):
    # A line added by hand at the end of c1's service: dropped, no restart asked; the same in c2's middle: asked.
    sb.personas("c1:claude:docker", "c2:claude:docker")
    before_personas_json(sb)

    def f(t):
        t = t.replace('\n\n  "c2":\n', "\n      - /srv/data:/data\n" + '\n  "c2":\n', 1)
        return t.replace("      - c2-home:/home/dev\n", "      - c2-home:/home/dev\n      - /srv/data:/data\n", 1)
    edit(sb, "a/compose.personas.yml", f)
    grep(sb, "srv/data|^  \"", "compose.personas.yml", label="grep before")
    sb.run("config", *YES)
    grep(sb, "srv/data", "compose.personas.yml", label="grep after (rc 1: gone)")


@case("config/m4-json-dir")
def _(sb):
    # personas.json a directory, on a checkout not yet moved: a run that adds c2.
    sb.personas("c1:claude:docker")
    before_personas_json(sb)
    os.mkdir(sb.path("a/personas.json"))
    sb.run("config", *YES, "--persona", "c2:claude:docker")
    sb.run("nexus", "ls")


@case("config/m4-json-bak-dir-zero")
def _(sb):
    # personas.json.bak a directory; the last persona removed.
    sb.personas("c1:claude:docker")
    os.mkdir(sb.path("a/personas.json.bak"))
    sb.run("config", *YES, "--remove", "c1", "--persona", "g1:grok:docker")
    sb.run("config", *YES, "--remove", "g1")
    sb.run(tool(sb, "ls"), "-A", "a/personas.json.bak", cwd=".", label="ls -A personas.json.bak  (still a directory: empty)")


@case("config/m4-json-symlink")
def _(sb):
    sb.personas("c1:claude:docker")
    os.makedirs(sb.path("shared"))
    shutil.move(sb.path("a/personas.json"), sb.path("shared/personas.json"))
    sb.symlink("a/personas.json", sb.path("shared/personas.json"))
    sb.run("config", *YES, "--persona", "c2:claude:docker")
    sb.run(tool(sb, "readlink"), "a/personas.json", cwd=".", label="readlink a/personas.json")


@case("config/m4-crash-window")
def _(sb):
    # The state a kill (or ENOSPC in copy2) between personas.json's rename and the compose write leaves:
    # personas.json new, compose.personas.yml still the old one with the lines, no .bak yet.
    two_personas(sb)
    before_personas_json(sb)
    with open(sb.path("a/compose.personas.yml"), "rb") as f:
        old = f.read()
    sb.setup("config", *YES)
    with open(sb.path("a/compose.personas.yml"), "wb") as f:
        f.write(old)
    os.unlink(sb.path("a/compose.personas.yml.bak"))
    sb.run("nexus", "c1", "start", cwd="projects", label="nexus c1 start  (half-moved)")
    sb.run("config", *YES, label="config --yes  (half-moved)")
    sb.run(tool(sb, "grep"), "-c", "^# persona:", "-r", ".", "--include=compose.personas.yml*", cwd="a",
           label="grep -c persona lines in compose.personas.yml*")


@case("config/m4-menu-unmigrated")
def _(sb):
    # The menu on a checkout not yet moved: remove c1, then ^C at the VM settings: nothing written.
    two_personas(sb)
    before_personas_json(sb)
    sb.run("config", *NO_LINK, mode="tty-in",
           steps=[("choice [done]: ", "remove"), ("choice [-]: ", "c1"), ("choice [done]: ", "done"),
                  ("Keep them? ", "<INT>")], label="config  (menu: remove c1, ^C)")
    sb.run(tool(sb, "ls"), "a", cwd=".", label="ls a")


@case("config/m4-zero-not-migrating-note")
def _(sb):
    # Migration with --yes and a persona whose env file is missing: env file written; the move note once.
    two_personas(sb)
    before_personas_json(sb)
    sb.write("a/compose.personas.yml.bak", "# older\n")
    sb.write("a/compose.personas.yml.bak.2", "# older 2\n")
    sb.run("config", *YES)
    sb.run(tool(sb, "ls"), "a", cwd=".", label="ls a")


@case("config/m4-migrate-check-fails")
def _(sb):
    from scen.config_cli import world
    two_personas(sb)
    before_personas_json(sb)
    world(sb, compose={"config_rc": 15, "config_out": "invalid compose project"})
    sb.run("config", *YES)
    sb.run(tool(sb, "ls"), "-a", "a", cwd=".", label="ls -a a")


@case("config/m4-migrate-readonly")
def _(sb):
    two_personas(sb)
    before_personas_json(sb)
    os.chmod(sb.a, 0o555)
    try:
        sb.run("config", *YES)
        sb.run(tool(sb, "ls"), "-a", "a", cwd=".", label="ls -a a")
    finally:
        os.chmod(sb.a, 0o755)


@case("config/m4-json-deleted")
def _(sb):
    # personas.json gone (deleted by hand), the generated compose file still there.
    two_personas(sb)
    os.unlink(sb.path("a/personas.json"))
    sb.run("nexus", "ls")
    sb.run("config", "--list")
    sb.run("config", *YES)
    sb.run("nexus", "ls", label="nexus ls  (after --yes)")


@case("config/m4-hand-edit-rewritten")
def _(sb):
    # A service edited by hand in the generated file (nexus warned at an attach): --yes writes it again from
    # personas.json and tells that persona to restart for it, and only that one.
    sb.personas("c1:claude:docker", "c2:claude:docker")
    edit(sb, "a/compose.personas.yml", lambda t: t.replace("      - c1-home:/home/dev\n",
                                                           "      - c1-home:/home/dev\n      - /srv/data:/data\n", 1))
    sb.run("config", *YES)
    sb.run("config", *YES, label="config --yes --no-link --no-build  (again: nothing to tell)")
