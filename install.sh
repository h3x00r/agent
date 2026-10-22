#!/usr/bin/env bash
# install.sh — nexus on a dev VM in one line:
#
#   curl -fsSL https://nexus.light-and-dark.io/install.sh | bash
#   curl -fsSL https://nexus.light-and-dark.io/install.sh | bash -s -- --yes --persona claude-docker:claude:docker
#
# That address serves this file as the default branch has it (raw.githubusercontent.com/h3x00r/agent/HEAD/
# install.sh). It clones that branch into /opt/agents, owned by you (sudo only to make the directory when /opt
# isn't yours), then hands over to nexus --config with what it was given: a first setup's questions, or none
# with --yes (nexus --config --help says what it takes). Run again, it updates the checkout it made (git pull
# --ff-only) and hands over again, which is runbook section 10's update. A directory there already that isn't
# a checkout of this repo is refused, and nothing in it is touched.
#
# Under `curl | bash` bash reads this script from stdin, so nexus --config reads its answers from the terminal
# (/dev/tty) instead. With no terminal (a script, CI), it is given /dev/null, so pass --yes; given nothing to
# pass on, it stops after the clone and says what to run. All of it is in main(), called on the last line: a
# download cut short runs nothing. It needs git; nexus checks the rest (Python 3.8, Docker, the ssh-agent).
# Run it as the VM user who will use the personas, not as root: nexus --config refuses root too.
#
#   AGENTS_DIR   where the checkout goes (default /opt/agents); nexus takes the same name for its checkout
#   AGENTS_REPO  what it clones (default https://github.com/h3x00r/agent.git): a fork, or a test's clone
set -uo pipefail

say() { echo "install: $*"; }
die() { echo "install: $*" >&2; exit 1; }

# key URL: a clone URL as host/path, lower case, so https://github.com/o/r(.git) and git@github.com:o/r(.git)
# read as the same repo; a local path stays itself.
key() {
  local u="${1%/}"
  u="${u%.git}"; u="${u#*://}"; u="${u#*@}"; u="${u/://}"
  printf '%s\n' "${u,,}"
}

main() {
  local dir="${AGENTS_DIR:-/opt/agents}" repo="${AGENTS_REPO:-https://github.com/h3x00r/agent.git}" origin
  if (( EUID == 0 )); then
    die "run this as the VM user who will use the personas, not as root or with sudo (it asks for sudo itself" \
        "if it needs it): the checkout is theirs, and nexus --config refuses root"
  fi
  command -v git >/dev/null || die "needs git (sudo apt-get install git), then run this again"
  [[ $dir == /?* ]] || die "AGENTS_DIR is '$dir': it takes an absolute path, not /"
  dir="${dir%/}"

  if [[ -e $dir/.git ]]; then
    origin="$(git -C "$dir" remote get-url origin 2>/dev/null)" || origin=""
    [[ -n $origin && $(key "$origin") == "$(key "$repo")" ]] \
      || die "$dir is a checkout of ${origin:-a repo without an origin}, not of $repo: update it with git yourself," \
             "or set AGENTS_DIR to another directory"
    say "updating $dir (git pull --ff-only)"
    git -C "$dir" pull --ff-only \
      || die "couldn't update $dir (above): changes of your own in it, not on a branch, or an ssh origin" \
             "without its key here? git -C $dir status says the first two; then run this again"
  else
    if [[ -e $dir && ! -d $dir ]]; then
      die "$dir is there already, and isn't a directory: move it away, or set AGENTS_DIR to another directory"
    elif [[ -d $dir && -n "$(ls -A -- "$dir" 2>&1)" ]]; then
      die "$dir is there already, and isn't a checkout of $repo: move it away, or set AGENTS_DIR to another" \
          "directory"
    elif [[ ! -d $dir ]] && ! mkdir -p -- "$dir" 2>/dev/null; then
      command -v sudo >/dev/null || die "can't make $dir, and there's no sudo to make it with: make it, yours," \
                                        "then run this again"
      say "making $dir, yours (sudo)"
      sudo install -d -o "$(id -u)" -g "$(id -g)" -- "$dir" || die "couldn't make $dir"
    fi
    [[ -w $dir ]] || die "$dir isn't yours to write to: sudo chown \"\$USER\" $dir, then run this again"
    say "cloning $repo into $dir"
    git clone -- "$repo" "$dir" || die "couldn't clone $repo (above); run this again once that's fixed"
  fi
  say "$dir is at $(git -C "$dir" log -1 --date=short --format='%h (%cd)')"

  # nexus --config takes its answers from a terminal: this one's, or the one curl | bash was typed in. It finds
  # its checkout by where it is, so the two variables above don't go with it.
  local next=(env -u AGENTS_DIR -u AGENTS_REPO "$dir/nexus" --config "$@")
  if [[ -t 0 ]]; then
    exec "${next[@]}"
  elif (: </dev/tty) 2>/dev/null; then
    exec "${next[@]}" </dev/tty
  elif (( $# )); then
    exec "${next[@]}" </dev/null
  fi
  say "no terminal to ask in: set it up with $dir/nexus --config, or run this again with --yes and its options" \
      "(curl … | bash -s -- --yes --persona NAME:FLAVOUR:CONTEXT; $dir/nexus --config --help lists them)"
}

main "$@"
