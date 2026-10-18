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
      A persona with an ssh key of its own, or no ssh, doesn't need it ([§5](#no-ssh-commit-inside-push-from-the-vm)).
- [ ] For ddev-context personas: ddev from pkg.ddev.com on the VM (it also provides `mkcert`).
- [ ] About 3 GB free for Docker for the first build: `df -h /var/lib/docker` (config.sh checks it too).
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
/opt/agents/config.sh
```
It checks Docker and your ssh-agent, then asks, in this order:

1. **Personas**, one at a time: flavour, context (the default is `ddev` if the VM has ddev), a name (suggested
   `<flavour>-<context>`: `claude-ddev` on a VM with ddev, `claude-docker` without), whether to show the
   statusline (what `nexus top` reads; Claude Code's or grok's), its projects root: `-` (the default) for the VM's, or a directory of its own, and
   how its ssh (git over ssh) signs in: `agent`, your ssh-agent (the default); `none`, not at all, so you pull
   and push from the VM; `new`, a key made for it whose public half you add as a deploy key; or the path of a
   key you have ([§5](#no-ssh-commit-inside-push-from-the-vm)).
   Personas can share a root. On a re-run it shows the personas and a menu instead: add, change or remove
   one at a time (it asks which), then done; personas you don't pick aren't asked about.
2. **Per VM**, under "VM settings": the projects root (default `~/projects`; every persona without its own
   uses it), the image registry (`local` = build here) and, if a persona is Claude, the Claude Code version (`latest`, `stable` or `X.Y.Z`). For ddev personas, the ddev
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

Nothing is written unless it all checks out: config.sh keeps the new `.env`, `compose.personas.yml` and
`personas/<name>.env` files staged until every question is answered and `docker compose config` accepts them,
then puts them in place together, before it links the launcher and builds. Stopping it earlier (Ctrl-C, a dropped
ssh session, the end of piped input) leaves the setup as it was; only the projects roots (the VM's, and any
persona's own) are created as you name them.

**Expected:** a table of the personas, then `Start one from a project directory: cd …/<project> && nexus <name>`,
plus a line for anything still to do (a missing key or git identity, the mkcert CA, a new ssh key's public
half to add). It has written `.env`, `compose.personas.yml` and `personas/<name>.env` (mode 600), and
`personas/<name>.key` for a persona given a new ssh key.

**If it fails:** the message says what's missing, e.g. Docker access, and nothing has been written: fix the cause
and run config.sh again. A failed launcher link, `mkcert -install` or image build comes after the setup is in
place, so it only adds a closing line with the command to retry (a failed build or pull also makes the exit
status non-zero).

Without questions, e.g. for a script:
```bash
/opt/agents/config.sh --yes --persona claude-ddev:claude:ddev --persona claude-docker:claude:docker \
  --projects-root ~/projects --claude-version 2.1.302
