"""nexus before and around a persona: usage, errors, ls, top."""
import re

from scen import NO_LINK, before_personas_json, case, images, move_lines, snapshot_line, two_personas  # noqa: F401


def hms(text):
    """top's clock in a coloured or a live frame. golden.py's own rule only knows the plain header line:
    the transcript has ESC as \\e by the time normalisers run, and a live frame starts with screen codes."""
    return re.sub(r"(nexus top(?:\\e\[[0-9;]*m)?  \S+  )\d\d:\d\d:\d\d", r"\1<HH:MM:SS>", text)


def snap_fields(n, **kw):
    """The first N fields of a snapshot line: one the statusline cut short."""
    return "\t".join(snapshot_line(**kw).split("\t")[:n])


def snap_with(i, value, **kw):
    """A snapshot line with field I (0 = the format) replaced by VALUE, which may be empty."""
    f = snapshot_line(**kw).split("\t")
    f[i] = value
    return "\t".join(f)


# --- usage, names, no personas --------------------------------------------------------------------
@case("nexus/basic-usage")
def _(sb):
    sb.run("nexus")
    sb.run("nexus", "--help")
    sb.run("nexus", "-h")
    sb.run("nexus", "--help", "extra")   # only the first argument is looked at


@case("nexus/basic-help-after-persona")
def _(sb):
    # After a persona, --help is no option: it is the session's name, and nexus attaches to it.
    two_personas(sb)
    sb.container("c1")
    sb.run("nexus", "c1", "--help")
    sb.run("nexus", "c1", "-h")
    sb.run("nexus", "Bad!", "--help")
    sb.run("nexus", "nope", "--help")
    sb.run("nexus", "top", "--help")
    sb.run("nexus", "ls", "--help")     # ls takes no arguments, and ignores any
    sb.run("nexus", "ls", "junk")


@case("nexus/basic-not-set-up")
def _(sb):
    sb.run("nexus", "ls")
    sb.run("nexus", "c1")
    sb.run("nexus", "top", "--once")
    sb.run("nexus", "top", "-n", "0")   # top's arguments are checked before the personas file
    sb.run("nexus", "c1", "ls")
    sb.run("nexus", "Bad!")             # the personas file is looked for before the name is checked


@case("nexus/basic-personas-empty")
def _(sb):
    # A personas file without one "# persona:" line.
    sb.write("a/compose.personas.yml", "services: {}\n")
    sb.run("nexus", "ls")
    sb.run("nexus", "c1")
    sb.run("nexus", "top", "--once")


@case("nexus/basic-names")
def _(sb):
    two_personas(sb)
    sb.run("nexus", "Bad!")
    sb.run("nexus", "nope")
    sb.run("nexus", "")
    sb.run("nexus", "-x")
    sb.run("nexus", "UPPER")
    sb.run("nexus", "c_1")
    sb.run("nexus", "c1 x")
    sb.run("nexus", "c")        # a prefix of c1
    sb.run("nexus", "c1-")      # nexus's rule takes it; no persona has it
    sb.run("nexus", "1abc")
    sb.run("nexus", "all")


@case("nexus/basic-crlf-personas")
def _(sb):
    # A personas file with CRLF line ends (edited on Windows, say): read as without them.
    two_personas(sb)
    before_personas_json(sb)
    with open(sb.a + "/compose.personas.yml") as f:
        text = f.read()
    sb.write("a/compose.personas.yml", text.replace("\n", "\r\n"))
    move_lines(sb)
    sb.container("c1")
    sb.run("nexus", "ls")
    sb.run("nexus", "nope")
    sb.run("nexus", "c1", "ls")
    sb.run("nexus", "g1", "ls")


# --- ls ----------------------------------------------------------------------------------------------
@case("nexus/basic-ls-not-started")
def _(sb):
    two_personas(sb)
    sb.run("nexus", "ls")


@case("nexus/basic-ls-running")
def _(sb):
    two_personas(sb)
    sb.container("c1", sessions=[
        {"name": "main", "windows": 1, "attached": 0, "path": sb.projects},
        {"name": "billing", "windows": 2, "attached": 1, "path": sb.projects + "/billing"}])
    sb.container("g1", state="exited")
    sb.run("nexus", "ls")
    sb.run("nexus", "c1", "ls")
    sb.run("nexus", "g1", "ls")


