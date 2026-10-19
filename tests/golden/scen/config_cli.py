"""The setup tool driven by flags: --help, --list, --yes and its refusals, the files it writes."""
import os

from scen import NO_LINK, before_personas_json, case, images, snapshot_line, two_personas  # noqa: F401

YES = ("--yes",) + NO_LINK


def keys(sb):
    """Keys to give with --ssh, in <S>/keys: one that works (ok), one with a passphrase (pass), one others
    can read (open), a symlink to ok (link), a file that isn't a key (junk) and a directory (dir)."""
    kg = os.path.join(sb.bin, "ssh-keygen")
    os.makedirs(sb.path("keys/dir"))
    sb.setup(kg, "-q", "-t", "ed25519", "-N", "", "-C", "nxt-ok", "-f", sb.path("keys/ok"))
    sb.setup(kg, "-q", "-t", "ed25519", "-N", "nxt-fake-pass", "-C", "nxt-pass", "-f", sb.path("keys/pass"))
    with open(sb.path("keys/ok"), "rb") as f:
        sb.write("keys/open", f.read(), 0o644)
    sb.symlink("keys/link", sb.path("keys/ok"))
    sb.write("keys/junk", "not a key\n", 0o600)


def grep(sb, pattern, rel, label=None):
    """A recorded look at some lines of a file in the checkout, between two runs of the tool."""
    return sb.run(os.path.join(sb.bin, "grep"), "-E", pattern, rel, cwd="a", label=label or "grep %s %s" % (pattern, rel))


def env_lines(sb, *names):
    return grep(sb, "^(%s)=" % "|".join(names), ".env")


def persona_lines(sb):
    """The personas as `# persona:` lines, from personas.json or the compose file (golden personas_view.py)."""
    return sb.run(os.path.join(sb.bin, "personas-view"), cwd="a", label="grep ^# persona:  compose.personas.yml")


def world(sb, **kw):
    sb.load_world()
    sb.world.update(kw)
    sb.save_world()


def drop_tool(sb, name):
    os.unlink(os.path.join(sb.bin, name))


@case("config/cli-help")
def _(sb):
    sb.run("config", "--help")


@case("config/cli-unknown-option")
def _(sb):
    sb.run("config", "--bogus")
    sb.run("config", "--help", "--bogus")


@case("config/cli-list-none")
def _(sb):
    sb.run("config", "--list")


@case("config/cli-yes-first-setup")
def _(sb):
    sb.run("config", "--yes", *NO_LINK, "--projects-root", sb.projects, "--registry", "local",
           "--persona", "c1:claude:docker", "--persona", "g1:grok:docker", "--ssh", "g1:new")


@case("config/cli-list-some")
def _(sb):
    two_personas(sb)
    sb.run("config", "--list")


# --- refused before anything runs: no lock, no docker call, nothing written ------------------------
@case("config/cli-bad-persona-name")
def _(sb):
    for n in ("ls", "top", "all", "start", "Nxt", "c1-", "1c", "c_1", "", "c" + "1" * 31):
        sb.run("config", *YES, "--persona", n + ":claude:docker")
    # The second of two is checked too, and -y is --yes.
    sb.run("config", "-y", "--no-link", "--persona", "c1:claude:docker", "--persona", "top:grok:docker")


@case("config/cli-bad-persona-spec")
def _(sb):
    for spec in ("c1", "c1:claude", "c1:claude:", "c1:Claude:docker", "c1:gpt:docker", "c1:claude:k8s",
                 "c1:grok:ddev:rel/dir", "c1:claude:docker:/", "c1:claude:docker://", "c1:claude:docker:/a:b",
                 "c1:claude:docker:/a b", "c1:claude:docker:" + sb.path("x::"), "c1:claude:docker:::"):
        sb.run("config", *YES, "--persona", spec)


