"""Personas: what one is, and the rules its settings follow. No I/O here.

A Persona has a name, a flavour (claude | grok), a context (ddev | docker), a tmux colour, the statusline
on or off, its own projects root ("" for the VM's), its ssh: "" (your ssh-agent, forwarded), "none",
or a key: personas/NAME.key (made by nexus --config) or an absolute path; and, for a claude persona, its
permissions: bypass (personas/claude.managed.json's bypassPermissions; the default) or ask
(personas/claude.ask.json's mode over it); and whether it sends telemetry (OpenTelemetry, to the collector
TELEMETRY_ENDPOINT in .env names; domain/telemetry.py), and with it its tool details (the commands, file paths and
other input its tools ran with) and its tool content (what they read, wrote and printed), each when .env doesn't
turn it on for every persona (TELEMETRY_TOOL_DETAILS, TELEMETRY_TOOL_CONTENT); and its port range, FROM-TO
(NEXUS_PORTS in its container): the ports its stacks publish on the VM's network, which every persona shares.
nexus --config gives each persona without one a block of 100 of its own (next_ports()); --ports sets one.

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
- permissions: written only when it is ask, so a personas.json from before it reads the same
  (test_adapters.PersonasJson.test_a_round_trip_keeps_order_and_bytes); ask only for a claude persona
  (test_adapters.PersonasJson.test_what_it_refuses, golden config/perm-refused)
- telemetry: written only when true, so a personas.json from before it reads the same; true or false, nothing else
  (test_adapters.PersonasJson.test_a_round_trip_keeps_order_and_bytes, test_adapters.PersonasJson.test_what_it_refuses)
- tool_details and tool_content: written only when true, true or false, and only for a persona that sends
  telemetry (test_adapters.PersonasJson.test_what_it_refuses, golden config/telemetry-hand-edits)
- ports: written only when set, so a personas.json from before it reads the same; FROM-TO in ASCII digits, no
  leading 0, 1024 <= FROM <= TO <= 65535 (test_adapters.PersonasJson.test_a_round_trip_keeps_order_and_bytes,
  test_adapters.PersonasJson.test_what_it_refuses, test_domain.Rules.test_ports)
- next_ports(): the block of 100 after the highest one in use (a range set by hand counts, rounded up to the next
  block), never a lower free one, so a removed persona's range (its container may still publish there) isn't handed
  on, unless it was the highest; past 65535, the first free block from 20000; none free, ""
  (test_domain.Rules.test_next_ports)
"""
import os
import re
from dataclasses import dataclass

from agentctl.core.shellcompat import read_array

FLAVOURS = ("claude", "grok")
CONTEXTS = ("ddev", "docker")
COLOURS = ("colour25", "colour28", "colour90", "colour130", "colour31", "colour124", "colour166", "colour61")
RESERVED = ("ls", "top", "all", "start")
PERMISSIONS = ("bypass", "ask")

NAME_RULE = ("a lowercase letter, then lowercase letters, digits or -; at most 31, not ending in -; "
             "not ls, top, all or start")
ROOT_RULE = "an absolute directory other than /, without ':' or spaces"
KEY_RULE = "an absolute path (or ~/…) without spaces, ':', quotes or $"
PORTS_RULE = "FROM-TO, two ports from 1024 to 65535 with FROM no higher than TO (e.g. 20000-20099)"
PORTS_BASE, PORTS_BLOCK, PORTS_MAX = 20000, 100, 65535

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
    permissions: str = "bypass"
    telemetry: bool = False
    tool_details: bool = False
    tool_content: bool = False
    ports: str = ""           # FROM-TO, or "" for none (yet): last, as the positional calls don't give it

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


def port_range(v):
    """FROM-TO as two ints, or None when it isn't one (PORTS_RULE)."""
    m = re.fullmatch(r"([1-9][0-9]{3,4})-([1-9][0-9]{3,4})", v)
    if m is None:
        return None
    a, b = int(m.group(1)), int(m.group(2))
    return (a, b) if 1024 <= a <= b <= PORTS_MAX else None


