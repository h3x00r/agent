#!/usr/bin/env bash
# config.sh — set this checkout up for one dev VM: which personas it runs, the flavour of each
# (claude | grok), and the context each one works in:
#   ddev     ddev projects: ddev inside, sharing the VM's ~/.ddev and mkcert CA
#   docker   projects run with docker compose / Dockerfiles: no ddev at all
#
#   ./config.sh                  interactive; re-run it any time to add, change or remove personas
#   ./config.sh --list           the personas set up so far
#   ./config.sh --yes [options]  no questions, for scripts: current settings are kept, and the
#                                 defaults below apply to new ones
#     --persona NAME:FLAVOUR:CONTEXT[:ROOT]
#                                      add or update a persona; repeatable. ROOT: its own projects root
#                                      (empty: the VM's; left out: an existing persona keeps its own)
#     --ssh NAME:agent|none|new|KEY    how that persona's ssh (git over ssh) signs in: agent, your ssh-agent
#                                      (the default); none, not at all (pull and push from the VM); new, a
#                                      key of its own made as personas/NAME.key, to add as a deploy key (an
#                                      existing one is kept); or KEY, the path of a private key of yours
#                                      without a passphrase; repeatable
#     --remove NAME                    drop a persona (its env file and home volume stay); repeatable
#     --projects-root DIR              default: $HOME/projects
#     --registry HOST                  local (build on this VM; the default) or host[:port][/path]
#     --claude-version V               latest | stable | X.Y.Z (default: latest)
#     --statusline | --no-statusline   the statusline (what nexus top shows) for the personas given with
#                                      --persona (default: on for a new one; an existing one keeps its own)
#     --link DIR | --no-link           where the `nexus` launcher goes (default: /usr/local/bin)
#     --no-build                       don't build (or pull) the images at the end
#
# Writes .env, compose.personas.yml and personas/<name>.env (mode 600; an existing one is kept), all
# at once and only after `docker compose config` accepts them: a run that stops early changes nothing,
# except the projects roots (the VM's and a persona's own), which are created as they are named. Then
# it makes the ssh keys asked for (personas/<name>.key) that don't exist yet. Every Claude persona gets
# personas/claude.json (committed) as a managed settings drop-in. A grok persona gets
# personas/<name>.grok.toml, its grok managed config: personas/grok.toml (the grok settings for every
# grok persona, committed) and, with the statusline on, statusline/grok/settings.toml.
set -euo pipefail

SELF="$(readlink -f "$0")"   # before the cd: $0 may be relative
AGENTS_DIR="$(dirname "$SELF")"
cd "$AGENTS_DIR"
PERSONAS_FILE=compose.personas.yml
MIN_FREE_GB=3   # for Docker, before building or pulling the images
COLOURS=(colour25 colour28 colour90 colour130 colour31 colour124 colour166 colour61)   # tmux status bars

say()   { printf '%s\n' "$*"; }
section() { (( YES )) || { say ""; say "== $*"; }; }   # a heading over a group of questions
warn()  { printf 'config: warning: %s\n' "$*" >&2; }
die()   { printf 'config: %s\n' "$*" >&2; exit 1; }
usage() { awk 'NR > 1 && !/^#/ { exit } NR > 1 { sub(/^# ?/, ""); print }' "$SELF"; }

# Staged files (temp files next to their targets, mode 600) can hold secrets: removed on any exit.
TMPS=()
trap 'rm -f "${TMPS[@]}"' EXIT
trap 'exit 130' INT TERM HUP

need() { [[ -n "${2-}" ]] || die "$1 needs $3 (see --help)"; }
YES=0 LIST=0 BUILD=1 STATUSLINE="" LINK=/usr/local/bin
OPT_ROOT="" OPT_REGISTRY="" OPT_CLAUDE=""
ADD=() SSHS=() REMOVE=() LATER=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)        usage; exit 0 ;;
    --list)           LIST=1 ;;
    -y|--yes)         YES=1 ;;
    --persona)        need "$1" "${2-}" "NAME:FLAVOUR:CONTEXT[:ROOT]"; ADD+=("$2"); shift ;;
    --ssh)            need "$1" "${2-}" "NAME:agent, NAME:none, NAME:new or NAME:KEY"; SSHS+=("$2"); shift ;;
    --remove)         need "$1" "${2-}" "a NAME"; REMOVE+=("$2"); shift ;;
    --projects-root)  need "$1" "${2-}" "a DIR"; OPT_ROOT="$2"; shift ;;
    --registry)       need "$1" "${2-}" "local or a HOST"; OPT_REGISTRY="$2"; shift ;;
    --claude-version) need "$1" "${2-}" "latest, stable or X.Y.Z"; OPT_CLAUDE="$2"; shift ;;
    --statusline)     STATUSLINE=yes ;;
    --no-statusline)  STATUSLINE=no ;;
    --link)           need "$1" "${2-}" "a DIR"; LINK="$2"; shift ;;
    --no-link)        LINK=none ;;
    --no-build)       BUILD=0 ;;
    *)                die "unknown option '$1' (see --help)" ;;
  esac
  shift
done

