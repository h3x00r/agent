# Odd things the bash tools do, pinned by the goldens

The goldens record what the bash `nexus` and `config.sh` did, including what looks unintended. The Python
rewrite keeps all of it, so the two can be compared byte for byte; the few items marked "accepted@" are
the exceptions, each changed on purpose in that milestone. Each other item below is a candidate for a
deliberate change once the swap is done: change the code, re-record the cases it touches with
`golden.py accept --reason …`, and say so in the commit.

Lock handling is the exception, already changed on purpose. bash left its lock fd open in every child
except the nexus it ran: fd 9 in config.sh, and fd 8 in `nexus upgrade`. The Python tools close it in all
of them. golden.py ignores fds 8 and 9 for that reason; a lock fd leaked at any other number still shows.

## config-tools
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
