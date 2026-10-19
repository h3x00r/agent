"""nexus starting, restarting and attaching to personas; headless runs; the management pass-throughs."""
import json
import os
import re
import socket

from scen import NO_LINK, case, images, set_field, snapshot_line, two_personas  # noqa: F401


# --- helpers -----------------------------------------------------------------------------------------
def world(sb, **kw):
    """Top-level keys of the docker world (compose={…}, ssh_add_rc=…), on a fresh read of it."""
    sb.load_world()
    sb.world.update(kw)
    sb.save_world()


def ctr_set(sb, persona, **kw):
    """Fields of an existing container in the docker world (state, env, reply_rc, …)."""
    sb.load_world()
    sb.world["containers"]["agent-" + persona].update(kw)
    sb.save_world()


def stopped(sb, persona):
    """The container as `docker compose stop` leaves it (the fake's stop doesn't change the world)."""
    ctr_set(sb, persona, state="exited", status="Exited (0) 2 minutes ago")


def edit(sb, rel, old, new):
    """Replace OLD (once) with NEW in the sandbox file REL, in place, so its inode stays."""
    p = sb.path(rel)
    with open(p) as f:
        text = f.read()
    if old not in text:
        raise RuntimeError("%s has no %r" % (rel, old))
    with open(p, "w") as f:
        f.write(text.replace(old, new, 1))


def append(sb, rel, text):
    with open(sb.path(rel), "a") as f:
        f.write(text)


def read(sb, rel):
    with open(sb.path(rel)) as f:
        return f.read()


def settings(sb, persona, data, mode=None):
    """The persona's ~/.claude/settings.json, inside its (fake) container home."""
    return sb.write("ctr/%s/home/.claude/settings.json" % persona,
                    data if isinstance(data, str) else json.dumps(data, indent=2) + "\n", mode)


def defaults(sb, data):
    """personas/claude.json, the Claude defaults."""
    sb.write("a/personas/claude.json", data if isinstance(data, str) else json.dumps(data, indent=2) + "\n")


