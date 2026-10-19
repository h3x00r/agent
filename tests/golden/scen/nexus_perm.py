"""nexus and a claude persona's permissions: one that asks (personas/claude.ask.json mounted after
claude.managed.json), its file gone or not asking, and its container made before it asked."""
import os

from scen import case

ASK = "a/personas/claude.ask.json"


@case("nexus/perm-ask-file-missing")
def _(sb):
    # claude.ask.json gone (a committed file): the persona that asks is told to restore it, and isn't started
    # (left out, it would bypass); one that doesn't ask starts as ever.
    sb.personas("c1:claude:docker", "c2:claude:docker", extra=("--permissions", "c1:ask"))
    os.unlink(sb.path(ASK))
    sb.run("nexus", "c1", "start", label="nexus c1 start  (claude.ask.json gone)")
    sb.run("nexus", "c2", "start", label="nexus c2 start  (claude.ask.json gone, c2 bypasses)")
    sb.run("nexus", "c1", "restart", label="nexus c1 restart  (claude.ask.json gone)")
    # Both committed files gone: claude.managed.json is said first.
    managed = sb.path("a/personas/claude.managed.json")
    os.rename(managed, managed + ".aside")
    sb.run("nexus", "c1", "start", label="nexus c1 start  (claude.managed.json and claude.ask.json gone)")
    os.rename(managed + ".aside", managed)
    # There, but not making it ask: so it would bypass, and isn't started.
    for text, what in (("{}\n", "no mode"), ('{"permissions": {"defaultMode": "bypassPermissions"}}\n', "bypassPermissions"),
                       ("{ not json\n", "not JSON")):
        sb.write(ASK, text)
        sb.run("nexus", "c1", "start", label="nexus c1 start  (claude.ask.json: %s)" % what)


@case("nexus/perm-ask-not-restarted")
def _(sb):
    # Set to ask while running: until a restart its container (made without 16-ask.json) still bypasses, and a start
    # or an attach says so; after the restart, nothing to say.
    sb.personas("c1:claude:docker")
    sb.setup("nexus", "c1", "start")
    sb.setup("config", "--yes", "--no-link", "--no-build", "--permissions", "c1:ask")
    sb.run("nexus", "c1", "start", label="nexus c1 start  (running, made before it asked)")
    sb.write("projects/app/.keep", "")
    sb.run("nexus", "c1", cwd="projects/app", label="nexus c1  (an attach, made before it asked)")
    sb.run("nexus", "c1", "restart")
    sb.run("nexus", "c1", "start", label="nexus c1 start  (restarted: it has 16-ask.json)")


# --- the session's words: --skip-bypass-permissions, --bypass-permissions -------------------------------------
ASK_TARGET = "/etc/claude-code/managed-settings.d/16-ask.json"
SKIP, BYPASS = "--skip-bypass-permissions", "--bypass-permissions"


def session(name, mode=None):
    s = {"name": name, "windows": 1, "attached": 0, "path": "<P>"}
    if mode:
        s["permissions"] = mode
    return s


def two(sb, **c1):
    """c1 bypasses (as committed), c2 asks; both running: c1 with a session app and one started asking, c2 with app
    and one started bypassing; and g1, a grok one."""
    sb.personas("c1:claude:docker", "c2:claude:docker", "g1:grok:docker", extra=("--permissions", "c2:ask"))
    sb.write("projects/app/.keep", "")
    sb.write("projects/new/.keep", "")
    sb.container("c1", sessions=[session("main"), session("app"), session("asks", "ask"), session("unread", "?")], **c1)
    sb.container("c2", mounts=[ASK_TARGET], sessions=[session("main"), session("app"), session("bypasses", "bypass")])
    sb.container("g1")


