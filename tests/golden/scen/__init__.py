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
