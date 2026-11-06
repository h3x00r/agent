"""A grok persona's wrapper file (--wrapper NAME:PATH or NAME:none, and the menu's question): its absolute path in
personas.json, its copy personas/NAME.wrapper.md (mode 644, rewritten in place on every run that writes), mounted
read-only as /etc/grok/wrapper.md with NEXUS_WRAPPER naming it; the file held to the grok shim's rules; what is
refused (nothing written), and what only warns (the save goes on).

Every source file is written with mode 644, so tree.txt doesn't depend on the umask."""
import os
import shutil

from scen import case, set_field
from scen.config_cli import YES, grep
from scen.config_interactive import CONTEXT, MENU, PICK, sl, tty, wrap

TEXT = "# Wrapper\n\nDo the whole task; stop only when it is done.\n"
COPY = "a/personas/g1.wrapper.md"
SERVICE = "wrapper|^  \""          # each service's name, and its wrapper lines (the mount, its comment, the env)


def source(sb, rel="w/a.md", text=TEXT):
    return sb.write(rel, text, 0o644)


def tool(sb, name):
    return os.path.join(sb.bin, name)


def wrapped(sb, *names, rel="w/a.md"):
    """c1 (claude) and g1 (grok), and any more NAMES, with g1's wrapper file set to REL (a setup run)."""
    w = source(sb, rel)
    sb.personas("c1:claude:docker", "g1:grok:docker", *names, extra=("--wrapper", "g1:" + w))
    return w


def change_g1(answer, default="-"):
    """The menu's change of g1 (a grok persona with your agent), every default but the wrapper file's ANSWER."""
    return [(MENU, "change"), (PICK, "g1"), ("choice [grok]: ", ""), (CONTEXT, ""), (sl("g1"), ""),
            ("Projects root for g1", ""), ("choice [agent]: ", ""), (wrap("g1") + " [%s]: " % default, answer)]


@case("config/wrapper-set")
def _(sb):
    # An existing persona gets one: the copy (mode 644, the file's bytes), the mount and the env in its service, the
    # path in personas.json, and the restart note, as its service changed.
    sb.personas("c1:claude:docker", "g1:grok:docker")
    w = source(sb)
    sb.run("config", *YES, "--wrapper", "g1:" + w)
    grep(sb, SERVICE, "compose.personas.yml")
    grep(sb, "wrapper", "personas.json")
    sb.run(tool(sb, "stat"), "-c", "%a %n", "personas/g1.wrapper.md", cwd="a")
    sb.run(tool(sb, "cmp"), w, "personas/g1.wrapper.md", cwd="a")
    sb.run("config", *YES, "--wrapper", "g1:" + w, label="config --yes … --wrapper g1:<S>/w/a.md  (again: as it was)")


@case("config/wrapper-change")
def _(sb):
    # Another file: no restart (the service is the same), the copy rewritten in place, and a note that says when it
    # is read. A relative path is from where nexus --config was run; ~/ is your HOME.
    wrapped(sb)
    source(sb, "w/b.md", "# Another wrapper\n")
    source(sb, "home/w/c.md", "# A third wrapper\n")
    sb.run("config", *YES, "--wrapper", "g1:b.md", cwd="w")
    grep(sb, "wrapper", "personas.json")
    sb.run("config", *YES, "--wrapper", "g1:~/w/c.md")
    grep(sb, "wrapper", "personas.json")
    sb.run(tool(sb, "cmp"), sb.path("home/w/c.md"), "personas/g1.wrapper.md", cwd="a")


@case("config/wrapper-edit")
def _(sb):
    # The file edited, then nexus --config again: the copy rewritten in place (inode=same), its note; again with the
    # file as it is: nothing written, no note.
    wrapped(sb)
    source(sb, text=TEXT + "\nAnd one more rule.\n")
    sb.run("config", *YES, label="config --yes …  (the wrapper file edited)")
    sb.run("config", *YES, label="config --yes …  (as it is)")


@case("config/wrapper-copy-gone")
def _(sb):
    # The copy alone gone (by hand, or git clean -X): written again from the file, a new file, which a running
    # container's single-file mount doesn't see, so the restart note; then an edit of the file: in place, its note.
    wrapped(sb)
    os.unlink(sb.path(COPY))
    sb.run("config", *YES, label="config --yes …  (g1's copy gone)")
    sb.run(tool(sb, "cmp"), sb.path("w/a.md"), "personas/g1.wrapper.md", cwd="a")
    source(sb, text=TEXT + "\nAnd one more rule.\n")
    sb.run("config", *YES, label="config --yes …  (the wrapper file edited)")


@case("config/wrapper-gone")
def _(sb):
    # The file gone, then there but no longer one the shim takes: a warning each time, and the copy kept as it was.
    w = wrapped(sb)
    os.unlink(w)
    sb.run("config", *YES, label="config --yes …  (g1's wrapper file gone)")
    source(sb, text="\n\n")
    sb.run("config", *YES, label="config --yes …  (g1's wrapper file empty)")
    grep(sb, "wrapper", "personas.json")


