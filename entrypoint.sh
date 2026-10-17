#!/usr/bin/env bash
# Runs under tini (compose `init: true`). Keeps one tmux server alive for this persona for the
# life of the container. Sessions are created from outside:  nexus <persona> [session]
# or:  docker exec -it agent-<persona> tmux new -A -s <name> -c <dir>
# Given a command instead (docker run --rm <image> claude --version), it just runs that.
set -uo pipefail

[[ $# -gt 0 ]] && exec "$@"

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

shutdown() {
  echo "[$PERSONA] stopping: killing tmux server"
  tmux kill-server 2>/dev/null
  exit 0
}
trap shutdown TERM INT

# "=main": exact match; plain "-t main" would also accept a session like "maintenance".
tmux has-session -t =main 2>/dev/null || start_server
echo "[$PERSONA] tmux server up on $(hostname) — attach with: nexus $PERSONA"

# Watchdog: recreate the "main" session if it gets killed, keep PID alive, stay signal-responsive.
while :; do
  tmux has-session -t =main 2>/dev/null || start_server
  sleep 15 & wait $!
done