```
Its closing lines list what is still missing, e.g. a grok persona's `XAI_API_KEY`, or the git identity when
`git config` has none; add that to `personas/<name>.env`. `config.sh --help` lists every flag. On a re-run,
`--yes` keeps what `.env` has unless a flag sets it. `--statusline` / `--no-statusline` turn the statusline on or
off for the Claude personas given with `--persona`; without either, a new Claude persona gets it and an existing
one keeps its setting.

**The setup script used to be called `install.sh`;** it is `config.sh` now, with the same flags. On a re-run
it shows the current VM settings and asks only whether to keep them.

**The launcher used to be called `agent`.** After `git pull`, re-run config.sh: it links `nexus` and removes
the old `agent` link into this checkout, which dangles once the script is renamed. Everything else keeps its
name (containers `agent-<persona>`, images, home volumes), so nothing is recreated.

**Upgrading a VM set up with the earlier fixed layout** (the personas were defined in compose.yml, their env
files are already in `personas/`): after `git pull`, the launcher says `no personas set up yet` until you run
config.sh interactively. It offers each `personas/<name>.env` that has no persona, one by one:
`Found personas/<name>.env (flavour <f>): set it up as persona <name>? [Y/n]`. When the file doesn't show its
flavour (a subscription persona has no key line), it asks the flavour; then the context and, for Claude, the
statusline. The env file is reused as it is, and keeping the name keeps the home volume
`agents_<name>-home`, so logins, settings and history carry over. Then `nexus <name> restart` recreates the old
container on the new image. With `--yes` nothing is offered: give the old names, e.g.
`--persona <name>:claude:ddev`.

**Step 3: first start**
```bash
cd ~/projects/billing && nexus claude-ddev       # the name config.sh printed (claude-docker on a VM without ddev)
```
**Expected:** the container is created, and you are attached to tmux session `billing` in that directory. The
status bar shows the persona's name and VM (`claude-ddev @ <vm>`) in its own colour.

**Step 4: log in** (Claude personas set to log in interactively)

Run `claude` and follow the login. With no browser in the container it shows a URL: open it on your
workstation and paste the code back. The login is kept in the persona's home volume, across restarts and
rebuilds.

**Optional: APM's default target.** `apm config set target claude` (`grok-build` in a grok persona), once per
persona; it is kept in the home volume too. Then `apm install` deploys to this persona's tool in any project
whose `apm.yml` has no `targets:` list of its own (section 2).

**Verification**
- [ ] `nexus ls` lists `claude-ddev` as `healthy` (or `starting`, for its first half minute), and your session
      in the sessions table below it.
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
| What is set up and running | `nexus ls`: a table of every persona (flavour, context, ssh, state, projects root), one of the running ones' sessions, and containers no persona has any more; `nexus <persona> ls`: one persona's sessions |
| One prompt, output on stdout (scripts, cron, pipes) | `nexus <persona> headless "PROMPT" [FLAGS…]` (section 3) |
| Live usage, all personas | `nexus top`: CPU and memory per persona; model, context, 5h/7d limits and cost per Claude Code session (section 7). `q` quits, `-n 10` redraws every 10 s, `--once` prints one screen |
| Persona logs | `nexus <persona> logs` (follows; `Ctrl-c` to stop) |
| Start without attaching / stop | `nexus <persona> start` / `nexus <persona> stop` |
| New Claude Code or grok release | `nexus <persona> upgrade` (section 6) |
| Personas set up on this VM | `/opt/agents/config.sh --list` |

Sessions are named after the directory (`.` and `:` become `_`). Two projects with the same directory name
share one session, so give one an explicit name: `nexus claude-ddev api-b`. A persona sees only its projects
root (its own, or the VM's); run from a directory outside it, `nexus` starts the session in the root. When a
session's command exits, tmux moves you to another session instead of dropping you out; `Prefix+d` leaves.

**APM** ([Agent Package Manager](https://microsoft.github.io/apm/)) is in every persona. In a project,
`apm install` installs what its `apm.yml` declares (skills, prompts, agents, MCP servers) and
`apm install <owner>/<repo>` adds a package. Where it deploys, first match wins:

1. `--target`, several with commas: `--target claude,grok-build`.
2. The `targets:` list in the project's `apm.yml`. Every persona working in the project shares it, so it beats
   each persona's own default: a grok persona in a project with `targets: [claude]` deploys to `.claude/`. List
   both for a project both flavours work on. If the project has no `apm.yml` yet, `apm install <pkg> --target …`
   creates one with that list in it; after `apm init` the list starts out commented out.
3. The persona's default from `apm config set target …` (setup step 4).
4. What the project already has: `.claude/` or `CLAUDE.md` (Claude Code), `.grok/` (Grok Build), and other tools'
   markers such as `.github/copilot-instructions.md` (a bare `.github/` doesn't count), `.cursor/`, `.codex/` or
   `GEMINI.md`. With exactly one tool's markers it deploys there, even when that tool isn't this persona's: a
   project with only `.cursor/` gets Cursor's files. With none it stops with `No harness detected`; with two or
   more (`.claude/` and `.grok/`, say, or `CLAUDE.md` and Copilot's instructions) it stops with `Multiple harnesses
   detected: …`. Both stops exit 2.

`apm install -g` deploys to the persona's home (`~/.claude`, `~/.grok`), so to that persona only. Public GitHub
packages need no token; for private ones run `gh auth login` inside the persona, or set `GH_TOKEN` or
`GITHUB_APM_PAT` in its env file. MCP servers set up through APM run with what the image
has: `npx` and `docker` work, `uvx` (Python) doesn't.

**Your team's marketplace.** A marketplace is a git repo holding a catalog of packages, on GitHub, GitLab or a
git server of your own. Register it once per persona; the registration is kept in that persona's home
(`~/.apm/marketplaces.json`):

```bash
apm marketplace add <org>/<repo> --ref v1.0.0 --name team   # pin a tag (#v1.0.0 works only on an https:// URL)
apm marketplace browse team                                 # every package, with its install string
apm search review@team                                      # search names, descriptions and tags
apm install code-review@team                                # add one to this project
```

The install writes the package's real git source into `apm.yml` and pins its commit in `apm.lock.yaml`. Another
persona, or a teammate, then gets it with a plain `apm install`, without registering the marketplace. A private
marketplace, and the repos of the packages in it, need read access from the persona, as private packages do
above: `gh auth login` inside it, or `GH_TOKEN` or `GITHUB_APM_PAT` in its env file. Without a token, APM reads
GitHub-hosted catalogs through GitHub's API on the anonymous limit of 60 requests an hour. Every persona on the VM
shares that limit, and one `marketplace add` uses about four requests. Once the limit is used up, reads fail with
`403 … rate limit exceeded` until the hour resets.

To make a marketplace, run `apm marketplace init` in its repo. It adds a `marketplace:` block, with an example
entry under `packages:`, to that repo's `apm.yml`. Replace the example with your packages; for one in another repo,
use `apm marketplace package add <owner>/<repo> …`. Then `apm pack` writes the catalog
(`.claude-plugin/marketplace.json`), and you commit, tag and push.

## 3. Headless runs

```bash
cd ~/projects/billing
nexus claude-ddev headless "$(cat task.md)" --output-format json --allowedTools "Edit,Bash(ddev *)"
git diff | nexus claude-ddev headless "review this diff" > review.md
nexus grok-docker headless "list the TODOs" --output-format json | jq -r .text
```

`nexus <persona> headless [PROMPT] [FLAGS…]` runs one prompt through the persona's CLI, without tmux, in the
directory you're in. That has to be in the persona's projects root (a path through a symlink is fine); from
anywhere else it refuses, where a session would start in the root. It starts the persona if it isn't running;
that needs an ssh-agent, as `nexus <persona> start` does, unless the persona has an ssh key of its own or no
ssh. The CLI's stdout, stderr and exit code are the command's; nexus's own messages (compose's progress included) go to stderr.

| You give | Claude runs | grok runs |
|----------|-------------|-----------|
| `"PROMPT" FLAGS…` | `claude -p FLAGS… -- "PROMPT"` | `grok --single="PROMPT" FLAGS…` |
| `"PROMPT" FLAGS…`, input piped in | the same; Claude adds the input after the prompt | `grok --prompt-file /dev/stdin FLAGS…`, fed the prompt, a newline, then the input |
| `FLAGS…`, input piped in | `claude -p FLAGS…`: the input is the prompt | `grok --prompt-file /dev/stdin FLAGS…` |

- **The prompt is the first argument**, unless that is a flag (`-x`, `--opt`, or `--opt=value`, whose value may
  have spaces): then all the arguments are flags, and the prompt has to be piped in. Any other text may start
  with `-`, so a task file with `---` front matter works. Don't add `-p` yourself.
- **Input counts** when it comes from a pipe, a file or a socket (`|`, `<`, `<<<`, Node's `child_process`); a
  terminal or `/dev/null` doesn't. It is read to its end before the run starts, so a slow producer loses
  nothing. Next to a prompt, input from a pipe or socket has to start within 3 s (the rule `claude -p` has
  itself): a caller that leaves stdin open costs those 3 s and a warning, and `</dev/null` skips them. With no
  prompt the input is the prompt, and nexus waits for it.
- **Ctrl-C, or a TERM** (e.g. `timeout 30m nexus …`, or the terminal closing), is passed on to the CLI inside
  the persona, to its process group as a terminal would; plain `docker exec` doesn't pass signals on, and the
  CLI would keep working. Daemons the run started in a session of their own keep running. nexus prints
  `nexus: INT: stopping the run in <persona>`, waits for the CLI to stop, and exits 130 (TERM: 143), even though
  `claude -p` itself exits 0 on Ctrl-C.
- No `-t`: a tty would turn the output's line endings into CRLF and mix stderr into stdout.
- Claude reports a run that fails inside, e.g. "Not logged in", on stdout with exit code 1.

`Bash(ddev *)` allows every ddev subcommand, `ddev exec <anything>` included. In a docker-context persona
allow what the project needs instead, e.g. `Bash(docker compose *)`. Docker access is root on the VM, so keep
such runs to repos you trust. A headless run doesn't show in `nexus top` (no statusline in one-shot mode).

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

Re-run `/opt/agents/config.sh`. It shows the personas and a menu: add one, change one (flavour, context,
statusline, projects root, ssh), remove one, or done; for change and remove it asks which. After done it offers
to keep the VM settings as they are. Without questions:
```bash
/opt/agents/config.sh --yes --persona grok-docker:grok:docker            # add, or update flavour/context
/opt/agents/config.sh --yes --persona grok-docker:grok:docker:/srv/work  # with a projects root of its own
/opt/agents/config.sh --yes --persona grok-docker:grok:docker:           # back to the VM's projects root
/opt/agents/config.sh --yes --remove grok-docker                         # drop it from compose.personas.yml
/opt/agents/config.sh --yes --ssh grok-docker:none                      # no ssh: push from the VM (below)
/opt/agents/config.sh --yes --ssh grok-docker:new                       # an ssh key of its own (below)
/opt/agents/config.sh --yes --ssh grok-docker:~/.ssh/grok_deploy        # a key you have
/opt/agents/config.sh --yes --ssh grok-docker:agent                     # back to your ssh-agent
```
- A persona's own projects root is kept in its `# persona:` line in `compose.personas.yml` (`root=`), so it
  can't contain spaces or `:`. Given without `:ROOT`, an existing persona keeps its root. Changing the root
  takes `nexus <persona> restart`; its sessions in the old root end. Until then nexus warns that it still runs
  with the old root: in that root it works as before; elsewhere it only attaches to one of its sessions by name.