@case("config/wrapper-both-gone")
def _(sb):
    # The file and its copy both gone: a warning that names both fixes, and the rest saved all the same (here c1's
    # change); the setting and the mount stay. Its first fix, the file put back and given again: the copy written
    # again, a new file that a running container's single-file mount doesn't see, so the restart note. Then none: the
    # setting and the mount go.
    w = wrapped(sb)
    os.unlink(w)
    os.unlink(sb.path(COPY))
    sb.run("config", *YES, "--permissions", "c1:ask",
           label="config --yes … --permissions c1:ask  (g1's file and copy gone)")
    grep(sb, "wrapper|permissions", "personas.json")
    grep(sb, SERVICE, "compose.personas.yml")
    source(sb)
    sb.run("config", *YES, "--wrapper", "g1:" + w,
           label="config --yes … --wrapper g1:<S>/w/a.md  (the file put back, given again)")
    sb.run(tool(sb, "cmp"), w, "personas/g1.wrapper.md", cwd="a")
    sb.run("config", *YES, "--wrapper", "g1:none")
    grep(sb, SERVICE, "compose.personas.yml")


@case("config/wrapper-menu-gone")
def _(sb):
    # The file gone, and the menu changes something else of g1 (its statusline), taking the wrapper file's default:
    # kept unread, so the change is saved, with the warning and the copy kept as it was (not asked again and again).
    w = wrapped(sb)
    os.unlink(w)
    tty(sb, [(MENU, "change"), (PICK, "g1"), ("choice [grok]: ", ""), (CONTEXT, ""), (sl("g1"), "n"),
             ("Projects root for g1", ""), ("choice [agent]: ", ""), (wrap("g1") + " [%s]: " % w, ""), (MENU, "")],
        label="config --no-link --no-build  (g1's wrapper file gone; its statusline off, the wrapper as it was)")
    grep(sb, "statusline|wrapper", "personas.json")
    sb.run(tool(sb, "cmp"), source(sb, "w/as-set.md"), "personas/g1.wrapper.md", cwd="a")


@case("config/wrapper-none")
def _(sb):
    # NAME:none: the setting and the copy gone, the compose file back to what it was before, and the restart note.
    # NAME:none on a claude persona: nothing to do, exit 0.
    sb.personas("c1:claude:docker", "g1:grok:docker")
    shutil.copy(sb.path("a/compose.personas.yml"), sb.path("compose.before"))
    sb.setup("config", *YES, "--wrapper", "g1:" + source(sb))
    sb.run("config", *YES, "--wrapper", "g1:none")
    grep(sb, "wrapper", "personas.json")
    sb.run(tool(sb, "cmp"), sb.path("compose.before"), "compose.personas.yml", cwd="a")
    sb.run("config", *YES, "--wrapper", "c1:none")


@case("config/wrapper-leftover")
def _(sb):
    # A copy no persona has a wrapper for (placed by hand for a claude persona and for a grok one without a wrapper):
    # removed at the next save; g2's, which it has, kept. g1 turned claude: its wrapper dropped, its copy removed.
    # g2 removed: its copy stays, named among what stays.
    w = wrapped(sb, "g2:grok:docker")
    sb.setup("config", *YES, "--wrapper", "g2:" + w)
    sb.personas("g3:grok:docker")
    sb.write("a/personas/c1.wrapper.md", "by hand\n", 0o644)
    sb.write("a/personas/g3.wrapper.md", "by hand\n", 0o644)
    sb.run("config", *YES, label="config --yes …  (c1.wrapper.md and g3.wrapper.md by hand)")
    sb.run("config", *YES, "--persona", "g1:claude:docker")
    grep(sb, "wrapper", "personas.json")
    sb.run("config", *YES, "--remove", "g2")


@case("config/wrapper-refused")
def _(sb):
    # Each refused before anything is written (tree.txt is empty): a bad spec, a claude persona (one turned claude in
    # the same run too), a persona there is none of, a path ending in / (a file's too), a control character.
    sb.personas("c1:claude:docker", "g1:grok:docker")
    w = source(sb)
    for spec in ("g1", "g1:", ":" + w, "Bad!:" + w, "c1:" + w, "nobody:" + w, "g1:" + sb.path("w") + "/",
                 "g1:" + w + "/", "g1:" + sb.path("w/a\tb.md")):
        sb.run("config", *YES, "--wrapper", spec)
    sb.run("config", *YES, "--wrapper")
    sb.run("config", *YES, "--persona", "g1:claude:docker", "--wrapper", "g1:" + w)


