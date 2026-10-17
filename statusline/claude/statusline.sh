#!/usr/bin/env bash
# /etc/claude-code/statusline/statusline.sh inside Claude personas with the statusline on
# (compose.personas.yml, written by config.sh, mounts this directory read-only; statusline/claude/settings.json
# points Claude Code at it). Needs python3, which the persona image has as a dependency of NodeSource's
# nodejs. The notes below describe the host the script was written on: the image has jq too, but the
# script only uses python3.
#
# Claude Code statusline — inspired by status-liner-demo.sh.
#
# Reads the statusline JSON on stdin and prints one line plus a separator:
#   ✳ <model> [1M]  <dir>/  ⎇ <branch>  <style>  <sub> <tier>  $cost  +adds -dels
#     Ctx <used>k/<size>k  Limit <5h used>% / <7d used>%
#   ────────────
#
# Inside tmux it also saves those figures to ~/.cache/nexus/status/<pane>, for `nexus top`.
#
# JSON is parsed with python3 (jq is not installed on this host; python3 is, and is
# already used for JSON parsing elsewhere in this workspace). No other dependencies
# beyond coreutils (tput/seq/awk) and git.

input=$(cat)

# --- Pick a JSON parser (python3 preferred, python fallback) ---
PY=""
command -v python3 >/dev/null 2>&1 && PY=python3
[ -z "$PY" ] && command -v python >/dev/null 2>&1 && PY=python
if [ -z "$PY" ]; then
  printf 'Claude Code — statusline needs python3 (or jq)\n'
  exit 0
fi

# --- Terminal width (override with COLUMNS when there is no tty; default 120) ---
cols=$(tput cols 2>/dev/null)
case "$cols" in ''|*[!0-9]*) cols=${COLUMNS:-120} ;; esac

# --- Palette ---
OR='\033[38;5;208m'   # orange (Claude mark)
CY='\033[0;36m'       # cyan
GR='\033[0;32m'       # green
YL='\033[0;33m'       # yellow
RD='\033[0;31m'       # red
MG='\033[0;35m'       # magenta
BL='\033[0;34m'       # blue
DM='\033[0;90m'       # dim/gray
BD='\033[1m'          # bold
RS='\033[0m'          # reset

# Load colour for a percentage: green below 50, yellow from 50, red from 80.
loadc() {
  local pct=${1:-0}
  if   [ "$pct" -ge 80 ]; then printf "%b" "$RD"
  elif [ "$pct" -ge 50 ]; then printf "%b" "$YL"
  else printf "%b" "$GR"
  fi
}

# Format a token count as "1.2k" (drop a trailing .0).
fk() { awk "BEGIN {printf \"%.1f\", ${1:-0}/1000}" | sed 's/\.0$//'; }

# --- Extract every field in one python pass (robust to missing/null values) ---
read -r -d '' PROG <<'PYEOF'
import json, sys, os

try:
    data = json.load(sys.stdin)
except Exception:
    data = {}

def g(d, *path, default=None):
    cur = d
    for k in path:
        if isinstance(cur, dict) and cur.get(k) is not None:
            cur = cur[k]
        else:
            return default
    return cur

def num(x, d=0):
    try:
        return float(x)
    except Exception:
        return d

model_id   = g(data, 'model', 'id', default='') or ''
model_name = g(data, 'model', 'display_name', default='?') or '?'
cwd        = g(data, 'workspace', 'current_dir') or g(data, 'cwd', default='') or ''
style      = g(data, 'output_style', 'name', default='default') or 'default'
cost       = num(g(data, 'cost', 'total_cost_usd', default=0))
adds       = int(num(g(data, 'cost', 'total_lines_added', default=0)))
dels       = int(num(g(data, 'cost', 'total_lines_removed', default=0)))

