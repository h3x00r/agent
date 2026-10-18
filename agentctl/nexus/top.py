"""nexus top: every persona at a glance, redrawn every few seconds (or once): its container's state, CPU and
memory, and each pane running its CLI, with the figures the CLI's statusline last saved for it (model,
context, 5-hour and 7-day limits, cost) and how long ago that was.

The statusline scripts (statusline/claude/, statusline/grok/) save one line per pane to
~/.cache/nexus/status/<pane> inside the persona: v1, epoch, model, used, size, pct, 5h, 7d, cost, tab-
separated, "-" where they have nothing. The top probe (probes/top_probe.sh) lists the panes running the
CLI with that line. What a persona sends is printed, never evaluated: numbers are checked before any
arithmetic, and control characters dropped, so a pane can't drive this terminal.

Pitfalls (each pinned where named):
- HOSTNAME from the environment if it is there, even empty, else gethostname() (golden
  nexus/edge-top-hostname)
- 10 s for the probe, 20 s for docker stats (golden nexus/edge-top-probe-hang, golden
  nexus/edge-top-stats-hang)
- numbers are decimal, leading zeros too, and never wrap (golden nexus/basic-top-octal, golden
  nexus/edge-top-big-numbers)
- an empty field moves the rest up one, as a tab IFS collapses (golden nexus/basic-top-snapshot-garbled)
- lengths and cuts in characters, bytes outside UTF-8 (test_nexus.TopFigures.test_cuts_by_locale, golden
  nexus/edge-top-model-c-locale)
- the probe's rows as `while read` gives them: a last line without a newline is dropped (not pinned)
- the live frame shows docker ps's errors (golden nexus/edge-top-ps-fail)
- INT, TERM and HUP in the live view: exit 0, the terminal put back (golden nexus/edge-top-exit-tty-state)
- a key is one character, all of a UTF-8 one (golden nexus/edge-top-key-utf8)
- a huge -n waits the longest select() takes (golden nexus/edge-top-n-huge)
- stdin not a terminal: a sleep, no key read (golden nexus/edge-top-stdin-not-tty)
"""
import os
import select
import signal
import socket
import sys
import termios
import time
import unicodedata

from agentctl.adapters import docker
from agentctl.core import proc, sigs
from agentctl.core.errors import NexusError
from agentctl.core.shellcompat import ere, read_fields
from agentctl.domain import flavour
from agentctl.ui import table
from agentctl.ui.style import Palette
from agentctl.ui.text import clean, utf8_locale as text_utf8


