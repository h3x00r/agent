"""Personas: what one is, and the rules its settings follow. No I/O here.

A Persona has a name, a flavour (claude | grok), a context (ddev | docker), a tmux colour, the statusline
on or off, its own projects root ("" for the VM's) and its ssh: "" (your ssh-agent, forwarded), "none",
or a key: personas/NAME.key (made by config-tools) or an absolute path.

Pitfalls (each pinned where named):
- valid_name() is a fullmatch: a trailing newline is no name (test_domain.Rules.test_names)
- a persona's own root has no whitespace (it goes into a line split on spaces); the VM's may
  (test_domain.Rules.test_roots_and_keys)
- a key's path without spaces, quotes or $: compose_render puts it into '…' as it is (golden
  config/cli-bad-ssh)
- parse_meta_line(): an unknown field ignored, a repeated one's last value kept
  (test_domain.Lines.test_reading_rules)
- norm_dir(): one trailing / goes, a ~user stays as it is (test_domain.Rules.test_norm_dir)
- next_colour(): past eight personas the colours come round again (test_domain.Rules.test_colours_and_names,
  golden config/cli-colours)
- from_record(): every key known and of its type, each value held to the rules a line was
  (test_adapters.PersonasJson.test_what_it_refuses)
- from_record(): no NUL, no surrogate that isn't a byte (golden config/m4-root-nul, golden nexus/m4-json-by-hand)
"""
import os
import re
from dataclasses import dataclass

from agentctl.core.shellcompat import read_array

FLAVOURS = ("claude", "grok")
CONTEXTS = ("ddev", "docker")
COLOURS = ("colour25", "colour28", "colour90", "colour130", "colour31", "colour124", "colour166", "colour61")
RESERVED = ("ls", "top", "all", "start")

NAME_RULE = ("a lowercase letter, then lowercase letters, digits or -; at most 31, not ending in -; "
             "not ls, top, all or start")
ROOT_RULE = "an absolute directory other than /, without ':' or spaces"
KEY_RULE = "an absolute path (or ~/…) without spaces, ':', quotes or $"

_SPACE = " \t\n\r\f\v"


@dataclass
class Persona:
    name: str
    flavour: str
    context: str
    colour: str
    statusline: bool = True
    root: str = ""
    ssh: str = ""

    @property
    def container(self):
        return "agent-" + self.name

    @property
    def volume(self):
        return "agents_%s-home" % self.name

    @property
    def own_key(self):
        """The path of its own ssh key, as given (relative to the checkout or absolute), or ""."""
        return self.ssh if self.ssh not in ("", "none") else ""


def valid_name(name):
    return (re.fullmatch(r"[a-z][a-z0-9-]{0,30}", name) is not None and not name.endswith("-")
            and name not in RESERVED)


def norm_dir(d, home):
    """A leading ~ or ~/ becomes HOME, one trailing / goes (but / stays /)."""
    if d == "~":
        d = home
    elif d.startswith("~/"):
        d = home + "/" + d[2:]
    if d != "/" and d.endswith("/"):
        d = d[:-1]
    return d


def root_ok(r):
    """The projects root is mounted as ROOT:ROOT: absolute, not /, no ':'."""
    return len(r) >= 2 and r.startswith("/") and ":" not in r


def persona_root_ok(r):
    """A persona's own root also goes into a line split on spaces: no whitespace either."""
    return root_ok(r) and not any(c in _SPACE for c in r)


def key_path_ok(name, k):
    if any(c in _SPACE + ":'\"$" for c in k):
        return False
    return (len(k) >= 2 and k.startswith("/")) or k == "personas/%s.key" % name


def valid_claude_version(v):
    return re.fullmatch(r"stable|latest|[0-9]+\.[0-9]+\.[0-9]+(-[^ \t\n\r\f\v]+)?", v, re.DOTALL) is not None


_REGISTRY = re.compile(r"[A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?(\.[A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?)*(:[0-9]+)?"
                       r"(/[a-z0-9]+((\.|_|__|-+)[a-z0-9]+)*)*")


def valid_registry(r):
    return r == "local" or _REGISTRY.fullmatch(r) is not None


def next_colour(personas):
    used = {p.colour for p in personas}
    for c in COLOURS:
        if c not in used:
            return c
    return COLOURS[len(personas) % len(COLOURS)]


