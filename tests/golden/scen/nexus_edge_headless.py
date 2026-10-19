"""Review of nexus <persona> headless beyond the life-headless-* cases: signals mid-run, a second one, one
before the run (while the input is spooled), the kill script's run id, stdin kinds, NUL and non-UTF-8
input, TMPDIR, the CLI's own exit. Signals other than a terminal's ^C come from a driver (drv/sigdrv,
written into the sandbox) that runs nexus in a session of its own and signals it, or its group, when a mark
appears (fake.py's cli_run and kill, in SANDBOX/marks)."""
import json
import os
import re
import shutil
import sys

from scen import case, two_personas

DRV = r'''
import json, os, signal, socket, subprocess, sys, time
spec, argv = json.loads(sys.argv[1]), sys.argv[2:]
marks, tmp = spec["marks"], spec["tmp"]
kind = spec.get("stdin", "devnull")
data = spec.get("data", "").encode("utf-8", "surrogateescape")
kw, keep = {}, None
if kind == "pipe":
    r, w = os.pipe()
    kw["stdin"] = r
    if spec.get("nonblock"):
        os.set_blocking(r, False)
elif kind == "socket":
    a, b = socket.socketpair()
    kw["stdin"] = b.fileno()
elif kind == "closed":
    kw["preexec_fn"] = lambda: os.close(0)
else:
    kw["stdin"] = subprocess.DEVNULL
if spec.get("stderr") == "dead":   # a reader that is gone: every write to it fails (EPIPE)
    er, ew = os.pipe()
    os.close(er)
    kw["stderr"] = ew
p = subprocess.Popen([spec["nexus"]] + argv, start_new_session=True, **kw)
if spec.get("stderr") == "dead":
    os.close(ew)
if kind == "pipe":
    os.close(r)
    if data:
        os.write(w, data)
    if spec.get("eof"):
        os.close(w)
        w = None
elif kind == "socket":
    b.close()
    if data:
        a.sendall(data)
    if spec.get("eof"):
        a.shutdown(socket.SHUT_WR)


def ready(what):
    if what == "spool":
        return any(n.startswith("tmp.") for n in os.listdir(tmp))
    if what.startswith("sleep"):
        return True
    return os.path.exists(os.path.join(marks, what))


for st in spec.get("steps", []):
    end = time.time() + 20
    while time.time() < end and p.poll() is None and not ready(st["wait"]):
        time.sleep(0.02)
    time.sleep(st.get("settle", 0.3))
    if p.poll() is not None:
        break
    n = getattr(signal, "SIG" + st["sig"])
    if st.get("to") == "group":
        os.killpg(p.pid, n)
    else:
        os.kill(p.pid, n)
if spec.get("later"):
    time.sleep(spec.get("later_after", 0.5))
    try:
        os.write(w, spec["later"].encode())
    except OSError as e:
        sys.stderr.write("[driver: the later write failed: %s]\n" % e.strerror)
time.sleep(spec.get("close_after", 1.0))
if kind == "pipe" and w is not None:
    os.close(w)
elif kind == "socket":
    a.close()
try:
    rc = p.wait(25)
except subprocess.TimeoutExpired:
    os.killpg(p.pid, signal.SIGKILL)
    rc = p.wait()
sys.exit(128 - rc if rc < 0 else rc)
'''


def spool_norm(text):
    """A spool left in TMPDIR, whatever its random name (mktemp's or Python's)."""
    return re.sub(r"tmp/tmp\.[A-Za-z0-9_]{6,12}\b", "tmp/tmp.<SPOOL>", text)


def setup(sb, **ctr):
    two_personas(sb)
    sb.container("c1", **ctr)
    sb.write("projects/app/.keep", "")
    return sb.write("drv/sigdrv", "#!%s -I\n%s" % (sys.executable, DRV), 0o755)


def drv(sb, d, label, *args, env=None, **spec):
    shutil.rmtree(sb.path("marks"), ignore_errors=True)
    spec.update(nexus=os.path.join(sb.a, "nexus"), marks=sb.path("marks"), tmp=sb.tmp)
    return sb.run(d, json.dumps(spec), "c1", "headless", *args, cwd="projects/app", env=env, label=label)


