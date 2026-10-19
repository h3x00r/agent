"""Review of the nexus port, lines 1-274 of the bash launcher: the top level, the tables and nexus ls."""
import os

from scen import before_personas_json, case, move_lines, two_personas  # noqa: F401
from scen.nexus_lifecycle import unix_socket


def edit_line(sb, name, fn):
    """The `# persona: NAME ` line of the personas file (bytes) replaced by fn(line): a hand edit of a checkout
    from before personas.json (move_lines then has nexus --config move them)."""
    before_personas_json(sb)
    p = sb.a + "/compose.personas.yml"
    with open(p, "rb") as f:
        lines = f.read().split(b"\n")
    pre = b"# persona: " + name.encode() + b" "
    lines = [fn(l) if l.startswith(pre) else l for l in lines]
    with open(p, "wb") as f:
        f.write(b"\n".join(lines))


def set_env(sb, line):
    """.env with its PROJECTS_ROOT line(s) replaced by LINE (bytes; may be several lines)."""
    p = sb.a + "/.env"
    with open(p, "rb") as f:
        keep = [l for l in f.read().split(b"\n") if l and b"PROJECTS_ROOT" not in l]
    with open(p, "wb") as f:
        f.write(b"\n".join(keep + [line]) + b"\n")


def no_cnf(text):
    return text.replace("command not found", "command-not-found")


# --- .env ------------------------------------------------------------------------------------------
@case("nexus/edge-ls-dotenv-latin1")
def _(sb):
    # A .env value with a byte that isn't UTF-8 (a Latin-1 é): sed's . and [^"] don't match it in a UTF-8
    # locale, so the bash keeps the quotes / the comment.
    two_personas(sb)
    s = sb.s.encode()
    with open(sb.a + "/.env", "rb") as f:
        orig = f.read()
    set_env(sb, b'PROJECTS_ROOT="' + s + b'/caf\xe9"')
    sb.run("nexus", "ls", label="(.env PROJECTS_ROOT=\"<S>/caf\\xe9\") nexus ls")
    set_env(sb, b"PROJECTS_ROOT='" + s + b"/caf\xe9'")
    sb.run("nexus", "ls", label="(.env PROJECTS_ROOT='<S>/caf\\xe9') nexus ls")
    set_env(sb, b"PROJECTS_ROOT=" + s + b"/x # caf\xe9 comment")
    sb.run("nexus", "ls", label="(.env PROJECTS_ROOT=<S>/x # caf\\xe9 comment) nexus ls")
    sb.write("a/.env", orig)   # tree.txt can't normalise the sandbox's path in a file that isn't UTF-8


@case("nexus/edge-ls-dotenv-forms")
def _(sb):
    # The dotenv forms env_get reads, and the shell's value over .env's.
    two_personas(sb)
    s = sb.s
    forms = ["export PROJECTS_ROOT=", "PROJECTS_ROOT=%s/a1\r" % s, "  export   PROJECTS_ROOT = '%s/a 2'  # c" % s,
             'PROJECTS_ROOT="%s/a\\\\3\\"\\$x\\tz\\n\\n"' % s, "PROJECTS_ROOT=%s/a4\nPROJECTS_ROOT=%s/a5" % (s, s),
             "PROJECTS_ROOT=%s/a6#no-space   " % s, "PROJECTS_ROOT=''", "PROJECTS_ROOT=\"%s/a7" % s,
             "#PROJECTS_ROOT=%s/a8" % s, "PROJECTS_ROOTX=%s/a9" % s, "PROJECTS_ROOT='%s/it\\'s'" % s]
    for i, f in enumerate(forms):
        set_env(sb, f.encode())
        sb.run("nexus", "ls", label="(.env form %d) nexus ls" % i)
    set_env(sb, ("PROJECTS_ROOT=%s/env" % s).encode())
    sb.run("nexus", "ls", env={"PROJECTS_ROOT": ""}, label="PROJECTS_ROOT= nexus ls")
    sb.run("nexus", "ls", env={"PROJECTS_ROOT": s + "/sh"}, label="PROJECTS_ROOT=<S>/sh nexus ls")
    sb.run("nexus", "ls", env={"PROJECTS_ROOT": "rel/x"}, label="PROJECTS_ROOT=rel/x nexus ls")


