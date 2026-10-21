# Odd things the bash tools do, pinned by the goldens

The goldens record what the bash `nexus` and `config.sh` did, including what looks unintended. The Python
rewrite keeps all of it, so the two can be compared byte for byte; the few items marked "accepted@" are
the exceptions, each changed on purpose in that milestone. Each other item below is a candidate for a
deliberate change once the swap is done: change the code, re-record the cases it touches with
`golden.py accept --reason …`, and say so in the commit.

Lock handling is the exception, already changed on purpose. bash left its lock fd open in every child
except the nexus it ran: fd 9 in config.sh, and fd 8 in `nexus upgrade`. The Python tools close it in all
of them. golden.py ignores fds 8 and 9 for that reason; a lock fd leaked at any other number still shows.

## nexus --config (config-tools, and config.sh before it)
- **yesno:** any answer that doesn't start with y or Y counts as no, and the question isn't asked again
  (`maybe` turns the statusline off).
- **A refused answer resets the default:** after a refused VM root, registry or Claude Code version, the
  default goes back to `$HOME/projects`, `local` or `latest`, so Enter can quietly move the VM root.
- **Trailing colon in a spec:** `NAME:F:C:<dir>:` is accepted; bash's `read` drops one trailing `:`.
- **Spaces in the VM root:** `--projects-root` may contain spaces, which a persona's own root may not.
- **Low disk under --yes:** the warning prints and the build runs anyway.
- **Flavour changes:** claude to grok prints two restart lines; grok to claude leaves the old
  `personas/<n>.grok.toml` behind.
- **The "to delete them" hint:** it ends in a trailing space, and lists `.key`, `.key.pub` and
  `.grok.toml` even for a Claude persona without them.
- **--remove and --persona of one name:** the persona moves to the end of the list, with a new colour.
- **Colours:** past eight personas they wrap, so two personas share one.
- **A value that looks like a flag:** `--persona --no-link` takes `--no-link` as the spec.
- **The replace prompt:** a regular file is named twice ("… exists (<same path>); replace it?").
- **The persona list:** PROJECTS ROOT is padded to 20 columns, so a longer one runs into SSH.
- **Hidden-answer prompts on a pipe:** they print a bare newline to stderr even though no prompt was
  shown.

## nexus
- **`nexus P --help`:** it isn't help; it attaches to a session named `--help`.
- **Arguments ignored:** `nexus ls ARGS…` ignores its arguments, and so does an attach for arguments
  after the session name that have no `--` in front.
- **`top -n 05`:** refused, as not whole seconds.
- **Octal numbers:** a statusline number with a leading zero is read as octal (`09` gives a bash error).
  A snapshot time like `0999` makes `top` die with "no persona 'top'", and a valid one like `010` is 8.
  The Python port reads them all as decimal (basic-top-octal, edge-top-octal-colour, accepted@M3).
- **An empty snapshot field:** the fields after it shift left, because a tab IFS collapses.
- **Token rounding:** 999500 tokens shows as `1000k`.
- **top's session cut:** a long `:window.pane` label overflows the column.
- **LC_ALL=C:** widths count bytes, so a non-ASCII cell shifts its row.
- **A copy of the launcher elsewhere:** it took its own directory as the checkout, so without AGENTS_DIR
  it said "no personas set up yet". The Python shim can't run without agentctl/ next to it, so it says
  to link the launcher instead, or to set AGENTS_DIR (basic-agents-dir run 8, accepted@M3).
- **AGENTS_DIR as a symlink:** used as given, so this checkout's orphan containers aren't found.
- **The root check:** `PROJECTS_ROOT=/` is refused as "not ''". The message names your shell or .env as
  the source whichever one it came from.
- **headless and `--`:** after `--`, an argument that looks like a flag is still a flag.
- **TERM unset:** bash sets TERM=dumb itself, so `infocmp dumb` is asked, not xterm-256color.
- **A migrated model value:** one Claude Code rewrote (`opus` to `opus[1m]`) is written back, and the
  merge is reported, at every start.