def ctr_set(sb, persona, **kw):
    sb.load_world()
    sb.world["containers"]["agent-" + persona].update(kw)
    sb.save_world()


# --- signals mid-run -----------------------------------------------------------------------------------
@case("nexus/edge-headless-int-tty")
def _(sb):
    setup(sb, cli_run={"secs": 10}, kill={})
    sb.run("nexus", "c1", "headless", "nxt long", mode="tty-all", cwd="projects/app",
           steps=[("cli: running", "<INT>")], label="nexus c1 headless 'nxt long'  (^C mid-run)")
    ctr_set(sb, "c1", cli_run={"secs": 10, "sig_rc": 3})
    shutil.rmtree(sb.path("marks"), ignore_errors=True)
    sb.run("nexus", "c1", "headless", "nxt long", mode="tty-all", cwd="projects/app",
           steps=[("cli: running", "<INT>")], label="nexus c1 headless 'nxt long'  (^C mid-run, the CLI exits 3)")


@case("nexus/edge-headless-sigs")
def _(sb):
    d = setup(sb, cli_run={"secs": 10}, kill={})
    drv(sb, d, "nexus c1 headless 'nxt long'  (INT to the group mid-run)", "nxt long",
        steps=[{"wait": "cli", "sig": "INT", "to": "group"}])
    drv(sb, d, "nexus c1 headless 'nxt long'  (INT to nexus alone)", "nxt long", steps=[{"wait": "cli", "sig": "INT"}])
    drv(sb, d, "nexus c1 headless 'nxt long'  (TERM)", "nxt long", steps=[{"wait": "cli", "sig": "TERM"}])
    drv(sb, d, "nexus c1 headless 'nxt long'  (HUP)", "nxt long", steps=[{"wait": "cli", "sig": "HUP"}])
    drv(sb, d, "nexus c1 headless 'nxt long'  (TERM to the group)", "nxt long",
        steps=[{"wait": "cli", "sig": "TERM", "to": "group"}])
    drv(sb, d, "echo x | nexus c1 headless 'nxt long'  (TERM, input piped)", "nxt long", stdin="pipe", data="x\n",
        eof=True, steps=[{"wait": "cli", "sig": "TERM"}])
    ctr_set(sb, "c1", cli_run={"secs": 10, "sig_rc": 1})
    drv(sb, d, "nexus c1 headless 'nxt long'  (HUP, the CLI exits 1)", "nxt long", steps=[{"wait": "cli", "sig": "HUP"}])


@case("nexus/edge-headless-second-sig")
def _(sb):
    d = setup(sb, cli_run={"secs": 10}, kill={"sleep": [1.5]})
    drv(sb, d, "nexus c1 headless 'nxt long'  (INT, then TERM during its kill)", "nxt long",
        steps=[{"wait": "cli", "sig": "INT", "to": "group"}, {"wait": "kill-1", "sig": "TERM"}])
    drv(sb, d, "nexus c1 headless 'nxt long'  (INT, then INT during its kill)", "nxt long",
        steps=[{"wait": "cli", "sig": "INT", "to": "group"}, {"wait": "kill-1", "sig": "INT", "to": "group"}])
    drv(sb, d, "nexus c1 headless 'nxt long'  (TERM, then INT during its kill)", "nxt long",
        steps=[{"wait": "cli", "sig": "TERM"}, {"wait": "kill-1", "sig": "INT"}])
    # The CLI ignores the signal it's sent, and ends on its own: a second ^C after the first kill is done.
    ctr_set(sb, "c1", cli_run={"secs": 3, "ignore": True}, kill={})
    drv(sb, d, "nexus c1 headless 'nxt long'  (INT twice, the CLI keeps going)", "nxt long",
        steps=[{"wait": "cli", "sig": "INT", "to": "group"}, {"wait": "kill-1", "settle": 0.5, "sig": "INT",
                                                                "to": "group"}])


@case("nexus/edge-headless-sigign")
def _(sb):
    setup(sb)
    sb.load_world()
    sb.world["record_sigign"] = True
    sb.save_world()
    sb.run("nexus", "c1", "headless", "nxt hi", cwd="projects/app", label="nexus c1 headless 'nxt hi'  (the exec's ignored signals)")