@case("config/cli-bad-ssh")
def _(sb):
    keys(sb)
    for k in ("c1", "c1:", ":new", "Bad:new", "c1:new:", "c1:rel/key", "c1:personas/c1.key",
              "c1:personas/g1.key", "c1:" + sb.path("keys/my key"), "c1:" + sb.path("keys/k$x"),
              "c1:" + sb.path("keys/k'x"), "c1:" + sb.path("keys/open"), "c1:" + sb.path("keys/pass"),
              "c1:" + sb.path("keys/junk"), "c1:" + sb.path("keys/none"), "c1:" + sb.path("keys/dir"),
              "c1:~/none", "c1:/etc/passwd"):
        sb.run("config", *YES, "--persona", "c1:claude:docker", "--ssh", k)
    # A persona that isn't there: only found after the preflight, so docker and ssh-add are asked first.
    sb.run("config", *YES, "--persona", "c1:claude:docker", "--ssh", "x1:agent")


@case("config/cli-need-argument")
def _(sb):
    for flag in ("--persona", "--ssh", "--remove", "--projects-root", "--registry", "--claude-version", "--link"):
        sb.run("config", "--yes", flag)
    sb.run("config", "--yes", "--registry", "", "--no-link")
    sb.run("config", "--yes", "--persona", "--no-link")   # the next flag is taken as the spec


@case("config/cli-bad-values")
def _(sb):
    for args in (("--projects-root", "rel/dir"), ("--projects-root", "/"), ("--projects-root", "//"),
                 ("--projects-root", "/a:b"), ("--registry", "Reg.lan/Team"), ("--registry", "/"),
                 ("--registry", "reg lan"), ("--registry", "reg.lan:port"), ("--registry", "-reg.lan"),
                 ("--claude-version", "v2.1.3"), ("--claude-version", "2.1"), ("--claude-version", "beta"),
                 ("--claude-version", "2.1.3-"), ("--link", "rel/bin")):
        sb.run("config", "--yes", "--no-build", *args)


# --- tools missing, docker refusing ---------------------------------------------------------------
@case("config/cli-no-tools")
def _(sb):
    keys(sb)
    sb.personas("c1:claude:docker")
    drop_tool(sb, "ssh-keygen")
    sb.run("config", *YES, "--ssh", "c1:new")
    sb.run("config", *YES, "--ssh", "c1:" + sb.path("keys/ok"))
    sb.run("config", *YES, "--ssh", "c1:none")   # needs no ssh-keygen
    drop_tool(sb, "docker")
    sb.run("config", *YES)


@case("config/cli-root-uncreatable")
def _(sb):
    sb.personas("c1:claude:docker")
    sb.write("blocker", "a file, not a directory\n", 0o644)
    # Both are found after the preflight, when the root is made.
    sb.run("config", *YES, "--persona", "c1:claude:docker:" + sb.path("blocker/sub"))
    sb.run("config", *YES, "--projects-root", sb.path("blocker/sub"))


@case("config/cli-ssh-keys")
def _(sb):
    sb.personas("g1:grok:docker", "c1:claude:docker", extra=("--ssh", "g1:new"))
    os.makedirs(sb.path("home/keys"))
    sb.setup(os.path.join(sb.bin, "ssh-keygen"), "-q", "-t", "ed25519", "-N", "", "-C", "nxt-c1", "-f",
             sb.path("home/keys/c1"))
    sb.run("config", *YES, "--ssh", "g1:new")   # there already: kept, not made again
    os.unlink(sb.path("a/personas/g1.key"))
    os.unlink(sb.path("a/personas/g1.key.pub"))
    # Made anew for a persona that exists, which then needs a restart; c1's given by a ~ path.
    sb.run("config", *YES, "--ssh", "g1:new", "--ssh", "c1:~/keys/c1")
    persona_lines(sb)
    grep(sb, "source: '|50-nexus", "compose.personas.yml")
    # ssh-keygen can't write it: a line in the summary, not the end of the run.
    sb.symlink("a/personas/x1.key", sb.path("nowhere/x1.key"))   # a dangling link where x1's key goes
    sb.run("config", *YES, "--persona", "x1:claude:docker", "--ssh", "x1:new")
    os.unlink(sb.path("a/personas/x1.key"))


