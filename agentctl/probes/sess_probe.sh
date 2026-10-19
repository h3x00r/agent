ids=$(tmux list-sessions -F "#{session_id}" 2>/dev/null) || exit 1
for id in $ids; do
  for f in session_name session_windows session_attached pane_current_path; do
    v=$(tmux display-message -p -t "$id" "#{$f}" 2>/dev/null); printf "%s" "$v" | tr "\n\t\037" "   "; printf "\037"
  done
  echo
done
