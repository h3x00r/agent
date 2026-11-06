"""nexus and a grok persona's wrapper file: --wrapper FILE (or --wrapper=FILE) for a new session or a headless run
(NEXUS_WRAPPER, tmux new-session -e or docker exec -e, and no -A), where the persona sees FILE (its projects root, its
scratch dir, else a copy staged there), the probe (wrapper_probe.sh: the shim, the persona's default, a session's own),
the `-- grok|agent … --wrapper F` form (checked by the shim inside first), and what is refused.

Every source file is written with mode 644, so tree.txt doesn't depend on the umask."""
import hashlib
import os

from scen import case

TEXT = "# Wrapper\n\nDo the whole task; stop only when it is done.\n"
SKIP = "--skip-bypass-permissions"
DEFAULT = "/etc/grok/wrapper.md"


def session(name, wrapper=None):
    s = {"name": name, "windows": 1, "attached": 0, "path": "<P>"}
    if wrapper is not None:
        s["wrapper"] = wrapper
    return s


def source(sb, rel, text=TEXT):
    return sb.write(rel, text, 0o644)


def files(sb):
    """projects/rules/a.md and b.md (in the projects root), and the dirs projects/app and projects/new."""
    source(sb, "projects/rules/a.md")
    source(sb, "projects/rules/b.md", "# Another wrapper\n")
    sb.write("projects/app/.keep", "")
    sb.write("projects/new/.keep", "")
    return os.path.join(sb.projects, "rules/a.md")


def two(sb, extra=(), **g1):
    """c1 (claude) and g1 (grok), both running; g1 with the sessions main, app (no wrapper of its own), wa (under
    rules/a.md), gone (its NEXUS_WRAPPER removed: -) and unread (?)."""
    sb.personas("c1:claude:docker", "g1:grok:docker", extra=extra)
    a = files(sb)
    sb.container("c1")
    sb.container("g1", sessions=[session("main"), session("app"), session("wa", a), session("gone", "-"),
                                 session("unread", "?")], **g1)
    return a


def ctr(sb, name, **fields):
    """Fields of NAME's container set in the world (None: taken out)."""
    sb.load_world()
    c = sb.world["containers"]["agent-" + name]
    for k, v in fields.items():
        if v is None:
            c.pop(k, None)
        else:
            c[k] = v
    sb.save_world()


@case("nexus/wrapper-flag-sessions")
def _(sb):
    two(sb)
    # A new session: NEXUS_WRAPPER (the file as the persona sees it), and no -A; wherever --wrapper is before the --.
    sb.run("nexus", "g1", "--wrapper", "rules/a.md", "s1")
    sb.run("nexus", "g1", "--wrapper=../rules/a.md", cwd="projects/new")
    sb.run("nexus", "g1", "s2", "--wrapper", "rules/a.md", "--", "grok", "--single", "hi")
    sb.run("nexus", "g1", "s3", "--wrapper", os.path.join(sb.projects, "rules/b.md"))
    # A session there already: under that very file, attached (-A, no -e); else refused, never attached.
    sb.run("nexus", "g1", "--wrapper", "rules/a.md", "wa")
    sb.run("nexus", "g1", "--wrapper", "rules/b.md", "wa")
    sb.run("nexus", "g1", "--wrapper", "rules/a.md", "app", label="nexus g1 --wrapper rules/a.md app  (none of its own, "
           "and g1 has no default)")
    sb.run("nexus", "g1", "--wrapper", "rules/a.md", "gone", label="nexus g1 --wrapper rules/a.md gone  (its "
           "NEXUS_WRAPPER removed)")
    sb.run("nexus", "g1", "--wrapper", "rules/a.md", "unread", label="nexus g1 --wrapper rules/a.md unread  (its "
           "wrapper can't be read)")
    sb.run("nexus", "g1", "--wrapper", "../rules/a.md", cwd="projects/app",
           label="nexus g1 --wrapper ../rules/a.md  (in projects/app: the session app)")
    # The hint's FILE shell-quoted, to paste as it is.
    source(sb, "projects/sp ace/w x.md")
    sb.run("nexus", "g1", "--wrapper", "sp ace/w x.md", "wa")
    # Without the word: as ever, whatever the session's wrapper.
    sb.run("nexus", "g1", "wa")
    sb.run("nexus", "g1", "gone")
    # The file held to the shim's rules here, before any probe: none is started.
    source(sb, "projects/rules/nul.md", "# Wrapper\0\n")
    source(sb, "projects/rules/blank.md", "\n\n")
    for f in ("rules/none.md", "rules", "rules/nul.md", "rules/blank.md"):
        sb.run("nexus", "g1", "--wrapper", f, "s4")


