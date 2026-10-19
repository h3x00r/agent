"""Review scenarios (adversarial): nexus <persona> subcommands, preflight, the Claude defaults merge, the
cwd mapping and the attach, where the existing life-/basic- scenarios are thin."""
import os

from scen import before_personas_json, case, images, move_lines, two_personas  # noqa: F401
from scen.nexus_edge_ls import no_cnf
from scen.nexus_lifecycle import LEGACY_CLAUDE_JSON, LEGACY_STATUSLINE, append, defaults, edit, unix_socket, world


def gone_wrapper(sb, rel, nopwd=False):
    """A wrapper that runs nexus in a directory it deleted first ($1), with PWD unset if NOPWD."""
    return sb.script(rel, 'd="$1"; shift\nmkdir -p "$d" && cd "$d" && rmdir "$d" && exec %s"%s/nexus" "$@"'
                     % ("env -u PWD " if nopwd else "", sb.a))


# --- the persona's line ----------------------------------------------------------------------------
@case("nexus/edge-persona-meta-empty")
def _(sb):
    # A `# persona: c1 ` line with nothing after the name, before c1's real one; and c2 with only that.
    sb.personas("c1:claude:docker")
    before_personas_json(sb)
    p = sb.path("a/compose.personas.yml")
    with open(p) as f:
        text = f.read()
    with open(p, "w") as f:
        f.write(text.replace("# persona: c1 ", "# persona: c1 \n# persona: c2 \n# persona: c1 ", 1))
    move_lines(sb)
    sb.run("nexus", "c1", "start", label="nexus c1 start  (first c1 line bare)")
    sb.run("nexus", "c2", "start", label="nexus c2 start  (c2 has a bare line only)")


# --- session names ---------------------------------------------------------------------------------
@case("nexus/edge-persona-session-newline")
def _(sb):
    sb.personas("c1:claude:docker")
    sb.container("c1")
    os.makedirs(sb.projects + "/app\n")
    os.makedirs(sb.projects + "/\n\n")
    sb.run("nexus", "c1", cwd="projects/app\n", label="nexus c1  (in <P>/$'app\\n')")
    sb.run("nexus", "c1", cwd="projects/\n\n", label="nexus c1  (in <P>/$'\\n\\n')")
    sb.run("nexus", "c1", "s\n", cwd="projects/app\n", label="nexus c1 $'s\\n'")


@case("nexus/edge-persona-attach-args")
def _(sb):
    sb.personas("c1:claude:docker")
    sb.container("c1")
    sb.write("projects/app/.keep", "")
    for args in (("--", "-x", "--y", ""), ("", "--", "a"), ("--", "--", "x"),
                 ("-s", "--", "a b", "a\nb", "'q'", '"dq"', "$HOME", "*"), ("--",), (".:",), ("a\nb",),
                 ("--x", "--"), ("x", "y", "--", "z")):
        sb.run("nexus", "c1", *args, cwd="projects/app", label="nexus c1 %r" % (args,))


# --- where it starts -------------------------------------------------------------------------------
@case("nexus/edge-persona-cwd-spellings")
def _(sb):
    sb.personas("c1:claude:docker")
    sb.container("c1")
    sb.write("projects/app/.keep", "")
    sb.write("projects/x/.keep", "")
    P = sb.projects
    for pwd, what in ((P + "/app/./", "app/./"), (P + "/x/../app", "x/../app"), (P + "/app/", "app/"),
                      ("/" + P + "/app", "//<P>/app"), (sb.s + "//projects/app", "<S>//projects/app"),
                      (P + "/x", "stale: <P>/x"), ("app", "relative"), (None, "unset")):
        sb.run("nexus", "c1", cwd="projects/app", env={"PWD": pwd}, label="nexus c1  (in <P>/app, PWD %s)" % what)
    for root, what in ((sb.s + "//projects", "<S>//projects"), (P + "//", "<P>//"), (P + "/.", "<P>/.")):
        sb.run("nexus", "c1", cwd="projects/app", env={"PROJECTS_ROOT": root},
               label="nexus c1  (in <P>/app, PROJECTS_ROOT=%s)" % what)
    sb.run("nexus", "c1", cwd="projects/app", env={"PROJECTS_ROOT": P + "/app"},
           label="nexus c1  (in <P>/app, PROJECTS_ROOT=<P>/app: another root)")


@case("nexus/edge-persona-pwd-unset-deleted")
def _(sb):
    # The shell's dir deleted and no PWD at all: bash's $PWD is unset (getcwd fails), and set -u stops it.
    sb.personas("c1:claude:docker")
    sb.container("c1")
    w = gone_wrapper(sb, "wrap/gone-nopwd", nopwd=True)
    sb.run(w, sb.projects + "/gone", "c1", label="(in <P>/gone, deleted, no PWD) nexus c1")
    sb.run(w, sb.projects + "/gone", "c1", "start", label="(in <P>/gone, deleted, no PWD) nexus c1 start")


