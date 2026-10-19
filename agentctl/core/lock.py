"""The lock `nexus --config` and `nexus <persona> upgrade` share, so only one of them changes a checkout at a
time.

It is flock(2) on the checkout directory itself, exclusive and not waiting (as `exec 9< DIR; flock -n 9`
takes it in bash). Never fcntl.lockf: POSIX record locks don't see flock locks at all. The fd is
close-on-exec, so no child holds it, and it is held until this process ends. (Before `nexus --config`, the
tools locked the tracked file config-tools; the directory is always there, whatever a checkout holds.)

Pitfalls (each pinned where named):
- flock, never lockf (test_adapters.Lock.test_excludes_flock1)
- not waiting: a lock held elsewhere is False at once (test_adapters.Lock.test_one_holder_at_a_time, golden
  config/cli-lock)
- no child inherits the fd (golden nexus/upgrade-recreate, golden config/cli-yes-first-setup)
- a checkout that can't be opened is an error, not a lock held (test_adapters.Lock.test_an_unopenable_checkout)
"""
import fcntl
import os

from agentctl.core.errors import NexusError

_held = []   # the fds, kept open for the life of the process


def take(checkout):
    """True if the lock is now ours, False if another run holds it. A checkout that can't be opened is an
    error, as bash's `exec 9< DIR` failing would be."""
    try:
        fd = os.open(checkout, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    except OSError as e:
        raise NexusError("%s: %s" % (checkout, e.strerror))
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(fd)
        return False
    _held.append(fd)
    return True
