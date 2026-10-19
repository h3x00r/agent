"""The .env files compose reads, read and rewritten as the bash tools did (get_kv/env_get, set_kv/env_set).

get():   the value compose would see: the LAST `KEY=` line (an `export ` prefix and spaces around `=` are
         fine, CRLF too), "…" with its escapes (\\\\ \\n \\t \\r \\$ \\"), '…' (only \\' unescaped), or a
         bare value ending at a `#` after a space. No ${VAR} expansion. NUL bytes and trailing newlines
         go, as $(…) dropped them. A path that isn't a regular file reads as empty (the bash tested -f);
         one that can't be read is an error (exit 2, as sed's).
updated(): the file's text with KEY set: the last active `KEY=` line replaced, else the first commented
         `# KEY=` (or `#KEY=`), else a new line at the end; other lines as they were (their CRs too),
         and a newline at the end.
quote(): a value as nexus --config writes it, so get() gives it back verbatim: bare if plain, '…' if it has
         no ' or \\, else "…" with \\ " $ escaped.

Pitfalls (each pinned where named):
- get() as the bash's sed reader, the last `KEY=` line winning (test_dotenv.Get.test_as_the_bash_reader_did,
  golden nexus/edge-ls-dotenv-forms)
- NUL bytes dropped (golden nexus/edge-ls-dotenv-nul)
- bytes that aren't UTF-8 kept as they are (golden nexus/edge-ls-dotenv-latin1, golden
  nexus/edge-upgrade-env-bytes)
- a fifo reads as empty (golden nexus/edge-ls-dotenv-fifo)
- an unreadable file: exit 2 (golden nexus/edge-ls-dotenv-unreadable, golden
  nexus/edge-upgrade-env-unreadable)
- updated() (test_dotenv.Updated.test_as_the_awk_did)
- quote() (test_dotenv.Quote.test_round_trip)
"""
import os
import re

from agentctl.core.errors import NexusError

_SPACE = "[ \t\n\r\f\v]"


def _decode(data):
    return data.decode("utf-8", "surrogateescape")


def _lines(text):
    """The lines sed and awk see: split on newlines, a last line without one included."""
    if text == "":
        return []
    lines = text.split("\n")
    if text.endswith("\n"):
        lines.pop()
    return lines


def get_text(text, key):
    prefix = re.compile(r"^%s*(export%s+)?%s%s*=%s*" % (_SPACE, _SPACE, re.escape(key), _SPACE, _SPACE))
    found = None
    for line in _lines(text):
        if line.endswith("\r"):
            line = line[:-1]
        m = prefix.match(line)
        if m:
            found = line[m.end():]
    if found is None:
        return ""
    v = found
    m = re.match(r'"((?:\\.|[^"\\])*)"', v)
    if m:
        v = m.group(1)
        v = v.replace("\\\\", "\x01")
        v = v.replace("\\n", "\n").replace("\\t", "\t").replace("\\r", "\r")
        v = re.sub(r'\\([$"])', r"\1", v)
        v = v.replace("\x01", "\\")
    else:
        m = re.match(r"'((?:\\.|[^'\\])*)'", v)
        if m:
            v = m.group(1).replace("\\'", "'")
        else:
            v = re.sub(_SPACE + r"+#.*", "", v, count=1)
            v = re.sub(_SPACE + r"+$", "", v)
    return v.replace("\0", "").rstrip("\n")


def get(path, key):
    if not os.path.isfile(path):
        return ""
    try:
        with open(path, "rb") as f:
            return get_text(_decode(f.read()), key)
    except OSError as e:
        raise NexusError("can't read %s: %s" % (path, e.strerror), 2)


def updated(text, key, line):
    """TEXT with LINE (a whole `KEY=value` line) where set_kv/env_set would put it."""
    lines = _lines(text)
    active = re.compile(r"^[ \t]*(export[ \t]+)?%s[ \t]*=" % re.escape(key))
    commented = re.compile(r"^# ?%s=" % re.escape(key))
    act = com = None
    for i, l in enumerate(lines):
        if active.search(l):
            act = i
        if com is None and commented.search(l):
            com = i
    at = act if act is not None else com
    if at is None:
        lines.append(line)
    else:
        lines[at] = line
    return "\n".join(lines) + "\n"


_PLAIN = re.compile(r"[A-Za-z0-9._@+:/,=-](?:[A-Za-z0-9 ._@+:/,=-]*[A-Za-z0-9._@+:/,=-])?")


def quote(value):
    if value == "" or _PLAIN.fullmatch(value):
        return value
    if "'" not in value and "\\" not in value:
        return "'%s'" % value
    return '"%s"' % value.replace("\\", "\\\\").replace('"', '\\"').replace("$", "\\$")
