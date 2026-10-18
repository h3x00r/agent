#!/usr/bin/env bash
# tests/secrets.sh — look for secrets (keys, tokens, passwords) with gitleaks, in a container, in:
#   - the git history: what every commit on a branch, tag or remote-tracking branch adds, files deleted
#     since and binary ones included, what a two-parent merge itself adds (a conflict resolution, say), and
#     the commit and tag messages. Stashes stay on the VM and aren't scanned;
#   - the files in the checkout: the tracked ones as they are now, and the untracked ones git doesn't
#     ignore. A staged version that differs from the file is scanned once it is committed.
# What git ignores (.env, personas/*.env, personas/*.key) never goes in: the container gets a bundle of
# those commits and a copy of those files, on stdin. Nothing is bind-mounted, so it works the same from
# inside a persona. A finding is printed as its rule, file, line, commit and fingerprint, never its text.
#
#   tests/secrets.sh    exit 0: nothing found; 1: something found (listed above); 2: it couldn't scan
#
# CI runs it on every push and pull request (.github/workflows/ci.yml), so what CI finds is already on
# GitHub: run it before you push. tests/run.sh runs it too, when the image is here.
#
# Every scan runs with this checkout's .gitleaks.toml and .gitleaksignore, if it has them. Before the
# scans, a fresh fake token has to be found with those rules, or they are off and it stops. False
# positives and real finds: runbook section 9. To bump gitleaks, change GITLEAKS_IMAGE, its tag and its
# digest together.
set -uo pipefail

GITLEAKS_IMAGE="ghcr.io/gitleaks/gitleaks:v8.30.1@sha256:c00b6bd0aeb3071cbcb79009cb16a60dd9e0a7c60e2be9ab65d25e6bc8abbb7f"
LEAKS=3     # gitleaks' exit code when it finds something; its own errors exit 1

REPO="$(cd "$(dirname "$(readlink -f "$0")")/.." && pwd)"
die() { echo "secrets: $*" >&2; exit 2; }

if ! command -v docker >/dev/null || ! docker info >/dev/null 2>&1; then die "needs Docker, and access to it"; fi
git -C "$REPO" rev-parse --is-inside-work-tree >/dev/null 2>&1 || die "$REPO isn't a git checkout"
if [[ "$(git -C "$REPO" rev-parse --is-shallow-repository)" == true ]]; then
  die "a shallow clone holds only part of the history: git fetch --unshallow, or check out with fetch-depth 0"
fi