@case("nexus/edge-persona-printf-q")
def _(sb):
    # The deleted-dir warning quotes $PWD with printf %q.
    sb.personas("c1:claude:docker")
    sb.container("c1")
    w = gone_wrapper(sb, "wrap/gone")
    for name, what in (("x y", "x<U+2028>y"), ("a b'c", "a b'c"), ("͸z", "<U+0378>z"),
                       ("tab\there", "tab<TAB>here"), ("café", "cafe-acute")):
        sb.run(w, sb.projects + "/" + name, "c1", "start", label="(in a deleted <P>/%s) nexus c1 start" % what)


# --- preflight -------------------------------------------------------------------------------------
@case("nexus/edge-persona-refusal-order", fakes=("ddev",))
def _(sb):
    sb.personas("c1:claude:docker", "g1:grok:docker", "d1:claude:ddev", extra=("--ssh", "g1:new"))
    os.unlink(sb.path("a/personas/g1.key"))
    os.unlink(sb.path("a/personas/g1.grok.toml"))
    os.unlink(sb.path("a/personas/claude.managed.json"))
    append(sb, "a/compose.personas.yml", LEGACY_STATUSLINE + LEGACY_CLAUDE_JSON)
    sb.run("nexus", "g1", "start", label="nexus g1 start  (all wrong)")
    sb.run("nexus", "g1", "start", env={"PROJECTS_ROOT": "rel"}, label="nexus g1 start  (all wrong, root relative)")
    sb.run("nexus", "d1", "start", label="nexus d1 start  (no CA, legacy file)")
    sb.run("nexus", "c1", "ls", label="nexus c1 ls  (all wrong, not running)")
    edit(sb, "a/compose.personas.yml", LEGACY_STATUSLINE, "")
    sb.run("nexus", "c1", "start", label="nexus c1 start  (legacy claude.json, managed gone)")
    edit(sb, "a/compose.personas.yml", LEGACY_CLAUDE_JSON, "")
    sb.run("nexus", "c1", "start", label="nexus c1 start  (managed gone)")
    sb.run("nexus", "g1", "restart", label="nexus g1 restart  (key, grok.toml gone)")
    sb.write("a/personas/g1.key", "k\n", 0o600)
    sb.run("nexus", "g1", "start", label="nexus g1 start  (grok.toml gone)")


@case("nexus/edge-persona-ddev-home-file", fakes=("ddev",))
def _(sb):
    sb.personas("d1:claude:ddev")
    sb.write("home/.ddev", "a file\n")
    sb.run("nexus", "d1", "start", label="nexus d1 start  (~/.ddev a file)")
    sb.run("nexus", "d1", "start", env={"HOME": None}, label="nexus d1 start  (no HOME)")


@case("nexus/edge-persona-umask", fakes=("ddev",))
def _(sb):
    # umask 177: mkdir -p still makes the parents it needs (u+wx); the last dir is 0600.
    sb.personas("g1:grok:docker", "d1:grok:ddev", extra=("--ssh", "g1:none", "--ssh", "d1:none"))
    w = sb.script("wrap/umask", 'umask 0177; exec "%s/nexus" "$@"' % sb.a)
    sb.run(w, "g1", "start", env={"PROJECTS_ROOT": sb.path("fresh/a/b")},
           label="(umask 177) nexus g1 start  (PROJECTS_ROOT=<S>/fresh/a/b)")
    sb.run(w, "d1", "start", label="(umask 177) nexus d1 start  (~/.local missing)")


# --- signals ---------------------------------------------------------------------------------------
@case("nexus/edge-persona-sigint-ignored")
def _(sb):
    sb.personas("c1:claude:docker")
    world(sb, record_sigign=True)
    sb.write("projects/app/.keep", "")
    w = sb.script("wrap/no-int", "trap '' INT\nexec \"%s/nexus\" \"$@\"" % sb.a)
    sb.run(w, "c1", "start", cwd="projects/app", label="(SIGINT ignored) nexus c1 start")
    sb.run(w, "c1", "stop", label="(SIGINT ignored) nexus c1 stop")
    sb.run(w, "c1", cwd="projects/app", label="(SIGINT ignored) nexus c1")
    sb.run("nexus", "c1", cwd="projects/app", label="nexus c1  (as usual)")


# --- the Claude defaults ---------------------------------------------------------------------------
@case("nexus/edge-persona-defaults-nul")
def _(sb):
    sb.personas("c1:claude:docker")
    defaults(sb, '{"a\\u0000b": 1, "theme": "dark"}\n')
    sb.run("nexus", "c1", "start")


