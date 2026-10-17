#!/usr/bin/env bash
# /etc/grok/statusline/statusline.sh inside grok personas with the statusline on: grok's status line.
# compose.personas.yml, written by config.sh, mounts this directory read-only, and the persona's managed
# config names this script (from settings.toml, next to it). Claude Code's is statusline/claude/.
#
# Reads grok's status line JSON on stdin (schema_version 1, as grok 1.0.62 sends it) and prints one line:
#   <model> (<effort>)  <dir>/  ⎇ <branch>  $<cost>  Ctx <used>k/<size>k
# grok sends no plan limits, and a cost only once something in the session has a price, so the line
# shows neither until then. It fits the row grok gives it (COLUMNS): the branch goes first, then the dir.
#
# Inside tmux it also saves those figures to ~/.cache/nexus/status/<pane>, for `nexus top`, in the format
# statusline/claude/statusline.sh writes. JSON is parsed with python3, which the persona image has.

input=$(cat)

PY=""
command -v python3 >/dev/null 2>&1 && PY=python3
if [ -z "$PY" ]; then
  printf 'grok — statusline needs python3\n'
  exit 0
fi

# --- Palette ---
CY=$'\033[0;36m'; GR=$'\033[0;32m'; YL=$'\033[0;33m'; RD=$'\033[0;31m'; BL=$'\033[0;34m'
DM=$'\033[0;90m'; BD=$'\033[1m'; RS=$'\033[0m'

# Load colour for a percentage: green below 50, yellow from 50, red from 80.
loadc() {
  if [ "${1:-0}" -ge 80 ]; then printf '%s' "$RD"
  elif [ "${1:-0}" -ge 50 ]; then printf '%s' "$YL"
  else printf '%s' "$GR"
  fi
}

# Format a token count as "1.2k" (drop a trailing .0).
fk() { awk "BEGIN {printf \"%.1f\", ${1:-0}/1000}" | sed 's/\.0$//'; }

# --- Every field in one python pass (missing or null values are fine) ---
read -r -d '' PROG <<'PYEOF'
import json, sys

try:
    data = json.load(sys.stdin)
except Exception:
    data = {}

def g(d, *path):
    for k in path:
        if not isinstance(d, dict) or d.get(k) is None:
            return None
        d = d[k]
    return d

def num(x):
    try:
        return float(x)
    except Exception:
        return 0.0

model  = g(data, 'model', 'display_name') or g(data, 'model', 'id') or '?'
cwd    = g(data, 'workspace', 'current_dir') or g(data, 'cwd') or ''
branch = g(data, 'workspace', 'branch') or ''
effort = g(data, 'effort', 'level') or ''
cost   = g(data, 'cost', 'total_cost_usd')
cost   = '' if cost is None else f"{num(cost):.4f}"   # absent until something carries a price
# Unknown (not sent yet) is empty, never 0: a context of 0 would be a figure grok didn't give.
size   = g(data, 'context_window', 'context_window_size')
size   = '' if size is None or num(size) <= 0 else int(num(size))
used   = g(data, 'context_window', 'context_tokens')
used   = '' if used is None else int(num(used))
pct    = g(data, 'context_window', 'used_percentage')
pct    = int(num(pct)) if pct is not None else (used * 100 // size if size != '' and used != '' else '')

# One value per line, without tabs or newlines of its own → mapfile yields exactly 8.
vals = [model, cwd, branch, effort, cost, size, used, pct]
sys.stdout.write("".join(str(v).replace('\t', ' ').replace('\n', ' ') + "\n" for v in vals))
PYEOF

mapfile -t F < <(printf '%s' "$input" | "$PY" -c "$PROG" 2>/dev/null)
if [ "${#F[@]}" -lt 8 ]; then
  printf 'grok — statusline parse error\n'
  exit 0
fi
model=${F[0]}; cwd=${F[1]}; branch=${F[2]}; effort=${F[3]}; cost=${F[4]}; size=${F[5]}; used=${F[6]}; pct=${F[7]}

# grok sends the branch, and none on a detached HEAD or outside a repo.
dir=""; [ -n "$cwd" ] && dir=$(basename "$cwd")

# --- Snapshot for `nexus top`, in tmux only: one tab-separated line per pane, "-" for a value not sent.
# Fields: format, time, model, context used, context size, context %, 5h %, 7d % (grok: none), cost.
if [ -n "$TMUX_PANE" ]; then
  snap="$HOME/.cache/nexus/status"
  [ -d "$snap" ] || mkdir -p "$snap" 2>/dev/null
  printf -v now '%(%s)T' -1
  printf 'v1\t%s\t%s\t%s\t%s\t%s\t-\t-\t%s\n' "$now" "$model" "${used:--}" "${size:--}" "${pct:--}" "${cost:--}" \
    2>/dev/null > "$snap/.${TMUX_PANE#%}.$$" \
    && mv -f "$snap/.${TMUX_PANE#%}.$$" "$snap/${TMUX_PANE#%}" 2>/dev/null
fi

# --- The line: data goes in with %s, never as a format. Each part as shown (P) and plain (W, for its width).
cost_show=$(awk "BEGIN {c=${cost:-0}; if (c >= 0.005) printf \"\$%.2f\", c}")   # as grok's own: no \$0.00
ctx=""; [ -n "$size" ] && [ -n "$used" ] && ctx="$(fk "$used")k/$(fk "$size")k"
head_p="${CY}${BD}${model}${RS}${effort:+ ${DM}(${effort})${RS}}"; head_w="$model${effort:+ ($effort)}"
dir_p="${dir:+  ${BL}${dir}/${RS}}";                                dir_w="${dir:+  $dir/}"
br_p="${branch:+  ${GR}⎇ ${branch}${RS}}";                           br_w="${branch:+  ⎇ $branch}"
tail_p="${cost_show:+  ${DM}${cost_show}${RS}}${ctx:+  ${CY}Ctx${RS} $(loadc "${pct:-0}")${ctx}${RS}}"
tail_w="${cost_show:+  $cost_show}${ctx:+  Ctx $ctx}"
cols=${COLUMNS:-80}; case "$cols" in ''|*[!0-9]*) cols=80 ;; esac
# Too wide for the row grok cuts it at: the branch goes, then the dir, so the figures stay.
if (( ${#head_w} + ${#dir_w} + ${#br_w} + ${#tail_w} > cols )); then br_p=""; br_w=""; fi
if (( ${#head_w} + ${#dir_w} + ${#tail_w} > cols )); then dir_p=""; fi
printf '%s\n' "$head_p$dir_p$br_p$tail_p"