@case("nexus/basic-ls-states")
def _(sb):
    sb.personas(*["c%d:claude:docker" % i for i in range(1, 9)], "g1:grok:docker", extra=("--ssh", "g1:none"))
    sb.container("c1", status="Up 1 hour")                                    # running, no health check
    sb.container("c2", status="Up 1 hour (unhealthy)")
    sb.container("c3", status="Up 2 seconds (health: starting)")
    sb.container("c4", state="restarting", status="Restarting (1) 5 seconds ago")
    sb.container("c5", state="paused", status="Up 1 hour (Paused)")
    sb.container("c6", state="created", status="Created")
    sb.container("c7", state="dead", status="Dead")
    sb.container("c8", state="exited", status="Exited (137) 3 days ago")
    sb.run("nexus", "ls")              # g1: not started, and no ssh
    sb.run("nexus", "c3", "ls")        # starting: it runs, so it has sessions
    sb.run("nexus", "c4", "ls")        # restarting: not running
    sb.run("nexus", "c5", "ls")        # paused: not running


@case("nexus/basic-ls-session-rows")
def _(sb):
    two_personas(sb)
    p = sb.projects
    sb.container("c1", sessions=[
        {"name": "main", "windows": 1, "attached": 0, "path": p},
        {"name": "w", "windows": "x2", "attached": "3", "path": p + "/w"},           # windows not a number: -
        {"name": "zero-lead", "windows": 12, "attached": "01", "path": p + "/z"},   # attached 01: no
        {"name": "word", "windows": 1, "attached": "abc", "path": p + "/"},
        {"name": "", "windows": "", "attached": "", "path": ""},                     # nothing at all: no row
        {"name": "", "windows": 1, "attached": 0, "path": ""},                       # no name, no dir: -
        {"name": "my proj", "windows": 1, "attached": 2, "path": p + "/my proj"},
        {"name": "a-rather-long-session-name", "windows": 100, "attached": 0, "path": p + "/long"}])
    sb.container("g1", sessions=[])                                                   # tmux, no session
    sb.run("nexus", "ls")
    sb.run("nexus", "c1", "ls")
    sb.run("nexus", "g1", "ls")


EVIL_DIR = "/q\n\x1b]0;pwned\x07\x1b[2Jevil\tcol:1:1:x\u009d0;c1\u009c"


@case("nexus/basic-ls-control-chars")
def _(sb):
    # A session whose name and dir hold a newline, OSC and CSI sequences, BEL, a tab, DEL and C1 bytes
    # (U+009D, U+009C): cleaned, and one row each, so a persona can't drive the terminal or forge a row.
    two_personas(sb)
    sb.container("c1", sessions=[
        {"name": "main", "windows": 1, "attached": 0, "path": sb.projects},
        {"name": "e\x1b[31mvil\x1b[0m\x07\x7f\u009dx", "windows": 1, "attached": 0, "path": sb.projects + EVIL_DIR}])
    sb.run("nexus", "ls")
    sb.run("nexus", "c1", "ls")
    sb.run("nexus", "ls", mode="tty-all")


@case("nexus/basic-ls-orphan")
def _(sb):
    two_personas(sb)
    sb.container("c1")
    sb.container("old", state="exited")                        # this checkout's, no persona has it
    sb.container("gone", status="Up 3 hours (healthy)")        # the same, still running
    sb.container("z1", workdir="/elsewhere/agents")            # another checkout's: not this ls's business
    sb.run("nexus", "ls")


@case("nexus/basic-ls-drifted-root")
def _(sb):
    two_personas(sb)
    sb.container("c1", root=sb.path("old"))
    sb.container("g1")
    sb.run("nexus", "ls")                                                    # .env's root
    sb.run("nexus", "c1", "ls")                                              # the same, as a warning
    sb.run("nexus", "ls", env={"PROJECTS_ROOT": sb.path("new")}, label="PROJECTS_ROOT=<S>/new nexus ls")
    sb.run("nexus", "c1", "ls", env={"PROJECTS_ROOT": sb.path("new")}, label="PROJECTS_ROOT=<S>/new nexus c1 ls")
    # Another spelling of g1's root is the same root: no note for it.
    sb.run("nexus", "ls", env={"PROJECTS_ROOT": sb.s + "//projects/"}, label="PROJECTS_ROOT=<S>//projects/ nexus ls")


@case("nexus/basic-ls-own-root")
def _(sb):
    # A persona with a projects root of its own (root= in its line): the note names no source.
    sb.personas("c1:claude:docker:" + sb.path("own"), "g1:grok:docker")
    sb.container("c1")                                         # created with <P>, configured with <S>/own
    sb.container("g1", root=sb.path("own"))
    sb.run("nexus", "ls")
    sb.run("nexus", "c1", "ls")
    sb.run("nexus", "ls", env={"PROJECTS_ROOT": sb.path("own")}, label="PROJECTS_ROOT=<S>/own nexus ls")


