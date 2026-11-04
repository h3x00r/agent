"""The Nexus notes: what every persona's CLI is told about the place it runs in (the shared Docker daemon, the paths
a container can mount, its scratch dir, its ports, names, cleaning up, the Docker guard). No I/O here.

personas/notes.md (committed) is the template; nexus --config fills it in for each persona as
personas/NAME.notes.md (gitignored, mode 644), written in place like a grok persona's managed config, so a running
container (a single-file mount) sees a new one at its CLI's next session. Mounted read-only:
- claude: /etc/claude-code/CLAUDE.md, Claude Code's managed CLAUDE.md (loaded first in every session, subagents
  too; a user can't exclude it);
- grok: /etc/grok/rules/00-nexus.md, a rule loaded in every session because its managed config lists the dir in
  [paths] extra_rule_dirs (nexus --config appends that table to personas/NAME.grok.toml).

render() fills in the placeholders by plain replacement: {{persona}} {{cli}} {{projects_root}} {{scratch}} {{ports}}
{{guard}}. Any other {{…}} in the template is an error that names it, so a typo isn't sent to every CLI.

Pitfalls (each pinned where named):
- a placeholder nexus doesn't know: refused, named, before anything is replaced (a value holding {{…}} is no
  placeholder) (test_domain.Notes.test_render)
- the guard as the entrypoint reads it: off only when exactly off (test_domain.Notes.test_guard)
"""
import re

TEMPLATE = "personas/notes.md"
TARGETS = {"claude": "/etc/claude-code/CLAUDE.md", "grok": "/etc/grok/rules/00-nexus.md"}
CLI = {"claude": "Claude Code", "grok": "Grok Build"}
GROK_RULES = "/etc/grok/rules"
GROK_PATHS = '\n# The Nexus notes, %s: nexus --config adds this table.\n[paths]\nextra_rule_dirs = ["%s"]\n' \
    % (TARGETS["grok"], GROK_RULES)
NO_PORTS = "none assigned"
KNOWN = ("persona", "cli", "projects_root", "scratch", "ports", "guard")
_PLACEHOLDER = re.compile(r"\{\{([^{}]*)\}\}")


class BadTemplate(ValueError):
    pass


def file(name):
    """NAME's notes, relative to the checkout."""
    return "personas/%s.notes.md" % name


def guard(shell, dotenv):
    """DOCKER_GUARD as a persona's entrypoint takes it: the shell's (SHELL), else .env's (DOTENV); off only when it
    is off, on otherwise (the default)."""
    return "off" if (shell or dotenv) == "off" else "on"


def render(template, persona, cli, projects_root, scratch, ports, guard_state):
    unknown = [m.group(0) for m in _PLACEHOLDER.finditer(template) if m.group(1) not in KNOWN]
    if unknown:
        raise BadTemplate("has %s, which nexus doesn't fill in (it knows %s)"
                          % (", ".join(sorted(set(unknown))), " ".join("{{%s}}" % k for k in KNOWN)))
    values = {"persona": persona, "cli": cli, "projects_root": projects_root, "scratch": scratch,
              "ports": ports or NO_PORTS, "guard": guard_state}
    return _PLACEHOLDER.sub(lambda m: values[m.group(1)], template)
