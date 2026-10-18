"""nexus's usage: what `nexus` alone and `nexus --help` print (the old launcher's header, word for word)."""

USAGE = """\
nexus — launcher for persona containers. ./config-tools sets up the personas and links this script,
e.g. to /usr/local/bin/nexus.

  nexus <persona> [session] [-- cmd ...]   attach to (or create) a tmux session in that persona,
                                           named after $PWD unless given; cmd runs only on create
  nexus ls                                 a table of every persona and its state; one of the sessions of
                                           the running ones
  nexus top [-n SECONDS] [--once]          live view of every persona: CPU, memory and, per Claude Code
                                           session, context, 5h/7d limits and cost (q quits)
  nexus <persona> ls                       a table of that persona's sessions
  nexus <persona> stop|logs|build|pull [args]
                                           args go to compose, e.g. build --no-cache
  nexus <persona> headless [PROMPT] [FLAGS…]
                                           one prompt, no tmux: claude -p or grok -p with FLAGS, in this
                                           dir; piped input goes with the prompt; output and exit code are its
  nexus <persona> start                    start it if it isn't running, without attaching (scripts, cron)
  nexus <persona> restart                  recreate the container: picks up a rebuilt/pulled image, or a
                                           projects root changed while it ran
  nexus <persona> upgrade [VERSION|CHANNEL] [-y]
                                           pin its CLI (claude or grok) to VERSION, else to the newest
                                           release (of CHANNEL), build or pull the images, recreate every
                                           persona of that flavour (asks first; -y doesn't), drop the old ones

Examples, with personas named claude-ddev and grok-docker (the names config-tools suggests):
  cd ~/projects/billing && nexus claude-ddev            # session "billing", shell at that dir
  nexus claude-ddev review -- claude --resume           # new session "review", straight into Claude Code
  nexus grok-docker                                     # same project, other persona
  git diff | nexus claude-ddev headless "review it"     # the review on stdout, no session
  nexus claude-ddev upgrade                             # the newest Claude Code, for every Claude persona
"""
