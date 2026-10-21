"""nexus --config --yes (and --list) in one pass, from the flags to the summary: the order the bash tool took
(the order decides which error wins, and what a stopped run has already done):

  1. refuse root; take the lock (not for --list); read the personas; --list stops here
  2. preflight: Docker, its compose plugin, the ssh-agent, ddev on the VM
  3. the personas: --remove, --persona, --ssh, --permissions, then the telemetry settings, --telemetry,
     --tool-details and --tool-content
  4. .env staged: the VM's projects root, the registry, the Claude Code version, DDEV_VERSION, AGENT_CPUS
  5. env files staged for the personas without a fitting one (sign-in and git identity)
  6. the settings files checked (JSON for Claude Code, TOML for grok), each grok persona's config staged
  7. compose.personas.yml staged, and everything checked with `docker compose config`
  8. the last questions: the launcher link, mkcert, the images
  9. commit: every staged file in place, signals ignored meanwhile (all of it or none of it)
 10. after the commit, failures are notes: ssh keys made, the collector tried (when its settings changed, or a
     persona sends now), the launcher linked, mkcert, the images
 11. the summary: the personas, how to start one, and the notes (LATER)
"""
from agentctl.configtools import after
from agentctl.configtools.questions import Questions
from agentctl.core import dotenv


def run(s):
    o = s.o
    q = Questions(s)
    s.check_flags()
    s.open()
    if o.list:
        s.list()
        return 0
    old_names = [p.name for p in s.personas]

    # 2. preflight
    s.preflight()

    # 3. the personas
    s.say("nexus --config: setting up %s%s" % (s.a, " (ddev %s on this VM)" % s.vm_ddev if s.vm_ddev else ""))
    hint = o.root or dotenv.get(".env", "PROJECTS_ROOT")
    s.vm_root_hint = "now " + hint if hint else "asked below"
    s.apply_flags()
    if not s.personas:
        s.warn("no personas configured; add some with nexus --config (or --persona NAME:FLAVOUR:CONTEXT)")
    s.change_notes(old_names)

    # 4. .env
    s.stage_env()
    q.shared()
    s.env_vm()

    # 5. env files
    q.env_files()

    # 6, 7. settings files, grok configs, compose.personas.yml, checked with the rest
    s.prepare()

    # 8. launcher, mkcert, images
    if o.link != "none" or (o.build and s.personas) or q.ca_missing():
        s.section("Launcher and images")
    target = q.launcher()
    mkcert = q.mkcert()
    verb = "build" if s.registry == "local" else "pull"
    images = q.images(verb)

    # 9. commit
    s.commit()

    # 10. after the commit
    after.ssh_keys(s)
    after.telemetry_check(s)
    after.link(s, target)
    if mkcert:
        after.mkcert(s)
    failed = 0
    if images:
        s.say("-- %sing the image of every persona; one that is already up to date takes seconds" % verb)
        failed = after.images(s, verb)

    # 11. the summary
    s.say("")
    s.say("Personas:")
    s.list()
    if s.personas:
        s.say("")
        s.say("Start one from a project directory:  cd %s/<project> && nexus %s"
              % (s.personas[0].root or s.root, s.personas[0].name))
    for line in s.later:
        s.say(line)
    s.say("Day-to-day use, upgrades and troubleshooting: runbook.md")
    return failed
