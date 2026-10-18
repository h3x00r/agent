"""The lock config-tools and `nexus <persona> upgrade` share, so only one of them changes a checkout at a time.

It is flock(2) on the tracked file `config-tools` in the checkout, exclusive and not waiting: what the bash
tools took with `exec 9< file; flock -n 9`, so a bash and a Python run exclude each other too. Never
fcntl.lockf: POSIX record locks don't see flock locks at all. The fd is close-on-exec (as `9<&-` closed it
for children) and held until this process ends.

Pitfalls (each pinned where named):
- flock, never lockf (test_adapters.Lock.test_excludes_flock1)
- not waiting: a lock held elsewhere is False at once (test_adapters.Lock.test_one_holder_at_a_time, golden
  config/cli-lock)
- no child inherits the fd (golden nexus/upgrade-recreate, golden config/cli-yes-first-setup)
- a lock file that can't be opened is an error, not a lock held (golden nexus/edge-upgrade-lock-file)
"""
import fcntl
import os

from agentctl.core.errors import NexusError

_held = []   # the fds, kept open for the life of the process


def take(checkout, name="config-tools"):
    """True if the lock is now ours, False if another run holds it. A lock file that can't be opened is an
    error, as bash's `exec 9< file` failing was."""
    path = os.path.join(checkout, name)
    try:
        fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC)
    except OSError as e:
        raise NexusError("%s: %s" % (path, e.strerror))
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(fd)
        return False
    _held.append(fd)
    return True
