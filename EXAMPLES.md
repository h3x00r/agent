# Nexus by example

This page is for you if you are new to Nexus, or to tmux, containers and dev VMs in general. It explains
the ideas in plain words, then walks through the everyday tasks one at a time, with the exact commands
to type. The full reference is [runbook.md](runbook.md); how it is built is in [README.md](README.md).

Every command on this page runs **on the dev VM**, not on your laptop: connect to it first, for example
`ssh you@your-vm`.

## The idea in one minute

You work on a **dev VM**: a Linux machine you reach over ssh, where your projects live. An AI coding agent
such as Claude Code or Grok Build is a program that reads your code, edits it and runs commands, like a
teammate in the terminal.

Normally that agent runs in your terminal window. Close the laptop and it stops. Nexus fixes that:

- **Think of the VM as an office building.** Nexus gives each agent an office of its own there: a
  **persona**. The office stays open when you go home, so the agent keeps working.
- **Each office has its own name badge.** A persona has its own login, its own git name and email, and its
  own colour, so your team account and your personal account never mix.
- **Each project gets its own desk.** Inside a persona, every project you open is a separate **session**.
  You can have `billing` and `shop` open at once and hop between them.
- **You can walk out and come back.** Leaving a session without closing it is called **detaching**. When
  you come back, everything is exactly where you left it, even if you connect from another computer.

You talk to all of it through one command: `nexus`.

### Words you'll meet

| Word | What it means here |
|---|---|
| **persona** | One AI agent with its own account, for example `claude-team` or `grok-docker`. It runs in a container on the VM. |
| **container** | A separate box on the VM where a persona lives. You don't manage it by hand; `nexus` does. |
| **image** | The ready-made package a persona's container starts from: the agent program and its tools. `nexus` builds it once on the VM (a few minutes). You never edit it. |
| **session** | One open project inside a persona, named after the folder you are in when you run `nexus`: in `~/projects/billing` it is `billing`. Every persona also has a session called `main`, a plain shell, that is always there. |
| **detach** | Leave the session but keep it running: press `Ctrl-b`, let go, then press `d`. |
| **projects root** | The folder that holds your projects, `~/projects` by default. The persona works on the files inside it. It is **not a security wall**: a persona can also run Docker on the VM, and through Docker it can reach the rest of the VM. Use it on dev VMs only, and give it only work you would be OK running yourself. |
| **flavour** | Which agent a persona runs: `claude` (Claude Code) or `grok` (Grok Build). |
| **context** | What kind of projects it works on: `ddev` or `docker`. ddev is a tool that runs PHP web sites in containers; your project uses it if its folder has a `.ddev` folder inside. If it doesn't, or you're not sure, pick `docker` (docker compose, Go, Node, …). |
| **bypassPermissions** | Claude Code's name for running its tools (editing files, running commands) without asking you first. Every Claude persona works this way unless you set it to ask (example 11). |

## Example 1: set up a VM for the first time

Someone has to do this once per VM. If `nexus ls` (or `/opt/agents/nexus ls`) already lists personas, skip to
example 2.

Type this, as yourself (not with sudo):

```bash
curl -fsSL https://nexus.light-and-dark.io/install.sh | bash
```

It downloads Nexus to `/opt/agents` (it may ask for your password, to create that folder), then starts the
setup, `/opt/agents/nexus --config`, in the same window.

It asks a few questions. For each one, the default is shown in `[brackets]`, and pressing Enter takes it:

1. **The settings every persona shares.** Where your projects are (Enter for `~/projects`), where the
   images come from (Enter for `local`: built on this VM), and which Claude Code version (Enter for the
   latest).
2. **Where to put the `nexus` command**, so you can type `nexus` anywhere (Enter for `/usr/local/bin`; it may
   ask for your password, for sudo).