def ports_overlap(p, personas):
    """The first other persona whose range overlaps P's, or None."""
    r = port_range(p.ports)
    for q in personas:
        o = port_range(q.ports) if q.name != p.name else None
        if r and o and r[0] <= o[1] and o[0] <= r[1]:
            return q
    return None


def next_ports(personas):
    """A block of PORTS_BLOCK ports for a new range: the one after the highest in use (from PORTS_BASE), or, past
    PORTS_MAX, the first from PORTS_BASE that no range overlaps; "" when none is free."""
    used = [r for r in (port_range(p.ports) for p in personas) if r]
    top = max([b for _, b in used] + [PORTS_BASE - 1])
    a = PORTS_BASE + -(-(top + 1 - PORTS_BASE) // PORTS_BLOCK) * PORTS_BLOCK     # rounded up to a block
    starts = [a] if a + PORTS_BLOCK - 1 <= PORTS_MAX else range(PORTS_BASE, PORTS_MAX - PORTS_BLOCK + 2, PORTS_BLOCK)
    for a in starts:
        if not any(a <= y and x <= a + PORTS_BLOCK - 1 for x, y in used):
            return "%d-%d" % (a, a + PORTS_BLOCK - 1)
    return ""


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
    """A `# persona:` line (without the prefix) as nexus --config reads it: split on whitespace, the name
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


TOOL_FIELDS = ("tool_details", "tool_content")     # what telemetry may carry besides its defaults (domain/telemetry.py)
FIELDS = ("flavour", "context", "colour", "statusline", "root", "ssh", "permissions", "telemetry") + TOOL_FIELDS \
    + ("ports",)


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
    """The persona as personas.json holds it under its name: root and ssh only when it has them, permissions
    only when it asks, telemetry only when it sends, tool_details and tool_content only when it sends them, ports
    only when it has a range."""
    d = {"flavour": p.flavour, "context": p.context, "colour": p.colour, "statusline": p.statusline}
    if p.root:
        d["root"] = p.root
    if p.ssh:
        d["ssh"] = p.ssh
    if p.permissions != "bypass":
        d["permissions"] = p.permissions
    if p.telemetry:
        d["telemetry"] = True
        for k in TOOL_FIELDS:
            if getattr(p, k):
                d[k] = True
    if p.ports:
        d["ports"] = p.ports
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
    for k in ("flavour", "context", "colour", "root", "ssh", "permissions", "ports"):
        if k in d and not isinstance(d[k], str):
            raise BadRecord("has a %s that isn't a string" % k)
        if k in d and not _text_ok(d[k]):
            raise BadRecord("has a %s with a NUL or a \\u escape no file name can hold" % k)
    if not isinstance(d["statusline"], bool):
        raise BadRecord("has a statusline that isn't true or false")
    if not isinstance(d.get("telemetry", False), bool):
        raise BadRecord("has a telemetry that isn't true or false")
    for k in TOOL_FIELDS:
        if not isinstance(d.get(k, False), bool):
            raise BadRecord("has a %s that isn't true or false" % k)
        if d.get(k, False) and not d.get("telemetry", False):
            raise BadRecord('has "%s": true without "telemetry": true (only a persona that sends telemetry can send '
                            '%s)' % (k, k.replace("_", " ")))
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
    perm = d.get("permissions", "bypass")
    if perm not in PERMISSIONS:
        raise BadRecord("has permissions '%s', not bypass or ask" % perm)
    if perm == "ask" and f != "claude":
        raise BadRecord("has permissions 'ask', which only a claude persona takes (grok's come from personas/grok.toml)")
    if "ports" in d and port_range(d["ports"]) is None:
        raise BadRecord("has ports '%s': it takes %s" % (d["ports"], PORTS_RULE))
    return Persona(name, f, c, col, d["statusline"], r, k, perm, d.get("telemetry", False), d.get("tool_details", False),
                   d.get("tool_content", False), d.get("ports", ""))


def key_file(p, checkout):
    """Its own key's path on the VM: a relative one is under the checkout."""
    k = p.own_key
    if not k:
        return ""
    return k if k.startswith("/") else os.path.join(checkout, k)
