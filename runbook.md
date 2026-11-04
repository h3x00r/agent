# Runbook — persona containers

How to set up, use and maintain the agent personas on a dev VM, from the user's side, plus every
environment variable involved. The design and its rules are in [README.md](README.md). New to Nexus? Start with
[EXAMPLES.md](EXAMPLES.md): the same tasks in plain words, with examples.

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
- [ ] git: `git --version` (if it's missing: `sudo apt-get install git`).
- [ ] Python 3.8 or newer: `python3 --version` (Ubuntu 20.04 has 3.8). nexus, and so `nexus --config`, runs on it, with its
      standard library only: nothing else to install.
- [ ] About 3 GB free for Docker for the first build: `df -h /var/lib/docker` (`nexus --config` checks it too).
- [ ] sudo, once: `nexus --config` creates the shared scratch root `/srv/nexus-scratch` with it, since `/srv`
      is root's ([§1](#1-first-time-setup-on-a-vm)). A root in a directory of yours needs none.
- [ ] Credentials: for Claude an Anthropic API key, a `claude setup-token` token or a Claude subscription to
      log in with; for grok an xAI API key.
- [ ] Network for building: Docker Hub (the `debian:bookworm-slim` base), the Debian, Docker, NodeSource and
      GitHub package hosts, pkg.ddev.com (ddev context), plus claude.ai and downloads.claude.ai (Claude Code),
      x.ai (grok), pypi.org and files.pythonhosted.org (APM), github.com and its release downloads
      (release-assets.githubusercontent.com, where uv's release redirects; older releases may still go to
      objects.githubusercontent.com), and gitlab.com (glab). `nexus-apm` reaches PyPI from inside a persona
      too, the first time it runs a version ([§6](#6-upgrade-claude-code-or-grok-apm-or-ddev)).

## 1. First-time setup on a VM

**Step 1: install**
```bash
curl -fsSL https://nexus.light-and-dark.io/install.sh | bash
```
Run it as yourself, not with sudo. The address redirects to `install.sh` on the default branch
(`raw.githubusercontent.com/h3x00r/agent/HEAD/install.sh`). It clones `https://github.com/h3x00r/agent.git` to
`/opt/agents`, owned by you; it uses sudo only to create that directory when `/opt` isn't yours. It then runs
step 2's `nexus --config`, which reads its answers from your terminal even though the script came through a
pipe. Whatever follows `bash -s --` goes to `nexus --config`, for example
`curl … | bash -s -- --yes --persona claude-docker:claude:docker`. With no terminal (a script, CI) and nothing to
pass on, it stops after the clone and says what to run next.
- `AGENTS_DIR=/srv/agents` before `bash` clones there instead; `AGENTS_REPO` clones a fork.
- A directory there already that isn't a clone of this repo is refused, and nothing in it changes.
- To read the script first: `curl -fsSL https://nexus.light-and-dark.io/install.sh -o install.sh`,
  `less install.sh`, then `bash install.sh`.
- Without it:
  `sudo git clone https://github.com/h3x00r/agent.git /opt/agents && sudo chown -R "$USER" /opt/agents`, then
  step 2.

**Expected:** `install: cloning … into /opt/agents`, `install: /opt/agents is at <commit> (<date>)`, then step 2's
questions.

**Step 2: run the setup**
```bash
/opt/agents/nexus --config
```
The installer runs this for you. Run it yourself after a manual clone, or whenever you want to change something.
`nexus` isn't on your PATH yet: the setup links it. It checks Docker and your ssh-agent, then, the first time
(no `.env` yet), asks in this order, saving each part as soon as it is answered:

1. **The settings every persona shares**, asked once (`shared` in the menu changes them later): the projects
   root (default `~/projects`; every persona without its own uses it), the image registry (`local` = build
   here) and the Claude Code version (`latest`, `stable` or `X.Y.Z`). For ddev personas, the ddev version is
   taken from the VM's `ddev version`.
2. **Where to link the launcher**: a directory (default `/usr/local/bin`, giving `/usr/local/bin/nexus`; via sudo
   if needed; `none` skips it).
3. **The first persona**: flavour, context (the default is `ddev` if the VM has ddev), a name (suggested
   `<flavour>-<context>`: `claude-ddev` on a VM with ddev, `claude-docker` without), whether to show the
   statusline (what `nexus top` reads; Claude Code's or grok's), its projects root: `-` (the default) for the
   VM's, or a directory of its own, and how its ssh (git over ssh) signs in: `agent`, your ssh-agent (the
   default); `none`, not at all, so you pull and push from the VM; `new`, a key made for it whose public half you
   add as a deploy key; or the path of a key you have ([§5](#no-ssh-commit-inside-push-from-the-vm)). Personas
   can share a root. A Claude persona is then asked `Let <name> run its tools without asking (bypassPermissions;
   …)? [Y/n]`: Enter leaves it in `bypassPermissions`, as before; `n` makes it ask first
   ([§5](#asking-before-running-tools)). Then its sign-in and identity, unless a `personas/<name>.env` of its
   flavour is there already (it is reused):
   - Claude: log in interactively later, an API key, or a `claude setup-token` token.
   - grok: its xAI key.
   - Then the git author name and email (defaults from your `git config`) and an optional `GH_TOKEN`.
   Secrets are read without echo. Then whether to build its image now (or pull it, with a registry), and, for a
   ddev persona on a VM without the CA yet, whether to run `mkcert -install`.

Then, and every time you run it after, it shows what is set up (the shared settings, where `nexus` is linked,
the personas, with a line `asks before running tools (not bypassPermissions): <name>, …` when one does, and any
`personas/<name>.env` that belongs to no persona) and a menu. A run after the first
brings what it generates up to date before that, as a re-run of the setup always did: each grok persona's config after
an edit of `personas/grok.toml`, `compose.personas.yml`, a deleted ssh key of its own, `.env`'s `DDEV_VERSION`.
It says which files it wrote (`-- brought up to date: …`), if any.
```text
What next?
  1) list      the shared settings and the personas, again
  2) add       a new persona
  3) change    one persona's flavour, context, statusline, projects root, ssh, permissions or telemetry
  4) remove    one persona (its env file and home volume stay)
  5) shared    the settings every persona shares: projects root, image registry, Claude Code version
  6) telemetry send OpenTelemetry to a collector: its address, token and CA, and which personas send
  7) launcher  link nexus into a directory on your PATH
  8) images    build (or pull) the image of every persona
  9) quit
choice [quit]:
```
Answer with the number or the word; Enter takes the default (`quit`, or `add` while there is no persona). Each
action asks only what it needs ([section 5](#5-add-change-or-remove-a-persona)).

**Each action is saved when it is done**, and only once it checks out: its `.env`, `personas.json`,
`compose.personas.yml` and `personas/<name>.env` stay staged until `docker compose config` accepts them, then
go in place together. Stopping in the middle of one (Ctrl-C, a dropped ssh session, the end of piped input)
leaves that action unsaved and keeps those before it. One the check refuses is said, nothing of it is written,
and the menu comes back. Only the projects roots (the VM's, and any persona's own) are created as you name them,
and the shared scratch root, below.

**The shared scratch root.** Once there is a persona, `nexus --config` makes sure after it saves (once a run) that
the shared scratch root is there:
`/srv/nexus-scratch`, unless `NEXUS_SCRATCH_ROOT` in `.env` (or your shell) names another. Every persona gets a
directory of its own in it, at the same path inside the persona as on the VM, for files a container it starts has
to mount ([§4](#bind-mounts-and-the-scratch-dir)). When the root's parent is yours, it is a plain `mkdir`. `/srv`
is root's, so it says `-- creating the shared scratch root /srv/nexus-scratch needs sudo` and runs
`sudo install -d -o <your uid> -g <your gid> -m 755 /srv/nexus-scratch`, which asks for your password. With
`--yes` it runs `sudo -n`, which never asks: without passwordless sudo that fails. Either way, a failure is a closing
line with the command, to run once yourself:
```bash
sudo install -d -o $(id -u) -g $(id -g) -m 755 /srv/nexus-scratch
```
Until the root is there, nexus starts, restarts and upgrades no persona: it refuses, with that command and the other
way out (`NEXUS_SCRATCH_ROOT` at a directory of your own). An attach or a headless run to a persona that runs
already only warns, with the same command, and goes on: its container has its scratch dir mounted. A root that is
there is never touched again: one that isn't a directory (a file in its place) gets a closing line saying to move
it aside, and one you can't write in, while a persona's dir is still to be made in it, one with the
`sudo install -d …` that makes it yours; `nexus --config` runs sudo for neither. For another place, set
`NEXUS_SCRATCH_ROOT=/data/nexus-scratch` (an absolute directory other than `/`, without `:` or whitespace) in `.env`,
then run `nexus --config` again.

**Expected:** after each action `-- saved: …`, and a line for anything still to do (a missing key or git
identity, a restart, the mkcert CA, a new ssh key's public half to add, the scratch root's `sudo install`). At `quit`:
`Start one from a project directory: cd …/<project> && nexus <name>`. It has written `.env`, `personas.json`, the
`compose.personas.yml` generated from it, `personas/<name>.env` (mode 600) and `personas/<name>.notes.md`, the
Nexus notes its CLI reads ([§5](#the-nexus-notes)), and `personas/<name>.key` for a persona given a new ssh key.

**If it fails:** the message says what's missing, e.g. Docker access, and nothing of that step has been written:
fix the cause and run it again. A failed launcher link, `mkcert -install` or image build comes after the save, so
it only adds a line with the command to retry (a failed build or pull also makes the exit status non-zero).

Without questions, e.g. for a script:
```bash
/opt/agents/nexus --config --yes --persona claude-ddev:claude:ddev --persona claude-docker:claude:docker \
  --projects-root ~/projects --claude-version 2.1.302
```
That does it all in one go: the settings and every persona saved together, then the launcher link and the
images (`mkcert -install` is left to you; its closing lines say so).
Its closing lines list what is still missing, e.g. a grok persona's `XAI_API_KEY`, or the git identity when
`git config` has none; add that to `personas/<name>.env`. `nexus --config --help` lists every flag. On a re-run,
`--yes` keeps what `.env` has unless a flag sets it. `--statusline` / `--no-statusline` turn the statusline on or
off for the personas given with `--persona` (Claude or grok); without either, a new persona gets it and an
existing one keeps its setting. Given without `--yes`, the options are saved first, and the menu follows.

**The setup tool used to be `install.sh`, then `config.sh`, then `config-tools`;** it is `nexus --config` now,
with the same flags. Today's `install.sh` is a different script: the one-line install of step 1. In a checkout
you already have, it pulls, then runs `nexus --config`. Coming from `config.sh`, run
`/opt/agents/nexus --config --yes --no-build` once, right after
the `git pull`: it moves the personas into `personas.json` ([section 10](#10-update-a-vm)).

**The launcher used to be called `agent`.** After `git pull`, run `/opt/agents/nexus --config --yes --no-build`
by its path (`nexus` isn't on your PATH yet), or `/opt/agents/nexus --config` and choose `launcher`: it links `nexus` and removes the old `agent` link into this checkout, which dangles once
the script is renamed. Everything else keeps its name (containers `agent-<persona>`, images, home volumes), so
nothing is recreated.

**Upgrading a VM set up with the earlier fixed layout** (the personas were defined in compose.yml, their env
files are already in `personas/`): after `git pull`, the launcher says `no personas set up yet` until you add
them. `nexus --config` lists each `personas/<name>.env` that has no persona, with its flavour where the file
shows it (`(a grok one)`); add a persona of that name and that flavour: it asks the usual questions, then reuses
the env file as it is instead of asking the sign-in (a file of the other flavour is replaced, the old one kept as
a `.bak`). Keeping
the name keeps the home volume `agents_<name>-home`, so logins, settings and history carry over. Then
`nexus <name> restart` recreates the old container on the new image. With `--yes`, give the old names, e.g.
`--persona <name>:claude:ddev`.

**Step 3: first start**
```bash
cd ~/projects/billing && nexus claude-ddev       # the name nexus --config printed (claude-docker on a VM without ddev)
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
- [ ] Inside: `echo $NEXUS_SCRATCH $NEXUS_PORTS` prints its scratch dir and its port range, and
      `ls -l /run/nexus/docker.sock` shows a socket (`srwx…`): the Docker guard ([§4](#the-docker-guard)). On the VM,
      `docker logs agent-claude-ddev | grep 'docker guard'` says `docker guard on: …`.
- [ ] ddev context: `ddev version` inside matches the VM's.

## 2. Daily use

| Task | How |
|------|-----|
| Open (or reattach to) a project | `cd ~/projects/<project> && nexus <persona>` |
| Start Claude Code | `claude` in the session; `claude --resume` to continue an earlier conversation |
| New session straight into Claude | `nexus <persona> <name> -- claude` (the command only runs when the session is created) |
| A session that asks before running tools | `nexus <persona> --skip-bypass-permissions [session] [-- claude]` (below) |
| A session that doesn't, in a persona that asks | `nexus <persona> --bypass-permissions [session] [-- claude]` |
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
| A project's stack on the shared Docker daemon | `docker compose up -d` inside, as on the VM: the project name gets the persona in front, ports go in its range, files to mount in the projects root or `$NEXUS_SCRATCH` (section 4) |
| Personas set up on this VM | `/opt/agents/nexus --config --list` |

Sessions are named after the directory (`.` and `:` become `_`). Two projects with the same directory name
share one session, so give one an explicit name: `nexus claude-ddev api-b`. A persona sees only its projects
root (its own, or the VM's); run from a directory outside it, `nexus` starts the session in the root. When a
session's command exits, tmux moves you to another session instead of dropping you out; `Prefix+d` leaves.

**A session that asks, or doesn't.** A Claude persona runs its tools without asking (`bypassPermissions`) unless
it is set to ask ([section 5](#asking-before-running-tools)). One session can differ: `--skip-bypass-permissions`
starts it asking (Claude Code's `default` mode: it asks before each tool that needs it), `--bypass-permissions`
starts it without asking, whatever the persona's setting. The word may stand before or after the session name;
after `--` it belongs to the command, and any other `-x` after the persona is still a session name, as before.
- It holds for as long as the session lives. nexus creates it with `NEXUS_PERMISSIONS` (`ask` or `bypass`) in
  its tmux environment, so its shell, a new window (`Prefix+c`) and a `claude` started again later all get it:
  the image's `claude` puts `--permission-mode default` (or `bypassPermissions`) first, and an agent team's
  teammates start in the same mode.
- It is the mode Claude starts in, not a lock: a flag you type after `claude` wins, so
  `claude --dangerously-skip-permissions` in a session that asks still bypasses.
- nexus looks inside the persona first. A session that is there already is attached as usual if it runs in that
  mode; one in the other mode, or whose mode can't be read, is refused: end it, or name a new session. `main`
  (the persona's own shell session) is always in the persona's own mode, so a word that differs from it is
  refused there. A new session is created in the mode and never attached to one that is there: if tmux finds
  one after all (made meanwhile, or a name with `#` or `\` that tmux rewrites), it says `duplicate session`.
- Refused before anything runs: both words together, a word with `ls`, `stop`, `logs`, `build`, `pull`,
  `start`, `restart` or `upgrade`, and a word for a grok persona (grok takes its approval mode from
  `personas/grok.toml`). With `headless` it sets that one run (section 3).
- The words need the persona's image built from this checkout, and the persona recreated on it:
  `nexus <persona> build` (with a registry, `pull`), then `nexus <persona> restart`; or the next
  `nexus <persona> upgrade`, which does both. On an image from before, a word for a new session or a headless run
  is refused, saying its image was built before them ([section 10](#10-update-a-vm)); a session that is there already is handled
  as above. Without a word, nexus works exactly as before.
- That teammates keep the mode was checked by reading Claude Code 2.1.302 and testing the links
  `/usr/local/libexec/claude-ask` and `claude-bypass`, not with a real signed-in team.

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
has: `npx`, `uvx` (Python) and `docker` work. A project that needs another APM version than the image's runs it
with `nexus-apm X.Y.Z …` ([section 6](#6-upgrade-claude-code-or-grok-apm-or-ddev)).

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
- **A run that asks** (a persona set to ask, [section 5](#asking-before-running-tools), or a run given
  `--skip-bypass-permissions`) can't get approval from anyone in `claude -p`, so tools that need it are denied.
  For such a run, give `--bypass-permissions`, or allow what it needs with `--allowedTools`. Either word may
  stand before `headless` or anywhere among its arguments, after the prompt too; nexus takes it out, and runs the
  CLI with `NEXUS_PERMISSIONS` set, so the image's `claude` puts `--permission-mode` first. The word needs the
  rebuilt image ([section 2](#2-daily-use)); a grok persona refuses it.

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
carries no project toolchains (no Go, PHP or Java; python3 is there for the statusline and APM, with venv and uv
for a project's own Python tools: [below](#python-venv-uv-and-uvx)), so versions come from the project:
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
- Every persona, and you on the VM, share one Docker daemon and one network. The rest of this section is how
  that stays workable: what a container can mount, which ports to publish, how compose names projects, and the
  Docker guard. The Nexus notes tell each persona's CLI the same ([section 5](#the-nexus-notes)).

### Bind mounts and the scratch dir

A container a persona starts runs on the VM's daemon, so every bind-mount source (`docker run -v SRC:…`,
`--mount source=…`, a path in a compose file's `volumes:`) is looked up on the VM, not in the persona. Two places
are at the same path on both sides, and writable:
- the persona's projects root (`$PROJECTS_ROOT`);
- its scratch dir, `$NEXUS_SCRATCH`: `/srv/nexus-scratch/<persona>`, or `<NEXUS_SCRATCH_ROOT>/<persona>`. It is for
  throwaway files a container has to mount: test keys, env files, a copy for a probe, a `TMPDIR` for tests that
  start containers.

```bash
cp ci/test.key "$NEXUS_SCRATCH/"
docker run --rm -v "$NEXUS_SCRATCH/test.key:/k:ro" alpine:3.20 wc -c /k
mkdir -p "$NEXUS_SCRATCH/tmp" && TMPDIR="$NEXUS_SCRATCH/tmp" make e2e     # tests that mount their temp files
```

Everything else in a persona (`/tmp`, `$HOME`, which is its home volume, the image's own files) isn't on the VM.
Docker refuses a missing `--mount` source (`bind source path does not exist`), and makes a missing `-v` source on
the VM as an empty directory owned by root, which the container then sees instead of your file.
- Only the projects root and the scratch dir themselves can be written to: their parent directories exist in the
  persona only as mount points, so a folder next to them can't be made. Make it inside one of them.
- nexus makes the persona's scratch dir (mode 755) when it starts the persona; `nexus --config` makes the root
  once ([section 1](#1-first-time-setup-on-a-vm)). Each persona mounts only its own dir.
- Nothing empties it: what is in it outlives restarts and rebuilds, and stays when the persona is removed
  (`nexus --config` then prints the `sudo rm -rf` for it: a container may have left root-owned files in it).
- A container that writes into it as root leaves files the persona can't delete: run it with
  `--user "$(id -u):$(id -g)"`.
- Another root: `NEXUS_SCRATCH_ROOT` in `.env`, then `nexus --config` (it makes the new root) and
  `nexus <persona> restart` for each persona. Until its restart, a persona warns at each start and attach that its
  container was made without its scratch dir. The old root and what is in it stay where they are.

### Ports

The personas use the VM's network, and so do the stacks they start: a port a stack publishes on `127.0.0.1:PORT`
is `localhost:PORT` inside every persona, with no `--network host` helper and no port forwarding. Two stacks can't
publish the same port, though: the second gets `Bind for 127.0.0.1:8080 failed: port is already allocated`.

So each persona has a range of ports of its own, `$NEXUS_PORTS` (e.g. `20000-20099`), which its notes tell its
CLI. `nexus --config` gives each persona one ([section 5](#personasjson)). It is an agreement, not a lock: nothing
stops a persona publishing outside its range. Publish on 127.0.0.1, and either:
- let Docker pick a free port: `-p 127.0.0.1::80`, or `ports: ["127.0.0.1::80"]` in compose, then ask which:
  `docker compose port web 80` (or `docker port <container> 80`). Docker takes it from the VM's ephemeral range
  (`/proc/sys/net/ipv4/ip_local_port_range`, 32768-60999 on Ubuntu), above the blocks `nexus --config` gives (until
  about 127 personas, or a range set by hand at 32768 or higher, which the next blocks follow); or
- pick one in the range, through the project's own variable when it has one: `WEB_PORT=20001 docker compose up -d`.

A fixed common port (80, 3000, 5432, 8080…) is what collides: another persona's copy of the stack, or one
started on the VM, may hold it. `docker ps --filter publish=8080` shows which container has it. A project whose
compose file publishes one with no variable for it takes an override file of your own, with `!override`
([below](#compose-project-names)). A server run in the persona itself (`npm run dev`, vite,
`python3 -m http.server`) listens on the VM's ports as well: 127.0.0.1, and a port in the range.

### Compose project names

Inside a persona, `docker compose` puts the persona's name in front of the project's: project `shop` (from the
directory `shop`, or `name: shop`) becomes `claude-team-shop`, so its containers are `claude-team-shop-<service>-1`
and its networks and volumes `claude-team-shop_…` (`claude-team-shop_default`). Two personas can run the same
project at once, each its own copy, and one's `down` never takes the other's containers. The image's
`docker compose` is a small shim (`/usr/local/lib/docker/cli-plugins/docker-compose`): it asks the real compose
which name it would pick (from `.env`, `name:`, `-f`, the directory, as compose always does), then runs it with
`COMPOSE_PROJECT_NAME` set to that name with the prefix. That costs about 15 ms a command, about 50 ms when it has
to ask.
- It asks with `docker compose config --no-env-resolution --no-consistency`. A compose that refuses those flags
  would run every project unprefixed, without a word; the unit tests (`tests/unit/test_compose_shim.py`) fail on
  such a compose rather than skip. Compose 2.x takes them but still fails on a service's missing `env_file`, so such
  a project runs unprefixed there; the image's build checks that its Compose (5 or later) doesn't.
- A remote `include:` or `-f oci://…` is fetched twice a command, once to ask: an unreachable one costs compose's
  own timeout twice (about 30 s each) before its error.
- When a script stops `docker compose` with a signal and there is no terminal, the docker CLI closes its plugin
  socket. If that comes while the shim asks, the command exits 130 with nothing started; after that, compose
  handles the signal as always.
- When the real plugin (`/usr/libexec/docker/cli-plugins/docker-compose`) is missing, `docker compose …` says
  `nexus compose shim: can't run Docker Compose at <path>: …` (exit 127, or 126 when it can't be run), and
  `docker --help` lists `compose*  Docker Compose is missing (nexus compose shim)`.

No prefix is added:
- with `-p NAME` (`--project-name`) or an exported `COMPOSE_PROJECT_NAME`: the name as given (one in the
  project's `.env` or an `--env-file` gets the prefix). Use them only to reach a stack on purpose, such as one
  started before the prefix ([section 10](#stacks-started-before-the-prefix-and-the-guard)). A project's Makefile or
  `.envrc` may export `COMPOSE_PROJECT_NAME` without the agent seeing it, and the stack then gets that bare name,
  quietly: `-p <persona>-<project>`, or `make COMPOSE_PROJECT_NAME=<persona>-<project> …`, gets the prefix back
  (`COMPOSE_PROJECT_NAME= make …` doesn't beat a Makefile's `export`);
- to a name that starts with `<persona>-` already (never twice);
- with a compose or env file from stdin or a pipe (`-f -`, `/dev/stdin`, `<(…)`, a FIFO, in `-f`, `--env-file` or
  `COMPOSE_FILE`), which the shim can't read before compose does. It says so in one line on stderr (not with `-p`
  or an exported `COMPOSE_PROJECT_NAME`):
  ```text
  nexus compose shim: a compose or env file from stdin or a pipe can't be read twice, so the project has no claude-team- prefix (use a file, or -p claude-team-<name>)
  ```
- where there is no project: `docker compose version`, `ls` (which lists every persona's projects);
- by ddev: it names its projects itself, `ddev-<site>` (from 1.25.3 it links compose as a Go library; before, it
  ran its own compose binary). Two personas' ddev sites of the same name are one project: keep site names
  distinct between personas;
- by programs that link compose as a Go library rather than run `docker compose` (testcontainers' compose
  module, say): they name the project themselves;
- by a `docker-compose` plugin of your own in `~/.docker/cli-plugins`, which docker finds before the shim.

A fixed `container_name:` in a compose file, a fixed `name:` on a network or volume, and a fixed host port are the
same for every copy of the stack. The second copy gets `Conflict. The container name "/shop-db" is already in use`,
or `Bind for 127.0.0.1:5432 failed: port is already allocated`; a fixed volume is the first copy's, which the guard
won't mount for another persona ([below](#the-docker-guard)). Override it in a file of your own rather than edit
the project's, e.g. a `compose.override.yaml` next to the project's `compose.yaml` (compose reads it too when no
`-f` is given; `.git/info/exclude` keeps it out of git):
```yaml
services:
  db:
    container_name: !reset null            # compose's own name again: <persona>-shop-db-1
    ports: !override ["127.0.0.1::5432"]   # a port Docker picks, instead of the project's fixed 5432
```
Then `docker compose port db 5432` says which. A plain `ports:` there adds to the project's list, so the fixed
port stays published; `!override` replaces it (`!reset` and `!override` were checked with `docker compose config`
on Compose 5.6.1, and `tests/run.sh` brings two personas' copies of such a project up at once). That file, like the
project's `.env`, is read by every persona working in that directory, so only a port Docker picks belongs in it: a
port from one persona's range there would be every persona's. A persona's own port goes on its command line
(`WEB_PORT=20001 docker compose up -d`), or in a file of its own in its scratch dir:
`docker compose -f compose.yaml -f "$NEXUS_SCRATCH/shop.ports.yaml" up -d`.

### The Docker guard

Every persona talks to the daemon through a guard, unless `DOCKER_GUARD=off` in `.env`. The image's `DOCKER_HOST`
is `unix:///run/nexus/docker.sock`, where the persona's entrypoint starts the guard (and starts it again a second
after it stops), in front of the VM's `/var/run/docker.sock`. Since the daemon is shared, the guard:
- **labels** every container, volume and network the persona creates `nexus.persona=<persona>` (compose's and
  ddev's included; a swarm service, secret or config too), and the volumes a container would make itself: a named
  one (`-v data:/x`), which it creates first, and the anonymous ones (`-v /data`, `--mount type=volume` with no
  source, `volumes: [/data]` in compose, an image's `VOLUME`), which the daemon then makes labelled. Beside
  `--volumes-from`, the `-v /data` and `VOLUME` ones stay unlabelled: a volume added there would hide the one it
  inherits. Images aren't labelled: Docker can't label a build or a pull on the way (a `docker commit` takes the
  container's labels).
- **narrows prunes** (`docker container prune`, `network prune`, `volume prune`, `image prune`, and so the first
  part of `docker system prune`) to what carries the persona's label. `docker volume prune` without `-a` reaches
  only anonymous volumes: the persona's unused ones go. `docker image prune` reaches only dangling images (with
  `-a`, unused ones), which carry no label, so it removes nothing but the persona's own commits.
- **refuses**, with `Error response from daemon: nexus guard: …`, saying whose it is (most say
  `… isn't labelled nexus.persona=<persona> (…)`):
  - stopping, killing, restarting, pausing, unpausing, updating, renaming or removing a container that isn't
    labelled the persona's (another persona's, the persona containers themselves, one you started on the VM, or
    one started before the guard), and `docker network connect` or `disconnect` of one, on any network;
  - removing a volume, network, service, secret, config or node that isn't labelled the persona's;
  - mounting a volume labelled another persona's (`-v NAME:…`, `--mount source=NAME`, a compose `external: true`
    volume, a fixed `name:`), `docker volume create` of its name (the daemon would answer with that volume), and
    `--volumes-from` a container labelled another persona's: a volume's name is global, so the two would share its
    data. Unlabelled volumes (yours on the VM, and those from before the guard) aren't checked: a persona can still
    mount them;
  - removing an image that is a persona image (label `agent.flavour`, which an image built `FROM` one inherits),
    labelled another persona's, built by a compose project not the persona's (`com.docker.compose.project` not
    `<persona>-…`; ddev's `ddev-…` go), or used by a container not labelled the persona's (another persona's, yours,
    a persona container), `-f` or not. An image labelled the persona's (a `docker commit` of its own container)
    is its own, whatever it was made from. Untagging one tag of an image that has more goes on, and so does
    removing any other image: a plain `docker build`, or a pull;
  - `docker tag` or `docker commit` onto a name an image has now, unless that image could be removed (above) or is
    the one being tagged: the name would move off it (a persona image's name: every persona of it would start on
    what was tagged);
  - pruning the build cache (`POST /build/prune`): Docker can't label it and every persona shares it. So
    `docker system prune` ends with that error, after its other prunes;
  - `docker swarm` (init, join, leave, update, unlock) and installing, changing or removing a plugin: the daemon's
    swarm mode and plugins are every persona's. `docker compose` runs a stack without swarm (not `docker stack`).

  ddev's shared containers `ddev-router` and `ddev-ssh-agent`, its network `ddev_default` and its volumes
  `ddev-global-cache`, `ddev-ssh-agent_socket_dir` and `ddev-router-traefik` are let through by name for what ddev
  does to them: every ddev project uses them, and ddev removes and recreates them as it needs to. So any persona may
  stop, kill, restart, remove, connect and disconnect those containers, and mount those volumes, but not pause,
  unpause, update or rename them. `ddev poweroff` (or `ddev stop --all`) in one persona can't remove the other
  personas' ddev projects: the guard refuses those, and ddev warns and carries on. It does remove `ddev-router` and
  `ddev-ssh-agent`, which every persona's ddev sites and yours go through: their `*.ddev.site` addresses stop
  answering until the next `ddev start` anywhere. The notes tell agents to `ddev stop` their own project instead.
- lets everything else through as it is: lists, inspect, logs, `exec`, start, build, pull, events,
  `docker service update`. A persona can still look at, and `exec` into, containers that aren't its own, the other
  persona containers included, whose environment holds that persona's tokens and keys (`docker inspect
  agent-<other>` shows them): the notes tell agents not to. `docker load`, `docker import`, `docker build -t` and a
  pull can still move a name off an image.

A refusal means: leave it, or ask the user. On the VM there is no guard, so a person who means it does it there.
```text
Error response from daemon: nexus guard: container shop-db isn't labelled nexus.persona=claude-team (it is persona grok-docker's; a persona may stop only what it made: leave it, or ask the user, who can stop it on the VM)
Error response from daemon: nexus guard: volume pgdata isn't labelled nexus.persona=claude-team (it is persona grok-docker's, and a volume's name is global on the daemon: one of yours by that name would be grok-docker's, data and all: name yours otherwise, e.g. claude-team-pgdata, or ask the user)
```
The persona's log (`nexus <persona> logs`) has a line for each refusal, `docker-guard: refused …` (a method and a
path, never the query), and at each start one saying `docker guard on: …` or `docker guard off (<why>): …`. Left
without a thread for a connection (the container's process limit), the guard drops that one connection, logs
`docker-guard: can't serve a connection: …`, and serves on. The guard starts whatever uid the persona runs as
(`/run/nexus` is writable by every uid). Where `/run/nexus` can't be written (a read-only root filesystem,
`read_only: true` in a compose override, say) the image's link to the raw socket stays, nothing is labelled or
refused, and the log says `docker guard can't start, docker here is UNGUARDED: …`. A persona whose guard is on but
not listening (one that can't start, say) shows as `unhealthy` in `nexus ls`; an unguarded one still shows
`healthy`.

`docker attach` to a container not the persona's: Ctrl-C is a kill, which the guard refuses, and the CLI stays
attached without a word; three Ctrl-C end it. `docker logs -f` follows such a container instead.

For a program of your own that talks to the API: only HTTP/1.x goes through (anything else gets a 505). An
upgrade, as `docker attach`, `docker exec`, `docker run -i` and buildx make it, works as before; a websocket attach
(`containers/<id>/attach/ws`) doesn't. A create body the daemon would read otherwise than the guard (not UTF-8, a
key twice, one field in two spellings, more after its JSON) gets a 400 saying which.

What it doesn't see, since it isn't a wall:
- The raw socket `/var/run/docker.sock` is still mounted in every persona (`docker -H unix:///var/run/docker.sock`,
  or that `DOCKER_HOST`, goes round the guard).
- A sibling container given the socket (`-v /var/run/docker.sock:/var/run/docker.sock`) talks to the daemon
  without it: that source is looked up on the VM, where only the raw socket is. A tool that mounts
  `DOCKER_HOST`'s socket into the containers it starts gets a path the VM doesn't have, so it must be given the raw
  one: act with `--container-daemon-socket unix:///var/run/docker.sock`. Otherwise `-v` leaves a root-owned
  directory `/run/nexus/docker.sock` on the VM (`sudo rmdir /run/nexus/docker.sock` removes it) and the container
  sees that directory. For testcontainers' Ryuk the image sets
  `TESTCONTAINERS_DOCKER_SOCKET_OVERRIDE=/var/run/docker.sock`; Ryuk itself is created through the guard, so it is
  labelled.
- `docker builder prune` (and `docker buildx prune`): buildx sends it over gRPC, which the guard passes through
  unread, so it empties the build cache every persona shares. Don't run it in a persona unless the user asks.

The raw socket is for a person who means it, not a way past a refusal. What a persona makes through it carries no
label, so the guard later won't let even that persona stop or remove it, and a prune through it reaches every
persona's stacks and data.

**Off:** set `DOCKER_GUARD=off` in `.env` (exactly `on` or `off`; empty is `on`, and `nexus --config` refuses
anything else and writes nothing), run `nexus --config` (it writes the guard's state into the notes, and says
`<persona>: the Docker guard is off now (DOCKER_GUARD), as its notes say; …` for each persona whose notes said
otherwise), then `nexus <persona> restart` for each persona. `/run/nexus/docker.sock` is then a link to the raw
socket: nothing is labelled or refused. `on` again the same way. Inside a persona,
`readlink /run/nexus/docker.sock` prints `/var/run/docker.sock` while the guard is off (or unguarded), nothing
while it is on.

Side effects to know: a named volume a container create would make is made first, labelled, once every check has
passed, so a create the daemon then fails (a missing image, say) leaves that empty volume behind; `docker volume rm`
removes it. And the guard asks the daemon more: a container create, for its image's `VOLUME`s; an image's removal,
for the containers that use it.

### Docker in Docker

Privileged sibling containers, Docker-in-Docker for an end-to-end suite say, are allowed. A privileged container
is root on the VM, as the Docker socket already is ([README, rules](README.md#rules-that-keep-it-working)). Then:
- it is labelled as the persona's, like everything it creates through the guard: remove it when done;
- publish its ports on 127.0.0.1, in the persona's range;
- the daemon inside it resolves bind paths inside it, not on the VM: mount the projects root or the scratch dir
  into it at the same path, so the suite's bind mounts find their files.

For example (a sketch, not run here; pin the tag):
```bash
docker run -d --privileged --name "$AGENT_PERSONA-dind" -v "$AGENT_PERSONA-dind:/var/lib/docker" \
  -v "$PROJECTS_ROOT:$PROJECTS_ROOT" -e DOCKER_TLS_CERTDIR= -p 127.0.0.1:20090:2375 docker:<version>-dind
DOCKER_HOST=tcp://127.0.0.1:20090 docker ps          # 20090: a port in the persona's range
```
Without TLS, anyone on the VM can use that daemon while it runs.

**Sysbox**, if the VM has it, is the safer runtime: Docker (or systemd) inside a sibling without `--privileged`
(`docker run --runtime=sysbox-runc …`, compose `runtime: sysbox-runc`), as a root that is root only inside a user
namespace, with an inner daemon apart from the VM's. Prefer it when `docker info` lists `sysbox-runc` among the
runtimes. Installing it is the VM owner's call, and costs a restart of Docker. From Sysbox's docs and issues
(v0.7.1, checked 2026-11-03; not installed or tried here):
- its package (`sysbox-ce_0.7.1.linux_amd64.deb`, or `_arm64`, from github.com/nestybox/sysbox releases) installs
  with `sudo apt-get install jq ./sysbox-ce_0.7.1.linux_amd64.deb`, and needs systemd (not Docker from snap);
- its installer adds the runtime to `/etc/docker/daemon.json`, and also `bip` and `default-address-pools`, which
  need dockerd restarted: that stops every container on the VM, ending every persona's tmux sessions and the
  agents working in them (Sysbox's own guide removes all containers first). Plan it like a reboot;
- a persona itself can't run under Sysbox: it uses the host network, which Sysbox refuses, as it does
  `--privileged`, `--pid=host` and buildx's QEMU emulation. Only the siblings a persona starts can;
- Ubuntu 26.04 isn't in its list of supported distributions yet (that ends at 24.04), and two open issues bear on
  it: #1005, `fusermount3: mount failed: Permission denied` (AppArmor; the workaround given there is
  `sudo aa-complain /etc/apparmor.d/fusermount3`), and #1021, an inner Docker 29 on the containerd image store
  losing its state when the container is recreated (the workaround: `"storage-driver": "overlay2"` in the inner
  daemon's `daemon.json`). Kernel: 5.12 or newer (from 5.19 it needs no shiftfs at all).

### Python: venv, uv and uvx

The image's python3 (3.11) is there for the statusline and APM, but a project's own Python tools can run on it too,
in a venv of the project's, at the versions it pins:
- `python3 -m venv .venv`, or `uv venv`; then `uv pip install -r requirements.txt`, or `uv sync` and `uv run …` in
  a uv project. The venv lives in the project, under the projects root. One made on the image's python3 points at
  `/usr/bin/python3` in the image, so it works in every persona, but not necessarily from the VM shell.
- `uvx --from ruff==0.6.9 ruff check` (or `uvx ruff@0.6.9 check`) runs a tool at the version a project pins,
  without installing it into anything. MCP servers that start with `uvx` work too.
- uv keeps its cache in the home volume (`~/.cache/uv`). `UV_LINK_MODE=copy` is set because that and the
  projects root are two filesystems, where uv's default hardlinks fail.
- uv itself is the image's (`UV_VERSION`, [section 6](#6-upgrade-claude-code-or-grok-apm-or-ddev)): `uv self update`
  refuses.
- The rest of a project's toolchain (a compiler, a database) still runs in its own containers.

### GitLab: glab

`glab`, GitLab's CLI, is in every persona: merge requests with `glab mr`, pipelines with `glab ci`. Put a token in
the persona's env file (`personas/<name>.env`), then `nexus <name> restart`. One token serves one GitLab host:
- **gitlab.com:** `GITLAB_TOKEN=…`. glab uses it, and so does git over https to gitlab.com: the image's git
  credential helper for `https://gitlab.com` (`/usr/local/libexec/nexus/glab-credential`, in front of glab's own;
  in `/etc/gitconfig`, so the persona's own `~/.gitconfig` stays its own) hands it over, so
  `git clone https://gitlab.com/…` and `git push` need nothing more.
- **A self-managed GitLab:** `GITLAB_TOKEN=…` and `GITLAB_HOST=gitlab.example.org` (with its port, if it has one:
  `gitlab.example.org:8443`). glab then works with that host, and the token is that host's alone: git over https to
  gitlab.com no longer gets it. For git over https to it, run once inside the persona (it is kept in the home
  volume):
  ```bash
  git config --global credential.https://gitlab.example.org.helper /usr/local/libexec/nexus/glab-credential
  ```
- **Another GitLab host besides** (gitlab.com next to a self-managed one, say): `glab auth login --hostname <host>`
  inside the persona (glab keeps that token in plain text in `~/.config/glab-cli`, in the home volume), then the
  same `git config` line for that host. Not glab's own `--setup-git-credential-helper` while `GITLAB_TOKEN` is set:
  the helper it writes hands the env token to every host glab knows, ahead of their own.

The image's helper hands the token in the environment (`GITLAB_TOKEN`, `GITLAB_ACCESS_TOKEN` or `OAUTH_TOKEN`) only
to the host glab designates: `GITLAB_HOST` (or `GITLAB_URI`, `GL_HOST`), else the `host` in glab's config, else
gitlab.com, the port included. For any other host, glab answers without it, with what `glab auth login` stored for
that host, if anything. When that is nothing, git's own error comes after a line naming the token's host:
```text
glab-credential: the GitLab token in the environment is for gitlab.example.org, so gitlab.com doesn't get it (a login of its own: glab auth login --hostname gitlab.com)
```
glab doesn't look for a newer release (`GLAB_CHECK_UPDATE=false`): the image is its updater (`GLAB_VERSION`,
[section 6](#6-upgrade-claude-code-or-grok-apm-or-ddev)). The image also turns off its usage telemetry to GitLab
(`GLAB_SEND_TELEMETRY=false`) and its "What's new in glab" notice, which would show on a `git push`
(`GLAB_SHOW_WHATS_NEW=false`).

## 5. Add, change or remove a persona

Run `nexus --config` and choose from the menu:
- **add**: the questions of a first persona (step 2 of [section 1](#1-first-time-setup-on-a-vm)), then its
  sign-in, then its image (or pull) and, for ddev, the CA.
- **change**: which persona, then the same questions with its current settings as the defaults (Enter keeps
  each); a new flavour asks about a new env file, a new flavour or context offers the image.
- **remove**: which persona, then `Remove <name>? Its env file and home volume stay [y/N]`.
- **shared**: the projects root, registry and Claude Code version, with what `.env` has as the defaults; a new
  registry offers every image again, a new Claude Code version the Claude ones.
- **telemetry**: the collector and which personas send ([below](#telemetry-opentelemetry)).
- **launcher**, **images**, **list**, **quit**: as they say.

Each is saved as it is done. Without questions:
```bash
/opt/agents/nexus --config --yes --persona grok-docker:grok:docker            # add, or update flavour/context
/opt/agents/nexus --config --yes --persona grok-docker:grok:docker:/srv/work  # with a projects root of its own
/opt/agents/nexus --config --yes --persona grok-docker:grok:docker:           # back to the VM's projects root
/opt/agents/nexus --config --yes --remove grok-docker                         # drop it from personas.json
/opt/agents/nexus --config --yes --ssh grok-docker:none                      # no ssh: push from the VM (below)
/opt/agents/nexus --config --yes --ssh grok-docker:new                       # an ssh key of its own (below)
/opt/agents/nexus --config --yes --ssh grok-docker:~/.ssh/grok_deploy        # a key you have
/opt/agents/nexus --config --yes --ssh grok-docker:agent                     # back to your ssh-agent
/opt/agents/nexus --config --yes --permissions claude-docker:ask             # it asks before running tools (below)
/opt/agents/nexus --config --yes --permissions claude-docker:bypass          # back to bypassPermissions
/opt/agents/nexus --config --yes --ports grok-docker:20500-20599             # its port range (below)
```
- A persona's own projects root is kept in `personas.json` (`root`), and can't contain spaces or `:`. Given
  without `:ROOT`, an existing persona keeps its root. Changing the root takes `nexus <persona> restart`; its
  sessions in the old root end. Until then nexus warns that it still runs with the old root: in that root it
  works as before; elsewhere it only attaches to one of its sessions by name.
- After a change, `nexus <persona> restart` applies it (that ends its tmux sessions); `nexus --config` says so
  for each persona it changed.
- With `--yes`, its build (or pull) covers every persona's image, not only a new one's: adding a grok persona also
  re-runs the claude image's build, which takes seconds from the cache when nothing changed. The menu builds the
  image of the persona added or changed, and `images` all of them. A running persona keeps the image it started
  with until `nexus <persona> restart`.
- An existing `personas/<name>.env` is kept. When the flavour changes, `nexus --config` offers a new one (with `--yes`
  it writes one) and keeps the old as `.env.bak` (`.env.bak.2` … if that is taken). A new persona whose name has a
  leftover env file is treated the same way: one of its flavour is reused, one of the other flavour is replaced
  (the old one to `.env.bak`).
- `--statusline` / `--no-statusline` with `--persona` turn a persona's statusline on or off; without either, an
  existing persona keeps its setting, and a new one gets it.
- `--permissions <name>:ask|bypass` (repeatable) works on a persona that is there, or one `--persona` adds in the
  same run; without it a new persona bypasses, and an existing one keeps its setting. `ask` for a grok persona,
  or a name no persona has, is refused.
- Every persona has a port range of its own, `NEXUS_PORTS` inside it ([section 4](#ports)). `nexus --config` gives
  each persona without one a block of 100, on any run that writes: the block after the highest one in use, from
  20000 (`20000-20099`, `20100-20199`, …), and once that would pass 65535, the first free block from 20000.
  `--ports <name>:FROM-TO` (repeatable) sets one, for a persona that is there or one `--persona` adds in the same
  run; two personas can swap theirs in one run. A range that overlaps another persona's is refused, and nothing is
  written. The menu keeps a persona's range and doesn't ask. A changed range needs `nexus <persona> restart`.
  `--list` ends with `published ports: <name> FROM-TO, …`.
- Removing keeps the env file, the ssh key, the Nexus notes (`personas/<name>.notes.md`), the scratch dir and the
  home volume; `nexus --config` prints the commands that delete them (the scratch dir's with `sudo rm -rf`: a
  container may have left root-owned files in it). What the persona made with docker stays too, and the note says
  how to list it: `docker ps -a --filter label=nexus.persona=<name>` (`docker volume ls` and `docker network ls` take
  the filter too). While that `personas/<name>.env` stays, the
  menu lists it as belonging to no persona (adding one of that name reuses it); delete it to stop that. Removing a
  persona and adding it again in one run (`--remove X --persona X:…`) makes it a new one, with a new port range:
  `nexus X restart` applies it.

### personas.json

`nexus --config` keeps the personas in `personas.json`, in the checkout: per VM, and gitignored. It holds them in
persona order (the order of their colours and of `--list`), each under its name:

```json
{"version": 1, "personas": {
  "claude-ddev": {"flavour": "claude", "context": "ddev", "colour": "colour25", "statusline": true,
                  "permissions": "ask", "ports": "20000-20099"},
  "grok-docker": {"flavour": "grok", "context": "docker", "colour": "colour28", "statusline": false,
                  "root": "/srv/work", "ssh": "none", "ports": "20100-20199"}}}
```

| Key | Value |
|-----|-------|
| `flavour` | `claude` or `grok` |
| `context` | `ddev` or `docker` |
| `colour` | `colourN`, the persona's colour in the status bar |
| `statusline` | `true` or `false` |
| `root` | only with a projects root of its own: an absolute directory other than `/`, without spaces or `:` |
| `ssh` | only when it doesn't use your agent: `none`, `personas/<name>.key`, or the absolute path of a key (no spaces, `:`, quotes or `$`) |
| `permissions` | only for a Claude persona that asks before running tools: `ask`. Left out (or `bypass`), it runs them without asking |
| `ports` | its port range, `FROM-TO`: two ports from 1024 to 65535, no leading zeros, FROM no higher than TO, overlapping no other persona's. Left out, the next `nexus --config` that writes gives it one ([above](#5-add-change-or-remove-a-persona)) |

- `nexus --config` writes it by a rename, under its lock, and keeps the one before as `personas.json.bak`. Then it
  generates `compose.personas.yml` from it: a service per persona, under a header that says "generated by
  `nexus --config` from personas.json; don't edit it".
- Both tools refuse a `personas.json` they can't take: JSON that doesn't parse, another `version`, a name or key
  given twice, a key it doesn't take, a value of the wrong type or one the rules above don't allow, a NUL, two
  personas whose port ranges overlap (`persona 'c2' has ports 20050-20149, which overlap c1's (20000-20099)`). The
  message names the file and what is wrong, and `personas.json.bak` when there is one, e.g.
  `nexus: /opt/agents/personas.json has 'colour' twice: fix it`.
- Editing it by hand is possible, but `nexus --config` is the way. After a hand edit that changes a service,
  `compose.personas.yml` is out of step with it, and nexus won't create a container until `nexus --config --yes`
  writes that file again ([section 10](#out-of-step-with-personasjson)).

### No ssh: commit inside, push from the VM

By default a persona's ssh, and so git over ssh, uses your ssh-agent, forwarded: your keys never enter the
container, but starting it needs an agent in your shell, and a forwarded one goes stale when you reconnect.
Committing needs no ssh at all, only pull, fetch and push do. So a persona can go without (`nexus --config`:
change it, SSH `none`; or `--ssh <name>:none`, then `nexus <name> restart`):

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

1. Re-run `nexus --config`, change the persona, and answer `new` at "SSH". It makes `personas/<name>.key` (ed25519, no
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

`new` never replaces a key that is there. For a fresh one, delete `personas/<name>.key*`, re-run `nexus --config`
(it makes the key and prints it), add it on the git host, and `nexus <name> restart`: a running persona keeps the
key it started with until then. The same goes for a key file you named and then replaced.

### Asking before running tools

As committed, every Claude persona runs its tools without asking: `personas/claude.managed.json` starts Claude
Code in `bypassPermissions` ([below](#settings-for-every-persona)). One can ask first instead (`nexus --config`:
add or change it, and answer `n` at `Let <name> run its tools without asking …?`; or `--permissions <name>:ask`),
then, for one that is there already, `nexus <name> restart`; `nexus --config` says so (`<name>: changed; …`).

- Its container gets the committed `personas/claude.ask.json` mounted read-only as the managed drop-in
  `/etc/claude-code/managed-settings.d/16-ask.json`. It comes after `15-personas.json`, so its mode wins: as
  committed `default`, which asks before each tool that needs it. For another mode that asks (`acceptEdits`,
  `plan`, …), edit `claude.ask.json`: every persona that asks gets it, at its next restart.
- It covers every `claude` in the persona (its sessions, one you type, headless runs, an agent team's
  teammates), since it is Claude Code's own managed setting, except a session or headless run given one of the
  words ([section 2](#2-daily-use)): `--bypass-permissions` runs without asking, and `--skip-bypass-permissions`
  starts in `default`, whatever mode `claude.ask.json` sets. It works on any image. Like a word, it is the mode
  Claude starts in, not a lock.
- Until the restart, a start or an attach of the running persona warns: `<name> asks before running tools now,
  but its container was made before that and doesn't`.
- While a persona asks and `claude.ask.json` is missing, unreadable, not JSON, sets no `permissions.defaultMode`,
  or sets `bypassPermissions`, `nexus --config` writes nothing and nexus won't start or restart that persona: it
  would bypass. `git checkout personas/claude.ask.json` restores the committed one.
- Headless runs of a persona that asks can't get approval, so tools that need it are denied
  ([section 3](#3-headless-runs)).
- grok personas can't be set to ask (refused): grok takes its approval mode from `personas/grok.toml`.

### Telemetry (OpenTelemetry)

Off unless you set it up. A persona can send what its CLI reports about itself (tokens, cost, sessions, tool use,
errors; no prompts or replies) to one OpenTelemetry collector, which every persona that sends shares: one you run
for several VMs, say. Nothing is sent from a persona you didn't turn on.

**Set it up:** `nexus --config`, then `telemetry` in the menu. It asks:
1. the collector's OTLP/HTTP address, e.g. `https://collector.lan:4318` (`none` turns telemetry off for all);
2. its bearer token, sent as `Authorization: Bearer <token>` (hidden; empty keeps the one set, `-` for none);
3. the CA file its certificate is signed by, PEM, if it has a private CA (`-`: a publicly trusted one);
4. for each persona, whether it sends;
5. if any persona sends, which of them send their tool details too: `none` (the default), `all` (every persona
   that sends, now or later) or `some` (a yes or no for each). See [Tool details](#tool-details) below.
6. then the same for their tool content. See [Tool content](#tool-content) below.

It saves, then tries the collector with an empty export (no data): `-- telemetry: <address> took a test export
with the token`, or what is wrong. Then restart each persona it names, e.g. `nexus <name> restart` (that ends its
tmux sessions). `add` and `change` ask whether a persona sends, while a collector is set, and then whether it sends
its tool details and its tool content, each unless it is on for all.

For scripts, the same with `--yes`:
```bash
nexus --config --yes --telemetry-endpoint https://collector.lan:4318 \
  --telemetry-token-file ~/collector-token --telemetry-ca ~/collector-ca.pem \
  --telemetry claude-team:on --telemetry grok:on
```
(`--telemetry-endpoint none` turns it off; `--telemetry NAME:off` stops one persona; the files take absolute paths;
an empty token file sets none.

A new endpoint takes its token and CA with it, so the old collector's token is never sent to a new one:
- Under `--yes`, `--telemetry-token-file` and `--telemetry-ca` must come with it, or nothing is written and the
  message gives the command. Use `--telemetry-token-file /dev/null` for no token, and `--telemetry-ca none` for a
  publicly trusted certificate.
- Without `--yes`, `nexus --config` asks for the token and CA.
- The menu's `telemetry` action doesn't offer the old ones when the address changes. Tool details: `--tool-details all:on` for every persona that sends, or
`--tool-details NAME:on` for one; `all:off` for none. Tool content: `--tool-content`, the same way.)

What it writes:
- `.env`: `TELEMETRY_ENDPOINT`, `TELEMETRY_TOKEN` and `TELEMETRY_CA` (`personas/telemetry-ca.pem`, the copy of the CA
  file it was given), and `TELEMETRY_TOOL_DETAILS` and `TELEMETRY_TOOL_CONTENT` (`on` for all, empty otherwise).
  `.env` is made private (mode 600) once it holds the token. A path of your own written into `TELEMETRY_CA` by hand
  is copied in at the next `nexus --config`.
- `personas.json`: `"telemetry": true` for each persona that sends, and `"tool_details": true` and
  `"tool_content": true` for each persona you said yes to under `some`.
- For a Claude persona that sends, `personas/<name>.telemetry.json` (mode 600: it holds the token), mounted as the
  managed drop-in `/etc/claude-code/managed-settings.d/50-telemetry.json`. Its `env` turns on Claude Code's metrics,
  events and traces over `http/protobuf`, with `host.name` (the VM) and `persona` (its name) as resource attributes,
  which Claude Code also puts on every datapoint, event and span. A drop-in rather than the container's environment,
  so docker compose in the persona's shell sends nothing (it sends traces of its own as soon as it sees any
  `OTEL_*ENDPOINT` variable), and a `claude -p` run from a script inside still sends.
- For a grok persona that sends, a `[telemetry]` table after its managed config (`personas/<name>.grok.toml`), and
  `personas/<name>.telemetry.env` (mode 600), a second env file holding the token header, which grok takes from
  the environment only. grok sends metrics and events, no traces. The header is set as
  `OTEL_EXPORTER_OTLP_METRICS_HEADERS` and `OTEL_EXPORTER_OTLP_LOGS_HEADERS`, which grok reads, not the generic
  `OTEL_EXPORTER_OTLP_HEADERS`: every process in the persona sees its environment, and docker compose and a
  project's tracing then never send the token. A project's own OpenTelemetry metrics or logs exporter run in that
  persona would still send it to the collector that project names: don't run one there with the token set, or
  give it its own `OTEL_EXPORTER_OTLP_*_HEADERS`.
- The CA is mounted read-only at `/etc/nexus/telemetry-ca.pem` (Claude Code takes it as `NODE_EXTRA_CA_CERTS`,
  grok as `otel_certificate`). A ddev Claude persona gets one file holding the collector's CA and then mkcert's
  (`personas/telemetry-ca.ddev.pem`, written by nexus each time it starts the persona): Claude Code takes a single
  file, and the drop-in's replaces the mkcert CA compose gives it, so without mkcert's in it `https://*.ddev.site`
  would stop being trusted. After a later `mkcert -install`, `nexus <name> restart` brings it in.
- A Claude persona that stops sending gets an empty drop-in (`{}`) until its next `nexus <name> restart`, after
  which it is removed: its running container still mounts the file, and Docker couldn't start that container
  again (a reboot) without it. A grok persona's file is removed at once. All of these are ignored by git.
- `nexus --config` refuses (and writes nothing) while a persona sends and: no collector is set; `.env` holds an
  address or token the options would refuse, or a `TELEMETRY_TOOL_DETAILS` or `TELEMETRY_TOOL_CONTENT` other than
  `on`, `off` or empty; the CA file isn't a certificate, or holds a private key; a Claude
  persona sets `NODE_EXTRA_CA_CERTS` itself (in its env file, a docker one; or in `personas/claude.managed.json`),
  which the drop-in's would replace: put both CAs in one PEM file and give that; or `personas/grok.toml` has a
  `[telemetry]` table of its own.
- Until a persona is restarted after it starts or stops sending, a start or an attach warns: `<name> sends
  telemetry now, but its container was made before that …`, or `… no longer sends telemetry, but its container
  was made while it did and still sends it …`. A Claude persona whose telemetry settings changed since (tool
  details or content turned off, say) warns too: `<name>'s telemetry settings changed after its container was made,
  and it still sends with the old ones …`.

#### Tool details

Off by default. Both CLIs then report which tool ran (Bash, Read, an MCP tool), how long it took and whether it
worked, but not what it ran. With tool details on, they also send what the tools ran with:
- Claude Code: the Bash command, file paths, URLs and search patterns, MCP server and tool names, skill and
  subagent names, and the full error of a failed tool (which can hold what it printed), on its tool events and
  spans; and on each tool event the tool's whole input, each value cut at 512 characters (the start of text written
  to a file, of an edit, of a subagent's prompt, an MCP tool's arguments) (`OTEL_LOG_TOOL_DETAILS=1`).
- grok: real tool, MCP and skill names, file paths, and the start (up to 4 KB) of each tool's arguments, shell
  commands included (`otel_log_tool_details = true` in its `[telemetry]` table).

Neither sends what a tool read or printed (apart from a failed tool's error): that is [tool content](#tool-content),
below. Nor do they send your prompts or the replies. But
the start of what a tool writes goes, and a command can hold a secret, such as a password typed into it or a token
in a URL. The collector stores it as sent, so have it mask secrets first. An OpenTelemetry Collector can, with a
`transform` processor.

Turn them on with `telemetry` in the `nexus --config` menu (question 5 above), or with `--tool-details`:
- `all`: `TELEMETRY_TOOL_DETAILS=on` in `.env`. Every persona that sends includes them, a persona added later too.
  One persona can't be left out then. To leave one out, turn them off for all, then on for each of the others:
  ```bash
  nexus --config --yes --tool-details all:off --tool-details claude-team:on
  ```
- per persona: `"tool_details": true` in `personas.json`, for a persona that sends. When it stops sending, its tool
  details go too.

Turning the collector off (`none`) turns tool details off for every persona. They stay off when you set a collector
up again.

To apply them, restart a Claude persona, as `nexus --config` says:
```bash
nexus <name> restart
```
A grok persona needs no restart: its managed config is rewritten in place, and its next `grok` has them.

#### Tool content

Off by default, and set on its own, like tool details: with `telemetry` in the `nexus --config` menu (question 6
above), or with `--tool-content all:on` or `--tool-content NAME:on`, as `TELEMETRY_TOOL_CONTENT=on` in `.env` or
`"tool_content": true` in `personas.json`. The same rules apply: only with telemetry, all or per persona, off again
when the collector is turned off, and a Claude persona needs a restart.

With it on, the CLIs send what the tools read, wrote and printed, each value up to 60 KB:
- Claude Code: the text a Read returned, a Bash command's output (stdout and stderr together), and what MCP tools,
  WebFetch and WebSearch returned (`OTEL_LOG_TOOL_CONTENT=1`). It goes as a `tool.output` event on the tool's
  span, so in traces only, not in events. The text a Write writes and the change an Edit makes go only with tool
  details on too. `nexus --config` says so once, when a Claude persona comes to send content without details.
- grok: each tool's input and output, the full command, and a failed tool's error message, on its events
  (`otel_log_tool_content = true` in its `[telemetry]` table). This includes the command, so with tool content on,
  grok sends its commands even with tool details off; `nexus --config` says so once. grok masks the secret shapes
  it recognises (a token's prefix, say) before sending, but a plain password in a file a tool read usually doesn't
  look like one.

This is a different kind of data from tool details: whole files go, such as a `.env` or a key a tool read, the
output of `env`, and API responses. Claude Code masks nothing in it. Masking at the collector is best effort, and
for Claude Code it has to cover span events: the `tool.output` event on the `claude_code.tool` span, attributes
`output`, `content` and `diff` (in an OpenTelemetry Collector, a `transform` processor's `spanevent` context). Keep
traces no longer than you would keep these files. Mind the size too:
- a record can reach a backend's limits. Tempo keeps the first 2 KB of an attribute by default, and Loki drops a
  record whose structured metadata is over its limit (64 KB by default);
- storage grows with every tool call.

To apply it, restart a Claude persona, as `nexus --config` says:
```bash
nexus <name> restart
```
A grok persona needs no restart: its next `grok` has it.

Check that data arrives (here with Prometheus and Loki behind the collector; the names are the OTLP ones with dots
as `_`):
- Claude: `claude_code_session_count_total{persona="<name>"}`, cost
  `sum by (persona) (claude_code_cost_usage_USD_total)`, events `{service_name="claude-code"} | persona="<name>"`.
- grok: `grok_code_session_count_total`, `grok_code_token_usage_total`, events `{service_name="grok-cli"}`. grok
  ignores the resource attributes it is given, so nexus sends the VM and persona as headers (`X-Nexus-Host`,
  `X-Nexus-Persona`), and the collector has to put them on the data; without that, grok's data carries no host or
  persona. In an OpenTelemetry Collector (contrib), as tested with 0.162.0:
  ```yaml
  receivers:
    otlp:
      protocols:
        http: { include_metadata: true }      # with the endpoint, TLS and auth you have (grpc the same)
  processors:
    resource/nexus:        # host.name on the resource, as Claude Code sends it: the label host_name
      attributes:
        - { key: host.name, from_context: metadata.x-nexus-host, action: insert }
    attributes/nexus:      # persona on each datapoint and event, as Claude Code copies it
      actions:
        - { key: persona, from_context: metadata.x-nexus-persona, action: insert }
  # both in the metrics and logs pipelines' processors, after memory_limiter; insert leaves Claude Code's own
  # values (and data without the headers) as they are
  ```
  Then `grok_code_session_count_total{host_name="<vm>", persona="<name>"}` and
  `{service_name="grok-cli", host_name="<vm>"} | persona="<name>"` work as Claude's do.

If nothing arrives, the persona works all the same: neither CLI says anything about a failed export, and a
collector that doesn't answer costs at most about 2 s when the CLI exits.
- `nexus --config`, then `telemetry`, and Enter through the questions: it tries the collector again.
- In a Claude persona: `CLAUDE_CODE_OTEL_DIAG_STDERR=1 claude -p hi` prints why an export failed (`Unauthorized`:
  the token; `unable to verify the first certificate`: the CA), and `claude -p hi --debug-file /tmp/d.log` logs
  `[3P telemetry] First metrics export: SUCCESS` or the reason.
- In a grok persona: `RUST_LOG=debug GROK_LOG_FILE=/tmp/g.log grok -p hi`, then `grep "export health" /tmp/g.log`
  (`export_failures=0 export_successes=2` is good).
- A token or CA changed on the collector: `nexus --config`, `telemetry`, give the new one, then restart the
  personas it names.

### Settings for every persona

Committed files hold the settings every persona of a flavour gets, on every VM that pulls the repo. Each is
either **defaults**, which a persona's own settings override key by key, or **enforced**, which win over them:

| File | For | Kind | How it reaches the persona |
|---|---|---|---|
| `personas/claude.json` | Claude Code: preferences (`model`, effort per model, `theme`, `outputStyle`, `tui`, …) | defaults | merged into the persona's own `~/.claude/settings.json` by nexus, each time it creates or starts the container (below) |
| `personas/claude.managed.json` | Claude Code: policy (`permissions.defaultMode`, `skipDangerousModePermissionPrompt`, `env`) | enforced | mounted read-only as the managed drop-in `/etc/claude-code/managed-settings.d/15-personas.json`, next to the image's `10-agent-image.json` (its update lock) and the statusline's `20-statusline.json` |
| `personas/claude.ask.json` | Claude Code: the mode of a persona that asks (`permissions.defaultMode`, as committed `default`) | enforced | for a persona that asks only, mounted read-only as the managed drop-in `/etc/claude-code/managed-settings.d/16-ask.json`, after `15-personas.json`, so its mode wins ([above](#asking-before-running-tools)) |
| `personas/grok.toml` | grok: everything | defaults | `nexus --config` writes it, with the statusline's `[ui.status_line]` where that's on, to `personas/<name>.grok.toml`, mounted read-only as grok's `/etc/grok/managed_config.toml` |
| `personas/notes.md` | both: the Nexus notes, what the CLI is told about the place it runs in | instructions, read in every session | `nexus --config` fills it in for each persona as `personas/<name>.notes.md`, mounted read-only as Claude Code's managed `/etc/claude-code/CLAUDE.md`, or as grok's rule `/etc/grok/rules/00-nexus.md` ([below](#the-nexus-notes)) |

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
| `personas/claude.ask.json` | `nexus <persona> restart` for each persona that asks, for the same reason |
| `personas/grok.toml` | re-run `nexus --config`: it rewrites `personas/<name>.grok.toml` in place, so the next grok started in a running persona reads it |
| `personas/notes.md` | re-run `nexus --config` (`nexus --update` does): it rewrites each `personas/<name>.notes.md` in place, so the next session of the CLI in a running persona reads it, with no restart |

- `nexus --config` writes nothing if a file wouldn't parse, since a broken one stops the CLI in every persona it
  reaches. JSON it checks itself; TOML with the VM's python3 if that is 3.11 or newer (tomllib), else in a persona
  image already there, and with neither it warns.
- `nexus --config` checks each file before it writes anything (JSON for Claude, and `claude.ask.json`, while a
  persona asks, for a mode that asks; TOML for grok, on its own and with the statusline added).
- A persona that had no such mount yet gets it when it is next created: `nexus --config` says `nexus <persona> restart`.
- Without a file, there is nothing to mount or merge. A persona's service that names a mounted one while it's
  missing won't start: nexus says to restore it (`git checkout personas/claude.managed.json`) or re-run
  `nexus --config`. A persona that asks is given `claude.ask.json` whether it is there or not, and won't start
  while it is missing or would bypass ([above](#asking-before-running-tools)).
- As committed, `personas/claude.managed.json` starts Claude Code in `bypassPermissions` mode without its warning,
  and `personas/grok.toml` sets grok to `always-approve`: every persona runs its tools without asking. For a
  Claude persona that should ask, set it to ask ([above](#asking-before-running-tools)): `claude.ask.json`'s mode
  then wins over that; for one session, `--skip-bypass-permissions` ([section 2](#2-daily-use)). For grok, change
  `personas/grok.toml`, which every grok persona gets.
- Leave out of `personas/grok.toml` what grok records for itself, such as `[privacy]` and the `[marketplace]`
  install flags: grok keeps those in each persona's own file, and reads `[privacy]` from no other.
- To see what a persona got: inside it, `claude`'s banner shows the model and effort, `~/.claude/settings.json`
  the defaults nexus merged, and `grok inspect` lists "System Managed: /etc/grok/managed_config.toml" and any
  warnings.

### The Nexus notes

Every persona's CLI is told how the place it runs in works: the Docker daemon is the VM's, so a bind-mount source
is looked up there; what is shared at the same path (its projects root and scratch dir); its port range, how to
publish, and the override for a project's fixed port; compose's prefix, the names that are global, and ddev's
shared router; cleaning up only what is its own, images included; the Docker guard, and how to see whether it is on
now; the other persona containers' secrets; Docker in Docker; the Python, APM and GitLab tools. The text is
`personas/notes.md`, committed, and short on purpose (Claude Code's guidance for a CLAUDE.md is under 200 lines).

- `nexus --config` fills it in for each persona as `personas/<name>.notes.md` (mode 644, ignored by git), on every
  run that writes, replacing `{{persona}}`, `{{cli}}` (Claude Code or Grok Build), `{{projects_root}}`,
  `{{scratch}}`, `{{ports}}` and `{{guard}}` (on or off). Any other `{{…}}` is refused, named, and nothing is
  written: a typo would reach every CLI. A missing `personas/notes.md` is refused too (`git checkout
  personas/notes.md` restores it).
- A Claude persona gets it as Claude Code's managed CLAUDE.md, `/etc/claude-code/CLAUDE.md` (read-only). Claude
  Code loads that before the user's and the project's CLAUDE.md, in every session, its subagents and an agent
  team's teammates included (not the built-in Explore and Plan agents, which load no CLAUDE.md), and a user can't
  exclude it.
- A grok persona gets it as a rule, `/etc/grok/rules/00-nexus.md` (read-only): `nexus --config` adds
  `[paths] extra_rule_dirs = ["/etc/grok/rules"]` to the end of its `personas/<name>.grok.toml`. grok loads it in
  every session, trusted folder or not, as a user rule (which grok gives the model as "follow if appropriate",
  after the project's rules). If `personas/grok.toml` has a
  `[paths]` table of its own, `nexus --config` adds none (a second would make it invalid TOML) and warns that the
  grok personas don't read the notes: add `"/etc/grok/rules"` to that table's `extra_rule_dirs`. A persona's own
  `~/.grok/config.toml` with an `extra_rule_dirs` of its own replaces the managed list (grok's `/import-claude`
  writes one), and that persona's grok then doesn't read the notes either: add `"/etc/grok/rules"` there too.
- They are written in place, so a running persona sees a change at its CLI's next session, without a restart:
  edit `personas/notes.md`, then re-run `nexus --config`. The edit is a change to a committed file:
  `nexus --update` refuses to pull over it when the update changes that file too, so commit it to your own branch
  or fork, or keep it short-lived.
- The guard's state in them (`on` or `off`) is what `nexus --config` found, but a container's guard is the one it
  was created with. After a change of `DOCKER_GUARD`, `nexus --config` says which personas to restart
  ([§4](#the-docker-guard)); until then the notes tell the CLI how to see the live state.
- A container made before the notes has none mounted: nexus warns at each start, attach and headless run that it
  was made without them, and that `nexus <name> restart` adds them.
- To see them: on the VM, `personas/<name>.notes.md`; inside a Claude persona, `cat /etc/claude-code/CLAUDE.md`
  (Claude Code's `/context` lists the memory files it loaded; whether it shows the managed one there wasn't
  checked); inside a grok persona, `grok inspect` lists `/etc/grok/rules/00-nexus.md (global, …)`.

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
mid-task. One upgrade at a time: it shares `nexus --config`'s lock. Check: `docker exec agent-<persona> claude --version`
(`grok --version` in a grok persona). A version exported in your shell beats `.env`, so `upgrade` refuses until you unset it.
Coming from a checkout older than `upgrade`, re-run `nexus --config` once, so the grok image tags follow
`GROK_VERSION` (`upgrade` warns until then).

By hand, the same for Claude: set `CLAUDE_VERSION=X.Y.Z` in `.env` (or
`nexus --config --yes --claude-version X.Y.Z --no-build`), `nexus <persona> build` once per context, then
`nexus <persona> restart` for every Claude persona. Left on a channel (`latest`, `stable`), a build is cached once
made and needs `nexus <persona> build --no-cache`, which rebuilds every layer; `upgrade` pins an exact version
instead.

- APM is in every image: set `APM_VERSION=X.Y.Z` in `.env` (current:
  `curl -fsSL https://pypi.org/pypi/apm-cli/json | jq -r .info.version`), build one persona of each flavour and
  context, and restart all personas. Only the APM stage reruns. `apm self-update` is off in the image and says
  so. `APM_VERSION` pins `apm-cli` only. Its Python libraries are resolved when the image is built, so two builds of
  one version can differ, and the built image is the real pin.
- A project that pins another APM version than the image's (its CI does, say): `nexus-apm X.Y.Z …` runs that
  version where `apm` would, e.g. `nexus-apm 0.34.0 install`; plain `apm` stays the image's. The first run of a
  version installs it, with uv from PyPI, into the persona's home (`~/.local/share/nexus/apm/X.Y.Z`, about 90 MB,
  plus uv's cache in `~/.cache/uv`), with its self-update and its "new version" notice off as in the image; later
  runs start it at once. That is once per persona and version; `rm -rf ~/.local/share/nexus/apm/X.Y.Z` removes one.
  Versions before 0.9.0 are refused: they have no switch to turn the self-update off, which would put APM into
  `~/.local/bin`, over the image's. `uvx --from apm-cli==X.Y.Z apm …` runs one as it is; never run its `self-update`.
  A version is kept only once its `apm --version` runs: one whose switch has moved, or whose dependencies (they come
  at their newest) keep it from starting, is refused, and nothing is kept. The home volume outlives the image: a
  version installed on another Python (an image on a newer Debian) is installed again at its next run, saying
  `nexus-apm: <dir> isn't a venv on this image's python3.11 (made on another Python?): installing it again`.
- uv and glab are pinned in the Dockerfile, not in `.env`: `UV_VERSION` with `UV_SHA256_AMD64` and
  `UV_SHA256_ARM64` (from the release's `uv-<arch>-unknown-linux-gnu.tar.gz.sha256` files), and `GLAB_VERSION` with
  `GLAB_SHA256_AMD64` and `GLAB_SHA256_ARM64` (from the release's `checksums.txt`). A version and its two hashes
  change together: a build checks its own architecture's hash, and stops when that one doesn't match:
  `<file> isn't the one pinned here: UV_VERSION and UV_SHA256_* change together`. A bump is a change to the
  Dockerfile: commit it, and `nexus --update` offers the builds and restarts on each VM that pulls it.
- ddev: set `DDEV_VERSION` to the VM's `ddev version` (re-running `nexus --config` does), build one persona of each
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
`nexus <persona> restart`. To turn it on or off for a persona, re-run `nexus --config` and change that persona.

- The line stays blank in a project until you accept Claude Code's workspace trust prompt there (once per
  project per persona).
- `Ctx used/size`: the size is the auto-compact window, which Claude Code doesn't send, so the script resolves it
  as Claude Code does: `CLAUDE_CODE_AUTO_COMPACT_WINDOW`, then `autoCompactWindow` in the settings files (where
  `/autocompact` saves it: the persona's `~/.claude/settings.json`, `modelSettings.<model>`). With none, or
  auto-compact off, it is the model's window. It can't see `--settings`, `--setting-sources` or a window
  Anthropic's servers set. grok sends the window it uses (`/context-window`), and compacts at
  `[session] auto_compact_threshold_percent` of it.
- The script needs python3 (it has no jq path, whatever its message says). The image installs python3 itself, for
  the statusline, APM and a project's venvs; without it the line reads "Claude Code — statusline needs python3 (or jq)".
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

- **CONTEXT** is used/size, coloured as on the statusline (yellow from 50%, red from 80%). For Claude Code the
  size is the auto-compact window where one is set ([section 7](#7-statusline)), else the model's. **5H** and **7D** are
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

## 9. Test a change to nexus (or `nexus --config`)

`tests/run.sh` runs `nexus --config` and nexus end to end in a scratch copy of the checkout, your uncommitted changes
included, on throwaway personas. Nothing of your setup is read or changed, and what it makes is removed at the end.
```bash
/opt/agents/tests/run.sh                  # about two minutes: ok or FAIL per check, a summary, exit 1 on a failure
/opt/agents/tests/run.sh --no-containers  # a minute or two: lint, the goldens, nexus --config and nexus, no persona
```
- Starting personas needs a Claude persona image built for your uid from this checkout (the newest
  `agent-claude-docker` here, or `--image REF`; one built before the session words fails the stages "the image's
  claude" and "permissions", so `nexus <persona> build` first), `ssh-agent` and `script`; a stage it can't run is
  skipped, saying why. There is no real sign-in: Claude Code talks to a stand-in API (`tests/fake-api.py`) and grok
  is a stand-in (`tests/fake-grok`), so it tests nexus and `nexus --config`, not a CLI release.
- Its stage "in a persona: its scratch dir, the Nexus notes, the docker guard, compose project names, tools" checks,
  in the test personas, that a container a persona starts sees its scratch dir; that each CLI gets its notes (a
  headless run's request to the stand-in API holds them); the guard's labels and refusals (another persona's
  container or volume, mounting that volume, one made outside, persona images, another persona's image or compose
  build, a tag onto its name), its filtered prunes, a `docker system prune -af --volumes` against a stand-in daemon
  (`tests/fake-dockerd.py`: every prune filtered, the build cache's refused, nothing real pruned), a guard that
  dies started again, and `DOCKER_GUARD=off`; compose's prefix, with two personas `up` on the same project at once,
  each its own stack, and one refused taking the other's down, also for a project with a fixed host port and an
  `!override` file; and `python3 -m venv`, uv, glab and its git helper (with fake tokens), and nexus-apm (installing
  0.9.0 is skipped when PyPI can't be reached). A check of something the image was built before is skipped, saying so:
  `nexus <persona> build` first. Its test personas' scratch root is under the run's own scratch dir, never
  `/srv/nexus-scratch`.
- It lints with shellcheck and hadolint if they are installed or their images are here; it never pulls them.
- Its goldens stage compares what the tools print, call and write with the scenarios in `tests/golden`, recorded
  from the bash tools, or from the Python where the behaviour is new (`tests/golden/ODDITIES.md` lists where they
  differ on purpose). The unit tests, in `tests/unit`, are plain unittest: `python3 -B -m unittest discover -s
  tests/unit`. CI also runs them with pytest, which the tools themselves don't need:
  `pip install --user --no-deps --require-hashes -r tests/requirements-dev.txt`, then
  `python3 -B -m pytest -p no:cacheprovider tests/unit`; on Python 3.8, and the runner's newer one.
- Its personas are `nxt-<id>-c`, `-g`, `-n` and `-x` (`<id>`: the run's pid and a random number), in compose
  project `agents`, so compose warns that your real personas are orphans; that is only a warning. `--keep` leaves
  it all for a look after a failure, and says what.

### CI: the secret scan

On GitHub, every push and pull request runs `.github/workflows/ci.yml`: the unit tests on Python 3.8,
`tests/run.sh --no-containers` (lint, the goldens, `nexus --config` and nexus without a persona), and `tests/secrets.sh`,
which uses gitleaks to look for keys, tokens and passwords in two places:

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

## 10. Update a VM

`nexus --version` prints the commit this checkout is at, its date and branch, and whether it has changes of its
own, for example `nexus 8398321 (2026-10-22, main)`.

```bash
nexus --update        # pull, rewrite what nexus --config generates, then offer the builds and restarts it needs
```
It does three things, in this order:
1. **Pull.** `git fetch`, then a fast-forward of the checkout's branch, after listing the new commits. It stops,
   changing nothing, if:
   - the branch has commits of its own;
   - you've changed files that the update also changes;
   - the checkout isn't on a branch (after a rollback, say).

   Each message says what to do.
2. **Rewrite the generated files.** The newly pulled code runs `nexus --config --yes --no-build --no-link`, which
   keeps every setting.
3. **Offer to apply it.** It lists what applying the update takes and asks once:
   - the images to build (or pull, with a registry), when the Dockerfile, `compose.yml` or a file the Dockerfile
     copies in changed;
   - the running personas to restart, each with its reason (its service, its image, or a settings file it reads
     at start such as `personas/claude.managed.json`) and the tmux sessions the restart ends;
   - the stopped personas, which pick the change up at their next start.
```text
nexus: to apply c261ba8..bf5590c to the personas:
  build agent-claude-ddev: Dockerfile changed
  build agent-claude-docker: Dockerfile changed
  restart claude-ddev: its image, if the build changes it  (ends its sessions: billing review)
  claude-docker isn't running: its next start picks it up, once its image is built
Apply it now? [y/N]
```
`-y` applies without asking. With no terminal it applies nothing, and lists each build and restart on its own.

**What isn't applied is kept** in `update.json` in the checkout (git ignores it), with the commit the personas
were brought up to. Every later `nexus --update` offers it again, even when there's nothing new to pull, until it
is done:
```text
nexus: /opt/agents is up to date: bf5590c (2026-10-22) …
nexus: not applied yet, from an earlier nexus --update: c261ba8..bf5590c. To apply it to the personas:
```
Each step comes off the record as soon as it's done, so a run that fails halfway offers only the rest.

The record follows what the personas actually do in the meantime:
- A persona you restart yourself (`nexus <persona> restart`, or a stop and start) drops off for every reason
  given before that restart.
- A persona that's stopped drops off, because its next start picks the change up.
- A restart for its image is offered only while the container runs an older image than the one its image name
  now points to. While a build is still to do, that restart is offered as "if the build changes it", and the
  check comes after the build, so a build that changed nothing restarts nobody.
- A build you run yourself (`nexus <persona> build`, or an `upgrade`) doesn't come off the record. The next
  `nexus --update` offers it again, which takes seconds when nothing changed, then restarts only the personas
  whose image did change.

A `git pull` of your own is covered too: the next `nexus --update` applies everything from the recorded commit to
the current one. The record starts at the first `nexus --update` on a VM. For a pull made before that, run
`nexus --update --since ORIG_HEAD` once.

By hand, the same pull and rewrite are:
```bash
git -C /opt/agents pull
/opt/agents/nexus --config --yes --no-build     # right away, while no persona is being started
```
Running the installer again works too:
`curl -fsSL https://nexus.light-and-dark.io/install.sh | bash -s -- --yes --no-build`. It pulls with `--ff-only`,
on any clone of this repo over https or ssh, but it doesn't offer the builds and restarts.
`--yes` asks nothing and keeps every setting, what `.env` and `personas.json` have. `nexus --config` writes the files
it generates from them again, and its closing lines name any persona that needs `nexus <persona> restart` for what
it wrote: a setting changed, a mount added, or a service in `compose.personas.yml` that wasn't what `personas.json`
gives (edited by hand, say).

**Asking before running tools** needs nothing at the update: after the pull, `nexus --config --yes --no-build`
changes nothing for it (every persona still runs its tools without asking, and the files it writes are as
before), and setting a persona to ask works on any image. The session words, `--skip-bypass-permissions` and
`--bypass-permissions` ([section 2](#2-daily-use)), need each Claude image built once from the new checkout:
`nexus <persona> build` (with a registry, `pull`) once per context, then `nexus <persona> restart` for each
Claude persona; or the next `nexus <persona> upgrade`, which does both. Until then a word for a new session or a
headless run is refused with `nothing was started: <persona>'s image was built before nexus had …`, even when
it is the persona's own mode; without a word nexus works as before.

**The scratch dir, port ranges, the Nexus notes and the Docker guard** (section 4) come with an update like this:
1. The shared scratch root first. nexus starts, restarts and upgrades no persona until it is there, so right after
   the pull each of those is refused with the command that makes it, and so is an attach or a headless run that
   would start one; to a persona that runs, those only warn (`stop`, `logs`, `build` and `pull` don't check). The
   update's own `nexus --config --yes` tries `sudo -n install -d …`, which works only with passwordless sudo;
   otherwise its note gives the command, and the update holds back the restarts (`nexus: but restarting … needs
   the shared scratch root /srv/nexus-scratch, which isn't there: sudo install -d …`). It still does the builds
   with `-y`, or on a terminal when you answer `y` to `Do the builds now (no persona is restarted)? [y/N]`. The
   restarts are kept: run the command once, then `nexus --update` again, which offers them:
   ```bash
   sudo install -d -o $(id -u) -g $(id -g) -m 755 /srv/nexus-scratch
   ```
2. The rewrite gives every persona a port range in `personas.json`, a notes file (`personas/<name>.notes.md`),
   and a service with its scratch dir, `NEXUS_PORTS` and the notes mounted; a grok persona's config gains the
   `[paths]` table. For each persona it says so:
   ```text
   claude-ddev: its service has the shared scratch dir, its port range and the Nexus notes now; `nexus claude-ddev restart` applies them (that ends its tmux sessions)
   ```
3. It offers to build every image (the Dockerfile changed: the guard, the compose shim, nexus-apm, uv, glab,
   python3-venv) and then to restart each running persona, for its service and its image. Until its restart, a
   persona runs as before, without the guard or the compose prefix, and nexus warns at each start and attach that
   its container was made without its scratch dir and the Nexus notes.
4. After the restart, the guard is on (`DOCKER_GUARD=off` in `.env` keeps it off: set it before the restarts), and
   compose names projects `<persona>-<name>`. Stacks started before are under their old names, unlabelled:
   [below](#stacks-started-before-the-prefix-and-the-guard).

After a `git pull` of your own instead, `compose.personas.yml` is out of step until `nexus --config --yes` writes it
again ([below](#out-of-step-with-personasjson)): nexus creates no container from it meanwhile (a running persona is
attached to, with a warning). Then the scratch root as in 1., and the builds and restarts by hand: `nexus <persona>
build` once per flavour and context (with a registry, `pull`), then `nexus <persona> restart` for each persona.

**From before `nexus --config`:** after the `git pull`, `config-tools` is gone; `nexus --config --yes --no-build`
does what `config-tools --yes --no-build` did, with the same flags. A `compose.personas.yml` config-tools wrote is
still in step (only its comments name it), so nothing refuses meanwhile; the next run writes it, and each grok
persona's config, with the comments of today, and no restart is asked for that.

### Stacks started before the prefix and the guard

A compose stack a persona started before the update keeps its old project name, the bare one (`shop`), and its
containers, volumes and networks carry no `nexus.persona` label. In the persona after its restart:
- `docker compose ps` in that directory shows nothing: it looks for `<persona>-shop`. `docker compose ls` lists the
  old name; `docker compose -p shop ps` shows the stack.
- `docker compose up -d` starts a second copy, `<persona>-shop`, with new, empty named volumes, which then
  collides with the old one on its ports and on any fixed `container_name:`.
- The guard refuses stopping or removing any of the old stack from the persona, `-p shop down` included: none of
  it is labelled.

So once, for each such stack, on the VM (where there is no guard), take it down by its old name:
```bash
cd ~/projects/shop && docker compose -p shop down        # its named volumes stay; -v would delete them
```
then `docker compose up -d` in the persona starts it as `<persona>-shop`. Its data is in the old project's
volumes (`shop_<volume>`), not the new ones: to keep it, copy it over before the persona's first `up`, or go on
naming the old project from the persona (`docker compose -p shop up -d`): new containers, labelled, on the old
volumes, which stay unlabelled (the guard doesn't check those when it mounts them), so removing them is for the VM
too. A ddev project keeps its name (ddev doesn't
get the prefix), but its containers are unlabelled all the same, so ddev in the persona can't stop or recreate
them: once, on the VM, `cd /tmp && docker compose -p ddev-<site> down` (`-p` with no compose file finds the
project by name; `docker compose ls -a` lists it, stopped or not), then `ddev start` in the persona. Anything else
started before the guard (a `docker run`, a network, a volume) is removed on the VM as well: `docker rm -f …`,
`docker volume rm …`.

### From before personas.json

A checkout from before `personas.json` (`config.sh`'s, or `install.sh`'s) keeps the personas as `# persona:` lines
in `compose.personas.yml`. Until they are moved, every nexus command but its usage refuses:
```text
nexus: the personas are still in /opt/agents/compose.personas.yml, from before personas.json: run /opt/agents/nexus --config --yes (it keeps every setting)
```
A cron job that starts a persona, or runs a headless prompt, fails the same way: let it wait for the move.

The first `nexus --config --yes` after the `git pull` moves them: it reads the lines once, writes `personas.json`
from them, and generates `compose.personas.yml` from that. It keeps the old file as `compose.personas.yml.bak`, or
`.bak.2`, `.bak.3` … if a `.bak` is there already, so an older backup survives, and says so:
```text
personas: moved from compose.personas.yml's `# persona:` lines into /opt/agents/personas.json; the old file is kept as compose.personas.yml.bak
```
For each persona whose service came out different (an older layout's, from before the statusline and Claude
settings splits, or one edited by hand; comments don't count) it adds a note; the other personas run on as they are:
```text
claude-ddev: its service is written from personas.json now, a little differently; `nexus claude-ddev restart` applies it (that ends its tmux sessions)
```
- A `# persona:` line it won't take (edited by hand, say) refuses the move, and nothing is written: the message
  quotes the line and says what a line takes. Fix the line, then run it again.
- `nexus --config --list` shows the personas from the lines, and writes nothing.
- nexus no longer has refusals of its own for the older layouts: the move rewrites them too.

### Out of step with personas.json

`compose.personas.yml` is generated from `personas.json`, and nexus checks that it still is: it generates it again
and compares. One edited by hand, or written by an older `nexus --config` before a `git pull` changed what it
generates, is out of step, and nexus won't create a container from it. `nexus <persona>`, `start` or `headless`
for a persona that isn't running, `restart`, and `upgrade` refuse:
```text
nexus: /opt/agents/compose.personas.yml is out of step with personas.json (edited by hand, or written by an older nexus --config): /opt/agents/nexus --config --yes writes it again from personas.json
```
Attaching to a running persona only warns, with the same message and "then `nexus <persona> restart`" after it:
its container was created from the file as it was. `start` and `headless` for a running persona, and `stop`,
`logs`, `build` and `pull`, don't look. A missing file is refused the same way, with
`…/compose.personas.yml isn't there: …/nexus --config --yes writes it from personas.json`.

Run `nexus --config --yes`: it names each persona whose service it changed back, to `nexus <persona> restart`. A
hand edit of `compose.personas.yml` doesn't survive that: the file is what `personas.json` gives.

### Roll back to the bash tools

1. `git -C /opt/agents checkout bash-final`: the tag bash-final is main as it was before the Python tools came
   in, the last commit with the bash `nexus` and `config.sh`. The launcher link `/usr/local/bin/nexus` stays as
   it is.
2. Put back the file the move kept, which still has the `# persona:` lines:
   `cp /opt/agents/compose.personas.yml.bak /opt/agents/compose.personas.yml` (or the `.bak.N` its note named).
3. Personas added or changed since the move aren't in it: set them up again with `/opt/agents/config.sh`, as the
   setup tool is called there. `personas.json` can stay: the bash tools ignore it (`git status` lists it as
   untracked there).

Back on the Python tools (`git checkout main`), `# persona:` lines in `compose.personas.yml` win over a
`personas.json` beside them: nexus refuses as before the move, and the next `nexus --config --yes` moves them again.

## Troubleshooting

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| `no personas set up yet: run …/nexus --config` | fresh checkout | run `nexus --config` |
| `no persona '<name>' (set up: …)` | typo, or not added yet | use a listed name, or add it with `nexus --config` |
| `required variable SSH_AUTH_SOCK is missing a value` | no ssh-agent found | start one / `ssh -A`, `ssh-add`, then retry |
| `warning: the ssh-agent at … holds no keys` | agent without your key (often gpg-agent's) | `ssh-add`, then `nexus <persona> restart` |
| `warning: nothing answers at … (stale ssh-agent socket)` | the agent behind that socket is gone | reconnect with `ssh -A`, then `nexus <persona> restart` |
| git over ssh fails inside after you reconnected | the container still has the old login's socket | `nexus <persona> restart` |
| `git pull` inside asks for a password, or says `Permission denied (publickey)` | your agent: gone or empty; a key of its own: not added on the git host | inside, `ssh-add -l`: "Could not open a connection" → `nexus <persona> restart` from a shell where `ssh-add -l` works on the VM; "no identities" → `ssh-add` on the VM. A key of its own: add `personas/<name>.key.pub` on the host ([§5](#an-ssh-key-of-its-own)) |
| `<persona>'s ssh key … isn't there` | its key file was moved or deleted | put it back, or re-run `nexus --config`: SSH `new` makes a new one (add it on the git host again), `none` or `agent` drop the key |
| `Permission denied (publickey)` at once, in a persona without ssh | that is what `none` does | pull and push from the VM ([§5](#no-ssh-commit-inside-push-from-the-vm)), or give it a key |
| `nexus --config`: `--ssh …: others can read it`, `… has a passphrase`, `… isn't yours` | ssh inside wouldn't use that key | `chmod 600` it; a passphrase: `ssh-keygen -p -N "" -f KEY` drops it (or use `new`); someone else's: copy it as yours |
| Persona not running after a VM reboot | its ssh-agent socket didn't exist at boot | `nexus <persona>` (or `nexus <persona> start`) |
| `the input device is not a TTY` | `nexus <persona>` attaches; a script has no tty | `nexus <persona> start` |
| `<persona> isn't running` | `nexus <persona> ls` on a stopped persona | `nexus <persona>` or `nexus <persona> start` |
| `env file …/personas/<persona>.env not found` | deleted, or the persona was added by hand | re-run `nexus --config` (it writes a new one) |
| `the projects root must be an absolute path` / `can't create the projects root …` | a bad `PROJECTS_ROOT` in `.env` or your shell, or a bad own root | the message says which: fix it, or re-run `nexus --config` |
| `… is outside <persona>'s projects root; starting in …` | you ran `nexus` from a directory that persona doesn't mount | `cd` into its root, or give the persona that root (section 5) |
| `telemetry: nothing answered at …` / `… refused the token (HTTP 401)` / `… has a certificate that … doesn't vouch for` / `… doesn't speak TLS (HTTPS) there` / `… answered a test export (HTTP 302), but not as an OpenTelemetry collector does` (`nexus --config`) | the collector is down or the address is wrong / the token is wrong / the CA given isn't the one its certificate is signed by / the port is plain HTTP: give `http://` / that address redirects (a login page, say): give the collector's own | fix it with `nexus --config`, then `telemetry` ([§5](#telemetry-opentelemetry)); the personas work meanwhile, but what they send is lost |
| `… sends telemetry now, but its container was made before that` / `… no longer sends telemetry, but its container was made while it did` | its telemetry changed while it ran | `nexus <persona> restart` (ends its sessions) |
| `… sends telemetry, but no collector is set` / `TELEMETRY_CA … names personas/telemetry-ca.pem, which isn't …` / `the collector's CA file … holds a private key as well` / `… sets NODE_EXTRA_CA_CERTS, and …'s telemetry drop-in would replace it` | a persona was turned on without a collector / the CA copy was deleted or replaced / a key+certificate file was given / a CA of its own for Claude | `nexus --config`, then `telemetry`: give the collector, or the CA certificate alone; for its own CA, put both in one PEM file and give that |
| `<persona> still runs with the projects root it was created with, …` | its root changed (`nexus --config`, or `PROJECTS_ROOT`) while it ran: a running container keeps the root it was created with | `nexus <persona> restart` (ends its sessions); to finish one first, `nexus <persona> <session>` attaches to it |
| `OCI runtime exec failed: … chdir to cwd …: no such file or directory` | an older nexus, attaching to a persona whose root changed while it ran | `nexus <persona> restart`; `git pull` brings the nexus that says so |
| `no Docker socket at /var/run/docker.sock` | Docker isn't installed or running | install / start Docker |
| `permission denied … docker.sock` | your user isn't in the `docker` group | `sudo usermod -aG docker "$USER"`, log in again |
| `range of CPUs is from 0.01 to N` | `AGENT_CPUS` above the VM's vCPUs | set `AGENT_CPUS` ≤ `nproc` (`nexus --config` caps it) |
| `pull access denied for local/agent-…` | `REGISTRY=local` means "build here" | `nexus <persona> build`, or set `REGISTRY` |
| `At least one invalid signature was encountered` (apt, during a build) | Docker's disk is full | `docker system df`; free space with `docker builder prune` / `docker image prune` on the VM (in a persona, the guard narrows or refuses them), then build again |
| After `git pull`: `config.sh: No such file or directory`, or `config-tools: …` | the setup tool was renamed `install.sh` → `config.sh` → `config-tools` → `nexus --config` (`install.sh` is now the one-line install: in a checkout, it pulls and runs `nexus --config`) | run `nexus --config` (same flags); coming from `config.sh`, `nexus --config --yes --no-build` first ([section 10](#10-update-a-vm)) |
| `install: /opt/agents is there already, and isn't a checkout of …` (or `is a checkout of …, not of …`) | the installer found a directory it didn't make, or a clone of another repo; it changed nothing | move it away, or put the checkout elsewhere: `curl … \| AGENTS_DIR=/srv/agents bash` |
| `install: run this as the VM user who will use the personas, not as root or with sudo …` | `curl … \| sudo bash`, or a root shell | run it as yourself: `curl … \| bash` (it uses sudo only to create `/opt/agents`) |
| After `git pull`: `nexus: command not found`, or `…/agent: No such file or directory` | the launcher was renamed `agent` → `nexus` | run `<checkout>/nexus --config --yes --no-build` by its path (it links `nexus`, and removes the old link); or run `<checkout>/nexus` |
| `nexus: the personas are still in …/compose.personas.yml, from before personas.json: run …/nexus --config --yes (it keeps every setting)` | a `git pull` brought the tools that read `personas.json`, and the personas haven't been moved yet; or `# persona:` lines are back after a rollback | `…/nexus --config --yes --no-build` ([section 10](#from-before-personasjson)), then `nexus <persona> restart` for each persona it names |
| `nexus --config`: `compose.personas.yml: persona '<name>' has a bad line (edited by hand?): …` | a `# persona:` line the move won't take | fix that line as the message says (it lists what a line takes), and run it again; nothing was written |
| `nexus: needs Python 3.8 or newer; python3 here is …` (`config:` from `nexus --config`) | the VM's python3 is older than 3.8, or missing | install Python 3.8 or newer (Ubuntu 20.04 has 3.8) |
| New Claude version not showing after a build | container not recreated, or a cached channel build | `nexus <persona> restart`; or `nexus <persona> upgrade <that version>`, which pins, builds and recreates (plain `upgrade` moves to the newest) |
| `nexus --config or another upgrade is running in …` (nexus), ``another `nexus --config`, or a … upgrade, is running in …`` (`nexus --config`) | a `nexus --config` or `nexus <persona> upgrade` is still running in that checkout; they share one lock | let it finish, then run it again |
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
| `ddev: command not found` inside | a docker-context persona | change it to context `ddev` with `nexus --config` |
| `nexus: …/compose.personas.yml is out of step with personas.json (edited by hand, or written by an older nexus --config): …` | the file was edited by hand, or an older `nexus --config` wrote it before a `git pull` changed what it generates (a pull that brought the scratch dir, port ranges and notes does); or `personas.json` was edited by hand | `…/nexus --config --yes` (`nexus --update` runs it for you), then `nexus <persona> restart` for a running persona ([section 10](#out-of-step-with-personasjson)) |
| `nexus --config`: `personas/grok.toml isn't TOML grok can read` | a typo in your grok settings (the message names line and column) | fix it and re-run; nothing was written |
| `nexus --config`: `personas/claude.json isn't JSON Claude Code can read` (or `claude.managed.json`) | a typo in the Claude settings (the message names line and column) | fix it and re-run; nothing was written |
| A change to `personas/claude.json`, `claude.managed.json` or `claude.ask.json` doesn't show in a persona | the defaults merge as the container starts, and the managed files are mounted on their own | `nexus <persona> restart` |
| A default from `personas/claude.json` doesn't reach a persona | the persona has its own value for that key in `~/.claude/settings.json` (set there, by `/config`, or there before), or took the key out | set it there; or take it out of `~/.claude/.nexus-defaults.json` too (or delete that file), then `nexus <persona> restart`, and the default comes back |
| `…/personas.json is missing, and …/compose.personas.yml was generated from it: put back …/personas.json.bak (the one nexus --config kept before it)` | `personas.json` was deleted | `cp personas.json.bak personas.json` in the checkout: it is the one from before `nexus --config`'s last change, so check it with `nexus --config --list`, then `nexus --config --yes`. Without a `.bak` the message says to remove `compose.personas.yml` and set the personas up again |
| `…/personas.json isn't valid JSON (…)`, `… is version 2; this checkout reads version 1`, `… has '<key>' twice`, `… persona '<name>' has …` | a hand edit the tools won't take | fix it as the message says, or put back `personas.json.bak` (the message names it when there is one); then `nexus --config --yes` ([section 5](#personasjson)) |
| `nexus: warning: <persona>: its ~/.claude/settings.json is not valid JSON` | that file is broken | fix it inside the persona; nexus merges the defaults at the next restart |
| `…/personas/claude.ask.json isn't there, and <persona> asks before running tools` (or `can't be read`, `isn't JSON Claude Code can read`, `sets no permissions.defaultMode`, `sets permissions.defaultMode to bypassPermissions`), from `nexus --config` or nexus | a persona is set to ask, and the file that makes it ask is gone, broken, or would bypass: `nexus --config` wrote nothing, nexus didn't start it | `git checkout personas/claude.ask.json`, or give it a `permissions.defaultMode` that asks (`default`, `acceptEdits`, `plan`, …); or let the persona bypass: `--permissions <persona>:bypass` ([§5](#asking-before-running-tools)) |
| `nexus: warning: <persona> asks before running tools now, but its container was made before that and doesn't: …` | it was set to ask while it ran: its container has no `16-ask.json` yet, and still bypasses | `nexus <persona> restart` (ends its tmux sessions) |
| `nexus --config`: `--permissions …: it takes NAME:ask or NAME:bypass`, or `… no persona <name> (add it with --persona)` | a typo, or a persona that isn't set up | `--permissions <name>:ask` or `<name>:bypass`, for a persona `--list` shows or one `--persona` adds in that run |
| `--skip-bypass-permissions is for claude personas: <persona> is a grok one, …` (or `--bypass-permissions`); `nexus --config`: `--permissions <persona>:ask: <persona> is a grok persona, …` | grok takes its approval mode from `personas/grok.toml` | leave the word out; change `personas/grok.toml`, which every grok persona gets |
| `pick one of --skip-bypass-permissions and --bypass-permissions` | both words given | give one |
| `--skip-bypass-permissions is for a session or a headless run, not for <command>` (or `--bypass-permissions`) | a word with `ls`, `stop`, `logs`, `build`, `pull`, `start`, `restart` or `upgrade` | leave it out there: it goes with `nexus <persona> [session]` or `headless` |
| `nothing was started: <persona>'s image was built before nexus had --skip-bypass-permissions` (or `--bypass-permissions`) | its image was built before the session words: its `claude` is Claude Code itself, not `claude-mode.sh` | `nexus <persona> build` (with a registry, `pull`: the message says which), then `nexus <persona> restart` (ends its tmux sessions) ([section 10](#10-update-a-vm)) |
| `session <name> already runs its tools without asking (…)`, or `… already asks before running tools (…)` | that session started in the other mode (with the other word, or in the persona's own mode), and keeps it | end it, or name a new session: `nexus <persona> --skip-bypass-permissions <new>` (or `--bypass-permissions`) |
| `session main is <persona>'s shell, in its own mode (…)` | `main`, the persona's own shell session, always runs in the persona's mode | name a new session; or change the persona's setting (`nexus --config`, change), then `nexus <persona> restart` |
| `can't read the mode session <name> runs in: …` | nexus couldn't read that session's tmux environment | name a new session |
| `can't read <persona>'s permission settings inside it (exit N); …` | the look inside the persona before a session word failed, or took over 10 s | try again, or `nexus <persona> restart` |
| `duplicate session: <name>`, with a session word | a session of that name was made since nexus looked, or a name with `#` or `\`, which tmux rewrites, matched one | name another session |
| A headless run of a persona that asks, or given `--skip-bypass-permissions`, doesn't use the tools it needs | `claude -p` can't get approval from anyone, so tools that need it are denied | `--bypass-permissions` for that run, or allow them with `--allowedTools` (section 3) |
| Claude doesn't ask in a session started with `--skip-bypass-permissions` | a flag typed after `claude` wins (`--dangerously-skip-permissions`, `--permission-mode …`): the word sets where Claude starts, not a lock | start `claude` without that flag |
| grok's status line row stays empty | grok shows its welcome screen (the row comes with the first prompt), statusline off, or the persona's own `~/.grok/config.toml` sets `[ui.status_line]` | start a session; turn it on with `nexus --config`; or remove that section there |
| No statusline in Claude | workspace not trusted yet, or statusline off for this persona | accept the trust prompt (section 7); else turn it on with `nexus --config` and restart |
| `nexus: headless: give the prompt first …` | no prompt, and nothing piped in (or the first argument is a one-word flag) | `nexus <persona> headless "PROMPT" [FLAGS…]`, or pipe the prompt in |
| `nexus: warning: headless: no input on stdin within 3 s …` | the caller (a CI runner, an agent's shell) left stdin open with nothing in it | give `</dev/null`; or, if input was meant, start it sooner |
| A headless run with no prompt never starts | it waits for its input to end, and the pipe stays open | close the pipe, or give the prompt as the first argument |
| `nexus: headless: … is outside <persona>'s projects root` | headless runs only in a project of that persona | `cd` to a directory in its root (`nexus --config --list` shows the roots) |
| A headless run prints `Not logged in · Please run /login` | the persona isn't signed in | `nexus <persona> -- claude`, then `/login` (or set the key in its env file) |
| `nexus top` shows a Claude Code session with `-` everywhere | statusline off for that persona, or it hasn't drawn yet in that pane | turn it on with `nexus --config` (section 7); else work in the session once |
| `nexus top` lists no session for a running persona | no pane runs its CLI (`no claude running`) | start one: `nexus <persona> <session> -- claude` |
| `nexus --config`: `'top' is not a usable name` | `ls` and `top` are nexus commands, and `all` and `start` are reserved too | pick another name |
| `nexus: the shared scratch root /srv/nexus-scratch isn't there (…): …/nexus --config creates it, or run: sudo install -d -o $(id -u) -g $(id -g) -m 755 /srv/nexus-scratch; or point NEXUS_SCRATCH_ROOT (.env, or your shell) at a directory of your own`, at a start, a restart or an upgrade, or an attach or a headless run that would start the persona; as `nexus: warning: …` ending `(<persona> runs on as it is; starting or restarting it is refused until then)` at an attach or a headless run to one that runs | `nexus --config` couldn't make it (no sudo, or `--yes` and `nexus --update`, which don't ask for a password), or `NEXUS_SCRATCH_ROOT` names a new place | run that command once; or `nexus --config` on a terminal, which asks for the password ([§1](#1-first-time-setup-on-a-vm)); or set `NEXUS_SCRATCH_ROOT` to a directory of yours |
| `nexus: the shared scratch root … isn't a directory (…): move it aside (nexus --config then creates the root), or point NEXUS_SCRATCH_ROOT …`; `nexus --config`: `scratch: the shared scratch root … isn't a directory, and every persona needs it to start: …` | something else, a file say, is where the root should be | move it aside, then `nexus --config`; or set `NEXUS_SCRATCH_ROOT` to another place |
| `nexus --config`: `scratch: couldn't create the shared scratch root …, which every persona needs to start; run: sudo install -d …` | the same, at setup: the settings are saved, only the root is missing | run the command it gives |
| `nexus --update`: `nexus: but restarting … needs the shared scratch root …, which isn't there: sudo install -d …` (or `which isn't a directory: …`, `which you can't write in, to make a persona's dir: …`) | the update couldn't make the root, so it holds back the restarts; the builds still go, with `-y` or when you say so | run that command (or move the file aside), then `nexus --update` again: it offers the restarts it kept ([section 10](#10-update-a-vm)) |
| `nexus: can't create <persona>'s scratch dir …/<persona> (Permission denied): you can't write in the shared scratch root …; make it yours: sudo install -d …`; `nexus --config`: `scratch: you can't write in the shared scratch root …, so nexus can't make a persona's dir in it; run: sudo install -d …` | the root isn't yours (made as root by hand, say) | `sudo install -d -o $(id -u) -g $(id -g) -m 755 <root>`: on a root that is there, it makes it yours; or set `NEXUS_SCRATCH_ROOT` to another place |
| `nexus: the shared scratch root must be an absolute directory other than /, …`; `nexus --config`: `NEXUS_SCRATCH_ROOT in … must be …` or `DOCKER_GUARD in … is neither on nor off …, so nothing was written` | a hand edit of `.env`, or a value exported in your shell | fix it as the message says (`DOCKER_GUARD`: `on` or `off`, exactly; empty is on), or unset it in your shell |
| `nexus: warning: <persona>'s container was made without its scratch dir … and the Nexus notes: …` (or one of the two) | its container is from before this release, or from before `NEXUS_SCRATCH_ROOT` moved | `nexus <persona> restart` (ends its sessions) |
| `nexus: …/personas/<persona>.notes.md isn't there: re-run …/nexus --config, which writes it` | it was deleted, or the checkout was pulled without the config run | `nexus --config --yes` |
| `nexus --config`: `…/personas/notes.md has {{…}}, which nexus doesn't fill in …` or `… can't be read …, so nothing was written` | a typo in the template, or it was deleted | fix the placeholder (the message lists the ones it knows), or `git checkout personas/notes.md` ([§5](#the-nexus-notes)) |
| `nexus --config`: `personas/grok.toml has a [paths] table of its own, so nexus --config adds none, and the grok personas don't read the Nexus notes …` | `[paths]` in your grok settings for every persona | add `"/etc/grok/rules"` to its `extra_rule_dirs`, then re-run `nexus --config` |
| A grok persona doesn't follow the Nexus notes; `grok inspect` doesn't list `/etc/grok/rules/00-nexus.md` | the persona's own `~/.grok/config.toml` sets `[paths] extra_rule_dirs` (grok's `/import-claude` writes it), which replaces the managed list; or its container is from before the notes | add `"/etc/grok/rules"` to that list there, or take the `[paths]` table out of it; else `nexus <persona> restart` |
| `Error response from daemon: nexus guard: container … isn't labelled nexus.persona=<persona> (…)` (or volume, network, image, service, secret, config, node) | the persona tried to stop, remove or otherwise change something it didn't make (another persona's, yours, the persona containers, or one from before the guard): a container (`network connect` and `disconnect` too), volume or network; a persona image, one built `FROM` one, another persona's image, or one another compose project built; or a `docker tag` or `commit` onto such an image's name | leave it, or ask the user; to do it anyway, do it on the VM. A stack from before the guard: [section 10](#stacks-started-before-the-prefix-and-the-guard) ([§4](#the-docker-guard)) |
| `… nexus guard: volume … isn't labelled nexus.persona=<persona> (it is persona …'s, and a volume's name is global on the daemon: …)`, or `container … (it is persona …'s: --volumes-from would share its volumes, …)` | `docker run -v NAME:…`, `--mount source=NAME`, `docker volume create NAME` or a compose volume (`external: true`, a fixed `name:`) names another persona's volume | name the persona's own otherwise (`<persona>-NAME`); to share the data on purpose, do it on the VM ([§4](#the-docker-guard)) |
| `… nexus guard: image … is used by container …, which isn't labelled nexus.persona=<persona> (…)` | removing an image (or tagging over its name) that a container not the persona's uses: another persona's, yours, a persona container | leave it: once no such container uses it, it can go |
| `… nexus guard: container ddev-router isn't labelled … (it is ddev's, shared by the ddev projects of every persona: …)` | pausing, updating or renaming ddev's shared router or ssh-agent | leave them: ddev only stops, restarts and removes them |
| `… nexus guard: the daemon's swarm mode isn't labelled …` or `… the daemon's plugins aren't labelled …` | `docker swarm …`, or installing, changing or removing a plugin: the daemon's, every persona's | run the stack with `docker compose`; for the rest, ask the user |
| `… nexus guard: a request body with …` (400), or `… only HTTP/1.x goes through the guard …` (505) | a program sent the API a create body the daemon would read otherwise than the guard (a key twice, one field in two spellings, not UTF-8, more after its JSON), or spoke HTTP/2 | send each key once, as UTF-8 JSON, over HTTP/1.1 ([§4](#the-docker-guard)) |
| `docker attach` to a container: Ctrl-C does nothing | the container isn't the persona's: the CLI turns Ctrl-C into a kill, which the guard refuses, and says nothing | three Ctrl-C end the attach; `docker logs -f` follows the container instead |
| `Error response from daemon: nexus guard: the build cache isn't labelled …` (e.g. at the end of `docker system prune`) | every persona shares the build cache, which Docker can't label | leave it; the other prunes before it ran, filtered to the persona's own. To empty the build cache, `docker builder prune` on the VM |
| `nexus guard: couldn't look up … to see whose it is (…)` | the guard's own inspect failed, so it can't tell whose it is | try again; `nexus <persona> logs` shows the guard's errors |
| `failed to connect to the docker API at unix:///run/nexus/docker.sock; … no such file or directory`, or `Cannot connect to the Docker daemon at unix:///run/nexus/docker.sock. Is the docker daemon running?` (a guard killed outright leaves its socket behind), inside a persona; once the guard has stayed down a while, `nexus ls` shows the persona `unhealthy` | the guard isn't listening (the persona's log says why: `docker guard on, but not listening …`, `docker guard exited …`). For about a second after it exits, until the entrypoint starts it again, every docker command fails this way | try again a second later; else `nexus <persona> logs`, `nexus <persona> restart`. To go without it, `DOCKER_GUARD=off` in `.env`, `nexus --config`, then `nexus <persona> restart` ([§4](#the-docker-guard)) |
| `docker guard can't start, docker here is UNGUARDED: uid … can't replace /run/nexus/docker.sock, …` in `nexus <persona> logs` | `/run/nexus` can't be written in the container: a read-only root filesystem (`read_only: true` in a compose override, say) | drop the `read_only`, then `nexus <persona> restart` |
| a tool's containers see a directory where the Docker socket should be, and an empty root-owned directory `/run/nexus/docker.sock` appears on the VM | the tool mounted `$DOCKER_HOST`'s socket into them, a path only the persona has | give it `/var/run/docker.sock` (act: `--container-daemon-socket unix:///var/run/docker.sock`); `sudo rmdir /run/nexus/docker.sock` on the VM ([§4](#the-docker-guard)) |
| `docker compose ps` shows nothing for a stack that runs, or `up` starts a second copy | the stack was started before compose's prefix, under its old name | `docker compose -p <old name> ps`; take it down on the VM and start it again ([section 10](#stacks-started-before-the-prefix-and-the-guard)) |
| two personas' copies of a project are one stack, under its bare name (`docker compose ls`) | `COMPOSE_PROJECT_NAME` exported in the shell, by the project's Makefile or `.envrc`, say: taken as given, quietly | `-p <persona>-<project>`, or `make COMPOSE_PROJECT_NAME=<persona>-<project> …` ([§4](#compose-project-names)) |
| `nexus compose shim: a compose or env file from stdin or a pipe can't be read twice, so the project has no <persona>- prefix (…)` | `-f -`, `/dev/stdin`, `<(…)` or a FIFO as a compose or env file | write it to a file (in `$NEXUS_SCRATCH`, say), or pass `-p <persona>-<name>` ([§4](#compose-project-names)) |
| `nexus compose shim: can't run Docker Compose at …: …` (exit 127, or 126), or `nexus compose shim: … is this shim, not Docker Compose`; `docker --help` lists `compose*  Docker Compose is missing (nexus compose shim)` | the real plugin at `/usr/libexec/docker/cli-plugins/docker-compose` is missing, or `NEXUS_COMPOSE_REAL` points somewhere wrong | `nexus <persona> build`, then `nexus <persona> restart`; or unset `NEXUS_COMPOSE_REAL` |
| `Bind for 127.0.0.1:8080 failed: port is already allocated` | another stack has that port: another persona's copy, one started on the VM, or one from before the prefix | `docker ps --filter publish=8080` shows which; publish on a port Docker picks (`127.0.0.1::8080`) or in `$NEXUS_PORTS` ([§4](#ports)). A port fixed in the project's compose file: `ports: !override ["127.0.0.1::8080"]` in a `compose.override.yaml` of your own ([§4](#compose-project-names)) |
| `Conflict. The container name "/…" is already in use` | a fixed `container_name:` in the compose file, the same in every persona's copy | override it in a compose file of your own: `container_name: !reset null` ([§4](#compose-project-names)) |
| `bind source path does not exist: …`, or a container sees an empty directory where a file should be, and an empty root-owned directory appears on the VM | the bind source is a path only the persona has (`/tmp`, `$HOME`): sources are looked up on the VM | put it in the projects root or `$NEXUS_SCRATCH` ([§4](#bind-mounts-and-the-scratch-dir)); remove the stray directory on the VM (`sudo rmdir …`) |
| `nexus --config`: `--ports …: that range overlaps …'s (…), so nothing was written` or `--ports …: it takes NAME:FROM-TO, …`; `personas.json … has ports …, which overlap …'s` | two personas given ranges that meet, or a typo (a range takes no leading zeros) | give each persona a range of its own ([§5](#5-add-change-or-remove-a-persona)) |
| `nexus-apm: apm-cli X.Y.Z is older than 0.9.0, …` | it can't turn that version's self-update off | pin 0.9.0 or later; or `uvx --from apm-cli==X.Y.Z apm …`, never with `self-update` ([section 6](#6-upgrade-claude-code-or-grok-apm-or-ddev)) |
| `nexus-apm: apm-cli X.Y.Z's self-update switch isn't where nexus-apm turns it off (…); nothing was kept …`, or `nexus-apm: apm-cli X.Y.Z was installed but doesn't start (above); nothing was kept` | that version moved its switch, or its dependencies (installed at their newest) keep it from starting | another version; or `uvx --from apm-cli==X.Y.Z apm …`, never with `self-update` ([section 6](#6-upgrade-claude-code-or-grok-apm-or-ddev)) |
| git over https: `fatal: could not read Username for 'https://gitlab.com': …`, after `glab-credential: the GitLab token in the environment is for <host>, so gitlab.com doesn't get it (…)` | `GITLAB_HOST` names another GitLab, and `GITLAB_TOKEN` is that host's | for gitlab.com as well: `glab auth login --hostname gitlab.com` in the persona ([§4](#gitlab-glab)) |
| git over https to a self-managed GitLab: `fatal: could not read Username for 'https://<host>': …` | git has no credential helper for that host | `git config --global credential.https://<host>.helper /usr/local/libexec/nexus/glab-credential` in the persona ([§4](#gitlab-glab)) |

## Environment variables

Precedence: a variable exported in your shell beats `.env`, except that a persona's own projects root beats
both. `.env` feeds compose, and the launcher reads `PROJECTS_ROOT`, `REGISTRY` and the version pins from it
(`upgrade` writes the pins); the persona env files go into the container's environment.

### Per VM — `/opt/agents/.env` (written by `nexus --config`)

| Variable | Required | Default | What it does |
|----------|----------|---------|--------------|
| `PROJECTS_ROOT` | no | `$HOME/projects` | The VM's projects root, mounted at the same path inside every persona that has no root of its own (`nexus --config`) |
| `REGISTRY` | no | `local` | Image name prefix. `local` = build on this VM; a registry host enables `nexus <persona> pull` |
| `CLAUDE_VERSION` | no | `latest` | Claude Code build: `latest`, `stable` or `X.Y.Z`. Pins below 2.1.118 ignore the update lock |
| `CLAUDE_TAG` | no | `CLAUDE_VERSION` | Tag of the Claude images |
| `APM_VERSION` | no | `latest` | APM in every image: `latest` or `X.Y.Z` (PyPI's `apm-cli`, not its Python libraries); `latest` is cached once built |
| `GROK_VERSION` | no | `latest` | Grok Build build: `latest` (newest on xAI's stable channel) or `X.Y.Z`; `upgrade` pins it |
| `GROK_TAG` | no | `GROK_VERSION` | Tag of the grok images |
| `DDEV_VERSION` | no | latest in the apt repo | ddev in ddev-context images; the VM's `ddev version` (leading `v` optional) |
| `AGENT_CPUS` | no | `4`, capped at `nproc` by `nexus --config` | CPU limit per persona; must not exceed `nproc` |
| `AGENT_MEMORY` | no | `8g` | Memory limit per persona |
| `TELEMETRY_ENDPOINT` | no | empty: no telemetry | The OpenTelemetry collector's OTLP/HTTP address for the personas that send ([§5](#telemetry-opentelemetry)) |
| `TELEMETRY_TOKEN` | no | empty: none sent | Its bearer token; `.env` is made mode 600 once it holds one |
| `TELEMETRY_CA` | no | empty: a public certificate | `personas/telemetry-ca.pem`, the collector's CA as `nexus --config` copied it in (a path of your own is copied in at its next run) |
| `NEXUS_SCRATCH_ROOT` | no | `/srv/nexus-scratch` | The shared scratch root: each persona's scratch dir is `<root>/<persona>`, at the same path inside it ([§4](#bind-mounts-and-the-scratch-dir)). An absolute directory other than `/`, without `:` or whitespace. `nexus --config` never writes it, but makes the root (with sudo when it must) |
| `DOCKER_GUARD` | no | `on` | `on` or `off`, exactly: the Docker guard in every persona ([§4](#the-docker-guard)). `nexus --config` never writes it, and refuses another value. A change: re-run `nexus --config` (it writes the guard's state into each persona's notes, and says which personas to restart), then `nexus <persona> restart` for each |

### Derived by the launcher — don't set them

| Variable | Value | Override only if… |
|----------|-------|-------------------|
| `AGENT_UID` / `AGENT_GID` | `id -u` / `id -g` | you build for another uid; export it in the shell |
| `DOCKER_GID` | gid of `/var/run/docker.sock` | never |
| `SSH_AUTH_SOCK` | your shell's agent, else `/run/user/<uid>/ssh-agent.sock`, `$XDG_RUNTIME_DIR/openssh_agent`, gnome-keyring, gpg-agent; unused by a persona with an ssh key of its own or no ssh | you want a specific agent |
| `AGENT_CA_CERTS` | ddev context: `~/.local/share/mkcert/rootCA.pem` if it exists, else empty | never |
| `AGENTS_DIR` | the directory the `nexus` script lives in | the repo is somewhere unusual (`install.sh` also clones there, instead of `/opt/agents`) |
| `HOME` | your VM home; ddev context mounts `~/.ddev` and the mkcert CA from there | never |

Without the launcher, export them yourself and use both compose files, e.g. from /opt/agents:
`AGENT_UID=$(id -u) AGENT_GID=$(id -g) DOCKER_GID=$(stat -c %g /var/run/docker.sock) PROJECTS_ROOT=$HOME/projects docker compose -p agents -f compose.yml -f compose.personas.yml --profile <persona> up -d`
(`PROJECTS_ROOT` = the persona's own root if it has one in `personas.json` (`root`), else the VM's; with
`SSH_AUTH_SOCK` from your shell; `-p agents` keeps an exported `COMPOSE_PROJECT_NAME` from switching to fresh
home volumes). For every persona, first `mkdir -m 755 /srv/nexus-scratch/<persona>` (its scratch dir, under the
`NEXUS_SCRATCH_ROOT` `.env` names, if it names one; compose reads that from `.env` itself), and run
`nexus --config` once so that `personas/<persona>.notes.md` is there: compose won't create either. For a ddev
persona, also `mkdir -p ~/.ddev ~/.local/share/mkcert` (or Docker creates them root-owned), and add
`AGENT_CA_CERTS=` (empty) if the VM has no mkcert CA. A ddev Claude persona that sends telemetry
to a collector with a CA of its own also needs the file nexus writes as it starts one:
`cat personas/telemetry-ca.pem ~/.local/share/mkcert/rootCA.pem > personas/telemetry-ca.ddev.pem` (the second only if
it is there).

### Per persona — `personas/<persona>.env` (written by `nexus --config` from `personas/<flavour>.env.example`)

| Variable | Flavour | Required | Notes |
|----------|---------|----------|-------|
| `ANTHROPIC_API_KEY` | claude | one sign-in | Console/API billing. Wins over the token if both are set |
| `CLAUDE_CODE_OAUTH_TOKEN` | claude | one sign-in | From `claude setup-token`; Pro/Max/Team/Enterprise plan |
| (neither) | claude | one sign-in | Log in interactively inside the persona (setup step 4) |
| `XAI_API_KEY` | grok | yes | Must match `env_key` in `~/.grok/config.toml` in the persona's home |
| `GIT_AUTHOR_NAME`, `GIT_AUTHOR_EMAIL`, `GIT_COMMITTER_NAME`, `GIT_COMMITTER_EMAIL` | both | recommended | Identity of commits made in that persona |
| `GH_TOKEN` | both | no | gh CLI as that account; APM uses it for private GitHub packages too |
| `GITHUB_APM_PAT` | both | no | A token for APM only; wins over `GH_TOKEN` there |
| `GITLAB_TOKEN` | both | no | glab as that account, and git over https to its GitLab through the image's credential helper ([§4](#gitlab-glab)). One token, one host: gitlab.com's, or `GITLAB_HOST`'s |
| `GITLAB_HOST` | both | no | Only for a self-managed GitLab: its host, e.g. `gitlab.example.org` (with its port, if it has one). `GITLAB_TOKEN` is then that host's, and git over https to gitlab.com doesn't get it; git over https to this host takes a `git config` in the persona ([§4](#gitlab-glab)) |
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
| `NEXUS_PERMISSIONS` | Claude personas: `ask` or `bypass` in a session started with `--skip-bypass-permissions` or `--bypass-permissions` (in that tmux session's environment), or in that headless run; unset otherwise. Set by nexus, not by hand: the image's `claude` (`claude-mode.sh`) turns it into `--permission-mode default` or `bypassPermissions`, in front of Claude Code at `/usr/local/libexec/claude` |
| `CLAUDE_CODE_TEAMMATE_COMMAND` | set by `claude-mode.sh` when it has a mode: `/usr/local/libexec/claude-ask` or `claude-bypass` (links to it), what an agent team's teammates start as, so they keep the session's mode |
| `NEXUS_SCRATCH` | the persona's scratch dir, `<NEXUS_SCRATCH_ROOT>/<persona>`: the same path on the VM ([§4](#bind-mounts-and-the-scratch-dir)) |
| `NEXUS_PORTS` | the persona's port range, `FROM-TO` from `personas.json` ([§4](#ports)) |
| `NEXUS_DOCKER_GUARD` | `on` or `off`, from `.env`'s `DOCKER_GUARD` when the container was created: whether the entrypoint starts the Docker guard (only `off` is off) |
| `DOCKER_HOST` | `unix:///run/nexus/docker.sock`: the Docker guard, or with it off a link to `/var/run/docker.sock` (image). Only the persona has that path: a tool that mounts this socket into its containers needs `/var/run/docker.sock` instead ([§4](#the-docker-guard)) |
| `TESTCONTAINERS_DOCKER_SOCKET_OVERRIDE` | `/var/run/docker.sock`: the socket testcontainers mounts into its Ryuk container, a path looked up on the VM (image) |
| `UV_LINK_MODE` | `copy`: uv's cache (home volume) and a project's venv (projects root) are on two filesystems (image) |
| `GLAB_CHECK_UPDATE` | `false`: glab doesn't look for a newer release; the image is its updater (image) |
| `GLAB_SHOW_WHATS_NEW` | `false`: no "What's new in glab" notice, which would otherwise show on a `git push` through glab's credential helper (image) |
| `GLAB_SEND_TELEMETRY` | `false`: glab sends GitLab no usage data (image) |

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
| `UV_VERSION`, `UV_SHA256_AMD64`, `UV_SHA256_ARM64` | every target (Dockerfile only): uv, its release tarball checked against these hashes | `0.13.6` and its hashes ([§6](#6-upgrade-claude-code-or-grok-apm-or-ddev)) |
| `GLAB_VERSION`, `GLAB_SHA256_AMD64`, `GLAB_SHA256_ARM64` | every target (Dockerfile only): glab, the same way | `1.127.0` and its hashes |
| `NODE_MAJOR` | every target (Dockerfile only) | `22` |
| `DEBIAN_FRONTEND` | apt steps (Dockerfile only) | `noninteractive` |

Images are named `${REGISTRY}/agent-<flavour>-<context>`, so personas with the same flavour and context share
one image.
