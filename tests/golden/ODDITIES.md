# Odd things the bash tools do, pinned by the goldens

The goldens record what the bash `nexus` and `config.sh` did, including what looks unintended. The Python
rewrite keeps all of it, so the two can be compared byte for byte. Each item below is a candidate for a
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
  A snapshot time like `0999` makes `top` die with "no persona 'top'".
- **An empty snapshot field:** the fields after it shift left, because a tab IFS collapses.
- **Token rounding:** 999500 tokens shows as `1000k`.
- **top's session cut:** a long `:window.pane` label overflows the column.
- **LC_ALL=C:** widths count bytes, so a non-ASCII cell shifts its row.
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
