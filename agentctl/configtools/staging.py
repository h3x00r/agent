"""Staged files: every file config-tools writes is first written next to its target, checked with the rest,
and only then put in place, so a run that stops early changes nothing.

stage(target): a new temp file `TARGET.XXXXXXXX` in the target's directory, mode 600 (it may hold a
secret), removed at the end of the run whatever happens (cleanup()).
commit(staged, target, backup=None): the staged file in place. An existing target (or a symlink) is
rewritten through, so a link keeps pointing at its target and the file keeps its owner, mode and inode;
an unchanged one isn't touched at all. A new target is the staged file renamed (mode 600 stays).
bakname(path): PATH.bak, else .bak.2, .bak.3 … , so an older backup (it may hold a key) survives.
"""
import filecmp
import os
import shutil
import tempfile

_staged = []


def stage(target, source=None):
    d, base = os.path.split(target)
    fd, path = tempfile.mkstemp(prefix=base + ".", dir=d or ".")
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
    if os.path.lexists(target):
        if same(staged, target):
            os.unlink(staged)
            return
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