@case("nexus/edge-ls-dotenv-unreadable")
def _(sb):
    # .env or the personas file there but unreadable (written by root, say): made so only for the run.
    two_personas(sb)
    sb.container("c1")
    # The personas file is personas.json, or compose.personas.yml in a tree from before it.
    pf = "personas.json" if os.path.exists(sb.a + "/personas.json") else "compose.personas.yml"
    for f, args in ((".env", ["ls"]), (pf, ["ls"]), (pf, ["c1", "ls"])):
        what = ".env" if f == ".env" else "the personas file"
        w = sb.script("wrap/locked-" + what.replace(" ", "-") + "-" + args[0],
                      'chmod 000 "%s/%s"; "%s/nexus" "$@"; rc=$?; chmod 644 "%s/%s"; exit $rc'
                      % (sb.a, f, sb.a, sb.a, f))
        sb.run(w, *args, label="(%s unreadable) nexus %s" % (what, " ".join(args)))


@case("nexus/edge-ls-dotenv-fifo")
def _(sb):
    # .env a named pipe (as a secrets manager mounts one): bash's -f says no file.
    two_personas(sb)
    w = sb.script("wrap/fifo-env", 'cd "%s" && mv .env .env.real && python3 -I -c "import os; os.mkfifo(\'.env\')" && '
                  'timeout 5 ./nexus "$@"; rc=$?; rm -f .env; mv .env.real .env; exit $rc' % sb.a)
    sb.run(w, "ls", label="(.env a fifo) nexus ls")


@case("nexus/edge-ls-dotenv-nul")
def _(sb):
    two_personas(sb)
    sb.container("c1", root=sb.projects)
    set_env(sb, b"PROJECTS_ROOT=" + sb.projects.encode() + b"\x00z")
    sb.run("nexus", "ls", label="(.env root with a NUL) nexus ls")
    sb.run("nexus", "c1", "ls", label="(.env root with a NUL) nexus c1 ls")
    sb.run("nexus", "c1", "stop", label="(.env root with a NUL) nexus c1 stop")


# --- the personas file ---------------------------------------------------------------------------
@case("nexus/edge-ls-names-latin1")
def _(sb):
    # A persona line with a Latin-1 byte (its root): the bash's "set up:" list keeps what .* can't match.
    two_personas(sb)
    edit_line(sb, "c1", lambda l: l + b" root=" + sb.s.encode() + b"/caf\xe9")
    move_lines(sb)
    sb.run("nexus", "nope")
    sb.run("nexus", "ls")


@case("nexus/edge-ls-line-tab")
def _(sb):
    # A tab between the fields of a persona line (hand-edited).
    two_personas(sb)
    edit_line(sb, "c1", lambda l: l.replace(b" context=", b"\tcontext="))
    edit_line(sb, "g1", lambda l: l.replace(b"# persona: g1 ", b"# persona: g1\t"))
    move_lines(sb)
    sb.run("nexus", "ls")
    sb.run("nexus", "c1", "ls")
    sb.run("nexus", "g1", "ls")
    sb.run("nexus", "nope")


@case("nexus/edge-ls-line-empty-meta")
def _(sb):
    # "# persona: c1 " and nothing after it: bash's meta is two spaces, which it takes for no persona.
    two_personas(sb)
    sb.container("c1")
    edit_line(sb, "c1", lambda l: b"# persona: c1 ")
    move_lines(sb)
    sb.run("nexus", "c1", "ls")
    sb.run("nexus", "ls")


@case("nexus/edge-ls-line-spaces")
def _(sb):
    # Extra spaces: after the prefix, between fields, at the end; a second line for one persona.
    two_personas(sb)
    edit_line(sb, "c1", lambda l: l.replace(b"# persona: c1 ", b"# persona:   c1   ").replace(b" ", b"  ") + b"   ")
    edit_line(sb, "g1", lambda l: l + b"\n# persona: g1 flavour=claude context=ddev")
    edit_line(sb, "g1", lambda l: l + b"\n# persona: \n\n# persona:\tc9 flavour=claude\n#  persona: c8 flavour=grok")
    move_lines(sb)
    sb.container("c1")
    sb.run("nexus", "ls")
    sb.run("nexus", "c1", "ls")
    sb.run("nexus", "g1", "ls")


@case("nexus/edge-ls-line-nul")
def _(sb):
    two_personas(sb)
    sb.container("c1")
    edit_line(sb, "c1", lambda l: l.replace(b"# persona: c1 ", b"# persona: c\x001 "))
    move_lines(sb)
    sb.run("nexus", "ls")


# --- AGENTS_DIR ------------------------------------------------------------------------------------
@case("nexus/edge-ls-agents-dir-dslash")
def _(sb):
    two_personas(sb)
    sb.container("old", state="exited")
    sb.run("nexus", "nope", env={"AGENTS_DIR": "/" + sb.a}, label="AGENTS_DIR=/<A> nexus nope")
    sb.run("nexus", "ls", env={"AGENTS_DIR": "/" + sb.a}, label="AGENTS_DIR=/<A> nexus ls")


