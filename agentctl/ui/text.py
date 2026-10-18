"""Text as a terminal will show it.

clean(): what a persona sent (a session name, a directory) made safe to print as one table cell: the C0
control bytes and DEL, then the UTF-8 encodings of C1 (U+0080-U+009F, which a terminal can take for
ESC [ and the like), removed byte by byte, as the bash t_clean did under LC_ALL=C; "-" if nothing is left.
plain(): a cell without the Palette's colour codes, for measuring.
width(): how many columns a cell takes as bash counted them: characters in a UTF-8 locale (an undecodable
byte counts as one), bytes in any other.

Pitfalls (each pinned where named):
- clean() on bytes: C1 goes as its UTF-8 bytes, a lone 0x9d byte stays (test_ui.Clean.test_c1_goes_byte_wise,
  test_ui.Clean.test_a_lone_byte_stays)
- "-" (test_ui.Clean.test_empty_is_a_dash)
- width() (test_ui.Width.test_characters_or_bytes, golden nexus/edge-ls-wide-tty)
"""
import re

from agentctl.core import shellcompat

_CONTROL = re.compile(rb"[\x00-\x1f\x7f]")
_C1 = re.compile(rb"\xc2[\x80-\x9f]")
_COLOUR2 = re.compile("\033\\[[0-9][0-9]m")
_COLOUR1 = re.compile("\033\\[[0-9]m")


def clean(text):
    b = text.encode("utf-8", "surrogateescape")
    b = _C1.sub(b"", _CONTROL.sub(b"", b))
    return b.decode("utf-8", "surrogateescape") or "-"


def plain(cell):
    return _COLOUR1.sub("", _COLOUR2.sub("", cell))


def utf8_locale(env=None):
    return shellcompat.utf8_locale(env)


def width(cell, utf8=None):
    p = plain(cell)
    if utf8 is None:
        utf8 = utf8_locale()
    return len(p) if utf8 else len(p.encode("utf-8", "surrogateescape"))
