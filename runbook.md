# Runbook — persona containers

How to set up, use and maintain the agent personas on a dev VM, from the user's side, plus every
environment variable involved. The design and its rules are in [README.md](README.md).

**When:** setup once per VM · daily use · upgrades and persona changes as needed.

A **persona** is one long-lived container with its own login, settings and history. Each has a
**flavour**, `claude` (Claude Code) or `grok` (Grok Build), and a **context**:

| Context | For | What the persona gets |
|---------|-----|-----------------------|
| `ddev` | ddev projects | ddev inside, sharing the VM's `~/.ddev` and mkcert CA |
| `docker` | projects run with docker compose / Dockerfiles (Go, Node, …) | no ddev at all |

## Prerequisites

- [ ] A Debian/Ubuntu dev VM with Docker Engine and the compose plugin: `docker compose version` works.
- [ ] Your VM user can use Docker without sudo: `docker ps` works. If not, `sudo usermod -aG docker "$USER"`
      and log in again.
- [ ] An ssh-agent holding your key: `ssh-add -l` lists it (connect with `ssh -A`, or run an agent on the VM).
- [ ] For ddev-context personas: ddev from pkg.ddev.com on the VM (it also provides `mkcert`).
- [ ] About 3 GB free for Docker for the first build: `df -h /var/lib/docker` (install.sh checks it too).
- [ ] Credentials: for Claude an Anthropic API key, a `claude setup-token` token or a Claude subscription to
      log in with; for grok an xAI API key.
- [ ] Network for building: Docker Hub (the `debian:bookworm-slim` base), the Debian, Docker, NodeSource and
      GitHub package hosts, pkg.ddev.com (ddev context), plus claude.ai and downloads.claude.ai (Claude Code),
      x.ai (grok), and pypi.org and files.pythonhosted.org (APM).

## 1. First-time setup on a VM

**Step 1: clone**
```bash
sudo git clone <this repo> /opt/agents && sudo chown -R "$USER" /opt/agents
```
**Expected:** `/opt/agents` exists and is owned by you. It defines no personas yet.

**Step 2: run the setup**
```bash
/opt/agents/install.sh
```
It checks Docker and your ssh-agent, then asks, in this order:

1. **Personas**, one at a time: flavour, context (the default is `ddev` if the VM has ddev), a name (suggested
   `<flavour>-<context>`: `claude-ddev` on a VM with ddev, `claude-docker` without), and for Claude whether to
   show the statusline. On a re-run it first lists the personas and asks for each: keep, change or remove.
2. **Per VM**: the projects root (default `~/projects`), the image registry (`local` = build here) and, if a
   persona is Claude, the Claude Code version (`latest`, `stable` or `X.Y.Z`). For ddev personas, the ddev
   version is taken from the VM's `ddev version`.
3. **Per persona without a `personas/<name>.env` yet, its sign-in and identity**:
   - Claude: log in interactively later, an API key, or a `claude setup-token` token.
   - grok: its xAI key.
   - Then the git author name and email (defaults from your `git config`) and an optional `GH_TOKEN`.
   Secrets are read without echo.
4. **Where to link the launcher**: a directory (default `/usr/local/bin`, giving `/usr/local/bin/nexus`; via sudo
   if needed; `none` skips it), whether
   to run `mkcert -install` (ddev contexts, if the VM has no CA yet), and whether to build the images now (or
   pull them, with a registry).

Nothing is written unless it all checks out: install.sh keeps the new `.env`, `compose.personas.yml` and
`personas/<name>.env` files staged until every question is answered and `docker compose config` accepts them,
then puts them in place together, before it links the launcher and builds. Stopping it earlier (Ctrl-C, a dropped
ssh session, the end of piped input) leaves the setup as it was; only the projects root is created as you name it.

**Expected:** a table of the personas, then `Start one from a project directory: cd …/<project> && nexus <name>`,
plus a line for anything still to do (a missing key or git identity, the mkcert CA). It has written `.env`,
`compose.personas.yml` and `personas/<name>.env` (mode 600).

**If it fails:** the message says what's missing, e.g. Docker access, and nothing has been written: fix the cause
and run install.sh again. A failed launcher link, `mkcert -install` or image build comes after the setup is in
place, so it only adds a closing line with the command to retry (a failed build or pull also makes the exit
status non-zero).