# --- values ----------------------------------------------------------------------------------
NAME_RULE="a lowercase letter, then lowercase letters, digits or -; at most 31, not ending in -; not ls, top, all or start"
valid_name() { [[ "$1" =~ ^[a-z][a-z0-9-]{0,30}$ && "$1" != *- && ! "$1" =~ ^(ls|top|all|start)$ ]]; }
valid_cv() { [[ "$1" =~ ^(stable|latest|[0-9]+\.[0-9]+\.[0-9]+(-[^[:space:]]+)?)$ ]]; }
# local, or what Docker takes in front of an image name: host[:port], then lowercase path segments.
RE_REGISTRY='^[A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?(\.[A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?)*(:[0-9]+)?(/[a-z0-9]+((\.|_|__|-+)[a-z0-9]+)*)*$'
valid_registry() { [[ "$1" == local || "$1" =~ $RE_REGISTRY ]]; }
# norm_dir VAR: a leading ~ or ~/ becomes $HOME, a trailing / goes.
norm_dir() {
  local _d="${!1}"
  case "$_d" in \~) _d="$HOME" ;; \~/*) _d="$HOME/${_d#\~/}" ;; esac
  [[ "$_d" == / ]] || _d="${_d%/}"
  printf -v "$1" '%s' "$_d"
}
# The projects root is bind-mounted as ROOT:ROOT, so no ':' in it.
root_ok() { [[ "$1" == /?* && "$1" != *:* ]]; }
# A persona's own root also goes into its "# persona:" line, whose fields are split on spaces.
persona_root_ok() { root_ok "$1" && [[ "$1" != *[[:space:]]* ]]; }
ROOT_RULE="an absolute directory other than /, without ':' or spaces"
check_spec() {  # check_spec NAME:FLAVOUR:CONTEXT[:ROOT]
  local n f c r; IFS=: read -r n f c r <<< "$1"
  valid_name "$n" || die "--persona $1: '$n' is not a usable name ($NAME_RULE)"
  [[ "$f" == claude || "$f" == grok ]] || die "--persona $1: flavour must be claude or grok"
  [[ "$c" == ddev || "$c" == docker ]] || die "--persona $1: context must be ddev or docker"
  [[ -z "$r" ]] || { norm_dir r; persona_root_ok "$r"; } || die "--persona $1: its projects root must be $ROOT_RULE"
}
# A persona's ssh: your ssh-agent (the default, nothing stored), none at all ("none"), or a key of its own,
# personas/NAME.key made here or a key of yours by its absolute path. That path goes into its "# persona:"
# line (ssh=) and, quoted, into the compose file, which expands $: hence KEY_RULE. key_path_ok NAME PATH
# checks the path; key_problem PATH says what keeps the file from working inside (nothing if it would): ssh
# wants it yours, private and without a passphrase.
KEY_RULE="an absolute path (or ~/…) without spaces, ':', quotes or \$"
key_path_ok() { [[ "$2" != *[[:space:]:\'\"\$]* && ( "$2" == /?* || "$2" == "personas/$1.key" ) ]]; }
key_problem() {
  local m
  command -v ssh-keygen >/dev/null || { echo "checking it needs ssh-keygen (openssh-client) on this VM"; return; }
  if [[ ! -f "$1" || ! -r "$1" ]]; then echo "no readable file there"
  elif [[ "$(stat -L -c %u "$1")" != "$(id -u)" ]]; then echo "it isn't yours, and the persona runs as you (uid $(id -u))"
  elif m="$(stat -L -c %a "$1")"; [[ ! "$m" =~ ^[0-7]00$ ]]; then echo "others can read it (mode $m), so ssh won't use it: chmod 600 it"
  elif ! ssh-keygen -y -P "" -f "$1" >/dev/null 2>&1; then echo "it has a passphrase, or isn't a private key: nothing inside could type a passphrase"
  fi
}
check_ssh_flag() {  # check_ssh_flag NAME:agent|none|new|KEY
  local n="${1%%:*}" k="${1#*:}" why
  if [[ "$1" != *:?* ]] || ! valid_name "$n"; then die "--ssh $1: it takes NAME:agent, NAME:none, NAME:new or NAME:/path/to/key"; fi
  case "$k" in
    agent|none) ;;
    new)   command -v ssh-keygen >/dev/null || die "--ssh $1: making a key needs ssh-keygen (openssh-client) on this VM" ;;
    *)     norm_dir k
           key_path_ok "$n" "$k" || die "--ssh $1: the key's path must be $KEY_RULE"
           why="$(key_problem "$k")"; [[ -z "$why" ]] || die "--ssh $1: $why" ;;
  esac
}

# py_on FILE CODE: run python3 CODE with FILE on stdin, here if this VM's python3 has what CODE imports on
# its first line (tomllib needs 3.11), else in a persona image already here. Its output in PY_OUT, its exit
# code in PY_RC; PY_RC=-1 when there is neither. Called directly, not in $(…), so the globals stand.
PY_OUT="" PY_RC=0
py_on() {
  local img
  PY_OUT="" PY_RC=0
  if python3 -c "${2%%$'\n'*}" 2>/dev/null; then   # its import line: is this python3 new enough?
    PY_OUT="$(python3 -c "$2" < "$1" 2>&1)" || PY_RC=$?
  elif img="$(docker images --format '{{.Repository}}:{{.Tag}}' 2>/dev/null | grep -E '/agent-(claude|grok)-(docker|ddev):' | head -n 1)" \
       && [[ -n "$img" ]]; then
    PY_OUT="$(docker run --rm -i --network none --entrypoint python3 "$img" -c "$2" < "$1" 2>&1)" || PY_RC=$?
  else
    PY_RC=-1
  fi
}
# settings_problem FILE FORMAT NAME: in WHY, why FILE isn't FORMAT (json, for Claude Code; toml, for grok)
# its CLI can read, or nothing. A broken settings file stops the CLI short in every persona it reaches, so
# it is checked before anything is written. When nothing here can check it, WHY stays empty and NAME (the
# file to name in a warning) goes on UNCHECKED.
UNCHECKED=() WHY=""
settings_problem() {
  local py
  case "$2" in   # ValueError covers a decode error too: a file that isn't UTF-8 is as broken
    json) py=$'import sys, json\ntry: json.load(sys.stdin.buffer)\nexcept ValueError as e: print(e); sys.exit(3)' ;;
    toml) py=$'import sys, tomllib\ntry: tomllib.load(sys.stdin.buffer)\nexcept ValueError as e: print(e); sys.exit(3)' ;;
  esac
  WHY=""; py_on "$1" "$py"
  if (( PY_RC == 3 )); then WHY="$PY_OUT"; elif (( PY_RC )); then UNCHECKED+=("$3"); fi
}
# grok_sets_statusline FILE: FILE (grok settings) has a status line of its own, which then stands: asked of
# tomllib, so any TOML spelling counts; without one here, its usual spellings.
grok_sets_statusline() {
  [[ -f "$1" ]] || return 1
  py_on "$1" $'import sys, tomllib\nd = tomllib.load(sys.stdin.buffer).get("ui")\nsys.exit(4 if isinstance(d, dict) and "status_line" in d else 0)'
  case "$PY_RC" in
    4) return 0 ;; 0) return 1 ;;
    *) grep -qE '^[[:space:]]*(\[[[:space:]]*"?ui"?[[:space:]]*\.[[:space:]]*"?status_line|(ui[[:space:]]*\.[[:space:]]*)?"?status_line"?[[:space:]]*[.=])' "$1" ;;
  esac
}

# Flag values are checked before anything is asked.
for p in "${ADD[@]}"; do check_spec "$p"; done
for p in "${SSHS[@]}"; do check_ssh_flag "$p"; done
if [[ -n "$OPT_ROOT" ]]; then
  norm_dir OPT_ROOT
  root_ok "$OPT_ROOT" || die "--projects-root must be an absolute directory other than /, without ':', not '$OPT_ROOT'"
fi
[[ "$OPT_REGISTRY" != ?*/ ]] || OPT_REGISTRY="${OPT_REGISTRY%/}"
[[ -z "$OPT_REGISTRY" ]] || valid_registry "$OPT_REGISTRY" \
  || die "--registry must be local or host[:port][/path] (e.g. registry.lan:5000/team), not '$OPT_REGISTRY'"
