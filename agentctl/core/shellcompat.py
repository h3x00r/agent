"""What the bash versions of the tools did with paths, fields and quoting, done the same way here.

The tools were bash scripts; their behaviour is the contract (tests/run.sh, tests/golden). Where Python's
own helpers differ from what bash or coreutils did, the difference lives here, once:
- lexical(): `$(realpath -ms …)`: absolute, `.`/`..`/repeated slashes resolved, a leading `//` too
  (os.path.normpath keeps that), no symlink followed, trailing newlines stripped as $(…) strips them;
- logical(): bash's $PWD after a logical `cd`, which keeps a leading `//`;
- utf8_locale(): whether the locale is UTF-8 as bash judged it (the shim tells), which decides what a
  character is for lengths, cuts, ere() and printf_q();
- realpath_e(): `realpath -e`: every component must exist (os.path.realpath doesn't check);
- under(): bash's `[[ "$p/" == "$root"/* ]]`, a string test, never os.path.commonpath;
- bash_basename(): `basename`, which ignores trailing slashes (os.path.basename("x/") is "");
- read_fields(): `IFS=… read -r a b c`, field splitting as bash's read does it: IFS whitespace (a tab)
  collapses and is trimmed, any other IFS character delimits one field, and the last variable takes the
  rest of the line;
- printf_q(): bash's `printf %q`, which shlex.quote doesn't match;
- sort_lines(): sort(1)'s order, which follows the locale's collation;
- mkdir_p(): `mkdir -p`, whose parents keep u+wx whatever the umask (os.makedirs' don't);
- ere(): bash's `[[ $s =~ ^…$ ]]`: POSIX ERE, so `$` is the end of the string, never before a newline.
  Patterns spell their classes in ASCII ([0-9], [ \\t…]) and never use \\d or \\s, which match more.

Pitfalls (each pinned where named):
- lexical() (test_shellcompat.Lexical.test_cleans_like_realpath_ms,
  test_shellcompat.Dirs.test_lexical_drops_trailing_newlines_as_command_substitution)
- logical() (test_shellcompat.Dirs.test_logical_keeps_a_leading_double_slash, golden
  nexus/edge-ls-agents-dir-dslash)
- utf8_locale(): the shim's measure, not the locale's name (golden nexus/edge-top-locale-missing)
- realpath_e() (test_shellcompat.Paths.test_realpath_e_needs_every_component)
- under() (test_shellcompat.Paths.test_under_is_a_string_test)
- bash_basename() (test_shellcompat.Paths.test_basename_ignores_trailing_slashes)
- read_fields(): a tab collapses, \\037 doesn't
  (test_shellcompat.Read.test_tab_ifs_collapses_and_the_last_takes_the_rest,
  test_shellcompat.Read.test_other_ifs_keeps_empty_fields)
- the last variable loses one trailing delimiter, not more: `x:y::` keeps `y::`
  (test_shellcompat.Read.test_one_trailing_delimiter_after_the_last_field_goes)
- read_array() (test_shellcompat.Read.test_array)
- printf_q(), by the locale (test_shellcompat.PrintfQ.test_as_bash_writes_it, golden
  nexus/edge-persona-printf-q)
- sort_lines() (test_shellcompat.Sort.test_c_order_is_by_bytes,
  test_shellcompat.Sort.test_a_locale_that_isnt_there_is_c)
- mkdir_p() (test_shellcompat.Dirs.test_mkdir_p_parents_keep_u_wx_under_a_tight_umask, golden
  nexus/edge-persona-umask)
- ere(): a fullmatch, with DOTALL (test_shellcompat.Ere.test_dollar_is_the_end,
  test_shellcompat.Ere.test_dot_matches_a_newline)
- ere() in UTF-8: a byte that isn't UTF-8 matches nothing
  (test_shellcompat.Ere.test_a_byte_that_isnt_utf8_matches_nothing_in_utf8)
- ASCII classes (test_boot.AsciiClasses.test_no_unicode_classes_in_patterns)
- logical_pwd(): the shim's $PWD (golden nexus/edge-persona-cwd-spellings)
"""
import os
import re
import stat
import unicodedata

IFS_WHITESPACE = " \t\n"
SPACE = " \t\n\r\f\v"   # [[:space:]]

# The locale as bash saw it: whether a UTF-8 character counted as one character. The shims measure it in
# bash itself and _boot sets it: Python's own view can differ (it takes a C locale for C.UTF-8, and a locale
# that isn't installed for its name). None: judged from LC_ALL, LC_CTYPE and LANG (run without a shim).
UTF8 = None