@case("nexus/edge-persona-defaults-unreadable")
def _(sb):
    sb.personas("c1:claude:docker")
    f = sb.path("a/personas/claude.json")
    w = sb.script("wrap/unreadable", 'chmod 000 "%s"; "%s/nexus" "$@"; rc=$?; chmod 644 "%s"; exit $rc' % (f, sb.a, f))
    sb.run(w, "c1", "start", label="nexus c1 start  (personas/claude.json mode 000)")


@case("nexus/edge-persona-defaults-exec")
def _(sb):
    sb.personas("c1:claude:docker")
    for spec, what in (({"rc": 125, "err": "Error response from daemon: oops\n"}, "exit 125"),
                       ({"rc": 126, "err": "OCI runtime exec failed\n"}, "exit 126"),
                       ({"rc": 127, "err": "exec: python3: not found\n"}, "exit 127"),
                       ({"rc": 3, "out": "custom three\n\n\n"}, "exit 3"),
                       ({"rc": 0, "out": "  \n"}, "exit 0, blanks"),
                       ({"rc": 0, "out": "\r\n"}, "exit 0, CR"),
                       ({"rc": 1}, "exit 1, silent"),
                       ({"signal": 9, "out": "before kill\n"}, "killed")):
        world(sb, py_exec={"agent-c1": spec})
        sb.run("nexus", "c1", "restart", label="nexus c1 restart  (merge: %s)" % what)


@case("nexus/edge-persona-defaults-timeout")
def _(sb):
    sb.personas("c1:claude:docker")
    world(sb, py_exec={"agent-c1": {"out": "slow\n", "sleep": 25}})
    sb.run("nexus", "c1", "start", timeout=40, label="nexus c1 start  (merge hangs 25 s)")


@case("nexus/edge-persona-defaults-timeout-child")
def _(sb):
    # docker exec whose own child holds stdout: timeout(1) kills the whole process group.
    sb.personas("c1:claude:docker")
    world(sb, py_exec={"agent-c1": {"out": "slow\n", "child_sleep": 45}})
    sb.run("nexus", "c1", "start", timeout=38, label="nexus c1 start  (merge hangs in a child 45 s)")


# --- docker itself -----------------------------------------------------------------------------------
@case("nexus/edge-persona-docker-missing", norm=no_cnf)
def _(sb):
    sb.personas("c1:claude:docker")
    os.unlink(os.path.join(sb.bin, "docker"))
    sb.run("nexus", "c1", "start", label="nexus c1 start  (no docker on PATH)")
    sb.run("nexus", "c1", "stop", label="nexus c1 stop  (the same)")
    sb.run("nexus", "c1", "ls", label="nexus c1 ls  (the same)")


@case("nexus/edge-persona-states")
def _(sb):
    sb.personas("c1:claude:docker")
    sb.write("projects/app/.keep", "")
    for st in ("restarting", "dead", "paused", "created"):
        sb.container("c1", state=st, status=st)
        sb.run("nexus", "c1", cwd="projects/app", label="nexus c1  (container %s)" % st)


# --- management pass-throughs ------------------------------------------------------------------------
@case("nexus/edge-persona-mgmt-extra")
def _(sb):
    two_personas(sb)
    images(sb)
    sock = unix_socket(sb, "s")
    os.makedirs(sb.path("adir"))
    sb.write("afile", "x\n")
    sb.symlink("slink", sock)
    sb.symlink("dlink", sb.path("nowhere"))
    sb.run("nexus", "c1", "stop", label="nexus c1 stop  (no container)")
    sb.run("nexus", "c1", "stop", "-t", "3")
    sb.run("nexus", "c1", "logs", "--since", "1h")
    sb.run("nexus", "c1", "pull", "--policy", "missing")
    sb.run("nexus", "c1", "build", "--", "x")
    for v, what in (("", "empty"), (None, "unset"), (sb.path("nowhere"), "missing"), (sb.path("afile"), "a file"),
                    (sb.path("adir"), "a dir"), (sb.path("slink"), "a link to a socket"), (sb.path("dlink"), "dangling")):
        sb.run("nexus", "g1", "stop", env={"SSH_AUTH_SOCK": v}, label="nexus g1 stop  (SSH_AUTH_SOCK %s)" % what)
        sb.run("nexus", "c1", "restart", env={"SSH_AUTH_SOCK": v}, label="nexus c1 restart  (SSH_AUTH_SOCK %s)" % what)
    sb.run("nexus", "c1", "start", "extra", "args")
    sb.run("nexus", "c1", "restart", "--x")
    sb.run("nexus", "c1", "ls", "extra")
