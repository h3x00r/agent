#!/usr/bin/env bash
# tests/run.sh — end-to-end tests for nexus and config.sh on this VM, in a scratch copy of this
# checkout: the real setup (.env, the personas, their containers and logins) is never read or changed.
#
#   tests/run.sh                  every stage this VM can run; one it can't is skipped, saying why
#   tests/run.sh --no-containers  lint, config.sh and nexus without starting a persona (seconds)
#   tests/run.sh --image REF      the Claude persona image to run the test personas on (default: the
#                                 newest agent-claude-docker image here, else agent-claude-ddev)
#   tests/run.sh --keep           leave the scratch dir, containers, volumes and tags, for a look after
#
# Stages:
#   lint       bash -n; shellcheck and hadolint, where installed or their images are here (never pulled)
#   config.sh  --yes: refused names, specs and ssh keys, a setup, --list, --remove, kept env files and keys
#   nexus      before any persona runs: usage, errors, ls, top, upgrade's argument checks
#   personas   a Claude persona against a stand-in Messages API (tests/fake-api.py, in a container of
#              its own) and a grok persona with a stand-in grok (tests/fake-grok), both on that one
#              image: headless runs (prompt, piped input, flags, directories, exit codes, Ctrl-C, TERM,
#              hangup, detached daemons, concurrent runs), sessions, ls, top, start and stop; the grok
#              persona has an ssh key of its own, the Claude one this run's (empty) ssh-agent, and a
#              third one, on the grok persona's image, no ssh at all
#
# What it makes is named after its pid: personas nxt-<pid>-c, -g and -n, image tags
# nxt-<pid>.invalid/…, their home volumes, the API's container nxt-<pid>-api and a throwaway ssh-agent;
# exactly those are removed at the end. The test personas share compose project "agents" with the real
# ones, which compose then calls orphans: a warning only, as COMPOSE_* variables are cleared for the
# tests (COMPOSE_REMOVE_ORPHANS would act on it).
# Needs Docker with compose and ssh-keygen; for the personas stage also ssh-agent, script (util-linux)
# and an image built for this user's uid. All of it takes about two minutes.
#
# The checks and waits are bash in single quotes, evaluated when they run: shellcheck can't see into them.
# shellcheck disable=SC2016,SC2034,SC2329
set -uo pipefail     # no -e: a failed check is counted, and the run goes on
shopt -s lastpipe    # in `producer | t nexus …`, t runs in this shell, so RC, OUT and ERR stay set

REPO="$(cd "$(dirname "$(readlink -f "$0")")/.." && pwd)"
IMAGE="" KEEP=0 CONTAINERS=1
while (( $# )); do
  case "$1" in
    --image)         [[ -n "${2-}" ]] || { echo "tests: --image needs an image" >&2; exit 2; }; IMAGE="$2"; shift ;;
    --no-containers) CONTAINERS=0 ;;
    --keep)          KEEP=1 ;;
    -h|--help)       awk 'NR > 1 && !/^#/ { exit } NR > 1 { sub(/^# ?/, ""); print }' "$0"; exit 0 ;;
    *)               echo "tests: unknown option '$1' (see --help)" >&2; exit 2 ;;
  esac
  shift
done