Without questions, e.g. for a script:
```bash
/opt/agents/install.sh --yes --persona claude-ddev:claude:ddev --persona claude-docker:claude:docker \
  --projects-root ~/projects --claude-version 2.1.302
```
Its closing lines list what is still missing, e.g. a grok persona's `XAI_API_KEY`, or the git identity when
`git config` has none; add that to `personas/<name>.env`. `install.sh --help` lists every flag. On a re-run,
`--yes` keeps what `.env` has unless a flag sets it. `--statusline` / `--no-statusline` turn the statusline on or
off for the Claude personas given with `--persona`; without either, a new Claude persona gets it and an existing
one keeps its setting.

**The launcher used to be called `agent`.** After `git pull`, re-run install.sh: it links `nexus` and removes
the old `agent` link into this checkout, which dangles once the script is renamed. Everything else keeps its
name (containers `agent-<persona>`, images, home volumes), so nothing is recreated.

**Upgrading a VM set up with the earlier fixed layout** (the personas were defined in compose.yml, their env
files are already in `personas/`): after `git pull`, the launcher says `no personas set up yet` until you run
install.sh interactively. It offers each `personas/<name>.env` that has no persona, one by one:
`Found personas/<name>.env (flavour <f>): set it up as persona <name>? [Y/n]`. When the file doesn't show its
flavour (a subscription persona has no key line), it asks the flavour; then the context and, for Claude, the
statusline. The env file is reused as it is, and keeping the name keeps the home volume
`agents_<name>-home`, so logins, settings and history carry over. Then `nexus <name> restart` recreates the old
container on the new image. With `--yes` nothing is offered: give the old names, e.g.
`--persona <name>:claude:ddev`.

**Step 3: first start**
```bash
cd ~/projects/billing && nexus claude-ddev       # the name install.sh printed (claude-docker on a VM without ddev)
```
**Expected:** the container is created, and you are attached to tmux session `billing` in that directory. The
status bar shows the persona's name and VM (`claude-ddev @ <vm>`) in its own colour.

**Step 4: log in** (Claude personas set to log in interactively)

Run `claude` and follow the login. With no browser in the container it shows a URL: open it on your
workstation and paste the code back. The login is kept in the persona's home volume, across restarts and
rebuilds.

**Optional: APM's default target.** `apm config set target claude` (`grok-build` in a grok persona), once per
persona; it is kept in the home volume too. Then `apm install` deploys to this persona's tool in any project
(section 2).

**Verification**
- [ ] `nexus ls` shows `== agent-claude-ddev  (healthy)` and your session.
- [ ] Inside: `claude --version` prints the version, `docker ps` lists the VM's containers, `ssh-add -l` lists
      your key, and `git var GIT_AUTHOR_IDENT` shows the persona's identity.
- [ ] ddev context: `ddev version` inside matches the VM's.

## 2. Daily use

