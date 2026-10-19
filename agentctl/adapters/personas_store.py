"""Where the personas set up on this VM are kept, read as each tool has read them.

personas.json (in the checkout, not in git) holds them: {"version": 1, "personas": {NAME: {flavour,
context, colour, statusline, root?, ssh?}}}, in persona order (the order of their colours, of --list and
of the hints).
- load_json(): the personas, each held to persona.from_record's rules; a name or key that comes twice,
  a key it doesn't take, another version, or JSON that doesn't parse is BadPersonasJson, for both tools.
- dumps(): the file's text, as nexus --config writes it (indented, a newline at the end).

Before personas.json, they were the `# persona: NAME key=value …` lines in compose.personas.yml; nexus --config
reads those once, to move them into personas.json (and --list shows them until then):
- lines(): every such line, in file order, without the prefix and a CR (anywhere in the file; a last
  line without a newline isn't read, as `while read` didn't), the spaces around it trimmed.
- load(): the personas from them as config.sh read them: each line checked (persona.parse_meta_line), a
  name that comes back updating the one before.
A file that can't be read is an error (exit 2, as sed's); NUL bytes in it are dropped.

Pitfalls (each pinned where named):
- personas.json: a name or key twice, a key it doesn't take, another version, not JSON: refused
  (test_adapters.PersonasJson.test_what_it_refuses)
- personas.json keeps the personas' order, and a byte that isn't UTF-8 (surrogateescape) round trip
  (test_adapters.PersonasJson.test_a_round_trip_keeps_order_and_bytes)
- the old lines: CRLF (golden config/cli-list-bad-line)
- the old lines: a last line without a newline isn't read (test_boot.PersonasLines.test_a_last_line_without_a_newline)
- where(): the `# persona:` lines win over a personas.json beside them (golden config/m4-crash-window)
- where(): a generated file without personas.json is "lost" (golden nexus/m4-json-gone)
- a .bak named only when there is one (golden config/m4-json-deleted)
- version 1 as an integer: not true, not 1.0 (golden config/m4-version-true)
"""
import json
import os

from agentctl.adapters import compose_render
from agentctl.core.errors import NexusError
from agentctl.domain import persona as rules

PREFIX = "# persona: "


JSON = "personas.json"
VERSION = 1


class BadPersonasJson(Exception):
    """What is wrong with personas.json, as a phrase after its path."""


def _no_twice(pairs):
    d = {}
    for k, v in pairs:
        if k in d:
            raise BadPersonasJson("has '%s' twice" % k)
        d[k] = v
    return d


def load_json(path):
    try:
        with open(path, "rb") as f:
            text = f.read().decode("utf-8", "surrogateescape")
    except OSError as e:
        raise NexusError("can't read %s: %s" % (path, e.strerror), 2)
    try:
        data = json.loads(text, object_pairs_hook=_no_twice)
    except ValueError as e:
        raise BadPersonasJson("isn't valid JSON (%s)" % e)
    if not isinstance(data, dict):
        raise BadPersonasJson("isn't a JSON object")
    for k in data:
        if k not in ("version", "personas"):
            raise BadPersonasJson("has a key it doesn't take: '%s'" % k)
    if type(data.get("version")) is not int or data["version"] != VERSION:     # not true, not 1.0
        raise BadPersonasJson("is version %s; this checkout reads version %d" % (json.dumps(data.get("version")), VERSION))
    personas = data.get("personas")
    if not isinstance(personas, dict):
        raise BadPersonasJson("has no \"personas\" object")
    out = []
    for name, rec in personas.items():
        try:
            out.append(rules.from_record(name, rec))
        except rules.BadRecord as e:
            raise BadPersonasJson("persona '%s' %s" % (name, e))
    return out


def dumps(personas):
    data = {"version": VERSION, "personas": {p.name: rules.to_record(p) for p in personas}}
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


class BadPersonasFile(Exception):
    def __init__(self, name, line):
        super().__init__(name)
        self.name, self.line = name, line


def _text(path):
    """The file's text, NUL bytes dropped as bash's read and $(…) dropped them."""
    try:
        with open(path, "rb") as f:
            return f.read().replace(b"\0", b"").decode("utf-8", "surrogateescape")
    except OSError as e:
        raise NexusError("can't read %s: %s" % (path, e.strerror), 2)


COMPOSE = "compose.personas.yml"


def where(checkout):
    """Where CHECKOUT's personas are:
    - "lines": the `# persona:` lines of a compose.personas.yml from before personas.json. They win over a
      personas.json beside them: that one is from before a rollback, or from a run cut short between its two
      writes, and the lines are what the tools last ran on;
    - "json": personas.json;
    - "lost": a compose.personas.yml generated from a personas.json that is gone (its header says so, or a
      personas.json.bak is there);
    - "none": none set up."""
    j, c = os.path.join(checkout, JSON), os.path.join(checkout, COMPOSE)
    if lines(c):
        return "lines"
    if os.path.isfile(j):
        return "json"
    if os.path.isfile(c) and (os.path.lexists(j + ".bak") or _text(c).startswith(compose_render.HEADER)):
        return "lost"
    return "none"


def restore_hint(path):
    """What to do about a personas.json that is wrong or gone: its .bak, if nexus --config kept one."""
    return "put back %s.bak (the one nexus --config kept before it)" % path if os.path.isfile(path + ".bak") else ""


def lost(checkout):
    j, c = os.path.join(checkout, JSON), os.path.join(checkout, COMPOSE)
    hint = restore_hint(j)
    return ("%s is missing, and %s was generated from it: %s" % (j, c, hint) if hint else
            "%s is missing, and %s was generated from it: remove that, and set the personas up again with "
            "nexus --config" % (j, c))


def lines(path):
    """The `# persona:` lines, as `sed -n 's/\\r$//; s/^# persona: //p' FILE | while read -r line` gave them:
    CR and prefix gone, the spaces around each trimmed, and a last line without a newline not read."""
    if not os.path.isfile(path):
        return []
    out = []
    for l in _text(path).split("\n")[:-1]:      # the part after the last newline isn't a line `read` returns
        if l.endswith("\r"):
            l = l[:-1]
        if l.startswith(PREFIX):
            out.append(l[len(PREFIX):].strip(" \t"))
    return out


def load(path):
    ps = []
    for line in lines(path):
        try:
            p = rules.parse_meta_line(line)
        except rules.BadLine as e:
            raise BadPersonasFile(str(e), line)
        for i, q in enumerate(ps):
            if q.name == p.name:
                ps[i] = p
                break
        else:
            ps.append(p)
    return ps


