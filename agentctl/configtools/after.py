"""What nexus --config does once the settings are saved; a failure here is a note, not a stop (the files are in
place already): the ssh keys asked for, the launcher linked, mkcert, the images built or pulled.
"""
import os
import shutil
import socket

from agentctl.adapters import ssh
from agentctl.configtools.host import ln_ok, mkdir_ok, read_or_empty, readlink_f
from agentctl.core import proc
from agentctl.core.shellcompat import printf_q


def ssh_keys(s):
    """The keys of the personas with ssh=new that aren't there yet."""
    # bash's ${HOSTNAME%%.*}: its own HOSTNAME unless one came in from the environment, even an empty one
    host = (os.environ["HOSTNAME"] if "HOSTNAME" in os.environ else socket.gethostname()).split(".", 1)[0]
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
