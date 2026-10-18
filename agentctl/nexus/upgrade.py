"""nexus <persona> upgrade [VERSION|CHANNEL] [-y]: pin the flavour's CLI (CLAUDE_VERSION or GROK_VERSION in
.env) to VERSION, or to the newest of a channel, build or pull the image of every persona of that flavour
(one per context), check each really has that version, recreate the running ones (asked first; -y
doesn't ask), and remove the old images no container uses.

The pin is VM-wide. A failure, or INT/TERM/HUP, between the pin and the version check puts the old pin
back, and each image tag the run built: one that existed before points at its old image again, a new
one goes. A signal there waits for the step running (a build, say) to end, as bash's trap did, then
puts things back and exits 130. One upgrade or config-tools run at a time per checkout: they share the
lock on config-tools.

Pitfalls (each pinned where named):
- 130 for INT, TERM and HUP alike (golden nexus/edge-upgrade-signals-match)
- a signal during the restore restores again, then exits (golden nexus/edge-upgrade-term-restore)
- the lookup: one 20 s deadline for all of it, as --max-time (golden nexus/edge-upgrade-lookup-slow)
- a 3xx without a Location is a body; CURL_CA_BUNDLE is the trust store (golden nexus/edge-upgrade-lookup-300,
  golden nexus/edge-upgrade-lookup-ca)
- NUL bytes dropped from the body (golden nexus/edge-upgrade-lookup-nul)
- env_set() writes through, so .env keeps its link, mode and inode (golden nexus/edge-upgrade-env-symlink)
- a failed write keeps its temp file, and names it (golden nexus/upgrade-env-readonly)
- the question on stderr only when stdin is a terminal, read a byte at a time (golden nexus/upgrade-prompt,
  golden nexus/edge-upgrade-prompt-answers)
- peers() keeps the CRs (golden nexus/upgrade-crlf-personas)
- a pin exported in the shell is refused, an empty one too (golden nexus/upgrade-pin-exported)
- the lock (golden nexus/upgrade-lock)
"""
import os
import re
import signal
import tempfile
import urllib.request

from agentctl.core import dotenv, lock, proc, sigs
from agentctl.core.errors import Exit, Interrupted, NexusError
from agentctl.core.shellcompat import read_fields
from agentctl.domain import flavour as flavours
from agentctl.nexus.context import is_socket

VERSION = r"[0-9]+\.[0-9]+\.[0-9]+(-[A-Za-z0-9._]+)?"


def die(msg):
    raise NexusError(msg)


def first_line(text):
    return text.split("\n", 1)[0] if text else ""


def fetch(url, seconds=20):
    """`curl -fsSL --max-time 20 URL`: the body (bytes), or None. One deadline for all of it (connect, TLS,
    headers, body), as --max-time is; a 3xx that names no Location is a body like a 2xx, as curl -f takes
    it; CURL_CA_BUNDLE, if set, is the trust store, as for curl (SSL_CERT_FILE works for both)."""
    import ssl
    import urllib.error

    def expired(signum, frame):
        raise TimeoutError()
    try:
        tls = ssl.create_default_context(cafile=os.environ.get("CURL_CA_BUNDLE") or None)
    except (OSError, ssl.SSLError):
        return None
    old = signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        try:
            with urllib.request.urlopen(url, timeout=seconds, context=tls) as resp:
                return resp.read()
        except urllib.error.HTTPError as e:
            return e.read() if 300 <= e.code < 400 else None
    except Exception:  # noqa: BLE001  (any failure is "can't look it up", as curl -f's was)
        return None
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, old)


