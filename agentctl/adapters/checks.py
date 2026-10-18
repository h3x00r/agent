"""Whether a settings file is one its CLI can read: JSON for Claude Code, TOML for grok.

A broken one stops the CLI short in every persona it reaches, so config-tools checks before it writes.
Each check is a short Python program run as the bash tool ran it (py_on): with this VM's python3 if that
can import what the program needs (tomllib wants 3.11), else in a persona image already here
(`docker run … --entrypoint python3`), else not at all. The programs are spelt exactly as before, as the
goldens record what goes to docker.

check(path, fmt) → (rc, output): rc 0 fine, 3 broken (output says why), -1 nothing here could check,
anything else the check itself failed (counts as unchecked).
"""
import os
import re

from agentctl.adapters.docker import Docker
from agentctl.core import proc

JSON = 'import sys, json\ntry: json.load(sys.stdin.buffer)\nexcept ValueError as e: print(e); sys.exit(3)'
TOML = 'import sys, tomllib\ntry: tomllib.load(sys.stdin.buffer)\nexcept ValueError as e: print(e); sys.exit(3)'
GROK_STATUSLINE = ('import sys, tomllib\nd = tomllib.load(sys.stdin.buffer).get("ui")\n'
                   'sys.exit(4 if isinstance(d, dict) and "status_line" in d else 0)')
_PERSONA_IMAGE = re.compile(r".*/agent-(claude|grok)-(docker|ddev):.*")


def py_on(path, code):
    first = code.split("\n", 1)[0]
    if proc.ok(["python3", "-c", first]):
        with open(path, "rb") as f:
            r = proc.run(["python3", "-c", code], stdin=f, merge=True)
        return r.rc, r.out.rstrip("\n")
    image = next((n for n in Docker.image_names() if _PERSONA_IMAGE.fullmatch(n)), "")
    if image:
        return Docker.run_python(image, code, path)
    return -1, ""


def check(path, fmt):
    return py_on(path, JSON if fmt == "json" else TOML)


_S = "[ \t\r\f\v]"   # [[:space:]] within one line
_GROK_SL = re.compile(r'%s*(\[%s*"?ui"?%s*\.%s*"?status_line|(ui%s*\.%s*)?"?status_line"?%s*[.=])'
                      % ((_S,) * 7))


def grok_sets_statusline(path):
    """Whether grok settings at PATH have a status line of their own (then config-tools adds none): asked of
    tomllib, so any TOML spelling counts; without one here, its usual spellings (line by line, as grep)."""
    if not os.path.isfile(path):
        return False
    rc, _ = py_on(path, GROK_STATUSLINE)
    if rc == 4:
        return True
    if rc == 0:
        return False
    with open(path, "rb") as f:
        text = f.read().decode("utf-8", "surrogateescape")
    return any(_GROK_SL.match(line) for line in text.split("\n"))