@case("nexus/perm-flag-sessions")
def _(sb):
    two(sb)
    # A new session in the other mode: NEXUS_PERMISSIONS for it, wherever the word is before the --.
    sb.run("nexus", "c1", SKIP, "--", "claude", cwd="projects/new")
    sb.run("nexus", "c1", "s2", SKIP, "--", "claude", "--resume")
    sb.run("nexus", "c2", BYPASS, cwd="projects/new")
    # The persona's own mode: set all the same (what the probe makes of it is a guess).
    sb.run("nexus", "c1", BYPASS, "s3")
    sb.run("nexus", "c2", SKIP, "s4", "--", "claude")
    # A session there already: in the mode asked for, attached; in the other, refused.
    sb.run("nexus", "c1", SKIP, "asks")
    sb.run("nexus", "c2", BYPASS, "bypasses")
    sb.run("nexus", "c1", SKIP, cwd="projects/app")
    sb.run("nexus", "c1", BYPASS, "asks")
    sb.run("nexus", "c2", BYPASS, "app")
    sb.run("nexus", "c2", SKIP, "bypasses")
    sb.run("nexus", "c1", SKIP, "unread", label="nexus c1 --skip-bypass-permissions unread  (its mode can't be read)")
    # Without a word: as ever, whatever the session's mode.
    sb.run("nexus", "c1", "asks")


@case("nexus/perm-flag-main")
def _(sb):
    # main, which every persona has, runs in the persona's own mode.
    two(sb)
    sb.run("nexus", "c1", SKIP, "main")
    sb.run("nexus", "c1", BYPASS, "main")
    sb.run("nexus", "c2", SKIP, "main")
    sb.run("nexus", "c2", BYPASS, "main")


@case("nexus/perm-flag-old-image")
def _(sb):
    # An image from before claude-mode.sh can't run a session in the other mode: refused, not run in its own.
    two(sb, no_wrapper=True)
    sb.run("nexus", "c1", SKIP, "--", "claude", cwd="projects/new")
    sb.run("nexus", "c1", SKIP, "headless", "nxt hi", cwd="projects/app")
    sb.run("nexus", "c1", BYPASS, "s3", label="nexus c1 --bypass-permissions s3  (its own mode, too)")
    sb.run("nexus", "c1", "s3", label="nexus c1 s3  (no word: as ever)")
    sb.load_world()
    sb.world["containers"]["agent-c1"]["perm_probe_rc"] = 1
    sb.save_world()
    sb.run("nexus", "c1", SKIP, "s3", label="nexus c1 --skip-bypass-permissions s3  (the probe fails)")


@case("nexus/perm-flag-refused")
def _(sb):
    two(sb)
    sb.run("nexus", "c1", SKIP, BYPASS)
    sb.run("nexus", "c1", BYPASS, "s1", SKIP, "--", "claude")
    for sub in ("ls", "stop", "logs", "build", "pull", "start", "restart", "upgrade"):
        sb.run("nexus", "c1", SKIP, sub)
    sb.run("nexus", "c1", "start", BYPASS)
    sb.run("nexus", "g1", SKIP)
    sb.run("nexus", "g1", BYPASS, "headless", "nxt hi", cwd="projects/app")
    sb.run("nexus", "c1", SKIP, SKIP, "s5", label="nexus c1 --skip-bypass-permissions twice s5  (the same word: fine)")


@case("nexus/perm-dash-session-unchanged")
def _(sb):
    # Every other -x is a session name still; after the -- the words are the command's.
    two(sb)
    sb.run("nexus", "c1", "-s")
    sb.run("nexus", "c1", "--skip")
    sb.run("nexus", "c1", "s6", "--", "echo", SKIP)
    sb.run("nexus", "c1", SKIP, "s7", "--", "claude", BYPASS)


@case("nexus/perm-headless")
def _(sb):
    two(sb)
    sb.run("nexus", "c1", "headless", "nxt hi", SKIP, cwd="projects/app")
    sb.run("nexus", "c1", SKIP, "headless", "--", "nxt hi", "--output-format", "json", cwd="projects/app")
    sb.run("nexus", "c1", "headless", BYPASS, "nxt hi", cwd="projects/app",
           label="nexus c1 headless --bypass-permissions 'nxt hi'  (its own mode, set all the same)")
    sb.run("nexus", "c2", "headless", "nxt hi", BYPASS, cwd="projects/app")
    sb.run("nexus", "c2", "headless", "nxt hi", cwd="projects/app", label="nexus c2 headless 'nxt hi'  (no word)")