ctx_size = int(num(g(data, 'context_window', 'context_window_size', default=0)))
cr = int(num(g(data, 'context_window', 'current_usage', 'cache_read_input_tokens', default=0)))
cc = int(num(g(data, 'context_window', 'current_usage', 'cache_creation_input_tokens', default=0)))
it = int(num(g(data, 'context_window', 'current_usage', 'input_tokens', default=0)))
used = cr + cc + it
pct  = (used * 100 // ctx_size) if ctx_size > 0 else 0

# Empty when Claude Code sends none (an API key, or no reply yet): the line shows 0%, nexus top a dash.
five = g(data, 'rate_limits', 'five_hour', 'used_percentage')
week = g(data, 'rate_limits', 'seven_day', 'used_percentage')
five_pct = '' if five is None else round(num(five))
week_pct = '' if week is None else round(num(week))

sub_type = rate_tier = ''
try:
    home = os.environ.get('HOME') or os.path.expanduser('~')
    with open(os.path.join(home, '.claude', '.credentials.json')) as f:
        oauth = (json.load(f).get('claudeAiOauth') or {})
    sub_type  = oauth.get('subscriptionType') or ''
    rate_tier = oauth.get('rateLimitTier') or ''
except Exception:
    pass

# One value per line, every value newline-terminated → mapfile yields exactly 14.
vals = [model_id, model_name, cwd, style, f"{cost:.4f}", adds, dels,
        ctx_size, used, pct, five_pct, week_pct, sub_type, rate_tier]
sys.stdout.write("".join(f"{v}\n" for v in vals))
PYEOF

mapfile -t F < <(printf '%s' "$input" | "$PY" -c "$PROG" 2>/dev/null)

if [ "${#F[@]}" -lt 14 ]; then
  printf 'Claude Code — statusline parse error\n'
  exit 0
fi

model_id=${F[0]};  model_name=${F[1]}; cwd=${F[2]};       style=${F[3]}
cost=${F[4]};      adds=${F[5]};       dels=${F[6]}
ctx_size=${F[7]};  used=${F[8]};       pct=${F[9]}
five_pct=${F[10]}; week_pct=${F[11]};  sub_type=${F[12]}; rate_tier=${F[13]}

# --- Model display name (current lineup) + 1M-context badge ---
# Longest id wins: match the point release before the family it belongs to.
case "$model_id" in
  *fable-5-1*|*fable-5.1*|*fable5-1*) mdl="Fable 5.1" ;;
  *fable-5*|*fable5*)        mdl="Fable 5" ;;
  *opus-5-5*|*opus-5.5*)     mdl="Opus 5.5" ;;
  *opus-5*|*opus5*)          mdl="Opus 5" ;;
  *opus-4-8*|*opus-4.8*)     mdl="Opus 4.8" ;;
  *opus-4-7*|*opus-4.7*)     mdl="Opus 4.7" ;;
  *opus-4-6*|*opus-4.6*)     mdl="Opus 4.6" ;;
  *sonnet-5-5*|*sonnet-5.5*) mdl="Sonnet 5.5" ;;
  *sonnet-5*|*sonnet5*)      mdl="Sonnet 5" ;;
  *sonnet-4-6*|*sonnet-4.6*) mdl="Sonnet 4.6" ;;
  *sonnet-4-5*|*sonnet-4.5*) mdl="Sonnet 4.5" ;;
  *haiku-4-5*|*haiku-4.5*)   mdl="Haiku 4.5" ;;
  *)                          mdl="$model_name" ;;
esac
onem=""
[[ "$model_id" == *"[1m]"* ]] && onem="  ${DM}1M${RS}"

# --- Git branch ---
branch=""
[ -n "$cwd" ] && branch=$(git -C "$cwd" rev-parse --abbrev-ref HEAD 2>/dev/null)

# --- Derived display strings ---
dir=""; [ -n "$cwd" ] && dir=$(basename "$cwd")
used_k=$(fk "$used"); ctx_k=$(fk "$ctx_size")

dir_str="";   [ -n "$dir" ]    && dir_str="  ${BL}${dir}/${RS}"
br_str="";    [ -n "$branch" ] && br_str="  ${GR}⎇ ${branch}${RS}"
style_str=""; [ "$style" != "default" ] && [ -n "$style" ] && style_str="  ${YL}${BD}${style}${RS}"
acct_str=""
[ -n "$sub_type" ] && acct_str="  ${DM}${sub_type}${RS}"
mult=""; [[ "$rate_tier" =~ ([0-9]+x)$ ]] && mult="${BASH_REMATCH[1]}"
[ -n "$mult" ] && acct_str="${acct_str} ${DM}${mult}${RS}"

# Session cost — shown when present and the terminal is wide enough.
cost_str=""
if [ "$cols" -ge 80 ]; then
  cost_show=$(awk "BEGIN {c=${cost:-0}; if (c > 0) printf \"\$%.2f\", c}")
  [ -n "$cost_show" ] && cost_str="  ${DM}${cost_show}${RS}"
fi
# Lines added/removed this session — only on wider terminals.
churn_str=""
if [ "$cols" -ge 100 ] && { [ "${adds:-0}" -gt 0 ] || [ "${dels:-0}" -gt 0 ]; }; then
  churn_str="  ${GR}+${adds}${RS} ${RD}-${dels}${RS}"
fi

# Context and the 5h / 7d limits — each value coloured by load.
ctx_str="  ${CY}Ctx${RS} $(loadc "$pct")${used_k}k/${ctx_k}k${RS}"
limit_str="  ${MG}Limit${RS} $(loadc "${five_pct:-0}")${five_pct:-0}%${RS} / $(loadc "${week_pct:-0}")${week_pct:-0}%${RS}"

# --- Snapshot for `nexus top`, in tmux only: one tab-separated line per pane, "-" for a value not sent.
# Fields: format, time, model, context used, context size, context %, 5h %, 7d %, session cost.
if [ -n "$TMUX_PANE" ]; then
  snap="$HOME/.cache/nexus/status"
  [ -d "$snap" ] || mkdir -p "$snap" 2>/dev/null
  printf -v now '%(%s)T' -1
  printf 'v1\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' "$now" "$mdl" "$used" "$ctx_size" "$pct" \
    "${five_pct:--}" "${week_pct:--}" "$cost" 2>/dev/null > "$snap/.${TMUX_PANE#%}.$$" \
    && mv -f "$snap/.${TMUX_PANE#%}.$$" "$snap/${TMUX_PANE#%}" 2>/dev/null
fi

# --- Line 1: everything ---
printf "${OR}✳${RS} ${CY}${BD}%s${RS}" "$mdl"
printf "%b" "$onem"
printf "%b" "$dir_str"
printf "%b" "$br_str"
printf "%b" "$style_str"
printf "%b" "$acct_str"
printf "%b" "$cost_str"
printf "%b" "$churn_str"
printf "%b" "$ctx_str"
printf "%b" "$limit_str"
printf "\n"

# --- Separator: keeps the statusline apart from the mode line below it ---
sep=$(( cols < 60 ? cols : 60 ))
printf "${DM}%.0s─" $(seq 1 "$sep")
printf "${RS}"