- **mgmt and the preflight:** stop, logs, build and pull run before it, so a bad projects root goes to
  compose unchecked.

## nexus upgrade
- **One error lacks its prefix:** "upgrade takes one VERSION or CHANNEL, not …" has no `upgrade:`.
- **Empty and bare-v arguments:** `upgrade v` and `upgrade ''` mean the default channel; `upgrade ''
  stable` is accepted.
- **grok channels:** they are `stable alpha`, so `upgrade latest` is refused for grok, although .env
  says GROK_VERSION=latest.
- **The fixed-tag warnings:** they name .env even when the tag came from the shell. A tag other than
  latest gets no warning at all.
- **A failed upgrade without a pin:** it leaves `CLAUDE_VERSION=latest` appended to .env.
- **-y and the ssh-agent check:** -y doesn't override it, and one peer that needs an agent holds back
  every running peer.
- **"removed the old image":** it can print while a running peer still uses that image (another tag of
  it is gone).
- **The success line:** it ends in a trailing space.
- **A read-only .env:** bash's own "Permission denied" line, and a `.env.XXXXXX` file left in the
  checkout.

## Changed on purpose in M3 (nexus in Python)
The review of the port compared the two on input the first goldens didn't cover (cases `nexus/edge-*`).
Where they still differ, the bash was broken or depended on the host, and the case is accepted@M3:
- **Messages:** the outcome and the exit code are the same, the wording isn't. bash's
  `<script>: line N:` prefixes, the lines sed, mktemp and env printed on its behalf, and its
  "command substitution: ignored null byte" and "setlocale: cannot change locale" warnings are gone,
  and so is its job notice for a docker exec killed by SIGKILL; nexus says it in one line of its own.
- **Bash failures:** where bash crashed, hung or failed, the Python does the job:
  - a top probe line that is short, empty or garbled (`count: bad array subscript`, then "no persona
    'top'");
  - a cell or line ending in a UTF-8 lead byte, which made `read` swallow the newline;
  - a non-blocking stdin for headless;
  - no curl for upgrade;
  - a session probe whose child keeps its output open (the time-out now ends the whole process
    group, as timeout(1) meant to).
- **Bytes that aren't UTF-8** in .env or the personas file: bash's sed and grep skipped them in a UTF-8
  locale (a value lost its quotes, a persona line was dropped); the Python keeps them as they are.
- **64-bit numbers:** bash's arithmetic wrapped past 2^63 in top; Python's doesn't.
- **AGENTS_DIR and cd:** bash's `cd "$AGENTS_DIR"` took `-P` as an option and searched CDPATH (printing
  the directory); AGENTS_DIR is now only ever a directory.
- **Refusals:**
  - `upgrade 'latest stable'` (one word) isn't a channel;
  - an unreadable personas file is an error, where ls showed an empty table;
  - a closed stdout stops the run at once.
- **Headless input:** INT while the piped input is read ends the run (bash swallowed it there), and
  the docker inspect calls see a closed stdin as it came in (bash's `$(…)` gave them a pipe).
- Not in a case, because they depend on the host's locales:
  - [[:cntrl:]] followed glibc's locale data (C1 kept under glibc 2.31's C.UTF-8, U+2028 dropped under
    en_US); the Python drops C0, DEL and C1 everywhere and keeps U+2028.
  - bash's [a-z0-9] took é, ä and ß under en_US.UTF-8, so `nexus café` was "no persona" rather than
    "not a persona name".