| Task | How |
|------|-----|
| Open (or reattach to) a project | `cd ~/projects/<project> && nexus <persona>` |
| Start Claude Code | `claude` in the session; `claude --resume` to continue an earlier conversation |
| New session straight into Claude | `nexus <persona> <name> -- claude` (the command only runs when the session is created) |
| Same project, other persona | `nexus <other-persona>` from the same directory |
| Next / previous project session | `Shift+→` / `Shift+←` |
| Pick a session from a list | `Prefix+s` (Prefix is tmux's default, `Ctrl-b`) |
| New window in this project | `Prefix+c` |
| Leave, keeping everything running | `Prefix+d` |
| What is running | `nexus ls` (all personas), `nexus <persona> ls` (one persona) |
| Persona logs | `nexus <persona> logs` (follows; `Ctrl-c` to stop) |
| Start without attaching / stop | `nexus <persona> start` / `nexus <persona> stop` |
| Personas set up on this VM | `/opt/agents/install.sh --list` |

Sessions are named after the directory (`.` and `:` become `_`). Two projects with the same directory name
share one session, so give one an explicit name: `nexus claude-ddev api-b`. When a session's command exits,
tmux moves you to another session instead of dropping you out; `Prefix+d` leaves.

**APM** ([Agent Package Manager](https://microsoft.github.io/apm/)) is in every persona. In a project,
`apm install` installs what its `apm.yml` declares (skills, prompts, agents, MCP servers) and
`apm install <owner>/<repo>` adds a package. Where it deploys: `--target` if given, else the persona's default
from `apm config set target …` (setup step 4), else the folders the project has: `.claude/` (Claude Code),
`.grok/` (Grok Build), `.github/` (Copilot). With none of these it stops and asks for `--target claude` or
`--target grok-build`. `apm install -g` deploys to the persona's home (`~/.claude`, `~/.grok`), so to that
persona only. Public GitHub packages need no token; for private ones run `gh auth login` inside the persona,
or set `GH_TOKEN` or `GITHUB_APM_PAT` in its env file. MCP servers set up through APM run with what the image
has: `npx` and `docker` work, `uvx` (Python) doesn't.

## 3. Headless runs

```bash
nexus claude-ddev start          # no tty needed; does nothing if it's running
docker exec -w ~/projects/billing agent-claude-ddev \
  claude -p "$(cat task.md)" --output-format json --allowedTools "Edit,Bash(ddev *)"
```
Start the persona first, especially after a VM reboot. `Bash(ddev *)` allows every ddev subcommand,
`ddev exec <anything>` included. In a docker-context persona allow what the project needs instead, e.g.
`Bash(docker compose *)`. Docker access is root on the VM, so keep such runs to repos you trust.

## 4. Working in each context

**ddev**: use ddev from inside the persona as you would on the VM (`ddev start`, `ddev exec …`), never
alternating with the VM shell; the persona shares the VM's ddev state. Node-based tools (Claude Code, node,
playwright) trust its mkcert CA through `NODE_EXTRA_CA_CERTS`; curl, git and openssl use the system store, so use
`curl --cacert "$NODE_EXTRA_CA_CERTS" https://<project>.ddev.site` (or plain http) there.

**docker**: the persona drives the VM's Docker daemon with your project at the same path, so a compose
project works from inside exactly as from the VM shell:
```bash
docker compose up -d
docker compose exec api go test ./...
docker compose logs -f api
curl -s localhost:8080/healthz          # published ports are on the VM's loopback (host networking)
```
With no compose file, run the toolchain in a throwaway container on the same daemon. The image deliberately
carries no project toolchains (no Go, PHP or Java; python3 is there only for the statusline and APM), so versions
come from the project:
```bash
docker run --rm --user "$(id -u):$(id -g)" -e HOME=/tmp -v "$PWD:$PWD" -w "$PWD" golang:1 go test ./...
```
To keep Go's caches between runs, mount a directory under the projects root (it exists at the same path on
the VM): `mkdir -p "$PROJECTS_ROOT/.cache/go"`, then add
`-v "$PROJECTS_ROOT/.cache/go:/cache" -e GOCACHE=/cache/build -e GOMODCACHE=/cache/mod` to that command.

Either context:
- Containers that run as root write root-owned files into your project. Give dev services `user: "1000:1000"`
  in compose (or `--user "$(id -u):$(id -g)"`).
- Tell Claude how the project runs, e.g. in the project's `CLAUDE.md`: "tests: `docker compose exec api go test ./...`".

## 5. Add, change or remove a persona

Re-run `/opt/agents/install.sh`. It lists the personas and asks for each whether to keep, change (flavour,
context, statusline) or remove it, then offers to add more. Without questions:
```bash
/opt/agents/install.sh --yes --persona grok-docker:grok:docker  # add, or update flavour/context
/opt/agents/install.sh --yes --remove grok-docker               # drop it from compose.personas.yml
```
- After a change, `nexus <persona> restart` applies it (that ends its tmux sessions); install.sh says so for
  each persona it changed.
- Its build (or pull) covers every persona's image, not only a new one's: adding a grok persona also re-runs the
  claude image's build, which takes seconds from the cache when nothing changed. A running persona keeps the
  image it started with until `nexus <persona> restart`.
- An existing `personas/<name>.env` is kept. When the flavour changes, install.sh offers a new one (with `--yes`
  it writes one) and keeps the old as `.env.bak`. A new persona whose name has a leftover env file is treated the
  same way: one of its flavour is reused, one of the other flavour is replaced (the old one to `.env.bak`).
- `--statusline` / `--no-statusline` with `--persona` turn a Claude persona's statusline on or off; without
  either, an existing Claude persona keeps its setting, and a new one (or one switched from grok) gets it.
- Removing keeps the env file and the home volume; install.sh prints the commands that delete them. While that
  `personas/<name>.env` stays, an interactive run offers to set the persona up again; delete it to stop that.

## 6. Upgrade Claude Code (or grok, APM, or ddev)

1. Find the version: `curl -fsSL https://downloads.claude.ai/claude-code-releases/latest` (or `…/stable`).
2. Set it: `install.sh --yes --claude-version X.Y.Z --no-build`, or `CLAUDE_VERSION=X.Y.Z` in `.env`. The image
   tag follows it unless `CLAUDE_TAG` is set.
3. `nexus <persona> build` for one Claude persona of each context. Only the Claude installer stage reruns.
4. `nexus <persona> restart` for every Claude persona. Each restart ends that persona's tmux sessions
   (`claude --resume` picks conversations back up).
5. Check: `docker exec agent-<persona> claude --version`.

- On a channel (`CLAUDE_VERSION=latest` or `stable`) the build is cached once made: use
  `nexus <persona> build --no-cache`, which rebuilds every layer.
- grok has no pin: `nexus <grok-persona> build --no-cache && nexus <grok-persona> restart`.
- APM is in every image: set `APM_VERSION=X.Y.Z` in `.env` (current:
  `curl -fsSL https://pypi.org/pypi/apm-cli/json | jq -r .info.version`), build one persona of each flavour and
  context, and restart all personas. Only the APM stage reruns. `apm self-update` is off in the image and says
  so.
- ddev: set `DDEV_VERSION` to the VM's `ddev version` (re-running install.sh does), build one persona of each
  ddev-context flavour, and restart all ddev personas.
- Don't run `claude update` or `claude install` inside a persona. The image refuses them ("Updates are disabled
  by your administrator"), because a copy in the home volume would shadow the image's binary.

**Rollback:** set the previous `CLAUDE_VERSION`, build (quick if that version is still in the build cache), then
restart the Claude personas.

## 7. Statusline

Claude personas set up with the statusline show `statusline/statusline.sh` as Claude Code's statusline: model,
directory, git branch, context use, cost and rate limits. The directory is mounted read-only, so an edit (or a
`git pull`) shows up at the next refresh, without a restart. `statusline/settings.json` is mounted as a managed
setting, so it wins over a `statusLine` in a persona's own settings; after changing that file,
`nexus <persona> restart`. To turn it on or off for a persona, re-run install.sh and change that persona.

- The line stays blank in a project until you accept Claude Code's workspace trust prompt there (once per
  project per persona).