- After a change, `nexus <persona> restart` applies it (that ends its tmux sessions); config.sh says so for
  each persona it changed.
- Its build (or pull) covers every persona's image, not only a new one's: adding a grok persona also re-runs the
  claude image's build, which takes seconds from the cache when nothing changed. A running persona keeps the
  image it started with until `nexus <persona> restart`.
- An existing `personas/<name>.env` is kept. When the flavour changes, config.sh offers a new one (with `--yes`
  it writes one) and keeps the old as `.env.bak`. A new persona whose name has a leftover env file is treated the
  same way: one of its flavour is reused, one of the other flavour is replaced (the old one to `.env.bak`).
- `--statusline` / `--no-statusline` with `--persona` turn a persona's statusline on or off; without either, an
  existing persona keeps its setting, and a new one gets it.
- Removing keeps the env file, the ssh key and the home volume; config.sh prints the commands that delete them.
  While that `personas/<name>.env` stays, an interactive run offers to set the persona up again; delete it to
  stop that.

### No ssh: commit inside, push from the VM

By default a persona's ssh, and so git over ssh, uses your ssh-agent, forwarded: your keys never enter the
container, but starting it needs an agent in your shell, and a forwarded one goes stale when you reconnect.
Committing needs no ssh at all, only pull, fetch and push do. So a persona can go without (config.sh: change
it, SSH `none`; or `--ssh <name>:none`, then `nexus <name> restart`):

- The agent commits as usual, as the persona's git identity (`GIT_AUTHOR_*` in `personas/<name>.env`).
- You pull and push on the VM, in the same directory: the projects root is the same path on both sides, so its
  commits are already in your repo, and your own key, passphrase and all, isn't used inside.
- It starts without an ssh-agent. Inside, git over ssh fails at once (`ssh/none.conf` sets `BatchMode`)
  rather than ask for a password. Don't run git on the VM while the agent is mid-commit: both share one
  `.git` (an `index.lock` error). Its commits aren't signed, if you sign yours on the VM.