# --- a signal before the run: while the input is spooled, or during the 3 s wait for it -----------------
@case("nexus/edge-headless-sig-spool", norm=spool_norm)
def _(sb):
    d = setup(sb)
    drv(sb, d, "(printf 'nxt part'; sleep) | nexus c1 headless  (INT to the group while spooling)",
        stdin="pipe", data="nxt part", steps=[{"wait": "spool", "sig": "INT", "to": "group"}])
    drv(sb, d, "(printf 'nxt part'; sleep) | nexus c1 headless  (TERM while spooling)",
        stdin="pipe", data="nxt part", steps=[{"wait": "spool", "sig": "TERM"}])
    drv(sb, d, "(printf 'nxt part'; sleep) | nexus c1 headless  (HUP while spooling)",
        stdin="pipe", data="nxt part", steps=[{"wait": "spool", "sig": "HUP"}])


@case("nexus/edge-headless-int-pid-spool", norm=spool_norm)
def _(sb):
    d = setup(sb)
    drv(sb, d, "(printf 'nxt part'; sleep 1) | nexus c1 headless  (kill -INT nexus while spooling)",
        stdin="pipe", data="nxt part", steps=[{"wait": "spool", "sig": "INT"}])


@case("nexus/edge-headless-sig-wait3", norm=spool_norm)
def _(sb):
    d = setup(sb)
    drv(sb, d, "sleep | nexus c1 headless 'nxt idle'  (INT to the group in the 3 s wait)", "nxt idle",
        stdin="pipe", steps=[{"wait": "sleep", "settle": 1.8, "sig": "INT", "to": "group"}], close_after=0.2)
    drv(sb, d, "sleep | nexus c1 headless 'nxt idle'  (TERM in the 3 s wait)", "nxt idle",
        stdin="pipe", steps=[{"wait": "sleep", "settle": 1.8, "sig": "TERM"}], close_after=0.2)


# --- input --------------------------------------------------------------------------------------------
@case("nexus/edge-headless-nul")
def _(sb):
    setup(sb)
    sb.run("nexus", "c1", "headless", "nxt nul", stdin="\0", cwd="projects/app",
           label="printf '\\0' | nexus c1 headless 'nxt nul'")
    sb.run("nexus", "c1", "headless", "nxt nul", stdin="\0\0abc", cwd="projects/app",
           label="printf '\\0\\0abc' | nexus c1 headless 'nxt nul'")
    sb.run("nexus", "c1", "headless", stdin="\0nxt abc", cwd="projects/app",
           label="printf '\\0nxt abc' | nexus c1 headless")


@case("nexus/edge-headless-badutf8")
def _(sb):
    setup(sb)
    sb.run("nexus", "c1", "headless", "-\udcff", cwd="projects/app", label="nexus c1 headless $'-\\xff'")
    sb.run("nexus", "c1", "headless", "--x=\udcff", cwd="projects/app", label="nexus c1 headless $'--x=\\xff'")
    sb.run("nexus", "c1", "headless", "-a\udcc3", stdin="nxt in\n", cwd="projects/app",
           label="echo 'nxt in' | nexus c1 headless $'-a\\xc3'")


@case("nexus/edge-headless-args")
def _(sb):
    setup(sb)
    for args, lab in ((["--", "--model", "x"], "-- --model x"), ([""], "''"), (["--", "--"], "-- --"),
                      (["-"], "-"), (["-a b"], "'-a b'"), (["-- "], "'-- '"), (["--x=a\nb"], "$'--x=a\\nb'"),
                      (["-a\nb"], "$'-a\\nb'"), (["a 'b' \"c\"\nd", "--v"], "$'a \\'b\\' \"c\"\\nd' --v"),
                      (["-=x"], "-=x"), (["--", "", "-v"], "-- '' -v")):
        sb.run("nexus", "c1", "headless", *args, cwd="projects/app", label="nexus c1 headless " + lab)
    sb.run("nexus", "c1", "headless", "", "--model=x", stdin="nxt piped", cwd="projects/app",
           label="printf 'nxt piped' | nexus c1 headless '' --model=x")


