"""nexus --update [-y] and nexus --update --since REV [-y]: bring this checkout up to date with its branch's
upstream, then apply what changed to the personas set up here, now or at a later run: what is still to do is
kept in update.json until it is done.

  1. The pull, by the code as it was: refuse root; the checkout must be a git clone, on a branch with an
     upstream; the lock nexus --config and upgrade share; git fetch; a branch with commits of its own is
     refused; the new commits listed; git merge --ff-only, which refuses changes of your own that the update
     touches.
  2. exec the launcher, now the code just pulled, as `nexus --update --since OLD` (OLD is HEAD when nothing was
     new): what applies an update is the code that knows what it means. The lock goes with the old process (its
     fd is close-on-exec).
  3. The apply, from update.json (RECORD): the commit the personas were last brought to ("rev") and what is still
     to do. Each commit between it, or --since REV, and HEAD is new to account for: then nexus --config --yes
     --no-build --no-link runs in this process (it writes what it generates again, and names each persona whose
     service that changed: `nexus NAME restart` in its notes), and git diff gives what it can't see: an image
     input (image_inputs()) means building that image; a file a container reads only when it starts
     (read_at_start()) means a restart. Both are added to what is still to do, and the record saved at HEAD.
     No new commit: config doesn't run, and only what is still to do is offered.
  4. What is due now: each image still to build; each running persona whose restart is still to do and that
     hasn't started since it was asked for ("at"), or whose container runs an older image than its tag now names
     (Docker's own answer, so a build that changed nothing restarts no one). A stopped persona is let off: its
     next start picks it all up. Asked once, on a terminal; -y doesn't ask; with no terminal, nothing is done.
     Each build and restart done comes off the record as it is done, so a run cut short offers only the rest.

Pitfalls (each pinned where named):
- the apply runs in a new process, the pulled code: an exec, never an import after the merge, and never config
  in a process that holds the lock already (flock on a second fd of one process is refused)
  (run.sh "a change to apply: pulled, then applied by the code just pulled (nexus --config in it, then the plan)")
- declined is kept: the next run, with nothing new to pull, offers it again (run.sh "declined, then nexus
  --update again: nothing new, and the same plan offered again")
- a first run that stops before it has accounted for what it pulled (config refusing, say) leaves the record at
  what was there before the pull, so the next run still offers it (run.sh "a first run that stops halfway (a
  personas.json that isn't JSON): pulled, the record left at what was there before")
- --no-link: --yes would otherwise link the launcher into /usr/local/bin on a VM that chose another place or none
  (not pinned)
- the image inputs come from the Dockerfile's own COPY lines, so a file it starts copying in counts at once
  (test_update.Plan.test_image_inputs_are_the_dockerfiles)
- a restart asked for again, after more commits, is due from the newest ask: a start in between doesn't cover it
  (test_update.Record.test_a_new_reason_moves_at)
"""
import calendar
import json
import os
import tempfile
import time

from agentctl.core import dotenv, lock, proc
from agentctl.core.errors import Exit, NexusError
from agentctl.nexus.context import Context, checkout, is_socket
from agentctl.nexus.upgrade import _ask

SHOWN = 20              # new commits listed at most; the rest counted
RECORD = "update.json"  # in the checkout, gitignored: {"rev", "from", "build": {image: [files]},
                        # "restart": {name: {"why": [...], "at": unix time}}}

# The files of this repo a persona reads only when its container is created or started (the rest it reads as it
# goes, statusline.sh at each refresh, or never: nexus's own code), by flavour; and what decides whether it
# reads them at all. compose.yml is every service's template, so every persona.
START = (
    ("compose.yml", "", lambda p: True),
    ("personas/claude.json", "claude", lambda p: True),
    ("personas/claude.managed.json", "claude", lambda p: True),
    ("personas/claude.ask.json", "claude", lambda p: p.permissions == "ask"),
    ("personas/grok.toml", "grok", lambda p: True),
    ("statusline/claude/settings.json", "claude", lambda p: p.statusline),
    ("statusline/grok/settings.toml", "grok", lambda p: p.statusline),
    ("ssh/none.conf", "", lambda p: p.ssh == "none"),
    ("ssh/key.conf", "", lambda p: bool(p.own_key)),
)