@case("nexus/wrapper-main")
def _(sb):
    # main, which every persona has, runs under the persona's own wrapper file or none: never another.
    two(sb)
    sb.run("nexus", "g1", "--wrapper", "rules/a.md", "main")
    sb.run("nexus", "g1", "--wrapper=rules/b.md", "main")
    sb.run("nexus", "g1", "main", label="nexus g1 main  (no word: as ever)")


@case("nexus/wrapper-default-session")
def _(sb):
    # g1 with a default wrapper (nexus --config --wrapper g1:…), started by nexus: its container has NEXUS_WRAPPER.
    source(sb, "w/default.md")
    sb.personas("c1:claude:docker", "g1:grok:docker", extra=("--wrapper", "g1:" + sb.path("w/default.md")))
    a = files(sb)
    sb.setup("nexus", "g1", "start")
    sb.load_world()
    c = sb.world["containers"]["agent-g1"]
    c["sessions"] += [session("app"), session("own", a), session("gone", "-")]
    sb.save_world()
    # Under the default (no NEXUS_WRAPPER of its own): refused, naming it.
    sb.run("nexus", "g1", "--wrapper", "rules/a.md", "app")
    # Its own file is F: attached.
    sb.run("nexus", "g1", "--wrapper", "rules/a.md", "own")
    # Its NEXUS_WRAPPER removed: without one, the default notwithstanding.
    sb.run("nexus", "g1", "--wrapper", "rules/a.md", "gone")
    # A new one, in a container this run didn't make: as without a default.
    sb.run("nexus", "g1", "--wrapper", "rules/a.md", "s1")


@case("nexus/wrapper-headless")
def _(sb):
    two(sb)
    # -e NEXUS_WRAPPER on its docker exec (calls.jsonl), --wrapper taken from anywhere in its arguments, after a --
    # too; grok's command line as without it.
    sb.run("nexus", "g1", "headless", "nxt hi", "--wrapper", "../rules/a.md", cwd="projects/app")
    sb.run("nexus", "g1", "--wrapper=../rules/a.md", "headless", "--", "nxt hi", "--model", "m",
           cwd="projects/app")
    sb.run("nexus", "g1", "headless", "--", "nxt hi", "--wrapper=../rules/b.md", cwd="projects/app",
           label="nexus g1 headless -- 'nxt hi' --wrapper=../rules/b.md  (after the --: nexus's too)")
    sb.run("nexus", "g1", "headless", "nxt hi", "--", "--wrapper=../rules/b.md", cwd="projects/app",
           label="nexus g1 headless 'nxt hi' -- --wrapper=../rules/b.md  (a -- only the word came after goes with it)")
    sb.run("nexus", "g1", "headless", "nxt hi", "--wrapper=../rules/b.md", "--", cwd="projects/app",
           label="nexus g1 headless 'nxt hi' --wrapper=../rules/b.md --  (one of the caller's own stays)")
    # --no-wrapper: no word of nexus's, so grok gets it, as given.
    sb.run("nexus", "g1", "headless", "nxt hi", "--no-wrapper", cwd="projects/app")
    sb.run("nexus", "g1", "headless", "nxt hi", cwd="projects/app", label="nexus g1 headless 'nxt hi'  (no word)")
    # Given twice: refused, before anything runs.
    sb.run("nexus", "g1", "headless", "nxt hi", "--wrapper", "../rules/a.md", "--wrapper=../rules/b.md",
           cwd="projects/app")


