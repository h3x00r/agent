"""Where the personas set up on this VM are kept, read as each tool has read them.

Until personas.json takes over, they are the `# persona: NAME key=value …` lines in compose.personas.yml,
written by config-tools from its list.
- lines(): every such line, in file order, without the prefix and a CR (anywhere in the file; a last
  line without a newline isn't read, as `while read` didn't).
- load(): the personas as config-tools reads them: each line checked (persona.parse_meta_line), a name
  that comes back updating the one before.
- meta(name): the first line for NAME as nexus reads it, padded with a space at each end, or None (also
  when that first line has nothing after the name); and meta_get() the first `key=` value in it, as
  nexus's meta_get did.
A file that can't be read is an error (exit 2, as sed's); NUL bytes in it are dropped.

Pitfalls (each pinned where named):
- CRLF (golden nexus/basic-crlf-personas, golden config/cli-list-bad-line)
- lines(): spaces around a line trimmed, as `read` trims them (golden nexus/edge-ls-line-spaces)
- a last line without a newline: dropped by lines(), kept with unterminated=True
  (test_boot.PersonasLines.test_a_last_line_without_a_newline)
- meta(): the first line wins, a bare `# persona: NAME ` too (golden nexus/edge-persona-meta-empty, golden
  nexus/edge-ls-line-empty-meta)
- meta(): a space after the name, never a tab (golden nexus/edge-ls-line-tab)
- NUL bytes (golden nexus/edge-ls-line-nul)
- bytes that aren't UTF-8 kept (golden nexus/edge-ls-names-latin1, golden nexus/edge-upgrade-personas-bytes)
- exit 2 (golden nexus/edge-ls-dotenv-unreadable)
"""
import os

from agentctl.core.errors import NexusError
from agentctl.domain import persona as rules

PREFIX = "# persona: "


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


def lines(path, unterminated=False):
    """The `# persona:` lines, as `sed -n 's/\\r$//; s/^# persona: //p' FILE` gives them: CR and prefix
    gone. `while read -r line` (config-tools, nexus ls) never sees a last line without a newline, and trims
    the spaces around a line; `mapfile -t` (nexus top) keeps it as it is: unterminated=True."""
    if not os.path.isfile(path):
        return []
    raw = _text(path).split("\n")
    if not unterminated:
        raw = raw[:-1]                     # the part after the last newline isn't a line `read` returns
    elif raw and raw[-1] == "":
        raw = raw[:-1]
    out = []
    for l in raw:
        if l.endswith("\r"):
            l = l[:-1]
        if l.startswith(PREFIX):
            l = l[len(PREFIX):]
            out.append(l if unterminated else l.strip(" \t"))
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


def exists(path):
    return os.path.isfile(path)


def meta(path, name):
    """nexus's view: the first line naming NAME (the space after the name is required), CRLF-safe."""
    if not os.path.isfile(path):
        return None
    for l in _text(path).split("\n"):
        if l.endswith("\r"):
            l = l[:-1]
        if l.startswith(PREFIX + name + " "):
            m = " %s " % l[len(PREFIX + name + " "):]
            return None if m == "  " else m      # a bare `# persona: NAME ` is no persona, as for nexus
    return None


def meta_get(meta_line, key):
    """The value after the first ` KEY=` in a padded meta line, up to the next space; "" if there is none."""
    marker = " %s=" % key
    i = meta_line.find(marker)
    if i < 0:
        return ""
    v = meta_line[i + len(marker):]
    return v.split(" ", 1)[0]


def names(path):
    """The names, in file order, as nexus lists them in its "no persona" error (no CR stripped there)."""
    if not os.path.isfile(path):
        return []
    out = []
    for l in _text(path).split("\n"):
        if l.startswith(PREFIX):
            rest = l[len(PREFIX):]
            out.append(rest.split(" ", 1)[0])
    return out
