#!/bin/sh
# /usr/local/bin/grok in the grok persona images, and /usr/local/bin/agent, a link to it. grok itself stays where xAI's
# installer puts it, /opt/grok/.grok/bin/grok, and agent is the same binary under its other name there; this execs the
# one named as it was started (agent as agent, anything else as grok), or NEXUS_GROK_REAL when that is set: the tests,
# and nexus's wrapper probe, which runs this file's checks with /bin/true for grok.
# NEXUS-GROK-SHIM v1
# (nexus's wrapper probe greps for the line above, to tell this image's grok from an older one's.)
#
# A wrapper file: a text put at the end of grok's system prompt for a whole session. It comes from
# `grok --wrapper=FILE` (or --wrapper FILE), else from NEXUS_WRAPPER, which nexus sets: `nexus <persona> --wrapper
# FILE` for one tmux session (tmux new-session -e) or headless run (docker exec -e), and `nexus --config --wrapper
# NAME:PATH` for the whole persona, as NEXUS_WRAPPER=/etc/grok/wrapper.md in its service's environment (the copy
# --config writes, mounted read-only there). `grok --no-wrapper` drops an inherited one. Those words are this file's,
# taken out before a bare --, and grok never sees them. `--wrapper FILE` with an empty FILE, or one starting with -,
# stops with exit 2 (as nexus's own --wrapper does): a file named -x goes as --wrapper=-x or --wrapper ./-x.
# The text goes in as ONE argv word put first, "--rules=TEXT", so a text that starts with - or --- is never taken
# for an option. grok takes one --rules only, so a user's own --rules or --append-system-prompt (either spelling) is
# merged into it, after the wrapper and a blank line. The text loses its trailing newlines, as $(cat) drops them.
#
# - Only a root call gets it: a word that is a subcommand (grok help's list, its aliases v and disk-usage included)
#   or -h/--help/-v/-V/--version passes everything through, with only this file's words taken out. So `grok update`
#   or `grok --version` never carries the text, and `grok agent …` and `grok dashboard` run unwrapped. An option's
#   value is no subcommand: `grok -p update`, `--agent worker`, `-m v`, `--print update` are wrapped. The words are
#   read as clap reads them: an option of grok's that takes a value (the lists below, hidden ones too) takes the next
#   word unless it starts with - (a lone - is a value), --opt=VALUE and -pVALUE are one word, a short cluster ends at
#   its first value-taking letter or at a letter that is none of grok's shorts (c h v V r m p s w; clap refuses it).
#   --rules and --append-system-prompt, merged into the text, take the next word by that same rule, so never --:
#   one that starts with - is no rule, and it stays in argv as it is, after the --rules (grok then refuses the call,
#   as it would without this file). So `grok --rules --wrapper F` reads --wrapper F as this file's, as nexus does.
#   With the wrapper from --wrapper, such a call says on stderr that it went without it; one from NEXUS_WRAPPER is
#   dropped quietly (every `grok update` in a persona's shell would say so otherwise).
# - FILE (relative: from the cwd) must be a readable regular file, UTF-8 (checked with python3, as strictly as grok's
#   argv is: glibc's iconv lets 5-byte and over-U+10FFFF sequences through) with no NUL, not empty once its trailing
#   newlines are gone; and bytes(FILE) + bytes(user rules) + 2 <= 122880 (120 KiB), the 2 counted always, with no
#   user rules too, so a file alone may be 122878 bytes (nexus --config's limit, 122878, matches it): one argv
#   string can't pass 128 KiB (MAX_ARG_STRLEN). Anything else stops here with exit 2.
# - A session keeps the system prompt it was created with: -c/--continue, -r/--resume, --load (a hidden grok option that
#   resumes too, its value required), or a short cluster that resumes (-cp; read as clap does, so -pc is -p's value
#   "c") gets a note that it keeps the wrapper only if it was created with one. The --rules still goes on. A FILE from
#   NEXUS_WRAPPER that fails a check there only warns, and grok runs without it: the resumed session has its own prompt
#   anyway. --fork-session alone is no resume (grok's help: it applies to --resume/--continue), so it is wrapped, and
#   checked, as a new session.
# - A FILE missing at /etc/grok/wrapper.md, the persona's own (NEXUS_WRAPPER from nexus --config), means its copy,
#   personas/<persona>.wrapper.md, is gone: the message says to re-run nexus --config.
# - --leader (before a bare --) with a wrapper on gets one warning, and grok runs as asked: the leader gives every
#   session it serves its first client's rules, so this session may get another wrapper, or none. ([cli] use_leader
#   in grok's config does the same unseen; leader mode is unsupported with a wrapper.)
# - NEXUS_WRAPPER is always unset before grok runs, and NEXUS_WRAPPED is the FILE's absolute path when a wrapper is on
#   and unset otherwise: a grok the model starts from its shell is no wrapped lead.
# - exec, never a child: tmux's pane_current_command must say grok, and a headless kill signals grok's process group.