@case("nexus/wrapper-refused")
def _(sb):
    two(sb)
    # The word that can't be read as one: refused as it is taken, before anything else.
    sb.run("nexus", "g1", "--wrapper")
    sb.run("nexus", "g1", "s1", "--wrapper", "--", "grok")
    sb.run("nexus", "g1", "--wrapper=", "s1")
    sb.run("nexus", "g1", "--wrapper", "-x", "s1")
    sb.run("nexus", "g1", "--wrapper", "rules/a\nb.md", "s1", label="nexus g1 --wrapper 'rules/a<newline>b.md' s1")
    sb.run("nexus", "g1", "--wrapper=rules/\x1b[1m.md", "s1", label="nexus g1 --wrapper='rules/<ESC>[1m.md' s1")
    sb.run("nexus", "g1", "--wrapper", "rules/a.md", "--wrapper=rules/b.md", "s1")
    sb.run("nexus", "g1", "--wrapper", "rules/a.md", "--wrapper", "rules/a.md", "s1",
           label="nexus g1 --wrapper rules/a.md twice s1  (the same file: refused too)")
    # Next to the -- form: two wrappers for one session.
    sb.run("nexus", "g1", "--wrapper", "rules/a.md", "s1", "--", "grok", "--wrapper", "rules/b.md")
    sb.run("nexus", "g1", "--wrapper", "rules/a.md", "s1", "--", "/usr/local/bin/agent", "--wrapper=rules/b.md")
    # Only for a session or a headless run.
    for sub in ("ls", "stop", "logs", "build", "pull", "start", "restart", "upgrade"):
        sb.run("nexus", "g1", "--wrapper", "rules/a.md", sub)
    sb.run("nexus", "g1", "start", "--wrapper=rules/a.md")
    # Only for a grok persona.
    sb.run("nexus", "c1", "--wrapper", "rules/a.md", "s1")
    sb.run("nexus", "c1", "headless", "nxt hi", "--wrapper", "../rules/a.md", cwd="projects/app")
    # The order: the parse-time refusals first, then the mode words', then --wrapper's other ones.
    sb.run("nexus", "c1", "--wrapper", label="nexus c1 --wrapper  (no FILE: refused before the flavour)")
    sb.run("nexus", "c1", "--wrapper", "ls", label="nexus c1 --wrapper ls  (ls is its FILE: no subcommand)")
    sb.run("nexus", "g1", SKIP, "--wrapper", "rules/a.md", "s1")
    sb.run("nexus", "g1", SKIP, "--wrapper", "rules/a.md", "s1", "--", "grok", "--wrapper", "rules/b.md",
           label="nexus g1 --skip-bypass-permissions --wrapper rules/a.md s1 -- grok --wrapper rules/b.md  (given twice, "
           "before the mode word's refusal)")
    sb.run("nexus", "c1", SKIP, "--wrapper", "rules/a.md", "ls")
    sb.run("nexus", "c1", "--wrapper", "rules/a.md", "s1", "--", "grok", "--wrapper", "rules/b.md",
           label="nexus c1 --wrapper rules/a.md s1 -- grok --wrapper rules/b.md  (given twice, before claude's)")
    # Not nexus's: --no-wrapper before a -- is a session name, as any other -x; after the --, the command's.
    sb.run("nexus", "g1", "--no-wrapper")
    sb.run("nexus", "g1", "s5", "--", "echo", "--wrapper", "rules/a.md")
    sb.run("nexus", "g1", "s6", "--", "grok", "--no-wrapper")
    # The order, again: for anything but an attach or a headless run before for a claude persona; and --wrapper's own
    # parse refusals before the mode words' (a grok persona's --skip-bypass-permissions).
    sb.run("nexus", "c1", "--wrapper", "rules/a.md", "ls", label="nexus c1 --wrapper rules/a.md ls  (not for ls, before "
           "the flavour)")
    sb.run("nexus", "g1", SKIP, "--wrapper", label="nexus g1 --skip-bypass-permissions --wrapper  (no FILE, before the "
           "mode word's refusal)")


@case("nexus/wrapper-old-image")
def _(sb):
    # An image from before grok-mode.sh takes no wrapper: refused, not run without one.
    two(sb, no_shim=True)
    sb.run("nexus", "g1", "--wrapper", "rules/a.md", "s1")
    sb.run("nexus", "g1", "headless", "nxt hi", "--wrapper", "../rules/a.md", cwd="projects/app")
    sb.run("nexus", "g1", "s1", "--", "grok", "--wrapper", "rules/a.md")
    sb.run("nexus", "g1", "--wrapper", "rules/a.md", "s2", env={"REGISTRY": "registry.example.com"},
           label="nexus g1 --wrapper rules/a.md s2  (REGISTRY set: pull, not build)")
    sb.run("nexus", "g1", "s1", label="nexus g1 s1  (no word: as ever)")
    ctr(sb, "g1", no_shim=None, wrapper_probe_rc=1)
    sb.run("nexus", "g1", "--wrapper", "rules/a.md", "s1", label="nexus g1 --wrapper rules/a.md s1  (the probe fails)")


