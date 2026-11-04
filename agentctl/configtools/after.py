"""What nexus --config does once the settings are saved; a failure here is a note, not a stop (the files are in
place already): the ssh keys asked for, the launcher linked, the shared scratch root made, mkcert, the images built
or pulled, the telemetry collector tried.

Pitfalls (each pinned where named):
- the scratch root: made once a run, never touched when it is there (install -d would chmod and chown it); by
  mkdir when the parent is yours, else with sudo (-n under --yes); a failure is a note with the command, which never
  says `nexus NAME restart` (Session.restarts() reads that as a persona to restart)
  (test_configtools.ScratchRoot.test_branches, golden config/scratch-root-made, golden config/scratch-root-sudo)
- one there that you can't write in is a note only while a persona's dir is still to be made in it (one chowned
  away after they were made works on); one that isn't a directory, a note with no sudo (install -d would fail)
  (test_configtools.ScratchRoot.test_branches)
"""
import os
import shutil

from agentctl.adapters import ssh
from agentctl.adapters import telemetry as tele_io
from agentctl.configtools.host import ln_ok, mkdir_ok, read_or_empty, readlink_f, short_host
from agentctl.core import proc
from agentctl.core.shellcompat import printf_q
from agentctl.domain import scratch as scr


def ssh_keys(s):
    """The keys of the personas with ssh=new that aren't there yet."""
    host = short_host()
    for p in s.personas:
        k = p.ssh
        if k != "personas/%s.key" % p.name or os.path.exists(k):
            continue
        if ssh.make_key(k, "%s@%s (nexus)" % (p.name, host)):
            s.say("-- made %s, the ssh key of %s" % (k, p.name))
            then = "then git over ssh works inside"
            if p.name in s.loaded:
                then = "then `nexus %s restart` (a running one keeps its old key until then)" % p.name
            s.later.append("%s: add its public key on your git host, as a deploy key (a repo's settings; write access to push)" % p.name)
            s.later.append("    or to a machine user's account, %s:" % then)
            s.later.append("    " + read_or_empty(k + ".pub").rstrip("\n"))
        else:
            s.later.append("%s: making its ssh key failed (see above); retry with: ssh-keygen -t ed25519 -N '' -f %s"
                           % (p.name, printf_q("%s/%s" % (s.a, k))))


def _sudo(s):
    return ["sudo", "-n"] if s.o.yes else ["sudo"]


def link(s, target):
    """The launcher linked as TARGET (sudo if it has to be); then, wherever it goes, whether `nexus` finds it,
    and the old `agent` link gone."""
    o, launcher = s.o, s.launcher
    if target:
        if mkdir_ok(o.link) and ln_ok(launcher, target):
            pass
        else:
            s.say("-- linking %s needs sudo" % target)
            if (os.path.isdir(o.link) or proc.run(_sudo(s) + ["mkdir", "-p", o.link], capture=False).ok) and \
                    proc.run(_sudo(s) + ["ln", "-sfn", launcher, target], capture=False).ok:
                pass
            else:
                retry = "sudo ln -sfn %s %s" % (printf_q(launcher), printf_q(target))
                if not os.path.isdir(o.link):
                    retry = "sudo mkdir -p %s && %s" % (printf_q(o.link), retry)
                s.later.append("launcher: couldn't link %s; run: %s" % (target, retry))
    if o.link != "none":
        d = o.link[:-1] if o.link.endswith("/") else o.link
        if d not in os.environ.get("PATH", "").split(":"):
            s.later.append("launcher: %s isn't on your PATH" % d)
        a = shutil.which("nexus") or ""
        if a and readlink_f(a) != launcher:
            s.later.append("launcher: `nexus` runs %s, not this launcher; put %s first in PATH, or use %s" % (a, d, launcher))
        old = d + "/agent"
        if os.path.islink(old) and readlink_f(old) == os.path.join(s.a, "agent"):
            try:
                os.unlink(old)
                gone = True
            except OSError:
                gone = proc.run(_sudo(s) + ["rm", "-f", old], capture=False).ok
            if gone:
                s.say("-- removed %s, the launcher's old name (it is nexus now)" % old)
            else:
                s.later.append("launcher: remove its old link: sudo rm %s" % printf_q(old))


