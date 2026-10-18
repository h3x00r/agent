"""compose.personas.yml, written as text, byte for byte the file config-tools has always written.

No YAML library: nexus and the tests look for exact lines in it (the managed-settings mount, the grok
config mount, older layouts), and compose expands the ${…} in it, not this code.

render(personas, managed=…, env_files=…): the whole file. managed: whether personas/claude.managed.json
exists (its mount is left out otherwise); env_files: a name → env file path map for the staged check
before a commit (personas/NAME.env otherwise).
"""
from agentctl.domain import context, flavour
from agentctl.domain.persona import meta_line

HEADER = (
    "# compose.personas.yml — written by ./config-tools; re-run it to add, change or remove personas.\n"
    "# config-tools and the nexus launcher read the \"# persona:\" lines back: keep them in step with\n"
    "# the services below if you edit this by hand.\n"
)

MANAGED = (
    "      # The Claude Code settings that hold in every Claude persona, personas/claude.managed.json, as a\n"
    "      # managed drop-in: they win over its own settings (the defaults in personas/claude.json don't: nexus\n"
    "      # merges those into them). A single-file mount, so a change (a git pull too) needs a restart.\n"
    "      - { type: bind, source: ./personas/claude.managed.json, target: "
    "/etc/claude-code/managed-settings.d/15-personas.json, read_only: true, bind: { create_host_path: false } }\n"
)

CLAUDE_STATUSLINE = (
    "      # Claude Code statusline: the script's directory, so edits and pulls show up live, and its\n"
    "      # setting as a managed drop-in (beats a user statusLine). The drop-in is a single-file mount,\n"
    "      # so a change to statusline/claude/settings.json needs `nexus <persona> restart`.\n"
    "      - ./statusline/claude:/etc/claude-code/statusline:ro\n"
    "      - ./statusline/claude/settings.json:/etc/claude-code/managed-settings.d/20-statusline.json:ro\n"
)

GROK_STATUSLINE = (
    "      # grok's statusline: the script's directory, so edits and pulls show up live.\n"
    "      - ./statusline/grok:/etc/grok/statusline:ro\n"
)

GROK_CONFIG = (
    "      # grok's managed config, written by config-tools: personas/grok.toml, then the statusline's\n"
    "      # [ui.status_line] with the statusline on. A key in the persona's ~/.grok/config.toml wins.\n"
    "      - { type: bind, source: ./personas/{name}.grok.toml, target: /etc/grok/managed_config.toml, "
    "read_only: true, bind: { create_host_path: false } }\n"
)

NO_SSH = (
    "      # No ssh: your ssh-agent isn't mounted (/dev/null takes the socket's place, SSH_AUTH_SOCK is\n"
    "      # empty), and its ssh setting makes git over ssh fail at once rather than ask for a password.\n"
    "      - ./ssh/none.conf:/etc/ssh/ssh_config.d/50-nexus-none.conf:ro\n"
    "      - { type: bind, source: /dev/null, target: /ssh-agent }\n"
)

OWN_KEY = (
    "      # Its own ssh key, read-only, and the ssh setting that makes ssh (and git over ssh) use it. Your\n"
    "      # ssh-agent isn't mounted: /dev/null takes the socket's place, and SSH_AUTH_SOCK is empty.\n"
    "      - type: bind\n"
    "        source: '{key}'\n"
    "        target: /run/nexus/ssh-key\n"
    "        read_only: true\n"
    "        bind: { create_host_path: false }\n"
    "      - ./ssh/key.conf:/etc/ssh/ssh_config.d/50-nexus-key.conf:ro\n"
    "      - { type: bind, source: /dev/null, target: /ssh-agent }\n"
)


def _service(p, env_file, managed):
    fl, cx = flavour.get(p.flavour), context.get(p.context)
    more = ', SSH_AUTH_SOCK: ""' if p.ssh else ""
    s = (
        "\n"
        "  \"%(n)s\":\n"
        "    extends: { file: compose.yml, service: %(base)s }\n"
        "    profiles: [\"%(n)s\", \"all\"]\n"
        "    image: ${REGISTRY:-local}/agent-%(f)s-%(c)s:%(tag)s\n"
        "    build: { target: %(f)s, args: { CONTEXT: %(c)s } }\n"
        "    container_name: agent-%(n)s\n"
        "    env_file: %(env)s\n"
        "    environment: { AGENT_PERSONA: \"%(n)s\", AGENT_COLOUR: %(col)s%(more)s }\n"
        "    labels: { agent.persona: \"%(n)s\", agent.flavour: %(f)s, agent.context: %(c)s }\n"
        "    volumes:\n"
        "      - %(n)s-home:/home/dev\n"
    ) % {"n": p.name, "base": cx.base_service, "f": p.flavour, "c": p.context, "tag": fl.image_tag,
         "env": env_file, "col": p.colour, "more": more}
    if p.flavour == "claude" and managed:
        s += MANAGED
    if p.flavour == "claude" and p.statusline:
        s += CLAUDE_STATUSLINE
    elif p.flavour == "grok":
        if p.statusline:
            s += GROK_STATUSLINE
        s += GROK_CONFIG.replace("{name}", p.name)
    if p.ssh == "none":
        s += NO_SSH
    elif p.ssh:
        k = p.ssh if p.ssh.startswith("/") else "./" + p.ssh
        s += OWN_KEY.replace("{key}", k)
    return s


def render(personas, managed, env_files=None, meta_lines=True):
    env_files = env_files or {}
    out = HEADER
    if meta_lines:
        for p in personas:
            out += "# persona: %s\n" % meta_line(p)
    out += "\nservices:\n"
    for p in personas:
        out += _service(p, env_files.get(p.name, "personas/%s.env" % p.name), managed)
    out += "\nvolumes:\n"
    for p in personas:
        out += "  \"%s-home\":\n" % p.name
    return out