@case("nexus/basic-ls-tmux-down")
def _(sb):
    two_personas(sb)
    sb.container("c1", tmux_down=True)
    sb.container("g1")
    sb.run("nexus", "ls")
    sb.run("nexus", "c1", "ls")
    sb.run("nexus", "g1", "ls")


@case("nexus/basic-ls-tty")
def _(sb):
    # On a terminal: the header dim, each state in its colour; NO_COLOR=1 turns them off, an empty one
    # doesn't. Widths count characters, but bytes in the C locale (the é rows).
    sb.personas("c1:claude:docker", "c2:claude:docker", "c3:claude:docker", "c4:claude:docker", "g1:grok:docker")
    sb.container("c1", sessions=[{"name": "main", "windows": 1, "attached": 0, "path": sb.projects},
                                 {"name": "café", "windows": 1, "attached": 1, "path": sb.projects + "/café"}])
    sb.container("c2", status="Up 1 hour (unhealthy)")
    sb.container("c3", status="Up 2 seconds (health: starting)")
    sb.container("c4", state="exited")
    sb.run("nexus", "ls", mode="tty-all")
    sb.run("nexus", "ls", mode="tty-all", env={"NO_COLOR": "1"}, label="NO_COLOR=1 nexus ls")
    sb.run("nexus", "ls", mode="tty-all", env={"NO_COLOR": ""}, label="NO_COLOR= nexus ls")
    sb.run("nexus", "ls", mode="tty-all", env={"LC_ALL": "C"}, label="LC_ALL=C nexus ls")
    sb.run("nexus", "ls", env={"LC_ALL": "C"}, label="LC_ALL=C nexus ls")
    sb.run("nexus", "c1", "ls", mode="tty-all")
    sb.run("nexus", "c1", "ls", mode="tty-all", env={"NO_COLOR": "1"}, label="NO_COLOR=1 nexus c1 ls")


# --- top ---------------------------------------------------------------------------------------------
@case("nexus/basic-top-once")
def _(sb):
    two_personas(sb)
    sb.container("c1", panes=[["%3", "1.0", "billing", snapshot_line()]], cpu="1.25%", mem="210MiB / 7.6GiB")
    sb.run("nexus", "top", "--once")


@case("nexus/basic-top-no-panes")
def _(sb):
    two_personas(sb)
    sb.run("nexus", "top", "--once")                           # nothing runs
    sb.container("c1")
    sb.run("nexus", "top", "--once")                           # c1 runs, without a claude pane
    sb.container("g1", status="Up 5 minutes (unhealthy)", cpu="", mem="")   # docker stats without figures
    sb.run("nexus", "top", "--once")


@case("nexus/basic-top-two-panes")
def _(sb):
    # A session with more than one CLI pane is shown per pane, as session:window.pane.
    two_personas(sb)
    sb.container("c1", panes=[
        ["%3", "1.0", "billing", snapshot_line()],
        ["%4", "1.1", "billing", snapshot_line(model="Sonnet 5", used=150000, size=1000000, pct=15, five=55,
                                               week=81, cost="12.3456", age=9000)],
        ["%7", "2.0", "api", snapshot_line(age=45 * 60 + 30)],
        ["%9", "3.0x", "api", snapshot_line(age=45 * 60 + 30)],          # only digits and dots of it
        ["%12", "0.0", "docs", snapshot_line(age=9000)]])
    sb.run("nexus", "top", "--once")


@case("nexus/basic-top-snapshot-figures")
def _(sb):
    two_personas(sb)
    sb.container("c1", panes=[
        ["%1", "0.0", "tok-small", snapshot_line(used=640, pct=0)],
        ["%2", "0.0", "tok-edge", snapshot_line(used=999500, size=1000000, pct=99)],
        ["%3", "0.0", "tok-big", snapshot_line(used=1250000, size=2000000, pct=62)],
        ["%4", "0.0", "tok-mega", snapshot_line(used=1049999, size=1000000, pct=100)],
        ["%5", "0.0", "cost-up", snapshot_line(cost="0.9950")],
        ["%6", "0.0", "cost-down", snapshot_line(cost="3.2049")],
        ["%7", "0.0", "cost-zero", snapshot_line(cost="0.0000")],
        ["%8", "0.0", "cost-int", snapshot_line(cost="12")],
        ["%9", "0.0", "cost-2dp", snapshot_line(cost="1.23")],
        ["%10", "0.0", "long-model", snapshot_line(model="Claude Opus 5.5 (1M context)")],
        ["%11", "0.0", "future", snapshot_line(age=-3600)],            # a clock ahead: 0s
        ["%12", "0.0", "minutes", snapshot_line(age=45 * 60 + 30)],
        ["%13", "0.0", "hours", snapshot_line(age=9000)],
        ["%14", "0.0", "limits", snapshot_line(five=100, week=0)],
        ["%15", "0.0", "lead-zero", snapshot_line(five="007", week="010")]])
    sb.run("nexus", "top", "--once")