@case("config/wrapper-files")
def _(sb):
    # Each check the grok shim makes, refused before anything is written (tree.txt is empty): not there, a directory,
    # empty, newlines only, a NUL, not UTF-8, unreadable, and over the size (122879: 2 short of the shim's 122880,
    # which counts 2 for a blank line even with no --rules of the user's).
    sb.personas("g1:grok:docker")
    files = [sb.path("w/missing.md"), sb.path("w")]
    for rel, data in (("empty", b""), ("newlines", b"\n\n\n"), ("nul", b"a\0b\n"), ("latin1", b"caf\xe9\n"),
                      ("big", b"a" * 122879), ("bigger", b"a" * 200000)):
        files.append(sb.write("w/%s.md" % rel, data, 0o644))
    locked = source(sb, "w/locked.md")        # 644 but for its own run: the snapshots read every file
    for f in files:
        sb.run("config", *YES, "--wrapper", "g1:" + f)
    os.chmod(locked, 0o000)
    sb.run("config", *YES, "--wrapper", "g1:" + locked,
           label="config --yes … --wrapper g1:<S>/w/locked.md  (mode 000)")
    os.chmod(locked, 0o644)


@case("config/wrapper-limit")
def _(sb):
    # 122878 bytes, the most the shim takes, is taken (a multibyte character at its end: counted in bytes).
    sb.personas("g1:grok:docker")
    w = sb.write("w/limit.md", b"a" * 122876 + "é".encode(), 0o644)
    sb.run("config", *YES, "--wrapper", "g1:" + w)
    sb.run(tool(sb, "cmp"), w, "personas/g1.wrapper.md", cwd="a")


@case("config/wrapper-two")
def _(sb):
    # Two personas in one run, each its own file; the claude one between them has none.
    sb.personas("g1:grok:docker", "c1:claude:docker", "g2:grok:docker")
    a, b = source(sb), source(sb, "w/b.md", "# g2's wrapper\n")
    sb.run("config", *YES, "--wrapper", "g1:" + a, "--wrapper", "g2:" + b)
    grep(sb, SERVICE, "compose.personas.yml")
    sb.run("config", "--list")


@case("config/wrapper-list")
def _(sb):
    sb.personas("c1:claude:docker", "g1:grok:docker")
    w = source(sb)
    sb.run("config", "--list", label="config --list  (no wrapper file)")
    sb.setup("config", *YES, "--wrapper", "g1:" + w)
    sb.run("config", "--list", label="config --list  (g1's)")


@case("config/wrapper-json-refused")
def _(sb):
    # personas.json edited by hand: a wrapper on a claude persona, a relative one, one with a control character, one
    # that isn't a string: refused, with the file named.
    sb.personas("c1:claude:docker", "g1:grok:docker")
    sb.run("config", "--list", label="config --list  (as nexus --config wrote it)")
    for name, value in (("c1", "/x/w.md"), ("g1", "w.md"), ("g1", "/x/a\nb.md"), ("g1", True), ("g1", "")):
        set_field(sb, name, "wrapper", value)
        sb.run("config", "--list", label="config --list  (%s's wrapper: %r)" % (name, value))
        set_field(sb, name, "wrapper", None)
    sb.run("config", "--list", label="config --list  (as it was)")


@case("config/wrapper-menu")
def _(sb):
    # The menu's question, for a grok persona only: two refused (gone, empty) then one taken; another, relative to
    # where nexus --config runs (no restart, the "changed" note); the default kept (as it was); - for none (a
    # restart). An added grok persona is asked too.
    sb.personas("c1:claude:docker", "g1:grok:docker")
    a, b = source(sb), source(sb, "w/b.md", "# Another wrapper\n")
    source(sb, "w/empty.md", "\n")
    tty(sb, change_g1(sb.path("w/missing.md")) + [(wrap("g1"), sb.path("w/empty.md")), (wrap("g1"), a),
                                                   (MENU, "list"), (MENU, "")],
        label="config --no-link --no-build  (g1: two refused, then <S>/w/a.md)")
    tty(sb, change_g1("../w/b.md", a) + [(MENU, "")], label="config --no-link --no-build  (g1: ../w/b.md)")
    tty(sb, change_g1("", b) + [(MENU, "")], label="config --no-link --no-build  (g1: as it was)")
    tty(sb, change_g1("-", b) + [(MENU, "")], label="config --no-link --no-build  (g1: none)")
    tty(sb, [(MENU, "add"), ("choice [claude]: ", "grok"), (CONTEXT, ""), ("Name [grok-docker]: ", "g2"),
             (sl("g2"), ""), ("Projects root for g2", ""), ("choice [agent]: ", ""), (wrap("g2"), a),
             ("XAI_API_KEY (hidden", ""),
             ("git author name for ", ""), ("git author email for ", ""), ("GH_TOKEN for ", ""), (MENU, "")],
        label="config --no-link --no-build  (g2 added, with <S>/w/a.md)")
    grep(sb, "wrapper", "personas.json")