## Changed on purpose in M4 (personas.json)
personas.json holds the personas now; compose.personas.yml is generated from it, without `# persona:` lines.
The cases that edited those lines by hand were rewritten: they now put the checkout back as it was before
personas.json (`before_personas_json`), edit the lines, and let config-tools move them (`move_lines`), so both
trees read them with their own parser; a field set by hand goes through `set_field`, into whichever file the
tree keeps. Where the two still differ, accepted@M4:
- **Lines config-tools won't move:** a bare `# persona: NAME `, a line without a colour or a flavour, extra
  lines a hand edit added. config.sh refused them too, but bash's nexus read them as they were; nexus now says
  to run config-tools --yes, which names the bad line.
- **A hand-edited personas.json** that its rules refuse (a relative root, an unknown flavour, a key twice, an
  unknown key, another version) is refused by both tools when they read it, naming the file and what is
  wrong. The "no CLI known for flavour" refusals of upgrade and headless can't happen any more.
- **Older layouts:** the refusals for a compose file from before the statusline and Claude settings splits
  are retired. A hand edit of compose.personas.yml (or one an older config-tools wrote) is now "out of step
  with personas.json": refused when a container would be created from it and by upgrade, a warning at an
  attach. The key, managed-settings and grok.toml checks come first.
- **No personas:** no personas.json and no `# persona:` line is "no personas set up yet".
- **tree.txt:** personas.json (and personas.json.bak, the one before) next to the generated file. Removing the
  last persona keeps personas.json.bak and removes the generated file. personas.json is written by a rename,
  so its inode changes where the bash's write-through kept the compose file's.
- **Which file wins:** `# persona:` lines in compose.personas.yml win over a personas.json beside them (one
  from before a rollback, or from a run cut short): they are what the tools last ran on, so they are moved
  again, and the file is kept first. A generated compose.personas.yml without personas.json is refused by
  both tools, which point at personas.json.bak when there is one.
- **The managed-settings mount** may be in the generated file or not: personas/claude.managed.json comes and
  goes with the checkout, and nexus checks for it on its own. A restart asked for after the move compares
  each service whole, its comments and CRs aside.
- **New cases** (recorded from the Python, nothing to compare): config/migrate-*, config/personas-json-refused,
  config/m4-* and the nexus/m4-* cases on new behaviour, from the review of M4.

