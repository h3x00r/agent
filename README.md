# agents — persona containers for dev VMs

One long-lived container per persona per VM. Each holds a tmux server; each project you
work on is a tmux session inside it. The container is a sibling of your project stacks on
the VM's Docker daemon, so `docker compose …` (and `ddev …`) typed inside it act on the VM.

## Quick start

```bash
./nexus --config               # once, in this checkout: the shared settings, then a persona, e.g. claude-team
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
As committed, Claude runs its tools without asking; `nexus claude-team --skip-bypass-permissions review -- claude`
opens a session whose Claude asks first ([Settings for every persona](#settings-for-every-persona)).
How the pieces fit together, as diagrams: [Annex: how it works](#annex-how-it-works).

**New to this?** [EXAMPLES.md](EXAMPLES.md) explains it in plain words and walks through the everyday tasks,
one example at a time: setting up, starting a project, leaving it running, two accounts, a second opinion, a
quick question from a script. It is the place for examples; this README is how it is built.

## What's in the checkout

The checkout is generic. `nexus --config` decides which personas a VM runs, the flavour of each
(Claude Code or Grok Build), and the context each one works in:

- **ddev**: ddev projects. ddev runs inside the persona, sharing the VM's ddev state and mkcert CA.
- **docker**: projects run with docker compose / Dockerfiles, a Go API say. No ddev at all.

```
compose.yml             templates only: _agent (context docker) and _agent-ddev
personas.json           the personas, written by nexus --config (gitignored)
compose.personas.yml    their services, generated from personas.json by nexus --config; don't edit it (gitignored)
Dockerfile              targets claude | grok, build arg CONTEXT=docker | ddev; no project toolchains on purpose
entrypoint.sh           keeps the tmux server alive, paints the persona into the status bar
claude-mode.sh          the image's `claude`: Claude Code (/usr/local/libexec/claude) in a session's permission mode,
                        if nexus gave it one (--skip-bypass-permissions | --bypass-permissions); else unchanged
tmux.conf               session-per-project ergonomics
nexus                   launcher: `nexus <persona>` from any project dir; `nexus --config`: the setup (personas,
                        flavours, contexts, .env, launcher link, images)
agentctl/               the Python behind nexus, which is a shim (3.8+, standard library only)
statusline/claude/      Claude Code's statusline + its setting, for Claude personas with it on; feeds `nexus top`
statusline/grok/        grok's statusline + its [ui.status_line], for grok personas with it on; feeds `nexus top`
ssh/                    ssh settings for personas with a key of their own (a deploy key, say), or no ssh
tests/                  run.sh: end-to-end tests of nexus and nexus --config; unit/: unit tests; golden/: recorded
                        scenarios; secrets.sh: the secret scan (gitleaks)
