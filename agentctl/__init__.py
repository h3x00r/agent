"""agentctl: nexus (the launcher) and config-tools (the setup tool) for the persona containers.

Layers, each importing only from the ones below it:
  cli        nexus/cli.py, configtools/cli.py: the argument grammar, one service call, the exit code
  services   nexus/*, configtools/*: one module per command; they return data and raise NexusError
  ui         style, text, table, screen, prompts, output: the only code that formats for a terminal
  adapters   docker, compose, tmux, ssh, the personas store, the compose renderer: the only code that
             runs other programs or knows a file format
  domain     personas, flavours, contexts, projects roots: rules and decisions, no I/O at all
  core       errors, processes, shell-compatible helpers, dotenv files, the lock

Python 3.8, the standard library only. The tools run as `python3 -I -B -S agentctl/_boot.py <tool>`, from
the bash shims `nexus` and `config-tools` at the repo root.
"""
