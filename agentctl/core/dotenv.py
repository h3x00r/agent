"""The .env files compose reads, read and rewritten as the bash tools did (get_kv/env_get, set_kv/env_set).

get():   the value compose would see: the LAST `KEY=` line (an `export ` prefix and spaces around `=` are
         fine, CRLF too), "…" with its escapes (\\\\ \\n \\t \\r \\$ \\"), '…' (only \\' unescaped), or a
         bare value ending at a `#` after a space. No ${VAR} expansion. Trailing newlines go, as $(…)
         dropped them.
updated(): the file's text with KEY set: the last active `KEY=` line replaced, else the first commented
         `# KEY=` (or `#KEY=`), else a new line at the end; other lines as they were (their CRs too),
         and a newline at the end.
quote(): a value as config-tools writes it, so get() gives it back verbatim: bare if plain, '…' if it has
         no ' or \\, else "…" with \\ " $ escaped.
"""
import re

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
    return v.rstrip("\n")


def get(path, key):
    try:
        with open(path, "rb") as f:
            return get_text(_decode(f.read()), key)
    except FileNotFoundError:
        return ""
    except IsADirectoryError:
        return ""


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