## Changed on purpose: nexus --config (cfg M2)
The setup tool is `nexus --config` now; the config-tools script is gone. Every case was recorded again from
the tree and checked, case by case, to differ from before only by that name (in messages, the usage, the
generated files' comments, the labels), accepted@cfg M2; each says so in its meta.json, after what it was.
- **The lock** is flock on the checkout's directory, not on the file config-tools: nexus/edge-upgrade-lock-file
  (that file unreadable, then gone) is retired, as a checkout that can't be opened can't be set up at all.
- **AGENTS_DIR** names the checkout `nexus --config` sets up, as it does nexus's; config-tools set up its own
  (config/cfg-agents-dir).
- **Files config-tools wrote:** a compose.personas.yml whose comments name it is in step all the same
  (nexus/cfg-legacy-header); the next writing run writes it, and each grok persona's config, as they are now,
  with no note for either (config/cfg-legacy-grok).
- **nexus's usage** has a line for `nexus --config`.

## Changed on purpose: the menu (cfg M3)
Without --yes, `nexus --config` works by section: a first run (no .env) asks the settings every persona shares
(the Claude Code version too, with no Claude persona yet), then where to link the launcher (without
--no-link), then the first persona; every run then shows what is set up and a menu: list, add, change,
remove, shared, launcher, images, quit. Each action is saved as it is done (the same stage, check and commit
as a --yes run); --yes and --list are as they were, byte for byte. The interactive cases were rewritten for
it, accepted@cfg M3 after reading each transcript:
- **Gone:** the "Found personas/NAME.env: set it up as persona NAME?" questions (an env file of no persona is
  listed with what is set up, and a persona of that name reuses it); the "Keep them?" question; the launcher,
  mkcert and image questions at the end of every run (they come with the action they belong to: launcher,
  images, an add or change, a registry or Claude Code version changed).
- **remove** asks first ("Remove NAME? … [y/N]").
- **Options without --yes** (--persona, --projects-root, …) are saved first, then the menu.
- **The end of input at the menu** is a quit, rc 0; within an action the run stops (rc 1), and what it hadn't
  saved yet isn't; ^C keeps what was saved before it (rc 130). An action the check refuses is said, nothing of
  it written (the personas and the shared settings as saved, none of its notes), and the menu comes back; the
  run then ends with rc 1, as one whose build failed. A first persona refused on a first run gets the menu too.
- **Options without --yes** set the shared settings once: saved first, then .env's are the defaults.
- **Every run but a first** brings what it generates up to date before the menu, as a re-run of config-tools
  did (the grok configs, compose.personas.yml, a deleted key of its own, .env's DDEV_VERSION), and says which
  files it wrote, if any (config/menu-refresh); one the check refuses is said, and the menu comes all the
  same. A save that writes nothing says so.
- **Warnings** a save gives (no ddev on the VM, a TOML it couldn't check) are said once a run.
- **Renamed:** config/ask-adopt-first, ask-adopt-later → config/menu-leftover-first, menu-leftover-later;
  config/ask-keep-vm-no, ask-keep-vm-yes → config/menu-shared, menu-shared-kept. New: config/menu-* (some from
  the review of cfg M3) and config/ask-tty-first-link.
## Changed on purpose: permissions (perm M1, M2)
A claude persona asks before running tools, or doesn't (bypassPermissions, as committed in
personas/claude.managed.json), per persona and per session:
- **nexus --config** asks it after the ssh question in add and change (claude personas only), so the
  interactive cases answer one more question; it takes --permissions NAME:ask|bypass, and the persona table is
  followed by "asks before running tools (not bypassPermissions): …" when one does (accepted@perm M1, with the
  menu's change line, which names permissions now, and --help).
- **`--skip-bypass-permissions` and `--bypass-permissions`** after a persona are no session names any more:
  wherever they are before a `--` (and anywhere in a headless run's arguments), nexus takes them as the mode of
  the session it starts, or of that headless run. Every other `-x` still is one (nexus/perm-dash-session-unchanged).
- **Four cases that list the checkout** (config/m4-menu-unmigrated, m4-migrate-check-fails, m4-migrate-readonly,
  m4-zero-not-migrating-note) show EXAMPLES.md, which came after they were recorded.

## Changed on purpose: telemetry (telemetry M1)
A persona can send telemetry (OpenTelemetry) to one collector, which .env names (TELEMETRY_ENDPOINT, TELEMETRY_TOKEN,
TELEMETRY_CA); off unless set:
- **The menu** has a telemetry action after shared, so launcher, images and quit are one further down (quit is 9:
  config/ask-tty-first-grok answers 9 and config/ask-invalid-choices tries 10); the change line names telemetry;
  the overview has a telemetry row ("off" in every case before this). Accepted@telemetry M1 in the 93 cases that
  show the menu, the overview, .env (its TELEMETRY_* lines from .env.example) or --help.
- **Add and change** ask whether the persona sends only while a collector is set, so no case before this one
  answers a new question; personas.json holds "telemetry": true only for one that sends, and compose.personas.yml
  changes only for those (config/telemetry-*, nexus/telemetry-start).
- **The review's fixes** (telemetry M1): config/telemetry-hand-edits (.env's TELEMETRY_* edited by hand, a refused
  menu run's options) and nexus/telemetry-not-restarted (the warnings for a running container made before its
  telemetry changed) are new; the other telemetry cases were recorded again with grok's token as its metrics and
  logs headers, a neutral drop-in for a Claude persona that stops sending, and restart notes for what changed.

## Changed on purpose: tool details (telemetry M2)
A persona that sends telemetry can send its tool details too (OTEL_LOG_TOOL_DETAILS: the commands, file paths and
other input its tools ran with); off unless asked for, for every persona (TELEMETRY_TOOL_DETAILS=on in .env) or per persona
(personas.json "tool_details": true):
- **.env.example** has a TELEMETRY_TOOL_DETAILS line (commented out, with two comment lines), and --help a
  --tool-details option. Accepted@telemetry M2 in the 72 cases that show .env whole or --help, after a script
  checked that each diff held those lines and nothing else.
- **The telemetry action** asks one more question when some persona sends ("Tool details too …: from which
  personas?", none, all or some), so config/telemetry-menu answers it with its default (none) and was recorded
  again; add and change ask it of a persona that sends, unless tool details are on for all.
- **Drop-ins and grok tables** change only for a persona that sends them, so no case before this one shows a
  difference there. The overview's telemetry row names tool details only while TELEMETRY_TOOL_DETAILS is on, and
  the persona list only when some persona sends them.
- **New cases**: config/telemetry-details-flags (--tool-details, what it refuses, all:on and back, a collector
  turned off), config/telemetry-details-menu (none, some, all, add and change under all, a change that turns them
  off), and three runs added to config/telemetry-hand-edits (TELEMETRY_TOOL_DETAILS neither on nor off, then off;
  tool_details without telemetry).
- **The review's fixes** (telemetry M2): the all:… flags apply before the NAME:… ones; NAME:on under all says it
  does already; off reads as empty, and an emptied TELEMETRY_ENDPOINT turns tool details off too. A start or an
  attach of a running Claude persona now also reads its mounted drop-in (`docker exec … cat`, in the calls of the
  telemetry cases that start one running) and warns when it isn't the checkout's (nexus/telemetry-not-restarted,
  five runs added: tool details turned on after the container was made).

## Changed on purpose: tool content (telemetry M3)
A persona that sends telemetry can send its tool content too (OTEL_LOG_TOOL_CONTENT: what its tools read, wrote and
printed), set as tool details are, on its own: TELEMETRY_TOOL_CONTENT=on in .env, "tool_content": true in
personas.json, --tool-content NAME|all:on|off. Both are one shape in the code now (tele.EXTRAS):
- **.env.example** has a TELEMETRY_TOOL_CONTENT line, and --help a --tool-content option. Accepted@telemetry M3 in
  the 72 cases that show .env whole or --help, after a script checked that each diff held those lines alone.
- **The telemetry action** asks a second question after "Tool details too" ("Tool content too …", none, all or
  some), so config/telemetry-menu and config/telemetry-details-menu answer it with its default and were recorded
  again; add and change ask it too.
- **Messages shared by both** were reworded so they read for either name, and after review:
  - NAME:on for one that doesn't send: "…, so it can't send tool details. To send both:" (was "and tool details go
    only with it").
  - all:on with no collector: "…, so no persona can send tool details. To set one up:" and the command on its own
    line (was "tool details go only with telemetry. Give one with --telemetry-endpoint URL").
  - NAME:off under all: "every persona that sends telemetry sends tool details now (…). To leave c1 out, turn it
    off for all, then turn tool details on for each of the others" (was "tool details are on for every persona …
    turn them off for all, then on for each of the others").
  - "c1: it sends tool details already, …" (was "tool details: c1 sends them already, …").
  - personas.json's "(only a persona that sends telemetry can send tool details)".
  - The questions say "…, which can hold secrets" (was "; they can hold secrets"), and the per-persona one "the tools
    ran with" (was "its tools"); the line under all ends 'then some at "Tool details too"'.
  config/telemetry-details-flags, config/telemetry-details-menu and config/telemetry-hand-edits were recorded again
  for those lines.
- **A persona that comes to send tool content without tool details** is told so once. A Claude one leaves out what
  Claude Code writes to files, and the note gives the command that adds tool details. A grok one sends its commands
  all the same.
- **New cases**: config/telemetry-content-flags and config/telemetry-content-menu; config/telemetry-hand-edits has
  TELEMETRY_TOOL_CONTENT neither on nor off, and "tool_content" without telemetry.