@case("nexus/wrapper-file-outside")
def _(sb):
    # A file the persona doesn't see: a copy in its scratch dir, <sha256:12>-<name> (mode 644, in wrappers/, 755), goes
    # in its place, said on stderr; the same file again: the same copy. One in its scratch dir: as it is.
    two(sb)
    out = source(sb, "w/out.md", "# From outside\n")
    sb.symlink("projects/rules/link.md", out)
    source(sb, "scratch/g1/mine.md", "# In the scratch dir\n")
    sb.run("nexus", "g1", "--wrapper", out, "s1")
    sb.run("nexus", "g1", "--wrapper", out, "s2", label="nexus g1 --wrapper <S>/w/out.md s2  (again: the same copy)")
    sb.run("nexus", "g1", "--wrapper", "rules/link.md", "s3",
           label="nexus g1 --wrapper rules/link.md s3  (a link in the projects root to that file: by its real path)")
    sb.run("nexus", "g1", "--wrapper", sb.path("scratch/g1/mine.md"), "s4")
    sb.run("nexus", "g1", "headless", "nxt hi", "--wrapper", out, cwd="projects/app")
    copy = [c for c in os.listdir(sb.path("scratch/g1/wrappers")) if c.endswith("-out.md")][0]
    sb.run(os.path.join(sb.bin, "cmp"), out, "scratch/g1/wrappers/" + copy, cwd=".",
           label="cmp <S>/w/out.md scratch/g1/wrappers/<copy>  (the file's bytes)")


@case("nexus/wrapper-staged")
def _(sb):
    # A container made without its scratch dir: a file outside its root can't be copied where it sees it, so refused
    # with the restart that adds it; one in its root is as ever. (The notes are mounted: their warning stays out.)
    two(sb, nexus_mounts=False, mounts=["/etc/claude-code/CLAUDE.md", "/etc/grok/rules/00-nexus.md"])
    out = source(sb, "w/out.md", "# From outside\n")
    source(sb, "scratch/g1/mine.md", "# In the scratch dir\n")
    sb.run("nexus", "g1", "--wrapper", out, "s1")
    sb.run("nexus", "g1", "headless", "nxt hi", "--wrapper", out, cwd="projects/app")
    sb.run("nexus", "g1", "--wrapper", sb.path("scratch/g1/mine.md"), "s2",
           label="nexus g1 --wrapper <S>/scratch/g1/mine.md s2  (in the scratch dir it doesn't mount)")
    sb.run("nexus", "g1", "--wrapper", "rules/a.md", "s3", label="nexus g1 --wrapper rules/a.md s3  (in its root)")


@case("nexus/wrapper-old-root")
def _(sb):
    # A container made with another projects root (old/): only that root counts; a file in the persona's root now
    # (which the container doesn't mount) is copied in.
    sb.personas("c1:claude:docker", "g1:grok:docker")
    files(sb)
    source(sb, "old/rules/o.md", "# In the old root\n")
    sb.write("old/app/.keep", "")
    sb.container("g1", root=sb.path("old"))
    sb.run("nexus", "g1", "--wrapper", "../rules/o.md", "s1", cwd="old/app")
    sb.run("nexus", "g1", "--wrapper", os.path.join(sb.projects, "rules/a.md"), "s2", cwd="old/app")
    sb.run("nexus", "g1", "headless", "nxt hi", "--wrapper", "../rules/o.md", cwd="old/app")
    out = source(sb, "w/out.md", "# From outside\n")
    sb.symlink("projects/rules/linkout.md", out)
    sb.symlink("old/rules/linkout.md", out)
    sb.run("nexus", "g1", "--wrapper", os.path.join(sb.projects, "rules/linkout.md"), "s3", cwd="old/app",
           label="nexus g1 --wrapper <P>/rules/linkout.md s3  (a link in its root now to a file outside both)")
    # Made without its scratch dir too: each refused, its own phrase first, the container's subject kept.
    ctr(sb, "g1", mounts=["/etc/claude-code/CLAUDE.md", "/etc/grok/rules/00-nexus.md"])
    sb.run("nexus", "g1", "--wrapper", out, "s4", cwd="old/app")
    sb.run("nexus", "g1", "--wrapper", "../rules/linkout.md", "s5", cwd="old/app",
           label="nexus g1 --wrapper ../rules/linkout.md s5  (a link in the old root, no scratch dir)")
    sb.run("nexus", "g1", "--wrapper", os.path.join(sb.projects, "rules/linkout.md"), "s6", cwd="old/app",
           label="nexus g1 --wrapper <P>/rules/linkout.md s6  (a link in its root now, no scratch dir)")
    sb.run("nexus", "g1", "--wrapper", os.path.join(sb.projects, "rules/a.md"), "s7", cwd="old/app",
           label="nexus g1 --wrapper <P>/rules/a.md s7  (in its root now, no scratch dir: said once)")


