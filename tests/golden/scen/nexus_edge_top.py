"""Review: nexus top, bash oracle vs the Python port. Each scenario aims at one suspected difference."""
import re
import time

from scen import case, snapshot_line, two_personas


def hms(text):
    """top's clock, in any frame, with any (or no) host name."""
    return re.sub(r"(nexus top(?:\\e\[[0-9;]*m)?  \S*  )\d\d:\d\d:\d\d", r"\1<HH:MM:SS>", text)


def snap_with(i, value, **kw):
    f = snapshot_line(**kw).split("\t")
    f[i] = value
    return "\t".join(f)


def add_persona_lines(sb, *lines):
    p = sb.a + "/compose.personas.yml"
    with open(p, "a") as f:
        for l in lines:
            f.write("# persona: " + l + "\n")


# --- numbers -------------------------------------------------------------------------------------
@case("nexus/edge-top-big-numbers", norm=hms)
def _(sb):
    # 64-bit wrap in bash arithmetic; Python ints don't wrap.
    two_personas(sb)
    now = int(time.time())
    sb.container("c1", panes=[
        ["%1", "0.0", "used-wrap", snapshot_line(used="18446744073709552616")],          # 2^64+1000
        ["%2", "0.0", "cost-wrap", snapshot_line(cost="99999999999999999999.0000")],
        ["%3", "0.0", "ts-wrap", snap_with(1, str(2 ** 64 + now - 3 * 86400))],
        ["%4", "0.0", "five-wrap", snapshot_line(five="18446744073709551617", pct="18446744073709551617")]])
    sb.run("nexus", "top", "--once")
    sb.run("nexus", "top", "--once", mode="tty-all")


@case("nexus/edge-top-octal-colour", norm=hms)
def _(sb):
    # Valid octal (no 8 or 9): bash compared it as octal, without an error.
    two_personas(sb)
    sb.container("c1", panes=[
        ["%1", "0.0", "five-0100", snapshot_line(five="0100", week="061")],      # bash 64 / 49
        ["%2", "0.0", "pct-0100", snapshot_line(pct="0100")],
        ["%3", "0.0", "used-octal", snapshot_line(used="0037000")]])             # bash 16k
    sb.run("nexus", "top", "--once")
    sb.run("nexus", "top", "--once", mode="tty-all")


# --- text, locales -------------------------------------------------------------------------------
@case("nexus/edge-top-model-c-locale")
def _(sb):
    two_personas(sb)
    sb.container("c1", panes=[["%1", "0.0", "m1", snapshot_line(model="é" * 11)],
                              ["%2", "0.0", "m2", snapshot_line(model="Opus-ü-5.5x")]])
    sb.run("nexus", "top", "--once", env={"LC_ALL": "C"}, label="LC_ALL=C nexus top --once")


@case("nexus/edge-top-locale-missing")
def _(sb):
    # A UTF-8 locale this host doesn't have (as ssh's SendEnv LANG brings): bash falls back to C, bytes.
    two_personas(sb)
    sb.container("c1", panes=[["%1", "0.0", "é" * 20, snapshot_line(model="Opus é")],
                              ["%2", "0.0", "plain", snapshot_line()]])
    sb.run("nexus", "top", "--once", env={"LANG": "xx_YY.UTF-8"}, label="LANG=xx_YY.UTF-8 nexus top --once")


@case("nexus/edge-top-hostname", norm=hms)
def _(sb):
    two_personas(sb)
    sb.run("nexus", "top", "--once", env={"HOSTNAME": ""}, label="HOSTNAME= nexus top --once")
    sb.run("nexus", "top", "--once", env={"HOSTNAME": "box-7"}, label="HOSTNAME=box-7 nexus top --once")


@case("nexus/edge-top-persona-lines", norm=hms)
def _(sb):
    # Hand-edited personas file: a non-ASCII name under LC_ALL=C (room for the session), no flavour.
    two_personas(sb)
    add_persona_lines(sb, "éééééééééé flavour=claude context=docker statusline=yes")
    sb.container("c1", panes=[["%1", "0.0", "abcdefghijklmnopq", snapshot_line()]])
    sb.run("nexus", "top", "--once", env={"LC_ALL": "C"}, label="LC_ALL=C nexus top --once")