USAGE = """\
nexus --update — bring this checkout up to date with its branch (git fetch, then a fast-forward), then apply
what changed to the personas set up here: nexus --config --yes writes its files again, then the images to build
and the personas to restart are listed, and asked about. What isn't applied yet is kept (update.json) and
offered again by the next nexus --update, until it is done.

  nexus --update [-y]               update, then apply; -y applies without asking (a restart ends that
                                    persona's tmux sessions)
  nexus --update --since REV [-y]   apply what changed since REV too, without fetching: after a git pull of
                                    your own on a checkout nexus --update hasn't seen yet (--since ORIG_HEAD)
"""


def die(msg):
    raise NexusError(msg)


def git(a, *args, capture=True):
    return proc.run(["git", "-C", a] + list(args), capture=capture)


def one_line(r):
    return r.out.strip() if r.ok else ""


def main(repo, argv, out):
    yes, since = False, None
    i = 0
    while i < len(argv):
        a = argv[i]
        if a in ("-h", "--help"):
            out.usage(USAGE)
            return 0
        if a in ("-y", "--yes"):
            yes = True
        elif a == "--since":
            if i + 1 >= len(argv) or not argv[i + 1]:
                die("--update --since needs a commit, e.g. --since ORIG_HEAD")
            i += 1
            since = argv[i]
        else:
            die("--update: unknown option '%s' (see nexus --update --help)" % a)
        i += 1
    if os.geteuid() == 0:
        die("run this as the VM user who will use the personas, not as root")
    a = checkout(repo)
    top = git(a, "rev-parse", "--show-toplevel")
    if not top.ok or os.path.realpath(top.out.rstrip("\n")) != os.path.realpath(a):
        die("%s isn't a git checkout of its own: update it the way you installed it" % a)
    if since is None:
        return pull(a, yes, out)
    return Apply(a, since, yes, out).run()