def scratch_root(s):
    """The shared scratch root (domain/scratch.py), made if it isn't there and a persona needs it: by mkdir when its
    parent is yours (mode 755, whatever the umask), else with sudo, as yours. Once a run (a menu saves often)."""
    if s.scratch_tried or not s.personas:
        return
    s.scratch_tried = True
    root = s.scratch_root()
    if not scr.root_ok(root):     # a bad one: refused before the save (Session.vm_problem)
        return
    if os.path.isdir(root):
        if not os.access(root, os.W_OK | os.X_OK) and \
                any(not os.path.isdir(scr.path(root, getattr(p, "name", p))) for p in s.personas):
            s.later.append("scratch: you can't write in the shared scratch root %s, so nexus can't make a persona's "
                           "dir in it; run: %s, %s" % (root, scr.install_cmd(root), scr.ELSEWHERE))
        return
    if os.path.lexists(root):
        s.later.append("scratch: the shared scratch root %s isn't a directory, and every persona needs it to start: %s"
                       % (root, scr.NOT_DIR))
        return
    if mkdir_ok(root):
        try:
            os.chmod(root, 0o755)
        except OSError:
            pass
        return
    s.say("-- creating the shared scratch root %s needs sudo" % root)
    if not proc.run(_sudo(s) + ["install", "-d", "-o", str(os.getuid()), "-g", str(os.getgid()), "-m", "755", root],
                    capture=False).ok:
        s.later.append("scratch: couldn't create the shared scratch root %s, which every persona needs to start; "
                       "run: %s, %s" % (root, scr.install_cmd(root), scr.ELSEWHERE))


def mkcert(s):
    if not proc.run(["mkcert", "-install"], capture=False).ok:
        s.later.append("ddev: `mkcert -install` failed (see above); run it again on this VM, then `nexus <persona> restart`")


def images(s, verb, personas=None):
    """`nexus <persona> build` (or pull) once for each flavour and context among PERSONAS (every one by
    default); 1 if one failed, 0 otherwise."""
    failed = 0
    seen = set()
    personas = s.personas if personas is None else personas
    for p in personas:
        k = "%s-%s" % (p.flavour, p.context)
        if k in seen:
            continue
        seen.add(k)
        users = ", ".join(q.name for q in s.personas if "%s-%s" % (q.flavour, q.context) == k)
        s.say("-- %s agent-%s, for %s" % (verb, k, users))
        if not proc.run([s.launcher, p.name, verb], capture=False).ok:
            failed = 1
            s.later.append("images: the %s of agent-%s failed (see above); retry with: %s %s %s"
                           % (verb, k, s.launcher, p.name, verb))
    return failed


def telemetry_check(s):
    """After a save that changed what the personas send with, or made one send (s.tele_check): the collector tried
    with an empty export, as the personas will send theirs; said in a line when it takes it, else a note on what is
    wrong. The personas work either way; what they send is lost while it fails."""
    t = s.tele
    if not (t["endpoint"] and s.tele_check):
        return
    ep = t["endpoint"]
    lost = "The personas work all the same, but what they send is lost until this is fixed."
    again = "nexus --config, then telemetry"
    ca = os.path.join(s.a, t["ca"]) if t["ca"] else ""
    why = tele_io.ca_problem(ca) if ca else ""
    kind, code = ("ca", "") if why else tele_io.probe(ep, t["token"], ca)
    if kind == "ok":
        s.say("-- telemetry: %s took a test export%s" % (ep, " with the token" if t["token"] else ""))
    elif kind == "ca":
        s.later.append("telemetry: the collector's CA file %s %s, so the personas couldn't send to %s. %s Give it again "
                       "with %s." % (t["ca"], why or "can't be loaded", ep, lost, again))
    elif kind == "auth":
        s.later.append("telemetry: %s refused the token (HTTP %s), so it would refuse the personas' telemetry too. %s "
                       "Set the right token with %s." % (ep, code, lost, again))
    elif kind == "status":
        s.later.append("telemetry: %s answered a test export (HTTP %s), but not as an OpenTelemetry collector does: is "
                       "it the collector's own OTLP/HTTP address (usually port 4318)? %s" % (ep, code, lost))
    elif kind == "cert":
        s.later.append("telemetry: %s has a certificate that %s doesn't vouch for (or one for another name), so the "
                       "personas couldn't send to it. %s Give the CA file its certificate is signed by with %s."
                       % (ep, "the CA given (%s)" % t["ca"] if t["ca"] else "no CA this VM trusts", lost, again))
    elif kind == "tls":
        s.later.append("telemetry: %s doesn't speak TLS (HTTPS) there, so the personas couldn't send to it: if that "
                       "port is plain HTTP, give its http:// address with %s. %s" % (ep, again, lost))
    else:
        s.later.append("telemetry: nothing answered at %s (is it down, or is the address wrong?). %s" % (ep, lost))