def unix_socket(sb, rel):
    p = sb.path(rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    s = socket.socket(socket.AF_UNIX)
    s.bind(p)
    s.close()
    return p


# The lines of a personas file from before the splits, as tests/run.sh appends them.
LEGACY_STATUSLINE = "      - ./statusline:/etc/claude-code/statusline:ro\n"
LEGACY_CLAUDE_JSON = ("      - { type: bind, source: ./personas/claude.json, "
                      "target: /etc/claude-code/managed-settings.d/15-personas.json }\n")


def no_traceback_frames(text):
    """A Python traceback without its frames: they name the host Python's own files and lines."""
    return re.sub(r"Traceback \(most recent call last\):\n(?:  .*\n)+", "Traceback (most recent call last):\n  …\n",
                  text)


# --- start -------------------------------------------------------------------------------------------
@case("nexus/life-start")
def _(sb):
    two_personas(sb)
    images(sb)
    sb.write("projects/app/.keep", "")
    sb.run("nexus", "c1", "start", cwd="projects/app")
    sb.run("nexus", "c1", "start", cwd="projects/app")


@case("nexus/life-start-other-flavours")
def _(sb):
    # A grok persona with a key of its own and a Claude one without ssh: compose gets the placeholder
    # socket /dev/null, ssh-add isn't asked; the grok one gets no Claude defaults.
    sb.personas("g1:grok:docker", "x1:claude:docker", extra=("--ssh", "g1:new", "--ssh", "x1:none"))
    images(sb)
    sb.run("nexus", "g1", "start")
    sb.run("nexus", "x1", "start")
    sb.run("nexus", "g1", "start")


@case("nexus/life-start-no-defaults-file")
def _(sb):
    # No personas/claude.json: a Claude persona starts without the merge.
    sb.personas("c1:claude:docker")
    os.unlink(sb.path("a/personas/claude.json"))
    sb.run("nexus", "c1", "start")


@case("nexus/life-defaults-later")
def _(sb):
    sb.personas("c1:claude:docker")
    sb.setup("nexus", "c1", "start")
    # What the persona did since: Claude Code's migration made opus opus[1m], /config set its own theme,
    # and it took outputStyle out.
    s = json.loads(read(sb, "ctr/c1/home/.claude/settings.json"))
    s["model"] = "opus[1m]"
    s["theme"] = "dark"
    del s["outputStyle"]
    settings(sb, "c1", s)
    # The defaults as they were; the migrated model counts as nexus's, and is written back as opus.
    sb.run("nexus", "c1", "restart", label="nexus c1 restart  (defaults unchanged; the model migrated to opus[1m])")
    # New defaults: the model, a nested one, outputStyle and the theme change; tui goes; a new key comes.
    defaults(sb, {"model": "sonnet", "modelSettings": {"opus": {"effortLevel": "high"}}, "theme": "light",
                  "outputStyle": "Explanatory", "teammateMode": "tmux", "worktree": {"baseRef": "main"},
                  "newKey": True})
    stopped(sb, "c1")
    sb.run("nexus", "c1", "start", label="nexus c1 start  (stopped; personas/claude.json changed)")
    # The persona picks its own model: a later default leaves it alone. newKey goes from the defaults.
    s = json.loads(read(sb, "ctr/c1/home/.claude/settings.json"))
    s["model"] = "haiku"
    settings(sb, "c1", s)
    defaults(sb, {"model": "opus", "modelSettings": {"opus": {"effortLevel": "high"}}, "theme": "light",
                  "outputStyle": "Explanatory", "teammateMode": "tmux", "worktree": {"baseRef": "main"}})
    sb.run("nexus", "c1", "restart", label="nexus c1 restart  (the model default changed, the persona's own; newKey gone)")


@case("nexus/life-defaults-bad-settings", norm=no_traceback_frames)
def _(sb):
    sb.personas("c1:claude:docker", "c2:claude:docker", "c3:claude:docker", "c4:claude:docker")
    settings(sb, "c1", "{not json\n")
    settings(sb, "c2", "[]\n")
    settings(sb, "c3", {"theme": "dark"}, mode=0o444)
    sb.run("nexus", "c1", "start", label="nexus c1 start  (settings.json not JSON)")
    sb.run("nexus", "c2", "start", label="nexus c2 start  (settings.json a JSON array)")
    sb.run("nexus", "c3", "start", label="nexus c3 start  (settings.json read-only)")
    defaults(sb, '["not", "an", "object"]\n')
    sb.run("nexus", "c4", "start", label="nexus c4 start  (personas/claude.json a JSON array)")
    defaults(sb, "{broken\n")
    sb.run("nexus", "c4", "restart", label="nexus c4 restart  (personas/claude.json not JSON: the merge fails)")


@case("nexus/life-restart")
def _(sb):
    two_personas(sb)
    images(sb)
    sb.container("c1")
    sb.run("nexus", "c1", "restart", label="nexus c1 restart  (running; no defaults merged yet)")
    sb.run("nexus", "c1", "restart", label="nexus c1 restart  (again: nothing to merge)")
    sb.container("g1", state="exited")
    sb.run("nexus", "g1", "restart", label="nexus g1 restart  (stopped)")


@case("nexus/life-compose-up-fails")
def _(sb):
    two_personas(sb)
    world(sb, compose={"up_rc": 17})
    sb.run("nexus", "c1", "start")
    sb.run("nexus", "c1", "restart")
    sb.run("nexus", "c1", "headless", "nxt say hi")
    sb.run("nexus", "c1")


# --- start refusals ----------------------------------------------------------------------------------
@case("nexus/life-refuse-files")
def _(sb):
    two_personas(sb)
    os.unlink(sb.path("a/personas/g1.grok.toml"))
    sb.run("nexus", "g1", "start", label="nexus g1 start  (personas/g1.grok.toml gone)")
    os.unlink(sb.path("a/personas/g1.key"))
    sb.run("nexus", "g1", "start", label="nexus g1 start  (its key gone too: checked first)")
    sb.run("nexus", "g1", "restart", label="nexus g1 restart  (the same)")
    os.unlink(sb.path("a/personas/claude.managed.json"))
    sb.run("nexus", "c1", "start", label="nexus c1 start  (personas/claude.managed.json gone)")
    sb.run("nexus", "c1", "headless", "nxt say hi", label="nexus c1 headless 'nxt say hi'  (the same)")
    # Running already: what is mounted is there, so nothing is checked.
    sb.container("g1")
    sb.container("c1")
    sb.run("nexus", "g1", "start", label="nexus g1 start  (running, its key and grok.toml gone)")
    sb.run("nexus", "c1", "start", label="nexus c1 start  (running, claude.managed.json gone)")
    sb.run("nexus", "c1", "headless", "nxt say hi", label="nexus c1 headless 'nxt say hi'  (the same)")


@case("nexus/life-legacy-statusline")
def _(sb):
    # A personas file from before statusline/ was split: a start refuses (any persona: it is the file's),
    # a running persona starts as it is, an attach to it warns, a headless run in it doesn't.
    two_personas(sb)
    sb.container("g1")
    append(sb, "a/compose.personas.yml", LEGACY_STATUSLINE)
    sb.run("nexus", "c1", "start")
    sb.run("nexus", "c1", "restart")
    sb.run("nexus", "g1", "start", label="nexus g1 start  (running)")
    sb.run("nexus", "g1", label="nexus g1  (running)")
    sb.run("nexus", "g1", "headless", "nxt say hi", label="nexus g1 headless 'nxt say hi'  (running)")
    sb.run("nexus", "g1", "restart", label="nexus g1 restart  (running)")


@case("nexus/life-legacy-claude-json")
def _(sb):
    # One from before personas/claude.json was split: personas/claude.json as the managed drop-in.
    two_personas(sb)
    sb.container("c1")
    append(sb, "a/compose.personas.yml", LEGACY_CLAUDE_JSON)
    sb.run("nexus", "g1", "start", label="nexus g1 start  (a grok persona: the same file)")
    sb.run("nexus", "c1", "start", label="nexus c1 start  (running)")
    sb.run("nexus", "c1", "review", label="nexus c1 review  (running)")


@case("nexus/life-refuse-root")
def _(sb):
    two_personas(sb)
    sb.write("blocker", "a file, not a dir\n")
    sb.run("nexus", "c1", "start", env={"PROJECTS_ROOT": "rel/projects"},
           label="nexus c1 start  (PROJECTS_ROOT=rel/projects in the shell)")
    sb.run("nexus", "c1", "start", env={"PROJECTS_ROOT": "/"}, label="nexus c1 start  (PROJECTS_ROOT=/ in the shell)")
    sb.run("nexus", "c1", "start", env={"PROJECTS_ROOT": sb.path("blocker/projects")},
           label="nexus c1 start  (PROJECTS_ROOT=<S>/blocker/projects in the shell, under a file)")
    sb.run("nexus", "c1", "headless", "nxt say hi", env={"PROJECTS_ROOT": "rel"},
           label="nexus c1 headless 'nxt say hi'  (PROJECTS_ROOT=rel in the shell)")
    sb.run("nexus", "c1", env={"PROJECTS_ROOT": "rel"}, label="nexus c1  (PROJECTS_ROOT=rel in the shell)")
    edit(sb, "a/.env", "PROJECTS_ROOT=" + sb.projects + "\n", "PROJECTS_ROOT=projects\n")
    sb.run("nexus", "c1", "start", label="nexus c1 start  (PROJECTS_ROOT=projects in .env)")
    edit(sb, "a/.env", "PROJECTS_ROOT=projects\n", "PROJECTS_ROOT=" + sb.projects + "\n")
    # The persona's own root, edited by hand (nexus --config checks it; a hand edit doesn't).
    set_field(sb, "c1", "root", "rel/own")
    sb.run("nexus", "c1", "start", label="nexus c1 start  (its own root=rel/own, by hand)")
    set_field(sb, "c1", "root", sb.path("blocker/own"))
    sb.run("nexus", "c1", "start", label="nexus c1 start  (its own root=<S>/blocker/own, under a file)")
    # The management commands don't check it: it goes to compose as it is.
    sb.run("nexus", "c1", "stop", label="nexus c1 stop  (the same)")
    set_field(sb, "c1", "root", None)


@case("nexus/life-root-created")
def _(sb):
    # A projects root that isn't there yet is made at the start, before Docker would make it root-owned.
    two_personas(sb)
    edit(sb, "a/.env", "PROJECTS_ROOT=" + sb.projects + "\n", "")
    sb.run("nexus", "c1", "start", env={"PROJECTS_ROOT": sb.path("fresh/root") + "/"},
           label="nexus c1 start  (PROJECTS_ROOT=<S>/fresh/root/ in the shell)")
    sb.run("nexus", "g1", "start", label="nexus g1 start  (no PROJECTS_ROOT in the shell or .env: ~/projects)")


# --- warnings at a start ------------------------------------------------------------------------------
@case("nexus/life-ddev-ca", fakes=("ddev",))
def _(sb):
    sb.personas("d1:claude:ddev", "c1:claude:docker")
    sb.run("nexus", "d1", "start", label="nexus d1 start  (no mkcert CA)")
    sb.run("nexus", "d1", "start", label="nexus d1 start  (running: nothing checked)")
    sb.run("nexus", "c1", "start", label="nexus c1 start  (a docker persona: no CA wanted)")
    sb.write("home/.local/share/mkcert/rootCA.pem", "fake CA\n")
    sb.run("nexus", "d1", "restart", label="nexus d1 restart  (the CA there now)")
    sb.run("nexus", "d1", "build", label="nexus d1 build  (the CA isn't looked for)")


@case("nexus/life-ssh-agent")
def _(sb):
    # The agent's socket at a path of its own: sb.agent_socket()'s <S>/agent.sock normalises to "<A>gent.sock".
    two_personas(sb)
    sock = unix_socket(sb, "ssh-agent/sock")
    sb.env["SSH_AUTH_SOCK"] = sock
    sb.run("nexus", "c1", "start", label="nexus c1 start  (the agent holds no keys: ssh-add -l exits 1)")
    world(sb, ssh_add_rc=2)
    sb.run("nexus", "c1", "restart", label="nexus c1 restart  (nothing answers: ssh-add -l exits 2)")
    world(sb, ssh_add_rc=0)
    sb.run("nexus", "c1", "restart", label="nexus c1 restart  (a key in it: ssh-add -l exits 0)")
    world(sb, ssh_add_rc=1)
    sb.run("nexus", "c1", "start", label="nexus c1 start  (running: the agent isn't asked)")
    sb.run("nexus", "g1", "start", label="nexus g1 start  (its own key: the agent isn't asked)")
    sb.run("nexus", "c1", "stop", label="nexus c1 stop  (the socket, not the placeholder)")


@case("nexus/life-ssh-agent-found")
def _(sb):
    # No SSH_AUTH_SOCK in the shell (or one that isn't a socket): the usual places are searched, in order.
    sb.personas("c1:claude:docker")
    unix_socket(sb, "run/keyring/ssh")
    sb.run("nexus", "c1", "start", label="nexus c1 start  (an agent at $XDG_RUNTIME_DIR/keyring/ssh)")
    unix_socket(sb, "run/openssh_agent")
    sb.run("nexus", "c1", "restart", env={"SSH_AUTH_SOCK": sb.path("nothing-here")},
           label="nexus c1 restart  (SSH_AUTH_SOCK not a socket; $XDG_RUNTIME_DIR/openssh_agent comes first)")
    sb.run("nexus", "c1", "restart", env={"XDG_RUNTIME_DIR": None},
           label="nexus c1 restart  (no XDG_RUNTIME_DIR: /run/user/$AGENT_UID, nothing there)")


# --- attach ------------------------------------------------------------------------------------------
@case("nexus/life-attach")
def _(sb):
    two_personas(sb)
    sb.container("c1")
    sb.write("projects/my.app/.keep", "")
    sb.run("nexus", "c1", cwd="projects/my.app")
    sb.run("nexus", "c1", "review", "--", "claude", "--resume", cwd="projects/my.app")
    sb.run("nexus", "c1")


@case("nexus/life-attach-names")
def _(sb):
    two_personas(sb)
    sb.container("c1")
    sb.write("projects/a:b.c/.keep", "")
    sb.write("projects/app/sub dir/.keep", "")
    sb.run("nexus", "c1", cwd="projects/a:b.c")
    sb.run("nexus", "c1", "v1.2:fix", cwd="projects/a:b.c")
    sb.run("nexus", "c1", "--", "claude", cwd="projects/app/sub dir")
    sb.run("nexus", "c1", "--", cwd="projects/app")
    sb.run("nexus", "c1", "work", "--", "sh", "-c", "echo 'a b'; exec bash", cwd="projects/app")
    sb.run("nexus", "c1", "work", "extra", cwd="projects/app")


@case("nexus/life-attach-stopped")
def _(sb):
    # Not running: started first (compose's progress on stderr), the defaults merged, then attached.
    two_personas(sb)
    images(sb)
    sb.write("projects/app/.keep", "")
    sb.run("nexus", "c1", cwd="projects/app")
    sb.container("g1", state="exited")
    sb.run("nexus", "g1", "notes", cwd="projects/app")


@case("nexus/life-attach-dirs")
def _(sb):
    two_personas(sb)
    sb.container("c1")
    sb.write("projects/app/.keep", "")
    sb.write("outside/proj/.keep", "")
    sb.symlink("links/app", sb.projects + "/app")
    sb.symlink("links/projects", sb.projects)
    sb.run("nexus", "c1", cwd="links/app", label="nexus c1  (from a symlink to <P>/app)")
    sb.run("nexus", "c1", cwd="links/projects", label="nexus c1  (from a symlink to the root)")
    sb.run("nexus", "c1", cwd="outside/proj", label="nexus c1  (outside the root)")
    sb.run("nexus", "c1", "proj", cwd="outside/proj", label="nexus c1 proj  (outside the root, named)")
    sb.run("nexus", "c1", "headless", "nxt link", cwd="links/app", label="nexus c1 headless 'nxt link'  (from a symlink to <P>/app)")


@case("nexus/life-attach-root-symlink")
def _(sb):
    # The projects root itself named by a symlink: a dir by its real path gets the root's spelling.
    two_personas(sb)
    root = sb.symlink("links/projects", sb.projects)
    sb.write("projects/app/.keep", "")
    sb.container("c1", root=root)
    sb.run("nexus", "c1", cwd="projects/app", env={"PROJECTS_ROOT": root},
           label="nexus c1  (PROJECTS_ROOT=<S>/links/projects, from <P>/app)")
    sb.run("nexus", "c1", "headless", "nxt real", cwd="projects/app", env={"PROJECTS_ROOT": root},
           label="nexus c1 headless 'nxt real'  (the same)")
    sb.run("nexus", "c1", "start", label="nexus c1 start  (PROJECTS_ROOT=<P> from .env: the real path, another root)")


@case("nexus/life-attach-deleted-dir")
def _(sb):
    two_personas(sb)
    sb.container("c1")
    gone = sb.projects + "/gone"
    wrap = sb.script("wrap/in-deleted-dir", 'mkdir -p "%s" && cd "%s" && rmdir "%s" && exec "%s/nexus" "$@"'
                     % (gone, gone, gone, sb.a))
    sb.run("nexus", "c1", via=wrap, label="nexus c1  (in <P>/gone, deleted)")
    sb.run("nexus", "c1", "headless", "nxt say hi", via=wrap, label="nexus c1 headless 'nxt say hi'  (the same)")
    sb.run("nexus", "c1", "start", via=wrap, label="nexus c1 start  (the same: a start needs no dir)")


@case("nexus/life-attach-term")
def _(sb):
    two_personas(sb)
    sb.container("c1", terms=["xterm-256color", "xterm", "screen-256color", "tmux-256color"])
    sb.run("nexus", "c1", env={"TERM": "xterm-kitty"}, label="nexus c1  (TERM=xterm-kitty, unknown inside)")
    sb.run("nexus", "c1", env={"TERM": "tmux-256color"}, label="nexus c1  (TERM=tmux-256color, known inside)")
    # bash itself sets TERM=dumb when it is unset, so nexus asks for dumb, not its default xterm-256color.
    sb.run("nexus", "c1", env={"TERM": None}, label="nexus c1  (no TERM)")
    sb.run("nexus", "c1", env={"TERM": ""}, label="nexus c1  (TERM empty)")


# --- a projects root changed while the persona runs ---------------------------------------------------
def drifted(sb, *personas):
    """PERSONAS (c1 by default) running with the projects root <S>/old; the one they are set up with now is
    <P>, from .env."""
    two_personas(sb)
    old = sb.path("old")
    sb.write("old/app/.keep", "")
    sb.write("projects/proj/.keep", "")
    sb.write("elsewhere/.keep", "")
    sb.symlink("links/old-app", old + "/app")
    for p in personas or ("c1",):
        sb.container(p, root=old, sessions=[
            {"name": "main", "windows": 1, "attached": 0, "path": old},
            {"name": "billing", "windows": 2, "attached": 1, "path": old + "/app"}])


@case("nexus/life-drift-attach")
def _(sb):
    drifted(sb)
    sb.run("nexus", "c1", cwd="old/app", label="nexus c1  (in the old root)")
    sb.run("nexus", "c1", cwd="links/old-app", label="nexus c1  (by a symlink into the old root)")
    sb.run("nexus", "c1", "billing", cwd="projects/proj", label="nexus c1 billing  (in the new root: a session it runs)")
    sb.run("nexus", "c1", cwd="projects/proj", label="nexus c1  (in the new root: no session proj)")
    sb.run("nexus", "c1", "main", "--", "claude", cwd="elsewhere", label="nexus c1 main -- claude  (outside both)")
    sb.run("nexus", "c1", cwd="elsewhere", label="nexus c1  (outside both: the root's name, no such session)")


@case("nexus/life-drift-start")
def _(sb):
    drifted(sb)
    sb.run("nexus", "c1", "start", label="nexus c1 start  (the root from .env)")
    sb.run("nexus", "c1", "start", env={"PROJECTS_ROOT": sb.path("elsewhere")},
           label="nexus c1 start  (PROJECTS_ROOT=<S>/elsewhere in the shell)")
    sb.run("nexus", "c1", "start", env={"PROJECTS_ROOT": sb.s + "//old/"},
           label="nexus c1 start  (PROJECTS_ROOT=<S>//old/: the same root, spelt otherwise)")
    sb.run("nexus", "c1", "restart", label="nexus c1 restart  (applies <P>)")
    sb.run("nexus", "c1", "start", label="nexus c1 start  (after the restart)")
    sb.run("nexus", "c1", cwd="projects/proj", label="nexus c1  (in the new root, after the restart)")


@case("nexus/life-drift-roots")
def _(sb):
    # Where the root it would apply comes from: its own root= (no clause), or the default ~/projects.
    drifted(sb, "c1", "g1")
    edit(sb, "a/.env", "PROJECTS_ROOT=" + sb.projects + "\n", "")
    set_field(sb, "c1", "root", sb.projects)
    sb.run("nexus", "c1", "start", label="nexus c1 start  (its own root=<P>)")
    sb.run("nexus", "c1", cwd="old/app", label="nexus c1  (its own root=<P>; in the old root)")
    sb.run("nexus", "g1", "start", label="nexus g1 start  (no PROJECTS_ROOT anywhere: ~/projects)")
    sb.run("nexus", "g1", "headless", "nxt hi", cwd="elsewhere", label="nexus g1 headless 'nxt hi'  (outside both)")


# --- headless ----------------------------------------------------------------------------------------
@case("nexus/life-headless-claude")
def _(sb):
    two_personas(sb)
    sb.container("c1")
    sb.write("projects/app/.keep", "")
    sb.run("nexus", "c1", "headless", "nxt say hi", cwd="projects/app")
    sb.run("nexus", "c1", "headless", "nxt review", stdin="line one\nline two\n", cwd="projects/app",
           label="printf 'line one\\nline two\\n' | nexus c1 headless 'nxt review'")
    sb.run("nexus", "c1", "headless", stdin="nxt piped only", cwd="projects/app",
           label="printf 'nxt piped only' | nexus c1 headless")
    sb.run("nexus", "c1", "headless", "--output-format", "json", stdin="nxt the prompt\n", cwd="projects/app",
           label="echo 'nxt the prompt' | nexus c1 headless --output-format json")
    sb.run("nexus", "c1", "headless", "nxt json", "--output-format", "json", "--model=opus", cwd="projects/app")
    sb.run("nexus", "c1", "headless", "---\nname: nxt-fm\n---\nthe task", cwd="projects/app",
           label="nexus c1 headless $'---\\nname: nxt-fm\\n---\\nthe task'")
    sb.run("nexus", "c1", "headless", "--", "nxt after dashes", "-v", cwd="projects/app")
    # A prompt that looks like a flag is a flag, -- or not.
    sb.run("nexus", "c1", "headless", "--", "--not-a-flag", cwd="projects/app")
    sb.run("nexus", "c1", "headless", "nxt empty pipe", stdin="", cwd="projects/app",
           label="printf '' | nexus c1 headless 'nxt empty pipe'")
    sb.run("nexus", "c1", "headless", "nxt terminal", mode="tty-in", cwd="projects/app",
           label="nexus c1 headless 'nxt terminal'  (stdin a terminal)")
    sb.run("nexus", "c1", "headless", "nxt root")


@case("nexus/life-headless-stdin-kinds")
def _(sb):
    two_personas(sb)
    sb.container("c1")
    sb.write("input.txt", "from a file\n")
    sb.write("empty.txt", "")
    wf = sb.script("wrap/from-file", 'exec "%s/nexus" "$@" < "%s"' % (sb.a, sb.path("input.txt")))
    we = sb.script("wrap/from-empty-file", 'exec "%s/nexus" "$@" < "%s"' % (sb.a, sb.path("empty.txt")))
    # A pipe that stays open with nothing in it: after 3 s the run goes ahead without it. The writer
    # sleeps 7 s, so the 3 s are up well before the pipe ends (nexus reaches its read within a second).
    wi = sb.script("wrap/idle-pipe", 'sleep 7 | "%s/nexus" "$@"' % sb.a)
    sb.run("nexus", "c1", "headless", "nxt file", via=wf, label="nexus c1 headless 'nxt file' < input.txt")
    sb.run("nexus", "c1", "headless", via=wf, label="nexus c1 headless < input.txt")
    sb.run("nexus", "c1", "headless", via=we, label="nexus c1 headless < empty.txt")
    sb.run("nexus", "c1", "headless", "nxt empty file", via=we, label="nexus c1 headless 'nxt empty file' < empty.txt")
    sb.run("nexus", "c1", "headless", "nxt idle", via=wi, label="sleep 7 | nexus c1 headless 'nxt idle'")


@case("nexus/life-headless-refusals")
def _(sb):
    two_personas(sb)
    sb.container("c1")
    sb.write("outside/.keep", "")
    sb.run("nexus", "c1", "headless")
    sb.run("nexus", "c1", "headless", "--output-format", "json")
    sb.run("nexus", "c1", "headless", mode="tty-in", label="nexus c1 headless  (stdin a terminal)")
    sb.run("nexus", "c1", "headless", stdin="", label="printf '' | nexus c1 headless")
    sb.run("nexus", "c1", "headless", "nxt out", cwd="outside")
    sb.run("nexus", "c1", "headless", "nxt out", stdin="x\n", cwd="outside", label="echo x | nexus c1 headless 'nxt out'")
    set_field(sb, "c1", "flavour", "other")
    sb.run("nexus", "c1", "headless", "nxt say hi", label="nexus c1 headless 'nxt say hi'  (flavour=other, by hand)")
    set_field(sb, "c1", "flavour", "claude")


@case("nexus/life-headless-drift")
def _(sb):
    drifted(sb)
    sb.run("nexus", "c1", "headless", "nxt say hi", cwd="old/app", label="nexus c1 headless 'nxt say hi'  (in the old root)")
    sb.run("nexus", "c1", "headless", "nxt say hi", cwd="links/old-app",
           label="nexus c1 headless 'nxt say hi'  (by a symlink into the old root)")
    sb.run("nexus", "c1", "headless", "nxt say hi", cwd="projects/proj",
           label="nexus c1 headless 'nxt say hi'  (in the new root)")
    sb.run("nexus", "c1", "headless", "nxt say hi", cwd="elsewhere", label="nexus c1 headless 'nxt say hi'  (outside both)")


@case("nexus/life-headless-grok")
def _(sb):
    two_personas(sb)
    sb.container("g1")
    sb.write("projects/app/.keep", "")
    sb.run("nexus", "g1", "headless", "nxt hello", cwd="projects/app")
    sb.run("nexus", "g1", "headless", "nxt hello", "--output-format", "json", cwd="projects/app")
    sb.run("nexus", "g1", "headless", "nxt review", stdin="in 1\nin 2\n", cwd="projects/app",
           label="printf 'in 1\\nin 2\\n' | nexus g1 headless 'nxt review'")
    sb.run("nexus", "g1", "headless", stdin="nxt only", cwd="projects/app", label="printf 'nxt only' | nexus g1 headless")
    sb.run("nexus", "g1", "headless", "--note=a b", "-v", stdin="nxt p", cwd="projects/app",
           label="printf 'nxt p' | nexus g1 headless '--note=a b' -v")
    sb.run("nexus", "g1", "headless", "---\nname: nxt-fm\n---\nthe task", cwd="projects/app",
           label="nexus g1 headless $'---\\nname: nxt-fm\\n---\\nthe task'")
    sb.run("nexus", "g1", "headless", "nxt empty pipe", stdin="", cwd="projects/app",
           label="printf '' | nexus g1 headless 'nxt empty pipe'")


@case("nexus/life-headless-exit")
def _(sb):
    two_personas(sb)
    images(sb)
    # Not started yet: compose and the defaults on stderr, the CLI's reply alone on stdout.
    sb.run("nexus", "c1", "headless", "nxt say hi", label="nexus c1 headless 'nxt say hi'  (not started)")
    ctr_set(sb, "c1", reply="nxt the CLI failed", reply_rc=7)
    sb.run("nexus", "c1", "headless", "nxt fail", label="nexus c1 headless 'nxt fail'  (the CLI exits 7)")
    sb.container("g1", reply="nxt grok failed", reply_rc=3)
    sb.run("nexus", "g1", "headless", "nxt fail", stdin="x\n", label="echo x | nexus g1 headless 'nxt fail'  (grok exits 3)")


# --- management pass-throughs ---------------------------------------------------------------------------
@case("nexus/life-mgmt")
def _(sb):
    two_personas(sb)
    images(sb)
    sb.container("c1")
    sock = unix_socket(sb, "ssh-agent/sock")
    sb.run("nexus", "c1", "stop")
    sb.run("nexus", "c1", "logs")
    sb.run("nexus", "c1", "logs", "--tail", "5", "--no-color")
    sb.run("nexus", "c1", "build")
    sb.run("nexus", "c1", "build", "--no-cache", "--pull")
    sb.run("nexus", "g1", "pull")
    sb.run("nexus", "g1", "pull", "--quiet")
    sb.run("nexus", "g1", "stop", env={"SSH_AUTH_SOCK": sock}, label="nexus g1 stop  (with an ssh-agent socket)")
    sb.run("nexus", "c1", "build", env={"SSH_AUTH_SOCK": sock}, label="nexus c1 build  (the same)")
    world(sb, compose={"build_rc": 4, "pull_rc": 5})
    sb.run("nexus", "c1", "build", label="nexus c1 build  (the build fails)")
    sb.run("nexus", "g1", "pull", label="nexus g1 pull  (the pull fails)")