@case("nexus/edge-ls-agents-dir-dotdot-link")
def _(sb):
    # AGENTS_DIR with .. after a symlink: bash's cd takes the .. lexically.
    two_personas(sb)
    sb.symlink("deep/link", sb.a)
    sb.run("nexus", "nope", env={"AGENTS_DIR": sb.path("deep/link/../link")},
           label="AGENTS_DIR=<S>/deep/link/../link nexus nope")


@case("nexus/edge-ls-agents-dir-rel-linked-cwd")
def _(sb):
    # A relative AGENTS_DIR, from a directory reached by a symlink: bash's cd is logical.
    two_personas(sb)
    sb.write("projects/sub/.keep", "")
    sb.symlink("plink", sb.projects + "/sub")
    sb.run("nexus", "nope", cwd="plink", env={"AGENTS_DIR": "../a"}, label="(in <S>/plink) AGENTS_DIR=../a nexus nope")


@case("nexus/edge-ls-agents-dir-cdpath")
def _(sb):
    two_personas(sb)
    sb.run("nexus", "nope", env={"AGENTS_DIR": "a", "CDPATH": sb.s}, label="CDPATH=<S> AGENTS_DIR=a nexus nope")


@case("nexus/edge-ls-agents-dir-option")
def _(sb):
    two_personas(sb)
    sb.run("nexus", "ls", env={"AGENTS_DIR": "-P"}, label="AGENTS_DIR=-P nexus ls")
    sb.run("nexus", "ls", env={"AGENTS_DIR": "-"}, label="AGENTS_DIR=- nexus ls")


# --- the locale --------------------------------------------------------------------------------------
@case("nexus/edge-ls-bad-locale")
def _(sb):
    # A locale that isn't installed (forwarded by ssh, say).
    two_personas(sb)
    sb.container("c1", sessions=[{"name": "main", "windows": 1, "attached": 0, "path": sb.projects},
                                 {"name": "café", "windows": 1, "attached": 1, "path": sb.projects + "/café"}])
    sb.run("nexus", "ls", env={"LC_ALL": "xx_YY.UTF-8"}, label="LC_ALL=xx_YY.UTF-8 nexus ls")
    sb.run("nexus", "ls", env={"LANG": "xx_YY.UTF-8"}, label="LANG=xx_YY.UTF-8 nexus ls")
    sb.run("nexus", "c1", "ls", env={"LANG": "xx_YY.UTF-8"}, label="LANG=xx_YY.UTF-8 nexus c1 ls")


@case("nexus/edge-ls-deleted-cwd-quote")
def _(sb):
    # The deleted-directory warning's `cd …`, quoted as printf %q does, which depends on the locale.
    two_personas(sb)
    w = sb.script("wrap/in-gone", 'd="$1"; shift\nmkdir -p "$d" && cd "$d" && rmdir "$d" && exec "%s/nexus" "$@"'
                  % sb.a)
    sb.run(w, sb.projects + "/café", "ls", env={"LC_ALL": "C"}, label="LC_ALL=C (in <P>/café, deleted) nexus ls")
    sb.run(w, sb.projects + "/a b", "ls", label="(in <P>/a<U+2028>b, deleted) nexus ls")
    sb.run(w, sb.projects + "/café", "ls", label="(in <P>/café, deleted) nexus ls")


# --- tables ----------------------------------------------------------------------------------------
@case("nexus/edge-ls-wide-tty")
def _(sb):
    two_personas(sb)
    sb.container("c1", sessions=[{"name": "main", "windows": 1, "attached": 0, "path": sb.projects},
                                 {"name": "日本語", "windows": 1, "attached": 0, "path": sb.projects + "/表"},
                                 {"name": "éx", "windows": 1, "attached": 0, "path": sb.projects + "/🙂"},
                                 {"name": "z​w", "windows": 1, "attached": 0, "path": sb.projects + "/ "},
                                 {"name": "bad\udce9", "windows": 1, "attached": 0, "path": sb.projects + "/\udcc2\udc9b"},
                                 {"name": "c1\u0085x\u009b", "windows": 1, "attached": 0, "path": "\udce2\u0080/x"}])
    sb.run("nexus", "ls", mode="tty-all")
    sb.run("nexus", "ls", mode="tty-all", env={"NO_COLOR": "1"}, label="NO_COLOR=1 nexus ls")
    sb.run("nexus", "ls")
    sb.run("nexus", "c1", "ls", env={"LC_ALL": "C"}, label="LC_ALL=C nexus c1 ls")


@case("nexus/edge-ls-session-nul")
def _(sb):
    two_personas(sb)
    sb.container("c1", sessions=[{"name": "a\x00b", "windows": 1, "attached": 0, "path": sb.projects}])
    sb.run("nexus", "ls")


