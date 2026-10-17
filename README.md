# agents — persona containers for dev VMs

One long-lived container per persona per VM. Each holds a tmux server; each project you
work on is a tmux session inside it. The container is a sibling of your project stacks on
the VM's Docker daemon, so `docker compose …` (and `ddev …`) typed inside it act on the VM.

## Quick start

```bash
bash install.sh                # once, in this checkout: add a persona, e.g. claude-team (flavour claude)
cd ~/projects/billing          # a project under the projects root
nexus claude-team -- claude    # first time: session "billing", straight into Claude Code (log in once)
```

Leave with `Ctrl-b d` (detach): Claude keeps running. From then on, from the same project directory, just:

```bash
nexus claude-team              # back into the same session
```

Quitting Claude (`/exit`) ends the session instead; start it again with `-- claude`. A grok persona works
the same way with `-- grok`. When a new Claude Code (or grok) comes out, `nexus claude-team upgrade` pins it,
rebuilds, and recreates every persona of that flavour once you confirm.
How the pieces fit together, as diagrams: [Annex: how it works](#annex-how-it-works).

## What's in the checkout

The checkout is generic. `./install.sh` decides which personas a VM runs, the flavour of each
(Claude Code or Grok Build), and the context each one works in:

- **ddev**: ddev projects. ddev runs inside the persona, sharing the VM's ddev state and mkcert CA.
- **docker**: projects run with docker compose / Dockerfiles, a Go API say. No ddev at all.

```
install.sh              setup CLI: personas, flavours, contexts, .env, launcher link, images
compose.yml             templates only: _agent (context docker) and _agent-ddev
compose.personas.yml    the personas, written by install.sh (gitignored)
Dockerfile              targets claude | grok, build arg CONTEXT=docker | ddev; no project toolchains on purpose
entrypoint.sh           keeps the tmux server alive, paints the persona into the status bar
tmux.conf               session-per-project ergonomics
nexus                   launcher: `nexus <persona>` from any project dir
statusline/             Claude Code statusline + its setting, mounted into Claude personas that have it on
runbook.md              setup, day-to-day use, upgrades, troubleshooting, every environment variable
LICENSE                 MIT, for the files in this repo (not the software the images download)
.env.example            per-VM settings template → .env (gitignored)
personas/*.env.example  per-flavour templates → personas/<persona>.env: secrets + git identity (gitignored)
```

## Why Nexus

**Your AI coding agents, always on, always in the right project.**

Nexus gives each of your AI agent identities its own long-lived container on your dev VM, with its own login,
git identity and GitHub token, and a tmux session for every project. Type `nexus <persona>` in any project
directory and you're in. Detach, and the agents keep working. Reconnect from any terminal and pick up where
they are.

- **One command, any project.** `nexus <persona>` opens, or reattaches to, the session for the directory
  you're in. `nexus ls` shows everything running.
- **Personas, not just containers.** Each has its own account, git author, token and persistent home. Run
  several side by side on one project, a team account next to a personal plan or Claude next to Grok, each
  in its own colour in the status bar (*persona @ VM*).
- **Agents that keep running.** Sessions survive disconnects: detach, close the laptop, come back later.
- **Native to your stack.** It uses the same Docker daemon, network and paths as the VM, so `ddev` and
  `docker compose` work inside exactly as outside. ddev personas share ddev's state and its local CA, so
  `https://*.ddev.site` is trusted inside. No project toolchains in the image: the agent drives the
  project's own containers, so it fits any stack.
- **Upgrades on your terms.** Claude Code, Grok Build, APM and ddev versions are pinned and the built-in
  auto-updaters are off. `nexus <persona> upgrade` moves to a new release in one command, and logins survive it.
- **Sensible limits.** SSH through agent forwarding only (private keys never enter a container), no
  privilege escalation, and CPU, memory and process limits per persona.
- **Agent packages built in.** Microsoft's APM (Agent Package Manager) installs skills, prompts, agents and
  MCP servers for each persona.
- **Set up in minutes.** Run the installer interactively, or scripted with `--yes`. It writes nothing until
  every answer checks out. Build the images locally or pull them from your registry.

## Install on a VM

Needs on the VM: Docker Engine with the compose plugin (your user in the `docker` group), and an
ssh-agent holding your key (`ssh-add -l`). For ddev-context personas also ddev from pkg.ddev.com
(it provides `mkcert`). Step by step: [runbook.md](runbook.md).

```bash
sudo git clone <this repo> /opt/agents && sudo chown -R "$USER" /opt/agents
/opt/agents/install.sh                     # asks: personas (flavour, context, name, root), VM settings, sign-in
cd ~/projects/billing && nexus claude-ddev # e.g. a persona named claude-ddev: opens session "billing"
```

`install.sh` suggests `<flavour>-<context>` names: with the default answers you get `claude-ddev` on a VM
with ddev, `claude-docker` without. It writes `.env`, `personas/<name>.env` (mode 600) and
`compose.personas.yml`, all together and only once `docker compose config` accepts them. Each persona mounts
the VM's projects root, or a root of its own; several personas can share one. It links the
launcher (`/usr/local/bin/nexus`, via sudo if needed), offers `mkcert -install` for ddev contexts, and
builds the images (or pulls them with a registry). Re-run it to add, change or remove personas.
`./install.sh --help` lists flags that do the same without questions, e.g.
`./install.sh --yes --persona claude-docker:claude:docker`.

Inside: `claude`, or open a new session straight into it with `nexus claude-ddev review -- claude`
(the command only runs when the session is created; an existing session is just attached).
`Shift+←/→` hops between project sessions, `Prefix+s` lists them, `Prefix+d` detaches and leaves
everything running. `nexus ls` shows every running persona and its sessions.
Every persona also has `apm` ([Agent Package Manager](https://microsoft.github.io/apm/)): `apm install`
in a project deploys its skills, prompts, agents and MCP servers to that persona's tool (runbook §2).
In Claude personas with the statusline on (install.sh asks), Claude Code's statusline is
`statusline/statusline.sh` (model, dir, branch, context, cost, limits); edit it here and the next
refresh picks it up. A change to `statusline/settings.json` needs `nexus <persona> restart`.

## Rules that keep it working

- **One uid everywhere.** The image user's uid is fixed at build time and each persona's `/home/dev`
  is a volume seeded from the image once. `nexus … build` uses your `id -u`/`id -g`, so a VM user that
  isn't 1000 gets a matching local build, but a pulled image must have been built for the same uid
  (its `agent.uid` label). After a uid change, also remove the persona's volume (`agents_<persona>-home`).
- **ddev context: run ddev from inside the persona, not from the VM shell.** DDEV bakes username/uid
  into its web image; alternating between host user and container user forces a rebuild each time.
- **ddev context: pin DDEV_VERSION to the VM's `ddev version`** (install.sh does). The router and
  ssh-agent are global containers; a version mismatch makes each side recreate them.
- **Upgrading a CLI = a new image + recreating the containers on it.** `nexus <persona> upgrade [VERSION]`
  does all of it for Claude Code and grok: it pins `CLAUDE_VERSION` or `GROK_VERSION` (the newest release by
  default; the pin is VM-wide), builds or pulls each context's image, recreates every persona of that
  flavour once you confirm (that ends their tmux sessions; `claude --resume` picks conversations back up)
  and removes the old images. By hand it is build/pull once per context + `nexus <persona> restart` for
  each persona of the flavour. The CLIs live outside the home
  volume, so a new image is enough; auth, settings and sessions in the volume are untouched. Pin versions
  and bump them: `latest` (or a `stable` channel) is cached once built and needs
  `nexus <persona> build --no-cache` instead. APM's `APM_VERSION` is bumped by hand the same way.
- **The image is the only Claude Code updater.** Claude Code comes from Anthropic's native installer at
  build time, and the image locks self-updates (`/etc/claude-code/managed-settings.d/`). `claude update` or
  `claude install` would put a copy in the home volume that shadows the image's binary in login shells
  and never upgrades. If an older volume has one, run `rm -rf ~/.local/bin/claude ~/.local/share/claude`
  inside the persona. `claude doctor` still notes "~/.local/bin is not in your PATH"; that is expected.
  APM's `apm self-update` is switched off in the image the same way.
- **The ssh-agent socket path is fixed when the container is created.** A forwarded agent (`ssh -A`) dies
  with that login, so after reconnecting run `nexus <persona> restart`. After a VM reboot the persona
  stays stopped until the next `nexus <persona>` if that socket is gone; run `nexus <persona> start`
  before headless jobs. An agent that outlives logins avoids both; when your shell has no agent, the
  launcher tries `/run/user/<uid>/ssh-agent.sock` first.
- **The socket is root on the VM.** Treat the dev VM as the blast radius: snapshot before an
  unattended `--dangerously-skip-permissions` run, no production secrets on dev VMs, egress
  allowlist at the hypervisor or router level (host networking rules out an in-container firewall).
  Building needs the hosts in the runbook's [prerequisites](runbook.md#prerequisites) on that allowlist.

## Headless / pipeline use

Same container, no tmux. The persona must be running: `nexus claude-ddev start` starts it without attaching.

```bash
docker exec -w ~/projects/billing agent-claude-ddev \
  claude -p "$(cat task.md)" --output-format json --allowedTools "Edit,Bash(ddev *)"
```

`Bash(ddev *)` allows every ddev subcommand, `ddev exec <anything>` included; in a docker-context persona
allow what the project needs instead, e.g. `Bash(docker compose *)`. Docker access is root on the VM, so
keep such runs to repos you trust ([runbook](runbook.md#3-headless-runs)).

## License

The files in this repo are under the [MIT License](LICENSE). The images they build download third-party
software that keeps its own license and terms of use: Claude Code (Anthropic), Grok Build (xAI), APM, ddev,
the Docker CLI, the GitHub CLI and Node.js among others. Building or using an image is subject to those
terms, and they decide whether you may share a built image, e.g. on a public registry. This project is not
affiliated with or endorsed by Anthropic or xAI.

## Annex: how it works

The persona names are examples; so is the split of projects roots in the first diagram.

### What runs on a VM

```text
VM
├── Docker daemon ◄─────────── /var/run/docker.sock, mounted into every persona
│   ├── agent-claude-team   ─┐  persona containers: one per persona, long-lived
│   ├── agent-claude-max     ├─ each has a tmux server, one session per project,
│   ├── agent-grok          ─┘  its CLI, and apm, docker, ddev, gh, node
│   └── project stacks (ddev web/db, compose services)  ◄── started from inside a persona
│
├── projects roots, mounted at the same path inside:
│   ├── /home/local/src    the VM's root: claude-max, grok
│   └── /home/local/work   a root of its own: claude-team
├── ssh-agent              its socket is mounted; no private keys inside
├── ~/.ddev + mkcert CA    shared with ddev-context personas
└── ~/agent                checkout: install.sh, nexus, .env, compose files, personas/*.env
```

### Inside one persona

```text
agent-claude-max                        image: local/agent-claude-ddev:<version>
├── tmux server     status bar "claude-max @ <vm>", in the persona's colour
│   ├── main        always there
│   ├── billing  ◄── nexus claude-max -- claude, run in /home/local/src/billing
│   └── api      ◄── nexus claude-max -- claude, run in /home/local/src/api
├── /home/dev           volume agents_claude-max-home: login, settings, history (kept across rebuilds)
├── /home/local/src     its projects root (same path as on the VM)
├── /ssh-agent          your agent
└── claude, apm, docker, ddev, gh   from the image, so a new image upgrades them
```

### Setup, once per VM (re-run it to change anything)

```text
bash install.sh
  │  asks: personas: flavour, context, name, statusline, projects root (- = the VM's)
  │        VM:       projects root, registry, Claude Code version
  │        new ones: sign-in key/token, git identity, GH_TOKEN
  ▼
writes .env, compose.personas.yml, personas/<name>.env    all together, or nothing
  ▼
links /usr/local/bin/nexus
  ▼
builds one image per flavour × context      (pulls instead when a registry is set)
  local/agent-claude-ddev:<ver>    local/agent-grok-ddev:<ver>
```

### Daily use

```text
cd /home/local/src/billing
nexus claude-max -- claude
  │
  ├─ looks up the persona: flavour, context, projects root
  ├─ container running? ── no ──► docker compose up -d (mounts the root, the socket, the ssh-agent)
  ├─ session "billing" exists? ── yes ─► attach
  │                            └─ no ──► create it in billing/, running claude
  ▼
working in Claude Code
  ├─ Ctrl-b d  detach: Claude keeps running ──► later, plain `nexus claude-max` reattaches
  └─ /exit     Claude quits and the session ends ──► next time, `-- claude` again
```

### A new release

```text
nexus claude-max upgrade [VERSION | stable] [-y]
  ├─ takes the lock (one install.sh or upgrade at a time)
  ├─ looks up the newest version (downloads.claude.ai; x.ai/cli for grok)
  ├─ pins CLAUDE_VERSION in .env ─────────── on a failure or Ctrl-C, the old pin goes back
  ├─ builds each context's image, checks `claude --version`
  ├─ lists the open sessions, asks ──► recreates every Claude persona (claude-team, claude-max)
  └─ removes the old images
```

### How an image is built (Dockerfile)

```text
tools: apt, docker CLI, node, gh, python3 ─► ctx-docker │ ctx-ddev (+ ddev) ─► base: user, tmux, entrypoint
fetch ─► claude-cli  Anthropic's installer ─┐
      ─► grok-cli    xAI's installer        ├─ copied into ─► target claude │ grok
      ─► apm-cli     PyPI, in a venv       ─┘
```
