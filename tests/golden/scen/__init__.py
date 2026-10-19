"""The golden scenarios, one module per area (config_cli, config_interactive, nexus_basic, …). Each
scenario is a function of a Sandbox (golden.py): it sets the scenario up (setup runs aren't recorded), then
makes the run(s) that are. Names are <tool>/<what>; the tool is "config" (the setup tool, whatever its
file is called) or "nexus".

The Sandbox, in short (golden.py has the rest):
  sb.personas(*specs, extra=())   config --yes --no-link --no-build --projects-root <P> … (a setup run)
  sb.setup(tool, *args, stdin=, ok=(0,), cwd=, env=)   any setup run; it must exit with a code in ok
  sb.run(tool, *args, mode="pipe"|"tty-in"|"tty-all", stdin=, steps=[(substring, answer)…], cwd=,
         env={K: V or None}, merge=False, via=PATH, timeout=30, label=)      a recorded run
         tool is "nexus", "config", or the path of an executable (a wrapper script, say)
  sb.container(persona, state="running", root=, sessions=[…], **fields)  a container in the docker world
  sb.load_world() / sb.world / sb.save_world()                           the world itself (fake.py)
  sb.write(rel, text, mode=)  sb.path(rel)  sb.symlink(rel, target)  sb.script(rel, body)  sb.agent_socket()
  sb.hold_lock()              a context manager: the tools' lock taken, as another run would hold it
  sb.a, sb.home, sb.projects, sb.tmp, sb.cfg (the setup tool's file name in this tree)
"""
import json
import os
import subprocess
import time

CASES = []


class Case:
    def __init__(self, name, fn, fakes=(), norm=None):
        self.name, self.fn, self.fakes, self.norm = name, fn, tuple(fakes), norm


def case(name, fakes=(), norm=None):
    def wrap(fn):
        if any(c.name == name for c in CASES):
            raise ValueError("two scenarios named " + name)
        CASES.append(Case(name, fn, fakes, norm))
        return fn
    return wrap


NO_LINK = ("--no-link", "--no-build")

# The header config.sh wrote above its `# persona:` lines, before personas.json.
OLD_HEADER = (
    "# compose.personas.yml — written by ./config-tools; re-run it to add, change or remove personas.\n"
    "# config-tools and the nexus launcher read the \"# persona:\" lines back: keep them in step with\n"
    "# the services below if you edit this by hand.\n"
)


def move_lines(sb):
    """After a hand edit of the `# persona:` lines (see before_personas_json): config-tools --yes, not recorded,
    which moves them into personas.json (the bash's config.sh wrote them out anew); a line it refuses leaves
    the file as it is, in either tree."""
    sb.setup("config", "--yes", "--no-link", "--no-build", ok=(0, 1))


def set_field(sb, name, key, value):
    """A hand edit of one persona's KEY (VALUE None: taken out), wherever the tree keeps it: in personas.json,
    or in the `# persona:` line of a tree from before it (root= before ssh=, as config.sh wrote them)."""
    j = os.path.join(sb.a, "personas.json")
    if os.path.exists(j):
        with open(j, encoding="utf-8", errors="surrogateescape") as f:
            data = json.load(f)
        rec = data["personas"][name]
        if value is None:
            rec.pop(key, None)
        else:
            rec[key] = value == "yes" if key == "statusline" else value
        with open(j, "w", encoding="utf-8", errors="surrogateescape") as f:
            f.write(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
        return
    p = os.path.join(sb.a, "compose.personas.yml")
    with open(p) as f:
        lines = f.read().split("\n")
    for i, l in enumerate(lines):
        if not l.startswith("# persona: %s " % name):
            continue
        words = [w for w in l[len("# persona: "):].split(" ") if not w.startswith(key + "=")]
        if value is not None:
            at = next((n for n, w in enumerate(words) if key == "root" and w.startswith("ssh=")), len(words))
            words.insert(at, "%s=%s" % (key, value))
        lines[i] = "# persona: " + " ".join(words)
        break
    with open(p, "w") as f:
        f.write("\n".join(lines))


def before_personas_json(sb):
    """The checkout as it was before personas.json: the personas as `# persona:` lines in compose.personas.yml,
    so a scenario can edit them, or check their move. A tree that keeps personas.json gets that file (from
    personas-view) and loses personas.json; the bash's is that way already."""
    j = os.path.join(sb.a, "personas.json")
    if not os.path.exists(j):
        return
    lines = subprocess.run([os.path.join(sb.bin, "personas-view")], cwd=sb.a, stdout=subprocess.PIPE).stdout.decode()
    p = os.path.join(sb.a, "compose.personas.yml")
    with open(p) as f:
        text = f.read()
    with open(p, "w") as f:
        f.write(OLD_HEADER + lines + text[text.index("\nservices:\n"):])
    os.unlink(j)


def two_personas(sb):
    """A Claude persona with your agent and a grok one with a key of its own: the usual setup."""
    sb.personas("c1:claude:docker", "g1:grok:docker", extra=("--ssh", "g1:new"))


def images(sb):
    """The image each persona's service resolves to, as compose would (the tag follows the pins)."""
    sb.load_world()
    sb.world["service_images"] = {"c1": "local/agent-claude-docker:{tag}", "g1": "local/agent-grok-docker:{grok_tag}"}
    sb.save_world()


def snapshot_line(model="Opus 5.5", used=38000, size=200000, pct=19, five=12, week=40, cost="1.2345", age=3 * 86400):
    """A statusline snapshot as statusline.sh writes it (v1, tab-separated), drawn AGE seconds ago."""
    return "\t".join(["v1", str(int(time.time()) - age), model, str(used), str(size), str(pct), str(five),
                      str(week), cost])