@case("nexus/wrapper-dashdash")
def _(sb):
    # `-- grok|agent … --wrapper F`: the shim's own word. The probe runs the command where the session starts (its
    # real grok swapped for /bin/true): a check that passes gives a new session (no -A, no -e); one that fails is
    # refused with the shim's own lines, and nothing is started.
    two(sb)
    sb.write("w/.keep", "")
    sb.run("nexus", "g1", "s1", "--", "grok", "--wrapper", "rules/a.md", "--single", "hi")
    sb.run("nexus", "g1", "--", "agent", "--wrapper=../rules/a.md", cwd="projects/new")
    sb.run("nexus", "g1", "s2", "--", "grok", "--wrapper", "rules/a.md", "--", "--wrapper", "x",
           label="nexus g1 s2 -- grok --wrapper rules/a.md -- --wrapper x  (passed on as given, its own -- too)")
    ctr(sb, "g1", wrapper_check_rc=2, wrapper_check="grok: --wrapper x.md: no such file here (a persona sees only its "
        "projects root and its scratch dir): nexus <persona> --wrapper FILE copies a file in")
    sb.run("nexus", "g1", "s3", "--", "grok", "--wrapper", "x.md")
    sb.run("nexus", "g1", "s3", "--", "grok", "--wrapper", "x.md", cwd="w",
           label="nexus g1 s3 -- grok --wrapper x.md  (in <S>/w, outside the root: x.md is read from the root)")
    sb.run("nexus", "g1", "s3", "--", "grok", "--wrapper", "./x.md", cwd="w",
           label="nexus g1 s3 -- grok --wrapper ./x.md  (in <S>/w: ./x.md, once)")
    sb.symlink("w/plink", sb.projects)
    sb.run("nexus", "g1", "s3", "--", "grok", "--wrapper", "x.md", cwd="w/plink/app",
           label="nexus g1 s3 -- grok --wrapper x.md  (in <S>/w/plink/app, a link to the root: the same dir, no note)")
    ctr(sb, "g1", wrapper_check="grok: --wrapper %s/x.md: no such file here (a persona sees only its projects root "
        "and its scratch dir): nexus <persona> --wrapper FILE copies a file in" % sb.projects)
    sb.run("nexus", "g1", "s3", "--", "grok", "--wrapper", os.path.join(sb.projects, "x.md"), cwd="w",
           label="nexus g1 s3 -- grok --wrapper <P>/x.md  (in <S>/w: an absolute path, no note)")
    ctr(sb, "g1", wrapper_check_rc=None, wrapper_check="other")
    sb.run("nexus", "g1", "s4", "--", "/opt/grok/bin/grok", "--wrapper", "rules/a.md",
           label="nexus g1 s4 -- /opt/grok/bin/grok --wrapper rules/a.md  (not the shim)")
    ctr(sb, "g1", wrapper_check=None, no_shim=True)
    sb.run("nexus", "g1", "s5", "--", "grok", "--wrapper", "rules/a.md", label="nexus g1 s5 -- grok --wrapper "
           "rules/a.md  (an image without the shim)")
    ctr(sb, "g1", no_shim=None)
    # Without --wrapper in it: no check, as ever (-A).
    sb.run("nexus", "g1", "s6", "--", "grok", "--single", "hi")
    # The shim's own bare -- ends its options: a --wrapper after it is grok's words, so no check (-A), and nexus's own
    # --wrapper next to it isn't given twice.
    sb.run("nexus", "g1", "s7", "--", "grok", "--", "--wrapper", "x",
           label="nexus g1 s7 -- grok -- --wrapper x  (after the shim's own --: no check)")
    sb.run("nexus", "g1", "--wrapper", "rules/a.md", "s8", "--", "grok", "--", "--wrapper", "x",
           label="nexus g1 --wrapper rules/a.md s8 -- grok -- --wrapper x  (nexus's, once)")
    # A shim line with = in it: kept whole, after the check= line.
    ctr(sb, "g1", wrapper_check_rc=2, wrapper_check="grok: --wrapper needs a file: --wrapper FILE, or --wrapper=FILE")
    sb.run("nexus", "g1", "s9", "--", "grok", "--wrapper", label="nexus g1 s9 -- grok --wrapper  (the shim's line has =)")
    # A shim that fails saying nothing: its exit code.
    ctr(sb, "g1", wrapper_check_rc=3, wrapper_check="")
    sb.run("nexus", "g1", "s10", "--", "grok", "--wrapper", "rules/a.md",
           label="nexus g1 s10 -- grok --wrapper rules/a.md  (the shim says nothing)")
    # The -- form is a grok persona's: a claude persona's command as ever (-A, no check).
    sb.run("nexus", "c1", "s1", "--", "grok", "--wrapper", "rules/a.md")


