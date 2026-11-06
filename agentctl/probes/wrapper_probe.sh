# shellcheck shell=sh
# In a grok persona, for `nexus <persona> --wrapper FILE` and `nexus <persona> [session] -- grok|agent … --wrapper F`:
# $1, a session name ("" for a headless run); to check a command, then the dir the session starts in, the NEXUS_WRAPPER
# to run it with ("" for the -- form: the container's own, which its --wrapper F overrides) and the command.
# Prints, one per line:
#   shim=yes|no   whether the grok on PATH is the image's shim (grok-mode.sh, by its marker line), which reads
#                 NEXUS_WRAPPER and --wrapper; an image built before it has none;
#   default=V     the container's own NEXUS_WRAPPER: the persona's default wrapper (nexus --config), or empty;
#   session=V     for a session that is there: its own NEXUS_WRAPPER (empty: none of its own, so it runs under the
#                 default; -: removed, so none at all; ?: it couldn't be read);
#   check=ok|RC   for a command, with the shim: the command, found from that dir (./grok too), run there with the
#                 shim's real grok swapped for /bin/true (and NEXUS_WRAPPER as given), so the shim's own checks
#                 decide; a failed one is followed by what the shim said (or cd, for a dir that isn't there).
#                 check=other: the command isn't the shim (a path to the real grok, say), so it wasn't run.
marker='# NEXUS-GROK-SHIM v1'
shim=no
if p=$(command -v grok) && [ -f "$p" ] && grep -qxF -- "$marker" "$p" 2>/dev/null; then shim=yes; fi
echo "shim=$shim"
echo "default=${NEXUS_WRAPPER-}"
if [ -n "$1" ] && tmux has-session -t "=$1" 2>/dev/null; then
  if v=$(tmux show-environment -t "=$1" NEXUS_WRAPPER 2>&1); then
    case $v in
      NEXUS_WRAPPER=*) echo "session=${v#NEXUS_WRAPPER=}" ;;
      -NEXUS_WRAPPER) echo "session=-" ;;
      *) echo "session=?" ;;
    esac
  else
    case $v in *"unknown variable"*) echo session= ;; *) echo "session=?" ;; esac
  fi
fi
if [ "$#" -ge 4 ] && [ "$shim" = yes ]; then
  d=$2 w=$3
  shift 3
  if [ -n "$w" ]; then NEXUS_WRAPPER=$w; export NEXUS_WRAPPER; fi
  # All in the dir the session starts in: the command found from there (./grok, bin/agent) and run there. A dir cd
  # can't enter fails the check with cd's own line (stderr is dropped, so 2>&1).
  if said=$(cd -- "$d" 2>&1 >/dev/null); then
    if (cd -- "$d" && c=$(command -v "$1") && [ -f "$c" ] && grep -qxF -- "$marker" "$c") 2>/dev/null; then
      said=$( (cd -- "$d" && NEXUS_GROK_REAL=/bin/true "$@" >/dev/null </dev/null) 2>&1)
      rc=$?
    else
      rc=other
    fi
  else
    rc=$?
  fi
  if [ "$rc" = other ]; then
    echo check=other
  elif [ "$rc" = 0 ]; then
    echo check=ok
  else
    echo "check=$rc"
    printf '%s\n' "$said"
  fi
fi
