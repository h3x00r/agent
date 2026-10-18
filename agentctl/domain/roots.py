"""Projects roots: which directory a session or a headless run starts in, given the one you're in.

A persona's projects root is mounted at its own path inside. A running container keeps the root it was
created with (compose applies another only when it creates the container, at a restart or the start of a
stopped one); place() works with both: the root configured now (ROOT) and, when the running container
has another, that one (OLD).

The rules, as nexus has always applied them:
- OLD set: a dir in OLD (by its spelling, or by the real path through a symlink) is there, in OLD's
  spelling, and works with a warning. Outside OLD, a headless run refuses, and an attach can only reach a
  session the container runs already (Placement.inside is False).
- Otherwise: a dir in ROOT (or the same place by another path, which gets ROOT's spelling) is used as it
  is. Truly outside, a session starts in ROOT (with a note) and a headless run refuses.
Nothing here touches the filesystem: realpath is passed in (shellcompat.realpath_e in real use).
"""
from dataclasses import dataclass

from agentctl.core.shellcompat import under


@dataclass
class Placement:
    dir: str
    inside: bool = True          # False: not in the root the running container has
    outside_root: bool = False   # HERE wasn't in ROOT: the session starts in ROOT instead
    refused: bool = False        # a headless run can't run here


def moved(persona, old, new, clause=""):
    """What to say of a running persona whose projects root is OLD, not NEW (NEW may name its source)."""
    return "%s still runs with the projects root it was created with, %s%s: nexus %s restart applies %s, and " \
           "ends its sessions" % (persona, old, ", " + clause if clause else "", persona, new)


def _in(here, root, realpath):
    """HERE in ROOT's spelling if it is in ROOT (directly or through a symlink on either side), else None."""
    if under(here, root):
        return here
    rr, rh = realpath(root), realpath(here)
    if rr and rh and under(rh, rr):
        return root + rh[len(rr):]
    return None


def place(here, root, old, realpath, headless=False):
    if old:
        d = _in(here, old, realpath)
        if d is not None:
            return Placement(d)
        if headless:
            return Placement(here, inside=False, refused=True)
    d = _in(here, root, realpath)
    if d is not None:
        return Placement(d, inside=not old)
    if headless:
        return Placement(here, inside=not old, outside_root=True, refused=True)
    return Placement(root, inside=not old, outside_root=True)
