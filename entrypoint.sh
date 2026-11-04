#!/usr/bin/env bash
# Runs under tini (compose `init: true`). Keeps one tmux server alive for this persona for the
# life of the container. Sessions are created from outside:  nexus <persona> [session]
# or:  docker exec -it agent-<persona> tmux new -A -s <name> -c <dir>
# Given a command instead (docker run --rm <image> claude --version), it just runs that.
#
# The docker CLI's DOCKER_HOST (the image's ENV) is /run/nexus/docker.sock. In a persona that is the docker
# guard (image/docker-guard.py), started here and started again a second after it dies; with DOCKER_GUARD=off
# in the .env (NEXUS_DOCKER_GUARD here), or nothing to guard, it is a link to the VM's socket instead (and when
# that link, the image's, can't be replaced, it stays and the log says docker is UNGUARDED).
# A command run instead gets that link when nothing is there.
set -uo pipefail

SOCK=/run/nexus/docker.sock
RAW=/var/run/docker.sock
GUARD=/usr/local/libexec/nexus/docker-guard

if [[ $# -gt 0 ]]; then
  [[ -e $SOCK ]] || ln -sfn "$RAW" "$SOCK" 2>/dev/null
  exec "$@"
fi

PERSONA="${AGENT_PERSONA:-agent}"
COLOUR="${AGENT_COLOUR:-colour240}"
ROOT="${PROJECTS_ROOT:-$HOME}"

start_server() {
  tmux new-session -d -s main -n shell -c "$ROOT"
  # Persona identity in the status bar: you always know which persona (and VM) you're typing into.
  tmux set -g status-style "bg=$COLOUR,fg=colour255"
  tmux set -g status-left-length 60
  tmux set -g status-left " #[bold]${PERSONA}#[nobold] @ #H  "
  tmux set -g status-right " #{session_name}  %H:%M "
}

# The guard, again a second after each exit, until this subshell gets TERM: then the guard gets it too
# (it removes its socket) and is waited for. A `&` subshell doesn't keep the caller's traps; wait (not a
# foreground child) is what lets the trap run at once.
supervise_guard() {
  child=
  trap '[[ -n $child ]] && kill "$child" 2>/dev/null && wait "$child" 2>/dev/null; exit 0' TERM
  while :; do
    "$GUARD" & child=$!
    wait "$child"
    echo "[$PERSONA] docker guard exited (status $?): starting it again in a second"
    sleep 1 & wait $!
  done
}

guard_pid=
shutdown() {
  echo "[$PERSONA] stopping: killing tmux server"
  tmux kill-server 2>/dev/null
  if [[ -n $guard_pid ]]; then kill "$guard_pid" 2>/dev/null; wait "$guard_pid" 2>/dev/null; fi
  exit 0
}
trap shutdown TERM INT

why=
if [[ ${NEXUS_DOCKER_GUARD:-on} == off ]]; then why="DOCKER_GUARD=off"
elif [[ -z ${AGENT_PERSONA:-} ]]; then why="no AGENT_PERSONA to label with"
elif [[ ! -x $GUARD ]]; then why="no $GUARD in this image"
elif [[ ! -S $RAW ]]; then why="$RAW isn't a socket"
fi
if [[ -z $why ]]; then
  # The image's link goes first: a guard that can't start leaves docker failing here, not going round it.
  # A link that can't go (/run/nexus not writable here, e.g. a read-only root filesystem) would lead docker round
  # the guard: then no guard is started and the log says docker is unguarded. Only a socket that isn't a link is
  # the guard's.
  if ! err="$(rm -f "$SOCK" 2>&1)" || [[ -e $SOCK || -L $SOCK ]]; then
    dir="$(stat -c 'uid %u, mode %a' "${SOCK%/*}" 2>/dev/null)"
    echo "[$PERSONA] docker guard can't start, docker here is UNGUARDED: uid $(id -u) can't replace $SOCK, the" \
         "image's link to the VM's socket (${err:-it is still there}; ${SOCK%/*}: $dir), so what this persona" \
         "creates isn't labelled and nothing is refused. ${SOCK%/*} must be writable: no read-only root" \
         "filesystem, and an image from nexus $PERSONA build" >&2
  else
    supervise_guard & guard_pid=$!
    for _ in {1..20}; do [[ -S $SOCK && ! -L $SOCK ]] && break; sleep 0.1; done
    if [[ -S $SOCK && ! -L $SOCK ]]; then
      echo "[$PERSONA] docker guard on: $SOCK labels what this persona creates and refuses removing what isn't its own"
    else
      echo "[$PERSONA] docker guard on, but not listening on $SOCK yet (its errors are above): docker fails until it is"
    fi
  fi
else
  ln -sfn "$RAW" "$SOCK"
  echo "[$PERSONA] docker guard off ($why): $SOCK is a link to the VM's socket, $RAW"
fi

# "=main": exact match; plain "-t main" would also accept a session like "maintenance".
tmux has-session -t =main 2>/dev/null || start_server
echo "[$PERSONA] tmux server up on $(hostname) — attach with: nexus $PERSONA"

# Watchdog: recreate the "main" session if it gets killed, keep PID alive, stay signal-responsive.
while :; do
  tmux has-session -t =main 2>/dev/null || start_server
  sleep 15 & wait $!
done