# --- docker ----------------------------------------------------------------------------------------
@case("nexus/edge-ls-probe-hang")
def _(sb):
    # tmux that prints a row, then doesn't exit: 10 s, then a row saying it isn't answering.
    two_personas(sb)
    sb.container("c1", probe_hang=[14, False])
    sb.run("nexus", "ls", timeout=40)


@case("nexus/edge-ls-probe-hang-child")
def _(sb):
    # The same, with a child of docker's holding its stdout (docker run by a wrapper that didn't exec):
    # timeout 10 ends the whole process group.
    two_personas(sb)
    sb.container("c1", probe_hang=[40, True])
    sb.run("nexus", "ls", timeout=25)


@case("nexus/edge-ls-probe-rc")
def _(sb):
    two_personas(sb)
    for rc in (125, 126, 127):
        sb.container("c1", probe_rc=rc)
        sb.run("nexus", "ls", label="(probe exits %d) nexus ls" % rc)


@case("nexus/edge-ls-docker-noexec")
def _(sb):
    two_personas(sb)
    sb.container("c1")
    import os
    os.chmod(sb.bin + "/docker", 0o644)
    sb.run("nexus", "ls")


@case("nexus/edge-ls-docker-missing", norm=no_cnf)
def _(sb):
    two_personas(sb)
    import os
    os.unlink(sb.bin + "/docker")
    sb.run("nexus", "ls")


@case("nexus/edge-ls-lead-byte")
def _(sb):
    # A last cell ending in a byte that starts a UTF-8 sequence (a Latin-1 é): bash's read takes the
    # newline after it as part of that character, so `read -a` hits the end of its input and fails.
    two_personas(sb)
    sb.container("c1", sessions=[{"name": "main", "windows": 1, "attached": 0, "path": sb.projects},
                                 {"name": "latin", "windows": 1, "attached": 0, "path": sb.projects + "/caf\udce9"}])
    sb.run("nexus", "ls")
    sb.run("nexus", "c1", "ls")
    sb.container("c1")
    sb.run("nexus", "ls", env={"PROJECTS_ROOT": sb.s + "/caf\udce9"}, label="PROJECTS_ROOT=<S>/caf\\xe9 nexus ls")


@case("nexus/edge-ls-stdout-closed")
def _(sb):
    two_personas(sb)
    w = sb.script("wrap/closed-out", 'exec "%s/nexus" "$@" >&-' % sb.a)
    sb.run(w, "ls", label="nexus ls >&-")
    sb.run(w, "nope", label="nexus nope >&-")


@case("nexus/edge-ls-ids-roots")
def _(sb):
    # The ids from id and stat when unset; container roots spelt in other ways.
    two_personas(sb)
    sb.container("c1", root=sb.projects + "/")
    sb.container("g1", root=sb.projects + "\t")
    sb.container("x1", root="rel/../p")
    sb.personas("x1:claude:docker")
    # An agent socket of the sandbox's own: with AGENT_UID unset, the search would look under the real
    # /run/user/<uid>, which differs from host to host.
    sock = unix_socket(sb, "run/agent.sock")
    sb.run("nexus", "ls", env={"AGENT_UID": None, "AGENT_GID": None, "DOCKER_GID": None, "SSH_AUTH_SOCK": sock},
           label="(no AGENT_UID, AGENT_GID, DOCKER_GID) nexus ls")
    sb.run("nexus", "ls", env={"DOCKER_HOST": "tcp://127.0.0.1:9"}, label="DOCKER_HOST=tcp://127.0.0.1:9 nexus ls")


@case("nexus/edge-ls-home-unset")
def _(sb):
    # No PROJECTS_ROOT anywhere and no HOME (a service without one): bash's set -u stops at $HOME.
    two_personas(sb)
    set_env(sb, b"# no PROJECTS_ROOT")
    sb.run("nexus", "ls", env={"HOME": None}, label="(no HOME) nexus ls")
    sb.run("nexus", "ls", env={"HOME": None, "PROJECTS_ROOT": sb.projects}, label="(no HOME) PROJECTS_ROOT=<P> nexus ls")


@case("nexus/edge-ls-root-newline")
def _(sb):
    # A shell PROJECTS_ROOT ending in a newline: bash's $(lexical …) drops it, so it is c1's root.
    two_personas(sb)
    sb.container("c1")
    sb.run("nexus", "ls", env={"PROJECTS_ROOT": sb.projects + "\n"}, label="PROJECTS_ROOT=$'<P>\\n' nexus ls")
    sb.run("nexus", "c1", "ls", env={"PROJECTS_ROOT": sb.projects + "\n"}, label="PROJECTS_ROOT=$'<P>\\n' nexus c1 ls")
