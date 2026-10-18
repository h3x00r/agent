"""nexus ls (every persona, its state and projects root, the sessions of the running ones, then notes) and
nexus <persona> ls (that persona's sessions).

A session row comes from the session probe (probes/sess_probe.sh) run in the persona: each field ends in
\\037 and has its own newlines and tabs flattened, so a session name or a directory can't forge a row or
a field; what it sends is cleaned (ui.text.clean) before it is printed. A persona whose tmux doesn't
answer in 10 s gets a row saying so: a hung persona can't hang ls.

Pitfalls (each pinned where named):
- a \\037 field may be empty, unlike a tab one: the fields stay in place (golden nexus/basic-ls-session-rows)
- cleaned, one row each (golden nexus/basic-ls-control-chars)
- 10 s, the probe's whole process group (golden nexus/edge-ls-probe-hang, golden
  nexus/edge-ls-probe-hang-child)
- a probe that fails gets the same row (golden nexus/edge-ls-probe-rc)
- the persona lines as `while read` gives them, split on spaces and tabs (golden nexus/edge-ls-line-spaces,
  golden nexus/edge-ls-line-tab)
- a root compared as lexical() spells it, a trailing newline gone (golden nexus/edge-ls-root-newline)
- orphan containers in sort(1)'s order (golden nexus/basic-ls-orphan)
"""
from agentctl.adapters import docker
from agentctl.core.errors import NexusError
from agentctl.core.shellcompat import ere, lexical, read_fields, sort_lines
from agentctl.domain.roots import moved
from agentctl.ui import table
from agentctl.ui.style import Palette
from agentctl.ui.text import clean


def session_rows(container, lead, in_loop=False):
    """The session rows of CONTAINER, each prefixed with LEAD; (rows, ok)."""
    rc, out = docker.exec_probe(container, "sess_probe.sh", pipe_stdin=in_loop)
    if rc:
        return [lead + ["-", "-", "-", "tmux isn't answering"]], False
    rows = []
    for line in out.rstrip("\n").split("\n"):
        s, w, a, d, _ = read_fields(line, "\037", 5)
        if not (s + w + a + d):
            continue
        w = w if ere(r"[0-9]+", w) else "-"
        a = "yes" if ere(r"[1-9][0-9]*", a) else "no"
        rows.append(lead + [clean(s), w, a, clean(d)])
    return rows, True


def _field(rest, key):
    """`${rest#* KEY=}` up to the next space: the value of the first ` KEY=` (all of REST if none)."""
    i = rest.find(" %s=" % key)
    v = rest[i + len(key) + 2:] if i >= 0 else rest
    return v.split(" ", 1)[0]


def ls_all(ctx):
    ctx.need_personas()
    p = Palette.for_stdout()
    st = docker.States(ctx.agents_dir)
    rows = [["PERSONA", "FLAVOUR", "CONTEXT", "SSH", "STATE", "PROJECTS ROOT"]]
    srows = [["PERSONA", "SESSION", "WINDOWS", "ATTACHED", "DIR"]]
    known, conf_root, conf_from, ups, idle = set(), {}, {}, [], False
    for line in ctx.personas_lines():
        name, rest = read_fields(line, " \t\n", 2)
        rest = " %s " % rest
        c = "agent-" + name
        known.add(c)
        fl, cx = _field(rest, "flavour"), _field(rest, "context")
        how = "none" if " ssh=none " in rest else "own key" if " ssh=" in rest else "agent"
        root, conf_from[c] = ctx.vm_root, " (%s)" % ctx.vm_from
        if " root=" in rest:
            root, conf_from[c] = _field(rest, "root"), ""
        conf_root[c] = lexical(root)
        root = clean(root[:-1] if root.endswith("/") else root)
        state = st.state.get(c, "not started")
        rows.append([name, fl, cx, how, p.state(state) + state + p.reset, root])
        if c in st.up:
            srows += session_rows(c, [name], in_loop=True)[0]
            ups.append(c)
        else:
            idle = True
    ctx.out.lines(table.render("lllll", rows, p))
    if len(srows) > 1:
        ctx.out.say()
        ctx.out.lines(table.render("llrll", srows, p))
    notes = []
    old = docker.roots(ups)
    for c in ups:
        o = old.get(c, "")
        if o and o != conf_root[c]:
            notes.append(clean(moved(c[len("agent-"):], o, conf_root[c] + conf_from[c])))
    if idle:
        notes.append(p.dim + "nexus <persona> starts one that isn't running" + p.reset)
    for c in sort_lines(st.here):
        if c not in known:
            notes.append("%s is %s, and no persona has it any more: docker rm -f %s removes it" % (c, st.state[c], c))
    if notes:
        ctx.out.say()
        ctx.out.lines(notes)
    return 0


def ls_persona(ctx, persona):
    if not docker.running(persona.container):
        raise NexusError("%s isn't running; start it with: nexus %s" % (persona.name, persona.name))
    p = Palette.for_stdout()
    rows, ok = session_rows(persona.container, [])
    if not ok:
        raise NexusError("tmux in %s isn't answering; try again, or nexus %s restart" % (persona.name, persona.name))
    ctx.out.lines(table.render("lrll", [["SESSION", "WINDOWS", "ATTACHED", "DIR"]] + rows, p))
    old = old_root(persona)
    if old:
        ctx.out.warn(moved(persona.name, old, persona.root + persona.root_from))
    return 0


def old_root(persona):
    """The projects root the running container was created with, if it isn't the persona's now."""
    r = docker.roots([persona.container]).get(persona.container, "")
    return "" if not r or r == lexical(persona.root) else r