W="$(mktemp -d "${TMPDIR:-/tmp}/secrets.XXXXXX")" || die "can't make a temporary directory"
NAMES=()        # the containers started, removed at the end even when a run is cut short
cleanup() { if (( ${#NAMES[@]} )); then docker rm -f "${NAMES[@]}" >/dev/null 2>&1; fi; rm -rf "$W"; }
trap cleanup EXIT
trap 'exit 130' INT TERM HUP
T="$W/in"       # what goes into the container
mkdir -p "$T/src" "$T/canary" || die "can't write to $W"

# The files in the checkout, without what git ignores and without what it counts as deleted (a tracked
# path under a directory that is now a symlink, say: it would be read through the link).
declare -A GONE=()
while IFS= read -r -d '' f; do GONE[$f]=1; done < <(git -C "$REPO" diff-files -z --name-only --diff-filter=D)
git -C "$REPO" ls-files -z --cached --others --exclude-standard \
  | while IFS= read -r -d '' f; do
      if [[ -z "${GONE[$f]+x}" && ( -f "$REPO/$f" || -L "$REPO/$f" ) ]]; then printf '%s\0' "$f"; fi
    done \
  | tar -C "$REPO" --null -T - -cf - | tar -C "$T/src" -xf - \
  || die "can't copy the checkout's files to $W"

# The history: the commits on branches, tags and remote-tracking branches (and HEAD), not stashes.
REFS=(--branches --tags --remotes)
if git -C "$REPO" rev-parse -q --verify HEAD >/dev/null; then REFS=(HEAD "${REFS[@]}"); fi
COMMITS=0 CHANGED=0
if [[ "${REFS[0]}" == HEAD || -n "$(git -C "$REPO" for-each-ref --count=1 refs/heads refs/tags refs/remotes)" ]]; then
  if ! COMMITS="$(git -C "$REPO" rev-list --count "${REFS[@]}")" \
     || ! CHANGED="$(git -C "$REPO" rev-list --count --no-merges "${REFS[@]}" -- .)"; then
    die "can't read the history (missing objects, or alternates that moved?)"
  fi
fi
if (( COMMITS )); then
  git -C "$REPO" bundle create "$T/history.bundle" "${REFS[@]}" 2>"$W/err" || { cat "$W/err" >&2; die "can't bundle the history"; }
fi
printf 'GH_TOKEN=ghp_%s\n' "$(LC_ALL=C tr -dc A-Za-z0-9 < /dev/urandom | head -c 36)" > "$T/canary/canary.txt"
cat > "$T/findings.tmpl" <<'EOF'
{{- range . }}
{{ .RuleID }}  {{ .File }}:{{ .StartLine }}{{ if .Commit }}  commit {{ .Commit }}{{ end }}
  fingerprint: {{ .Fingerprint }}
{{- end }}
EOF

# in_gitleaks PART… -- SCRIPT: stream src and those parts of $T into a gitleaks container, unpacked in
# /tmp/w, and run SCRIPT in /tmp/w/src with sh, its .gitleaks.toml as the config if it has one; "$@" in
# SCRIPT are the flags every scan takes. Its stderr goes to $W/err.
GL=(--redact --no-banner --no-color --exit-code "$LEAKS"
    --report-format template --report-template /tmp/w/findings.tmpl --report-path -)
in_gitleaks() {
  local parts=(findings.tmpl src) name="secrets-$$-${#NAMES[@]}"
  while [[ "$1" != -- ]]; do parts+=("$1"); shift; done
  shift
  NAMES+=("$name")
  tar -C "$T" -cf - "${parts[@]}" \
    | docker run -i --rm --name "$name" -u "$(id -u):$(id -g)" --entrypoint sh \
        -e GIT_CONFIG_COUNT=1 -e GIT_CONFIG_KEY_0=safe.directory -e GIT_CONFIG_VALUE_0='*' \
        "$GITLEAKS_IMAGE" -c "set -e; mkdir /tmp/w; tar -xf - -C /tmp/w; cd /tmp/w/src
          if [ -f .gitleaks.toml ]; then export GITLEAKS_CONFIG=/tmp/w/src/.gitleaks.toml; fi
          $1" sh "${GL[@]}" 2>"$W/err"
}
# checked WHAT RC: show gitleaks' log; 0 or LEAKS stand, anything else, or an error in the log, stops it.
checked() {
  cat "$W/err" >&2
  if (( $2 != 0 && $2 != LEAKS )) || sed 's/\x1b\[[0-9;]*m//g' "$W/err" | grep -qE ' (ERR|FTL) '; then
    die "gitleaks couldn't scan $1 (see above)"
  fi
}

in_gitleaks canary -- 'exec gitleaks dir ../canary "$@"' > /dev/null
rc=$?; checked "a fake token" "$rc"
if (( rc != LEAKS )); then
  die "the rules don't find a fake GitHub token (ghp_…): a .gitleaks.toml needs \`[extend] useDefault = true\`," \
      "and no disabledRules or allowlist that covers ghp_ tokens or canary.txt"
fi

found=0
echo "secrets: the git history ($COMMITS commits), its commit and tag messages as ../msgs/"
if (( COMMITS )); then
  # shellcheck disable=SC2016  # the script runs in the container's sh, which expands it
  in_gitleaks history.bundle -- '
    git init -q --bare ../git
    git -C ../git fetch -q /tmp/w/history.bundle "+refs/*:refs/*"
    git -C ../git fetch -q /tmp/w/history.bundle "+HEAD:refs/scan/HEAD" 2>/dev/null || :
    mkdir ../msgs
    git -C ../git log --all --format="@@@commit %H%n%B" \
      | awk '\''/^@@@commit [0-9a-f]+$/ { if (f) close(f); f = "../msgs/commit-" $2 ".txt"; next } f { print > f }'\''
    git -C ../git for-each-ref refs/tags --format="%(if:equals=tag)%(objecttype)%(then)@@@tag %(objectname)%0a%(contents)%(end)" \
      | awk '\''/^@@@tag [0-9a-f]+$/ { if (f) close(f); f = "../msgs/tag-" $2 ".txt"; next } f { print > f }'\''
    h=0; gitleaks git ../git --log-opts="--all --full-history --diff-filter=uxdb --diff-merges=remerge --text" "$@" || h=$?
    if [ "$h" != 0 ] && [ "$h" != 3 ]; then exit "$h"; fi
    m=0; gitleaks dir ../msgs "$@" || m=$?
    if [ "$m" != 0 ] && [ "$m" != 3 ]; then exit "$m"; fi
    if [ "$h" = 3 ] || [ "$m" = 3 ]; then exit 3; fi'
  rc=$?; checked "the git history" "$rc"
  if (( CHANGED )) && sed 's/\x1b\[[0-9;]*m//g' "$W/err" | grep -qE ' INF 0 commits scanned'; then
    die "gitleaks scanned none of the $COMMITS commits"
  fi
  if (( rc == LEAKS )); then found=1; fi
fi
echo "secrets: the files in the checkout"
in_gitleaks -- 'exec gitleaks dir . "$@"'
rc=$?; checked "the files" "$rc"
if (( rc == LEAKS )); then found=1; fi

if (( found )); then
  echo "secrets: found some; if one is real, revoke it first: removing it from git doesn't undo a push" >&2
  exit 1
fi
echo "secrets: nothing found"
