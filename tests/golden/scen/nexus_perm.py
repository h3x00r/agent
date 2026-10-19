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