class Upgrade:
    def __init__(self, run, args):
        self.r, self.p, self.out, self.ctx = run, run.p, run.out, run.ctx
        self.args = args
        self.env_file = os.path.join(self.ctx.agents_dir, ".env")
        self.signalled = False
        self.refs, self.pre = [], {}

    def say(self, line):
        self.out.say(line)

    # --- compose and docker, as the bash launcher asked them ---
    def compose_q(self, persona, *args, capture="stdout"):
        """Compose for PERSONA; nothing here mounts the ssh-agent, so the socket may be a placeholder."""
        e = dict(os.environ)
        e["SSH_AUTH_SOCK"] = e.get("SSH_AUTH_SOCK") or "/dev/null"
        e["PROJECTS_ROOT"] = self.p.root
        return proc.run(self.ctx.compose_argv() + ["--profile", persona] + list(args), env=e, capture=capture)

    def image_ref(self, persona):
        r = self.compose_q(persona, "config", "--images", persona)
        return r.out.rstrip("\n") if r.ok else None

    @staticmethod
    def image_id(ref):
        r = proc.run(["docker", "image", "inspect", "-f", "{{.Id}}", ref])
        return r.out.rstrip("\n") if r.ok else None

    def cli_version(self, ref):
        return first_line(proc.run(["docker", "run", "--rm", "--network", "none", ref, self.cli, "--version"]).out)

    def env_set(self, key, value):
        """KEY=VALUE where compose reads it, written through, so .env keeps its link, owner, mode and inode;
        what it should hold goes to a temp file next to it first, kept (and named) if the write fails."""
        f = self.env_file
        try:
            fd, tmp = tempfile.mkstemp(prefix=os.path.basename(f) + ".", dir=os.path.dirname(f))
        except OSError:
            return False
        try:
            with open(f, "rb") as fh:
                text = fh.read().decode("utf-8", "surrogateescape")
            data = dotenv.updated(text, key, "%s=%s" % (key, value)).encode("utf-8", "surrogateescape")
            with os.fdopen(fd, "wb") as w:
                w.write(data)
        except OSError:
            os.unlink(tmp)
            return False
        try:
            with open(f, "wb") as fh:
                fh.write(data)
        except OSError:
            self.out.warn("writing %s failed; what it should hold is in %s" % (f, tmp))
            return False
        os.unlink(tmp)
        return True

    # --- signals in the pin-to-check window ---
    def on_signal(self, signum, frame):
        self.signalled = True

    def check(self):
        """Where bash ran a pending trap (`up_restore; exit 130`): after each command in the window, the
        restore's own included, so a signal during a restore restores again, then exits."""
        if self.signalled:
            self.signalled = False
            self.restore()
            raise Interrupted(130)

    def restore(self):
        ok = self.env_set(self.pin, self.old)
        self.check()
        if not ok:
            self.out.warn("couldn't put %s=%s back into %s: set it by hand" % (self.pin, self.old, self.env_file))
        for r in self.refs:
            pre = self.pre[r]
            if pre:
                same = self.image_id(r) == pre
                self.check()
                if not same:
                    ok = proc.run(["docker", "tag", pre, r], capture=False).ok
                    self.check()
                    if not ok:
                        self.out.warn("couldn't point %s back at its old image %s" % (r, pre[7:19]))
            else:
                proc.run(["docker", "image", "rm", r])
                self.check()

    def fail(self, msg):
        self.restore()
        die(msg)

    def peers(self):
        """The personas of this flavour (`grep '^# persona: ' FILE | while read -r _ _ name rest`: no CR
        stripped), those with ssh of their own, and the first of each context (it builds that image)."""
        with open(self.ctx.personas_file, "rb") as f:
            text = f.read().decode("utf-8", "surrogateescape")
        peers, builders, own, seen = [], [], set(), set()
        for line in text.split("\n"):        # grep ends a last line without a newline with one: it is read
            if not line.startswith("# persona: "):
                continue
            _, _, name, rest = read_fields(line, " \t\n", 4)
            if " flavour=%s " % self.p.flavour not in " %s " % rest:
                continue
            peers.append(name)
            if " ssh=" in " " + rest:
                own.add(name)
            i = rest.find("context=")
            ctx = (rest[i + len("context="):] if i >= 0 else rest).split(" ", 1)[0] or "none"
            if ctx not in seen:
                seen.add(ctx)
                builders.append(name)
        return peers, builders, own

    def run(self):
        want, yes = "", False
        for a in self.args:
            if a in ("-y", "--yes"):
                yes = True
            elif a.startswith("-"):
                die("upgrade: unknown option '%s'" % a)
            else:
                if want:
                    die("upgrade takes one VERSION or CHANNEL, not '%s %s'" % (want, a))
                want = a
        fl = flavours.get(self.p.flavour)
        if not self.p.flavour or fl is None:
            die("upgrade: no CLI known for %s's flavour '%s'" % (self.p.name, self.p.flavour))
        self.cli, self.pin, tagvar, channels = fl.cli, fl.pin_var, fl.tag_var, fl.channels
        a = self.ctx.agents_dir
        if not os.path.isfile(self.env_file):
            die("no %s yet: run %s/config-tools" % (self.env_file, a))
        if self.pin in os.environ:
            die("%s is exported in this shell and beats %s: unset it, then upgrade" % (self.pin, self.env_file))
        if not lock.take(a):
            die("config-tools or another upgrade is running in %s; let it finish first" % a)

        # The version: the one given, or the newest of a channel.
        version = want[1:] if want.startswith("v") else want
        version = version or channels[0]
        if version in channels:
            url = fl.newest_url(version)
            body = fetch(url)
            if body is None:
                die("can't look up the newest %s at %s" % (self.cli, url))
            version = re.sub(r"[ \t\n\r\f\v]", "", body.replace(b"\0", b"").decode("utf-8", "surrogateescape"))
            if not re.fullmatch(VERSION, version):
                die("%s gave '%s', not a version" % (url, version))
        elif not re.fullmatch(VERSION, version):
            die("upgrade: '%s' is neither a version (X.Y.Z) nor a channel of %s (%s)" % (want, self.cli, " ".join(channels)))

        # What the peers and their image names run now.
        peers, builders, own = self.peers()
        olds, was = [], ""
        for name in peers:
            r = self.image_ref(name)
            if r is None:
                die("compose can't resolve %s's image (see above)" % name)
            i = self.image_id(r)
            if i is not None:
                olds.append(i)
                was = was or self.cli_version(r)
            ci = proc.run(["docker", "inspect", "-f", "{{.Image}}", "agent-" + name])
            if ci.ok:
                olds.append(ci.out.rstrip("\n"))
        self.old = dotenv.get(self.env_file, self.pin) or "latest"   # no pin yet meant latest, compose's default
        self.say("nexus: %s for %s: %s -> %s" % (self.cli, " ".join(peers), was or self.old, version))

        # Pin it, then build or pull each image; a failure or a signal puts the old pin back.
        handlers = sigs.trap((signal.SIGINT, signal.SIGTERM, signal.SIGHUP), self.on_signal)
        try:
            if not self.env_set(self.pin, version):
                die("can't write %s" % self.env_file)
            self.check()
            for name in builders:
                r = self.image_ref(name)
                self.check()
                if r is None:
                    self.fail("compose can't resolve %s's image (see above); %s is back to %s" % (name, self.pin, self.old))
                self.refs.append(r)
                self.pre[r] = self.image_id(r) or ""
                self.check()
            registry = os.environ.get("REGISTRY") or dotenv.get(self.env_file, "REGISTRY")
            verb = "build" if (registry or "local") == "local" else "pull"
            for name in builders:
                args = ["build", name] if verb == "build" else ["pull", "--policy", "always", name]
                ok = self.compose_q(name, *args, capture=False).ok
                self.check()
                if not ok:
                    self.fail("the %s for %s failed (see above); %s is back to %s" % (verb, name, self.pin, self.old))
            # Each image must really have that version: with a fixed tag, or a registry image that isn't that
            # version, .env would name a version nothing runs.
            tag = os.environ.get(tagvar) or dotenv.get(self.env_file, tagvar)
            ref_new, id_new = {}, {}
            for name in peers:
                r = self.image_ref(name)
                self.check()
                i = self.image_id(r) if r is not None else None
                self.check()
                if r is None or i is None:
                    self.fail("no image for %s after the %s; %s is back to %s" % (name, verb, self.pin, self.old))
                ref_new[name], id_new[name] = r, i
            now = ""
            for name in builders:
                n = self.cli_version(ref_new[name])
                self.check()
                if " %s " % version not in " %s " % n:
                    self.restore()
                    if not n:
                        die("`%s --version` prints nothing in %s (try: docker run --rm %s %s --version); %s is back to %s"
                            % (self.cli, ref_new[name], ref_new[name], self.cli, self.pin, self.old))
                    die("%s has %s, not %s%s; %s is back to %s" % (
                        ref_new[name], n, version, " (it is tagged by %s=%s, not the version)" % (tagvar, tag) if tag else "",
                        self.pin, self.old))
                now = now or n
        finally:
            sigs.untrap(handlers)
        self.say("nexus: %s=%s in %s; %s in: %s" % (self.pin, version, self.env_file, now or "%s %s" % (self.cli, version),
                                                    "".join(ref_new[n] + " " for n in builders)))
        r = ref_new[builders[0]]
        if r.rsplit(":", 1)[-1] != version:
            if not tag or (self.p.flavour == "grok" and tag == "latest"):   # a personas file or .env from before upgrade
                self.out.warn("%s is still tagged %s: re-run %s/config-tools once, and the tags follow %s from then on"
                              % (r, r.rsplit(":", 1)[-1], a, self.pin))
            elif tag == "latest":
                self.out.warn("%s is tagged by %s=latest in %s, not by the version: remove that line, and the tags "
                              "follow %s" % (r, tagvar, self.env_file, self.pin))

        # Containers still on an older image. A stopped one is removed: its next start creates it anew.
        run, later, failed, gone, kept = [], [], [], [], []
        for name in peers:
            ci = proc.run(["docker", "inspect", "-f", "{{.Image}}", "agent-" + name])
            if not ci.ok or ci.out.rstrip("\n") == id_new[name]:
                continue
            if proc.run(["docker", "inspect", "-f", "{{.State.Running}}", "agent-" + name],
                        capture="stdout").out.rstrip("\n") == "true":
                run.append(name)
            elif proc.run(["docker", "rm", "agent-" + name], capture="stdout").ok:
                self.say("nexus: removed the stopped agent-%s; its next start uses the new image" % name)
        if run:
            self.say("nexus: still on the old image, so recreating ends their tmux sessions (and the %s in them):" % self.cli)
            for name in run:
                s = proc.run(["docker", "exec", "agent-" + name, "tmux", "list-sessions", "-F", "#{session_name}"]).out
                names = [x for x in (s.split("\n")[:-1] if s.endswith("\n") else s.split("\n")) if x and x != "main"]
                self.say("  %s%s" % (name, "  (sessions: %s)" % " ".join(names) if names else ""))
            answer = "y"
            need = " ".join(n for n in run if n not in own)
            if need and not is_socket(os.environ.get("SSH_AUTH_SOCK", "")):
                self.say("nexus: but recreating %s needs an ssh-agent, and this shell has none" % need)
                answer = "n"
            elif not yes:
                answer = "n"
                if os.isatty(0):
                    answer = _ask("Recreate them now? [y/N] ")
            if answer[:1] in ("y", "Y"):
                # Through the launcher, so each gets its own context's preflight (ddev dirs, mkcert CA).
                for name in run:
                    if not proc.run([os.path.join(a, "nexus"), name, "restart"], capture=False).ok:
                        failed.append(name)
            else:
                later = list(run)

        # The old images, unless a container still uses them: their tags under these images' names, or the
        # image itself if a rebuild left it without one.
        new_ids = set(id_new.values())
        repos = {ref_new[n].rsplit(":", 1)[0] for n in peers}
        for i in sorted(set(olds)):
            if i in new_ids:
                continue
            t = proc.run(["docker", "image", "inspect", "-f", "{{range .RepoTags}}{{.}} {{end}}", i])
            if not t.ok:
                continue
            tags = t.out.split()
            if not tags:
                (gone if proc.run(["docker", "image", "rm", i]).ok else kept).append(i[7:19])
                continue
            for tg in tags:
                if tg.rsplit(":", 1)[0] in repos:
                    (gone if proc.run(["docker", "image", "rm", tg]).ok else kept).append(tg)
        if gone:
            self.say("nexus: removed the old image: " + " ".join(gone))
        if kept:
            self.say("nexus: kept the old image (a container still uses it): " + " ".join(kept))
        if failed:
            self.out.error_line("nexus: recreating %s failed (see above); once that's fixed:%s"
                                % (" ".join(failed), "".join(" nexus %s restart;" % n for n in failed)))
            raise Exit(1)
        if later:
            self.say("nexus: not recreated: %s. When they're idle, `nexus %s upgrade %s` recreates them and removes "
                     "the old image" % (" ".join(later), self.p.name, version))
        else:
            self.say("nexus: done; %s run %s" % (" ".join(peers), now or "%s %s" % (self.cli, version)))


def _ask(prompt):
    """bash's `read -r -p PROMPT a || a=n`: the prompt on stderr, one line; the end of input is "n"."""
    proc.flush()
    os.write(2, prompt.encode())
    buf = bytearray()
    while True:
        b = os.read(0, 1)
        if not b:
            return "n"
        if b == b"\n":
            return buf.decode("utf-8", "surrogateescape").strip(" \t")
        buf.extend(b)