# --- 1, 2: the pull, then the pulled code ---
def pull(a, yes, out):
    up = one_line(git(a, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"))
    if not up:
        if not git(a, "symbolic-ref", "-q", "HEAD").ok:
            die("%s isn't on a branch (after a rollback, say): git -C %s switch main, then nexus --update" % (a, a))
        branch = one_line(git(a, "symbolic-ref", "--short", "HEAD"))
        die("%s's branch %s has no upstream to update from: git -C %s branch --set-upstream-to origin/%s"
            % (a, branch, a, branch))
    if not lock.take(a):
        die("nexus --config or an upgrade is running in %s; let it finish first" % a)
    old = one_line(git(a, "rev-parse", "HEAD"))
    out.say("nexus: fetching %s" % up)
    if not git(a, "fetch", "--quiet", capture=False).ok:
        die("git fetch failed (above); nothing changed")
    ahead = int(one_line(git(a, "rev-list", "--count", "@{u}..HEAD")) or 0)
    behind = int(one_line(git(a, "rev-list", "--count", "HEAD..@{u}")) or 0)
    if ahead:
        die("%s has %d commit%s of its own that %s hasn't; nothing changed. Bring them together with git yourself "
            "(git -C %s pull --rebase, say), then nexus --update" % (a, ahead, "" if ahead == 1 else "s", up, a))
    if not behind:
        out.say("nexus: %s is up to date: %s" % (a, describe(a, "HEAD")))
    else:
        out.say("nexus: %d new commit%s on %s:" % (behind, "" if behind == 1 else "s", up))
        out.lines(git(a, "log", "--format=  %h %s", "-n", str(SHOWN), "HEAD..@{u}").out.splitlines())
        if behind > SHOWN:
            out.say("  … and %d more" % (behind - SHOWN))
        if not git(a, "merge", "--ff-only", "--quiet", "@{u}", capture=False).ok:
            die("couldn't update %s (above): changes of your own in files the update changes? git -C %s status says "
                "which; commit or stash them, then nexus --update. Nothing changed" % (a, a))
        out.say("nexus: updated %s to %s" % (a, describe(a, "HEAD")))
    launcher = os.path.join(a, "nexus")
    proc.flush()
    try:
        os.execv(launcher, [launcher, "--update", "--since", old] + (["-y"] if yes else []))
    except OSError as e:
        die("can't run %s (%s): apply the update with nexus --update --since %s" % (launcher, e.strerror, old[:12]))


def describe(a, rev):
    return one_line(git(a, "log", "-1", "--date=short", "--format=%h (%cd) %s", rev))


# --- 3, 4: the apply ---
def image_inputs(dockerfile):
    """{path: flavour} for each file of this repo an image is built from: the Dockerfile, compose.yml (its build
    section), and what the Dockerfile COPYs or ADDs from the build context, by the flavour whose stage copies it
    ("" for a stage both build on)."""
    from agentctl.domain import flavour as flavours
    inputs, stage = {"Dockerfile": "", "compose.yml": ""}, ""
    for line in dockerfile.splitlines():
        w = line.split()
        if not w:
            continue
        if w[0].upper() == "FROM":
            w = [x for x in w if not x.startswith("--")]
            stage = w[3] if len(w) >= 4 and w[2].upper() == "AS" else ""
        elif w[0].upper() in ("COPY", "ADD") and not any(x.startswith("--from=") for x in w[1:]):
            for src in [x for x in w[1:] if not x.startswith("--")][:-1]:
                inputs[src[2:] if src.startswith("./") else src] = stage if stage in flavours.FLAVOURS else ""
    return inputs


def read_at_start(path, p):
    """Whether persona P reads PATH (a file of this repo) only when its container is created or started."""
    return any(path == f and fl in ("", p.flavour) and used(p) for f, fl, used in START)


def image(p):
    return "agent-%s-%s" % (p.flavour, p.context)


def plan(changed, personas, restarts, inputs):
    """What a change asks of the personas: [(persona, its image's inputs that changed, why it restarts)] for each
    persona it touches, in their order. CHANGED: the paths git diff gives; RESTARTS: the personas nexus --config
    said to restart; INPUTS: image_inputs()."""
    todo = []
    for p in personas:
        build = [f for f in changed if inputs.get(f) in ("", p.flavour)]
        why = ((["its service"] if p.name in restarts else []) + (["its image"] if build else [])
               + [f for f in changed if read_at_start(f, p) and f not in build])
        if why:
            todo.append((p, build, why))
    return todo


def merged(rec, todo, now):
    """The record's builds and restarts still to do, with TODO's added: an image's changed inputs joined; a
    persona's reasons joined, its image left out (Docker says whether that one is due), and "at" moved to NOW
    when a reason is new, so only a start after the newest ask covers it."""
    build = {k: list(v) for k, v in rec.get("build", {}).items()}
    restart = {k: {"why": list(v["why"]), "at": v["at"]} for k, v in rec.get("restart", {}).items()}
    for p, files, why in todo:
        if files:
            have = build.setdefault(image(p), [])
            have += [f for f in files if f not in have]
        why = [w for w in why if w != "its image"]
        if why:
            e = restart.setdefault(p.name, {"why": [], "at": now})
            new = [w for w in why if w not in e["why"]]
            if new:
                e["why"] += new
                e["at"] = now
    return build, restart


def load_record(a):
    """update.json as nexus --update last saved it, or {} if there is none (or one it can't read: it starts over
    from what this run pulls)."""
    try:
        with open(os.path.join(a, RECORD), encoding="utf-8") as f:
            rec = json.load(f)
        if not isinstance(rec, dict) or not isinstance(rec.get("rev", ""), str) \
                or not all(isinstance(v, list) for v in rec.get("build", {}).values()) \
                or not all(isinstance(v, dict) and isinstance(v.get("why"), list) and isinstance(v.get("at"), int)
                           for v in rec.get("restart", {}).values()):
            return {}
        return rec
    except (OSError, ValueError, AttributeError):
        return {}


def save_record(a, rec):
    """update.json written whole: a temp file next to it, then renamed over it."""
    fd, tmp = tempfile.mkstemp(prefix=RECORD + ".", dir=a)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(rec, f, indent=2, sort_keys=True)
            f.write("\n")
        os.chmod(tmp, 0o644)
        os.replace(tmp, os.path.join(a, RECORD))
    except OSError as e:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        die("can't write %s/%s: %s" % (a, RECORD, e.strerror))


def started(container):
    """When CONTAINER last started, as a unix time, if it is running; None if it isn't (or isn't there)."""
    r = proc.run(["docker", "inspect", "-f", "{{.State.Running}} {{.State.StartedAt}}", container])
    run, _, at = r.out.strip().partition(" ")
    if not r.ok or run != "true":
        return None
    try:
        return calendar.timegm(time.strptime(at[:19], "%Y-%m-%dT%H:%M:%S"))
    except ValueError:
        return 0


def image_behind(container):
    """Whether CONTAINER runs an older image than the one its image name (its tag) names now."""
    r = proc.run(["docker", "inspect", "-f", "{{.Image}} {{.Config.Image}}", container])
    runs, _, ref = r.out.strip().partition(" ")
    now = proc.run(["docker", "image", "inspect", "-f", "{{.Id}}", ref]) if r.ok and ref else None
    return bool(now and now.ok and now.out.strip() and now.out.strip() != runs)


class Apply:
    def __init__(self, a, since, yes, out):
        self.a, self.since, self.yes, self.out = a, since, yes, out
        self.launcher = os.path.join(a, "nexus")
        self.rec = {}

    def say(self, line=""):
        self.out.say(line)

    def save(self, build, restart):
        self.rec = {"rev": self.head, "from": self.rec.get("from", "") if build or restart else "",
                    "build": build, "restart": restart}
        if not (build or restart):
            del self.rec["from"]
        save_record(self.a, self.rec)

    def run(self):
        from agentctl.adapters import personas_store
        a = self.a
        since = one_line(git(a, "rev-parse", "--verify", "--quiet", self.since + "^{commit}"))
        if not since:
            die("--since %s isn't a commit in %s" % (self.since, a))
        self.head = one_line(git(a, "rev-parse", "HEAD"))
        self.rec = load_record(a)
        kept = self.rec.get("rev", "")
        if kept and not git(a, "rev-parse", "--verify", "--quiet", kept + "^{commit}").ok:
            kept = ""       # a commit this clone doesn't have (a history rewritten, say): what was pulled counts
        bases = [b for b in (since, kept) if b and b != self.head]
        if bases and not kept:      # no record yet: one at what was there before, so a stop below loses nothing
            save_record(a, {"rev": bases[0], "build": {}, "restart": {}})
        if personas_store.where(a) == "none":
            self.save({}, {})
            if bases:
                self.say("nexus: no personas set up here yet, so nothing to apply: %s/nexus --config sets them up" % a)
            return 0
        if bases:
            personas, registry = self.account(bases)
        else:
            if not self.rec.get("build") and not self.rec.get("restart"):
                self.save({}, {})       # a first run here starts the record at HEAD
                return 0
            if not lock.take(a):
                die("nexus --config or an upgrade is running in %s; let it finish first" % a)
            personas = Context(self.out, a).personas()
            registry = os.environ.get("REGISTRY") or dotenv.get(os.path.join(a, ".env"), "REGISTRY") or "local"
        return self.offer(personas, "build" if registry == "local" else "pull", earlier=not bases)

    def account(self, bases):
        """nexus --config --yes in this process, then what the commits since BASES ask of the personas, added to
        what is still to do; the record saved at HEAD. The personas and the registry, as config left them."""
        from agentctl.configtools import scripted, staging
        from agentctl.configtools.cli import parse
        from agentctl.configtools.session import Session
        from agentctl.ui.output import Output
        a = self.a
        os.chdir(a)
        s = Session(a, parse(["--yes", "--no-build", "--no-link"]), Output("config"))
        try:
            scripted.run(s)
        finally:
            staging.cleanup()
        # A line a path: proc's output is $(…)'s, which drops NULs, so no -z; and none of this repo's paths has a newline.
        changed = []
        for b in bases:
            changed += [f for f in git(a, "-c", "core.quotePath=false", "diff", "--name-only", b, "HEAD").out.split("\n")
                        if f and f not in changed]
        with open(os.path.join(a, "Dockerfile"), encoding="utf-8", errors="surrogateescape") as f:
            inputs = image_inputs(f.read())
        build, restart = merged(self.rec, plan(changed, s.personas, s.restarts(), inputs), int(time.time()))
        if (build or restart) and not self.rec.get("from"):     # where what is still to do began: the oldest base
            self.rec["from"] = max(bases, key=lambda b: int(one_line(git(a, "rev-list", "--count", b + "..HEAD")) or 0))
        self.save(build, restart)
        self.say("")
        return s.personas, s.registry

    def offer(self, personas, verb, earlier):
        """What is due now, asked once, then done a step at a time, each step off the record as it is done.
        EARLIER: no new commit this run, so all of it is from an earlier one."""
        build = {k: v for k, v in self.rec.get("build", {}).items() if any(image(p) == k for p in personas)}
        restart = {k: v for k, v in self.rec.get("restart", {}).items() if any(p.name == k for p in personas)}
        due, let_off = [], []
        for p in personas:
            at = started(p.container)
            e = restart.get(p.name)
            if at is None:
                if e or image(p) in build:
                    let_off.append(p)
                restart.pop(p.name, None)
                continue
            why = list(e["why"]) if e and at < e["at"] else []
            if e and not why:
                restart.pop(p.name)      # started since it was asked for: that took it up
            if image(p) in build:
                why.append("its image, if the %s changes it" % verb)
            elif image_behind(p.container):
                why.append("its image")
            if why:
                due.append((p, why))
        self.save(build, restart)
        if not build and not due:
            for p in let_off:
                self.say("nexus: %s isn't running: its next start picks it up" % p.name)
            self.say("nexus: applied; nothing needs an image built or a persona restarted")
            return 0

        frm = self.rec.get("from", "")
        what = "%s..%s" % (frm[:7], self.head[:7]) if frm else "this update"
        if earlier:
            self.say("nexus: not applied yet, from an earlier nexus --update: %s. To apply it to the personas:" % what)
        else:
            self.say("nexus: to apply %s to the personas:" % what)
        for k, files in build.items():
            self.say("  %s %s: %s changed" % (verb, k, ", ".join(files)))
        for p, why in due:
            names = sessions(p.container)
            self.say("  restart %s: %s%s" % (p.name, ", ".join(why),
                                             "  (ends its sessions: %s)" % " ".join(names) if names else ""))
        for p in let_off:
            self.say("  %s isn't running: its next start picks it up%s" % (
                p.name, ", once its image is %s" % ("built" if verb == "build" else "pulled") if image(p) in build else ""))

        Context(self.out, self.a)     # SSH_AUTH_SOCK as nexus finds it: the shell's, else the usual agent sockets
        need = [p.name for p, _ in due if not p.ssh]
        if need and not is_socket(os.environ.get("SSH_AUTH_SOCK", "")):
            self.say("nexus: but restarting %s needs an ssh-agent, and this shell has none" % " ".join(need))
            answer = "n"
        elif self.yes:
            answer = "y"
        else:
            answer = _ask("Apply it now? [y/N] ") if os.isatty(0) else "n"
        if answer[:1] not in ("y", "Y"):
            self.say("nexus: not applied. It's kept: the next nexus --update offers it again. Or, when it suits you (a "
                     "restart ends that persona's tmux sessions), a step at a time:")
            for k in build:
                self.say("  nexus %s %s" % (next(p.name for p in personas if image(p) == k), verb))
            for p, _ in due:
                self.say("  nexus %s restart" % p.name)
            return 0

        for k in list(build):
            users = [p for p in personas if image(p) == k]
            self.say("-- %s %s, for %s" % (verb, k, ", ".join(p.name for p in users)))
            if not proc.run([self.launcher, users[0].name, verb], capture=False).ok:
                self.out.error_line("nexus: the %s of %s failed (see above); no persona restarted. Once that's fixed, "
                                    "nexus --update carries on from there" % (verb, k))
                raise Exit(1)
            del build[k]
            self.save(build, restart)
        done, bad = [], []
        for p, why in due:
            if why == ["its image, if the %s changes it" % verb] and not image_behind(p.container):
                self.say("-- %s: its image is the same as before; no restart" % p.name)
                continue
            if proc.run([self.launcher, p.name, "restart"], capture=False).ok:
                restart.pop(p.name, None)
                self.save(build, restart)
                done.append(p.name)
            else:
                bad.append(p.name)
        if bad:
            self.out.error_line("nexus: restarting %s failed (see above). Once that's fixed, nexus --update carries on "
                                "from there (or: %s)" % (" ".join(bad), "; ".join("nexus %s restart" % n for n in bad)))
            raise Exit(1)
        self.say("nexus: applied%s" % (": restarted " + " ".join(done) if done else ""))
        return 0


def sessions(container):
    """The tmux sessions in CONTAINER, but main (the one the entrypoint keeps)."""
    s = proc.run(["docker", "exec", container, "tmux", "list-sessions", "-F", "#{session_name}"]).out
    return [x for x in (s.split("\n")[:-1] if s.endswith("\n") else s.split("\n")) if x and x != "main"]