@case("nexus/wrapper-dashdash-existing")
def _(sb):
    # The -- form for a session that is there already: tmux would attach to it without running the command, so
    # refused before the exec; the session named, or the one the dir gives.
    two(sb)
    sb.run("nexus", "g1", "wa", "--", "grok", "--wrapper", "rules/a.md")
    sb.run("nexus", "g1", "--", "grok", "--wrapper", "../rules/a.md", cwd="projects/app",
           label="nexus g1 -- grok --wrapper ../rules/a.md  (in projects/app: the session app)")
    sb.run("nexus", "g1", "main", "--", "agent", "--wrapper=rules/a.md")
    sb.run("nexus", "g1", "wa", "--", "grok", "--wrapper", "sp ace/w x.md", "--single=a b",
           label="nexus g1 wa -- grok --wrapper 'sp ace/w x.md' '--single=a b'  (the hint's command shell-quoted)")


@case("nexus/wrapper-copy-missing")
def _(sb):
    # g1 with a wrapper (nexus --config), its copy personas/g1.wrapper.md gone: not started (Docker would make the
    # mount's source a root-owned directory), with how to give it again or none; c1 starts as ever.
    source(sb, "w/default.md")
    sb.personas("c1:claude:docker", "g1:grok:docker", extra=("--wrapper", "g1:" + sb.path("w/default.md")))
    os.unlink(sb.path("a/personas/g1.wrapper.md"))
    sb.run("nexus", "g1", "start")
    sb.run("nexus", "g1", label="nexus g1  (an attach to one that isn't running)")
    sb.run("nexus", "c1", "start", label="nexus c1 start  (its service doesn't mount it)")
    sb.container("g1")
    sb.run("nexus", "g1", "restart")
    sb.run("nexus", "g1", label="nexus g1  (running: an attach, as ever)")


@case("nexus/wrapper-not-running")
def _(sb):
    # g1 not running: --wrapper's FILE is held to the shim's rules before it is started, so a bad one starts nothing
    # (no compose up); a good one starts it, then the new session.
    sb.personas("c1:claude:docker", "g1:grok:docker")
    files(sb)
    sb.run("nexus", "g1", "--wrapper", "rules/none.md", "s1")
    sb.run("nexus", "g1", "headless", "nxt hi", "--wrapper", "../rules", cwd="projects/app")
    sb.run("nexus", "g1", "--wrapper", "rules/a.md", "s1")