3. **Your first persona.** In this order:
   - the agent (`claude`), the context, and a name. On a VM that has ddev, the context default is `ddev`:
     type `docker` if your projects don't use ddev. Enter takes the suggested name, such as `claude-docker`;
   - the statusline and the persona's own projects root: press Enter for both;
   - **SSH (git over ssh): type `none`.** The agent can still commit; you run `git pull` and `git push`
     yourself on the VM, in the same folder. The default, `agent`, lends the persona your ssh keys, but only
     while you are connected with them loaded (see "When something goes wrong" below), so start with `none`;
   - whether it may run its tools without asking: press Enter for yes, so it works on its own. Type `n` if
     you'd rather it ask you before it acts (example 11);
   - how it signs in to Claude (Enter for `login`: you log in inside it later);
   - your git name and email for this persona. Enter takes your own. If you'll have more than one persona,
     type a name that says which one, e.g. `Ana (claude-team)`, so `git log` can tell them apart;
   - `GH_TOKEN`: press Enter to skip it. What you type here is hidden, so the screen stays empty. That is
     normal.
4. **Build its image now?** Say yes. The first build takes a few minutes; later ones take seconds.

Then it shows what is set up and a small menu. Type `quit` (or press Enter) to finish. You can run
`nexus --config` again at any time to add or change things (example 9).

## Example 2: start working on a project

```bash
cd ~/projects/billing
nexus claude-docker -- claude
```

What happens:

1. Nexus starts the `claude-docker` persona if it isn't running yet (a few seconds the first time).
2. It opens a session called `billing`, in the `billing` folder.
3. Because of `-- claude`, Claude Code starts in it right away.

The first time, Claude asks you to log in. It shows a link: open it in your browser, then paste the code
back. You do this once per persona; it remembers.

Now talk to Claude as usual, for example: *"Add a CSV export to the invoices page and make the tests pass."*

## Example 3: leave it working, come back later

You've given Claude a long task and it's your time to go home.

1. Press `Ctrl-b`, let go, then press `d`. You're back in your normal terminal. This is detaching.
2. Close the laptop. Claude keeps working on the VM.
3. Next morning, from any computer, ssh to the VM and run, **from the same folder**:

```bash
cd ~/projects/billing
nexus claude-docker
```

No `-- claude` this time: the session already exists, so you simply get back into it, with Claude's screen
as it left it. (From another folder, say `~/projects/billing/src`, you'd get a new session called `src`
instead. You can also name a session yourself: `nexus claude-docker billing` gets back into `billing` from
anywhere under the projects root.)

**Careful:** typing `/exit` in Claude (or `exit` in the shell) *ends* the session instead of leaving it
running. To keep it, always leave with `Ctrl-b d`.

## Example 4: two projects at the same time

```bash
cd ~/projects/billing && nexus claude-docker -- claude
# Ctrl-b d to detach, then:
cd ~/projects/shop && nexus claude-docker -- claude
```

You now have two project sessions in the same persona, `billing` and `shop`, each with its own Claude working
on its own project. Inside, `Shift+→` and `Shift+←` step through the sessions (the `main` one too), and
`Ctrl-b` then `s` shows a list to pick from. To see them all from outside:

```bash
nexus claude-docker ls
```

## Example 5: a work account and a personal account

Say your team pays for one Claude account and you have a personal plan too. Make one persona for each with
`nexus --config` → `add` (example 9), for example `claude-team` and `claude-max`, each with its own git name
(example 1, step 3). Then:

```bash
# terminal 1
cd ~/projects/billing
nexus claude-team -- claude       # signs in with the team account
```

```bash
# terminal 2: ssh to the VM again, then
cd ~/projects/billing
nexus claude-max -- claude        # your personal plan, same project
```

Each one has its own login and its own git name and email, so `git log` shows which persona made each commit.
Each also has its own colour in the bar at the bottom, so you can tell at a glance which one you are typing
into. Give them work one at a time: two agents editing the same folder at once can overwrite each other's
changes, and git may stop with an `index.lock` error.

## Example 6: a second opinion from another agent

