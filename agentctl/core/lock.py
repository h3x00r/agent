"""The lock config-tools and `nexus <persona> upgrade` share, so only one of them changes a checkout at a time.

It is flock(2) on the tracked file `config-tools` in the checkout, exclusive and not waiting: what the bash
tools took with `exec 9< file; flock -n 9`, so a bash and a Python run exclude each other too. Never
fcntl.lockf: POSIX record locks don't see flock locks at all. The fd is close-on-exec (as `9<&-` closed it
for children) and held until this process ends.
"""
import fcntl
import os

_held = []   # the fds, kept open for the life of the process


def take(checkout, name="config-tools"):
    """True if the lock is now ours, False if another run holds it."""
    fd = os.open(os.path.join(checkout, name), os.O_RDONLY | os.O_CLOEXEC)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(fd)
        return False
    _held.append(fd)
    return True
