"""nexus <persona> upgrade: its argument checks and refusals, the pin, the build or pull, the version check,
the containers on the old image (removed when stopped, recreated through nexus itself when running) and the
old images; and what a failure puts back."""
import os
import re
import socket

from scen import NO_LINK, before_personas_json, case, images, move_lines, set_field, snapshot_line, two_personas  # noqa: F401

# Fake image ids, so ${id:7:12} (the short id upgrade prints for an untagged image) reads as one.
OLD = "sha256:" + "a" * 64        # Claude Code 2.1.300, at local/agent-claude-docker:latest
GROK_OLD = "sha256:" + "b" * 64   # grok 0.9.0, at local/agent-grok-docker:latest
LOOSE = "sha256:" + "c" * 64      # an image a rebuild left without a tag, under a stopped peer
LOOSE_RUN = "sha256:" + "d" * 64  # the same, under a running peer
PREV = "sha256:" + "e" * 64       # local/agent-claude-docker:2.1.400, built by an earlier upgrade

CLAUDE_REPO = "local/agent-claude-docker"
GROK_REPO = "local/agent-grok-docker"


# --- helpers -----------------------------------------------------------------------------------------
def personas(sb, *specs, extra=()):
    sb.personas(*specs, extra=extra)


def peers(sb):
    """c1 (your ssh-agent) and c2 (a key of its own), the Claude peers; g1, grok with its own key, no peer."""
    personas(sb, "c1:claude:docker", "c2:claude:docker", "g1:grok:docker", extra=("--ssh", "c2:new", "--ssh", "g1:new"))


def world(sb, **kw):
    """Top-level keys of the docker world (compose={…}, built_version=…), on a fresh read of it."""
    sb.load_world()
    sb.world.update(kw)
    sb.save_world()


def templates(sb, **kw):
    """The image each persona's service resolves to, as compose would: images(sb)'s c1 and g1, the other
    Claude personas the same as c1, and KW on top ({tag} follows CLAUDE_TAG or CLAUDE_VERSION)."""
    images(sb)
    sb.load_world()
    si = sb.world["service_images"]
    for p in ("c2", "c3"):
        si[p] = CLAUDE_REPO + ":{tag}"
    si.update(kw)
    sb.save_world()


def image(sb, ref, id_, version):
    """An image in the docker world: REF tagged on ID_, whose CLI prints VERSION."""
    sb.load_world()
    sb.world["images"][ref] = {"id": id_, "version": version}
    sb.save_world()


def untagged(sb, *ids):
    sb.load_world()
    sb.world.setdefault("untagged", []).extend(ids)
    sb.save_world()


def edit(sb, rel, old, new):
    """Replace OLD (once) with NEW in the sandbox file REL, in place, so its inode stays."""
    p = sb.path(rel)
    with open(p) as f:
        text = f.read()
    if old not in text:
        raise RuntimeError("%s has no %r" % (rel, old))
    with open(p, "w") as f:
        f.write(text.replace(old, new, 1))