@case("nexus/basic-top-snapshot-garbled")
def _(sb):
    two_personas(sb)
    sb.container("c1", panes=[
        ["%1", "0.0", "nosnap", "-"],                                   # no snapshot yet
        ["%2", "0.0", "v0", snap_with(0, "v0")],                        # a format it doesn't know
        ["%3", "0.0", "bad-time", snap_with(1, "yesterday")],
        ["%4", "0.0", "short", snap_fields(2)],                         # format and time only
        ["%5", "0.0", "junk", snapshot_line(model="Op\x1b[2Jus\x07", used="lots", size=0, pct="x", five="-3",
                                            week="1e3", cost="$1.2345")],
        ["%6", "0.0", "zero-size", snapshot_line(size=0)],
        ["%7", "0.0", "negative", snapshot_line(used=-5)],
        ["%8", "0.0", "no-model", snap_with(2, "")],                    # an empty field: the rest move up one
        ["%9", "0.0", "e\x1b]0;x\x07vil\u009dname", snapshot_line()]])  # the session's name, cleaned
    sb.run("nexus", "top", "--once")


@case("nexus/basic-top-octal")
def _(sb):
    # A limit or a time with a leading zero. The bash took them for octal: 5H "09" printed an arithmetic
    # error (with a line number of nexus) and the row still showed 09%, and a time like "0999" ended top
    # with exit 1 before any table. The Python reads them as decimal (accepted@M3); the time is two days
    # back, as a leading-zero epoch, so the row says 2d on any day.
    two_personas(sb)
    sb.container("c1", panes=[["%1", "0.0", "five-09", snapshot_line(five="09")]])
    sb.run("nexus", "top", "--once")
    ts = "0" + snapshot_line(age=2 * 86400).split("\t")[1]
    sb.container("c1", panes=[["%1", "0.0", "ok", snapshot_line()], ["%2", "0.0", "ts-0lead", snap_with(1, ts)]])
    sb.run("nexus", "top", "--once")


@case("nexus/basic-top-grok")
def _(sb):
    # grok's statusline sends no limits ("-"), and a cost only once the session has a price.
    two_personas(sb)
    sb.container("c1")
    sb.container("g1", panes=[
        ["%1", "0.0", "gtop", snapshot_line(model="Grok 4.6", used=1324, size=500000, pct=0, five="-", week="-",
                                            cost="-", age=9000)],
        ["%2", "0.0", "gnew", snapshot_line(model="Grok 4.6", used="-", size="-", pct="-", five="-", week="-",
                                            cost="-", age=9000)],
        ["%3", "0.0", "gpaid", snapshot_line(model="Grok 4.6", used=250000, size=500000, pct=50, five="-",
                                             week="-", cost="0.4321", age=9000)]])
    sb.run("nexus", "top", "--once")


@case("nexus/basic-top-long-sessions")
def _(sb):
    # Persona and session share 26 columns, so with 2-character persona names a session gets 24.
    two_personas(sb)
    sb.container("c1", panes=[
        ["%1", "0.0", "abcdefghijklmnopqrstuvwx", snapshot_line()],      # 24: as it is
        ["%2", "0.0", "abcdefghijklmnopqrstuvwxy", snapshot_line()],     # 25: cut, with a ~
        ["%3", "0.0", "twenty-two-characters!", snapshot_line()],        # two panes: the label counts
        ["%4", "0.1", "twenty-two-characters!", snapshot_line()],
        ["%5", "0.0", "é" * 25, snapshot_line()]])                       # cut by characters (bytes in C)
    sb.run("nexus", "top", "--once")
    sb.run("nexus", "top", "--once", env={"LC_ALL": "C"}, label="LC_ALL=C nexus top --once")