@case("nexus/wrapper-word-command")
def _(sb):
    # The word with a grok or agent command after the --: the probe runs it as the shim would, with NEXUS_WRAPPER the
    # file as the persona sees it (its real grok swapped for /bin/true), so a command that isn't the shim, or one the
    # shim would stop at once (the file and the command's own --rules over the limit, say), starts nothing.
    two(sb)
    sb.run("nexus", "g1", "--wrapper", "rules/a.md", "s1", "--", "grok", "--single", "hi")
    sb.run("nexus", "g1", "--wrapper", "rules/a.md", "s2", "--", "bash",
           label="nexus g1 --wrapper rules/a.md s2 -- bash  (no grok or agent: a shell, no check)")
    sb.run("nexus", "g1", "--wrapper", "rules/a.md", "wa", "--", "grok",
           label="nexus g1 --wrapper rules/a.md wa -- grok  (there under that file: attached, no check)")
    ctr(sb, "g1", wrapper_check="other")
    sb.run("nexus", "g1", "--wrapper", "rules/a.md", "s3", "--", "/opt/grok/.grok/bin/grok", "--single", "hi",
           label="nexus g1 --wrapper rules/a.md s3 -- /opt/grok/.grok/bin/grok --single hi  (grok itself, not the shim)")
    ctr(sb, "g1", wrapper_check_rc=2, wrapper_check="grok: --wrapper %s/rules/a.md (NEXUS_WRAPPER): is too big: 122878 "
        "bytes, 8 of --rules and the 2 always kept for a blank line come to 122888, over the 122880 (120 KiB) one "
        "argument takes: shorten it" % sb.projects)
    sb.run("nexus", "g1", "--wrapper", "rules/a.md", "s4", "--", "grok", "--rules", "be terse",
           label="nexus g1 --wrapper rules/a.md s4 -- grok --rules 'be terse'  (the shim would stop at once)")
    # A file outside the root: its copy, made by this run, goes again with the refusal (and no note says it went in
    # its place); one there before stays, as another session may run under it.
    out = source(sb, "w/out.md", "# From outside\n")
    ctr(sb, "g1", wrapper_check="grok: --wrapper %s (NEXUS_WRAPPER): is too big: …" % sb.path(
        "scratch/g1/wrappers/%s-out.md" % hashlib.sha256(b"# From outside\n").hexdigest()[:12]))
    sb.run("nexus", "g1", "--wrapper", out, "s5", "--", "grok", "--rules", "be terse")
    sb.run(os.path.join(sb.bin, "ls"), "-A", "scratch/g1/wrappers", cwd=".", label="ls -A scratch/g1/wrappers  (no copy)")
    ctr(sb, "g1", wrapper_check_rc=None, wrapper_check=None)
    sb.run("nexus", "g1", "--wrapper", out, "s6", "--", "grok")
    ctr(sb, "g1", wrapper_check="other")
    sb.run("nexus", "g1", "--wrapper", out, "s7", "--", "/opt/grok/.grok/bin/grok",
           label="nexus g1 --wrapper <S>/w/out.md s7 -- /opt/grok/.grok/bin/grok  (the copy s6 made stays)")
    ctr(sb, "g1", wrapper_check=None)
    # A session there already under another wrapper: the hint keeps the command, shell-quoted.
    sb.run("nexus", "g1", "--wrapper", "../rules/a.md", "--", "grok", "--single", "hi there", cwd="projects/app",
           label="nexus g1 --wrapper ../rules/a.md -- grok --single 'hi there'  (in projects/app: the session app)")


@case("nexus/wrapper-copy-after-checks")
def _(sb):
    # A file outside the root is copied in only once the session is known to start (or to be attached to): a run
    # refused for main, a session under another wrapper, a probe that fails or an image without the shim leaves no copy.
    two(sb)
    out = source(sb, "w/out.md", "# From outside\n")
    sb.run("nexus", "g1", "--wrapper", out, "main")
    sb.run("nexus", "g1", "--wrapper", out, "wa")
    ctr(sb, "g1", wrapper_probe_rc=1)
    sb.run("nexus", "g1", "--wrapper", out, "s1", label="nexus g1 --wrapper <S>/w/out.md s1  (the probe fails)")
    ctr(sb, "g1", wrapper_probe_rc=None, no_shim=True)
    sb.run("nexus", "g1", "--wrapper", out, "s1", label="nexus g1 --wrapper <S>/w/out.md s1  (an image without the shim)")
    sb.run("nexus", "g1", "headless", "nxt hi", "--wrapper", out, cwd="projects/app")
    sb.run(os.path.join(sb.bin, "ls"), "-A", "scratch/g1", cwd=".", label="ls -A scratch/g1  (no copy)")
    ctr(sb, "g1", no_shim=None)
    sb.run("nexus", "g1", "--wrapper", out, "s1", label="nexus g1 --wrapper <S>/w/out.md s1  (it starts: the copy)")
