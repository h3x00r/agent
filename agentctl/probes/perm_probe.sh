# In a claude persona, for `nexus <persona> --skip-bypass-permissions | --bypass-permissions`: $1, a session
# name ("" for a headless run). Prints wrapper=yes|no (the image's claude reads NEXUS_PERMISSIONS);
# config=bypass|ask, the mode a claude started without NEXUS_PERMISSIONS runs in: bypass when the last managed
# drop-in that sets permissions.defaultMode sets bypassPermissions, or none does and the persona's own
# ~/.claude/settings.json does; and, for a session that exists, session=MODE (its NEXUS_PERMISSIONS; empty:
# none; ?: it couldn't be read).
if [ -x /usr/local/libexec/claude ]; then echo wrapper=yes; else echo wrapper=no; fi
python3 -I -c '
import glob, json, os
def mode(f):
    try:
        with open(f) as fh:
            d = json.load(fh)
    except (OSError, ValueError):
        return None
    p = d.get("permissions") if isinstance(d, dict) else None
    m = p.get("defaultMode") if isinstance(p, dict) else None
    return m if isinstance(m, str) and m else None
found = [m for m in map(mode, sorted(glob.glob("/etc/claude-code/managed-settings.d/*.json"))) if m]
m = found[-1] if found else mode(os.path.expanduser("~/.claude/settings.json"))
print("config=" + ("bypass" if m == "bypassPermissions" else "ask"))'
if [ -n "$1" ] && tmux has-session -t "=$1" 2>/dev/null; then
  if v=$(tmux show-environment -t "=$1" NEXUS_PERMISSIONS 2>&1); then
    case $v in NEXUS_PERMISSIONS=*) echo "session=${v#NEXUS_PERMISSIONS=}" ;; *) echo session= ;; esac
  else
    case $v in *"unknown variable"*) echo session= ;; *) echo "session=?" ;; esac
  fi
fi