@case("nexus/basic-top-long-persona")
def _(sb):
    # A persona name longer than 18 leaves every session 8 columns.
    sb.personas("c1:claude:docker", "x1-a-really-long-name:claude:docker")
    sb.container("c1", panes=[
        ["%1", "0.0", "payments", snapshot_line()],                      # 8: as it is
        ["%2", "0.0", "payments2", snapshot_line()],                     # 9: cut
        ["%3", "1.0", "billing", snapshot_line()],
        ["%4", "1.1", "billing", snapshot_line()],
        ["%5", "100.100", "api", snapshot_line()],                       # a label longer than the room left
        ["%6", "100.101", "api", snapshot_line()]])
    sb.container("x1-a-really-long-name", panes=[["%1", "0.0", "main", snapshot_line()]])
    sb.run("nexus", "top", "--once")


@case("nexus/basic-top-statusline-off")
def _(sb):
    sb.personas("c1:claude:docker")
    sb.personas("g1:grok:docker", "c2:claude:docker", extra=("--no-statusline",))
    sb.container("c1", panes=[["%1", "0.0", "main", snapshot_line()]])
    sb.container("g1")
    sb.container("c2", panes=[["%1", "0.0", "main", "-"]])
    sb.run("nexus", "top", "--once")


@case("nexus/basic-top-args")
def _(sb):
    two_personas(sb)
    for args in (["-n", "0"], ["-n", "x"], ["-n"], ["-n", "1.5"], ["-n", "05"], ["--interval", "0"],
                 ["--interval=5"], ["--bogus"], ["--once", "extra"], ["-n", "10", "-n"]):
        sb.run("nexus", "top", *args)
    sb.run("nexus", "top", "-n", "10", "--once")
    sb.run("nexus", "top", "--interval", "3", "--once", "--once")
    sb.run("nexus", "top")            # not on a terminal: one frame, as --once
    sb.run("nexus", "top", "-n", "2")


@case("nexus/basic-top-live", norm=hms)
def _(sb):
    # The live view on a terminal: the alternate screen, no cursor, no wrapping; q quits and puts the
    # terminal back. Ctrl-C does the same, with exit 0. (q comes with a CR, as the driver types it: both
    # reach the terminal while read -s has its echo off.)
    two_personas(sb)
    sb.container("c1", panes=[["%3", "1.0", "billing", snapshot_line(pct=85, five=55, week=10)]])
    sb.run("nexus", "top", mode="tty-all", steps=[("q quits", "q")])
    sb.run("nexus", "top", "-n", "7", mode="tty-all", steps=[("q quits", "<INT>")])
    sb.run("nexus", "top", mode="tty-all", env={"NO_COLOR": "1"}, steps=[("q quits", "Q")],
           label="NO_COLOR=1 nexus top")


@case("nexus/basic-top-tty", norm=hms)
def _(sb):
    two_personas(sb)
    sb.container("c1", panes=[["%3", "1.0", "billing", snapshot_line(pct=85, five=55, week=10)],
                              ["%4", "0.0", "café", snapshot_line(pct=49, five=50, week=79, model="Opus é")]])
    sb.container("g1", status="Up 2 seconds (health: starting)")
    sb.run("nexus", "top", "--once", mode="tty-all")
    sb.run("nexus", "top", "--once", mode="tty-all", env={"NO_COLOR": "1"}, label="NO_COLOR=1 nexus top --once")
    sb.run("nexus", "top", "--once", mode="tty-all", env={"LC_ALL": "C"}, label="LC_ALL=C nexus top --once")


