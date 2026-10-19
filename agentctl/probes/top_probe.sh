tmux list-panes -a -F "#{pane_id}:#{window_index}.#{pane_index}:#{pane_current_command}:#{session_name}:#{pane_start_command}" 2>/dev/null |
while IFS=: read -r id wp cur sess start; do
  [ "$cur" = "$1" ] || case "$start" in "$1"|"$1 "*|\""$1 "*) ;; *) continue ;; esac
  s=-; f="$HOME/.cache/nexus/status/${id#%}"
  [ -f "$f" ] && read -r s < "$f"
  printf "%s\t%s\t%s\t%s\n" "$id" "$wp" "$sess" "$s"
done