@case("config/cli-preflight")
def _(sb):
    sb.personas("c1:claude:docker")
    world(sb, info_rc=1)
    sb.run("config", *YES)
    sb.run("config", "--list")   # no docker needed for that
    world(sb, info_rc=0, ssh_add_rc=0)
    sb.run("config", *YES)
    world(sb, ssh_add_rc=2)
    sb.run("config", *YES)
    # compose refuses the result: nothing written, but the persona's own root is made as it is named.
    world(sb, ssh_add_rc=1, compose={"config_rc": 15, "config_out":
                                     'service "d1" refers to undefined volume d1-home: invalid compose project'})
    sb.run("config", *YES, "--persona", "d1:claude:docker:" + sb.path("d1-root"))
    persona_lines(sb)


# --- values that are taken ------------------------------------------------------------------------
@case("config/cli-vm-settings")
def _(sb):
    sb.personas("c1:claude:docker")
    sb.run("config", *YES, "--projects-root", "~/p2", "--registry", "registry.lan:5000/team/",
           "--claude-version", "2.1.302-beta.1")
    env_lines(sb, "PROJECTS_ROOT", "REGISTRY", "CLAUDE_VERSION")
    sb.run("config", *YES, "--projects-root", sb.path("p3") + "/", "--registry", "local/", "--claude-version", "stable")
    env_lines(sb, "PROJECTS_ROOT", "REGISTRY", "CLAUDE_VERSION")
    # The VM's root may have a space (a persona's own may not).
    sb.run("config", *YES, "--projects-root", sb.path("p 4"), "--registry", "Reg-1.lan:5000/te_am/x--y")
    env_lines(sb, "PROJECTS_ROOT", "REGISTRY")
    # No Claude persona left: the version is written all the same, for the first one.
    sb.run("config", *YES, "--remove", "c1", "--persona", "g1:grok:docker", "--claude-version", "2.1.5")
    env_lines(sb, "PROJECTS_ROOT", "REGISTRY", "CLAUDE_VERSION", "GROK_VERSION")


@case("config/cli-env-legacy")
def _(sb):
    os.unlink(os.path.join(sb.home, ".gitconfig"))   # no git identity to copy
    sb.write("a/.env", "PROJECTS_ROOT=%s\nGROK_TAG=latest\nAGENT_CPUS=99\nexport REGISTRY = 'local'  # here\n"
             % sb.projects, 0o640)
    sb.run("config", *YES, "--persona", "c1:claude:docker", "--persona", "g1:grok:docker")
    sb.run(os.path.join(sb.bin, "cat"), ".env", cwd="a", label="cat .env")
    sb.write("a/.env", "PROJECTS_ROOT=%s\nGROK_TAG=1.2.3\nAGENT_CPUS=.5\n" % sb.projects, 0o640)
    sb.run("config", *YES)
    env_lines(sb, "PROJECTS_ROOT", "GROK_TAG", "AGENT_CPUS", "REGISTRY", "CLAUDE_VERSION")


@case("config/cli-statusline")
def _(sb):
    sb.run("config", "--yes", "--no-link", "--statusline")   # nothing to apply it to, no personas, nothing to build
    sb.run("config", *YES, "--no-statusline", "--persona", "g1:grok:docker", "--persona", "c1:claude:docker")
    sb.run("config", *YES, "--statusline", "--persona", "g1:grok:docker")
    persona_lines(sb)
    grep(sb, "status_line|statusline", "personas/g1.grok.toml")
    grep(sb, "statusline/", "compose.personas.yml")


# --- re-runs ---------------------------------------------------------------------------------------
@case("config/cli-rerun-unchanged")
def _(sb):
    keys(sb)
    sb.personas("c1:claude:docker:" + sb.path("own"), "g1:grok:docker", "x1:grok:docker", "d1:claude:docker",
                extra=("--ssh", "g1:new", "--ssh", "x1:none", "--ssh", "d1:" + sb.path("keys/link")))
    # Nothing changes, so nothing is rewritten: tree.txt stays empty.
    sb.run("config", *YES)
    sb.run("config", *YES, "--persona", "c1:claude:docker", "--ssh", "g1:new", "--ssh", "d1:" + sb.path("keys/link"))


