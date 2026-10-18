"""What the bash versions of the tools did with paths, fields and quoting, done the same way here.

The tools were bash scripts; their behaviour is the contract (tests/run.sh, tests/golden). Where Python's
own helpers differ from what bash or coreutils did, the difference lives here, once:
- lexical(): `realpath -ms`: absolute, `.`/`..`/repeated slashes resolved, a leading `//` too
  (os.path.normpath keeps that), no symlink followed;
- realpath_e(): `realpath -e`: every component must exist (os.path.realpath doesn't check);
- under(): bash's `[[ "$p/" == "$root"/* ]]`, a string test, never os.path.commonpath;
- bash_basename(): `basename`, which ignores trailing slashes (os.path.basename("x/") is "");
- read_fields(): `IFS=… read -r a b c`, field splitting as bash's read does it: IFS whitespace (a tab)
  collapses and is trimmed, any other IFS character delimits one field, and the last variable takes the
  rest of the line;
- printf_q(): bash's `printf %q`, which shlex.quote doesn't match;
- ere(): bash's `[[ $s =~ ^…$ ]]`: POSIX ERE, so `$` is the end of the string, never before a newline.
  Patterns spell their classes in ASCII ([0-9], [ \\t…]) and never use \\d or \\s, which match more.
"""
import os
import re

IFS_WHITESPACE = " \t\n"
SPACE = " \t\n\r\f\v"   # [[:space:]]


def lexical(path, cwd=None):
    """`realpath -ms -- PATH`: PATH made absolute against cwd (the current directory by default) and
    cleaned without following symlinks. An empty PATH stays empty, as the bash helper echoed it back."""
    if not path:
        return path
    if not path.startswith("/"):
        path = os.path.join(cwd if cwd is not None else os.getcwd(), path)
    out = os.path.normpath(path)
    if out.startswith("//"):
        out = "/" + out.lstrip("/")
    return out


def realpath_e(path):
    """`realpath -e -- PATH`, or None where it would fail (a missing component, a dangling link)."""
    if not path:
        return None
    try:
        real = os.path.realpath(path)
    except OSError:
        return None
    return real if os.path.exists(real) else None


def under(path, root):
    """Whether PATH is ROOT or below it, as bash's string test `[[ "$path/" == "$root"/* ]]` says."""
    return (path + "/").startswith(root + "/")


def bash_basename(path):
    stripped = path.rstrip("/")
    if not stripped:
        return "/" if path.startswith("/") else ""
    return stripped.rsplit("/", 1)[-1]


def ere(pattern, text):
    """A bash `[[ text =~ pattern ]]` test for an anchored pattern: the whole text must match."""
    return re.fullmatch(pattern, text, re.DOTALL) is not None


def _skip(s, i, chars):
    while i < len(s) and s[i] in chars:
        i += 1
    return i


def _delimiter(s, i, ifs):
    """Past one field delimiter at i: IFS whitespace, at most one other IFS character, IFS whitespace."""
    ws = "".join(c for c in ifs if c in IFS_WHITESPACE)
    i = _skip(s, i, ws)
    if i < len(s) and s[i] in ifs and s[i] not in IFS_WHITESPACE:
        i = _skip(s, i + 1, ws)
    return i


def _word(s, i, ifs):
    j = i
    while j < len(s) and s[j] not in ifs:
        j += 1
    return s[i:j], j


def read_fields(line, ifs, count):
    """`IFS=IFS read -r v1 … vCOUNT <<< LINE`: the COUNT values bash's read assigns."""
    s = line.split("\n", 1)[0]
    ws = "".join(c for c in ifs if c in IFS_WHITESPACE)
    i = _skip(s, 0, ws)
    values = []
    for _ in range(count - 1):
        w, i = _word(s, i, ifs)
        values.append(w)
        i = _delimiter(s, i, ifs)
    rest = s[i:]
    w, j = _word(rest, 0, ifs)
    if _delimiter(rest, j, ifs) == len(rest):
        values.append(w)          # one field left, with at most its delimiter after it
    else:
        values.append(rest.rstrip(ws))
    return values


def read_array(line, ifs):
    """`IFS=IFS read -r -a arr <<< LINE`: every field (a tab IFS never gives an empty one)."""
    s = line.split("\n", 1)[0]
    ws = "".join(c for c in ifs if c in IFS_WHITESPACE)
    i = _skip(s, 0, ws)
    out = []
    while i < len(s):
        w, i = _word(s, i, ifs)
        out.append(w)
        i = _delimiter(s, i, ifs)
    return out


# printf %q, as bash 5 writes it: these are backslashed anywhere, # and ~ only at the start, and a string
# with a control character or a byte that isn't printable text comes out whole as $'…'.
_Q_ANY = set("!\"$&'()*,;<>?[\\]^`{|} ")
_Q_START = set("#~")
_ANSI_NAMED = {7: "\\a", 8: "\\b", 9: "\\t", 10: "\\n", 11: "\\v", 12: "\\f", 13: "\\r", 27: "\\E",
               92: "\\\\", 39: "\\'"}


def _needs_ansi(s):
    for ch in s:
        o = ord(ch)
        if 0xDC80 <= o <= 0xDCFF:          # an undecodable byte (surrogateescape)
            return True
        if o < 0x20 or o == 0x7F or 0x80 <= o <= 0x9F:
            return True
    return False


def printf_q(s):
    if s == "":
        return "''"
    if _needs_ansi(s):
        out = []
        for ch in s:
            o = ord(ch)
            if o in _ANSI_NAMED:
                out.append(_ANSI_NAMED[o])
            elif 0x20 <= o < 0x7F:
                out.append(ch)
            elif o > 0x9F and not (0xDC80 <= o <= 0xDCFF):
                out.append(ch)
            else:
                for b in ch.encode("utf-8", "surrogateescape"):
                    out.append("\\%03o" % b)
        return "$'" + "".join(out) + "'"
    out = []
    for i, ch in enumerate(s):
        if ch in _Q_ANY or (i == 0 and ch in _Q_START):
            out.append("\\" + ch)
        else:
            out.append(ch)
    return "".join(out)


def logical_pwd():
    """The current directory as bash's $PWD has it: the shim (bash) has already checked the PWD it got,
    and passes its own on. Without one, the physical directory."""
    pwd = os.environ.get("PWD", "")
    if pwd.startswith("/"):
        return pwd
    return os.getcwd()