# --- where nexus runs from ---------------------------------------------------------------------------
@case("nexus/basic-agents-dir")
def _(sb):
    two_personas(sb)
    sb.container("old", state="exited")                       # this checkout's orphan, by its real path
    sb.write("empty/.keep", "")
    sb.symlink("link-to-a", sb.a)
    with open(sb.a + "/nexus") as f:
        sb.write("other/nexus", f.read(), 0o755)
    sb.run("nexus", "ls", env={"AGENTS_DIR": sb.path("empty")}, label="AGENTS_DIR=<S>/empty nexus ls")
    sb.run("nexus", "ls", env={"AGENTS_DIR": sb.path("nope")}, label="AGENTS_DIR=<S>/nope nexus ls")
    sb.run("nexus", "nope", env={"AGENTS_DIR": "../a"}, label="AGENTS_DIR=../a nexus nope")
    sb.run("nexus", "nope", env={"AGENTS_DIR": sb.a + "/"}, label="AGENTS_DIR=<A>/ nexus nope")
    sb.run("nexus", "nope", env={"AGENTS_DIR": ""}, label="AGENTS_DIR= nexus nope")
    # By a symlink to the checkout, AGENTS_DIR is that path: the orphan (labelled with the real one) isn't seen.
    sb.run("nexus", "ls", env={"AGENTS_DIR": sb.path("link-to-a")}, label="AGENTS_DIR=<S>/link-to-a nexus ls")
    sb.run("nexus", "nope", env={"AGENTS_DIR": sb.path("link-to-a")}, label="AGENTS_DIR=<S>/link-to-a nexus nope")
    # A copy of nexus elsewhere: its own dir, unless AGENTS_DIR says otherwise.
    sb.run("nexus", "ls", via=sb.path("other/nexus"), label="<S>/other/nexus ls")
    sb.run("nexus", "ls", via=sb.path("other/nexus"), env={"AGENTS_DIR": sb.a},
           label="AGENTS_DIR=<A> <S>/other/nexus ls")


@case("nexus/basic-symlink")
def _(sb):
    # Linked into a bin dir, as config.sh does: the checkout is found through the link (any chain of them).
    two_personas(sb)
    sb.container("old", state="exited")
    sb.symlink("linkbin/nexus", sb.a + "/nexus")
    sb.symlink("linkbin/rel", "../a/nexus")
    sb.symlink("linkbin/chain", sb.path("linkbin/rel"))
    sb.run("nexus", "ls", via=sb.path("linkbin/nexus"), label="linkbin/nexus ls")
    sb.run("nexus", "nope", via=sb.path("linkbin/nexus"), label="linkbin/nexus nope")
    sb.run("nexus", "nope", via=sb.path("linkbin/rel"), label="linkbin/rel nope")
    sb.run("nexus", "ls", via=sb.path("linkbin/chain"), label="linkbin/chain ls")
    sb.run("nexus", via=sb.path("linkbin/chain"), label="linkbin/chain")


@case("nexus/basic-deleted-cwd")
def _(sb):
    # A shell sitting in a directory that was deleted: a warning, then on as usual where it can.
    two_personas(sb)
    sb.container("c1")
    sb.write("elsewhere/.keep", "")      # made here, so the runs' mkdir -p doesn't show it (with a umask's mode)
    w = sb.script("wrap/in-gone", 'd="$1"; shift\nmkdir -p "$d" && cd "$d" && rmdir "$d" && exec "%s/nexus" "$@"'
                  % sb.a)
    gone, spaced, outside = sb.projects + "/gone", sb.projects + "/my gone", sb.path("elsewhere/gone")
    sb.run(w, gone, "ls", label="(in <P>/gone, deleted) nexus ls")
    sb.run(w, spaced, "ls", label="(in '<P>/my gone', deleted) nexus ls")
    sb.run(w, gone, "c1", "ls", label="(in <P>/gone, deleted) nexus c1 ls")
    sb.run(w, gone, "c1", label="(in <P>/gone, deleted) nexus c1")
    sb.run(w, outside, "c1", label="(in <S>/elsewhere/gone, deleted) nexus c1")


@case("nexus/basic-sigpipe")
def _(sb):
    # Output to a reader that has gone. Nothing runs, so docker ps, before any output, is ls's only call:
    # when the reader goes doesn't change the calls. (No top | head: top writes its header before its
    # docker calls, so those would race head's exit.)
    two_personas(sb)
    head = sb.script("wrap/head1", '"%s/nexus" "$@" | head -1' % sb.a)
    # A pipe whose read end is closed before nexus starts: its first write gets SIGPIPE, every time.
    closed = sb.script("wrap/closed", "\n".join([
        "exec python3 -I -c '",
        "import os, subprocess, sys",
        "r, w = os.pipe()",
        "os.close(r)",
        "rc = subprocess.call(sys.argv[1:], stdout=w)",
        "print(\"killed by signal %d\" % -rc if rc < 0 else \"exit %d\" % rc)",
        "' \"$@\""]))
    sb.run(head, "ls", label="nexus ls | head -1")
    sb.run(closed, sb.a + "/nexus", "ls", label="nexus ls >closed-pipe")
    sb.run(closed, sb.a + "/nexus", "top", "--once", label="nexus top --once >closed-pipe")
    sb.run(closed, sb.a + "/nexus", "--help", label="nexus --help >closed-pipe")
    sb.run(closed, sb.a + "/nexus", "nope", label="nexus nope >closed-pipe")
