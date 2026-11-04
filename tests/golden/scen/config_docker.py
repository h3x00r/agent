"""nexus --config and the docker work: the shared scratch root it makes (by mkdir, or with sudo), the settings only a
hand edit sets (NEXUS_SCRATCH_ROOT, DOCKER_GUARD), each persona's Nexus notes (personas/notes.md filled in) and the
[paths] table that has grok read them, the port ranges (--ports, the fill, overlaps refused), the note an older
checkout's personas get after a pull, and what --remove leaves."""
import glob
import json
import os
import re

from scen import case, two_personas
from scen.config_cli import YES, grep
from scen.config_interactive import MENU, claude_env

GROK_PATHS = '\n# The Nexus notes, /etc/grok/rules/00-nexus.md: nexus --config adds this table.\n[paths]\n' \
             'extra_rule_dirs = ["/etc/grok/rules"]\n'


def older_checkout(sb):
    """The checkout as an older nexus --config left it, then a git pull: compose.personas.yml without each service's
    scratch dir, port range and notes mount, personas.json without the ranges, the grok configs without [paths], no
    notes files. Taken out by their text, so this doesn't lean on the code under test."""
    p = sb.path("a/compose.personas.yml")
    with open(p) as f:
        lines = re.sub(r', NEXUS_(SCRATCH|PORTS): "[^"]*"', "", f.read()).split("\n")
    out, i = [], 0
    while i < len(lines):
        if lines[i].startswith(("      # The shared scratch dir (NEXUS_SCRATCH)", "      # The Nexus notes (")):
            i += 3                       # the block's two comment lines and its mount
            continue
        out.append(lines[i])
        i += 1
    text = "\n".join(out)
    if "NEXUS_" in text or "notes.md" in text:
        raise RuntimeError("older_checkout: compose.personas.yml still has the docker work's lines")
    with open(p, "w") as f:
        f.write(text)
    p = sb.path("a/personas.json")
    with open(p) as f:
        data = json.load(f)
    for rec in data["personas"].values():
        rec.pop("ports", None)
    with open(p, "w") as f:
        f.write(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    for g in glob.glob(sb.path("a/personas/*.grok.toml")):
        with open(g) as f:
            text = f.read()
        if not text.endswith(GROK_PATHS):
            raise RuntimeError("older_checkout: %s doesn't end in the [paths] table" % g)
        with open(g, "w") as f:
            f.write(text[:-len(GROK_PATHS)])
    for n in glob.glob(sb.path("a/personas/*.notes.md")):
        os.unlink(n)


def append(sb, rel, text):
    with open(sb.path(rel), "a") as f:
        f.write(text)


def tty(sb, steps, *args, env=None, label=None):
    return sb.run("config", *(args or ("--no-link", "--no-build")), mode="tty-in", steps=steps, env=env, label=label)


# --- the shared scratch root -------------------------------------------------------------------------------------
@case("config/scratch-root-made")
def _(sb):
    # Not there: made once, by mkdir -p (mode 755), as the parent is yours; there: left as it is. In the shell, else in
    # .env; each persona's notes name the dir under it.
    sb.personas("c1:claude:docker")
    sb.run("config", *YES, env={"NEXUS_SCRATCH_ROOT": sb.path("fresh/scratch") + "/"},
           label="config --yes  (NEXUS_SCRATCH_ROOT=<S>/fresh/scratch/ in the shell, not there)")
    sb.run("config", *YES, env={"NEXUS_SCRATCH_ROOT": sb.path("fresh/scratch")},
           label="config --yes  (the same, there now)")
    append(sb, "a/.env", "NEXUS_SCRATCH_ROOT=%s\n" % sb.path("dotenv/scratch"))
    sb.run("config", *YES, env={"NEXUS_SCRATCH_ROOT": None},
           label="config --yes  (no NEXUS_SCRATCH_ROOT in the shell, <S>/dotenv/scratch in .env)")
    grep(sb, "scratch dir", "personas/c1.notes.md")
    # No persona: nothing needs it, nothing is made.
    sb.run("config", *YES, "--remove", "c1", env={"NEXUS_SCRATCH_ROOT": sb.path("unneeded")},
           label="config --yes --remove c1  (NEXUS_SCRATCH_ROOT=<S>/unneeded: the last persona gone)")


@case("config/scratch-root-sudo")
def _(sb):
    # Its parent isn't yours (here: mode 555, left so: nothing is made in it): with sudo, as yours; under --yes
    # `sudo -n`, which never waits for a password. The fake sudo wants one, so it fails: a note with the command (and
    # the way without sudo), and the personas are saved all the same. Without --yes (options saved first, then the
    # menu): plain sudo.
    sb.personas("c1:claude:docker")
    os.makedirs(sb.path("ro"))
    os.chmod(sb.path("ro"), 0o555)
    sb.run("config", *YES, "--persona", "c2:claude:docker", env={"NEXUS_SCRATCH_ROOT": sb.path("ro/scratch")},
           label="config --yes --persona c2:claude:docker  (NEXUS_SCRATCH_ROOT=<S>/ro/scratch, <S>/ro mode 555)")
    tty(sb, claude_env() + [(MENU, "")], "--no-link", "--no-build", "--persona", "c3:claude:docker",
        env={"NEXUS_SCRATCH_ROOT": sb.path("ro/scratch")},
        label="config --no-link --no-build --persona c3:claude:docker  (the same; then quit)")
    # A file in its place: a note to move it aside, and no sudo (install -d would fail on it). One there that you
    # can't write in, with the personas' dirs still to make: a note with the command that makes it yours, no sudo.
    sb.write("afile", "a file, not a directory\n")
    sb.run("config", *YES, env={"NEXUS_SCRATCH_ROOT": sb.path("afile")},
           label="config --yes  (NEXUS_SCRATCH_ROOT=<S>/afile, a file)")
    sb.run("config", *YES, env={"NEXUS_SCRATCH_ROOT": sb.path("ro")},
           label="config --yes  (NEXUS_SCRATCH_ROOT=<S>/ro, there, mode 555)")


@case("config/vm-settings-refused")
def _(sb):
    # NEXUS_SCRATCH_ROOT and DOCKER_GUARD: nexus --config never writes them; a value their rules refuse, in the shell
    # or in .env, stops the run before anything is written. DOCKER_GUARD=off reaches the notes.
    sb.personas("c1:claude:docker", "g1:grok:docker")
    for v in ("rel/dir", "/", "/srv/a:b", "/srv/a b"):
        sb.run("config", *YES, env={"NEXUS_SCRATCH_ROOT": v}, label="config --yes  (NEXUS_SCRATCH_ROOT=%s in the shell)" % v)
    sb.run("config", *YES, env={"DOCKER_GUARD": "OFF"}, label="config --yes  (DOCKER_GUARD=OFF in the shell)")
    with open(sb.path("a/.env")) as f:
        env = f.read()
    append(sb, "a/.env", "NEXUS_SCRATCH_ROOT=srv/scratch\n")
    sb.run("config", *YES, label="config --yes  (NEXUS_SCRATCH_ROOT=srv/scratch in .env)")
    sb.write("a/.env", env)
    append(sb, "a/.env", "DOCKER_GUARD=maybe\n")
    sb.run("config", *YES, label="config --yes  (DOCKER_GUARD=maybe in .env)")
    sb.write("a/.env", env)
    append(sb, "a/.env", "DOCKER_GUARD=off\n")
    sb.run("config", *YES, label="config --yes  (DOCKER_GUARD=off in .env)")
    grep(sb, "guard is", "personas/g1.notes.md")
    sb.run("config", *YES, env={"DOCKER_GUARD": "on"}, label="config --yes  (DOCKER_GUARD=on in the shell beats .env's)")
    grep(sb, "guard is", "personas/g1.notes.md")


# --- the notes --------------------------------------------------------------------------------------------------
@case("config/notes-template")
def _(sb):
    # personas/notes.md is the template: edited, every persona's notes follow, in place (a running container's
    # single-file mount sees them); a placeholder nexus doesn't fill in, or the template gone: nothing written.
    sb.personas("c1:claude:docker", "g1:grok:docker")
    with open(sb.path("a/personas/notes.md")) as f:
        template = f.read()
    append(sb, "a/personas/notes.md", "\nAsk {{owner}} before {{persona}} deletes anything.\n")
    sb.run("config", *YES, label="config --yes  (personas/notes.md with {{owner}})")
    sb.write("a/personas/notes.md", template + "\nAsk the user before {{persona}} ({{cli}}) deletes anything.\n")
    sb.run("config", *YES, label="config --yes  (personas/notes.md with a line added)")
    grep(sb, "^Ask", "personas/c1.notes.md")
    grep(sb, "^Ask", "personas/g1.notes.md")
    os.unlink(sb.path("a/personas/notes.md"))
    sb.run("config", *YES, label="config --yes  (personas/notes.md gone)")
    sb.write("a/personas/notes.md", template)


@case("config/notes-grok-paths")
def _(sb):
    # The [paths] table that has grok read /etc/grok/rules: added to each grok persona's config, unless
    # personas/grok.toml has a [paths] of its own (a second would be invalid TOML): then none, and a warning unless
    # its extra_rule_dirs lists /etc/grok/rules.
    sb.personas("g1:grok:docker", "c1:claude:docker")
    with open(sb.path("a/personas/grok.toml")) as f:
        base = f.read()
    sb.write("a/personas/grok.toml", base + '\n[paths]\nextra_rule_dirs = ["/home/dev/rules"]\n')
    sb.run("config", *YES, label="config --yes  (personas/grok.toml with [paths] extra_rule_dirs = [\"/home/dev/rules\"])")
    grep(sb, "paths|rule", "personas/g1.grok.toml")
    sb.write("a/personas/grok.toml", base + "\n[paths]\nextra_rule_dirs = ['/home/dev/rules', '/etc/grok/rules']\n")
    sb.run("config", *YES, label="config --yes  (its extra_rule_dirs lists '/etc/grok/rules' too)")
    sb.write("a/personas/grok.toml", base)
    sb.run("config", *YES, label="config --yes  (personas/grok.toml as committed: the table nexus --config adds)")
    grep(sb, "paths|rule", "personas/g1.grok.toml")


# --- port ranges --------------------------------------------------------------------------------------------------
@case("config/ports-flags")
def _(sb):
    # --ports NAME:FROM-TO: a range of its own (a changed one is a restart: NEXUS_PORTS is in its environment), two can
    # swap; one that overlaps another's, a persona that isn't there, or a range its rules refuse: nothing written.
    two_personas(sb)
    sb.run("config", "--list")
    sb.run("config", *YES, "--ports", "c1:30000-30099")
    grep(sb, "Yours are", "personas/c1.notes.md")
    sb.run("config", *YES, "--ports", "c1:20100-20199")
    sb.run("config", *YES, "--ports", "c1:20100-20199", "--ports", "g1:30000-30049")
    sb.run("config", *YES, "--ports", "zz:40000-40099")
    for spec in ("c1:020000-020099", "c1:1000-1099", "c1:20099-20000", "c1:65500-65600", "c1:20000", "c1:a-b", "c1"):
        sb.run("config", *YES, "--ports", spec)
    # A new persona: the block after the highest in use, from 20000 in blocks of 100; or the one --ports gives it.
    sb.run("config", *YES, "--persona", "n1:claude:docker")
    sb.run("config", *YES, "--persona", "n2:claude:docker", "--ports", "n2:25000-25099")
    sb.run("config", "--list")


@case("config/ports-json-by-hand")
def _(sb):
    # personas.json edited by hand: ranges that overlap, or one its rules refuse, are refused by both tools when they
    # read it, naming the persona; one taken out is given again by the next writing run (the block after the highest).
    two_personas(sb)
    p = sb.path("a/personas.json")
    with open(p) as f:
        good = f.read()
    sb.write("a/personas.json", good.replace('"20100-20199"', '"20050-20149"'))
    sb.run("config", "--list", label="config --list  (g1's ports 20050-20149 by hand: c1 has 20000-20099)")
    sb.run("nexus", "g1", "start", label="nexus g1 start  (the same)")
    sb.write("a/personas.json", good.replace('"20100-20199"', '"020100-020199"'))
    sb.run("config", "--list", label="config --list  (g1's ports 020100-020199 by hand)")
    sb.run("nexus", "ls", label="nexus ls  (the same)")
    sb.write("a/personas.json", good.replace('"20000-20099"', '"20200-20299"'))
    sb.run("config", *YES, label="config --yes  (c1's ports 20200-20299 by hand: changed)")
    data = json.loads(good)
    del data["personas"]["c1"]["ports"]
    sb.write("a/personas.json", json.dumps(data, indent=2) + "\n")
    sb.run("config", "--list", label="config --list  (c1's ports taken out by hand)")
    sb.run("config", *YES, label="config --yes  (the same: c1 gets the block after g1's)")


@case("config/ports-older-checkout")
def _(sb):
    # A checkout an older nexus --config set up, after a git pull: its compose file is out of step, so a start is
    # refused; the next nexus --config --yes gives each persona its range and notes, and the restart note says why
    # (not the hand-edit one; no "grok settings changed" for the [paths] table it adds to g1's); then nothing to say.
    sb.personas("c1:claude:docker", "g1:grok:docker", "c2:claude:docker", extra=("--ssh", "g1:new"))
    older_checkout(sb)
    sb.run("nexus", "c1", "start", label="nexus c1 start  (an older checkout's files)")
    sb.run("config", "--list", label="config --list  (no ranges yet)")
    sb.run("config", *YES)
    sb.run("config", *YES, label="config --yes  (again: nothing to say)")
    sb.run("nexus", "c1", "start")


@case("config/ports-older-checkout-menu")
def _(sb):
    # The same without --yes: the menu brings the files up to date before it is shown, with the same notes.
    two_personas(sb)
    older_checkout(sb)
    tty(sb, [(MENU, "")], label="config --no-link --no-build  (an older checkout's files; then quit)")
    tty(sb, [(MENU, "")], label="config --no-link --no-build  (again: up to date)")


# --- --remove ----------------------------------------------------------------------------------------------------
@case("config/remove-scratch-notes")
def _(sb):
    # A removed persona's notes and scratch dir stay, as its env file does: named, and in the command that deletes
    # them, when they are there (g1 never started: no scratch dir).
    two_personas(sb)
    sb.setup("nexus", "c1", "start")
    sb.run("config", *YES, "--remove", "c1")
    sb.run("config", *YES, "--remove", "g1")