Add a grok persona (example 9): pick `grok` as the agent. Grok has no browser login: it asks for
`XAI_API_KEY`, an API key from xAI (ask your team lead, or get one from xAI's console, before you start).
Paste it and press Enter; the screen stays empty while you paste, and that is normal. Then point it at the
same project:

```bash
cd ~/projects/billing
nexus grok-docker -- grok
```

Ask it, for example: *"Review the last commit and tell me what could break."* Claude wrote the feature; grok
checks it. Both see the same files, because both work in your projects root.

## Example 7: one quick question, no session

Sometimes you just want an answer printed, for example in a script or a git hook:

```bash
cd ~/projects/billing
git diff | nexus claude-docker headless "review this diff"
```

This runs one prompt and prints the answer, then stops. No session is opened. It must be run inside a
project under the persona's projects root (otherwise it refuses and says so).

## Example 8: see what's running

```bash
nexus ls       # every persona: is it running, and the sessions of the running ones
nexus top      # a live view: CPU and memory, and for each Claude session how full its conversation
               # memory is (Claude calls this "context") and how much of your plan's limits is used
```

In `nexus top`, press `q` to quit.

## Example 9: add, change or remove a persona

```bash
nexus --config
```

It shows what is set up, then a menu:

```text
What next?
  1) list      the shared settings and the personas, again
  2) add       a new persona
  3) change    one persona's flavour, context, statusline, projects root, ssh or permissions
  4) remove    one persona (its env file and home volume stay)
  5) shared    the settings every persona shares: projects root, image registry, Claude Code version
  6) launcher  link nexus into a directory on your PATH
  7) images    build (or pull) the image of every persona
  8) quit
choice [quit]:
```

Type the number or the word. Each choice asks only what it needs, and saves when you finish it. For example,
`add` asks the same questions as the first persona in example 1 (a grok one asks for its API key instead of a
login, and isn't asked whether it may run its tools without asking: it always does). After a `change`, it tells
you to run `nexus <persona> restart` so the change takes effect. That closes the persona's sessions, so finish
your work first.

## Example 10: a new Claude Code version is out

```bash
nexus claude-team upgrade
```

It finds the newest version, builds it and checks it. If Claude personas are running, it lists their sessions
and asks `Recreate them now? [y/N]` before it restarts them on the new version: answer `y` when nobody is in the
middle of something. Your logins, settings and history are kept.

When Nexus itself has changed, rather than Claude Code, type:

```bash
nexus --update
```

It downloads the new Nexus and lists the new changes. Then it says what applying them takes: images to build,
and personas to restart, with the sessions each restart would close. Then it asks `Apply it now? [y/N]`. If you
answer `n`, it keeps what's left to do, and the next `nexus --update` asks again. Answer `y` when nobody is
in the middle of something.

## Example 11: make the agent ask before it acts

By default, Claude **runs its tools without asking**: it edits files and runs commands (the tests, `git commit`,
`ddev start`, …) on its own. Claude Code calls this `bypassPermissions`. It is what lets Claude keep working
while you're away (example 3).

Sometimes you'd rather it ask first: in a repo you don't know yet, in a demo, or while you're learning what it
does. Then, before it edits a file or runs a command, Claude shows what it wants to do and waits for your
answer. While it waits it does nothing, so an agent that asks is not one to leave working overnight.

**For one session.** Add `--skip-bypass-permissions` when you open it:

```bash
cd ~/projects/billing
nexus claude-team --skip-bypass-permissions -- claude
```

Only this session asks. The persona's other sessions work as before.

- The session keeps that mode until it ends: every Claude you start in it, now or later, asks first.
- An open session can't switch. If `billing` is already open in `claude-team` (as in example 5), Nexus refuses
  and says so. End the old one first (`/exit` in its Claude, or `exit` in its shell), or give the new one a
  name of its own: `nexus claude-team --skip-bypass-permissions billing-careful -- claude`. `main`, the
  persona's shell, always works the persona's way and comes back if you end it: give the new session a name of
  its own, or change the persona's setting (below).
- The first time, Nexus may say `nothing was started: claude-team's image was built before nexus had
  --skip-bypass-permissions`. Then rebuild the persona's image once: `nexus claude-team build` (the message
  says `pull` if your team uses an image registry), then `nexus claude-team restart`. That closes its sessions,
  so finish your work first.
- If the bar at the bottom shows the session as `--skip-bypass-permissions` instead of `billing`, this VM's
  Nexus is older than this page and took the word for a session name, so that Claude doesn't ask. End that
  session, update Nexus (`git -C /opt/agents pull`, then `/opt/agents/nexus --config --yes --no-build`; a Nexus that has `nexus --update` does both), and try again.

**For the whole persona.** Run `nexus --config`, type `change` and pick the persona. Press Enter to keep each
answer as it is, until this question:

```text
Let claude-team run its tools without asking (bypassPermissions; no: it asks first, as personas/claude.ask.json says)? [Y/n]:
```

Type `n`, then apply it (this closes the persona's sessions too):

```bash
nexus claude-team restart
```

From then on every Claude in `claude-team` asks first. This needs no rebuild. To go back, do the same and type
`y` at that question.

**The other way round.** In a persona that asks, `--bypass-permissions` opens one session that runs its tools
without asking. The same rules apply as for one session above.

```bash
cd ~/projects/shop
nexus claude-team --bypass-permissions -- claude
```

A `headless` run (example 7) has nobody to ask, so in a persona that asks, Claude can't use the tools that need
a yes. Add `--bypass-permissions` for that run (it needs the rebuilt image too):

```bash
cd ~/projects/billing
nexus claude-team --bypass-permissions headless "run the tests"
```

Two more things to know:

- Only Claude personas have this. A grok persona always runs its tools without asking (`personas/grok.toml` sets
  that for every grok persona), and Nexus refuses both words for one.
- It sets how Claude starts; it isn't a lock. If you type `claude --dangerously-skip-permissions` yourself in a
  session that asks, that Claude doesn't ask.

## Example 12: see what the agents cost, on a dashboard

You run an OpenTelemetry collector (with Grafana behind it, say) for all your VMs, and want each persona's tokens,
cost and sessions there:

```bash
nexus --config
```
Pick `telemetry`, then answer: the collector's address (`https://collector.lan:4318`), its token, the CA file its
certificate is signed by if it has a private one, and `y` for each persona that should send. nexus tries the
collector at once and says if it doesn't take the export. Then restart each persona it names:

```bash
nexus claude-team restart
```

From then on every `claude` and `grok` in those personas sends; no prompts or replies, only what happened (tokens,
cost, tool use, errors). In Grafana, `claude_code_cost_usage_USD_total` by `persona` shows the cost of each. To stop,
`nexus --config`, `telemetry`, and `none` for the address. Details, and what to do if nothing arrives: runbook §5,
[Telemetry](runbook.md#telemetry-opentelemetry).

To see the commands, file paths and other input each tool ran with, not only that it ran, answer the next
question, "Tool details too", with `all` (every persona that sends) or `some` (then `y` or `n` for each persona),
then restart as above. They can hold secrets, such as a password typed into a command, so have the collector mask them
([Tool details](runbook.md#tool-details)). For scripts, all personas or one:

```bash
nexus --config --yes --tool-details all:on
nexus --config --yes --tool-details claude-team:on
```

The question after it, "Tool content too", also sends what the tools read, wrote and printed, so whole files can
reach the collector. Read [Tool content](runbook.md#tool-content) before you turn it on. For scripts:

```bash
nexus --config --yes --tool-content claude-team:on
```

## When something goes wrong

| You see | What it means | What to do |
|---|---|---|
| `nexus: command not found` | The `nexus` command isn't linked yet. | Type the full path for now, e.g. `/opt/agents/nexus ls`. To fix it for good: run `/opt/agents/nexus --config`, type `launcher`, and press Enter for `/usr/local/bin` (it may ask for your password). |
| `no personas set up yet: run …/nexus --config` | This VM has no personas yet. | Example 1. |
| `no persona 'x' (set up: …)` | A typo, or that persona doesn't exist. | Use one of the names it lists, or add it (example 9). |
| `claude-docker isn't running; start it with: nexus claude-docker` | You asked for its sessions, but it is stopped. | Run what it says. |
| `headless: … is outside claude-docker's projects root (…)` | You ran `headless` from a folder the persona can't see. | `cd` into a project under the projects root first. |
| `… is outside claude-docker's projects root; starting in …` | You started it from a folder it can't see, so it opened at the projects root instead. | Next time, `cd` into the project first. |
| `required variable SSH_AUTH_SOCK is missing a value: run an ssh-agent on the VM, …` | The persona was set up to borrow your ssh keys for git (SSH `agent`), and this login has no keys to lend. | Easiest: `nexus --config` → `change` → the persona → SSH `none`, then `nexus <persona> restart`. Or connect with `ssh -A you@your-vm`, which lends your laptop's keys to the VM for that login only: you have to do it every time, and a persona started with it needs `nexus <persona> restart` after you reconnect. |
| `nothing was started: claude-team's image was built before nexus had --skip-bypass-permissions` (or `… --bypass-permissions`) | The persona's image was built before Nexus had the words `--skip-bypass-permissions` and `--bypass-permissions`. | Rebuild it once: `nexus claude-team build` (if your team uses an image registry: `nexus claude-team pull`), then `nexus claude-team restart`. That closes its sessions, so finish your work first. |
| `session billing already runs its tools without asking (…)` or `… already asks before running tools (…)`, or `session main is claude-team's shell, in its own mode …` | You asked for the other mode, but that session is already open, and an open session can't switch (example 11). | Give the new session a name of its own, with the same word you typed: e.g. `nexus claude-team --skip-bypass-permissions billing-careful -- claude`. For `billing`, you can also end the old one first; for `main`, change the persona's setting instead (example 11). |

Anything else: the message usually says what to run. The full list is in
[runbook.md, Troubleshooting](runbook.md#troubleshooting).

## Cheat sheet

| I want to… | Type |
|---|---|
| set up or change the VM's personas | `nexus --config` |
| start a project with Claude | `cd ~/projects/<project> && nexus <persona> -- claude` |
| get back into it | `cd ~/projects/<project> && nexus <persona>` |
| leave it running | `Ctrl-b` then `d` |
| hop between sessions | `Shift+→` / `Shift+←`, or `Ctrl-b` then `s` for a list |
| list a persona's sessions | `nexus <persona> ls` |
| see everything | `nexus ls`, `nexus top` |
| ask one question from a script | `nexus <persona> headless "…"` |
| make one session ask before it acts | `nexus <persona> --skip-bypass-permissions -- claude` |
| let one session act without asking, in a persona that asks | `nexus <persona> --bypass-permissions -- claude` |
| make a whole persona ask | `nexus --config` → `change` → the persona → `n` at "Let … run its tools without asking", then `nexus <persona> restart` |
| send tokens and cost to an OpenTelemetry collector | `nexus --config` → `telemetry`, then `nexus <persona> restart` |
| send tool details too (the commands, file paths and other input the tools ran with) | `nexus --config` → `telemetry` → `all` or `some` at "Tool details too", then `nexus <persona> restart` for a Claude persona (grok needs none) |
| send tool content too (what the tools read, wrote and printed) | `nexus --config` → `telemetry` → `all` or `some` at "Tool content too", then `nexus <persona> restart` for a Claude persona (grok needs none) |
| upgrade Claude Code | `nexus <persona> upgrade` |
| update Nexus itself | `nexus --update` |
| see which Nexus this VM runs | `nexus --version` |
| apply a change to a running persona | `nexus <persona> restart` |

The persona names on this page (`claude-docker`, `claude-team`, `claude-max`, `grok-docker`), the repo address
and `you@your-vm` are examples: use your own, and the persona names `nexus ls` shows on your VM.