.github/workflows/      ci.yml: GitHub Actions on every push and pull request: secret scan, unit tests, run.sh
EXAMPLES.md             the everyday tasks, step by step, in plain words: start here
runbook.md              setup, day-to-day use, upgrades, troubleshooting, every environment variable
LICENSE                 MIT, for the files in this repo (not the software the images download)
.env.example            per-VM settings template → .env (gitignored)
personas/*.env.example  per-flavour templates → personas/<persona>.env: secrets + git identity (gitignored)
personas/claude.json    Claude Code defaults for every Claude persona, on every VM (merged into its own settings)
personas/claude.managed.json  Claude Code settings that hold in every Claude persona (a managed drop-in)
personas/claude.ask.json  the permission mode a Claude persona set to ask starts in (a managed drop-in after that one)
personas/grok.toml      grok settings for every grok persona, on every VM (its managed config, with the statusline)
```

## Why Nexus

**Your AI coding agents, always on, always in the right project.**

Nexus gives each of your AI agent identities its own long-lived container on your dev VM, with its own login,
git identity and GitHub token, and a tmux session for every project. Type `nexus <persona>` in any project
directory and you're in. Detach, and the agents keep working. Reconnect from any terminal and pick up where
they are.

- **One command, any project.** `nexus <persona>` opens, or reattaches to, the session for the directory
  you're in. `nexus ls` shows every persona, running or not, and its sessions, as tables.
- **See them all at once.** `nexus top` is a live view: CPU and memory per persona, and per Claude Code
  session the model, context use, 5-hour and 7-day plan limits and cost.
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
- **Sensible limits.** SSH through your forwarded agent (your keys never enter a container), a key of the
  persona's own such as a deploy key, or none at all; no privilege escalation; CPU, memory and process
  limits per persona.
- **Agent packages built in.** Microsoft's APM (Agent Package Manager) installs skills, prompts, agents and
  MCP servers for each persona.
- **Set up in minutes.** Run `nexus --config` and answer, or script it with `--yes`. Each step is saved only
  once it checks out, so one stopped or refused halfway changes nothing. Build the images locally or pull them
  from your registry.

## Install on a VM

Needs on the VM: Docker Engine with the compose plugin (your user in the `docker` group), Python 3.8 or
newer (Ubuntu 20.04 has 3.8; nothing else to install), and an ssh-agent holding your key (`ssh-add -l`) for
the personas that use it. For ddev-context personas also ddev from pkg.ddev.com (it provides `mkcert`). Step
by step: [runbook.md](runbook.md).

```bash
sudo git clone <this repo> /opt/agents && sudo chown -R "$USER" /opt/agents
/opt/agents/nexus --config                 # asks: the shared settings, the launcher, a first persona; then a menu
cd ~/projects/billing && nexus claude-ddev # e.g. a persona named claude-ddev: opens session "billing"
```

The first time, `nexus --config` asks the settings every persona shares (the projects root, the image
registry, the Claude Code version), where to link the launcher (`/usr/local/bin/nexus`, via sudo if needed),
and a first persona; it suggests `<flavour>-<context>` names: with the default answers you get `claude-ddev`
on a VM with ddev, `claude-docker` without. It then builds that persona's image (or pulls it with a registry)
and offers `mkcert -install` for a ddev context. After that, and every time you run it, it shows what is set
up and a menu: list, add, change or remove a persona, the shared settings, the launcher, the images. Each
action is saved when it is done: `.env`, `personas/<name>.env` (mode 600), `personas.json` and the
`compose.personas.yml` generated from it, all together and only once `docker compose config` accepts them.
Each persona mounts the VM's projects root, or a root of its own; several personas can share one.
`nexus --config --help` lists flags that do it all without questions, e.g.
`nexus --config --yes --persona claude-docker:claude:docker`.

Inside: `claude`, or open a new session straight into it with `nexus claude-ddev review -- claude`
(the command only runs when the session is created; an existing session is just attached).
`nexus claude-ddev --skip-bypass-permissions review -- claude` opens one whose Claude asks before running a tool,
whatever the persona's setting, and `--bypass-permissions` one whose Claude doesn't (Claude personas only; the word
goes anywhere before the `--`). The mode holds for that session as long as it lives, new windows and a claude started
again included; an existing session in the other mode is refused, not attached. The words need an image with
`claude-mode.sh`: `nexus claude-ddev build` (with a registry, `pull`) and `restart`, or the next `upgrade`; nexus
refuses them on an older one.
`Shift+←/→` hops between project sessions, `Prefix+s` lists them, `Prefix+d` detaches and leaves
everything running. `nexus ls` shows every persona and its state, and the sessions of the running ones;
`nexus top` keeps a live view of them (`q` quits), with Claude Code's own figures for each session.
Every persona also has `apm` ([Agent Package Manager](https://microsoft.github.io/apm/)): `apm install`
in a project deploys its skills, prompts, agents and MCP servers to that persona's tool (runbook §2).
In personas with the statusline on (`nexus --config` asks), the CLI's statusline is `statusline/claude/statusline.sh`
or `statusline/grok/statusline.sh` (model, dir, branch, context, cost; Claude's also its plan limits); edit it
here and the next refresh picks it up. A change to `statusline/claude/settings.json` needs
`nexus <persona> restart`. The same figures are what `nexus top` shows, so a persona without the statusline
shows its sessions without them. Every persona also gets its flavour's settings from `personas/`, as defaults or
enforced ([Settings for every persona](#settings-for-every-persona)).

## Rules that keep it working

- **One uid everywhere.** The image user's uid is fixed at build time and each persona's `/home/dev`
  is a volume seeded from the image once. `nexus … build` uses your `id -u`/`id -g`, so a VM user that
  isn't 1000 gets a matching local build, but a pulled image must have been built for the same uid
  (its `agent.uid` label). After a uid change, also remove the persona's volume (`agents_<persona>-home`).
- **ddev context: run ddev from inside the persona, not from the VM shell.** DDEV bakes username/uid
  into its web image; alternating between host user and container user forces a rebuild each time.
- **ddev context: pin DDEV_VERSION to the VM's `ddev version`** (`nexus --config` does). The router and
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
  stays stopped until the next `nexus <persona>` if that socket is gone; `nexus <persona> start` (or a
  headless run) starts it, given an agent. An agent that outlives logins avoids both; when your shell has
  no agent, the launcher tries `/run/user/<uid>/ssh-agent.sock` first. A persona with an ssh key of its own,
  or with no ssh at all, is spared both: it starts without an agent. Without ssh it still commits, and you
  pull and push from the VM in the same directory (runbook §5).
- **An ssh key of its own is readable inside.** Anything that runs in that persona, a prompt-injected agent
  included, can copy it. Give it a key that only does what the persona should: a deploy key on the repos it
  works on, or a machine user's key, never your own. Removing it on the git host cuts it off (runbook §5).
- **The ssh choices steer a persona's own ssh; they aren't a wall.** Every persona has the Docker socket
  (next rule), so one that goes rogue can still get at your agent through a persona that has it, at your
  `~/.ssh`, or at the other personas' keys. And a persona without ssh still pushes over https with a
  `GH_TOKEN`. What contains an agent is the VM, so a passphrase on your own key still matters.
- **The socket is root on the VM.** Treat the dev VM as the blast radius: snapshot before an
  unattended `--dangerously-skip-permissions` run, no production secrets on dev VMs, egress
  allowlist at the hypervisor or router level (host networking rules out an in-container firewall).
  Building needs the hosts in the runbook's [prerequisites](runbook.md#prerequisites) on that allowlist.
  For a person to approve Claude's tools first, set the persona to ask, or start the session with
  `--skip-bypass-permissions` ([Settings for every persona](#settings-for-every-persona)); it is the mode Claude
  starts in, not a lock.

## Settings for every persona

Three committed files hold the settings every persona of a flavour gets, on every VM that pulls the repo, and a
fourth the permission mode of a Claude persona set to ask. Each is either **defaults**, which a persona's own
settings override key by key, or **enforced**, which win over them. Nothing is copied by hand; the three that are
mounted are read-only, and the one written into a persona's home goes in through a merge that keeps the persona's
own choices.

| File | Kind | How it reaches each persona of its flavour | A change applies |
|---|---|---|---|
| `personas/claude.json` | defaults: `model`, effort per model, `theme`, `outputStyle`, `tui`, … | nexus merges it into the persona's own `~/.claude/settings.json` each time it creates or starts the container, before any Claude runs: Claude Code has no defaults layer of its own | at `nexus <persona> restart` |
| `personas/claude.managed.json` | enforced: `permissions.defaultMode`, `skipDangerousModePermissionPrompt`, `env` | compose mounts it read-only as the managed drop-in `/etc/claude-code/managed-settings.d/15-personas.json`, which outranks every other layer but a later drop-in (`16-ask.json`) | at `nexus <persona> restart`: a single-file mount keeps the version the container started with |
| `personas/claude.ask.json` | enforced, for a Claude persona set to ask: `permissions.defaultMode` (`default` as committed: it asks before each tool that needs it; edit it for `acceptEdits`, `plan`, …) | compose mounts it read-only, in a persona set to ask only, as `/etc/claude-code/managed-settings.d/16-ask.json`: after `15-personas.json`, so its mode wins over `bypassPermissions` | at `nexus <persona> restart`, as above; until then a persona newly set to ask gets a warning at each start and attach |
| `personas/grok.toml` | defaults: all of it | `nexus --config` writes it, with the statusline's `[ui.status_line]` where that's on, to `personas/<name>.grok.toml`; compose mounts that read-only as grok's `/etc/grok/managed_config.toml`, a layer below the persona's own `~/.grok/config.toml` | once `nexus --config` has re-run, at the next grok started in the persona: the file is rewritten in place, so no restart |

- The merge records in `~/.claude/.nexus-defaults.json` the keys nexus owns. A key the persona doesn't have gets the
  default; one nexus owns follows a changed default (also after Claude's own migrations, such as `opus` to
  `opus[1m]`); one the persona had, set, changed (`/model`, `/config`) or took out stays its own; and a default
  dropped from `personas/claude.json` goes where nexus owned it. A `settings.json` that isn't JSON, or can't be
  written, is left alone, with a warning. nexus says which keys it set.
- `nexus --config` checks the files first (JSON; TOML with tomllib; `claude.ask.json` only while a persona is set to
  ask) and writes nothing if one wouldn't parse: a broken one would stop the CLI in every persona it reaches. While a
  persona is set to ask, `nexus --config` writes nothing, and nexus won't start or restart that persona, if
  `claude.ask.json` isn't there, can't be read, isn't JSON, sets no `permissions.defaultMode` or sets
  `bypassPermissions`: the persona would bypass.
- Effort as a Claude default goes per model (`modelSettings.opus.effortLevel`): Claude Code ignores a top-level
  `effortLevel` in a user's own settings.
- As committed, they make every persona run its tools without asking: Claude in `bypassPermissions`, grok in
  `always-approve`. A Claude persona can be set to ask instead, in `nexus --config` (add or change: "Let NAME run its
  tools without asking …? [Y/n]", answer n) or with `nexus --config --permissions NAME:ask` (`NAME:bypass` undoes
  it), applied at `nexus NAME restart`. Every claude in it then starts in `claude.ask.json`'s mode, headless runs
  included; no rebuild needed. Grok can't be set to ask here: its mode is `permission_mode` in `personas/grok.toml`,
  a default for every grok persona (re-run `nexus --config` after an edit) that a persona's own `~/.grok/config.toml`
  overrides.
- One session can differ from its persona: `nexus <persona> --skip-bypass-permissions [session]` starts one that asks
  (in Claude Code's `default` mode, whatever `claude.ask.json` sets), `--bypass-permissions` one that doesn't even in
  a persona set to ask (with `headless`: that run), so a session can be made less safe than its persona as well as
  safer. Either way it is the mode Claude starts in, not a lock: a flag typed after `claude` still wins, so
  `claude --dangerously-skip-permissions` in a session that asks still bypasses. Unlike the persona's setting, the
  words need an image with `claude-mode.sh` (`nexus <persona> build`, with a registry `pull`, then `restart`); nexus
  refuses them on an older one.
- Details, and how to see what a persona got: runbook §5, [Settings for every persona](runbook.md#settings-for-every-persona).

## Headless / pipeline use

One prompt, no tmux. `nexus <persona> headless` runs the persona's CLI in its one-shot mode (`claude -p`, or
grok's) in the directory you're in, and starts the persona if it isn't running. You give the prompt and the
CLI's own flags; its output and exit code are the command's.

```bash
cd ~/projects/billing
nexus claude-ddev headless "$(cat task.md)" --output-format json --allowedTools "Edit,Bash(ddev *)"
git diff | nexus claude-ddev headless "review this diff"     # piped input goes with the prompt
nexus grok-docker headless "list the TODOs" --output-format json
```

The prompt comes first, then the flags. Piped input is added after the prompt, for grok as for Claude, and
without a prompt it is the prompt (runbook §3 has the details). Ctrl-C, or a `timeout` around the command,
stops the run inside the persona too; a plain `docker exec` would leave it running.

`Bash(ddev *)` allows every ddev subcommand, `ddev exec <anything>` included; in a docker-context persona
allow what the project needs instead, e.g. `Bash(docker compose *)`. Docker access is root on the VM, so
keep such runs to repos you trust ([runbook](runbook.md#3-headless-runs)).

In a Claude persona set to ask, a headless run has nobody to approve a tool, so the tools that need it are denied:
allow them with `--allowedTools`, or bypass for that run: `nexus claude-ddev --bypass-permissions headless "…"`.
With `--skip-bypass-permissions`, a run in a persona that bypasses has them denied the same way. Both words need an
image with `claude-mode.sh`.

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
├── ssh-agent              its socket is mounted into the personas that use it: your keys stay outside
├── ~/.ddev + mkcert CA    shared with ddev-context personas
└── ~/agent                checkout: nexus, .env, personas.json, compose files,
                           personas/ (*.env, *.key, grok.toml)
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
├── /ssh-agent          your agent (with a key of its own: /run/nexus/ssh-key and no agent; with none: neither)
└── claude, apm, docker, ddev, gh   from the image, so a new image upgrades them
```

### Settings into a persona

```text
on the VM, in the checkout                         inside a Claude persona
personas/claude.json ───── nexus, as it starts ──► ~/.claude/settings.json            merged: the persona's own keys stand
                                                   ~/.claude/.nexus-defaults.json     what nexus wrote, for the next merge
personas/claude.managed.json ───── mount (ro) ───► /etc/claude-code/managed-settings.d/15-personas.json   wins
personas/claude.ask.json ───────── mount (ro) ───► /etc/claude-code/managed-settings.d/16-ask.json        wins over 15's mode
                                   (a persona set to ask)
statusline/claude/settings.json ── mount (ro) ───► /etc/claude-code/managed-settings.d/20-statusline.json
                                  (the image's own 10-agent-image.json: its update lock)

                                                   inside a grok persona
personas/grok.toml ──────┐
statusline/grok/         ├─ nexus --config ─► personas/<name>.grok.toml ─ mount (ro) ─► /etc/grok/managed_config.toml
  settings.toml ─────────┘  (statusline on)                                              below ~/.grok/config.toml, which wins
```

### Setup, once per VM (run it again to change anything)

```text
nexus --config
  │  first run:  shared settings: projects root, registry, Claude Code version   ─► .env
  │              the launcher: links /usr/local/bin/nexus
  │              the first persona: flavour, context, name, statusline, projects root (- = the VM's), ssh,
  │                permissions (claude), then its sign-in key/token, git identity, GH_TOKEN ─► saved, its image built
  ▼
what is set up (shared settings, launcher, personas, env files of no persona), and a menu:
  list · add · change · remove · shared · launcher · images · quit
  │  each action: its questions, then saved: .env, personas.json, personas/<name>.env, and
  │               compose.personas.yml generated from personas.json; all together, or nothing
  │  then:        personas/<name>.key for a new ssh key (its public half shown, to add), the image
  ▼
quit: how to start one
```

`nexus --config --yes [options]` does it all in one go, without questions: the settings, the personas, the link,
one image per flavour × context (`local/agent-claude-ddev:<ver>`, …; pulled instead when a registry is set).

### Daily use

```text
cd /home/local/src/billing
nexus claude-max -- claude
  │
  ├─ looks up the persona in personas.json: flavour, context, projects root
  ├─ container running? ── no ──► docker compose up -d (mounts the root, the socket, the ssh-agent or its key)
  ├─ session "billing" exists? ── yes ─► attach
  │                            └─ no ──► create it in billing/, running claude
  ▼
working in Claude Code
  ├─ Ctrl-b d  detach: Claude keeps running ──► later, plain `nexus claude-max` reattaches
  └─ /exit     Claude quits and the session ends ──► next time, `-- claude` again
```

### Watching them

```text
inside a persona, in each tmux pane running Claude Code (or grok):
  the CLI redraws its statusline ─► statusline/<cli>/statusline.sh draws the line
                                        └─ and saves model, context, 5h/7d limits, cost
                                           to ~/.cache/nexus/status/<pane>
on the VM:
nexus top [-n SECONDS]               redraws every 5s; q quits
  ├─ docker ps, docker stats ─────► state, CPU, memory per persona: the first table
  ├─ docker exec <persona> ──────► its panes running claude (or grok), each with its saved figures
  └─ the second table: a row per pane    SEEN = since the CLI last drew; grok's have no 5H/7D
```

### One prompt, headless

```text
git diff | nexus claude-max headless "review this diff" --output-format json
  ├─ in the persona's projects root? ── no ──► refuses
  ├─ persona running? ── no ──► docker compose up -d             (progress on stderr)
  ├─ piped input: read to its end first (next to a prompt, only if it starts within 3 s)
  ├─ claude: claude -p --output-format json -- "review this diff"   the diff follows the prompt
  │  grok:   grok --single="…" …, or with input piped in: grok --prompt-file /dev/stdin, fed prompt + input
  ├─ docker exec -w <this dir> (-i only for piped input; never -t), no tmux, in a session of its own
  ├─ Ctrl-C / TERM / hangup ──► passed on to the CLI's process group inside ──► exit 130 / 143
  └─ stdout, stderr and exit code: the CLI's
```

### A new release

```text
nexus claude-max upgrade [VERSION | stable] [-y]
  ├─ takes the lock (one nexus --config or upgrade at a time)
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