@case("nexus/edge-top-no-flavour", norm=hms)
def _(sb):
    two_personas(sb)
    add_persona_lines(sb, "n1 context=docker statusline=yes")
    sb.container("n1", panes=[["%1", "0.0", "main", snapshot_line()]])
    sb.run("nexus", "top", "--once")


# --- docker answers ------------------------------------------------------------------------------
@case("nexus/edge-top-ps-fail", norm=hms)
def _(sb):
    two_personas(sb)
    sb.container("c1")
    sb.load_world()
    sb.world["ps_rc"] = 1
    sb.save_world()
    sb.run("nexus", "top", "--once")
    sb.run("nexus", "top", mode="tty-all", steps=[("q quits", "q")])


@case("nexus/edge-top-states", norm=hms)
def _(sb):
    sb.personas("c1:claude:docker", "c2:claude:docker", "c3:claude:docker", "c4:claude:docker",
                "c5:claude:docker", "c6:claude:docker", "c7:claude:docker")
    sb.container("c1", state="exited")
    sb.container("c2", state="created", status="Created")
    sb.container("c3", state="dead", status="Dead")
    sb.container("c4", state="restarting", status="Restarting (1) 2 seconds ago")
    sb.container("c5", state="paused", status="Up 2 hours (Paused)")
    sb.container("c6", status="Up 2 hours (unhealthy)")
    sb.container("c7", status="Up 2 hours")
    sb.run("nexus", "top", "--once")
    sb.run("nexus", "top", "--once", mode="tty-all")


@case("nexus/edge-top-stats-garbled", norm=hms)
def _(sb):
    sb.personas("c1:claude:docker", "c2:claude:docker", "c3:claude:docker")
    sb.container("c1", cpu="1.5%\x1b[31m", mem="12MiB")
    sb.container("c2", cpu="\t", mem=" / 7GiB")
    sb.container("c3", cpu="2%\textra", mem="1GiB / 2GiB / 3GiB")
    sb.run("nexus", "top", "--once")


@case("nexus/edge-top-probe-short")
def _(sb):
    two_personas(sb)
    sb.container("c1", panes=[["%1", "0.0"]])                       # one tab: no session
    sb.run("nexus", "top", "--once")


@case("nexus/edge-top-probe-empty-line")
def _(sb):
    two_personas(sb)
    sb.container("c1", panes=[[], ["%2", "0.0", "main", snapshot_line()]])
    sb.run("nexus", "top", "--once")


@case("nexus/edge-top-probe-empty-id")
def _(sb):
    two_personas(sb)
    sb.container("c1", panes=[["", "0.0", "main", snapshot_line()], ["%2", "0.0", "main", snapshot_line()]])
    sb.run("nexus", "top", "--once")


@case("nexus/edge-top-probe-extra", norm=hms)
def _(sb):
    two_personas(sb)
    sb.container("c1", panes=[["%1", "0.0", "main", "-", "extra"],
                              ["%2", "0.0", "main", snapshot_line() + "\tmore\tfields"],
                              ["%3", "0.0", "x", "v1"]])
    sb.run("nexus", "top", "--once")


@case("nexus/edge-top-probe-garbled-live", norm=hms)
def _(sb):
    two_personas(sb)
    sb.container("c1", panes=[["%1", "0.0"]])
    sb.run("nexus", "top", mode="tty-all", steps=[("q quits", "q")])


# --- live mode -----------------------------------------------------------------------------------
@case("nexus/edge-top-n-huge", norm=hms)
def _(sb):
    two_personas(sb)
    sb.run("nexus", "top", "-n", "10000000000", mode="tty-all", steps=[("q quits", "q")], timeout=15)
    sb.run("nexus", "top", "-n", "99999999999999999999", mode="tty-all", steps=[("q quits", "q")], timeout=15)


@case("nexus/edge-top-key-utf8", norm=hms)
def _(sb):
    two_personas(sb)
    sb.run("nexus", "top", "-n", "30", mode="tty-all",
           steps=[("q quits", "<RAW>é"), ("q quits", "<SLEEP>2"), ("", "<RAW>q")])