def tok(n):
    """640, 38k, 1.2M"""
    if n >= 1000000:
        d = (n + 50000) // 100000
        return "%d.%dM" % (d // 10, d % 10) if d % 10 else "%dM" % (d // 10)
    if n >= 1000:
        return "%dk" % ((n + 500) // 1000)
    return "%d" % n


def age(s):
    """12s, 3m, 2h, 4d"""
    s = max(s, 0)
    if s < 60:
        return "%ds" % s
    if s < 3600:
        return "%dm" % (s // 60)
    if s < 86400:
        return "%dh" % (s // 3600)
    return "%dd" % (s // 86400)


def _field(rest, key):
    i = rest.find(" %s=" % key)
    v = rest[i + len(key) + 2:] if i >= 0 else rest
    return v.split(" ", 1)[0]


def _no_controls(s, utf8):
    """bash's ${s//[[:cntrl:]]/}: control characters (C1 too) in a UTF-8 locale, else control bytes."""
    if utf8:
        return "".join(c for c in s if unicodedata.category(c) != "Cc")
    b = bytes(x for x in s.encode("utf-8", "surrogateescape") if x >= 32 and x != 127)
    return b.decode("utf-8", "surrogateescape")


def _length(s, utf8):
    return len(s) if utf8 else len(s.encode("utf-8", "surrogateescape"))


def _head(s, n, utf8):
    """bash's ${s:0:n}: the first n characters (bytes outside a UTF-8 locale); a negative n counts from the end."""
    if utf8:
        return s[:n] if n >= 0 else s[:len(s) + n]
    b = s.encode("utf-8", "surrogateescape")
    b = b[:n] if n >= 0 else b[:len(b) + n]
    return b.decode("utf-8", "surrogateescape")


def header(p, every=""):
    now = int(time.time())
    # ${HOSTNAME:-}: bash's own HOSTNAME unless one came in from the environment, even an empty one
    hostname = os.environ["HOSTNAME"] if "HOSTNAME" in os.environ else socket.gethostname()
    return ["%snexus top%s  %s  %s%s" % (p.bold, p.reset, hostname, time.strftime("%H:%M:%S", time.localtime(now)),
                                         "  %severy %ss, q quits, any other key redraws%s" % (p.dim, every, p.reset)
                                         if every else ""), ""]


def frame(ctx, p, every="", head=True):
    """One frame's lines. In the live one (HEAD), docker ps's errors are in it, after the header, as the
    bash's `$(top_frame 2>&1)` had them; otherwise they go to stderr as they come."""
    now = int(time.time())
    utf8 = text_utf8()
    out = header(p, every) if head else []
    lines = ctx.personas_lines(unterminated=True)
    errors = [] if head else None
    st = docker.States(ctx.agents_dir, errors)
    for e in errors or ():
        out += e.rstrip("\n").split("\n")
    names = [l.split(" ", 1)[0] for l in lines]
    pw = max([_length(n, utf8) for n in names] + [0])
    run = ["agent-" + n for n in names if "agent-" + n in st.up]
    cpus, mems = {}, {}
    if run:
        r = proc.run(["docker", "stats", "--no-stream", "--format", r"{{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}"] + run,
                     timeout=20)
        for line in r.out.split("\n")[:-1] if r.out else []:
            c, cpu, mem = read_fields(line, "\t", 3)
            cpus[c], mems[c] = cpu, mem.split(" /", 1)[0]
    width = 8 if pw > 18 else 26 - pw
    prow = [["PERSONA", "STATE", "CPU", "MEM", "PANES"]]
    srow = [["PERSONA", "SESSION", "MODEL", "CONTEXT", "5H", "7D", "COST", "SEEN"]]
    grok = False
    for pl in lines:
        name = pl.split(" ", 1)[0]
        rest = " %s " % pl[len(name):]
        fl, sl = _field(rest, "flavour"), _field(rest, "statusline")
        c = "agent-" + name
        f = flavour.get(fl)
        cli = f.cli if f else fl
        state = st.state.get(c, "not started")
        lc = p.state(state)
        if c not in st.up:
            prow.append([p.bold + name + p.reset, lc + state + p.reset, "-", "-", "-"])
            continue
        rc, probe_out = docker.exec_probe(c, "top_probe.sh", cli, timeout=10)
        rows = probe_out.split("\n")[:-1] if probe_out else []      # `while read` drops a last unended line
        count = {}
        for i in rows:
            parts = i.split("\t", 3)
            sess = parts[2] if len(parts) > 2 else ""
            count[sess] = count.get(sess, 0) + 1
        panes = "%d %s" % (len(rows), cli) if rows else "%sno %s running%s" % (p.dim, cli, p.reset)
        if sl != "yes":
            panes += "%s, statusline off%s" % (p.dim, p.reset)
        prow.append([p.bold + name + p.reset, lc + state + p.reset, clean(cpus.get(c, "")), clean(mems.get(c, "")),
                     panes])
        for i in rows:
            (_id, wp, sess, ver, ts, model, used, size, pct, five, week, cost) = read_fields(i, "\t", 12)
            a = count.get(sess, 0)
            sess = _no_controls(sess, utf8)
            wp = "".join(ch for ch in wp if ch in "0123456789.")
            lab = ":" + wp if a > 1 else ""
            if _length(sess, utf8) + len(lab) > width:
                sess = _head(sess, width - 1 - len(lab), utf8) + "~"
            lab = clean(sess + lab)
            if fl == "grok":
                grok = True
            if ver != "v1" or not ere(r"[0-9]+", ts):
                srow.append([name, lab, cli, "-", "-", "-", "-", "-"])
                continue
            model = clean(_head(model, 11, utf8))
            ctxt, lcol = "-", ""
            if ere(r"[0-9]+", used + size) and ere(r"[0-9]+", used) and ere(r"[1-9][0-9]*", size):
                ctxt, lcol = "%s/%s" % (tok(int(used)), tok(int(size))), p.load(pct)
            c5, c7 = p.load(five), p.load(week)
            five = five + "%" if ere(r"[0-9]+", five) else "-"
            week = week + "%" if ere(r"[0-9]+", week) else "-"
            if ere(r"[0-9]+\.[0-9]{4}", cost):
                cents = (int(cost.replace(".", ""), 10) + 50) // 100
                cost = "$%d.%02d" % (cents // 100, cents % 100)
            else:
                cost = "-"
            srow.append([name, lab, model, lcol + ctxt + p.reset, c5 + five + p.reset, c7 + week + p.reset, cost,
                         age(now - int(ts))])
    out += table.render("llrrl", prow, p)
    out.append("")
    if len(srow) == 1:
        out.append("%sno Claude Code or grok pane in a running persona%s" % (p.dim, p.reset))
        return out
    out += table.render("lllrrrrr", srow, p)
    out.append("")
    out.append("%sCONTEXT used/size; 5H, 7D: the plan's limits used; COST: the session's, at API prices%s" % (p.dim, p.reset))
    out.append("%sSEEN: since the CLI last drew its statusline, which it does as the session works%s" % (p.dim, p.reset))
    if grok:
        out.append("%sgrok sends no plan limits (5H, 7D), and a cost only once the session has a price%s" % (p.dim, p.reset))
    return out


def parse(args):
    every, once, i = "5", False, 0
    while i < len(args):
        a = args[i]
        if a in ("-n", "--interval"):
            v = args[i + 1] if i + 1 < len(args) else ""
            if not ere(r"[1-9][0-9]*", v):
                raise NexusError("top: %s takes whole seconds, e.g. %s 10" % (a, a))
            every = v
            i += 1
        elif a == "--once":
            once = True
        else:
            raise NexusError("top: unknown argument '%s'; it takes [-n SECONDS] [--once]" % a)
        i += 1
    return every, once


# The longest wait select() and sleep() take (about 31 years): a larger -n waits that long.
_LONGEST = 10 ** 9


def _key(timeout):
    """One key from the terminal on stdin, its echo off, as `read -rsn1 -t`: the key (one character, so
    all the bytes of a UTF-8 one in a UTF-8 locale), "" on time-out, None at the end of input."""
    fd = 0
    old = termios.tcgetattr(fd)
    new = termios.tcgetattr(fd)
    new[3] &= ~(termios.ICANON | termios.ECHO)
    new[6][termios.VMIN], new[6][termios.VTIME] = 1, 0
    termios.tcsetattr(fd, termios.TCSADRAIN, new)
    try:
        r, _, _ = select.select([fd], [], [], min(timeout, _LONGEST))
        if not r:
            return ""
        b = os.read(fd, 1)
        if not b:
            return None
        more = (1 if b[0] < 0xE0 else 2 if b[0] < 0xF0 else 3) if b[0] >= 0xC0 and text_utf8() else 0
        while more and select.select([fd], [], [], 0.1)[0]:
            c = os.read(fd, 1)
            if not c or not 0x80 <= c[0] < 0xC0:
                break
            b += c
            more -= 1
        return b.decode("utf-8", "replace")
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


def top(ctx, args):
    every, once = parse(args)
    ctx.need_personas()
    p = Palette.for_stdout()
    if once or not sys.stdout.isatty():
        ctx.out.lines(header(p))     # first, as bash wrote it before asking docker anything
        ctx.out.lines(frame(ctx, p, head=False))
        return 0
    w = sys.stdout

    def restore():
        w.write("\033[?7h\033[?25h\033[?1049l")
        w.flush()

    def stop(signum, frm):
        raise SystemExit(0)
    sigs.trap((signal.SIGINT, signal.SIGTERM, signal.SIGHUP), stop)
    w.write("\033[?1049h\033[?25l\033[?7l")
    try:
        while True:
            try:
                text = "\n".join(frame(ctx, p, every)).rstrip("\n")
            except Exception as e:  # noqa: BLE001  (a frame that fails shows why, and the loop goes on)
                text = "nexus: %s" % e
            w.write("\033[H" + text.replace("\n", "\033[K\n") + "\033[K\033[J")
            w.flush()
            if not os.isatty(0):
                time.sleep(min(int(every), _LONGEST))
                continue
            k = _key(int(every))
            if k is None or k in ("q", "Q"):
                break
    finally:
        restore()
    return 0