@case("config/cli-persona-regiven")
def _(sb):
    sb.personas("c1:claude:docker:" + sb.path("own"), "g1:grok:docker", extra=("--ssh", "c1:none"))
    persona_lines(sb)
    sb.run("config", *YES, "--persona", "c1:grok:docker:" + sb.path("own"), "--persona", "c1:claude:docker")
    persona_lines(sb)   # the last --persona wins; colour, root and ssh kept
    sb.run("config", *YES, "--persona", "c1:claude:docker:" + sb.path("own2") + "/")
    persona_lines(sb)
    sb.run("config", *YES, "--persona", "c1:claude:docker:")
    persona_lines(sb)
    sb.run("config", *YES, "--persona", "c1:claude:docker:" + sb.path("own3") + ":")   # the last ':' dropped
    persona_lines(sb)
    sb.run("config", *YES, "--persona", "c1:claude:docker::")   # clears it too
    persona_lines(sb)
    sb.run("config", *YES, "--persona", "c1:claude:docker:~/own4", "--ssh", "c1:agent")
    persona_lines(sb)


@case("config/cli-colours")
def _(sb):
    sb.personas(*["c%d:claude:docker" % i for i in range(1, 9)])   # all eight tmux colours taken
    persona_lines(sb)
    # None free: they come round again, by the number of personas so far.
    sb.run("config", *YES, "--persona", "c9:claude:docker", "--persona", "d1:grok:docker")
    persona_lines(sb)
    # One freed: a new persona takes it.
    sb.run("config", *YES, "--remove", "c3", "--persona", "x1:claude:docker")
    persona_lines(sb)


@case("config/cli-flavour-change")
def _(sb):
    sb.personas("c1:claude:docker")
    sb.run("config", *YES, "--persona", "c1:grok:docker")
    sb.run("config", *YES, "--persona", "c1:claude:docker")
    persona_lines(sb)


@case("config/cli-foreign-env")
def _(sb):
    sb.write("a/personas/d1.env", "# left from an older layout\nFOO=1\n", 0o600)
    sb.write("a/personas/x1.env", "XAI_API_KEY=nxt-fake-key-1\n", 0o600)
    sb.write("a/personas/c1.env", "  export ANTHROPIC_API_KEY = nxt-fake-key-2\n", 0o600)
    sb.write("a/personas/c1.env.bak", "an older backup\n", 0o600)
    sb.run("config", *YES, "--persona", "d1:grok:docker")
    sb.run("config", *YES, "--persona", "x1:grok:docker")
    sb.run("config", *YES, "--persona", "c1:grok:docker")


@case("config/cli-grok-toml")
def _(sb):
    sb.personas("g1:grok:docker")
    sb.setup("config", *YES, "--persona", "g2:grok:docker", "--no-statusline")
    # Set up before config wrote their managed config: written now, and a restart brings it in.
    os.unlink(sb.path("a/personas/g1.grok.toml"))
    os.unlink(sb.path("a/personas/g2.grok.toml"))
    sb.run("config", *YES)
    with open(sb.path("a/personas/grok.toml")) as f:
        base = f.read()
    sb.write("a/personas/grok.toml", base + "\n[nxt]\nmark = 1\n")
    sb.run("config", *YES)   # changed: the next grok reads it
    sb.run("config", *YES)   # unchanged: nothing said
    # A status line of its own in personas/grok.toml stands; the statusline's isn't added.
    sb.write("a/personas/grok.toml", base + "\n[ ui . status_line ]\ntype = \"builtin\"\n")
    sb.run("config", *YES)
    grep(sb, "status_line|statusline|builtin", "personas/g1.grok.toml")
    # Changed persona and changed settings at once: only the restart is asked for.
    sb.write("a/personas/grok.toml", base)
    sb.run("config", *YES, "--persona", "g1:grok:docker", "--no-statusline")