[[ -z "$OPT_CLAUDE" ]] || valid_cv "$OPT_CLAUDE" || die "--claude-version must be latest, stable or X.Y.Z, not '$OPT_CLAUDE'"
if [[ "$LINK" != none ]]; then
  norm_dir LINK
  [[ "$LINK" == /* ]] || die "--link must be an absolute directory, not '$LINK'"
fi
[[ -z "$STATUSLINE" || ${#ADD[@]} -gt 0 ]] || warn "--statusline / --no-statusline only apply to personas given with --persona"

# --- prompts (with --yes, every answer is its default) ---------------------------------------
# At the end of input a prompt stops the run instead of taking defaults; a last line without a
# newline still counts as an answer.
eof() { [[ -n "$1" ]] || { echo >&2; die "input ended; answer on a terminal or use --yes"; }; }
DEFAULTS=0   # 1 while a block of questions takes its defaults (the VM settings, kept on a re-run)
ask() {    # ask VAR "question" [default]
  local _a=""
  if (( YES || DEFAULTS )); then _a="${3-}"; else read -r -p "$2${3:+ [$3]}: " _a || eof "$_a"; _a="${_a:-${3-}}"; fi
  printf -v "$1" '%s' "$_a"
}
yesno() {  # yesno "question" y|n
  local _a=""
  if (( YES )); then _a="$2"; else read -r -p "$1 [$([[ $2 == y ]] && echo Y/n || echo y/N)]: " _a || eof "$_a"; _a="${_a:-$2}"; fi
  [[ "$_a" == [yY]* ]]
}
secret() { # secret VAR "prompt": not echoed, empty = skip
  local _a=""
  if (( ! YES )); then read -r -s -p "$2 (hidden; empty to skip): " _a || eof "$_a"; echo >&2; fi
  printf -v "$1" '%s' "$_a"
}
choose() { # choose VAR "question" default "key  description"...; answer by number or key
  local _var="$1" _q="$2" _def="$3" _a="" _i _o; shift 3
  while :; do
    if (( YES )); then _a="$_def"; else
      say "$_q"
      _i=1; for _o in "$@"; do say "  $_i) $_o"; _i=$((_i + 1)); done
      read -r -p "choice [$_def]: " _a || eof "$_a"; _a="${_a:-$_def}"
    fi
    if [[ "$_a" =~ ^[0-9]{1,9}$ ]] && (( 10#$_a >= 1 && 10#$_a <= $# )); then _a=$((10#$_a)); _a="${!_a}"; _a="${_a%% *}"; fi
    for _o in "$@"; do [[ "${_o%% *}" == "$_a" ]] && { printf -v "$_var" '%s' "$_a"; return 0; }; done
    (( YES )) && die "'$_a' is not one of the choices for: $_q"
    say "  pick a number or a name from the list"
  done
}

# --- dotenv files ----------------------------------------------------------------------------
# get_kv FILE KEY: KEY's value the way compose reads a dotenv file (last KEY= line, export prefix,
# CRLF, '…', "…" with its escapes, trailing comment), minus ${VAR} expansion. The nexus launcher
# reads PROJECTS_ROOT the same way.
get_kv() {
  [[ -f "$1" ]] || return 0
  sed -nE "s/\r\$//; s/^[[:space:]]*(export[[:space:]]+)?$2[[:space:]]*=[[:space:]]*//p" "$1" | tail -n 1 \
    | sed -E 's/^"((\\.|[^"\\])*)".*/\1/; t dq; s/^\x27((\\.|[^\x27\\])*)\x27.*/\1/; t sq; s/[[:space:]]+#.*//; s/[[:space:]]+$//; b
              :sq; s/\\\x27/\x27/g; b
              :dq; s/\\\\/\x01/g; s/\\n/\n/g; s/\\t/\t/g; s/\\r/\r/g; s/\\([$"])/\1/g; s/\x01/\\/g'
}
# quote VALUE so compose's dotenv parser gives it back verbatim: plain values stay bare, anything
# with $, #, quotes or edge spaces is single-quoted (literal), and values containing ' or \ get "…"
# with \ " $ escaped.
quote() {
  local v="$1"
  if [[ -z "$v" || "$v" =~ ^[A-Za-z0-9._@+:/,=-]([A-Za-z0-9\ ._@+:/,=-]*[A-Za-z0-9._@+:/,=-])?$ ]]; then printf '%s' "$v"
  elif [[ "$v" != *[\'\\]* ]]; then printf "'%s'" "$v"
  else v="${v//\\/\\\\}"; v="${v//\"/\\\"}"; v="${v//\$/\\\$}"; printf '"%s"' "$v"
  fi
}
# set_kv FILE KEY VALUE: rewrite the KEY= line compose uses (the last one; "export KEY =" too), else
# the first commented "# KEY=", else append it. Plain POSIX awk: gawk and mawk alike.
set_kv() {
  local tmp; tmp="$(mktemp "$1.XXXXXX")"; TMPS+=("$tmp")
  K="$2" L="$2=$(quote "$3")" awk 'BEGIN { k = ENVIRON["K"]; l = ENVIRON["L"] }
    { line[NR] = $0 }
    $0 ~ "^[ \t]*(export[ \t]+)?" k "[ \t]*=" { act = NR }
    !com && $0 ~ "^# ?" k "=" { com = NR }
    END { at = act ? act : com
          for (i = 1; i <= NR; i++) print (i == at ? l : line[i])
          if (!at) print l }' "$1" > "$tmp"
  chmod --reference="$1" "$tmp"
  mv "$tmp" "$1"
}
# stage VAR TARGET [SOURCE]: a temp file next to TARGET (mode 600) with SOURCE's content.
stage() {
  local _t; _t="$(mktemp "$2.XXXXXX")"; TMPS+=("$_t")
  if [[ -n "${3-}" ]]; then cat "$3" > "$_t"; fi
  printf -v "$1" '%s' "$_t"
}
# bakname FILE: FILE.bak, or FILE.bak.2, .3 … if that's taken, so an older backup (it may hold a key) survives.
bakname() { local b="$1.bak" i=2; while [[ -e "$b" || -L "$b" ]]; do b="$1.bak.$i"; i=$((i + 1)); done; echo "$b"; }
# commit STAGED TARGET [BACKUP]: put a staged file in place. An existing TARGET is rewritten (cat),
# so a symlink keeps pointing at its target and the mode stays; an unchanged one isn't touched.
commit() {
  if [[ -e "$2" || -L "$2" ]]; then
    if cmp -s "$1" "$2"; then rm -f "$1"; return 0; fi
    if [[ -n "${3-}" && -e "$2" ]]; then cp -p "$2" "$3"; fi
    (umask 077; cat "$1" > "$2"); rm -f "$1"
  else
    mv "$1" "$2"
  fi
}
# env_flavour FILE: claude or grok from its key lines (commented or not), empty if it can't tell.
env_flavour() {
  if grep -qE '^[#[:space:]]*(export[[:space:]]+)?XAI_API_KEY[[:space:]]*=' "$1"; then echo grok
  elif grep -qE '^[#[:space:]]*(export[[:space:]]+)?(ANTHROPIC_API_KEY|CLAUDE_CODE_OAUTH_TOKEN)[[:space:]]*=' "$1"; then echo claude
  fi
}

# --- personas: kept in arrays, loaded from and written to compose.personas.yml -----------------
P_NAME=() P_FLAVOUR=() P_CONTEXT=() P_COLOUR=() P_STATUSLINE=() P_ROOT=() P_SSH=()   # empty: the VM's root, your agent
declare -A LOADED=() STAGED=()   # name -> "flavour context statusline root ssh" as loaded; name -> staged env file
idx() { local i; for i in "${!P_NAME[@]}"; do [[ "${P_NAME[i]}" == "$1" ]] && { echo "$i"; return 0; }; done; return 1; }
put() {    # put NAME FLAVOUR CONTEXT COLOUR STATUSLINE [ROOT [SSHKEY]]
  local i; i="$(idx "$1")" || i="${#P_NAME[@]}"
  P_NAME[i]="$1" P_FLAVOUR[i]="$2" P_CONTEXT[i]="$3" P_COLOUR[i]="$4" P_STATUSLINE[i]="$5" P_ROOT[i]="${6-}" P_SSH[i]="${7-}"
}
drop() {
  local i; i="$(idx "$1")" || return 0
  unset 'P_NAME[i]' 'P_FLAVOUR[i]' 'P_CONTEXT[i]' 'P_COLOUR[i]' 'P_STATUSLINE[i]' 'P_ROOT[i]' 'P_SSH[i]'
  P_NAME=("${P_NAME[@]}") P_FLAVOUR=("${P_FLAVOUR[@]}") P_CONTEXT=("${P_CONTEXT[@]}")
  P_COLOUR=("${P_COLOUR[@]}") P_STATUSLINE=("${P_STATUSLINE[@]}") P_ROOT=("${P_ROOT[@]}") P_SSH=("${P_SSH[@]}")
}
load() {
  [[ -f "$PERSONAS_FILE" ]] || return 0
  local line w kv name f c col sl r k
  while read -r line; do
    read -ra w <<< "$line"
    name="${w[0]-}" f="" c="" col="" sl="" r="" k=""
    for kv in "${w[@]:1}"; do
      case "$kv" in
        flavour=*) f="${kv#*=}" ;; context=*) c="${kv#*=}" ;; colour=*) col="${kv#*=}" ;; statusline=*) sl="${kv#*=}" ;;
        root=*) r="${kv#*=}" ;; ssh=*) k="${kv#*=}" ;;
      esac
    done
    if ! valid_name "$name" || ! [[ "$f" =~ ^(claude|grok)$ && "$c" =~ ^(ddev|docker)$ && "$col" =~ ^colour[0-9]+$ && "$sl" =~ ^(yes|no)$ ]] \
       || { [[ -n "$r" ]] && ! persona_root_ok "$r"; } || { [[ -n "$k" && "$k" != none ]] && ! key_path_ok "$name" "$k"; }; then
      die "$PERSONAS_FILE: persona '$name' has a bad line (edited by hand?): '# persona: $line'; it takes flavour=claude|grok context=ddev|docker colour=colourN statusline=yes|no [root=/dir] [ssh=none|/path|personas/NAME.key]"
    fi
    put "$name" "$f" "$c" "$col" "$sl" "$r" "$k"
    LOADED[$name]="$f $c $sl $r $k"
  done < <(sed -n 's/\r$//; s/^# persona: //p' "$PERSONAS_FILE")
}
next_colour() {
  local c; for c in "${COLOURS[@]}"; do [[ " ${P_COLOUR[*]} " == *" $c "* ]] || { echo "$c"; return; }; done
  echo "${COLOURS[${#P_NAME[@]} % ${#COLOURS[@]}]}"
}
suggest_name() {
  local n="$1-$2" i=2; while idx "$n" >/dev/null; do n="$1-$2-$i"; i=$((i + 1)); done; echo "$n"
}
has() { local i; for i in "${!P_NAME[@]}"; do [[ "${P_FLAVOUR[i]} ${P_CONTEXT[i]}" == *"$1"* ]] && return 0; done; return 1; }
list() {
  if (( ! ${#P_NAME[@]} )); then say "  (none yet)"; return; fi
  local i r; printf '  %-31s %-8s %-8s %-10s %-20s %s\n' NAME FLAVOUR CONTEXT STATUSLINE "PROJECTS ROOT" SSH
  for i in "${!P_NAME[@]}"; do
    r="${P_ROOT[i]}"; [[ -n "$r" ]] || r="the VM's"
    printf '  %-31s %-8s %-8s %-10s %-20s %s\n' "${P_NAME[i]}" "${P_FLAVOUR[i]}" "${P_CONTEXT[i]}" "${P_STATUSLINE[i]}" "$r" \
      "${P_SSH[i]:-your agent}"
  done
}

FLAVOURS=("claude   Claude Code (Anthropic)" "grok     Grok Build (xAI)")
CONTEXTS=("ddev     ddev projects: ddev inside, sharing the VM's ddev state and mkcert CA"
          "docker   projects run with docker compose / Dockerfiles: no ddev")
CONTEXT_Q="Context: which kind of projects does it work on?"
SL_Q="Statusline (statusline/: model, context, cost; what nexus top shows) in"
ask_flavour_context() {  # ask_flavour_context VAR_FLAVOUR VAR_CONTEXT default_flavour default_context
  choose "$1" "Flavour:" "$3" "${FLAVOURS[@]}"
  choose "$2" "$CONTEXT_Q" "$4" "${CONTEXTS[@]}"
}
# ask_root VAR NAME CURRENT: a persona's own projects root, or "" for the VM's ("-" at the prompt).
ask_root() {
  local _r="${3:--}" _why
  while :; do
    ask _r "Projects root for $2 (-: the VM's, $VM_ROOT_HINT)" "$_r"
    if [[ -z "$_r" || "$_r" == - ]]; then printf -v "$1" ''; return; fi
    norm_dir _r
    if ! persona_root_ok "$_r"; then
      _why="a projects root must be $ROOT_RULE, not '$_r'"
    elif ! mkdir -p "$_r" 2>/dev/null; then
      _why="can't create the projects root '$_r'"
    else
      printf -v "$1" '%s' "$_r"; return
    fi
    (( ! YES )) || die "$_why"
    say "  $_why"; _r=-
  done
}
SSH_HOW=("agent    your ssh-agent, forwarded: your own keys; starting it then needs an agent in your shell"
         "none     no ssh: it commits, and you pull and push from the VM, in the same directory"
         "new      a key of its own, made as personas/NAME.key: add its public half as a deploy key"
         "path     a private key you already have, without a passphrase")
# ask_ssh VAR NAME CURRENT: how NAME's ssh signs in: "" for your ssh-agent, none, or its key's path.
ask_ssh() {
  local _how _k _why _def=agent
  case "$3" in "") ;; none) _def=none ;; "personas/$2.key") _def=new ;; *) _def=path ;; esac
  while :; do
    choose _how "SSH (git over ssh) for $2:" "$_def" "${SSH_HOW[@]//NAME/$2}"
    case "$_how" in
      agent) printf -v "$1" ''; return ;;
      none)  printf -v "$1" none; return ;;
      new)   if command -v ssh-keygen >/dev/null; then printf -v "$1" '%s' "personas/$2.key"; return; fi
             say "  making a key needs ssh-keygen (openssh-client) on this VM" ;;
      *)     _k=""; [[ "$_def" != path ]] || _k="$3"
             while :; do   # a path until one works; - goes back to the choice
               ask _k "Path of $2's private key (-: back)" "$_k"
               [[ -n "$_k" && "$_k" != - ]] || break
               norm_dir _k
               if ! key_path_ok "$2" "$_k"; then _why="the key's path must be $KEY_RULE"
               else _why="$(key_problem "$_k")"; fi
               if [[ -z "$_why" ]]; then printf -v "$1" '%s' "$_k"; return; fi
               say "  $2's key: $_why"; _k=""
             done ;;
    esac
  done
}
add_interactive() {
  local f c n r k sl=no
  ask_flavour_context f c claude "$DEFAULT_CONTEXT"
  while :; do
    ask n "Name" "$(suggest_name "$f" "$c")"
    valid_name "$n" && ! idx "$n" >/dev/null && break
    say "  $NAME_RULE; not taken"
  done
  if yesno "$SL_Q $n?" y; then sl=yes; fi
  ask_root r "$n" ""
  ask_ssh k "$n" ""
  put "$n" "$f" "$c" "$(next_colour)" "$sl" "$r" "$k"
}
change_interactive() {
  local i f c sl r k; i="$(idx "$1")"
  ask_flavour_context f c "${P_FLAVOUR[i]}" "${P_CONTEXT[i]}"
  sl=no
  if yesno "$SL_Q $1?" "$([[ ${P_STATUSLINE[i]} == yes ]] && echo y || echo n)"; then sl=yes; fi
  ask_root r "$1" "${P_ROOT[i]}"
  ask_ssh k "$1" "${P_SSH[i]}"
  put "$1" "$f" "$c" "${P_COLOUR[i]}" "$sl" "$r" "$k"
}
# A re-run asks only about what you pick: add, change or remove one persona at a time, then done.
persona_menu() {
  local act n
  while :; do
    say ""; list
    choose act "What next?" "done" \
      "add      a new persona" \
      "change   one persona's flavour, context, statusline, projects root or ssh" \
      "remove   one persona (its env file and home volume stay)" \
      "done     go on with the rest of the setup"
    case "$act" in
      add)    add_interactive ;;
      change) if pick n change; then change_interactive "$n"; fi ;;
      remove) if pick n remove; then drop "$n"; fi ;;
      "done") return 0 ;;
    esac
  done
}
pick() {   # pick VAR VERB: one of the personas, or false for "-" (none)
  local _o=() _i
  if (( ! ${#P_NAME[@]} )); then say "  no persona to $2"; return 1; fi
  for _i in "${!P_NAME[@]}"; do _o+=("$(printf '%-31s %s, %s' "${P_NAME[_i]}" "${P_FLAVOUR[_i]}" "${P_CONTEXT[_i]}")"); done
  choose "$1" "Which persona to $2?" - "${_o[@]}" "-        none: back to the menu"
  [[ "${!1}" != - ]]
}
# personas/<name>.env files without a persona, e.g. from the earlier fixed layout. The same name
# means the same home volume (agents_<name>-home), so logins and history carry over.
adopt_interactive() {
  local file n f c sl
  for file in personas/*.env; do
    n="${file#personas/}"; n="${n%.env}"
    if [[ ! -f "$file" ]] || ! valid_name "$n" || [[ -n "${LOADED[$n]+x}" ]] || idx "$n" >/dev/null; then continue; fi
    f="$(env_flavour "$file")"
    # A first setup offers them by default; later they are more likely personas removed on purpose.
    yesno "Found $file${f:+ (flavour $f)}: set it up as persona $n?" "$( (( FIRST )) && echo y || echo n)" || continue
    [[ -n "$f" ]] || choose f "Flavour:" claude "${FLAVOURS[@]}"
    choose c "$CONTEXT_Q" "$DEFAULT_CONTEXT" "${CONTEXTS[@]}"
    sl=no
    if yesno "$SL_Q $n?" y; then sl=yes; fi
    put "$n" "$f" "$c" "$(next_colour)" "$sl"
  done
}
add_flag() {  # --persona NAME:FLAVOUR:CONTEXT[:ROOT], already checked
  local n f c r i col sl=yes keep="" key=""
  IFS=: read -r n f c r <<< "$1"
  if i="$(idx "$n")"; then
    col="${P_COLOUR[i]}" keep="${P_ROOT[i]}" key="${P_SSH[i]}"
    sl="${P_STATUSLINE[i]}"   # an existing persona keeps it
  else
    col="$(next_colour)"
  fi
  sl="${STATUSLINE:-$sl}"
  if [[ "$1" == *:*:*:* ]]; then
    if [[ -n "$r" ]]; then norm_dir r; mkdir -p "$r" 2>/dev/null || die "--persona $1: can't create the projects root '$r'"; fi
  else
    r="$keep"
  fi
  put "$n" "$f" "$c" "$col" "$sl" "$r" "$key"
}
ssh_flag() {  # --ssh NAME:agent|none|new|KEY, already checked
  local n="${1%%:*}" k="${1#*:}" i
  i="$(idx "$n")" || die "--ssh $1: no persona $n (add it with --persona)"
  case "$k" in agent) k="" ;; none) ;; new) k="personas/$n.key" ;; *) norm_dir k ;; esac
  P_SSH[i]="$k"
}

write_personas() {  # write_personas FILE [staged]: the compose file for every persona in the arrays;
                    # "staged" points env_file at the staged env files, for the check before commit
  local i n f c k tag base env more
  {
    say "# compose.personas.yml — written by ./config.sh; re-run it to add, change or remove personas."
    say "# config.sh and the nexus launcher read the \"# persona:\" lines back: keep them in step with"
    say "# the services below if you edit this by hand."
    for i in "${!P_NAME[@]}"; do
      say "# persona: ${P_NAME[i]} flavour=${P_FLAVOUR[i]} context=${P_CONTEXT[i]} colour=${P_COLOUR[i]} statusline=${P_STATUSLINE[i]}${P_ROOT[i]:+ root=${P_ROOT[i]}}${P_SSH[i]:+ ssh=${P_SSH[i]}}"
    done
    say ""
    say "services:"
    for i in "${!P_NAME[@]}"; do
      n="${P_NAME[i]}" f="${P_FLAVOUR[i]}" c="${P_CONTEXT[i]}" k="${P_SSH[i]}" more=""
      env="personas/$n.env"; [[ "${2-}" != staged ]] || env="${STAGED[$n]:-$env}"
      [[ -z "$k" ]] || more=', SSH_AUTH_SOCK: ""'   # a key of its own or none, not your agent (see below)
      base=_agent; [[ "$c" == ddev ]] && base=_agent-ddev
      # shellcheck disable=SC2016 # literal: compose expands these, not this script
      if [[ "$f" == claude ]]; then tag='${CLAUDE_TAG:-${CLAUDE_VERSION:-latest}}'; else tag='${GROK_TAG:-${GROK_VERSION:-latest}}'; fi
      cat <<EOF

  "$n":
    extends: { file: compose.yml, service: $base }
    profiles: ["$n", "all"]
    image: \${REGISTRY:-local}/agent-$f-$c:$tag
    build: { target: $f, args: { CONTEXT: $c } }
    container_name: agent-$n
    env_file: $env
    environment: { AGENT_PERSONA: "$n", AGENT_COLOUR: ${P_COLOUR[i]}$more }
    labels: { agent.persona: "$n", agent.flavour: $f, agent.context: $c }
    volumes:
      - $n-home:/home/dev
EOF
      if [[ "$f" == claude && -f personas/claude.json ]]; then
        cat <<'EOF'
      # Claude Code settings for every Claude persona, personas/claude.json, as a managed drop-in: they
      # win over the persona's own settings. A single-file mount, so a change to it (a git pull too)
      # needs `nexus <persona> restart`.
      - { type: bind, source: ./personas/claude.json, target: /etc/claude-code/managed-settings.d/15-personas.json, read_only: true, bind: { create_host_path: false } }
EOF
      fi
      if [[ "$f" == claude && "${P_STATUSLINE[i]}" == yes ]]; then
        cat <<'EOF'
      # Claude Code statusline: the script's directory, so edits and pulls show up live, and its
      # setting as a managed drop-in (beats a user statusLine). The drop-in is a single-file mount,
      # so a change to statusline/claude/settings.json needs `nexus <persona> restart`.
      - ./statusline/claude:/etc/claude-code/statusline:ro
      - ./statusline/claude/settings.json:/etc/claude-code/managed-settings.d/20-statusline.json:ro
EOF
      elif [[ "$f" == grok ]]; then
        if [[ "${P_STATUSLINE[i]}" == yes ]]; then
          cat <<'EOF'
      # grok's statusline: the script's directory, so edits and pulls show up live.
      - ./statusline/grok:/etc/grok/statusline:ro
EOF
        fi
        cat <<EOF
      # grok's managed config, written by config.sh: personas/grok.toml, then the statusline's
      # [ui.status_line] with the statusline on. A key in the persona's ~/.grok/config.toml wins.
      - { type: bind, source: ./personas/$n.grok.toml, target: /etc/grok/managed_config.toml, read_only: true, bind: { create_host_path: false } }
EOF
      fi
      if [[ "$k" == none ]]; then
        cat <<'EOF'
      # No ssh: your ssh-agent isn't mounted (/dev/null takes the socket's place, SSH_AUTH_SOCK is
      # empty), and its ssh setting makes git over ssh fail at once rather than ask for a password.
      - ./ssh/none.conf:/etc/ssh/ssh_config.d/50-nexus-none.conf:ro
      - { type: bind, source: /dev/null, target: /ssh-agent }
EOF
      elif [[ -n "$k" ]]; then
        [[ "$k" == /* ]] || k="./$k"
        cat <<EOF
      # Its own ssh key, read-only, and the ssh setting that makes ssh (and git over ssh) use it. Your
      # ssh-agent isn't mounted: /dev/null takes the socket's place, and SSH_AUTH_SOCK is empty.
      - type: bind
        source: '$k'
        target: /run/nexus/ssh-key
        read_only: true
        bind: { create_host_path: false }
      - ./ssh/key.conf:/etc/ssh/ssh_config.d/50-nexus-key.conf:ro
      - { type: bind, source: /dev/null, target: /ssh-agent }
EOF
      fi
    done
    say ""
    say "volumes:"
    for n in "${P_NAME[@]}"; do say "  \"$n-home\":"; done
  } > "$1"
}

write_env_file() {  # write_env_file NAME FLAVOUR: a staged personas/NAME.env from personas/FLAVOUR.env.example
  local n="$1" f="$2" tmp auth key name email gh
  if (( YES )); then say "-- personas/$n.env"; else section "$n: sign-in and identity (personas/$n.env)"; fi
  stage tmp "personas/$n.env" "personas/$f.env.example"
  STAGED[$n]="$tmp"
  if [[ "$f" == claude ]]; then
    choose auth "How does $n sign in to Claude?" login \
      "login    interactively, inside the persona (a Claude subscription; kept in its home volume)" \
      "apikey   an Anthropic API key (Console / API billing)" \
      "token    a long-lived token from \`claude setup-token\` (Pro/Max/Team/Enterprise plan)"
    case "$auth" in
      apikey) secret key "ANTHROPIC_API_KEY"; [[ -n "$key" ]] && set_kv "$tmp" ANTHROPIC_API_KEY "$key" ;;
      token)  secret key "CLAUDE_CODE_OAUTH_TOKEN"; [[ -n "$key" ]] && set_kv "$tmp" CLAUDE_CODE_OAUTH_TOKEN "$key" ;;
    esac
    [[ "$auth" == login || -n "$key" ]] || LATER+=("$n: no key entered; add it to $AGENTS_DIR/personas/$n.env, or log in inside the persona")
  else
    secret key "XAI_API_KEY"
    if [[ -n "$key" ]]; then set_kv "$tmp" XAI_API_KEY "$key"; else LATER+=("$n: add XAI_API_KEY to $AGENTS_DIR/personas/$n.env"); fi
  fi
  ask name  "git author name for $n"  "$(git config --global user.name 2>/dev/null || true)"
  ask email "git author email for $n" "$(git config --global user.email 2>/dev/null || true)"
  if [[ -n "$name" && -n "$email" ]]; then
    set_kv "$tmp" GIT_AUTHOR_NAME "$name";   set_kv "$tmp" GIT_COMMITTER_NAME "$name"
    set_kv "$tmp" GIT_AUTHOR_EMAIL "$email"; set_kv "$tmp" GIT_COMMITTER_EMAIL "$email"
  else
    LATER+=("$n: set the git identity in $AGENTS_DIR/personas/$n.env")
  fi
  secret gh "GH_TOKEN for the gh CLI in $n"
  if [[ -n "$gh" ]]; then set_kv "$tmp" GH_TOKEN "$gh"; fi
}

# =============================================================================================
(( EUID != 0 )) || die "run this as the VM user who will use the personas, not as root (it asks for sudo only to link the launcher)"
if (( ! LIST )); then
  exec 9< "$SELF"   # one run at a time per checkout: a lock on this script, no lock file
  flock -n 9 || die "another config.sh, or a \`nexus <persona> upgrade\`, is running in $AGENTS_DIR; let it finish first"
fi
FIRST=1; [[ -f "$PERSONAS_FILE" ]] && FIRST=0   # no registry yet: a first setup, or one from the fixed layout
load
if (( LIST )); then list; exit 0; fi
OLD_NAMES=("${P_NAME[@]}")

# --- preflight -------------------------------------------------------------------------------
command -v docker >/dev/null || die "Docker isn't installed: Docker Engine + compose plugin, https://docs.docker.com/engine/install/"
docker info >/dev/null 2>&1 \
  || die "can't reach Docker as $(id -un): is it running, and are you in the docker group? (sudo usermod -aG docker \"\$USER\", then log in again)"
docker compose version >/dev/null 2>&1 || die "the docker compose plugin is missing (docker-compose-plugin)"
rc=0; ssh-add -l >/dev/null 2>&1 || rc=$?
case $rc in
  1) warn "your ssh-agent holds no keys: \`ssh-add\` before starting a persona that uses it, or git over ssh fails inside" ;;
  2) warn "no ssh-agent in this shell: starting a persona that uses one needs it (connect with ssh -A, or run one on the VM)" ;;
esac
VM_DDEV="$(command -v ddev >/dev/null && ddev --version 2>/dev/null | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -n 1 || true)"
DEFAULT_CONTEXT=docker; [[ -n "$VM_DDEV" ]] && DEFAULT_CONTEXT=ddev

# Nothing below writes a file until the commit step: answers go into staged temp files.
# --- personas --------------------------------------------------------------------------------
say "config.sh: setting up $AGENTS_DIR${VM_DDEV:+ (ddev $VM_DDEV on this VM)}"
VM_ROOT_HINT="${OPT_ROOT:-$(get_kv .env PROJECTS_ROOT)}"   # for ask_root; the VM's root is asked after the personas
if [[ -n "$VM_ROOT_HINT" ]]; then VM_ROOT_HINT="now $VM_ROOT_HINT"; else VM_ROOT_HINT="asked below"; fi
for n in "${REMOVE[@]}"; do idx "$n" >/dev/null || die "--remove $n: no such persona"; drop "$n"; done
for p in "${ADD[@]}"; do add_flag "$p"; done
for p in "${SSHS[@]}"; do ssh_flag "$p"; done
if (( ! YES )); then
  section "Personas"
  adopt_interactive
  if (( ! ${#P_NAME[@]} )); then say "No personas yet; the first one:"; add_interactive; fi
  persona_menu
fi
(( ${#P_NAME[@]} )) || warn "no personas configured; add some with ./config.sh (or --persona NAME:FLAVOUR:CONTEXT)"
for i in "${!P_NAME[@]}"; do
  n="${P_NAME[i]}"
  if [[ -n "${LOADED[$n]+x}" && "${LOADED[$n]}" != "${P_FLAVOUR[i]} ${P_CONTEXT[i]} ${P_STATUSLINE[i]} ${P_ROOT[i]} ${P_SSH[i]}" ]]; then
    LATER+=("$n: changed; \`nexus $n restart\` applies it (that ends its tmux sessions)")
  fi
done
for n in "${OLD_NAMES[@]}"; do
  idx "$n" >/dev/null && continue
  what="env file"; [[ ! -e "personas/$n.key" ]] || what+=", ssh key"
  LATER+=("$n: removed. Its $what and home volume stay; to delete them:"
          "    docker rm -f agent-$n; docker volume rm agents_$n-home; rm -f $(printf '%q ' "$AGENTS_DIR/personas/$n".{env,key,key.pub,grok.toml})")
done

# --- per-VM settings: .env -------------------------------------------------------------------
stage ENV_NEW .env "$([[ -e .env ]] && echo .env || echo .env.example)"
# The old template set GROK_TAG=latest; emptied, the grok tag follows GROK_VERSION as Claude's follows CLAUDE_VERSION.
[[ "$(get_kv "$ENV_NEW" GROK_TAG)" != latest ]] || set_kv "$ENV_NEW" GROK_TAG ""
root="${OPT_ROOT:-$(get_kv "$ENV_NEW" PROJECTS_ROOT)}"; root="${root:-$HOME/projects}"
registry="${OPT_REGISTRY:-$(get_kv "$ENV_NEW" REGISTRY)}"; registry="${registry:-local}"
cv="${OPT_CLAUDE:-$(get_kv "$ENV_NEW" CLAUDE_VERSION)}"; cv="${cv:-latest}"
section "VM settings, shared by every persona"
if (( ! YES && ! FIRST )) && [[ -e .env ]] \
   && yesno "Keep them? projects root $root, image registry $registry$(has claude && echo ", Claude Code $cv")" y; then
  DEFAULTS=1   # still checked below, just not asked
fi
while :; do
  ask root "The VM's projects root, for personas without their own (mounted at the same path inside)" "$root"
  norm_dir root
  if ! root_ok "$root"; then
    why="the projects root must be an absolute directory other than /, without ':', not '$root'"
  elif ! mkdir -p "$root" 2>/dev/null; then
    why="can't create the projects root '$root'"
  else
    break
  fi
  (( ! YES )) || die "$why"
  say "  $why"; root="$HOME/projects"
done
set_kv "$ENV_NEW" PROJECTS_ROOT "$root"
while :; do
  ask registry "Image registry (local = build on this VM)" "$registry"
  [[ "$registry" != ?*/ ]] || registry="${registry%/}"
  registry="${registry:-local}"
  valid_registry "$registry" && break
  (( ! YES )) || die "the image registry must be local or host[:port][/path], not '$registry'"
  say "  local, or a registry like registry.lan:5000 (or registry.lan:5000/team)"; registry=local
done
set_kv "$ENV_NEW" REGISTRY "$registry"
if has claude; then
  while :; do
    ask cv "Claude Code version, for the claude personas: latest | stable | X.Y.Z" "$cv"
    cv="${cv:-latest}"
    valid_cv "$cv" && break
    (( ! YES )) || die "the Claude Code version must be latest, stable or X.Y.Z, not '$cv'"
    say "  latest, stable or a version like 2.1.302"; cv=latest
  done
  set_kv "$ENV_NEW" CLAUDE_VERSION "$cv"
elif [[ -n "$OPT_CLAUDE" ]]; then
  set_kv "$ENV_NEW" CLAUDE_VERSION "$OPT_CLAUDE"   # no Claude persona yet; there for the first one
fi
if has ddev; then
  dv="$(get_kv "$ENV_NEW" DDEV_VERSION)"; dv="${VM_DDEV:-$dv}"
  [[ -n "$dv" ]] || warn "ddev isn't installed on this VM: the ddev personas get the newest ddev; install the same on the VM (pkg.ddev.com) so both agree, and for mkcert"
  set_kv "$ENV_NEW" DDEV_VERSION "$dv"
fi
DEFAULTS=0
cpus="$(get_kv "$ENV_NEW" AGENT_CPUS)"; n="$(nproc)"
if ! [[ "$cpus" =~ ^([0-9]+(\.[0-9]+)?|\.[0-9]+)$ ]] || awk -v c="$cpus" -v n="$n" 'BEGIN { exit !(c + 0 <= 0 || c + 0 > n + 0) }'; then
  set_kv "$ENV_NEW" AGENT_CPUS "$(( n < 4 ? n : 4 ))"
fi

# --- per persona: sign-in and identity, for those without a fitting env file ------------------
BACKUP=" "
for i in "${!P_NAME[@]}"; do
  n="${P_NAME[i]}" f="${P_FLAVOUR[i]}" file="personas/${P_NAME[i]}.env"
  if [[ -f "$file" ]]; then
    if [[ -n "${LOADED[$n]+x}" ]]; then
      [[ "${LOADED[$n]%% *}" != "$f" ]] || continue   # same flavour: kept as it is
      why="$n changed flavour"
    else   # left over from a removed persona or the earlier layout
      have="$(env_flavour "$file")"
      if [[ -z "$have" || "$have" == "$f" ]]; then say "-- $file already exists${have:+ (a $have one)}: reusing it"; continue; fi
      why="$file is for a $have persona"
    fi
    if (( YES )); then
      say "-- $why: writing a new $file (the old one is kept as a .bak)"
    elif ! yesno "$why: write a new $file (the old one is kept as a .bak)?" y; then
      continue
    fi
    BACKUP+="$n "
  fi
  write_env_file "$n" "$f"
done

# --- per grok persona: its managed config, personas/<name>.grok.toml ---------------------------
# grok's fleet-default layer (/etc/grok/managed_config.toml inside): personas/grok.toml, the grok
# settings for every grok persona here, then the statusline's [ui.status_line] if the persona has it
# on (unless personas/grok.toml has one). A key in the persona's own ~/.grok/config.toml wins. Each is
# checked before anything is written; written in place, it reaches a running persona's next grok.
declare -A GSTAGED=()
if has claude && [[ -f personas/claude.json ]]; then
  settings_problem personas/claude.json json personas/claude.json
  [[ -z "$WHY" ]] || die "personas/claude.json isn't JSON Claude Code can read, so nothing was written: $WHY"
fi
if has grok && [[ -f personas/grok.toml ]]; then   # on its own first, so a message's line numbers are the file's
  settings_problem personas/grok.toml toml personas/grok.toml
  [[ -z "$WHY" ]] || die "personas/grok.toml isn't TOML grok can read, so nothing was written (grok would stop on it): $WHY"
fi
for i in "${!P_NAME[@]}"; do
  [[ "${P_FLAVOUR[i]}" == grok ]] || continue
  n="${P_NAME[i]}"
  stage tmp "personas/$n.grok.toml"
  {
    say "# /etc/grok/managed_config.toml in grok persona $n, written by config.sh from personas/grok.toml"
    say "# (grok settings for every grok persona here) and, with the statusline on, statusline/grok/settings.toml."
    say "# Edit those and re-run config.sh, not this file. A key in the persona's ~/.grok/config.toml wins."
    if [[ -f personas/grok.toml ]]; then say ""; cat personas/grok.toml; say ""; fi
    if [[ "${P_STATUSLINE[i]}" == yes ]] && ! grok_sets_statusline personas/grok.toml; then say ""; cat statusline/grok/settings.toml; fi
  } > "$tmp"
  settings_problem "$tmp" toml personas/grok.toml
  [[ -z "$WHY" ]] || die "personas/grok.toml is TOML, but with the statusline config.sh adds for $n it isn't (does it set ui.status_line in a way config.sh missed?), so nothing was written; at personas/$n.grok.toml as it would be: $WHY"
  GSTAGED[$n]="$tmp"
done
for f in $(printf '%s\n' "${UNCHECKED[@]}" | sort -u); do
  warn "couldn't check $f (no python3 for it here, nor a persona image yet): a broken one stops the CLI; re-run config.sh after the build to check it"
done

# --- stage compose.personas.yml and check the whole result ------------------------------------
files=(-f compose.yml)
if (( ${#P_NAME[@]} )); then
  stage PERSONAS_NEW "$PERSONAS_FILE"; chmod 644 "$PERSONAS_NEW"; write_personas "$PERSONAS_NEW"
  stage PERSONAS_CHECK "$PERSONAS_FILE"; write_personas "$PERSONAS_CHECK" staged
  files+=(-f "$PERSONAS_CHECK")
fi
# What the launcher will hand compose, with the staged .env and env files, so a bad result fails
# here and not at the first start.
if ! err="$(env -u PROJECTS_ROOT AGENT_UID="$(id -u)" AGENT_GID="$(id -g)" DOCKER_GID="$(stat -c %g /var/run/docker.sock)" \
            SSH_AUTH_SOCK=/dev/null AGENT_CA_CERTS="" \
            docker compose --env-file "$ENV_NEW" -p agents "${files[@]}" --profile all config -q 2>&1)"; then
  die "the new settings don't pass docker compose config, so nothing was written:"$'\n'"$err"
fi

# --- the rest of the questions: launcher link, mkcert, images ---------------------------------
if [[ "$LINK" != none ]] || (( BUILD && ${#P_NAME[@]} )) || { has ddev && [[ ! -f "$HOME/.local/share/mkcert/rootCA.pem" ]]; }; then
  section "Launcher and images"
fi
if (( ! YES )) && [[ "$LINK" != none ]]; then
  while :; do
    ask LINK "Link the launcher as <dir>/nexus (none: skip)" "$LINK"
    [[ "$LINK" == none ]] && break
    norm_dir LINK
    [[ "$LINK" == /* ]] && break
    say "  an absolute directory, e.g. /usr/local/bin or ~/.local/bin; none to skip"; LINK=/usr/local/bin
  done
fi
link=0
if [[ "$LINK" != none ]]; then
  target="${LINK%/}/nexus"
  if [[ "$(readlink -f "$target" 2>/dev/null)" == "$AGENTS_DIR/nexus" ]]; then
    :
  elif [[ -e "$target" || -L "$target" ]] && ! yesno "$target already exists ($(readlink -f "$target" 2>/dev/null || echo a file)); replace it?" n; then
    LATER+=("launcher: not linked; run it as $AGENTS_DIR/nexus")
  else
    link=1
  fi
fi
mkcert=0
if has ddev && [[ ! -f "$HOME/.local/share/mkcert/rootCA.pem" ]]; then
  if command -v mkcert >/dev/null && (( ! YES )) && yesno "Trust ddev's local CA on this VM now (mkcert -install)?" y; then
    mkcert=1
  else
    LATER+=("ddev: run \`mkcert -install\` on this VM (it comes with ddev), then \`nexus <persona> restart\`, or https://*.ddev.site stays untrusted inside")
  fi
fi
verb=build; [[ "$registry" != local ]] && verb=pull
images=0
if (( BUILD && ${#P_NAME[@]} )) && yesno "$([[ $verb == build ]] && echo Build || echo Pull) the images now? (the first build takes a few minutes)" y; then
  images=1
  # A full Docker disk breaks a build halfway, often as apt's "At least one invalid signature was
  # encountered". Checked only where Docker's data is on this machine (not Docker Desktop or a remote host).
  droot="$(docker info -f '{{.DockerRootDir}}' 2>/dev/null || true)"
  if [[ -n "$droot" && -d "$droot" ]] && free="$(df -Pk "$droot" 2>/dev/null | awk 'NR == 2 { print int($4 / 1048576) }')" \
     && [[ -n "$free" ]] && (( free < MIN_FREE_GB )); then
    say "  Only $free GB is free for Docker ($droot); a $verb from scratch needs about $MIN_FREE_GB GB, and a full disk"
    say "  fails it halfway, often as apt's \"At least one invalid signature was encountered\"."
    say "  Free space first: \`docker system df\` shows what's used; \`docker builder prune\` and \`docker image prune\` free it."
    if (( ! YES )) && ! yesno "$([[ $verb == build ]] && echo Build || echo Pull) anyway?" n; then
      images=0 cmds="" seen=" "
      for i in "${!P_NAME[@]}"; do
        key="${P_FLAVOUR[i]}-${P_CONTEXT[i]}"; [[ "$seen" == *" $key "* ]] && continue; seen+="$key "
        cmds+="${cmds:+; }$AGENTS_DIR/nexus ${P_NAME[i]} $verb"
      done
      LATER+=("images: skipped for lack of disk space; after freeing some, run: $cmds")
    fi
  fi
fi

# --- commit: everything is answered and checked, so the staged files go in place --------------
# Every target (and the dirs that get the backups) must be writable first, or a commit would stop halfway.
for t in . personas .env "$PERSONAS_FILE" $(for n in "${!STAGED[@]}"; do echo "personas/$n.env"; done) \
         $(for n in "${!GSTAGED[@]}"; do echo "personas/$n.grok.toml"; done); do
  if [[ -e "$t" && ! -w "$t" ]]; then die "can't write $AGENTS_DIR/$t (check its owner and mode), so nothing was written"; fi
done
trap '' INT TERM HUP   # all of it or none of it
for n in "${!STAGED[@]}"; do
  bak=""; [[ "$BACKUP" != *" $n "* ]] || bak="$(bakname "personas/$n.env")"
  commit "${STAGED[$n]}" "personas/$n.env" ${bak:+"$bak"}
done
# Claude personas whose service had no personas/claude.json mount yet get it with their next (re)create.
if [[ -f personas/claude.json ]] && ! grep -qF './personas/claude.json,' "$PERSONAS_FILE" 2>/dev/null; then
  for i in "${!P_NAME[@]}"; do
    if [[ "${P_FLAVOUR[i]}" == claude && -n "${LOADED[${P_NAME[i]}]+x}" ]]; then
      LATER+=("${P_NAME[i]}: it gets personas/claude.json as managed settings now; \`nexus ${P_NAME[i]} restart\` brings them in")
    fi
  done
fi
commit "$ENV_NEW" .env
for n in "${!GSTAGED[@]}"; do
  t="personas/$n.grok.toml"
  i="$(idx "$n")"
  if [[ -n "${LOADED[$n]+x}" && ! -e "$t" ]]; then
    LATER+=("$n: its grok settings$([[ ${P_STATUSLINE[i]} == yes ]] && echo " and statusline") come through $t now; \`nexus $n restart\` brings them in")
  elif [[ -n "${LOADED[$n]+x}" && "${LOADED[$n]}" == "${P_FLAVOUR[i]} ${P_CONTEXT[i]} ${P_STATUSLINE[i]} ${P_ROOT[i]} ${P_SSH[i]}" ]] \
       && ! cmp -s "${GSTAGED[$n]}" "$t"; then
    LATER+=("$n: its grok settings changed; the next grok started in it reads them")   # else: changed, and told to restart
  fi
  commit "${GSTAGED[$n]}" "$t"
done
if (( ${#P_NAME[@]} )); then
  commit "$PERSONAS_NEW" "$PERSONAS_FILE" "$PERSONAS_FILE.bak"
elif [[ -f "$PERSONAS_FILE" ]]; then
  mv "$PERSONAS_FILE" "$PERSONAS_FILE.bak"
fi
trap 'exit 130' INT TERM HUP
# From here on a failure is a line in the summary, not the end of the run.

# --- ssh keys made for personas: only those not there yet, so a key added somewhere stays valid ------
for i in "${!P_NAME[@]}"; do
  n="${P_NAME[i]}" k="${P_SSH[i]}"
  [[ "$k" == "personas/$n.key" && ! -e "$k" ]] || continue
  if ssh-keygen -q -t ed25519 -N "" -C "$n@${HOSTNAME%%.*} (nexus)" -f "$k" </dev/null && [[ -f "$k.pub" ]]; then
    say "-- made $k, the ssh key of $n"
    then="then git over ssh works inside"
    # An existing persona's container may run, with the key it started with mounted until it is recreated.
    [[ -z "${LOADED[$n]+x}" ]] || then="then \`nexus $n restart\` (a running one keeps its old key until then)"
    LATER+=("$n: add its public key on your git host, as a deploy key (a repo's settings; write access to push)"
            "    or to a machine user's account, $then:"
            "    $(cat "$k.pub")")
  else
    LATER+=("$n: making its ssh key failed (see above); retry with: ssh-keygen -t ed25519 -N '' -f $(printf %q "$AGENTS_DIR/$k")")
  fi
done

# --- the launcher on PATH --------------------------------------------------------------------
SUDO_OPTS=(); (( YES )) && SUDO_OPTS=(-n)   # --yes never waits for a password
if (( link )); then
  if mkdir -p "$LINK" 2>/dev/null && ln -sfn "$AGENTS_DIR/nexus" "$target" 2>/dev/null; then
    :
  elif say "-- linking $target needs sudo" \
       && { [[ -d "$LINK" ]] || sudo "${SUDO_OPTS[@]}" mkdir -p "$LINK"; } \
       && sudo "${SUDO_OPTS[@]}" ln -sfn "$AGENTS_DIR/nexus" "$target"; then
    :
  else
    retry="sudo ln -sfn $(printf %q "$AGENTS_DIR/nexus") $(printf %q "$target")"
    [[ -d "$LINK" ]] || retry="sudo mkdir -p $(printf %q "$LINK") && $retry"
    LATER+=("launcher: couldn't link $target; run: $retry")
  fi
fi
if [[ "$LINK" != none ]]; then
  case ":$PATH:" in *":${LINK%/}:"*) ;; *) LATER+=("launcher: ${LINK%/} isn't on your PATH") ;; esac
  a="$(command -v nexus || true)"
  if [[ -n "$a" && "$(readlink -f "$a")" != "$AGENTS_DIR/nexus" ]]; then
    LATER+=("launcher: \`nexus\` runs $a, not this launcher; put ${LINK%/} first in PATH, or use $AGENTS_DIR/nexus")
  fi
  # The launcher used to be called agent: a link of that name into this checkout dangles now.
  old="${LINK%/}/agent"
  if [[ -L "$old" && "$(readlink -f "$old" 2>/dev/null)" == "$AGENTS_DIR/agent" ]]; then
    if rm -f "$old" 2>/dev/null || sudo "${SUDO_OPTS[@]}" rm -f "$old"; then
      say "-- removed $old, the launcher's old name (it is nexus now)"
    else
      LATER+=("launcher: remove its old link: sudo rm $(printf %q "$old")")
    fi
  fi
fi

# --- ddev's CA, for the ddev personas --------------------------------------------------------
if (( mkcert )); then
  mkcert -install || LATER+=("ddev: \`mkcert -install\` failed (see above); run it again on this VM, then \`nexus <persona> restart\`")
fi

# --- images ----------------------------------------------------------------------------------
# Every persona's image, not only a new one's: an image that is up to date takes seconds (the build
# cache, or a pull with nothing new), and one whose pins or Dockerfile changed is brought up to date.
failed=0
if (( images )); then
  say "-- ${verb}ing the image of every persona; one that is already up to date takes seconds"
  seen=" "
  for i in "${!P_NAME[@]}"; do
    key="${P_FLAVOUR[i]}-${P_CONTEXT[i]}"
    [[ "$seen" == *" $key "* ]] && continue
    seen+="$key "
    users=""
    for j in "${!P_NAME[@]}"; do
      [[ "${P_FLAVOUR[j]}-${P_CONTEXT[j]}" == "$key" ]] && users+="${users:+, }${P_NAME[j]}"
    done
    say "-- $verb agent-$key, for $users"
    "$AGENTS_DIR/nexus" "${P_NAME[i]}" "$verb" 9<&- \
      || { failed=1; LATER+=("images: the $verb of agent-$key failed (see above); retry with: $AGENTS_DIR/nexus ${P_NAME[i]} $verb"); }
  done
fi

# --- done ------------------------------------------------------------------------------------
say ""
say "Personas:"; list
if (( ${#P_NAME[@]} )); then
  say ""
  say "Start one from a project directory:  cd ${P_ROOT[0]:-$root}/<project> && nexus ${P_NAME[0]}"
fi
for l in "${LATER[@]}"; do say "$l"; done
say "Day-to-day use, upgrades and troubleshooting: runbook.md"
exit "$failed"