@case("nexus/edge-top-live-keys", norm=hms)
def _(sb):
    two_personas(sb)
    sb.container("c1", panes=[["%3", "1.0", "billing", snapshot_line(pct=85, five=55, week=10)]])
    sb.run("nexus", "top", "-n", "1", mode="tty-all",
           steps=[("q quits", "<SLEEP>0"), ("q quits", "<SLEEP>0"), ("q quits", "<RAW>x"), ("q quits", "<EOF>"),
                  ("q quits", "<RAW>Q")])
    sb.run("nexus", "top", "-n", "30", mode="tty-all", steps=[("q quits", "<RAW>\x1b"), ("q quits", "<RAW>q")])


@case("nexus/edge-top-winch", norm=hms)
def _(sb):
    two_personas(sb)
    sb.run("nexus", "top", "-n", "30", mode="tty-all",
           steps=[("q quits", "<WINCH>30x80"), ("", "<SLEEP>2"), ("", "<RAW>q")])


@case("nexus/edge-top-stdin-not-tty", norm=hms)
def _(sb):
    two_personas(sb)
    w = sb.script("wrap/nostdin", 'exec "%s/nexus" "$@" </dev/null' % sb.a)
    sb.run(w, "top", "-n", "1", mode="tty-all", steps=[("q quits", "<SLEEP>0"), ("q quits", "<INT>")],
           label="nexus top -n 1 </dev/null")


@case("nexus/edge-top-tty-in", norm=hms)
def _(sb):
    two_personas(sb)
    sb.container("c1", panes=[["%3", "1.0", "billing", snapshot_line(pct=85)]])
    sb.run("nexus", "top", mode="tty-in")


# --- time-outs -----------------------------------------------------------------------------------
@case("nexus/edge-top-probe-hang", norm=hms)
def _(sb):
    two_personas(sb)
    sb.container("c1", panes=[["%1", "0.0", "first", snapshot_line()]], panes_hang=30)
    sb.run("nexus", "top", "--once", timeout=45)


@case("nexus/edge-top-stats-hang", norm=hms)
def _(sb):
    two_personas(sb)
    sb.container("c1", panes=[["%1", "0.0", "first", snapshot_line()]], stats_hang=40)
    sb.container("g1")
    sb.run("nexus", "top", "--once", timeout=50)


# --- the terminal after each way out ---------------------------------------------------------------
TTY_STATE = ("python3 -I -c 'import termios; a = termios.tcgetattr(0); "
             "print(\"after:\", \"echo\" if a[3] & termios.ECHO else \"-echo\", "
             "\"icanon\" if a[3] & termios.ICANON else \"-icanon\")'")


@case("nexus/edge-top-exit-tty-state", norm=hms)
def _(sb):
    two_personas(sb)
    w = sb.script("wrap/then-state", 'trap "echo trapped" INT TERM HUP\n"%s/nexus" "$@"\necho "rc=$?"\n%s'
                  % (sb.a, TTY_STATE))
    for label, step in (("q", "<RAW>q"), ("ctrl-c", "<INT>"), ("term", "<SIG>TERM"), ("hup", "<SIG>HUP")):
        sb.run(w, "top", "-n", "30", mode="tty-all", steps=[("q quits", step)], label="nexus top, then " + label)


@case("nexus/edge-top-persona-tab", norm=hms)
def _(sb):
    two_personas(sb)
    add_persona_lines(sb, "t1\tx flavour=claude context=docker statusline=yes")
    sb.run("nexus", "top", "--once")


@case("nexus/edge-top-label-overflow", norm=hms)
def _(sb):
    # max is 8 (a persona name over 18): a :window.pane label past the room left, by more than the session.
    sb.personas("c1:claude:docker", "x1-a-really-long-name:claude:docker")
    sb.container("c1", panes=[["%1", "1234567890.1", "ab", snapshot_line()],
                              ["%2", "1234567890.2", "ab", snapshot_line()]])
    sb.run("nexus", "top", "--once")


@case("nexus/edge-top-no-persona-lines", norm=hms)
def _(sb):
    # A personas file without any "# persona:" line (and one with only a CRLF line).
    two_personas(sb)
    with open(sb.a + "/compose.personas.yml") as f:
        keep = [l for l in f if not l.startswith("# persona: ")]
    with open(sb.a + "/compose.personas.yml", "w") as f:
        f.writelines(keep)
    sb.run("nexus", "top", "--once")
    add_persona_lines(sb, "c1 flavour=claude context=docker statusline=yes\r")
    sb.container("c1", panes=[["%1", "0.0", "main", snapshot_line()]])
    sb.run("nexus", "top", "--once")
