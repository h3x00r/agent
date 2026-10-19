"""nexus --config's command line: the flags, checked before anything is asked or written.

The grammar is the bash tool's, kept as it was (not argparse, whose exit codes and messages differ):
options are read left to right; --help prints the usage and stops when it is reached; a flag that takes a
value takes the next argument whatever it looks like, as long as it isn't empty; the last of a repeated
single-value flag wins; anything else is "unknown option '…' (see --help)", exit 1.

Pitfalls (each pinned where named):
- --help only where it is reached (test_configtools.Parse.test_help_stops_where_it_is_read)
- the next argument, even one like a flag; an empty one refused
  (test_configtools.Parse.test_a_value_is_the_next_argument_whatever_it_is, golden config/cli-need-argument)
"""
from agentctl.core.errors import NexusError

USAGE = """\
nexus --config — set this checkout up for one dev VM: which personas it runs, the flavour of each
(claude | grok), and the context each one works in:
  ddev     ddev projects: ddev inside, sharing the VM's ~/.ddev and mkcert CA
  docker   projects run with docker compose / Dockerfiles: no ddev at all

  nexus --config                  interactive; re-run it any time to add, change or remove personas
  nexus --config --list           the personas set up so far
  nexus --config --yes [options]  no questions, for scripts: current settings are kept, and the
                                defaults below apply to new ones
    --persona NAME:FLAVOUR:CONTEXT[:ROOT]
                                     add or update a persona; repeatable. ROOT: its own projects root
                                     (empty: the VM's; left out: an existing persona keeps its own)
    --ssh NAME:agent|none|new|KEY    how that persona's ssh (git over ssh) signs in: agent, your ssh-agent
                                     (the default); none, not at all (pull and push from the VM); new, a
                                     key of its own made as personas/NAME.key, to add as a deploy key (an
                                     existing one is kept); or KEY, the path of a private key of yours
                                     without a passphrase; repeatable
    --remove NAME                    drop a persona (its env file and home volume stay); repeatable
    --projects-root DIR              default: $HOME/projects
    --registry HOST                  local (build on this VM; the default) or host[:port][/path]
    --claude-version V               latest | stable | X.Y.Z (default: latest)
    --statusline | --no-statusline   the statusline (what nexus top shows) for the personas given with
                                     --persona (default: on for a new one; an existing one keeps its own)
    --link DIR | --no-link           where the `nexus` launcher goes (default: /usr/local/bin)
    --no-build                       don't build (or pull) the images at the end

Writes .env, personas.json (this VM's personas; the one before is kept as personas.json.bak), the
compose.personas.yml generated from it, and personas/<name>.env (mode 600; an existing one is kept), all
at once and only after `docker compose config` accepts them: a run that stops early changes nothing,
except the projects roots (the VM's and a persona's own), which are created as they are named. A
checkout from before personas.json has its personas moved into it (the old file is kept as .bak). Then
it makes the ssh keys asked for (personas/<name>.key) that don't exist yet. Every Claude persona gets
personas/claude.managed.json (committed) as a managed settings drop-in; nexus merges the defaults in
personas/claude.json into its own settings when it starts it. A grok persona gets
personas/<name>.grok.toml, its grok managed config: personas/grok.toml (the grok settings for every
grok persona, committed) and, with the statusline on, statusline/grok/settings.toml.
"""


class Options:
    def __init__(self):
        self.yes = False
        self.list = False
        self.build = True
        self.statusline = ""          # "", "yes" or "no": what --[no-]statusline set
        self.link = "/usr/local/bin"  # or "none"
        self.root = ""
        self.registry = ""
        self.claude = ""
        self.add = []
        self.ssh = []
        self.remove = []
        self.help = False


VALUED = {
    "--persona": ("NAME:FLAVOUR:CONTEXT[:ROOT]", "add"),
    "--ssh": ("NAME:agent, NAME:none, NAME:new or NAME:KEY", "ssh"),
    "--remove": ("a NAME", "remove"),
    "--projects-root": ("a DIR", "root"),
    "--registry": ("local or a HOST", "registry"),
    "--claude-version": ("latest, stable or X.Y.Z", "claude"),
    "--link": ("a DIR", "link"),
}


def parse(argv):
    o = Options()
    i = 0
    while i < len(argv):
        a = argv[i]
        if a in ("-h", "--help"):
            o.help = True
            return o
        if a == "--list":
            o.list = True
        elif a in ("-y", "--yes"):
            o.yes = True
        elif a in VALUED:
            what, attr = VALUED[a]
            v = argv[i + 1] if i + 1 < len(argv) else ""
            if not v:
                raise NexusError("%s needs %s (see --help)" % (a, what))
            if isinstance(getattr(o, attr), list):
                getattr(o, attr).append(v)
            else:
                setattr(o, attr, v)
            i += 1
        elif a == "--statusline":
            o.statusline = "yes"
        elif a == "--no-statusline":
            o.statusline = "no"
        elif a == "--no-link":
            o.link = "none"
        elif a == "--no-build":
            o.build = False
        else:
            raise NexusError("unknown option '%s' (see --help)" % a)
        i += 1
    return o