@case("config/cli-managed-json")
def _(sb):
    sb.personas("c1:claude:docker", "g1:grok:docker")
    grep(sb, "15-personas", "compose.personas.yml")
    os.rename(sb.path("a/personas/claude.managed.json"), sb.path("managed.json.aside"))
    sb.run("config", *YES)   # no drop-in, and nothing to say about it
    grep(sb, "15-personas", "compose.personas.yml")
    os.rename(sb.path("managed.json.aside"), sb.path("a/personas/claude.managed.json"))
    sb.run("config", *YES)
    grep(sb, "15-personas", "compose.personas.yml")


@case("config/cli-settings-json-broken")
def _(sb):
    sb.personas("c1:claude:docker")
    with open(sb.path("a/personas/claude.json")) as f:
        defaults = f.read()
    with open(sb.path("a/personas/claude.managed.json")) as f:
        managed = f.read()
    sb.run("config", "--list")   # the tree is compared from here: both files are put back at the end
    sb.write("a/personas/claude.json", '{"model": }\n')
    sb.run("config", *YES)
    sb.write("a/personas/claude.json", defaults)
    sb.write("a/personas/claude.managed.json", b'{"env": {"A": "\xff"}}\n')
    sb.run("config", *YES)
    sb.write("a/personas/claude.managed.json", b'[1, 2')
    sb.write("a/personas/claude.json", b'{"model": "opus"} {}')
    sb.run("config", *YES)   # personas/claude.json is checked first
    # Only a Claude persona needs them checked.
    sb.run("config", *YES, "--remove", "c1", "--persona", "g1:grok:docker")
    sb.write("a/personas/claude.json", defaults)
    sb.write("a/personas/claude.managed.json", managed)


@case("config/cli-remove")
def _(sb):
    two_personas(sb)
    sb.run("config", *YES, "--remove", "x1")
    # Removed and given again: a new place at the end of the list, and a colour of its own again.
    sb.run("config", *YES, "--remove", "c1", "--persona", "c1:claude:docker")
    persona_lines(sb)
    sb.run("config", *YES, "--remove", "g1")
    sb.run("config", *YES, "--remove", "c1")   # the last one: the personas file goes to .bak


# --- --list and the lock ------------------------------------------------------------------------
@case("config/cli-list-detail")
def _(sb):
    keys(sb)
    long_name = "c" + "1234567890" * 3
    sb.personas("c1:claude:docker:" + sb.path("own"), "g1:grok:docker", "x1:grok:ddev", long_name + ":claude:docker",
                extra=("--ssh", "g1:new", "--ssh", "x1:none", "--ssh", long_name + ":" + sb.path("keys/link"),
                       "--no-statusline"))
    sb.run("config", "--list")
    sb.run("config", "--list", "--yes", "--remove", "c1")   # --list wins, changes nothing
    sb.run("config", "--list")


@case("config/cli-list-bad-line")
def _(sb):
    sb.personas("c1:claude:docker")
    before_personas_json(sb)     # the lines config-tools reads once, to move them into personas.json
    with open(sb.path("a/compose.personas.yml")) as f:
        text = f.read()
    sb.write("a/compose.personas.yml", text.replace("statusline=yes", "statusline=maybe"))
    sb.run("config", "--list")
    sb.run("config", *YES)
    sb.write("a/compose.personas.yml", text.replace("# persona: c1 ", "# persona: c1 root=rel/dir "))
    sb.run("config", "--list")
    sb.write("a/compose.personas.yml", text.replace("# persona: c1 ", "# persona: c1 ssh=personas/g1.key "))
    sb.run("config", "--list")
    # CRLF line ends are read as if they weren't there.
    sb.write("a/compose.personas.yml", text.replace("\n", "\r\n"))
    sb.run("config", "--list")
    # Back to LF: a CR in tree.txt wouldn't survive verify's text-mode read of the golden.
    sb.write("a/compose.personas.yml", text)


