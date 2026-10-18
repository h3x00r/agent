"""Tables the way `nexus ls` and `nexus top` print them.

render(align, rows, palette): rows (the first is the header, printed dim) as columns two spaces apart.
align has an "l" or "r" per column; a missing letter is "l", and a last column on the left isn't padded.
Widths are measured on the plain text (colours take no room).

An empty cell disappears and the cells after it move left, as they did when bash split a tab-joined row
(a tab is IFS whitespace): give "-" for nothing.

Pitfalls (each pinned where named):
- an empty cell (test_ui.Table.test_an_empty_cell_disappears)
- a tab inside a cell splits it, as `read -a` did (golden nexus/edge-top-persona-tab)
- widths in characters, bytes outside UTF-8 (golden nexus/basic-ls-tty)
- colours (test_ui.Table.test_header_dim_and_colours_unmeasured)
"""
from agentctl.ui import text


def render(align, rows, palette, utf8=None):
    if utf8 is None:
        utf8 = text.utf8_locale()
    # A row's cells as bash's `IFS=$'\t' read -r -a` split them: on every tab (one inside a cell too), and
    # never an empty cell.
    rows = [[c for c in "\t".join(r).split("\t") if c != ""] for r in rows]
    widths = []
    for r in rows:
        for i, c in enumerate(r):
            w = text.width(c, utf8)
            if i >= len(widths):
                widths.append(w)
            elif w > widths[i]:
                widths[i] = w
    lines = []
    for n, r in enumerate(rows):
        line = ""
        last = len(r) - 1
        for i, c in enumerate(r):
            pad = " " * (widths[i] - text.width(c, utf8))
            if i < len(align) and align[i] == "r":
                line += pad + c
            elif i == last:
                line += c
            else:
                line += c + pad
            if i != last:
                line += "  "
        lines.append(line if n else palette.dim + line + palette.reset)
    return lines