def suggest_name(personas, flavour, context):
    names = {p.name for p in personas}
    n, i = "%s-%s" % (flavour, context), 2
    while n in names:
        n = "%s-%s-%d" % (flavour, context, i)
        i += 1
    return n


class BadLine(ValueError):
    pass


def parse_meta_line(line):
    """A `# persona:` line (without the prefix) as config-tools reads it: split on whitespace, the name
    first, then key=value fields (an unknown one ignored, a repeated one's last value kept), all checked."""
    words = read_array(line, " \t\n")
    name = words[0] if words else ""
    vals = {"flavour": "", "context": "", "colour": "", "statusline": "", "root": "", "ssh": ""}
    for kv in words[1:]:
        k, eq, v = kv.partition("=")
        if eq and k in vals:
            vals[k] = v
    f, c, col, sl, r, k = (vals[x] for x in ("flavour", "context", "colour", "statusline", "root", "ssh"))
    if (not valid_name(name) or f not in FLAVOURS or c not in CONTEXTS
            or re.fullmatch(r"colour[0-9]+", col) is None or sl not in ("yes", "no")
            or (r and not persona_root_ok(r)) or (k and k != "none" and not key_path_ok(name, k))):
        raise BadLine(name)
    return Persona(name, f, c, col, sl == "yes", r, k)


FIELDS = ("flavour", "context", "colour", "statusline", "root", "ssh")


class BadRecord(ValueError):
    """What is wrong with one persona in personas.json, as a phrase after its name."""


def _text_ok(v):
    """No NUL, and no surrogate but those that stand for a byte that isn't UTF-8 (surrogateescape)."""
    if "\0" in v:
        return False
    try:
        v.encode("utf-8", "surrogateescape")
    except UnicodeEncodeError:
        return False
    return True


def to_record(p):
    """The persona as personas.json holds it under its name: root and ssh only when it has them."""
    d = {"flavour": p.flavour, "context": p.context, "colour": p.colour, "statusline": p.statusline}
    if p.root:
        d["root"] = p.root
    if p.ssh:
        d["ssh"] = p.ssh
    return d


def from_record(name, d):
    """A personas.json entry, held to the rules a `# persona:` line was, and stricter: every key known,
    each of its type."""
    if not isinstance(d, dict):
        raise BadRecord("isn't an object")
    for k in d:
        if k not in FIELDS:
            raise BadRecord("has a key personas.json doesn't take: '%s'" % k)
    for k in FIELDS[:4]:
        if k not in d:
            raise BadRecord("has no %s" % k)
    for k in ("flavour", "context", "colour", "root", "ssh"):
        if k in d and not isinstance(d[k], str):
            raise BadRecord("has a %s that isn't a string" % k)
        if k in d and not _text_ok(d[k]):
            raise BadRecord("has a %s with a NUL or a \\u escape no file name can hold" % k)
    if not isinstance(d["statusline"], bool):
        raise BadRecord("has a statusline that isn't true or false")
    if not valid_name(name):
        raise BadRecord("isn't a persona name: it takes %s" % NAME_RULE)
    f, c, col, r, k = d["flavour"], d["context"], d["colour"], d.get("root", ""), d.get("ssh", "")
    if f not in FLAVOURS:
        raise BadRecord("has flavour '%s', not %s" % (f, " or ".join(FLAVOURS)))
    if c not in CONTEXTS:
        raise BadRecord("has context '%s', not %s" % (c, " or ".join(CONTEXTS)))
    if re.fullmatch(r"colour[0-9]+", col) is None:
        raise BadRecord("has colour '%s', not colourN" % col)
    if "root" in d and not persona_root_ok(r):
        raise BadRecord("has root '%s': a projects root is %s" % (r, ROOT_RULE))
    if "ssh" in d and k != "none" and not key_path_ok(name, k):
        raise BadRecord("has ssh '%s': it takes none, personas/%s.key, or an absolute path without spaces, ':', "
                        "quotes or $" % (k, name))
    return Persona(name, f, c, col, d["statusline"], r, k)


def key_file(p, checkout):
    """Its own key's path on the VM: a relative one is under the checkout."""
    k = p.own_key
    if not k:
        return ""
    return k if k.startswith("/") else os.path.join(checkout, k)