@case("config/cli-lock")
def _(sb):
    sb.personas("c1:claude:docker")
    with sb.hold_lock():
        sb.run("config", *YES)
        sb.run("config", "--list")
        sb.run("config", *YES, "--persona", "Bad")   # flags are checked before the lock is taken
    sb.run("config", *YES)


# --- the launcher link --------------------------------------------------------------------------
@case("config/cli-link")
def _(sb):
    sb.personas("c1:claude:docker")
    lbin = sb.path("lbin")
    sb.script("other/nexus", "echo another nexus")
    sb.run("config", "--yes", "--no-build", "--link", lbin + "/")
    sb.run("config", "--yes", "--no-build", "--link", lbin)   # linked already: nothing to do
    sb.run("config", "--yes", "--no-build", "--link", lbin, env={"PATH": sb.bin + ":" + lbin})
    # The launcher's old name, linked into this checkout: removed.
    sb.symlink("lbin/agent", sb.path("a/agent"))
    sb.run("config", "--yes", "--no-build", "--link", lbin, env={"PATH": sb.path("other") + ":" + sb.bin + ":" + lbin})
    sb.run("config", "--yes", "--no-build", "--link", "~/bin")


@case("config/cli-link-refused")
def _(sb):
    sb.personas("c1:claude:docker")
    sb.script("lbin/nexus", "echo someone else's nexus")
    sb.write("blocker", "a file, not a directory\n", 0o644)
    os.makedirs(sb.path("ro"))
    # Not replaced under --yes.
    sb.run("config", "--yes", "--no-build", "--link", sb.path("lbin"))
    # Can't be made without sudo, and the fake sudo wants a password: --yes never waits for one (-n).
    sb.run("config", "--yes", "--no-build", "--link", sb.path("blocker/sub"))
    mode = os.stat(sb.path("ro")).st_mode & 0o7777
    os.chmod(sb.path("ro"), 0o555)
    try:
        sb.run("config", "--yes", "--no-build", "--link", sb.path("ro"))
    finally:
        os.chmod(sb.path("ro"), mode)   # as it was, so tree.txt doesn't show it and the sandbox can go


# --- images ------------------------------------------------------------------------------------
@case("config/cli-images-build")
def _(sb):
    sb.personas("c1:claude:docker", "c2:claude:docker", "g1:grok:docker")
    images(sb)
    sb.run("config", "--yes", "--no-link")
    # Low on disk: said, but under --yes it builds anyway.
    world(sb, free_gb=1)
    sb.run("config", "--yes", "--no-link")


@case("config/cli-images-fail")
def _(sb):
    two_personas(sb)
    images(sb)
    world(sb, compose={"build_rc": 2}, docker_root=sb.path("no-such-root"))
    sb.run("config", "--yes", "--no-link")


@case("config/cli-images-pull")
def _(sb):
    two_personas(sb)
    images(sb)
    sb.run("config", "--yes", "--no-link", "--registry", "registry.lan:5000")
    world(sb, compose={"pull_rc": 1}, free_gb=2)
    sb.run("config", "--yes", "--no-link")
    sb.run("config", "--yes", "--no-link", "--no-build", "--registry", "local")   # no images asked for


# --- ddev ---------------------------------------------------------------------------------------
@case("config/cli-ddev", fakes=("ddev", "mkcert"))
def _(sb):
    os.makedirs(sb.path("home/.local/share/mkcert"))
    sb.run("config", *YES, "--projects-root", sb.projects, "--persona", "d1:claude:ddev", "--persona", "c1:claude:docker")
    env_lines(sb, "DDEV_VERSION")
    sb.write("home/.local/share/mkcert/rootCA.pem", "fake CA\n", 0o644)
    world(sb, ddev_version="v1.25.0-rc.1")
    sb.run("config", *YES)
    env_lines(sb, "DDEV_VERSION")


@case("config/cli-ddev-missing")
def _(sb):
    sb.run("config", *YES, "--projects-root", sb.projects, "--persona", "d1:grok:ddev")
    env_lines(sb, "DDEV_VERSION")
    sb.run("config", *YES, "--remove", "d1", "--persona", "c1:claude:docker")   # no ddev persona: no warning
