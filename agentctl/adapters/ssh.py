"""ssh on the VM: the forwarded agent, and keys of a persona's own.

agent_status(): what `ssh-add -l` says of the agent in this shell: 0 keys there, 1 no keys, 2 no agent
answering, 127 no ssh-add at all.
key_problem(): why a private key wouldn't work inside a persona ("" when it would): ssh wants it to be
the user's own, private (mode ?00) and without a passphrase.
make_key(): an ed25519 key without a passphrase, as nexus --config makes one (an existing one is never
replaced: the caller checks).

Pitfalls (each pinned where named):
- key_problem(): mode ?00, the user's own, no passphrase (golden config/cli-bad-ssh)
- no ssh-keygen: said, not a crash (golden config/cli-no-tools)
"""
import os
import shutil
import subprocess

from agentctl.core import proc


def agent_status():
    if shutil.which("ssh-add") is None:
        return 127
    r = proc.run(["ssh-add", "-l"])
    return r.rc


def have_keygen():
    return shutil.which("ssh-keygen") is not None


def key_problem(path):
    if not have_keygen():
        return "checking it needs ssh-keygen (openssh-client) on this VM"
    if not (os.path.isfile(path) and os.access(path, os.R_OK)):
        return "no readable file there"
    st = os.stat(path)
    if st.st_uid != os.getuid():
        return "it isn't yours, and the persona runs as you (uid %d)" % os.getuid()
    mode = "%o" % (st.st_mode & 0o7777)
    if len(mode) != 3 or mode[1:] != "00" or mode[0] not in "01234567":
        return "others can read it (mode %s), so ssh won't use it: chmod 600 it" % mode
    if not proc.ok(["ssh-keygen", "-y", "-P", "", "-f", path]):
        return "it has a passphrase, or isn't a private key: nothing inside could type a passphrase"
    return ""


def make_key(path, comment):
    """True if the key and its .pub are there afterwards."""
    r = proc.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", comment, "-f", path],
                 stdin=subprocess.DEVNULL, capture=False)
    return r.ok and os.path.isfile(path + ".pub")