def utf8_locale(env=None):
    if env is None and UTF8 is not None:
        return UTF8
    env = os.environ if env is None else env
    for k in ("LC_ALL", "LC_CTYPE", "LANG"):
        v = env.get(k)
        if v:
            return "utf8" in v.lower().replace("-", "")
    return False


def _undecodable(s):
    return any(0xDC80 <= ord(c) <= 0xDCFF for c in s)


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
    return out.rstrip("\n") or out      # as $(lexical …) had it: trailing newlines stripped


def logical(path, cwd):
    """bash's $PWD after a logical `cd PATH` from CWD: . and .. resolved by name (a symlink's path kept),
    repeated slashes squeezed, but a leading `//` kept as it is, as POSIX lets it be."""
    return os.path.normpath(path if path.startswith("/") else os.path.join(cwd, path))


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
    """A bash `[[ text =~ pattern ]]` test for an anchored pattern: the whole text must match. In a UTF-8
    locale a byte that isn't UTF-8 matches nothing, not even `.`, so a text holding one never matches."""
    if utf8_locale() and _undecodable(text):
        return False
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
# with a character that isn't printable in the locale comes out whole as $'…', each such character as its
# bytes in octal. In UTF-8 that is a control character (C0, DEL, C1), a byte that isn't UTF-8, and what
# glibc's iswprint refuses besides (unassigned and private-use code points, U+2028, U+2029); in any other
# locale, every byte outside ASCII.
_Q_ANY = set("!\"$&'()*,;<>?[\\]^`{|} ")
_Q_START = set("#~")
_ANSI_NAMED = {7: "\\a", 8: "\\b", 9: "\\t", 10: "\\n", 11: "\\v", 12: "\\f", 13: "\\r", 27: "\\E",
               92: "\\\\", 39: "\\'"}


def _printable(ch, utf8):
    o = ord(ch)
    if o < 0x80:
        return 0x20 <= o < 0x7F
    if not utf8 or o <= 0x9F or 0xDC80 <= o <= 0xDCFF:
        return False
    return unicodedata.category(ch) not in ("Cn", "Co", "Cs", "Zl", "Zp")


def printf_q(s, utf8=None):
    if s == "":
        return "''"
    utf8 = utf8_locale() if utf8 is None else utf8
    if not all(_printable(ch, utf8) for ch in s):
        out = []
        for ch in s:
            o = ord(ch)
            if o in _ANSI_NAMED:
                out.append(_ANSI_NAMED[o])
            elif _printable(ch, utf8):
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


def mkdir_p(path):
    """`mkdir -p PATH`. Raises OSError as mkdir fails. Like mkdir -p, a parent it makes gets u+wx on top
    of what the umask leaves, so a tight umask (0177) still lets it make the next one; PATH itself gets
    the umask's mode."""
    path = path.rstrip("/") or path
    if os.path.isdir(path):
        return
    parent = os.path.dirname(path)
    if parent and parent != path and not os.path.isdir(parent):
        mkdir_p(parent)
        mask = os.umask(0)
        os.umask(mask)
        want = (0o777 & ~mask) | 0o300
        if stat.S_IMODE(os.stat(parent).st_mode) != want:
            os.chmod(parent, want)
    try:
        os.mkdir(path)
    except FileExistsError:
        if not os.path.isdir(path):
            raise


def sort_lines(items):
    """`printf '%s\n' ITEMS | sort`: in the order of the locale's collation (LC_ALL, LC_COLLATE or LANG,
    as sort reads them; C if that locale isn't installed), ties broken byte by byte, as sort breaks them."""
    import locale

    def raw(x):
        return x.encode("utf-8", "surrogateescape")
    try:
        locale.setlocale(locale.LC_COLLATE, "")
        try:
            return sorted(items, key=lambda x: (locale.strxfrm(x), raw(x)))
        finally:
            locale.setlocale(locale.LC_COLLATE, "C")
    except (locale.Error, ValueError, OSError):
        return sorted(items, key=raw)


def logical_pwd():
    """The current directory as bash's $PWD has it: the shim (bash) has already checked the PWD it got,
    and passes its own on. Without one, the physical directory."""
    pwd = os.environ.get("PWD", "")
    if pwd.startswith("/"):
        return pwd
    return os.getcwd()