def unix_socket(sb, rel):
    """A socket at REL, e.g. an ssh-agent's (sb.agent_socket()'s <S>/agent.sock would normalise to <A>gent.sock)."""
    p = sb.path(rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    s = socket.socket(socket.AF_UNIX)
    s.bind(p)
    s.close()
    return p


def agent(sb):
    """An ssh-agent in this shell, holding a key, as a recreate of an agent persona wants."""
    sb.env["SSH_AUTH_SOCK"] = unix_socket(sb, "ssh-agent/sock")
    world(sb, ssh_add_rc=0)


def old_claude(sb):
    """Claude Code 2.1.300 built before, at the tag CLAUDE_VERSION=latest names."""
    image(sb, CLAUDE_REPO + ":latest", OLD, "2.1.300 (Claude Code)")


def pin(sb):
    """What .env's CLAUDE_VERSION line says now, as a recorded run."""
    sb.run("grep", "^CLAUDE_VERSION=", sb.path("a/.env"), label="grep ^CLAUDE_VERSION= <A>/.env")


SESSIONS = [{"name": "main", "windows": 1, "attached": 0, "path": None},
            {"name": "billing", "windows": 2, "attached": 1, "path": None}]


def sessions(sb):
    return [dict(s, path=sb.projects) for s in SESSIONS]


# --- arguments and refusals ----------------------------------------------------------------------------
@case("nexus/upgrade-args")
def _(sb):
    # The arguments are checked first, then the flavour, .env, the pin, the lock and then the version. Each
    # refusal leaves .env as it was, and no fake is called.
    personas(sb, "c1:claude:docker", "g1:grok:docker", extra=("--ssh", "g1:new"))
    sb.run("nexus", "c1", "upgrade", "-x")
    sb.run("nexus", "c1", "upgrade", "--force", "-y")
    sb.run("nexus", "c1", "upgrade", "-y", "2.1.400", "-n",
           label="nexus c1 upgrade -y 2.1.400 -n  (the option after the version)")
    sb.run("nexus", "c1", "upgrade", "2.1.400", "stable")
    sb.run("nexus", "c1", "upgrade", "2.1.400", "2.1.401", "-x",
           label="nexus c1 upgrade 2.1.400 2.1.401 -x  (the second version first)")
    sb.run("nexus", "c1", "upgrade", "1.2")
    sb.run("nexus", "c1", "upgrade", "nightly", "-y")
    sb.run("nexus", "c1", "upgrade", "2.1.400-", label="nexus c1 upgrade 2.1.400-  (a suffix needs a character)")
    sb.run("nexus", "c1", "upgrade", "V2.1.400", label="nexus c1 upgrade V2.1.400  (only a lower-case v is dropped)")
    # grok's channels are stable and alpha: latest, the .env template's value for GROK_VERSION, is neither.
    sb.run("nexus", "g1", "upgrade", "latest")
    sb.run("nexus", "g1", "upgrade", "2.1")
    set_field(sb, "c1", "flavour", "other")
    sb.run("nexus", "c1", "upgrade", "2.1.400", label="nexus c1 upgrade 2.1.400  (flavour=other, by hand)")
    sb.run("nexus", "c1", "upgrade", "-x", label="nexus c1 upgrade -x  (flavour=other: the option first)")
    set_field(sb, "c1", "flavour", "claude")


@case("nexus/upgrade-no-env")
def _(sb):
    # No .env: refused before the pin is looked at. The projects root falls back to ~/projects, which the
    # preflight ahead of every persona command makes.
    personas(sb, "c1:claude:docker")
    os.unlink(sb.path("a/.env"))
    sb.run("nexus", "c1", "upgrade", "2.1.400", "-y")
    sb.run("nexus", "c1", "upgrade", "2.1.400", "-y", env={"CLAUDE_VERSION": "2.1.0"},
           label="nexus c1 upgrade 2.1.400 -y  (CLAUDE_VERSION=2.1.0 exported too: .env is checked first)")
    sb.run("nexus", "c1", "upgrade", "-x", label="nexus c1 upgrade -x  (the option first)")


@case("nexus/upgrade-pin-exported")
def _(sb):
    personas(sb, "c1:claude:docker", "g1:grok:docker", extra=("--ssh", "g1:new"))
    templates(sb)
    sb.run("nexus", "c1", "upgrade", "2.1.400", "-y", env={"CLAUDE_VERSION": "2.1.0"},
           label="nexus c1 upgrade 2.1.400 -y  (CLAUDE_VERSION=2.1.0 exported)")
    sb.run("nexus", "c1", "upgrade", "2.1.400", "-y", env={"CLAUDE_VERSION": ""},
           label="nexus c1 upgrade 2.1.400 -y  (CLAUDE_VERSION exported, empty)")
    sb.run("nexus", "c1", "upgrade", "1.2", env={"CLAUDE_VERSION": ""},
           label="nexus c1 upgrade 1.2  (the same: the pin is checked before the version)")
    sb.run("nexus", "g1", "upgrade", "0.9.1", "-y", env={"GROK_VERSION": ""},
           label="nexus g1 upgrade 0.9.1 -y  (GROK_VERSION exported, empty)")
    # The other flavour's pin is no matter: the upgrade goes ahead (and the fake sees GROK_VERSION).
    sb.run("nexus", "c1", "upgrade", "2.1.400", "-y", env={"GROK_VERSION": "0.9.0"},
           label="nexus c1 upgrade 2.1.400 -y  (GROK_VERSION=0.9.0 exported)")


@case("nexus/upgrade-lock")
def _(sb):
    personas(sb, "c1:claude:docker", "g1:grok:docker", extra=("--ssh", "g1:new"))
    with sb.hold_lock():
        sb.run("nexus", "c1", "upgrade", "2.1.400", "-y", label="nexus c1 upgrade 2.1.400 -y  (the lock held)")
        sb.run("nexus", "c1", "upgrade", "1.2", label="nexus c1 upgrade 1.2  (the lock held: checked before the version)")
        sb.run("nexus", "g1", "upgrade", label="nexus g1 upgrade  (the lock held: no channel looked up)")
        sb.run("nexus", "c1", "upgrade", "2.1.400", env={"CLAUDE_VERSION": ""},
               label="nexus c1 upgrade 2.1.400  (the lock held, the pin exported: the pin first)")
    sb.run("nexus", "c1", "upgrade", "1.2", label="nexus c1 upgrade 1.2  (the lock free again)")


def no_curl(text):
    """Without curl's own error line: bash prints what curl says ("curl: (7) Failed to connect to …", its
    wording and timing curl's own), where a port that looks the channel up with urllib says nothing of its
    own. The "can't look up" line that follows is compared."""
    return re.sub(r"^curl: .*\n", "", text, flags=re.M)


@case("nexus/upgrade-channel-lookup", norm=no_curl)
def _(sb):
    # The proxies point at 127.0.0.1:9, so every lookup fails, before the pin or any image is touched.
    personas(sb, "c1:claude:docker", "g1:grok:docker", extra=("--ssh", "g1:new"))
    templates(sb)
    sb.run("nexus", "c1", "upgrade", label="nexus c1 upgrade  (the default channel, latest)")
    sb.run("nexus", "c1", "upgrade", "stable", "-y")
    sb.run("nexus", "c1", "upgrade", "-y", label="nexus c1 upgrade -y  (no version: the default channel)")
    sb.run("nexus", "c1", "upgrade", "v", label="nexus c1 upgrade v  (v alone: no version, so the default channel)")
    sb.run("nexus", "c1", "upgrade", "", label="nexus c1 upgrade ''  (an empty argument: the same)")
    sb.run("nexus", "c1", "upgrade", "", "stable", label="nexus c1 upgrade '' stable  (an empty one doesn't count as one)")
    sb.run("nexus", "g1", "upgrade", label="nexus g1 upgrade  (grok's default channel, stable)")
    sb.run("nexus", "g1", "upgrade", "alpha")


def no_bash_error(text):
    """Without bash's own error for a redirection that failed ("<A>/nexus: line 84: <A>/.env: Permission
    denied"): its wording and line number are bash's, which a port can't match. The nexus lines that
    follow are compared."""
    return re.sub(r"^<A>/nexus: line \d+: .*\n", "", text, flags=re.M)


@case("nexus/upgrade-env-readonly", norm=no_bash_error)
def _(sb):
    # .env can't be written: the pin can't be set, so nothing is built. The temp file env_set wrote stays.
    # (Run as root, 0444 doesn't stop the write, and this would record a plain upgrade.)
    personas(sb, "c1:claude:docker")
    templates(sb)
    os.chmod(sb.path("a/.env"), 0o444)
    sb.run("nexus", "c1", "upgrade", "2.1.400", "-y")


@case("nexus/upgrade-no-image")
def _(sb):
    personas(sb, "c1:claude:docker", "c2:claude:docker", extra=("--ssh", "c2:new"))
    # compose names no image for c1 (the fake's world has none): refused before the pin is set.
    sb.run("nexus", "c1", "upgrade", "2.1.400", "-y", label="nexus c1 upgrade 2.1.400 -y  (compose can't resolve the image)")
    # c2's service, edited by hand, names an image no one builds: c1, the first of the context, is built,
    # and c2 then has no image. The pin goes back, and the image built is removed.
    templates(sb, c2="local/agent-claude-mine:{tag}")
    sb.run("nexus", "c1", "upgrade", "2.1.400", "-y", label="nexus c1 upgrade 2.1.400 -y  (c2's image is in another repo)")


# --- a full run ----------------------------------------------------------------------------------------
@case("nexus/upgrade-version")
def _(sb):
    # An explicit version, no images and no containers yet: "latest -> …", built, done.
    personas(sb, "c1:claude:docker", "g1:grok:docker", extra=("--ssh", "g1:new"))
    templates(sb)
    sb.run("nexus", "c1", "upgrade", "2.1.400", "-y")
    # A v in front is dropped, a suffix is a version; the image 2.1.400 is the old one now, and goes.
    sb.run("nexus", "c1", "upgrade", "v2.1.401-rc.1")
    pin(sb)


@case("nexus/upgrade-contexts", fakes=("ddev",))
def _(sb):
    # Two contexts: one image each, built through the first persona of each. From a ddev persona the
    # preflight makes ~/.ddev and the mkcert dir first.
    personas(sb, "c1:claude:docker", "d1:claude:ddev", "c2:claude:docker", extra=("--ssh", "c2:new"))
    templates(sb, d1="local/agent-claude-ddev:{tag}")
    sb.run("nexus", "d1", "upgrade", "2.1.400", "-y")


@case("nexus/upgrade-peers")
def _(sb):
    # The images there already, one running and one stopped peer, and a grok persona that is no peer.
    # c1, on your ssh-agent, can't be recreated without one: even with -y it is left for later, and its
    # old image kept.
    peers(sb)
    templates(sb)
    old_claude(sb)
    image(sb, GROK_REPO + ":latest", GROK_OLD, "grok 0.9.0")
    sb.container("c1", image=OLD, sessions=sessions(sb))
    sb.container("c2", state="exited", image=OLD)
    sb.container("g1", image=GROK_OLD)
    sb.run("nexus", "c1", "upgrade", "2.1.400", "-y")
    sb.run("nexus", "ls", label="nexus ls  (after it)")


@case("nexus/upgrade-recreate")
def _(sb):
    # Both peers running on the old image, an agent in this shell: each is recreated through
    # `nexus <peer> restart` (without the lock's fd), its Claude defaults merged; the old image goes.
    peers(sb)
    templates(sb)
    old_claude(sb)
    agent(sb)
    sb.container("c1", image=OLD, sessions=sessions(sb))
    sb.container("c2", image=OLD)
    sb.container("g1", image=GROK_OLD)
    sb.run("nexus", "c2", "upgrade", "2.1.400", "-y", label="nexus c2 upgrade 2.1.400 -y  (from the peer with its own key)")


@case("nexus/upgrade-recreate-fails")
def _(sb):
    peers(sb)
    templates(sb)
    old_claude(sb)
    agent(sb)
    sb.container("c1", image=OLD)
    sb.container("c2", image=OLD)
    world(sb, compose={"up_rc": 17})
    sb.run("nexus", "c1", "upgrade", "2.1.400", "-y", label="nexus c1 upgrade 2.1.400 -y  (compose up fails)")


@case("nexus/upgrade-ssh")
def _(sb):
    # c1 needs your ssh-agent, c2 has its own key. SSH_AUTH_SOCK names no socket (a file), and no agent
    # is found in the usual places: c1 can't be recreated, so neither is c2.
    peers(sb)
    templates(sb)
    old_claude(sb)
    sb.write("not-a-socket", "")
    sb.container("c1", image=OLD)
    sb.container("c2", image=OLD)
    sb.run("nexus", "c1", "upgrade", "2.1.400", "-y", env={"SSH_AUTH_SOCK": sb.path("not-a-socket")},
           label="nexus c1 upgrade 2.1.400 -y  (SSH_AUTH_SOCK=<S>/not-a-socket)")
    # c1 stopped now: only c2 runs, and it needs no agent.
    sb.load_world()
    sb.world["containers"]["agent-c1"].update(state="exited", status="Exited (0) 2 minutes ago")
    sb.save_world()
    sb.run("nexus", "c1", "upgrade", "2.1.401", "-y", env={"SSH_AUTH_SOCK": sb.path("not-a-socket")},
           label="nexus c1 upgrade 2.1.401 -y  (c1 stopped, c2 running; still no agent)")


@case("nexus/upgrade-prompt")
def _(sb):
    # Without -y the recreate is asked for, on a terminal only.
    personas(sb, "c1:claude:docker")
    templates(sb)
    old_claude(sb)
    agent(sb)
    sb.container("c1", image=OLD, sessions=sessions(sb))
    sb.run("nexus", "c1", "upgrade", "2.1.400", label="nexus c1 upgrade 2.1.400  (stdin not a terminal: not asked, no)")
    sb.run("nexus", "c1", "upgrade", "2.1.401", mode="tty-in", steps=[("Recreate them now? [y/N] ", "n")],
           label="nexus c1 upgrade 2.1.401  (asked: n)")
    sb.run("nexus", "c1", "upgrade", "2.1.402", mode="tty-in", steps=[("Recreate them now? [y/N] ", "<EOF>")],
           label="nexus c1 upgrade 2.1.402  (asked: ^D)")
    sb.run("nexus", "c1", "upgrade", "2.1.403", mode="tty-in", steps=[("Recreate them now? [y/N] ", "y")],
           label="nexus c1 upgrade 2.1.403  (asked: y)")
    # All of it on the terminal: one stream, the prompt in its place, and no colours (upgrade has none).
    # c1 runs the image of 2.1.403 now, the old one after this build.
    sb.run("nexus", "c1", "upgrade", "2.1.404", mode="tty-all", steps=[("Recreate them now? [y/N] ", "n")],
           label="nexus c1 upgrade 2.1.404  (all on a terminal, asked: n)")


@case("nexus/upgrade-old-images")
def _(sb):
    # Which old images go: under the new images' repo only (a mirror's tag stays), an untagged one by its
    # id; one a running container still uses is kept. c1 needs an agent and there is none, so nothing is
    # recreated.
    personas(sb, "c1:claude:docker", "c2:claude:docker", "c3:claude:docker", extra=("--ssh", "c2:new", "--ssh", "c3:new"))
    templates(sb)
    old_claude(sb)
    image(sb, "mirror.invalid/agent-claude-docker:2.1.300", OLD, "2.1.300 (Claude Code)")
    untagged(sb, LOOSE, LOOSE_RUN)
    sb.container("c1", image=LOOSE_RUN)
    sb.container("c2", image=OLD)
    sb.container("c3", state="exited", image=LOOSE)
    sb.run("nexus", "c1", "upgrade", "2.1.400", "-y")


# --- failures: what is put back ------------------------------------------------------------------------
@case("nexus/upgrade-version-check")
def _(sb):
    personas(sb, "c1:claude:docker")
    templates(sb)
    old_claude(sb)
    sb.container("c1", image=OLD)
    world(sb, built_version="2.1.399 (Claude Code)")
    sb.run("nexus", "c1", "upgrade", "2.1.400", "-y", label="nexus c1 upgrade 2.1.400 -y  (the image has 2.1.399)")
    world(sb, built_version="")
    sb.run("nexus", "c1", "upgrade", "2.1.400", "-y", label="nexus c1 upgrade 2.1.400 -y  (claude --version prints nothing)")
    pin(sb)
    # The tag was there before, from an earlier build: it is pointed back at that image. Here it has a
    # second tag; in the fake an image whose last tag moves is gone (Docker keeps it, untagged), and the
    # tag back fails, as the next run shows.
    image(sb, CLAUDE_REPO + ":2.1.400", PREV, "2.1.400 (Claude Code)")
    image(sb, "mirror.invalid/agent-claude-docker:2.1.400", PREV, "2.1.400 (Claude Code)")
    world(sb, built_version="2.1.399 (Claude Code)")
    sb.run("nexus", "c1", "upgrade", "2.1.400", "-y", label="nexus c1 upgrade 2.1.400 -y  (2.1.399 again; :2.1.400 there before)")
    sb.load_world()
    del sb.world["images"]["mirror.invalid/agent-claude-docker:2.1.400"]
    sb.world["images"][CLAUDE_REPO + ":2.1.400"] = {"id": PREV, "version": "2.1.400 (Claude Code)"}
    sb.save_world()
    sb.run("nexus", "c1", "upgrade", "2.1.400", "-y", label="nexus c1 upgrade 2.1.400 -y  (the same; the old image's only tag)")
    pin(sb)


@case("nexus/upgrade-build-fails")
def _(sb):
    personas(sb, "c1:claude:docker")
    templates(sb)
    old_claude(sb)
    sb.container("c1", image=OLD)
    world(sb, compose={"build_rc": 4})
    sb.run("nexus", "c1", "upgrade", "2.1.400", "-y", label="nexus c1 upgrade 2.1.400 -y  (the build fails)")
    edit(sb, "a/.env", "CLAUDE_VERSION=latest\n", "CLAUDE_VERSION=2.1.300\n")
    sb.run("nexus", "c1", "upgrade", "2.1.400", "-y", label="nexus c1 upgrade 2.1.400 -y  (pinned to 2.1.300 before)")
    # No CLAUDE_VERSION line at all: the pin is appended, and what goes back is latest, the default.
    edit(sb, "a/.env", "CLAUDE_VERSION=2.1.300\n", "")
    sb.run("nexus", "c1", "upgrade", "2.1.400", "-y", label="nexus c1 upgrade 2.1.400 -y  (no CLAUDE_VERSION line)")
    pin(sb)


# --- tags ----------------------------------------------------------------------------------------------
def second_tag(sb, ref, extra):
    """Another tag, EXTRA, on the image REF names now. The fake drops an image whose last tag moves on
    (Docker keeps it, untagged), so a failed upgrade's tag back to it would fail; with a second tag the
    fake keeps it, as Docker would."""
    sb.load_world()
    sb.world["images"][extra] = dict(sb.world["images"][ref])
    sb.save_world()


@case("nexus/upgrade-tag-env")
def _(sb):
    # A fixed CLAUDE_TAG in .env: the image keeps that tag. The fake's build leaves no old image behind;
    # the old one is put among the untagged by hand, as Docker would leave it, so it goes by its id.
    personas(sb, "c1:claude:docker")
    templates(sb)
    old_claude(sb)
    untagged(sb, OLD)
    edit(sb, "a/.env", "#CLAUDE_TAG=\n", "CLAUDE_TAG=latest\n")
    world(sb, built_version="2.1.400 (Claude Code)")
    sb.run("nexus", "c1", "upgrade", "2.1.400", "-y", label="nexus c1 upgrade 2.1.400 -y  (CLAUDE_TAG=latest in .env)")
    edit(sb, "a/.env", "CLAUDE_TAG=latest\n", "CLAUDE_TAG=mine\n")
    world(sb, built_version="2.1.401 (Claude Code)")
    sb.run("nexus", "c1", "upgrade", "2.1.401", "-y", label="nexus c1 upgrade 2.1.401 -y  (CLAUDE_TAG=mine: no warning)")
    # The image doesn't have the version: the pin goes back, the tag back to the image it had.
    second_tag(sb, CLAUDE_REPO + ":mine", "mirror.invalid/agent-claude-docker:2.1.401")
    world(sb, built_version="2.1.400 (Claude Code)")
    sb.run("nexus", "c1", "upgrade", "2.1.402", "-y", label="nexus c1 upgrade 2.1.402 -y  (CLAUDE_TAG=mine; the image has 2.1.400)")
    pin(sb)
    # Without the fake's version for it, an image tagged latest has "latest (Claude Code)".
    edit(sb, "a/.env", "CLAUDE_TAG=mine\n", "CLAUDE_TAG=latest\n")
    sb.load_world()
    del sb.world["built_version"]
    sb.save_world()
    second_tag(sb, CLAUDE_REPO + ":latest", "mirror.invalid/agent-claude-docker:2.1.400")
    sb.run("nexus", "c1", "upgrade", "2.1.402", "-y", label="nexus c1 upgrade 2.1.402 -y  (CLAUDE_TAG=latest; the image has latest)")


@case("nexus/upgrade-tag-shell")
def _(sb):
    # CLAUDE_TAG exported in the shell beats .env's (commented out) line; the warning still names .env.
    personas(sb, "c1:claude:docker")
    templates(sb)
    world(sb, built_version="2.1.400 (Claude Code)")
    sb.run("nexus", "c1", "upgrade", "2.1.400", "-y", env={"CLAUDE_TAG": "latest"},
           label="nexus c1 upgrade 2.1.400 -y  (CLAUDE_TAG=latest exported)")
    second_tag(sb, CLAUDE_REPO + ":latest", "mirror.invalid/agent-claude-docker:2.1.400")
    sb.run("nexus", "c1", "upgrade", "2.1.401", "-y", env={"CLAUDE_TAG": "latest"},
           label="nexus c1 upgrade 2.1.401 -y  (the same; the image has 2.1.400)")


@case("nexus/upgrade-tag-legacy")
def _(sb):
    # A personas file from before upgrade: its image names no version, so the tag stays latest.
    personas(sb, "c1:claude:docker", "g1:grok:docker", extra=("--ssh", "g1:new"))
    templates(sb, c1=CLAUDE_REPO + ":latest")
    world(sb, built_version="2.1.400 (Claude Code)")
    sb.run("nexus", "c1", "upgrade", "2.1.400", "-y", label="nexus c1 upgrade 2.1.400 -y  (the image is always :latest)")
    # A .env from before upgrade had GROK_TAG=latest: for grok that is the same warning.
    edit(sb, "a/.env", "#GROK_TAG=\n", "GROK_TAG=latest\n")
    world(sb, built_version="grok 0.9.1")
    sb.run("nexus", "g1", "upgrade", "0.9.1", "-y", label="nexus g1 upgrade 0.9.1 -y  (GROK_TAG=latest in .env)")


# --- a registry, grok ----------------------------------------------------------------------------------
@case("nexus/upgrade-registry")
def _(sb):
    # REGISTRY not local: the images are pulled (the fake's images don't follow REGISTRY: their names are
    # set per run).
    personas(sb, "c1:claude:docker")
    edit(sb, "a/.env", "REGISTRY=local\n", "REGISTRY=registry.invalid:5000\n")
    templates(sb, c1="registry.invalid:5000/agent-claude-docker:{tag}")
    sb.run("nexus", "c1", "upgrade", "2.1.400", "-y", label="nexus c1 upgrade 2.1.400 -y  (REGISTRY=registry.invalid:5000 in .env)")
    world(sb, compose={"pull_rc": 5})
    sb.run("nexus", "c1", "upgrade", "2.1.401", "-y", label="nexus c1 upgrade 2.1.401 -y  (the pull fails)")
    pin(sb)
    # The shell's REGISTRY beats .env's.
    world(sb, compose={})
    templates(sb)
    sb.run("nexus", "c1", "upgrade", "2.1.401", "-y", env={"REGISTRY": "local"},
           label="nexus c1 upgrade 2.1.401 -y  (REGISTRY=local exported: built)")
    edit(sb, "a/.env", "REGISTRY=registry.invalid:5000\n", "REGISTRY=\n")
    sb.run("nexus", "c1", "upgrade", "2.1.402", "-y", label="nexus c1 upgrade 2.1.402 -y  (REGISTRY= empty in .env: local, built)")


@case("nexus/upgrade-grok")
def _(sb):
    # A grok persona: GROK_VERSION, grok --version, every grok persona; the Claude one is no peer.
    personas(sb, "c1:claude:docker", "g1:grok:docker", "g2:grok:docker", extra=("--ssh", "g1:new", "--ssh", "g2:none"))
    templates(sb, g2=GROK_REPO + ":{grok_tag}")
    image(sb, GROK_REPO + ":latest", GROK_OLD, "grok 0.9.0")
    old_claude(sb)
    sb.container("g1", image=GROK_OLD, sessions=sessions(sb))
    sb.container("g2", state="exited", image=GROK_OLD)
    sb.container("c1", image=OLD)
    world(sb, built_version="grok 0.9.1")
    sb.run("nexus", "g1", "upgrade", "0.9.1", "-y")
    world(sb, built_version="grok 0.9.0")
    sb.run("nexus", "g2", "upgrade", "0.9.2", "-y", label="nexus g2 upgrade 0.9.2 -y  (the image has grok 0.9.0)")


@case("nexus/upgrade-crlf-personas")
def _(sb):
    # A personas file with CRLF line ends (edited on Windows): its persona lines are read all the same.
    personas(sb, "c1:claude:docker", "c2:claude:docker", "g1:grok:docker", extra=("--ssh", "c2:new", "--ssh", "g1:new"))
    templates(sb)
    before_personas_json(sb)
    p = sb.path("a/compose.personas.yml")
    with open(p) as f:
        text = f.read()
    with open(p, "w") as f:
        f.write(text.replace("\n", "\r\n"))
    move_lines(sb)
    sb.container("c2", image=OLD)
    old_claude(sb)
    sb.run("nexus", "c2", "upgrade", "2.1.400", "-y")


@case("nexus/upgrade-out-of-step")
def _(sb):
    # A compose.personas.yml edited by hand (or written by an older config-tools): upgrade builds from it and may
    # recreate the containers, so it refuses before any pin, as a start does (M4: new, from the Python).
    personas(sb, "c1:claude:docker")
    templates(sb)
    with open(sb.path("a/compose.personas.yml"), "a") as f:
        f.write("# a line added by hand\n")
    sb.run("nexus", "c1", "upgrade", "2.1.400", "-y")
    pin(sb)