nl='
'
die() { printf 'grok: %s\n' "$1" >&2; exit 2; }

if [ -n "${NEXUS_GROK_REAL-}" ]; then real=$NEXUS_GROK_REAL
else
  case "${0##*/}" in
    agent) real=/opt/grok/.grok/bin/agent ;;
    *) real=/opt/grok/.grok/bin/grok ;;
  esac
fi

# grok's own words, from `grok help` (grok-help-sub.txt in the plan), so a check can take them out and hold them
# against a newer grok's help: one per line between a BEGIN and its END line, in the lines that start neither with #
# nor with ' (the variable's own two lines). Subcommands: the Commands list, with the aliases it gives in
# [aliases: …]. Value options: each shown as --opt <VALUE> or -x, --opt <VALUE>, with the aliases its help gives in
# "(compat alias: …)", "(compat aliases: …)" or "[aliases: …]". Optional-value options: each shown with [<VALUE>].
# Hidden value options: the root's options that take a value and that grok help doesn't show (hidden ones, hidden
# aliases), from an inventory of the real binary, to re-run on every grok upgrade (done this way for 1.0.77, in its
# image with --network none):
#   candidates: every root option of `grok completions bash` (its `grok)` case's opts=), zsh (its first _arguments
#   block, the descriptions' compat aliases too) and fish (__fish_grok_global_optspecs); every --name in
#   `grok --help`; every --name literal in the binary; every name-shaped substring of the .rodata spans dense in the
#   names above (clap keeps the root's long names and aliases there, without --, glued to each other: --print is
#   only there); every name a probe's "tip: a similar argument exists" gives (clap suggests hidden aliases too).
#   each, against the binary grok resolves to: `grok --C --version` -> 'a value is required' = takes a value,
#   'unexpected argument' = no root option; `grok --C=x --version` -> 'unexpected value' = a flag, else an optional
#   value (none in 1.0.77 but grok help's; a hidden one would need a block of its own, as these have, the
#   optional-value block being held equal to grok help); `grok --C update --version` -> exit 0 = it takes a
#   subcommand word for its value (so a stale list passes such a call through unwrapped); `grok --C -c --version` ->
#   'a value is required' = a word starting with - is no value, as the shim reads it. The shorts likewise: every
#   -[A-Za-z0-9]. This block is the options that take a value, minus the value options block.
# BEGIN grok subcommands
subcommands='
agent
clone
completions
dashboard
doctor
du
disk-usage
export
help
inspect
leader
login
logout
mcp
memory
models
plugin
sessions
setup
trace
update
usage
version
v
worker
worktree
wrap
'
# END grok subcommands
# BEGIN grok value options
value_options='
--add-dir
--agent
--agents
--allow
--allowedTools
--allowed-tools
--cwd
--debug-file
--deny
--disallowedTools
--disallowed-tools
--json-schema
--leader-socket
-m
--model
--max-turns
--output-format
-p
--single
--permission-mode
--prompt-file
--prompt-json
--reasoning-effort
--effort
--rules
-s
--session-id
--sandbox
--system-prompt-override
--system-prompt
--tools
--worktree-ref
--ref
'
# END grok value options
# BEGIN grok hidden value options
hidden_value_options='
--append-system-prompt
--background-shell-timeout
--background-wait-timeout
--client-identifier
--compaction-detail
--compaction-mode
--conversation-history-file
--harness
--header
--hunk-tracker-mode
--installer
--load
--mode
--print
--storage-mode
'
# END grok hidden value options
# BEGIN grok optional-value options
optional_value_options='
-r
--resume
-w
--worktree
'
# END grok optional-value options

# listed WORD LIST: WORD is one of LIST's lines.
listed() {
  case $1 in ""|*"$nl"*) return 1 ;; esac
  case $2 in *"$nl$1$nl"*) return 0 ;; esac
  return 1
}