# --- checks ----------------------------------------------------------------------------------------
PASS=0 FAILS=() SKIPS=()
ok()    { PASS=$(( PASS + 1 )); printf 'ok    %s\n' "$1"; }
fail()  { FAILS+=("$1"); printf 'FAIL  %s\n' "$1"; [[ -z "${2-}" ]] || printf '%s\n' "$2" | head -n 40 | sed 's/^/      | /'; }
skip()  { SKIPS+=("$1"); printf 'skip  %s\n' "$1"; }
stage() { printf '\n== %s\n' "$1"; }
summary() {
  local f
  printf '\n%d passed, %d failed, %d skipped\n' "$PASS" "${#FAILS[@]}" "${#SKIPS[@]}"
  for f in "${FAILS[@]}"; do printf '  FAIL  %s\n' "$f"; done
  for f in "${SKIPS[@]}"; do printf '  skip  %s\n' "$f"; done
  exit $(( ${#FAILS[@]} ? 1 : 0 ))
}

# t CMD…: run CMD, with its exit code, stdout and stderr in RC, OUT and ERR (carriage returns dropped).
RC=0 OUT="" ERR=""
t() { "$@" > "$T/out" 2> "$T/err"; RC=$?; OUT="$(tr -d '\r' < "$T/out")"; ERR="$(tr -d '\r' < "$T/err")"; }
# check DESCRIPTION EXPR: EXPR (bash, evaluated) holding is a pass; a fail shows the last run.
check() {
  if eval "$2"; then ok "$1"; else fail "$1" "expected: $2"$'\n'"exit code: $RC"$'\n'"stdout: $OUT"$'\n'"stderr: $ERR"; fi
}
rc()       { (( RC == $1 )); }
out_is()   { [[ "$OUT" == "$1" ]]; }
out_has()  { [[ "$OUT" == *"$1"* ]]; }
out_line() { grep -qxF -- "$1" <<< "$OUT"; }
out_re()   { grep -qE -- "$1" <<< "$OUT"; }
err_has()  { [[ "$ERR" == *"$1"* ]]; }
# wait_for SECONDS EXPR: true once EXPR (evaluated every 0.2 s) holds; false if it doesn't in time.
wait_for() { local i; for (( i = 0; i < $1 * 5; i++ )); do eval "$2" && return 0; sleep 0.2; done; return 1; }

# --- the scratch checkout --------------------------------------------------------------------------
git -C "$REPO" rev-parse --is-inside-work-tree >/dev/null 2>&1 \
  || { echo "tests: $REPO isn't a git checkout, and the tests copy its files from git" >&2; exit 2; }
T="$(mktemp -d "${TMPDIR:-/tmp}/nxt.XXXXXX")" || exit 2
ID="nxt-$$"
PC="$ID-c" PG="$ID-g" PN="$ID-n" PX="$ID-x" REG="$ID.invalid" API="$ID-api"
A="$T/agents" P="$T/agents/compose.personas.yml" ROOT="$T/projects" S="$T/projects/.nxt-bin" SOCK="$T/ssh.sock"
AGENT_PID="" TAGS=() BG=() BG_PID=""

cleanup() {
  local p
  cd / || true
  for p in "${BG[@]}"; do kill "$p" 2>/dev/null; done
  if (( KEEP )); then
    echo "kept: $T; containers agent-$PC agent-$PG agent-$PN $API; volumes agents_$PC-home agents_$PG-home agents_$PN-home;" \
         "tags ${TAGS[*]:-none}; ssh-agent ${AGENT_PID:-none}"
    return
  fi
  if command -v docker >/dev/null; then
    docker rm -f "agent-$PC" "agent-$PG" "agent-$PN" "$API" >/dev/null 2>&1
    docker volume rm "agents_$PC-home" "agents_$PG-home" "agents_$PN-home" >/dev/null 2>&1
    for p in "${TAGS[@]}"; do docker image rm "$p" >/dev/null 2>&1; done   # the tag: the image keeps its own
  fi
  [[ -z "$AGENT_PID" ]] || kill "$AGENT_PID" 2>/dev/null
  rm -rf "$T" 2>/dev/null || echo "tests: couldn't remove all of $T" >&2
}
trap cleanup EXIT
trap 'exit 130' INT TERM HUP

mkdir -p "$A" "$T/home" "$T/tmp" "$T/outside" "$ROOT/app/sub" "$ROOT/my.app"
ln -s "$ROOT/app" "$T/link"
# The checkout as it stands, uncommitted changes included, without what git ignores (.env, personas/*.env).
git -C "$REPO" ls-files -z --cached --others --exclude-standard \
  | while IFS= read -r -d '' f; do if [[ -e "$REPO/$f" ]]; then printf '%s\0' "$f"; fi; done \
  | tar -C "$REPO" --null -T - -cf - | tar -C "$A" -xf - \
  || { echo "tests: can't copy the checkout to $A" >&2; exit 2; }

# nexus and config.sh run as from a fresh login: a scratch HOME and TMPDIR, this run's ssh-agent, and
# none of the variables a shell may export that would beat the scratch .env or steer compose.
NX_ENV=(-u PROJECTS_ROOT -u REGISTRY -u CLAUDE_VERSION -u CLAUDE_TAG -u GROK_VERSION -u GROK_TAG -u APM_VERSION
        -u DDEV_VERSION -u AGENTS_DIR -u AGENT_UID -u AGENT_GID -u DOCKER_GID -u AGENT_CPUS -u AGENT_MEMORY
        -u AGENT_CA_CERTS -u NO_COLOR -u XDG_RUNTIME_DIR)
for v in $(compgen -e | grep '^COMPOSE_'); do NX_ENV+=(-u "$v"); done
NX_ENV+=(HOME="$T/home" DOCKER_CONFIG="${DOCKER_CONFIG:-$HOME/.docker}" SSH_AUTH_SOCK="$SOCK" TMPDIR="$T/tmp"
         TERM=xterm-256color)
nx()  { env "${NX_ENV[@]}" "$A/nexus" "$@"; }
cfg() { env "${NX_ENV[@]}" "$A/config.sh" "$@"; }
# compose_t ARGS…: compose on the scratch checkout, as nexus calls it
compose_t() {
  env "${NX_ENV[@]}" AGENT_UID="$(id -u)" AGENT_GID="$(id -g)" DOCKER_GID="$(stat -c %g /var/run/docker.sock)" \
    PROJECTS_ROOT="$ROOT" docker compose -p agents -f "$A/compose.yml" -f "$P" "$@"
}

# --- lint ------------------------------------------------------------------------------------------
stage "lint"
for f in nexus config.sh entrypoint.sh statusline/claude/statusline.sh statusline/grok/statusline.sh tests/run.sh; do
  if out="$(bash -n "$REPO/$f" 2>&1)"; then ok "bash -n $f"; else fail "bash -n $f" "$out"; fi
done
if out="$(sh -n "$REPO/tests/fake-grok" 2>&1)"; then ok "sh -n tests/fake-grok"; else fail "sh -n tests/fake-grok" "$out"; fi
if command -v python3 >/dev/null; then
  if out="$(python3 -c 'import ast, sys; ast.parse(open(sys.argv[1]).read(), sys.argv[1])' "$REPO/tests/fake-api.py" 2>&1)"
  then ok "python3 parses tests/fake-api.py"; else fail "python3 parses tests/fake-api.py" "$out"; fi
fi

docker_ok=0
if command -v docker >/dev/null && docker info >/dev/null 2>&1 && docker compose version >/dev/null 2>&1 \
   && [[ -S /var/run/docker.sock ]]; then docker_ok=1; fi
SC=() HL=()
if command -v shellcheck >/dev/null; then SC=(shellcheck)
elif (( docker_ok )) && docker image inspect koalaman/shellcheck:stable >/dev/null 2>&1; then
  SC=(docker run --rm -v "$REPO:/mnt:ro" -w /mnt koalaman/shellcheck:stable)
fi
if (( ${#SC[@]} )); then
  for f in nexus config.sh entrypoint.sh statusline/grok/statusline.sh tests/run.sh tests/fake-grok; do
    if out="$(cd "$REPO" && "${SC[@]}" "$f" 2>&1)"; then ok "shellcheck $f"; else fail "shellcheck $f" "$out"; fi
  done
  # Claude's statusline.sh keeps its colours in printf formats (SC2059).
  if out="$(cd "$REPO" && "${SC[@]}" -e SC2059 statusline/claude/statusline.sh 2>&1)"; then ok "shellcheck statusline/claude/statusline.sh"
  else fail "shellcheck statusline/claude/statusline.sh" "$out"; fi
else
  skip "shellcheck: not installed, and no koalaman/shellcheck:stable image here"
fi
if command -v hadolint >/dev/null; then HL=(hadolint)
elif (( docker_ok )) && docker image inspect hadolint/hadolint:latest >/dev/null 2>&1; then
  HL=(docker run --rm -v "$REPO:/mnt:ro" -w /mnt hadolint/hadolint:latest hadolint)
fi
if (( ${#HL[@]} )); then
  if out="$(cd "$REPO" && "${HL[@]}" Dockerfile 2>&1)"; then ok "hadolint Dockerfile"; else fail "hadolint Dockerfile" "$out"; fi
else
  skip "hadolint: not installed, and no hadolint/hadolint:latest image here"
fi

if (( ! docker_ok )); then
  skip "config.sh, nexus, personas: Docker, its compose plugin or /var/run/docker.sock isn't usable here"
  summary
fi
if ! command -v ssh-keygen >/dev/null; then skip "config.sh, nexus, personas: no ssh-keygen (openssh-client) here"; summary; fi

# --- config.sh -------------------------------------------------------------------------------------
stage "config.sh --yes"
t cfg --help </dev/null
check "--help: the usage" 'rc 0 && out_has "--persona NAME:FLAVOUR:CONTEXT"'
for spec in top:claude:docker ls:claude:docker all:claude:docker start:claude:docker Nxt:claude:docker \
            "$ID-:claude:docker" "$ID-q:gpt:docker" "$ID-q:claude:k8s" "$ID-q:claude:docker:rel/dir"; do
  t cfg --yes --no-link --no-build --persona "$spec" </dev/null
  check "refuses --persona $spec" 'rc 1 && err_has "--persona $spec:"'
done
t cfg --yes --no-link --no-build --projects-root rel/dir </dev/null
check "refuses a relative --projects-root" 'rc 1 && err_has "--projects-root must be"'
# Keys to give: one that works, and three ssh would refuse.
mkdir -p "$T/keys"
if ! { ssh-keygen -q -t ed25519 -N "" -C nxt -f "$T/keys/ok" && ssh-keygen -q -t ed25519 -N "nxt secret" -f "$T/keys/pass" \
        && cp "$T/keys/ok" "$T/keys/open" && chmod 644 "$T/keys/open" && ln -s "$T/keys/ok" "$T/keys/link"; }; then
  fail "test keys in $T/keys"
fi
for k in "$PG" "$PG:rel/key" "$PG:$T/keys/open" "$PG:$T/keys/pass" "$PG:$T/keys/none" "$ID-nope:new"; do
  t cfg --yes --no-link --no-build --persona "$PG:grok:docker" --ssh "$k" </dev/null
  case "$k" in
    *:rel/key)  why="must be an absolute path" ;;     *open) why="others can read it" ;;
    *pass)      why="has a passphrase" ;;             *none) why="no readable file" ;;
    *nope:new)  why="no persona $ID-nope" ;;          *)     why="it takes NAME:agent" ;;
  esac
  check "refuses --ssh ${k#"$T/keys/"}: $why" 'rc 1 && err_has "--ssh $k: " && err_has "$why"'
done
check "  a refused run writes nothing" '[[ ! -e $A/.env && ! -e $P && ! -e $A/personas/$PG.key ]]'

# Settings for every Claude and every grok persona, in place of the committed ones.
printf '{"model": "sonnet", "effortLevel": "low"}\n' > "$A/personas/claude.json"
printf '[models]\ndefault_reasoning_effort = "xhigh"\n\n[ui]\ntheme = "grokday"\n' > "$A/personas/grok.toml"
t cfg --yes --no-link --no-build --projects-root "$ROOT" --registry "$REG" \
      --persona "$PC:claude:docker" --persona "$PG:grok:docker" --persona "$PX:claude:docker:$T/own" \
      --ssh "$PG:new" --ssh "$PX:$T/keys/link" </dev/null
check "sets up three personas" 'rc 0'
check "  a Claude one, statusline on, your agent" 'grep -qxE "# persona: $PC flavour=claude context=docker colour=colour[0-9]+ statusline=yes" "$P"'
check "  a grok one, statusline on, a key made for it" \
      'grep -qxE "# persona: $PG flavour=grok context=docker colour=colour[0-9]+ statusline=yes ssh=personas/$PG.key" "$P"'
check "  one with its own projects root, created, and a key given (a symlink to one)" \
      'grep -qxE "# persona: $PX flavour=claude context=docker colour=colour[0-9]+ statusline=yes root=$T/own ssh=$T/keys/link" "$P" && [[ -d $T/own ]]'
check "  the key made: private, and its public half shown to add" \
      '[[ $(stat -c %a "$A/personas/$PG.key") == 600 ]] && out_has "$(cut -d " " -f 1,2 "$A/personas/$PG.key.pub")"'
t cfg --yes --no-link --no-build --persona "$PN:grok:docker" --ssh "$PN:none" --no-statusline </dev/null
check "adds a fourth: grok, with --no-statusline and no ssh; the others as they were" \
      'rc 0 && grep -qxE "# persona: $PN flavour=grok context=docker colour=colour[0-9]+ statusline=no ssh=none" "$P" \
       && grep -qE "^# persona: $PG .* statusline=yes " "$P" && grep -qE "^# persona: $PC .* statusline=yes$" "$P"'
G="$A/personas/$PG.grok.toml" GN="$A/personas/$PN.grok.toml"
check "  the grok one's managed config: personas/grok.toml, then its statusline; private" \
      'grep -qx "theme = \"grokday\"" "$G" && grep -qx "command = \"/etc/grok/statusline/statusline.sh\"" "$G" \
       && [[ $(grep -c "^\[ui.status_line\]" "$G") == 1 && $(stat -c %a "$G") == 600 ]]'
check "  and with --no-statusline: the settings alone" 'grep -qx "theme = \"grokday\"" "$GN" && ! grep -q "status_line" "$GN"'
t compose_t --profile "$PG" config "$PG"
check "  the grok one's service: its statusline, through grok's managed config" \
      'rc 0 && out_has "source: $A/statusline/grok" && out_has "target: /etc/grok/statusline" \
       && out_has "target: /etc/grok/managed_config.toml" && ! out_has "claude-code"'
check "  and its key and ssh setting, /dev/null for the agent, SSH_AUTH_SOCK empty" \
      'rc 0 && out_has "target: /run/nexus/ssh-key" && out_has "target: /etc/ssh/ssh_config.d/50-nexus-key.conf" \
       && grep -B 1 -x "        target: /ssh-agent" <<< "$OUT" | grep -qx "        source: /dev/null" && out_has "SSH_AUTH_SOCK: \"\""'
t compose_t --profile "$PN" config "$PN"
check "  a persona without ssh: its ssh setting, /dev/null for the agent, SSH_AUTH_SOCK empty, no key; grok settings, no statusline" \
      'rc 0 && out_has "target: /etc/ssh/ssh_config.d/50-nexus-none.conf" && ! out_has "nexus/ssh-key" \
       && out_has "target: /etc/grok/managed_config.toml" && ! out_has "/etc/grok/statusline" \
       && grep -B 1 -x "        target: /ssh-agent" <<< "$OUT" | grep -qx "        source: /dev/null" && out_has "SSH_AUTH_SOCK: \"\""'
t compose_t --profile "$PC" config "$PC"
check "  the Claude one's: statusline/claude, at the path it always had" \
      'rc 0 && out_has "source: $A/statusline/claude" && out_has "target: /etc/claude-code/statusline" \
       && out_has "source: $A/statusline/claude/settings.json" && ! out_has "statusline/grok"'
check "  and personas/claude.json as a managed drop-in" \
      'out_has "source: $A/personas/claude.json" && out_has "target: /etc/claude-code/managed-settings.d/15-personas.json"'
check "  an agent persona's: the agent's socket, no key" \
      'rc 0 && grep -B 1 -x "        target: /ssh-agent" <<< "$OUT" | grep -qxF "        source: $SOCK" && ! out_has "nexus/ssh-key"'
check "  .env: the projects root and the registry" 'grep -qxF "PROJECTS_ROOT=$ROOT" "$A/.env" && grep -qxF "REGISTRY=$REG" "$A/.env"'
check "  env files, mode 600" '[[ $(stat -c %a "$A/personas/$PC.env") == 600 && $(stat -c %a "$A/personas/$PG.env") == 600 ]]'
t cfg --list </dev/null
check "--list: all four, and how they sign in over ssh" \
      'rc 0 && out_re "^  $PC .* your agent$" && out_re "^  $PG .* personas/$PG.key$" && out_re "^  $PN .* none$" \
       && out_re "^  $PX .* $T/keys/link$"'
echo "NXT_MARK=kept" >> "$A/personas/$PC.env"
pub="$(cat "$A/personas/$PG.key.pub")"
t cfg --yes --no-link --no-build --remove "$PX" --persona "$PC:claude:docker" --ssh "$PG:new" </dev/null
check "--remove, with a persona given again" \
      'rc 0 && ! grep -q "^# persona: $PX " "$P" && grep -q "^# persona: $PC " "$P" && grep -q "^# persona: $PG " "$P"'
check "  the removed one's env file stays" '[[ -f $A/personas/$PX.env ]]'
check "  an existing env file is kept as it is" 'grep -qxF NXT_MARK=kept "$A/personas/$PC.env"'
check "  and an existing key: new keeps it" '[[ $(cat "$A/personas/$PG.key.pub") == "$pub" ]] && ! out_has "made personas/$PG.key"'
mv "$A/personas/$PG.key" "$T/keys/pg"; mv "$A/personas/$PG.key.pub" "$T/keys/pg.pub"
t cfg --yes --no-link --no-build --ssh "$PG:new" </dev/null
check "a key made anew for an existing persona: shown, with the restart that brings it in" \
      'rc 0 && out_has "made personas/$PG.key" && out_has "nexus $PG restart"'
mv -f "$T/keys/pg" "$A/personas/$PG.key"; mv -f "$T/keys/pg.pub" "$A/personas/$PG.key.pub"   # the first one, for the tests below
check "  the personas file before it is backed up" '[[ -f $P.bak ]]'
printf '[ui\ntheme = "broken"\n' > "$A/personas/grok.toml"; cp "$G" "$T/g.before"
t cfg --yes --no-link --no-build </dev/null
if err_has "couldn't check personas/grok.toml"; then
  skip "a broken personas/grok.toml: no Python 3.11 here, nor a persona image, to check it with"
else
  check "a personas/grok.toml grok couldn't read: refused, nothing written" \
        'rc 1 && err_has "TOML grok can read" && err_has "line 1" && cmp -s "$G" "$T/g.before"'
fi
printf '[ui]\ntheme = "gr\xffokday"\n' > "$A/personas/grok.toml"; cp "$G" "$T/g.before"
t cfg --yes --no-link --no-build </dev/null
if err_has "couldn't check personas/grok.toml"; then skip "a personas/grok.toml that isn't UTF-8: nothing here to check it with"
else check "a personas/grok.toml that isn't UTF-8: refused too" 'rc 1 && err_has "TOML grok can read" && cmp -s "$G" "$T/g.before"'; fi
# With nothing to check TOML with (a VM's first setup: no persona image yet, a python3 older than 3.11),
# config.sh must say so rather than pass a file it couldn't check.
mkdir -p "$T/fakebin"
printf '#!/bin/sh\n[ "$1" = images ] && exit 0\nexec %q "$@"\n' "$(command -v docker)" > "$T/fakebin/docker"; chmod 755 "$T/fakebin/docker"
if python3 -c 'import tomllib' 2>/dev/null; then
  skip "the couldn't-check warning: this VM's python3 checks TOML itself"
else
  printf '[ui]\ntheme = "grokday"\n' > "$A/personas/grok.toml"
  t env "${NX_ENV[@]}" PATH="$T/fakebin:$PATH" "$A/config.sh" --yes --no-link --no-build </dev/null
  check "with nothing to check the TOML with: it says so" 'rc 0 && err_has "couldn'"'"'t check personas/grok.toml"'
fi
printf '[ui]\ntheme = "grokday"\n\n[ ui . status_line ]\ntype = "builtin"\n' > "$A/personas/grok.toml"
t cfg --yes --no-link --no-build </dev/null
check "a personas/grok.toml with a status line of its own: that one stands, once; a running persona reads it" \
      'rc 0 && ! grep -q "^\[ui.status_line\]" "$G" && grep -qx "type = \"builtin\"" "$G" \
       && out_has "$PG: its grok settings changed; the next grok started in it reads them"'
printf '[models]\ndefault_reasoning_effort = "xhigh"\n\n[ui]\ntheme = "grokday"\n' > "$A/personas/grok.toml"
t cfg --yes --no-link --no-build </dev/null
check "  and back: the statusline's again" 'rc 0 && grep -qx "command = \"/etc/grok/statusline/statusline.sh\"" "$G"'
cp "$A/personas/claude.json" "$T/claude.json"; printf '{"model": }\n' > "$A/personas/claude.json"; cp "$P" "$T/p.before"
t cfg --yes --no-link --no-build </dev/null
if err_has "couldn't check personas/claude.json"; then
  skip "a broken personas/claude.json: no python3 here, nor a persona image, to check it with"
else
  check "a personas/claude.json Claude Code couldn't read: refused, nothing written" \
        'rc 1 && err_has "JSON Claude Code can read" && cmp -s "$P" "$T/p.before"'
fi
rm "$A/personas/claude.json"; cfg --yes --no-link --no-build </dev/null >/dev/null 2>&1
t compose_t --profile "$PC" config "$PC"
check "without personas/claude.json: no drop-in" 'rc 0 && ! out_has "15-personas"'
cp "$T/claude.json" "$A/personas/claude.json"
t cfg --yes --no-link --no-build </dev/null
check "  and with it back: it is mounted again, and the persona is told to restart for it" \
      'rc 0 && grep -qF "./personas/claude.json," "$P" && out_has "$PC: it gets personas/claude.json as managed settings now"'

# --- nexus, nothing started ------------------------------------------------------------------------
stage "nexus, before any persona runs"
cd "$ROOT/app" || exit 2
t nx </dev/null
check "no arguments: the usage, exit 1" 'rc 1 && out_has "nexus <persona> headless"'
t nx --help </dev/null
check "--help: the usage, exit 0" 'rc 0 && out_has "nexus top"'
t nx "$ID-nope" </dev/null
check "an unknown persona" 'rc 1 && err_has "no persona" && err_has "$ID-nope"'
t nx 'Bad!' </dev/null
check "a name that can't be one" 'rc 1 && err_has "not a persona name"'
t nx ls </dev/null
check "ls: a table of every persona, not started; the removed one gone" \
      'rc 0 && out_re "^PERSONA +FLAVOUR +CONTEXT +SSH +STATE +PROJECTS ROOT$" && out_re "^$PC +claude +docker +agent +not started +$ROOT$" \
       && out_re "^$PG +grok +docker +own key +not started +$ROOT$" && out_re "^$PN +grok +docker +none +not started +$ROOT$" \
       && out_has "nexus <persona> starts one" && ! out_has "$PX" && ! out_has "SESSION"'
hdr="$(grep -E '^PERSONA +FLAVOUR' <<< "$OUT")" row="$(grep -E "^$PC " <<< "$OUT")"; x="${hdr%%STATE*}"
check "  its columns line up under their headers" '[[ ${row:${#x}:11} == "not started" && ${row:${#x}-1:1} == " " ]]'
t nx top --once </dev/null
check "top --once: a table of every persona, not started" \
      'rc 0 && out_has "nexus top" && out_re "^PERSONA +STATE +CPU +MEM +PANES$" && out_re "^$PC +not started +- +- +-$" \
       && out_re "^$PN +not started +- +- +-$" && out_has "no Claude Code or grok pane"'
t nx top -n 0 </dev/null
check "top: -n takes whole seconds" 'rc 1 && err_has "whole seconds"'
t nx top --bogus </dev/null
check "top: an unknown argument" 'rc 1 && err_has "unknown argument"'
t nx "$PC" upgrade 1.2 </dev/null
check "upgrade: neither a version nor a channel" 'rc 1 && err_has "neither a version"'
t nx "$PC" upgrade -x </dev/null
check "upgrade: an unknown option" 'rc 1 && err_has "unknown option"'
t nx "$PC" ls </dev/null
check "PERSONA ls before it runs" 'rc 1 && err_has "start it with"'
cp "$P" "$T/personas.yml"; printf '      - ./statusline:/etc/claude-code/statusline:ro\n' >> "$P"
t nx "$PC" start </dev/null
check "a start from a personas file from before the statusline split: refused, saying what to do" \
      'rc 1 && err_has "from before statusline/ was split" && err_has "config.sh"'
cp "$T/personas.yml" "$P"
mv "$A/personas/claude.json" "$T/claude.json"
t nx "$PC" start </dev/null
check "a start with personas/claude.json gone: refused, saying how to bring it back" 'rc 1 && err_has "personas/claude.json isn" && err_has "git checkout"'
mv "$T/claude.json" "$A/personas/claude.json"
mv "$A/personas/$PG.key" "$T/keys/moved"
t nx "$PG" start </dev/null
check "a start with its ssh key gone: refused" 'rc 1 && err_has "ssh key $A/personas/$PG.key isn'"'"'t there"'
mv "$T/keys/moved" "$A/personas/$PG.key"
check "none of that started a container" '[[ -z $(docker ps -aq --filter "name=^agent-$ID-") ]]'

# --- personas --------------------------------------------------------------------------------------
stage "personas"
why=""
(( CONTAINERS )) || why="--no-containers"
for c in ssh-agent script mkfifo setsid; do
  [[ -n "$why" ]] || command -v "$c" >/dev/null || why="no $c on this VM"
done
if [[ -z "$why" && -z "$IMAGE" ]]; then
  list="$(docker images --format '{{.Repository}}:{{.Tag}}' | grep -E '/agent-claude-(docker|ddev):[^<]' | grep -v '^nxt-')"
  IMAGE="$(grep -m 1 -- '-docker:' <<< "$list" || grep -m 1 . <<< "$list")"
  [[ -n "$IMAGE" ]] || why="no Claude persona image here: build one (nexus <persona> build), or name one with --image"
fi
if [[ -z "$why" ]]; then
  # shellcheck disable=SC2016  # expanded by sh in the image
  probe="$(docker run --rm --network none --entrypoint sh "$IMAGE" \
             -c 'id -u; for c in claude python3 tmux setsid; do command -v "$c" >/dev/null || echo "no $c"; done' 2>&1)"
  [[ "$probe" == "$(id -u)" ]] \
    || why="$IMAGE won't do: it says '$(paste -sd ' ' <<< "$probe")'; it needs to run as uid $(id -u), with claude, python3, tmux and setsid"
fi
if [[ -n "$why" ]]; then skip "personas: $why"; summary; fi
echo "image: $IMAGE"

out="$(ssh-agent -s -a "$SOCK" 2>&1)" && AGENT_PID="$(sed -n 's/^SSH_AGENT_PID=\([0-9]*\);.*/\1/p' <<< "$out")"
if [[ ! -S "$SOCK" || -z "$AGENT_PID" ]]; then fail "a throwaway ssh-agent at $SOCK" "$out"; summary; fi

# Each persona's image name, from compose as nexus resolves it, tagged onto the one image.
for p in "$PC" "$PG"; do
  r="$(compose_t --profile "$p" config --images "$p" 2>&1)"
  if [[ "$r" == "$REG/"* ]] && docker tag "$IMAGE" "$r"; then TAGS+=("$r"); else fail "tag $IMAGE for $p" "$r"; summary; fi
done

# The stand-in API, on the host's loopback as the personas see it (host network).
if ! docker run -d --name "$API" --network host --user "$(id -u):$(id -g)" -v "$T:$T" --entrypoint python3 "$IMAGE" \
       "$A/tests/fake-api.py" "$T/api.port" "$T/api.log" >/dev/null || ! wait_for 20 '[[ -s $T/api.port ]]'; then
  fail "the stand-in API" "$(docker logs "$API" 2>&1 | tail -n 20)"; summary
fi
api_got() { grep -qF -- "$1" "$T/api.log" 2>/dev/null; }
# A scratch dir under /tmp/claude-<uid> would leave that dir root-owned inside, which Claude Code refuses.
cat >> "$A/personas/$PC.env" <<EOF
# tests/run.sh: the stand-in API and a fake key
ANTHROPIC_API_KEY=sk-ant-nxt-fake-key
ANTHROPIC_BASE_URL=http://127.0.0.1:$(cat "$T/api.port")
CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1
CLAUDE_CODE_TMPDIR=/home/dev
EOF
# The stand-in grok, first on the grok persona's PATH; it keeps its pid files next to itself.
mkdir -p "$S" && cp "$A/tests/fake-grok" "$S/grok" && chmod 755 "$S/grok"
path="$(docker image inspect -f '{{range .Config.Env}}{{println .}}{{end}}' "$IMAGE" | sed -n 's/^PATH=//p')"
printf '# tests/run.sh: the stand-in grok\nPATH=%s\nNXT_STATE=%s\n' "$S:${path:-/usr/local/bin:/usr/bin:/bin}" "$S" \
  >> "$A/personas/$PG.env"

# marked CONTAINER: the processes there that a headless run started (they carry its NEXUS_RUN marker).
marked() { docker exec "$1" sh -c 'grep -lsz "^NEXUS_RUN=" /proc/[0-9]*/environ; true' 2>/dev/null; }
no_claude_left() { wait_for 10 '[[ -z "$(marked "agent-$PC")" ]]'; }
# alive FILE: the process whose pid the stand-in grok wrote to $S/FILE still runs; gone FILE: it ends in 10 s.
alive() { docker exec "agent-$PG" sh -c 'kill -0 "$(cat "$1")"' sh "$S/$1" 2>/dev/null; }
gone()  { wait_for 10 "! alive $1"; }

# pty DIR ARGS…: nexus ARGS from DIR on a terminal of its own (script), in the background; fd 7 is its
# keyboard, and what it shows goes to $T/pty.out. pty_end SECONDS waits that long at most for it to end:
# RC its exit code (124: it didn't), OUT what it showed.
PTY=""
pty() {
  local dir="$1"; shift
  rm -f "$T/kbd"; mkfifo "$T/kbd" || return 1
  # Job control on for the launch: without it, bash starts a background job with SIGINT ignored, which
  # nexus can't trap then, so Ctrl-C would test nothing.
  set -m
  SHELL=/bin/bash script -qefc "cd $(printf %q "$dir") && exec $(printf '%q ' env "${NX_ENV[@]}" "$A/nexus" "$@")" /dev/null \
    < "$T/kbd" > "$T/pty.out" 2>&1 &
  PTY=$!
  set +m
  exec 7> "$T/kbd"
}
pty_end() {
  if wait_for "$1" '! kill -0 "$PTY" 2>/dev/null'; then wait "$PTY"; RC=$?; else kill "$PTY" 2>/dev/null; wait "$PTY"; RC=124; fi
  exec 7>&-
  OUT="$(tr -d '\r' < "$T/pty.out")" ERR=""
}
# bg NAME ARGS…: nexus ARGS in the background, its pid in BG_PID, its output in $T/NAME.out. bg_end: its
# exit code in RC, its output in OUT.
bg() { local n="$1"; shift; ( exec env "${NX_ENV[@]}" "$A/nexus" "$@" ) </dev/null > "$T/$n.out" 2>&1 & BG_PID=$!; BG+=("$BG_PID"); }
bg_end() { wait "$1"; RC=$?; OUT="$(cat "$T/$2.out")" ERR=""; }

stage "headless with Claude ($PC, the stand-in API)"
t nx "$PC" headless "nxt say hi" </dev/null
check "starts the persona; only the reply on stdout" 'rc 0 && out_is FAKE-REPLY && err_has "agent-$PC"'
check "  the prompt reached the API" 'api_got "nxt say hi"'
check "  with the model and effort from personas/claude.json" 'api_got "\"model\":\"claude-sonnet" && api_got "\"effort\":\"low\""'
printf 'line one\nline two\n' | t nx "$PC" headless "nxt review"
check "a prompt and piped input: both, the prompt first" 'rc 0 && out_is FAKE-REPLY && api_got "nxt review\\nline one\\nline two"'
printf 'nxt piped only' | t nx "$PC" headless
check "piped input alone is the prompt" 'rc 0 && out_is FAKE-REPLY && api_got "nxt piped only"'
t nx "$PC" headless $'---\nname: nxt-fm\n---\nthe task' </dev/null
check "a prompt starting with --- (front matter)" 'rc 0 && out_is FAKE-REPLY && api_got "---\\nname: nxt-fm"'
t nx "$PC" headless "nxt json" --output-format json </dev/null
check "flags after the prompt reach claude -p" 'rc 0 && out_re "\"result\": ?\"FAKE-REPLY\""'
pty "$ROOT/app" "$PC" headless "nxt HOLD"
if wait_for 60 'api_got "nxt HOLD"'; then printf '\003' >&7; fi
pty_end 30
check "Ctrl-C: exit 130, and a notice" 'rc 130 && out_has "nexus: INT: stopping the run in $PC"'
check "  no claude left in the persona" 'no_claude_left'

stage "headless with grok ($PG, the stand-in grok)"
# SSH_AUTH_SOCK pointing nowhere: nexus may still find an agent of yours in /run/user/<uid>, but a persona with
# a key or none mounts /dev/null all the same. (Whether nexus found one isn't up to this test.)
sock_of() { docker inspect -f '{{range .Mounts}}{{if eq .Destination "/ssh-agent"}}{{.Source}}{{end}}{{end}}' "agent-$1"; }
t env "${NX_ENV[@]}" SSH_AUTH_SOCK="$T/no-agent" "$A/nexus" "$PG" start </dev/null
check "start, with SSH_AUTH_SOCK pointing nowhere: it runs, nothing attached, /dev/null for the agent" \
      'rc 0 && [[ $(docker inspect -f "{{.State.Running}}" "agent-$PG" 2>/dev/null) == true && $(sock_of "$PG") == /dev/null ]]'
t docker exec "agent-$PG" cat /etc/grok/managed_config.toml
check "  inside: grok's managed config is the persona's, with the settings and the statusline" \
      'rc 0 && out_has "theme = \"grokday\"" && out_has "/etc/grok/statusline/statusline.sh"'
pub="$(cut -d " " -f 1,2 "$A/personas/$PG.key.pub")"
t docker exec "agent-$PG" sh -c 'echo "sock=[$SSH_AUTH_SOCK]"; [ -c /ssh-agent ] && echo agent=/dev/null
                                 ssh-keygen -y -f /run/nexus/ssh-key; ssh -G nxt.invalid | grep -E "^(identit|batchmode)"'
check "  inside: its key, which its ssh uses, no password prompt, and no agent" \
      'rc 0 && out_line "sock=[]" && out_line "agent=/dev/null" && out_has "$pub" \
       && out_line "identityfile /run/nexus/ssh-key" && out_line "identitiesonly yes" && out_line "batchmode yes"'
# The persona without ssh, on the grok persona's image (its image name is the same).
t env "${NX_ENV[@]}" SSH_AUTH_SOCK="$T/no-agent" "$A/nexus" "$PN" start </dev/null
check "a persona without ssh starts the same way, /dev/null for the agent" \
      'rc 0 && [[ $(docker inspect -f "{{.State.Running}}" "agent-$PN" 2>/dev/null) == true && $(sock_of "$PN") == /dev/null ]]'
t docker exec "agent-$PN" sh -c 'echo "sock=[$SSH_AUTH_SOCK]"; [ -c /ssh-agent ] && echo agent=/dev/null
                                 [ -e /run/nexus/ssh-key ] || echo no-key; ssh -G nxt.invalid | grep -E "^batchmode"'
check "  inside: no agent, no key, and no password prompt" \
      'rc 0 && out_line "sock=[]" && out_line "agent=/dev/null" && out_line no-key && out_line "batchmode yes"'
t docker exec "agent-$PC" sh -c 'echo "sock=[$SSH_AUTH_SOCK]"; ssh-add -l'
check "the Claude persona, inside: your agent, at /ssh-agent" \
      'rc 1 && out_line "sock=[/ssh-agent]" && out_has "no identities" && [[ $(sock_of "$PC") == "$SOCK" ]]'
mv "$A/personas/$PG.key" "$T/keys/moved"
t nx "$PG" headless "nxt key gone" </dev/null
check "a running persona whose key file is gone: still usable (its key is mounted)" 'rc 0 && out_line "argv: [--single=nxt key gone]"'
mv "$T/keys/moved" "$A/personas/$PG.key"
t nx "$PG" headless "nxt hello" --output-format json </dev/null
check "grok --single=PROMPT, then the flags, in this directory" \
      'rc 0 && out_line "argv: [--single=nxt hello] [--output-format] [json]" && out_line "pwd: $ROOT/app"'
printf 'in 1\nin 2\n' | t nx "$PG" headless "nxt review"
check "a prompt and piped input: one stream on --prompt-file, the prompt first" \
      'rc 0 && out_line "argv: [--prompt-file] [/dev/stdin]" && out_line "stdin: nxt review|in 1|in 2|"'
printf 'nxt only' | t nx "$PG" headless
check "piped input alone" 'rc 0 && out_line "argv: [--prompt-file] [/dev/stdin]" && out_line "stdin: nxt only"'
printf 'nxt p' | t nx "$PG" headless "--note=a b" -v
check "a first argument --opt=value with spaces is a flag" \
      'rc 0 && out_line "argv: [--prompt-file] [/dev/stdin] [--note=a b] [-v]" && out_line "stdin: nxt p"'
fm=$'---\nnxt fm'
t nx "$PG" headless "$fm" </dev/null
check "a prompt starting with ---" 'rc 0 && out_has "argv: [--single=$fm]"'
printf 'from a file\n' > "$T/input.txt"
t nx "$PG" headless "nxt file" < "$T/input.txt"
check "input from a file" 'rc 0 && out_line "stdin: nxt file|from a file|"'
{ sleep 5; } | t nx "$PG" headless "nxt idle"
check "an idle pipe next to a prompt: 3 s, then without it" \
      'rc 0 && err_has "no input on stdin within 3 s" && out_line "argv: [--single=nxt idle]"'
t nx "$PG" headless --output-format json "nxt x" </dev/null
check "flags first and no input: refused" 'rc 1 && err_has "give the prompt first"'
t nx "$PG" headless </dev/null
check "no prompt and no input: refused" 'rc 1 && err_has "give the prompt first"'
printf '' | t nx "$PG" headless
check "no prompt and an empty pipe: refused" 'rc 1 && err_has "piped in was empty"'
t nx "$PG" headless "exit 7" </dev/null
check "the CLI's exit code is the run's" 'rc 7'
cd "$ROOT/app/sub" && t nx "$PG" headless "nxt sub" </dev/null
check "from a subdirectory: runs there" 'rc 0 && out_line "pwd: $ROOT/app/sub"'
cd "$T/link" && t nx "$PG" headless "nxt link" </dev/null
check "from a symlink into the root: runs at the project's own path" 'rc 0 && out_line "pwd: $ROOT/app"'
cd "$T/outside" && t nx "$PG" headless "nxt out" </dev/null
check "outside the projects root: refused" 'rc 1 && err_has "is outside"'
cd "$ROOT/app" || exit 2

pty "$ROOT/app" "$PG" headless "daemon d1"
if wait_for 20 '[[ -s $S/d1.pid && -s $S/d1.daemon ]]'; then printf '\003' >&7; fi
pty_end 20
check "Ctrl-C: exit 130, and a notice" 'rc 130 && out_has "nexus: INT: stopping the run in $PG"'
check "  grok stops" 'gone d1.pid'
check "  a daemon it detached keeps running" 'alive d1.daemon'
docker exec "agent-$PG" sh -c 'kill "$(cat "$1")"' sh "$S/d1.daemon" 2>/dev/null
bg t1 "$PG" headless "hold t1"
if wait_for 20 '[[ -s $S/t1.pid ]]'; then kill -TERM "$BG_PID"; fi
bg_end "$BG_PID" t1
check "TERM: exit 143, and grok stops" 'rc 143 && out_has "nexus: TERM: stopping the run" && gone t1.pid'
bg h1 "$PG" headless "hold h1"
if wait_for 20 '[[ -s $S/h1.pid ]]'; then kill -HUP "$BG_PID"; fi
bg_end "$BG_PID" h1
check "a hangup: exit 143, and grok stops" 'rc 143 && gone h1.pid'
bg c1 "$PG" headless "hold c1"; p1="$BG_PID"
bg c2 "$PG" headless "hold c2"; p2="$BG_PID"
if wait_for 20 '[[ -s $S/c1.pid && -s $S/c2.pid ]]'; then kill -TERM "$p1"; fi
bg_end "$p1" c1
check "two runs at once: stopping one" 'rc 143 && gone c1.pid'
check "  leaves the other running" 'alive c2.pid && kill -0 "$p2" 2>/dev/null'
kill -TERM "$p2"; bg_end "$p2" c2
check "  until it gets its own signal" 'rc 143 && gone c2.pid'

stage "sessions, ls, top, stop"
tx() { docker exec "agent-$PC" tmux "$@"; }
pty "$ROOT/my.app" "$PC" -- sleep 300
wait_for 30 '[[ -n $(tx list-clients -t =my_app 2>/dev/null) ]]'
OUT="$(tx list-panes -t =my_app -F '#{pane_current_path}|#{pane_start_command}' 2>&1)" RC=0 ERR=""
check "PERSONA -- CMD: a session named after the dir ('.' as _), at it, running CMD" 'out_has "$ROOT/my.app|" && out_has "sleep 300"'
tx detach-client -s =my_app >/dev/null 2>&1
pty_end 20
check "  detaching ends nexus, exit 0" 'rc 0 && out_has "detached"'
pty "$ROOT/my.app" "$PC"
wait_for 30 '[[ -n $(tx list-clients -t =my_app 2>/dev/null) ]]'
OUT="$(tx list-sessions -F '#{session_name}' 2>&1)" RC=0 ERR=""
check "again from that dir: the same session" '[[ $(grep -cx my_app <<< "$OUT") == 1 ]]'
tx detach-client -s =my_app >/dev/null 2>&1
pty_end 20
check "  detached again, exit 0" 'rc 0'

# A pane started as Claude Code, and one statusline draw in it, as Claude Code would do.
wait_for 30 'tx has-session -t =main 2>/dev/null'
pane="$(tx new-session -d -P -F '#{pane_id}' -s billing -c "$ROOT/app" 'claude --version >/dev/null 2>&1; exec sleep 300')"
docker exec -i -e TMUX_PANE="$pane" "agent-$PC" bash /etc/claude-code/statusline/statusline.sh >/dev/null <<EOF
{"model": {"id": "claude-opus-5-5", "display_name": "Opus"}, "workspace": {"current_dir": "$ROOT/app"},
 "cost": {"total_cost_usd": 1.2345}, "context_window": {"context_window_size": 200000,
 "current_usage": {"input_tokens": 38000, "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}},
 "rate_limits": {"five_hour": {"used_percentage": 12}, "seven_day": {"used_percentage": 40}}}
EOF
t nx top --once </dev/null
re="^$PC"' +billing +Opus 5\.5 +38k/200k +12% +40% +\$1\.23 +[0-9]+s$'
check "top: a Claude Code pane, with its statusline's figures" \
      'rc 0 && out_re "^PERSONA +SESSION +MODEL +CONTEXT +5H +7D +COST +SEEN$" && out_re "$re" && out_re "^$PC +(starting|healthy|running) +.* +1 claude$"'
check "  a grok persona: state, CPU, memory; no grok running, statusline off" \
      'out_re "^$PN +(starting|healthy|running) +[0-9.]+% +[0-9.]+[kKMG]i?B +no grok running, statusline off$"'
# A pane started as grok in the grok persona, and one status line draw in it, with the payload grok
# 1.0.62 sends (taken from a real run): no limits, and no cost before a priced request.
gpane="$(docker exec "agent-$PG" tmux new-session -d -P -F '#{pane_id}' -s gtop -c "$ROOT/app" "grok '--single=hold gtop'")"   # the stand-in waits, as grok does
docker exec -i -e TMUX_PANE="$gpane" "agent-$PG" /etc/grok/statusline/statusline.sh > "$T/gline" <<EOF
{"schema_version": 1, "cwd": "$ROOT/app", "session_id": "nxt", "model": {"id": "grok-4.6", "display_name": "Grok 4.6"},
 "workspace": {"current_dir": "$ROOT/app"}, "version": "1.0.62", "cost": {"total_duration_ms": 31, "total_api_duration_ms": 0},
 "context_window": {"context_window_size": 500000, "context_tokens": 1324, "session_input_tokens": 0, "used_percentage": 0}}
EOF
OUT="$(sed 's/\x1b\[[0-9;]*m//g' "$T/gline")" RC=0 ERR=""
check "grok's statusline.sh: one line, its model and context; no limits, no cost before grok sends one" \
      '[[ $(wc -l < "$T/gline") == 1 ]] && out_has "Grok 4.6" && out_has "Ctx 1.3k/500k" && ! out_has "✳" && ! out_has "Limit" && ! out_has "─" \
       && ! out_has "\$"'
OUT="$(docker exec -i -e COLUMNS=32 "agent-$PG" /etc/grok/statusline/statusline.sh <<< '{"schema_version": 1, "model": {"display_name": "Grok 4.6"},
 "workspace": {"current_dir": "/tmp/some-long-project-name", "branch": "main"}, "context_window": {"context_window_size": 500000, "context_tokens": 1324}}' \
  | sed 's/\x1b\[[0-9;]*m//g')"
check "  in a narrow row: the dir and branch give way, the context stays" 'out_has "Ctx 1.3k/500k" && ! out_has "some-long" && ! out_has "main"'
t nx top --once </dev/null
check "top: a grok pane, with its figures; dashes for limits and cost" \
      'rc 0 && out_re "^$PG +gtop +Grok 4\.6 +1k/500k +- +- +- +[0-9]+s$" && out_re "^$PG +.* +1 grok$" && out_has "grok sends no plan limits"'
docker exec -i -e TMUX_PANE="$gpane" "agent-$PG" /etc/grok/statusline/statusline.sh >/dev/null <<< '{"schema_version": 1, "model": {"display_name": "Grok 4.6"}}'
t nx top --once </dev/null
check "  a pane whose context grok hasn't sent yet: its model, and - for the rest" 'rc 0 && out_re "^$PG +gtop +Grok 4\.6 +- +- +- +- +[0-9]+s$"'
# The same on a terminal, in colour: the colours take no room, so the columns line up as without them.
pty "$ROOT/app" top --once
pty_end 30
plain="${OUT//$'\e['[0-9][0-9]m/}"; plain="${plain//$'\e['[0-9]m/}"   # the T_* codes nexus uses
hdr="$(grep -E '^PERSONA +SESSION' <<< "$plain")" row="$(grep -E -- "$re" <<< "$plain")"
check "  on a terminal: coloured, and the colours take no room (the right-aligned rows end level)" \
      'rc 0 && [[ $OUT == *$'"'"'\e[32m'"'"'* && -n $row ]] && (( ${#hdr} == ${#row} )) && [[ $(grep -c "^PERSONA " <<< "$plain") == 2 ]]'
t nx ls </dev/null
check "ls: running, with a table of the sessions" \
      'rc 0 && out_re "^$PC +claude +docker +agent +(starting|healthy|running) +$ROOT$" && out_re "^PERSONA +SESSION +WINDOWS +ATTACHED +DIR$" \
       && out_re "^$PC +billing +1 +no +$ROOT/app$" && out_re "^$PC +my_app +1 +no +$ROOT/my\.app$" && out_re "^$PG +main +1 +no +$ROOT$"'
t nx "$PC" ls </dev/null
check "PERSONA ls: a table of its sessions" \
      'rc 0 && out_re "^SESSION +WINDOWS +ATTACHED +DIR$" && out_re "^billing +1 +no +$ROOT/app$" && out_re "^my_app +1 +no +$ROOT/my\.app$"'
# A session whose dir holds a newline, ESC and BEL sequences, a tab and a C1 OSC (U+009D): what a persona
# shows must not drive the terminal, nor forge a row.
evil="$ROOT/q"$'\n\e]0;pwned\a\e[2Jevil\tcol:1:1:x\xc2\x9d0;c1\xc2\x9c'
esc=$'\e' bel=$'\a' c1=$'\xc2\x9d'
mkdir -p "$evil" && tx new-session -d -s evil -c "$evil" 'exec sleep 300'
t nx ls </dev/null
check "ls: a session dir with control characters reaches the terminal cleaned, as one row" \
      'rc 0 && [[ $OUT != *"$esc"* && $OUT != *"$bel"* && $OUT != *"$c1"* ]] && [[ $(grep -c pwned <<< "$OUT") == 1 ]] \
       && out_re "^$PC +evil +1 +no +$ROOT/q "'
t nx "$PC" ls </dev/null
check "  and PERSONA ls the same" 'rc 0 && [[ $OUT != *"$esc"* && $OUT != *"$bel"* && $OUT != *"$c1"* ]] && [[ $(grep -c pwned <<< "$OUT") == 1 ]]'
tx kill-session -t =evil 2>/dev/null
t nx "$PG" stop </dev/null
check "stop: the container stops" 'rc 0 && [[ $(docker inspect -f "{{.State.Status}}" "agent-$PG" 2>/dev/null) == exited ]]'
t nx ls </dev/null
check "ls: a stopped persona" 'out_re "^$PG +grok +docker +own key +stopped +$ROOT$" && ! out_re "^$PG +main "'
check "headless runs left no temp files" '[[ -z $(ls -A "$T/tmp") ]]'

summary