@case("nexus/edge-headless-stdin")
def _(sb):
    d = setup(sb)
    drv(sb, d, "nexus c1 headless 'nxt closed' <&-", "nxt closed", stdin="closed")
    drv(sb, d, "nexus c1 headless <&-", stdin="closed")
    drv(sb, d, "nexus c1 headless 'nxt null' </dev/null", "nxt null", stdin="devnull")
    drv(sb, d, "nexus c1 headless 'nxt sock'  (stdin a socket, x sent and shut)", "nxt sock", stdin="socket", data="x\n",
        eof=True)
    drv(sb, d, "nexus c1 headless  (stdin a socket, the prompt sent and shut)", stdin="socket", data="nxt sp\n", eof=True)
    drv(sb, d, "nexus c1 headless 'nxt sock'  (stdin a socket left open)", "nxt sock", stdin="socket", close_after=4.5)


@case("nexus/edge-headless-tmpdir", norm=spool_norm)
def _(sb):
    setup(sb)
    os.makedirs(sb.path("alt"))
    os.makedirs(sb.path("ro"))
    os.chmod(sb.path("ro"), 0o555)
    os.makedirs(sb.path("projects/app/reltmp"))
    alt = sb.path("alt")
    for t, lab in ((sb.path("nope"), "a missing dir"), (sb.path("ro"), "a read-only dir"), ("rel-missing", "relative, missing"),
                   ("reltmp", "relative, in the cwd but not in the checkout nexus runs in")):
        sb.run("nexus", "c1", "headless", "nxt t", stdin="x\n", cwd="projects/app", env={"TMPDIR": t, "TEMP": alt},
               label="echo x | TMPDIR=<%s> nexus c1 headless 'nxt t'" % lab)
    os.chmod(sb.path("ro"), 0o755)


# --- the CLI's own exit ---------------------------------------------------------------------------------
@case("nexus/edge-headless-cli-exit")
def _(sb):
    d = setup(sb)
    for rc in (125, 126, 127, 255, 130):
        ctr_set(sb, "c1", reply="nxt rc %d" % rc, reply_rc=rc)
        sb.run("nexus", "c1", "headless", "nxt rc", cwd="projects/app", label="nexus c1 headless 'nxt rc'  (the CLI exits %d)" % rc)
    for sig in ("KILL", "TERM", "INT", "HUP"):
        ctr_set(sb, "c1", cli_run={"secs": 5, "die": sig})
        drv(sb, d, "nexus c1 headless 'nxt die'  (docker exec dies of %s)" % sig, "nxt die")


@case("nexus/edge-headless-dead-stderr")
def _(sb):
    d = setup(sb, cli_run={"secs": 10}, kill={})
    drv(sb, d, "nexus c1 headless 'nxt long' 2>dead-pipe  (INT to the group mid-run)", "nxt long", stderr="dead",
        steps=[{"wait": "cli", "sig": "INT", "to": "group"}])
    # The CLI ends at the second signal, so the INT comes while the run is still on, whatever the timing.
    ctr_set(sb, "c1", cli_run={"secs": 10, "exit_after": 2})
    drv(sb, d, "nexus c1 headless 'nxt long' 2>dead-pipe  (TERM, then INT)", "nxt long", stderr="dead",
        steps=[{"wait": "cli", "sig": "TERM"}, {"wait": "ack", "settle": 0.0, "sig": "INT"}])


@case("nexus/edge-headless-nonblock")
def _(sb):
    d = setup(sb)
    drv(sb, d, "nexus c1 headless < nonblocking-pipe  ('nxt one', then ' two' 0.5 s later)", stdin="pipe", nonblock=True,
        data="nxt one", later=" two")
    drv(sb, d, "nexus c1 headless 'nxt p' < nonblocking-pipe  ('x', then 'y' 0.5 s later)", "nxt p", stdin="pipe",
        nonblock=True, data="x", later="y")
    drv(sb, d, "nexus c1 headless 'nxt p' < nonblocking-pipe  (empty, 'y' 0.5 s later)", "nxt p", stdin="pipe",
        nonblock=True, later="y")