rules="" hasrules=0
add_rules() {
  if [ "$hasrules" = 1 ]; then rules=$rules$nl$nl$1; else rules=$1 hasrules=1; fi
}

# Pass 1, up to a bare --: this file's words, the user's rules, and what kind of call it is. want says what the
# word after an option is: this file's FILE, the user's rules, or a value of grok's (skipped, unless it starts with
# -, as clap takes it). pass: why the call goes through without the wrapper, the first reason found.
nw=0 nono=0 wfile="" want="" pass="" resume=0 leader=0
for a do
  case "$want" in
    wrapper)
      want=""
      case "$a" in ""|--|-*) die "--wrapper needs a file: --wrapper FILE, or --wrapper=FILE" ;; esac
      wfile=$a; continue ;;
    rules)
      want=""
      case "$a" in -?*) ;; *) add_rules "$a"; continue ;; esac ;;
    value)
      want=""
      case "$a" in -?*) ;; *) continue ;; esac ;;
  esac
  [ "$a" = -- ] && break
  case "$a" in
    --wrapper=*) nw=$((nw + 1)) wfile=${a#--wrapper=}
                 [ -n "$wfile" ] || die "--wrapper needs a file: --wrapper FILE, or --wrapper=FILE" ;;
    --wrapper) nw=$((nw + 1)) want=wrapper ;;
    --no-wrapper) nono=1 ;;
    --rules=*) add_rules "${a#--rules=}" ;;
    --append-system-prompt=*) add_rules "${a#--append-system-prompt=}" ;;
    --rules|--append-system-prompt) want=rules ;;
    -h|--help|-v|-V|--version) [ -n "$pass" ] || pass="a help/version call" ;;
    -c|--continue|--resume=*|--load=*) resume=1 ;;
    -r|--resume|--load) resume=1 want=value ;;
    --leader) leader=1 ;;
    --*=*) ;;
    --*) listed "$a" "$value_options$hidden_value_options$optional_value_options" && want=value ;;
    -?*)
      # A short cluster, as clap reads it: flags until one that takes a value, which takes the rest of the word,
      # or the next word when it is the last letter; a letter that is none of grok's shorts ends it (clap refuses).
      rest=${a#-}
      while [ -n "$rest" ]; do
        c=${rest%"${rest#?}"} rest=${rest#?}
        case "$c" in
          c) resume=1 ;;
          h|v|V) [ -n "$pass" ] || pass="a help/version call" ;;
          r|m|p|s|w)
            [ "$c" = r ] && resume=1
            [ -n "$rest" ] || want=value
            break ;;
          *) break ;;
        esac
      done ;;
    *) listed "$a" "$subcommands" && { [ -n "$pass" ] || pass="'$a' is a subcommand"; } ;;
  esac
done
[ "$want" = wrapper ] && die "--wrapper needs a file: --wrapper FILE, or --wrapper=FILE"
[ "$nw" -gt 1 ] && die "--wrapper given twice: give one file"
[ "$nw" = 1 ] && [ "$nono" = 1 ] && die "--wrapper with --no-wrapper: give one of them"

if [ "$nw" = 1 ]; then f=$wfile from=""
elif [ "$nono" = 0 ] && [ -n "${NEXUS_WRAPPER-}" ]; then f=$NEXUS_WRAPPER from=" (NEXUS_WRAPPER)"
else f="" from=""
fi
if [ -n "$pass" ]; then
  [ "$nw" = 1 ] && printf 'grok: --wrapper: not applied: %s\n' "$pass" >&2
  f=""
fi
unset NEXUS_WRAPPER NEXUS_WRAPPED

