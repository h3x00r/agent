"""Staged files: every file nexus --config writes is first written next to its target, checked with the rest,
and only then put in place, so a run that stops early changes nothing.

stage(target): a new temp file `TARGET.XXXXXXXX` in the target's directory, mode 600 (it may hold a
secret), removed at the end of the run whatever happens (cleanup()).
commit(staged, target, backup=None): the staged file in place. An existing target (or a symlink) is
rewritten through, so a link keeps pointing at its target and the file keeps its owner, mode and inode;
an unchanged one isn't touched at all. A new target is the staged file renamed (mode 600 stays).
replace(staged, target, backup=None): the staged file renamed into place, atomically (a reader sees the old
file or the new one, never half of one), the old one kept as BACKUP first; an unchanged target is left
alone. For personas.json, which nothing mounts, so its inode needn't stay.
bakname(path): PATH.bak, else .bak.2, .bak.3 … , so an older backup (it may hold a key) survives.

Pitfalls (each pinned where named):
- stage() (test_configtools.Staging.test_stage_is_private_and_named_like_mktemp)
- cleanup() on every way out, ^C included (golden config/ask-int-tty)
- commit() through a link (test_configtools.Staging.test_commit_writes_through_a_link_and_keeps_the_inode)
- an unchanged target (test_configtools.Staging.test_an_unchanged_target_is_left_alone)
- a new target (test_configtools.Staging.test_a_new_target_is_renamed_into_place)
- bakname() skips a dangling .bak link too (test_configtools.Staging.test_bakname_keeps_older_backups)
- replace() renames, keeping the old one as the backup, and leaves an unchanged target alone
  (test_configtools.Staging.test_replace_renames_into_place_and_keeps_the_old_one)
- stage() in a directory it can't write: one line, nothing written (golden config/m4-migrate-readonly)
"""
import filecmp
import os
import shutil
import tempfile

from agentctl.core.errors import NexusError

_staged = []


def stage(target, source=None):
    d, base = os.path.split(target)
    try:
        fd, path = tempfile.mkstemp(prefix=base + ".", dir=d or ".")
    except OSError as e:     # a read-only checkout: as config.sh's mktemp failing, one line and exit 1
        raise NexusError("can't make a file next to %s (%s), so nothing was written" % (os.path.abspath(target),
                                                                                       e.strerror))
    if not d:
        path = os.path.basename(path)   # spelt as mktemp spells it: no "./" in front
    _staged.append(path)
    with os.fdopen(fd, "wb") as f:
        if source is not None:
            with open(source, "rb") as s:
                shutil.copyfileobj(s, f)
    return path


def write(path, text):
    with open(path, "wb") as f:
        f.write(text.encode("utf-8", "surrogateescape"))


def read(path):
    with open(path, "rb") as f:
        return f.read().decode("utf-8", "surrogateescape")


def cleanup():
    for p in _staged:
        try:
            os.unlink(p)
        except FileNotFoundError:
            pass
    del _staged[:]


def bakname(path):
    b, i = path + ".bak", 2
    while os.path.lexists(b):
        b = "%s.bak.%d" % (path, i)
        i += 1
    return b


def same(a, b):
    try:
        return filecmp.cmp(a, b, shallow=False)
    except OSError:
        return False


def commit(staged, target, backup=None):
    """True if TARGET changed."""
    if os.path.lexists(target):
        if same(staged, target):
            os.unlink(staged)
            return False
        if backup and os.path.exists(target):
            shutil.copy2(target, backup)
        old = os.umask(0o077)
        try:
            with open(staged, "rb") as s, open(target, "wb") as t:
                shutil.copyfileobj(s, t)
        finally:
            os.umask(old)
        os.unlink(staged)
    else:
        os.rename(staged, target)
    return True


def replace(staged, target, backup=None):
    """True if TARGET changed."""
    if os.path.lexists(target):
        if same(staged, target):
            os.unlink(staged)
            return False
        if backup and os.path.exists(target):
            shutil.copy2(target, backup)
    os.rename(staged, target)
    return True