- The script needs python3 (it has no jq path, whatever its message says). The image has python3 as a
  dependency of NodeSource's nodejs; without it the line reads "Claude Code — statusline needs python3 (or jq)".
- Per Anthropic's docs, if a persona's account belongs to an organisation that pushes server-managed settings,
  Claude Code uses those instead of file-based ones and skips this drop-in. From v2.1.223 the image's update
  lock still applies (managed `env` merges across sources); older pins lose it too.

## 8. Reset a persona's home

```bash
nexus claude-ddev stop
docker rm agent-claude-ddev
docker volume rm agents_claude-ddev-home     # deletes its login, settings, memory and history
```
The next `nexus claude-ddev` starts it fresh, seeded from the image.

## Troubleshooting

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| `no personas set up yet: run …/install.sh` | fresh checkout | run install.sh |
| `no persona '<name>' (set up: …)` | typo, or not added yet | use a listed name, or add it with install.sh |
| `required variable SSH_AUTH_SOCK is missing a value` | no ssh-agent found | start one / `ssh -A`, `ssh-add`, then retry |
| `warning: the ssh-agent at … holds no keys` | agent without your key (often gpg-agent's) | `ssh-add`, then `nexus <persona> restart` |
| `warning: nothing answers at … (stale ssh-agent socket)` | the agent behind that socket is gone | reconnect with `ssh -A`, then `nexus <persona> restart` |
| git over ssh fails inside after you reconnected | the container still has the old login's socket | `nexus <persona> restart` |
| Persona not running after a VM reboot | its ssh-agent socket didn't exist at boot | `nexus <persona>` (or `nexus <persona> start`) |
| `the input device is not a TTY` | `nexus <persona>` attaches; a script has no tty | `nexus <persona> start` |
| `<persona> isn't running` | `nexus <persona> ls` on a stopped persona | `nexus <persona>` or `nexus <persona> start` |
| `env file …/personas/<persona>.env not found` | deleted, or the persona was added by hand | re-run install.sh (it writes a new one) |
| `PROJECTS_ROOT must be an absolute path` / `can't create PROJECTS_ROOT=…` | bad value in `.env` or your shell | fix it, or re-run install.sh |
| `no Docker socket at /var/run/docker.sock` | Docker isn't installed or running | install / start Docker |
| `permission denied … docker.sock` | your user isn't in the `docker` group | `sudo usermod -aG docker "$USER"`, log in again |
| `range of CPUs is from 0.01 to N` | `AGENT_CPUS` above the VM's vCPUs | set `AGENT_CPUS` ≤ `nproc` (install.sh caps it) |
| `pull access denied for local/agent-…` | `REGISTRY=local` means "build here" | `nexus <persona> build`, or set `REGISTRY` |
| `At least one invalid signature was encountered` (apt, during a build) | Docker's disk is full | `docker system df`; free space with `docker builder prune` / `docker image prune`, then build again |
| After `git pull`: `nexus: command not found`, or `…/agent: No such file or directory` | the launcher was renamed `agent` → `nexus` | re-run install.sh (it links `nexus`), or run `<checkout>/nexus` |
| New Claude version not showing after a build | container not recreated, or a cached channel build | `nexus <persona> restart`; with `latest`/`stable` add `--no-cache` to the build |
| `Updates are disabled by your administrator` | expected: the image is the updater | upgrade via section 6 |
| `claude doctor`: `~/.local/bin is not in your PATH` | expected with the image's layout | nothing to do |
| `The agent image is the APM updater: …` | `apm self-update` is off in the image | upgrade via section 6 |
| `apm install` stops and lists `--target` options | the project has no `.claude/`, `.grok/` or `.github/` yet | `--target claude` (or `grok-build`), or set a default once (setup step 4) |
| `Permission denied` under `~/.claude` after a uid change | the home volume was seeded for the old uid | reset the persona's home (section 8) |
| `warning: no mkcert CA on this VM yet` | ddev context, `mkcert -install` not run | `mkcert -install` on the VM, then `nexus <persona> restart` |
| `https://*.ddev.site` untrusted in Claude or node | the container predates the CA | `nexus <persona> restart` |
| `curl: (60) SSL certificate problem` for `*.ddev.site` | curl uses the system store, not the mkcert CA | `curl --cacert "$NODE_EXTRA_CA_CERTS" …` |
| `-- cmd` didn't run | the session already existed | use a new session name |
| ddev rebuilds its web image on every start | ddev run alternately from the VM and the persona | run ddev only inside the persona |
| `ddev: command not found` inside | a docker-context persona | change it to context `ddev` with install.sh |
| No statusline in Claude | workspace not trusted yet, or statusline off for this persona | accept the trust prompt (section 7); else turn it on with install.sh and restart |

## Environment variables

Precedence: a variable exported in your shell beats `.env`. `.env` only feeds compose (and the launcher reads
`PROJECTS_ROOT` from it); the persona env files go into the container's environment.

### Per VM — `/opt/agents/.env` (written by install.sh)

| Variable | Required | Default | What it does |
|----------|----------|---------|--------------|
| `PROJECTS_ROOT` | no | `$HOME/projects` | Absolute path of your projects, mounted at the same path inside |
| `REGISTRY` | no | `local` | Image name prefix. `local` = build on this VM; a registry host enables `nexus <persona> pull` |
| `CLAUDE_VERSION` | no | `latest` | Claude Code build: `latest`, `stable` or `X.Y.Z`. Pins below 2.1.118 ignore the update lock |
| `CLAUDE_TAG` | no | `CLAUDE_VERSION` | Tag of the Claude images |
| `APM_VERSION` | no | `latest` | APM in every image: `latest` or `X.Y.Z` (PyPI's `apm-cli`); `latest` is cached once built |
| `GROK_TAG` | no | `latest` | Tag of the grok images |
| `DDEV_VERSION` | no | latest in the apt repo | ddev in ddev-context images; the VM's `ddev version` (leading `v` optional) |
| `AGENT_CPUS` | no | `4`, capped at `nproc` by install.sh | CPU limit per persona; must not exceed `nproc` |
| `AGENT_MEMORY` | no | `8g` | Memory limit per persona |

### Derived by the launcher — don't set them

| Variable | Value | Override only if… |
|----------|-------|-------------------|
| `AGENT_UID` / `AGENT_GID` | `id -u` / `id -g` | you build for another uid; export it in the shell |
| `DOCKER_GID` | gid of `/var/run/docker.sock` | never |
| `SSH_AUTH_SOCK` | your shell's agent, else `/run/user/<uid>/ssh-agent.sock`, `$XDG_RUNTIME_DIR/openssh_agent`, gnome-keyring, gpg-agent | you want a specific agent |
| `AGENT_CA_CERTS` | ddev context: `~/.local/share/mkcert/rootCA.pem` if it exists, else empty | never |
| `AGENTS_DIR` | the directory the `nexus` script lives in | the repo is somewhere unusual |
| `HOME` | your VM home; ddev context mounts `~/.ddev` and the mkcert CA from there | never |

Without the launcher, export them yourself and use both compose files, e.g. from /opt/agents:
`AGENT_UID=$(id -u) AGENT_GID=$(id -g) DOCKER_GID=$(stat -c %g /var/run/docker.sock) PROJECTS_ROOT=$HOME/projects docker compose -p agents -f compose.yml -f compose.personas.yml --profile <persona> up -d`
(with `SSH_AUTH_SOCK` from your shell; `-p agents` keeps an exported `COMPOSE_PROJECT_NAME` from switching to fresh
home volumes). For a ddev persona, first `mkdir -p ~/.ddev ~/.local/share/mkcert` (or Docker creates them
root-owned), and add `AGENT_CA_CERTS=` (empty) if the VM has no mkcert CA.

### Per persona — `personas/<persona>.env` (written by install.sh from `personas/<flavour>.env.example`)

| Variable | Flavour | Required | Notes |
|----------|---------|----------|-------|
| `ANTHROPIC_API_KEY` | claude | one sign-in | Console/API billing. Wins over the token if both are set |
| `CLAUDE_CODE_OAUTH_TOKEN` | claude | one sign-in | From `claude setup-token`; Pro/Max/Team/Enterprise plan |
| (neither) | claude | one sign-in | Log in interactively inside the persona (setup step 4) |
| `XAI_API_KEY` | grok | yes | Must match `env_key` in `~/.grok/config.toml` in the persona's home |
| `GIT_AUTHOR_NAME`, `GIT_AUTHOR_EMAIL`, `GIT_COMMITTER_NAME`, `GIT_COMMITTER_EMAIL` | both | recommended | Identity of commits made in that persona |
| `GH_TOKEN` | both | no | gh CLI as that account; APM uses it for private GitHub packages too |
| `GITHUB_APM_PAT` | both | no | A token for APM only; wins over `GH_TOKEN` there |
| `DDEV_NONINTERACTIVE` | both, ddev context | no | `true`: never block an unattended run on a ddev prompt |
| `ANTHROPIC_MODEL` | claude | no | Default model for that persona |

Keep comments on their own lines: after an empty value, compose reads an inline comment as the value.

### Set inside the containers — for reference

| Variable | Value |
|----------|-------|
| `HOME` | `/home/dev` (the persona's home volume) |
| `PROJECTS_ROOT` | same as on the VM |
| `SSH_AUTH_SOCK` | `/ssh-agent` (your agent's socket, mounted) |
| `TERM` / `COLORTERM` / `LANG` | `xterm-256color` (tmux sets its own) / `truecolor` / `C.UTF-8` |
| (timezone) | the VM's: `/etc/localtime` is mounted, so clocks and commit times match it |
| `AGENT_PERSONA`, `AGENT_COLOUR` | persona name and status-bar colour |
| `DISABLE_AUTOUPDATER`, `GROK_DISABLE_AUTOUPDATER` | `1`; the Claude image also sets `DISABLE_UPDATES` and `DISABLE_INSTALLATION_CHECKS` as managed settings |
| `CAROOT`, `NODE_EXTRA_CA_CERTS` | ddev context only: the VM's mkcert CA dir and file, so ddev, node and Claude trust `https://*.ddev.site` (`NODE_EXTRA_CA_CERTS` is empty when the launcher finds no CA) |

### Build: targets and arguments

compose.personas.yml sets only the target (`claude` or `grok`, the flavour) and `CONTEXT` for each persona;
`CLAUDE_VERSION`, `APM_VERSION`, `DDEV_VERSION`, `UID` and `GID` come from compose.yml's `_agent` template, which takes them from
`.env` and the launcher. The Default column is what a plain `docker build` uses; with no `--target` you get
`claude`.

| Arg | Used by | Default |
|-----|---------|---------|
| `CONTEXT` | every target: `docker` or `ddev` | `docker` |
| `CLAUDE_VERSION` | the `claude` target | `latest` |
| `APM_VERSION` | every target | `latest` |
| `DDEV_VERSION` | `CONTEXT=ddev` | empty (latest) |
| `UID` / `GID` | every target | `1000` / `1000` |
| `NODE_MAJOR` | every target (Dockerfile only) | `22` |
| `DEBIAN_FRONTEND` | apt steps (Dockerfile only) | `noninteractive` |

Images are named `${REGISTRY}/agent-<flavour>-<context>`, so personas with the same flavour and context share
one image.