# The checks on FILE: why it can't be the wrapper, in $why, or the text in $text.
check() {
  if [ ! -e "$1" ]; then
    if [ "$1" = /etc/grok/wrapper.md ]; then
      why="no such file here (the persona's copy of its wrapper file, personas/<persona>.wrapper.md, is missing): re-run nexus --config"
    else
      why="no such file here (a persona sees only its projects root and its scratch dir): nexus <persona> --wrapper FILE copies a file in"
    fi
    return 1
  fi
  [ -f "$1" ] || { why="not a regular file: name a text file"; return 1; }
  [ -r "$1" ] || { why="can't be read: make it readable (chmod a+r)"; return 1; }
  # Size first, before the file is read whole: a log taken for a wrapper is refused unread.
  size=$(wc -c < "$1") || { why="can't be read: make it readable (chmod a+r)"; return 1; }
  size=$((size)) rsize=0
  [ "$hasrules" = 1 ] && rsize=$(($(printf '%s' "$rules" | wc -c)))
  if [ $((size + rsize + 2)) -gt 122880 ]; then
    why="is too big: $size bytes, $rsize of --rules and the 2 always kept for a blank line come to $((size + rsize + 2)), over the 122880 (120 KiB) one argument takes: shorten it"
    return 1
  fi
  python3 -I -c 'import sys
b = open(sys.argv[1], "rb").read()
if b"\0" in b:
    sys.exit(3)
try:
    b.decode("utf-8")
except UnicodeDecodeError:
    sys.exit(4)' "$1" 2>/dev/null
  case $? in
    0) ;;
    3) why="holds a NUL byte: save it as UTF-8 text"; return 1 ;;
    4) why="isn't valid UTF-8: save it as UTF-8"; return 1 ;;
    127) why="can't be checked, as this image has no python3: rebuild it (nexus <persona> build)"; return 1 ;;
    *) why="can't be read: make it readable (chmod a+r)"; return 1 ;;
  esac
  # A redirection, not cat FILE: cat reads stdin for a file named -, even after --.
  text=$(cat < "$1") || { why="can't be read: make it readable (chmod a+r)"; return 1; }
  [ -n "$text" ] || { why="is empty (or only newlines): write the instructions in it"; return 1; }
}

apply=0
if [ -n "$f" ]; then
  if check "$f"; then apply=1
  elif [ -n "$from" ] && [ "$resume" = 1 ]; then
    printf 'grok: --wrapper %s%s: %s; resuming without it, as a resumed session keeps the system prompt it was created with\n' \
      "$f" "$from" "$why" >&2
  else die "--wrapper $f$from: $why"
  fi
fi
if [ "$apply" = 1 ] && [ "$resume" = 1 ]; then
  printf 'grok: --wrapper %s%s: a resumed session keeps the system prompt it was created with, so it has a wrapper only if it was created with one; for this one, start a new session\n' \
    "$f" "$from" >&2
fi
if [ "$apply" = 1 ] && [ "$leader" = 1 ]; then
  printf 'grok: --wrapper %s%s: with --leader, the leader gives every session it serves its first client'"'"'s rules, so this one may get another wrapper or none: leave out --leader\n' \
    "$f" "$from" >&2
fi

# Pass 2: the arguments again, rotated through "$@", without this file's words and, with a wrapper on, without the
# user's rules (merged into it). Value words are read as pass 1 read them; from a bare -- on, all go as they are.
n=$# end=0
while [ "$n" -gt 0 ]; do
  a=$1; shift; n=$((n - 1))
  if [ "$end" = 1 ]; then set -- "$@" "$a"; continue; fi
  case "$a" in
    --) end=1; set -- "$@" "$a" ;;
    --wrapper=*|--no-wrapper) ;;
    --wrapper) shift; n=$((n - 1)) ;;
    --rules=*|--append-system-prompt=*) [ "$apply" = 1 ] || set -- "$@" "$a" ;;
    --rules|--append-system-prompt)
      # Its value as pass 1 took it: the next word unless it starts with - (or there is none left: "$1" is then a
      # word already rotated). Untaken, the option goes on alone and the next word is read as any other.
      take=0
      [ "$n" -gt 0 ] && case "$1" in -?*) ;; *) take=1 ;; esac
      if [ "$take" = 1 ]; then
        if [ "$apply" = 1 ]; then shift; else set -- "$@" "$a" "$1"; shift; fi
        n=$((n - 1))
      else set -- "$@" "$a"
      fi ;;
    *) set -- "$@" "$a" ;;
  esac
done

if [ "$apply" = 1 ]; then
  [ "$hasrules" = 1 ] && text=$text$nl$nl$rules
  # ./ first: cd takes a directory named - for $OLDPWD, even after --.
  case "$f" in /*) NEXUS_WRAPPED=$f ;; *) NEXUS_WRAPPED=$(unset CDPATH; cd -- "./$(dirname -- "$f")" && pwd)/${f##*/} ;; esac
  export NEXUS_WRAPPED
  exec "$real" "--rules=$text" "$@"
fi
exec "$real" "$@"