What `none` doesn't stop: with a `GH_TOKEN` in `personas/<name>.env` it still pushes over https and through
`gh`, so leave that out (or give it a token without write access) if it shouldn't push. And the shared `.git`
is the persona's to write too: its config (`core.pager`, `core.fsmonitor`, `core.sshCommand`, remotes) and
hooks run as you when you run git on the VM. That doesn't widen what an agent can already do with the Docker
socket ([README, rules](README.md#rules-that-keep-it-working)), but it means pushing from the VM is your
check on honest work, not a guard against an agent led astray: then look at `.git/config` and `.git/hooks`
before you run git there.

### An ssh key of its own

Or a persona can have a key of its own, such as a deploy key, and pull and push by itself:

1. Re-run config.sh, change the persona, and answer `new` at "SSH". It makes `personas/<name>.key` (ed25519, no
   passphrase, mode 600, gitignored) and prints its public half. Or answer `path` with a key you already have: it
   must be yours, mode 600 and without a passphrase, since nothing inside could type one.
2. Add the public half on your git host: as a deploy key on each repo the persona works on (with write access if
   it pushes), or to a machine user's account with access to them. `cat personas/<name>.key.pub` shows it again.
3. `nexus <name> restart`. Inside, `ssh -T git@<your git host>` greets the key's account.

The key is mounted read-only at `/run/nexus/ssh-key`, and `ssh/key.conf` makes ssh use it and no other
(`IdentitiesOnly`), without a password prompt if the host refuses it. Your agent isn't mounted: `SSH_AUTH_SOCK`
is empty inside, and the persona starts without one. Anything that runs in the persona can read that key, a
prompt-injected agent included, and through the Docker socket the other personas' keys too, so give each only
the access its persona needs, never your own key; removing it on the git host cuts it off.

`new` never replaces a key that is there. For a fresh one, delete `personas/<name>.key*`, re-run config.sh (it
makes the key and prints it), add it on the git host, and `nexus <name> restart`: a running persona keeps the
key it started with until then. The same goes for a key file you named and then replaced.

### Settings for every persona

Committed files hold the settings every persona of a flavour gets, on every VM that pulls the repo. Each is
either **defaults**, which a persona's own settings override key by key, or **enforced**, which win over them:

| File | For | Kind | How it reaches the persona |
|---|---|---|---|
| `personas/claude.json` | Claude Code: preferences (`model`, effort per model, `theme`, `outputStyle`, `tui`, …) | defaults | merged into the persona's own `~/.claude/settings.json` by nexus, each time it creates or starts the container (below) |
| `personas/claude.managed.json` | Claude Code: policy (`permissions.defaultMode`, `skipDangerousModePermissionPrompt`, `env`) | enforced | mounted read-only as the managed drop-in `/etc/claude-code/managed-settings.d/15-personas.json`, next to the image's `10-agent-image.json` (its update lock) and the statusline's `20-statusline.json` |
| `personas/grok.toml` | grok: everything | defaults | config.sh writes it, with the statusline's `[ui.status_line]` where that's on, to `personas/<name>.grok.toml`, mounted read-only as grok's `/etc/grok/managed_config.toml` |

Claude Code has no layer of its own below a user's settings, hence the merge. When nexus creates or starts a
Claude persona's container, before any Claude runs there, it merges `personas/claude.json` into the persona's
`~/.claude/settings.json`. `~/.claude/.nexus-defaults.json` records the keys nexus owns there. So:
- a key the persona doesn't have, and never took out, gets the default, and nexus owns it;
- a key nexus owns follows a changed default, and goes when it is no longer one. A value Claude Code's own
  migration made of it (a model `opus` becomes `opus[1m]`) still counts as nexus's;
- a key the persona had before, set, changed (`/model`, `/config`) or took out is its own, and stays as it is;
- a `settings.json` that isn't JSON, or can't be written, is left alone, with a warning. A symlinked one is written
  through. nexus says which keys it set.
- To have a default again where the persona had its own value: take the key out of the persona's
  `~/.claude/.nexus-defaults.json` too (or delete that file to apply every default again), then restart it.
- Effort as a default goes per model, `"modelSettings": {"opus": {"effortLevel": "xhigh"}}`: Claude Code 2.1.302
  ignores a top-level `effortLevel` in a user's own settings (managed settings, project ones and `--settings` take it).

grok's managed config is itself a layer of defaults: a key in the persona's `~/.grok/config.toml`, which grok
writes as you change settings there, wins.

| After an edit, or a `git pull` that changes one | Do |
|---|---|
| `personas/claude.json` | `nexus <persona> restart` (the merge runs as the container starts) |
| `personas/claude.managed.json` | `nexus <persona> restart`: it is mounted on its own, so a running container keeps the version it started with |
| `personas/grok.toml` | re-run config.sh: it rewrites `personas/<name>.grok.toml` in place, so the next grok started in a running persona reads it |

- config.sh writes nothing if a file wouldn't parse, since a broken one stops the CLI in every persona it reaches.
  It checks with python3 on the VM (3.11 for TOML) or in a persona image already there; with neither, it warns.
- config.sh checks each file before it writes anything (JSON for Claude; TOML for grok, on its own and with the
  statusline added).
- A persona that had no such mount yet gets it when it is next created: config.sh says `nexus <persona> restart`.
- Without a file, there is nothing to mount or merge. A persona's service that names a mounted one while it's
  missing won't start: nexus says to restore it (`git checkout personas/claude.managed.json`) or re-run config.sh.
- As committed, `personas/claude.managed.json` starts Claude Code in `bypassPermissions` mode without its warning,
  and `personas/grok.toml` sets grok to `always-approve`: every persona runs its tools without asking. Change
  them there if a persona should ask.
- Leave out of `personas/grok.toml` what grok records for itself, such as `[privacy]` and the `[marketplace]`
  install flags: grok keeps those in each persona's own file, and reads `[privacy]` from no other.
- To see what a persona got: inside it, `claude`'s banner shows the model and effort, `~/.claude/settings.json`
  the defaults nexus merged, and `grok inspect` lists "System Managed: /etc/grok/managed_config.toml" and any
  warnings.

## 6. Upgrade Claude Code (or grok, APM, or ddev)

```bash
nexus claude-ddev upgrade            # the newest Claude Code (a grok persona: the newest grok)
nexus claude-ddev upgrade stable     # the newest of a channel: claude latest|stable, grok stable|alpha
nexus claude-ddev upgrade 2.1.290    # an exact version, e.g. to roll back
```

`upgrade` works on the persona's CLI, so one command covers both flavours. The pin is VM-wide, so it
upgrades every persona of that flavour, whichever persona you name:
1. It looks the version up and pins it in `.env` (`CLAUDE_VERSION` or `GROK_VERSION`); the image tags follow
   unless `CLAUDE_TAG` / `GROK_TAG` is set.
2. It builds the image of each context those personas use (only that CLI's installer stage reruns; about a
   minute each), or pulls them on a VM with a registry, and checks each one really has that version. If that
   fails, or you press Ctrl-C, the pin goes back to its old value; the rest of `.env` is left alone.
3. It recreates those personas, after listing their tmux sessions and asking: recreating ends those
   sessions, and the CLI running in them (`claude --resume` picks a conversation back up). `-y` skips the
   question; without a terminal it doesn't recreate, and says what to run when they're idle. A stopped
   persona is removed, and its next start creates it on the new image. If a recreate fails, it says so and
   exits non-zero.
4. It removes the old images, unless a container still uses them.

Run it from a shell with an ssh-agent (recreating a persona that uses yours needs one), and when no agent is
mid-task. One upgrade at a time: it shares config.sh's lock. Check: `docker exec agent-<persona> claude --version`
(`grok --version` in a grok persona). A version exported in your shell beats `.env`, so `upgrade` refuses until you unset it.
Coming from a checkout older than `upgrade`, re-run config.sh once, so the grok image tags follow
`GROK_VERSION` (`upgrade` warns until then).

By hand, the same for Claude: set `CLAUDE_VERSION=X.Y.Z` in `.env` (or `config.sh --yes --claude-version X.Y.Z
--no-build`), `nexus <persona> build` once per context, then `nexus <persona> restart` for every Claude persona.
Left on a channel (`latest`, `stable`), a build is cached once made and needs `nexus <persona> build --no-cache`,
which rebuilds every layer; `upgrade` pins an exact version instead.

- APM is in every image: set `APM_VERSION=X.Y.Z` in `.env` (current:
  `curl -fsSL https://pypi.org/pypi/apm-cli/json | jq -r .info.version`), build one persona of each flavour and
  context, and restart all personas. Only the APM stage reruns. `apm self-update` is off in the image and says
  so. `APM_VERSION` pins `apm-cli` only. Its Python libraries are resolved when the image is built, so two builds of
  one version can differ, and the built image is the real pin.
- ddev: set `DDEV_VERSION` to the VM's `ddev version` (re-running config.sh does), build one persona of each
  ddev-context flavour, and restart all ddev personas.
- Don't run `claude update` or `claude install` inside a persona. The image refuses them ("Updates are disabled
  by your administrator"), because a copy in the home volume would shadow the image's binary.

**Rollback:** `nexus <persona> upgrade <previous version>` (quick while that version is still in the build cache).
By hand: set the previous `CLAUDE_VERSION`, build, then restart the Claude personas.

## 7. Statusline

Claude personas set up with the statusline show `statusline/claude/statusline.sh` as Claude Code's statusline:
model, directory, git branch, context use, cost and rate limits. The directory is mounted read-only, so an edit
(or a `git pull`) shows up at the next refresh, without a restart. `statusline/claude/settings.json` is mounted as
a managed setting, so it wins over a `statusLine` in a persona's own settings; after changing that file,
`nexus <persona> restart`. To turn it on or off for a persona, re-run config.sh and change that persona.

- The line stays blank in a project until you accept Claude Code's workspace trust prompt there (once per
  project per persona).
- The script needs python3 (it has no jq path, whatever its message says). The image has python3 as a
  dependency of NodeSource's nodejs; without it the line reads "Claude Code — statusline needs python3 (or jq)".
- Per Anthropic's docs, if a persona's account belongs to an organisation that pushes server-managed settings,
  Claude Code uses those instead of file-based ones and skips this drop-in. From v2.1.223 the image's update
  lock still applies (managed `env` merges across sources); older pins lose it too.

**grok** personas with the statusline on show `statusline/grok/statusline.sh` as grok's status line: model and
effort, directory, branch, cost and context use. grok sends no plan limits, and a cost only once something in the
session has a price, so the line shows neither until then. It is switched on through grok's managed config,
with the grok settings for every grok persona ([section 5](#settings-for-every-persona)), and grok reads that at
launch: a grok already running keeps what it had.

**`nexus top`** shows those figures for every persona at once. Each time the statusline draws in a tmux pane,
the script also saves them to `~/.cache/nexus/status/<pane>` in the persona's home. `nexus top` reads them
from each running persona: a table of the personas (state, `docker stats` CPU and memory, how many panes run
their CLI), then one with a row per pane running Claude Code (or grok):

```text
PERSONA      STATE      CPU       MEM  PANES
claude-team  healthy  1.69%  610.2MiB  3 claude
claude-max   stopped      -         -  -
grok         healthy  0.02%  300.1MiB  1 grok

PERSONA      SESSION  MODEL         CONTEXT   5H   7D   COST  SEEN
claude-team  api      Opus 5.5    112k/200k  41%  18%  $3.20   12s
claude-team  web:1.1  Sonnet 5.5   38k/200k  41%  18%  $0.85    3m
claude-team  web:1.2  Sonnet 5.5   21k/200k  41%  18%  $0.40    9m
grok         api      Grok 4.7     64k/500k    -    -  $0.42    1m
```

- **CONTEXT** is used/size, coloured as on the statusline (yellow from 50%, red from 80%). **5H** and **7D** are
  the plan's limits used: per account, so personas on one account show the same. They are `-` with an API key.
- **COST** is the session's at API prices, as Claude Code reports it; on a plan it isn't a bill.
- **SEEN** is the time since the CLI last drew the statusline. It draws as the session works, so an idle
  session's figures are as old as SEEN says.
- A session with two CLI panes shows each as `session:window.pane`. A pane where the CLI has quit drops out; so
  does a closed one.
- No figures (`-`) for a persona with the statusline off, and for a session that hasn't drawn one yet (e.g.
  before Claude Code's trust prompt is accepted, or while grok shows its welcome screen). grok rows never have
  5H or 7D, and have COST once grok reports one.
- A frame takes about 2 s, most of it `docker stats`. Whatever a persona writes is only displayed: numbers are
  checked, control characters dropped.

## 8. Reset a persona's home

```bash
nexus claude-ddev stop
docker rm agent-claude-ddev
docker volume rm agents_claude-ddev-home     # deletes its login, settings, memory and history
```
The next `nexus claude-ddev` starts it fresh, seeded from the image.

## 9. Test a change to nexus or config.sh

`tests/run.sh` runs config.sh and nexus end to end in a scratch copy of the checkout, your uncommitted changes
included, on throwaway personas. Nothing of your setup is read or changed, and what it makes is removed at the end.
```bash
/opt/agents/tests/run.sh                  # about two minutes: ok or FAIL per check, a summary, exit 1 on a failure
/opt/agents/tests/run.sh --no-containers  # seconds: lint, config.sh and nexus without starting a persona
```
- Starting personas needs a Claude persona image built for your uid (the newest `agent-claude-docker` here, or
  `--image REF`), `ssh-agent` and `script`; a stage it can't run is skipped, saying why. There is no real sign-in:
  Claude Code talks to a stand-in API (`tests/fake-api.py`) and grok is a stand-in (`tests/fake-grok`), so it tests
  nexus and config.sh, not a CLI release.
- It lints with shellcheck and hadolint if they are installed or their images are here; it never pulls them.
- Its personas are `nxt-<pid>-c` and `nxt-<pid>-g`, in compose project `agents`, so compose warns that your real
  personas are orphans; that is only a warning. `--keep` leaves it all for a look after a failure, and says what.

### CI: the secret scan

On GitHub, every push and pull request runs `.github/workflows/ci.yml`. It runs `tests/secrets.sh`, which uses
gitleaks to look for keys, tokens and passwords in two places:

- **The git history:** what every commit on a branch or tag adds, files deleted since and binary ones included. It
  also covers what a two-parent merge itself adds, such as a conflict resolution, and the commit and tag messages.
  On GitHub that means the branches and tags pushed there; on the VM, local and remote-tracking branches too.
  Stashes stay on the VM and aren't scanned.
- **The files in the checkout:** the tracked ones as they are now, and untracked ones git doesn't ignore. A staged
  version that differs from the file on disk is scanned once it is committed.

What git ignores (`.env`, `personas/*.env`, `personas/*.key`) never goes into the scan. A finding shows its rule,
file, line, commit and fingerprint, never the secret or the text around it. A finding in a message shows as
`../msgs/commit-<sha>.txt` or `../msgs/tag-<sha>.txt`. One in a merge's conflict resolution shows its file as
`b/<path>`.

CI scans after the push, so anything it finds is already on GitHub. Run the same scan yourself before you push.
Inside a persona, run it from a clone of this repo under that persona's projects root, because `/opt/agents`
isn't mounted there. `tests/run.sh` runs it too, once the image is here:

```bash
/opt/agents/tests/secrets.sh                                  # exit 0 nothing found, 1 found something (listed), 2 couldn't scan
ln -s ../../tests/secrets.sh /opt/agents/.git/hooks/pre-push   # optional: run it on every git push from this checkout
```

The hook is a convenience, not a guard: anything that can write `.git/hooks` can remove it. The scan pulls its
gitleaks image the first time. It stops with exit 2, having scanned nothing, in these cases:

- **A history it can't read:** a shallow clone (`git fetch --unshallow`), missing objects, or alternates that moved.
- **Rules that miss a fake token it plants first:** a `.gitleaks.toml` without `[extend] useDefault = true`, or one
  whose `disabledRules` or allowlists cover `ghp_` tokens.

What to do with a finding, and how to keep the scan up to date:

- **A real secret:** revoke or rotate it first. A push can't be taken back: the commit stays in every clone and in
  pull requests, and on GitHub it stays reachable by its SHA until GitHub Support removes it. Take the secret out
  of the files (into `.env`, say) and commit that. Then do one of two things, or every scan keeps failing on the
  old commit:
  - add the history finding's fingerprint (`<commit>:<file>:<rule>:<line>`) as a line of `.gitleaksignore` and
    commit it;
  - or remove the secret from the history: `git filter-repo` on every branch and tag, then a force-push that
    everyone has to follow.
- **A false positive:** before you commit the line, put `gitleaks:allow` in a comment on it. After the commit that
  no longer helps, because the history scan reads the commit that added the line. Instead, add the fingerprints
  the scan printed, as printed, to `.gitleaksignore` and commit it. Careful with the one without a commit
  (`<file>:<rule>:<line>`): it hides that rule on that line in every commit, later edits included. For a value
  that stays in the files, allow that exact value in a `.gitleaks.toml` instead:

  ```toml
  [extend]
  useDefault = true          # keep gitleaks' own rules: without this line there are none

  [[allowlists]]
  description = "the fixture token in tests/"
  regexes = ['''^ghp_the-exact-value$''']
  ```
- **Upgrades:** gitleaks is pinned in `tests/secrets.sh` (`GITLEAKS_IMAGE`, a tag and its digest), and
  `actions/checkout` in `ci.yml` (a commit). Bump them by hand. The release notes are at
  github.com/gitleaks/gitleaks and github.com/actions/checkout.
- **Blocking merges:** to stop a pull request with a finding from being merged, make **Secret scan** a required
  check in the repo's branch rules. On a private repo that needs a paid GitHub plan.

## Troubleshooting

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| `no personas set up yet: run …/config.sh` | fresh checkout | run config.sh |
| `no persona '<name>' (set up: …)` | typo, or not added yet | use a listed name, or add it with config.sh |
| `required variable SSH_AUTH_SOCK is missing a value` | no ssh-agent found | start one / `ssh -A`, `ssh-add`, then retry |
| `warning: the ssh-agent at … holds no keys` | agent without your key (often gpg-agent's) | `ssh-add`, then `nexus <persona> restart` |
| `warning: nothing answers at … (stale ssh-agent socket)` | the agent behind that socket is gone | reconnect with `ssh -A`, then `nexus <persona> restart` |
| git over ssh fails inside after you reconnected | the container still has the old login's socket | `nexus <persona> restart` |
| `git pull` inside asks for a password, or says `Permission denied (publickey)` | your agent: gone or empty; a key of its own: not added on the git host | inside, `ssh-add -l`: "Could not open a connection" → `nexus <persona> restart` from a shell where `ssh-add -l` works on the VM; "no identities" → `ssh-add` on the VM. A key of its own: add `personas/<name>.key.pub` on the host ([§5](#an-ssh-key-of-its-own)) |
| `<persona>'s ssh key … isn't there` | its key file was moved or deleted | put it back, or re-run config.sh: SSH `new` makes a new one (add it on the git host again), `none` or `agent` drop the key |
| `Permission denied (publickey)` at once, in a persona without ssh | that is what `none` does | pull and push from the VM ([§5](#no-ssh-commit-inside-push-from-the-vm)), or give it a key |
| config.sh: `--ssh …: others can read it`, `… has a passphrase`, `… isn't yours` | ssh inside wouldn't use that key | `chmod 600` it; a passphrase: `ssh-keygen -p -N "" -f KEY` drops it (or use `new`); someone else's: copy it as yours |
| Persona not running after a VM reboot | its ssh-agent socket didn't exist at boot | `nexus <persona>` (or `nexus <persona> start`) |
| `the input device is not a TTY` | `nexus <persona>` attaches; a script has no tty | `nexus <persona> start` |
| `<persona> isn't running` | `nexus <persona> ls` on a stopped persona | `nexus <persona>` or `nexus <persona> start` |
| `env file …/personas/<persona>.env not found` | deleted, or the persona was added by hand | re-run config.sh (it writes a new one) |
| `the projects root must be an absolute path` / `can't create the projects root …` | a bad `PROJECTS_ROOT` in `.env` or your shell, or a bad own root | the message says which: fix it, or re-run config.sh |
| `… is outside <persona>'s projects root; starting in …` | you ran `nexus` from a directory that persona doesn't mount | `cd` into its root, or give the persona that root (section 5) |
| `<persona> still runs with the projects root it was created with, …` | its root changed (config.sh, or `PROJECTS_ROOT`) while it ran: a running container keeps the root it was created with | `nexus <persona> restart` (ends its sessions); to finish one first, `nexus <persona> <session>` attaches to it |
| `OCI runtime exec failed: … chdir to cwd …: no such file or directory` | an older nexus, attaching to a persona whose root changed while it ran | `nexus <persona> restart`; `git pull` brings the nexus that says so |
| `no Docker socket at /var/run/docker.sock` | Docker isn't installed or running | install / start Docker |
| `permission denied … docker.sock` | your user isn't in the `docker` group | `sudo usermod -aG docker "$USER"`, log in again |
| `range of CPUs is from 0.01 to N` | `AGENT_CPUS` above the VM's vCPUs | set `AGENT_CPUS` ≤ `nproc` (config.sh caps it) |
| `pull access denied for local/agent-…` | `REGISTRY=local` means "build here" | `nexus <persona> build`, or set `REGISTRY` |
| `At least one invalid signature was encountered` (apt, during a build) | Docker's disk is full | `docker system df`; free space with `docker builder prune` / `docker image prune`, then build again |
| After `git pull`: `install.sh: No such file or directory` | the setup script was renamed `install.sh` → `config.sh` | run `config.sh` (same flags) |
| After `git pull`: `nexus: command not found`, or `…/agent: No such file or directory` | the launcher was renamed `agent` → `nexus` | re-run config.sh (it links `nexus`), or run `<checkout>/nexus` |
| New Claude version not showing after a build | container not recreated, or a cached channel build | `nexus <persona> restart`; or `nexus <persona> upgrade <that version>`, which pins, builds and recreates (plain `upgrade` moves to the newest) |
| `config.sh or another upgrade is running in …` (nexus), `another config.sh, or a … upgrade, is running in …` (config.sh) | a config.sh or `nexus <persona> upgrade` is still running in that checkout; they share one lock | let it finish, then run it again |
| `CLAUDE_VERSION is exported in this shell and beats …/.env` (or `GROK_VERSION`) | `CLAUDE_VERSION` or `GROK_VERSION` exported in your shell | `unset CLAUDE_VERSION` (or `GROK_VERSION`), then upgrade |
| `Updates are disabled by your administrator` | expected: the image is the updater | upgrade via section 6 |
| `claude doctor`: `~/.local/bin is not in your PATH` | expected with the image's layout | nothing to do |
| `The agent image is the APM updater: …` | `apm self-update` is off in the image | upgrade via section 6 |
| `apm install`: `No harness detected` (exit 2) | no `targets:` in the project's `apm.yml`, no default for the persona, and no `.claude/`, `CLAUDE.md`, `.grok/` or Copilot files yet | `--target claude` (or `grok-build`), a `targets:` list in `apm.yml`, or a default for the persona (setup step 4) |
| `apm install`: `Multiple harnesses detected: …` (exit 2) | markers of two or more tools in the project (`.claude/` and `.grok/`, say, or `CLAUDE.md` and `.github/copilot-instructions.md`), and neither a `targets:` list nor a default for the persona | `targets: [claude, grok-build]` in `apm.yml`, `--target`, or a default for the persona (setup step 4) |
| A grok persona's `apm install` writes `.claude/`, not `.grok/` | the project's `apm.yml` has `targets: [claude]`, which beats the persona's default | add `grok-build` to its `targets:` (section 2) |
| `apm install …@<marketplace>` or `apm marketplace add` fails with `403 … rate limit exceeded` | a GitHub-hosted marketplace read without a token: 60 requests an hour, shared by every persona on the VM | `gh auth login` inside the persona; or `GH_TOKEN` or `GITHUB_APM_PAT` in its env file, then `nexus <persona> restart` |
| `Permission denied` under `~/.claude` after a uid change | the home volume was seeded for the old uid | reset the persona's home (section 8) |
| `warning: no mkcert CA on this VM yet` | ddev context, `mkcert -install` not run | `mkcert -install` on the VM, then `nexus <persona> restart` |
| `https://*.ddev.site` untrusted in Claude or node | the container predates the CA | `nexus <persona> restart` |
| `curl: (60) SSL certificate problem` for `*.ddev.site` | curl uses the system store, not the mkcert CA | `curl --cacert "$NODE_EXTRA_CA_CERTS" …` |
| `-- cmd` didn't run | the session already existed | use a new session name |
| ddev rebuilds its web image on every start | ddev run alternately from the VM and the persona | run ddev only inside the persona |
| `ddev: command not found` inside | a docker-context persona | change it to context `ddev` with config.sh |
| `nexus: …compose.personas.yml is from before statusline/ was split into claude/ and grok/` | a personas file written before `git pull` brought that change | `./config.sh --yes` (keeps every setting), then `nexus <persona> restart` for each persona; running Claude sessions show no statusline until then |
| config.sh: `personas/grok.toml isn't TOML grok can read` | a typo in your grok settings (the message names line and column) | fix it and re-run; nothing was written |
| config.sh: `personas/claude.json isn't JSON Claude Code can read` (or `claude.managed.json`) | a typo in the Claude settings (the message names line and column) | fix it and re-run; nothing was written |
| A change to `personas/claude.json` or `claude.managed.json` doesn't show in a persona | the defaults merge as the container starts, and the managed file is mounted on its own | `nexus <persona> restart` |
| A default from `personas/claude.json` doesn't reach a persona | the persona has its own value for that key in `~/.claude/settings.json` (set there, by `/config`, or there before), or took the key out | set it there; or take it out of `~/.claude/.nexus-defaults.json` too (or delete that file), then `nexus <persona> restart`, and the default comes back |
| `nexus: …compose.personas.yml is from before personas/claude.json was split into defaults and claude.managed.json` | a personas file written between the two changes, which mounts the defaults as the enforced drop-in | `./config.sh --yes`, then `nexus <persona> restart` for each Claude persona |
| `nexus: warning: <persona>: its ~/.claude/settings.json is not valid JSON` | that file is broken | fix it inside the persona; nexus merges the defaults at the next restart |
| grok's status line row stays empty | grok shows its welcome screen (the row comes with the first prompt), statusline off, or the persona's own `~/.grok/config.toml` sets `[ui.status_line]` | start a session; turn it on with config.sh; or remove that section there |
| No statusline in Claude | workspace not trusted yet, or statusline off for this persona | accept the trust prompt (section 7); else turn it on with config.sh and restart |
| `nexus: headless: give the prompt first …` | no prompt, and nothing piped in (or the first argument is a one-word flag) | `nexus <persona> headless "PROMPT" [FLAGS…]`, or pipe the prompt in |
| `nexus: warning: headless: no input on stdin within 3 s …` | the caller (a CI runner, an agent's shell) left stdin open with nothing in it | give `</dev/null`; or, if input was meant, start it sooner |
| A headless run with no prompt never starts | it waits for its input to end, and the pipe stays open | close the pipe, or give the prompt as the first argument |
| `nexus: headless: … is outside <persona>'s projects root` | headless runs only in a project of that persona | `cd` to a directory in its root (`config.sh --list` shows the roots) |
| A headless run prints `Not logged in · Please run /login` | the persona isn't signed in | `nexus <persona> -- claude`, then `/login` (or set the key in its env file) |
| `nexus top` shows a Claude Code session with `-` everywhere | statusline off for that persona, or it hasn't drawn yet in that pane | turn it on with config.sh (section 7); else work in the session once |
| `nexus top` lists no session for a running persona | no pane runs its CLI (`no claude running`) | start one: `nexus <persona> <session> -- claude` |
| config.sh: `'top' is not a usable name` | `ls` and `top` are nexus commands, and `all` and `start` are reserved too | pick another name |

## Environment variables

Precedence: a variable exported in your shell beats `.env`, except that a persona's own projects root beats
both. `.env` feeds compose, and the launcher reads `PROJECTS_ROOT`, `REGISTRY` and the version pins from it
(`upgrade` writes the pins); the persona env files go into the container's environment.

### Per VM — `/opt/agents/.env` (written by config.sh)

| Variable | Required | Default | What it does |
|----------|----------|---------|--------------|
| `PROJECTS_ROOT` | no | `$HOME/projects` | The VM's projects root, mounted at the same path inside every persona that has no root of its own (config.sh) |
| `REGISTRY` | no | `local` | Image name prefix. `local` = build on this VM; a registry host enables `nexus <persona> pull` |
| `CLAUDE_VERSION` | no | `latest` | Claude Code build: `latest`, `stable` or `X.Y.Z`. Pins below 2.1.118 ignore the update lock |
| `CLAUDE_TAG` | no | `CLAUDE_VERSION` | Tag of the Claude images |
| `APM_VERSION` | no | `latest` | APM in every image: `latest` or `X.Y.Z` (PyPI's `apm-cli`, not its Python libraries); `latest` is cached once built |
| `GROK_VERSION` | no | `latest` | Grok Build build: `latest` (newest on xAI's stable channel) or `X.Y.Z`; `upgrade` pins it |
| `GROK_TAG` | no | `GROK_VERSION` | Tag of the grok images |
| `DDEV_VERSION` | no | latest in the apt repo | ddev in ddev-context images; the VM's `ddev version` (leading `v` optional) |
| `AGENT_CPUS` | no | `4`, capped at `nproc` by config.sh | CPU limit per persona; must not exceed `nproc` |
| `AGENT_MEMORY` | no | `8g` | Memory limit per persona |

### Derived by the launcher — don't set them

| Variable | Value | Override only if… |
|----------|-------|-------------------|
| `AGENT_UID` / `AGENT_GID` | `id -u` / `id -g` | you build for another uid; export it in the shell |
| `DOCKER_GID` | gid of `/var/run/docker.sock` | never |
| `SSH_AUTH_SOCK` | your shell's agent, else `/run/user/<uid>/ssh-agent.sock`, `$XDG_RUNTIME_DIR/openssh_agent`, gnome-keyring, gpg-agent; unused by a persona with an ssh key of its own or no ssh | you want a specific agent |
| `AGENT_CA_CERTS` | ddev context: `~/.local/share/mkcert/rootCA.pem` if it exists, else empty | never |
| `AGENTS_DIR` | the directory the `nexus` script lives in | the repo is somewhere unusual |
| `HOME` | your VM home; ddev context mounts `~/.ddev` and the mkcert CA from there | never |

Without the launcher, export them yourself and use both compose files, e.g. from /opt/agents:
`AGENT_UID=$(id -u) AGENT_GID=$(id -g) DOCKER_GID=$(stat -c %g /var/run/docker.sock) PROJECTS_ROOT=$HOME/projects docker compose -p agents -f compose.yml -f compose.personas.yml --profile <persona> up -d`
(`PROJECTS_ROOT` = the persona's own root if its `# persona:` line has `root=`, else the VM's; with
`SSH_AUTH_SOCK` from your shell; `-p agents` keeps an exported `COMPOSE_PROJECT_NAME` from switching to fresh
home volumes). For a ddev persona, first `mkdir -p ~/.ddev ~/.local/share/mkcert` (or Docker creates them
root-owned), and add `AGENT_CA_CERTS=` (empty) if the VM has no mkcert CA.

### Per persona — `personas/<persona>.env` (written by config.sh from `personas/<flavour>.env.example`)

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
| `PROJECTS_ROOT` | the persona's projects root: its own, or the VM's |
| `SSH_AUTH_SOCK` | `/ssh-agent` (your agent's socket, mounted); empty in a persona with an ssh key of its own (the key: `/run/nexus/ssh-key`) or no ssh |
| `TERM` / `COLORTERM` / `LANG` | `xterm-256color` (tmux sets its own) / `truecolor` / `C.UTF-8` |
| (timezone) | the VM's: `/etc/localtime` is mounted, so clocks and commit times match it |
| `AGENT_PERSONA`, `AGENT_COLOUR` | persona name and status-bar colour |
| `DISABLE_AUTOUPDATER`, `GROK_DISABLE_AUTOUPDATER` | `1`; the Claude image also sets `DISABLE_UPDATES` and `DISABLE_INSTALLATION_CHECKS` as managed settings |
| `CAROOT`, `NODE_EXTRA_CA_CERTS` | ddev context only: the VM's mkcert CA dir and file, so ddev, node and Claude trust `https://*.ddev.site` (`NODE_EXTRA_CA_CERTS` is empty when the launcher finds no CA) |

### Build: targets and arguments

compose.personas.yml sets only the target (`claude` or `grok`, the flavour) and `CONTEXT` for each persona;
`CLAUDE_VERSION`, `GROK_VERSION`, `APM_VERSION`, `DDEV_VERSION`, `UID` and `GID` come from compose.yml's `_agent` template, which takes them from
`.env` and the launcher. The Default column is what a plain `docker build` uses; with no `--target` you get
`claude`.

| Arg | Used by | Default |
|-----|---------|---------|
| `CONTEXT` | every target: `docker` or `ddev` | `docker` |
| `CLAUDE_VERSION` | the `claude` target | `latest` |
| `GROK_VERSION` | the `grok` target | `latest` |
| `APM_VERSION` | every target | `latest` |
| `DDEV_VERSION` | `CONTEXT=ddev` | empty (latest) |
| `UID` / `GID` | every target | `1000` / `1000` |
| `NODE_MAJOR` | every target (Dockerfile only) | `22` |
| `DEBIAN_FRONTEND` | apt steps (Dockerfile only) | `noninteractive` |

Images are named `${REGISTRY}/agent-<flavour>-<context>`, so personas with the same flavour and context share
one image.
