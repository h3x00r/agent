#!/bin/sh
# /usr/local/bin/claude in the claude persona images; Claude Code itself is /usr/local/libexec/claude.
# The permission mode `nexus <persona> --skip-bypass-permissions` (ask) or `--bypass-permissions` (bypass) set
# for one tmux session or headless run, in NEXUS_PERMISSIONS, or the one this file is started as:
# /usr/local/libexec/claude-ask and claude-bypass link to it, and are what an agent team's teammates start
# as (CLAUDE_CODE_TEAMMATE_COMMAND), so they keep the mode wherever their panes are. It becomes a
# --permission-mode put first, so one given after it still wins. Neither: Claude Code as its settings say.
case "${0##*/}" in
  claude-ask) mode=ask ;;
  claude-bypass) mode=bypass ;;
  *) mode="${NEXUS_PERMISSIONS-}" ;;
esac
case "$mode" in
  ask)
    CLAUDE_CODE_TEAMMATE_COMMAND=/usr/local/libexec/claude-ask; export CLAUDE_CODE_TEAMMATE_COMMAND
    exec /usr/local/libexec/claude --permission-mode default "$@" ;;
  bypass)
    CLAUDE_CODE_TEAMMATE_COMMAND=/usr/local/libexec/claude-bypass; export CLAUDE_CODE_TEAMMATE_COMMAND
    exec /usr/local/libexec/claude --permission-mode bypassPermissions "$@" ;;
  *)
    exec /usr/local/libexec/claude "$@" ;;
esac
