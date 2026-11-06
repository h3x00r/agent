"""nexus <persona> …: everything but ls, top, upgrade and headless's own work. In the bash launcher's order,
which decides which error wins:

  0. --wrapper FILE (or --wrapper=FILE), taken out wherever it is before a `--` (and from anywhere in a headless
     run's arguments, a --wrapper= meant for grok itself included): only for an attach or a headless run of a grok
     persona, and not with a `-- grok|agent … --wrapper F` command too; then --skip-bypass-permissions or
     --bypass-permissions, the same way: only for an attach or a headless run of a claude persona. --no-wrapper is
     no word of nexus's: in a headless run it goes to grok (whose shim reads it), and before a `--` it is, as any
     other -x there, a session name. A --wrapper that can't be read as one (no FILE, -x, a control character,
     twice, next to the -- grok form) is refused as it is taken; the mode words' refusals come before the other
     refusals of --wrapper; in a headless run, a -- that only --wrapper came after goes with it;
  1. the subcommand: ls, stop/logs/build/pull (compose, exec'd: no checks), start, restart, upgrade,
     headless, or an attach (the default: [SESSION] [-- CMD…]);
  2. preflight: the projects root must be absolute and creatable; the shared scratch root must be there
     (nexus --config makes it), and the persona's scratch dir in it is made (an attach or a headless run to a
     running one only warns when it can't be: it has its mount already); a ddev persona gets ~/.ddev and
     the mkcert dir, and its CA if there is one; a persona with ssh= of its own gets no agent;
  3. upgrade, if that was asked;
  4. --wrapper's FILE (relative: from here) held to the grok shim's rules here (checks.read_wrapper), so a bad one
     starts nothing; then a restart, or a persona that isn't running: the checks that keep Docker from making
     root-owned files (its ssh key, the settings files its service mounts, its notes, its wrapper file's copy, its
     telemetry files and the collector's CA),
     then that compose.personas.yml is what personas.json gives (a hand edit isn't used to create one), the
     ddev telemetry bundle written (the CA, then mkcert's), the warnings (no mkcert CA, an empty or stale
     ssh-agent), then compose up and the Claude defaults; a running one: the projects root it was created with,
     if that isn't the persona's now, a warning for each thing its container was made without (the ask drop-in,
     its scratch dir, the notes, telemetry), and one if compose.personas.yml is out of step;
  5. start stops here; otherwise where to start (domain.roots.place), then headless or the attach: `docker exec -it …
     tmux new-session -A`, which takes this process over. With one of the two words, a probe in the persona first
     (mode_env): the session's NEXUS_PERMISSIONS, which the image's claude (claude-mode.sh) turns into
     --permission-mode for every claude started in it. With --wrapper FILE: FILE (read in step 4) is passed on as a
     path the persona sees: in the projects root its container has (or its scratch dir), by real path, in the root's
     own spelling; else a copy, <scratch dir>/wrappers/<sha256:12>-<name>; a probe (wrapper_env) then refuses an
     image without the shim, the session main, and a session that is there under another wrapper (its own, the
     persona's default from nexus --config, or none); only then is that copy written, and it stays (a snapshot); a new
     session with a grok or agent command has it run by the probe as the shim would, under that NEXUS_WRAPPER (one
     that isn't the shim, or that the shim would stop at once, starts nothing); and a new session gets NEXUS_WRAPPER
     (tmux new-session -e, or docker exec -e for a headless run), which the image's grok (grok-mode.sh) turns into
     --rules for every grok started in it. With `-- grok|agent … --wrapper F` (the shim's own word; F
     relative to the session's dir): the probe finds that command from there and runs it with the shim's real grok
     swapped for /bin/true, so the shim's own checks decide, and a session of that name that is there already is
     refused (tmux would attach to it without running the command).

Pitfalls (each pinned where named):
- the order (golden nexus/edge-persona-refusal-order)
- stop, logs, build and pull: no checks, and an empty SSH_AUTH_SOCK is /dev/null for compose (golden
  nexus/edge-persona-mgmt-extra)
- the projects root made as mkdir -p makes it, under a tight umask too (golden nexus/edge-persona-umask)
- the scratch root there, or refused with the sudo command that makes it (nexus never runs sudo), or the other way
  out (a root of your own); one that isn't a directory, with no install -d (it would fail); the persona's dir in it
  made mode 755, under a tight umask too; never chmod-ed once there; a root you can't write in, said so, with the
  command that makes it yours (test_nexus.Scratch.test_scratch_dir, golden nexus/scratch-root-missing, golden
  nexus/edge-persona-umask)
- the scratch root missing at an attach or a headless run to a running persona: a warning, and on (its container has
  its mount); a start, a restart or an upgrade, and an attach to one that isn't running, still refused (golden
  nexus/scratch-root-missing)
- ~/.ddev and the mkcert dir both tried, then exit 1 with mkdir's own line, ‘…’ in a UTF-8 locale (golden
  nexus/edge-persona-ddev-home-file)
- the defaults merge: 20 s, then 124 with what it printed (golden nexus/edge-persona-defaults-timeout, golden
  nexus/edge-persona-defaults-timeout-child)
- personas/claude.json unreadable: the redirect's error, the merge's exit 1, and the start goes on (golden
  nexus/edge-persona-defaults-unreadable)
- the session name as $(basename …) gives it, trailing newlines gone, then . and : as _ (golden
  nexus/edge-persona-session-newline, golden nexus/life-attach-names)
- no TERM: infocmp is asked for dumb, as bash set it (golden nexus/life-attach-term)
- before creating a container: the key, claude.managed.json, claude.ask.json for one that asks (there, and setting
  a mode that asks), the grok.toml, the notes, a wrapper file's copy its service mounts (gone: not started, with
  how to give it again or none, the checkout's path shell-quoted (test_nexus.WrapperHints.test_the_copy_gone_hint);
  another persona starts as ever), then compose.personas.yml in step with
  personas.json (golden nexus/edge-persona-refusal-order, golden nexus/life-legacy-claude-json, golden
  nexus/perm-ask-file-missing, golden nexus/notes-missing, golden nexus/wrapper-copy-missing)
- out of step at an attach to a running one: a warning, not a refusal (golden nexus/life-legacy-statusline)
- one that asks, running in a container made before it did (no 16-ask.json mount): a warning, at a start and an
  attach (golden nexus/perm-ask-not-restarted)
- the two words: both refused together, and for anything but an attach or a headless run, and for grok; every
  other -x is still a session name (golden nexus/perm-flag-refused, golden nexus/perm-dash-session-unchanged)
- a session that is there already runs in the mode it started in: one in the other mode, or whose mode can't be
  read, is refused, never attached; a new one gets NEXUS_PERMISSIONS whatever the persona's own mode, and no -A
  (golden nexus/perm-flag-sessions, golden nexus/perm-flag-main)
- an image without claude-mode.sh is refused, not run in the persona's mode (golden nexus/perm-flag-old-image)
- --wrapper: taken before a `--` (after it, the command's) and from anywhere in a headless run's arguments, both
  spellings; refused as it is taken (no FILE, an empty one, --, -x, a control character, shown as JSON spells it,
  the word twice, the word next to a `-- grok|agent … --wrapper F` command, a session name given too, not one after
  the shim's own bare `--`) before the mode words' refusals; a headless run's -- that only the word came after goes
  with it (no bare -- last for grok); after those, for anything but an attach or a headless run, then for a claude
  persona; --no-wrapper is no word of nexus's (a session name before a `--`, grok's in a headless run); take_mode
  still finds headless past --wrapper F (test_nexus.TakeWrapper.test_where_it_is_taken,
  test_nexus.TakeWrapper.test_what_it_refuses, test_nexus.TakeWrapper.test_take_mode_still_finds_headless, golden
  nexus/wrapper-refused, golden nexus/wrapper-dashdash, golden nexus/wrapper-headless)
- --wrapper's FILE held to the grok shim's rules here, before any probe and before a persona that isn't running is
  started: missing, not a file, a NUL, empty (golden nexus/wrapper-flag-sessions, golden nexus/wrapper-not-running,
  test_nexus.WrapperPath.test_held_to_the_shims_rules_first)
- where the persona sees FILE: by real path, in the projects root its container has (the old one, if it was made
  with another: the persona's root now doesn't count), in that root's own spelling (a symlinked root), or in its
  scratch dir; else (a link in the root to a file outside it too) a copy, <scratch dir>/wrappers/<sha256:12>-<name>,
  mode 644 in a dir of 755, said on stderr (a link in the root, or in the persona's root now: where it leads; a file in
  the persona's root now: the old root its container was made with), the same one for the same bytes, written (and
  said) only once the probe has let the run go on: a run refused for main, an image without the shim, a probe that
  fails or a session under another wrapper leaves none (golden nexus/wrapper-copy-after-checks); a container
  made without its scratch dir: a file outside its root refused, with the restart that adds it, each phrase above
  kept whole and the container named once, one in that scratch dir said to be there
  (test_nexus.WrapperPath.test_inside_the_root,
  test_nexus.WrapperPath.test_a_symlinked_root, test_nexus.WrapperPath.test_the_scratch_dir,
  test_nexus.WrapperPath.test_outside_staged, test_nexus.WrapperPath.test_the_old_root,
  test_nexus.WrapperPath.test_no_scratch_mount, golden nexus/wrapper-file-outside, golden nexus/wrapper-staged,
  golden nexus/wrapper-old-root)
- with --wrapper F, a session that is there already runs under the wrapper it started with: under F, attached (-A);
  under another file, under the persona's default (none of its own), under none (its NEXUS_WRAPPER removed, or no
  default), or one that can't be read: refused, never attached; main always refused; a new one gets NEXUS_WRAPPER
  and no -A; without the word, as ever; the hint's FILE shell-quoted, and the command after the -- kept, shell-quoted
  too (golden nexus/wrapper-flag-sessions, golden nexus/wrapper-default-session, golden nexus/wrapper-main, golden
  nexus/wrapper-word-command)
- the word with a grok or agent command (by its basename) for a new session: the probe runs it where the session
  starts, under NEXUS_WRAPPER (the file as the persona sees it) with the shim's real grok swapped for /bin/true, so
  one that isn't the shim (grok itself, which would run without the file) is refused, and so is one the shim would
  stop at once (the file and the command's own --rules over the limit), with the shim's lines; a copy this run made
  for it goes again, one there before stays (another session may run under it); a session attached to, or another
  command (a shell): no check (test_nexus.WrapperProbe.test_the_words_wrapper, golden nexus/wrapper-word-command)
- an image without grok-mode.sh is refused with --wrapper (build, or pull under a REGISTRY), not run without it, and
  so is a probe that fails (golden nexus/wrapper-old-image)
- the `-- grok|agent … --wrapper F` form, a grok persona's only, F before the shim's own bare `--` (a --wrapper
  after it is grok's words: no check): the shim's own checks run first where the session starts, the command found
  from there too (./grok), a dir that isn't there failing with cd's line
  (test_nexus.WrapperProbe.test_found_from_the_dir, test_nexus.WrapperProbe.test_a_dir_that_isnt_there); F relative
  to there, said (./F, once) when that isn't here by real path (test_nexus.DashdashNote.test_the_note); one that
  fails is refused with the shim's lines, whole (one with = in it too), or its exit code when it said nothing, a
  command that isn't the shim too; one that passes gives a new session, no -A; a session of that name there already
  (the one the dir gives, main) refused before the exec, the hint's command shell-quoted
  (test_nexus.DashdashNote.test_the_hint_quoted, test_nexus.TakeWrapper.test_the_dashdash_form, golden
  nexus/wrapper-dashdash, golden nexus/wrapper-dashdash-existing)
- telemetry: a file its service mounts that isn't there (or a CA that isn't a certificate) is refused before Docker
  makes it a root-owned directory; a ddev claude persona's bundle written at every start, so a later `mkcert
  -install` and a restart reach it; once the container is made, a telemetry file its service no longer names (a
  neutral drop-in left for the old container) removed (golden nexus/telemetry-start)
- running in a container made without its scratch dir (or with another root's) or the notes: one warning naming
  what it lacks, at a start, an attach and a headless run; none when its mounts can't be read
  (test_nexus.Scratch.test_made_without, golden nexus/scratch-made-without)
- telemetry, running in a container made before it changed: a warning at a start, an attach and a headless run, as
  for one that asks: a claude one by its drop-in's mount, and by what that drop-in holds there against the one in
  the checkout (tool details turned off, say: it would go on sending them), a grok one by its token header in the
  container's environment (golden nexus/telemetry-not-restarted)
"""
import errno
import hashlib
import json
import os
import shlex
import shutil
import tempfile

from agentctl.adapters import checks, docker
from agentctl.adapters import telemetry as tele_io
from agentctl.core import proc
from agentctl.core.errors import Exit, NexusError
from agentctl.core.shellcompat import bash_basename, lexical, mkdir_p, realpath_e, under, utf8_locale
from agentctl.domain import notes as nts
from agentctl.domain import scratch
from agentctl.domain import telemetry as tele
from agentctl.domain.persona import wrapper_file
from agentctl.domain.roots import moved, place
from agentctl.nexus import headless, ls, upgrade
from agentctl.nexus.context import Persona, is_socket

MODES = {"--skip-bypass-permissions": "ask", "--bypass-permissions": "bypass"}
NOT_FOR = ("ls", "stop", "logs", "build", "pull", "start", "restart", "upgrade")
SAYS = {"ask": "asks before running tools", "bypass": "runs its tools without asking"}
ASK_FILE = "personas/claude.ask.json"
ASK_TARGET = "/etc/claude-code/managed-settings.d/16-ask.json"
WRAPPER = "--wrapper"
WRAPPER_VAR = "NEXUS_WRAPPER"
SHIMS = ("grok", "agent")     # the grok image's names for its shim (grok-mode.sh)


def die(msg):
    raise NexusError(msg)


class NoScratch(NexusError):
    """The shared scratch root as it is on the VM keeps the persona's scratch dir from being made (not there, not a
    directory, not writable): what an attach to a running persona only warns about."""


def first_word(args):
    """The first of ARGS that is no word of nexus's own (a mode word, --wrapper and its value, --wrapper=FILE): the
    subcommand, if there is one."""
    skip = False
    for a in args:
        if skip:
            skip = False
        elif a == WRAPPER:
            skip = True
        elif a not in MODES and not a.startswith(WRAPPER + "="):
            return a
    return ""


def _control(v):
    return any(ord(c) < 32 or ord(c) == 127 for c in v)


def _shown(v):
    """V for a message: quoted as it is, unless it holds a control character (then as JSON spells it)."""
    return json.dumps(v) if _control(v) else "'%s'" % v


def take_wrapper(args):
    """--wrapper FILE or --wrapper=FILE out of ARGS: wherever it is before a `--`, and anywhere in a headless run's
    (whose arguments are never a command), so a --wrapper= there meant for grok itself is nexus's too (it ends up the
    same: NEXUS_WRAPPER, which the shim reads). (FILE or "", the rest.) Refused: no FILE, one that is -- or starts
    with - (a file named -x goes as ./-x), one with a control character (the probe's answers are read by line), the
    word twice, and the word next to a `-- grok|agent … --wrapper F` command (two wrappers for one session). In a
    headless run's, a -- that only the word came after goes with it (grok would get a bare -- last)."""
    headless = first_word(args) == "headless"
    f, rest, cmd, i, at = None, [], False, 0, -1
    while i < len(args):
        a = args[i]
        if a == "--" and not headless:
            cmd = True
        if cmd or not (a == WRAPPER or a.startswith(WRAPPER + "=")):
            rest.append(a)
            i += 1
            continue
        if a == WRAPPER:
            if i + 1 >= len(args):
                die("--wrapper needs a file: --wrapper FILE, or --wrapper=FILE")
            v, i = args[i + 1], i + 2
        else:
            v, i = a[len(WRAPPER) + 1:], i + 1
        if not v or v == "--":
            die("--wrapper needs a file: --wrapper FILE, or --wrapper=FILE")
        if _control(v):
            die("--wrapper %s: a path with a newline or another control character can't be passed on: rename the file"
                % _shown(v))
        if v.startswith("-"):
            die("--wrapper needs a file, not '%s': a file whose name starts with - goes as ./%s" % (v, v))
        if f is not None:
            die("--wrapper given twice: give one file")
        f, at = v, len(rest)
    if headless and at == len(rest) and rest and rest[-1] == "--":
        rest.pop()
    if f is not None and not headless:
        end = rest.index("--") if "--" in rest else len(rest)
        _, cmd_args = split_attach([a for a in rest[:end] if a not in MODES] + rest[end:])    # the mode words go later
        if dashdash_wrapper(cmd_args) is not None:
            die("--wrapper given twice, to nexus and to the %s after --: give it to one of them" % bash_basename(cmd_args[0]))
    return f or "", rest


def split_attach(args):
    """An attach's arguments: (the session named, or "", the command after `--`, or [])."""
    session, cmd = "", []
    if args and args[0] != "--":
        session, args = args[0], args[1:]
    if args and args[0] == "--":
        cmd = args[1:]
    return session, cmd


def dashdash_wrapper(cmd):
    """The FILE of a `grok|agent … --wrapper FILE` (or --wrapper=FILE) command, before its own bare `--` ("" for one
    without a FILE, which the shim refuses), or None for any other command."""
    if not cmd or bash_basename(cmd[0]) not in SHIMS:
        return None
    for i, a in enumerate(cmd[1:], 1):
        if a == "--":
            break
        if a.startswith(WRAPPER + "="):
            return a[len(WRAPPER) + 1:]
        if a == WRAPPER:
            return cmd[i + 1] if i + 1 < len(cmd) else ""
    return None


def take_mode(args):
    """--skip-bypass-permissions (ask) or --bypass-permissions (bypass) out of ARGS: wherever it is before a
    `--`, and anywhere in a headless run's (whose arguments are never a command). (mode or "", the word, the
    rest.)"""
    headless = first_word(args) == "headless"
    mode, word, rest, cmd = "", "", [], False
    for a in args:
        if a == "--" and not headless:
            cmd = True
        if not cmd and a in MODES:
            if mode and MODES[a] != mode:
                die("pick one of --skip-bypass-permissions and --bypass-permissions")
            mode, word = MODES[a], a
            continue
        rest.append(a)
    return mode, word, rest


class Run:
    def __init__(self, ctx, name, args):
        self.ctx, self.out = ctx, ctx.out
        self.p = Persona(ctx, name)
        self.args = list(args)
        self.mode = self.word = ""
        self.wrapper = self.wrapper_given = ""    # the path passed on (once resolved), and the FILE as given
        self.copy, self.fresh = None, ""          # the copy to write (wrapper_path), and the one this run made
        self.env = os.environ

    def compose(self, *args, env=None):
        e = dict(self.env if env is None else env)
        e["PROJECTS_ROOT"] = self.p.root
        e[scratch.VAR] = self.ctx.scratch_root     # the root the scratch dir was made in, whatever .env says
        return self.ctx.compose_argv() + ["--profile", self.p.name] + list(args), e

    def mgmt(self, *args):
        """stop, logs, build, pull: compose itself, with a placeholder for the ssh-agent none of them mounts."""
        self.env["SSH_AUTH_SOCK"] = self.env.get("SSH_AUTH_SOCK") or "/dev/null"
        argv, e = self.compose(*(list(args) + [self.p.name]))
        proc.execvp(argv, e)

    def personas_text(self):
        try:
            with open(self.ctx.personas_file, "rb") as f:
                return f.read().decode("utf-8", "surrogateescape")
        except OSError:
            return ""


    # --- the defaults merge, after compose up ---
    def claude_defaults(self):
        src = os.path.join(self.ctx.agents_dir, "personas", "claude.json")
        if not (self.p.flavour == "claude" and os.path.isfile(src)):
            return
        try:
            f = open(src, "rb")
        except OSError as e:     # bash's `< "$src"`: the redirect fails, the merge doesn't run, exit 1
            self.out.error_line("nexus: %s: %s" % (src, e.strerror))
            r = proc.Completed(1)
        else:
            with f:
                r = proc.run(["docker", "exec", "-i", self.p.container, "python3", "-c",
                              docker.probe("claude_defaults.py")], stdin=f, timeout=20, merge=True)
        out = r.out.rstrip("\n")
        if r.rc == 0:
            if out:
                self.out.note("%s: %s" % (self.p.name, out))
        elif r.rc == 3:
            self.out.warn("%s: %s" % (self.p.name, out))
        else:
            self.out.warn("%s: the defaults in personas/claude.json couldn't be merged (exit %d): %s" % (self.p.name, r.rc, out))

    def mode_env(self, session):
        """With one of the two words: the NEXUS_PERMISSIONS for the new SESSION (or the headless run: ""), or ""
        when SESSION is there already, in that mode. Refused: a session there in the other mode (or one whose mode
        can't be read), and an image whose claude doesn't read NEXUS_PERMISSIONS. A new session gets it even in the
        persona's own mode, which the probe only guesses (settings it can't see may differ)."""
        p = self.p
        if not self.mode:
            return ""
        rc, out = docker.exec_probe(p.container, "perm_probe.sh", session)
        got = dict(l.partition("=")[::2] for l in out.split("\n") if "=" in l)
        own = got.get("config")
        if rc or own not in SAYS:
            die("can't read %s's permission settings inside it (exit %d); try again, or `nexus %s restart`" % (p.name, rc, p.name))
        if got.get("session") == "?":
            die("can't read the mode session %s runs in: name a new session, nexus %s %s NAME" % (session, p.name, self.word))
        if "session" in got:
            now = got["session"] if got["session"] in SAYS else own
            if now != self.mode and session == "main" and got["session"] not in SAYS:
                die("session main is %s's shell, in its own mode (it %s): name a new session, nexus %s %s NAME, or change "
                    "%s's setting (nexus --config, change)" % (p.name, SAYS[now], p.name, self.word, p.name))
            if now != self.mode:
                how = "it was started with %s" % next(w for w, m in MODES.items() if m == now) \
                    if got["session"] in SAYS else "%s's own setting" % p.name
                die("session %s already %s (%s): end it, or name a new session: nexus %s %s NAME"
                    % (session, SAYS[now], how, p.name, self.word))
            return ""
        if got.get("wrapper") != "yes":
            # build or pull as upgrade does: pull with a registry other than local.
            verb = "build" if (os.environ.get("REGISTRY") or self.ctx.env_get("REGISTRY") or "local") == "local" else "pull"
            die("nothing was started: %s's image was built before nexus had %s.\n"
                "       Run `nexus %s %s`, then `nexus %s restart` (this closes its tmux sessions)."
                % (p.name, self.word, p.name, verb, p.name))
        return self.mode

    def main(self):
        p, a = self.p, self.ctx.agents_dir
        self.wrapper_given, args = take_wrapper(self.args)
        self.mode, self.word, args = take_mode(args)
        sub = args[0] if args else ""
        if self.mode and sub in NOT_FOR:
            die("%s is for a session or a headless run, not for %s" % (self.word, sub))
        if self.mode and p.flavour != "claude":
            die("%s is for claude personas: %s is a grok one, which takes its approval mode from personas/grok.toml"
                % (self.word, p.name))
        if self.wrapper_given and sub in NOT_FOR:
            die("--wrapper is for a session or a headless run, not for %s" % sub)
        if self.wrapper_given and p.flavour != "grok":
            die("--wrapper is for grok personas: %s is a claude one, whose claude takes no wrapper file" % p.name)
        restart = start_only = is_upgrade = is_headless = False
        if sub == "ls":
            return ls.ls_persona(self.ctx, p)
        if sub in ("stop", "logs", "build", "pull"):
            extra = {"stop": ["stop"], "logs": ["logs", "-f"], "build": ["build"], "pull": ["pull", "--policy", "always"]}
            self.mgmt(*(extra[sub] + args[1:]))
        if sub == "start":
            start_only = True
        elif sub == "restart":
            restart = True
        elif sub == "upgrade":
            is_upgrade, args = True, args[1:]
        elif sub == "headless":
            is_headless, args = True, args[1:]

        # 2. preflight
        if p.own_root:
            where = "%s's own, in %s; change it with nexus --config" % (p.name, self.ctx.json_file)
        else:
            where = "PROJECTS_ROOT from your shell or %s/.env; fix it, or unset it for ~/projects" % a
        if not (len(p.root) >= 2 and p.root.startswith("/")):
            die("the projects root must be an absolute path, not '%s' (%s)" % (p.root, where))
        try:
            mkdir_p(p.root)
        except OSError:
            die("can't create the projects root %s (%s)" % (p.root, where))
        running = None     # asked of Docker once, when it's needed here
        try:
            scratch_dir(self.ctx.scratch_root, p.name, self.ctx.scratch_from, a)
        except NoScratch as e:
            # The container a start, restart or upgrade makes would mount it; a running one has its mount already.
            if start_only or restart or is_upgrade:
                raise
            running = docker.running(p.container)
            if not running:
                raise
            self.out.warn("%s (%s runs on as it is; starting or restarting it is refused until then)" % (e.message, p.name))
        no_ca = False
        if p.context == "ddev":
            if "HOME" not in self.env:
                die("HOME isn't set, so there is no ~/.ddev or mkcert CA to give %s" % p.name)
            home = self.env["HOME"]
            failed = False
            for d in (home + "/.ddev", home + "/.local/share/mkcert"):   # `mkdir -p` both, then fail as set -e did
                try:
                    mkdir_p(d)
                except OSError as e:
                    q = "\u2018%s\u2019" if utf8_locale() else "'%s'"
                    self.out.error_line("mkdir: cannot create directory %s: %s" % (q % d, e.strerror))
                    failed = True
            if failed:
                raise Exit(1)
            ca = home + "/.local/share/mkcert/rootCA.pem"
            if os.path.isfile(ca):
                self.env["AGENT_CA_CERTS"] = ca
            else:
                self.env["AGENT_CA_CERTS"] = ""
                no_ca = True
        own_ssh, key = p.ssh, ""
        if own_ssh:
            if own_ssh != "none":
                key = own_ssh if own_ssh.startswith("/") else os.path.join(a, own_ssh)
            self.env["SSH_AUTH_SOCK"] = self.env.get("SSH_AUTH_SOCK") or "/dev/null"

        # 3. upgrade: it builds from compose.personas.yml and may recreate the containers, so only from one in step
        if is_upgrade:
            upgrade.Upgrade(self, args).run()
            return 0

        # 4. (re)create, or look at the running one; --wrapper's FILE read first, so a bad one starts nothing
        read = self.wrapper_read(self.wrapper_given) if self.wrapper_given else None
        old = ""
        self.mounts = None      # a running container's mounts; None: one this run made, which has all compose gives it
        if restart or not (docker.running(p.container) if running is None else running):
            if key and not (os.path.isfile(key) and os.access(key, os.R_OK)):
                die("%s's ssh key %s isn't there; nexus --config makes one (SSH: new), or points it at another, none or your "
                    "agent" % (p.name, key))
            text = self.personas_text()
            if "./personas/claude.managed.json," in text and p.flavour == "claude" \
                    and not os.path.isfile(os.path.join(a, "personas", "claude.managed.json")):
                die("%s/personas/claude.managed.json isn't there: restore it (git checkout personas/claude.managed.json), "
                    "or re-run %s/nexus --config without it" % (a, a))
            if p.flavour == "claude" and p.permissions == "ask":
                why, fix = checks.ask_problem(os.path.join(a, ASK_FILE))
                if why:
                    die("%s/%s %s, and %s asks before running tools: %s" % (a, ASK_FILE, why, p.name, fix))
            if "./personas/%s.grok.toml," % p.name in text and not os.path.isfile(os.path.join(a, "personas", p.name + ".grok.toml")):
                die("%s/personas/%s.grok.toml isn't there: re-run %s/nexus --config, which writes it" % (a, p.name, a))
            if "./%s," % nts.file(p.name) in text and not os.path.isfile(os.path.join(a, nts.file(p.name))):
                die("%s/%s isn't there: re-run %s/nexus --config, which writes it" % (a, nts.file(p.name), a))
            mine = service_text(text, p.name)
            w = wrapper_file(p.name)
            if "./%s," % w in mine and not os.path.isfile(os.path.join(a, w)):
                die(copy_gone(a, w, p.name))
            for f in (tele.dropin_file(p.name), tele.env_file(p.name)):
                if f in mine and not os.path.isfile(os.path.join(a, f)):
                    die("%s/%s isn't there: re-run %s/nexus --config, which writes it" % (a, f, a))
            if "./%s," % tele.CA_FILE in mine or "./%s," % tele.DDEV_BUNDLE in mine:
                why = tele_io.ca_problem(os.path.join(a, tele.CA_FILE))
                if why:
                    die("%s/%s %s, and %s sends telemetry to a collector with a CA of its own: give the CA file again "
                        "with %s/nexus --config, then telemetry" % (a, tele.CA_FILE, why, p.name, a))
            why = self.ctx.step_problem()
            if why:
                die(why)
            if "./%s," % tele.DDEV_BUNDLE in mine:
                why = tele_io.write_bundle(a, self.env.get("HOME", ""))
                if why:
                    die("%s's telemetry needs the collector's CA and mkcert's in one file, but nexus %s" % (p.name, why))
            if no_ca:
                self.out.warn("no mkcert CA on this VM yet — run 'mkcert -install' here, then `nexus %s restart`, or "
                              "https://*.ddev.site stays untrusted inside" % p.name)
            sock = self.env.get("SSH_AUTH_SOCK", "")
            if not own_ssh and is_socket(sock) and shutil.which("ssh-add"):
                rc = proc.run(["ssh-add", "-l"]).rc
                if rc == 1:
                    self.out.warn("the ssh-agent at %s holds no keys (ssh-add -l); git over ssh will fail inside" % sock)
                elif rc == 2:
                    self.out.warn("nothing answers at %s (stale ssh-agent socket); git over ssh will fail inside" % sock)
            if restart:
                argv, e = self.compose("up", "-d", "--force-recreate", p.name)
                rc = proc.run(argv, env=e, capture=False).rc
                if rc:
                    return rc
                tele_leftovers(a, p.name, mine)
                self.claude_defaults()
                return 0
            argv, e = self.compose("up", "-d", p.name)
            rc = proc.run(argv, env=e, capture=False, stdout_to_stderr=True).rc
            if rc:
                return rc
            tele_leftovers(a, p.name, mine)
            self.claude_defaults()
        else:
            old = ls.old_root(p)
            m = self.mounts = docker.mounts(p.container)
            if p.flavour == "claude" and p.permissions == "ask" and ASK_TARGET not in m:
                self.out.warn("%s asks before running tools now, but its container was made before that and doesn't: "
                              "`nexus %s restart` applies it (that ends its tmux sessions)" % (p.name, p.name))
            self.made_without(m)
            self.tele_made_before(m)
        if start_only:
            if old:
                self.out.warn(moved(p.name, old, p.root + p.root_from))
            return 0

        # 5. where it starts
        here = self.ctx.here
        pl = place(here, p.root, old, realpath_e, headless=is_headless)
        if old and pl.inside:
            if not os.path.isdir(pl.dir):
                die("%s no longer exists; cd to a project dir" % pl.dir)
            self.out.warn(moved(p.name, old, p.root + p.root_from))
        elif old and is_headless and pl.refused:
            die(moved(p.name, old, p.root + p.root_from, "which %s isn't in" % here))
        else:
            if not pl.outside_root and not os.path.isdir(pl.dir):
                die("%s no longer exists; cd to a project dir" % pl.dir)
            if pl.refused:
                die("headless: %s is outside %s's projects root (%s); cd to a project in it" % (here, p.name, p.root))
            if pl.outside_root and not old:
                self.out.error_line("nexus: %s is outside %s's projects root; starting in %s" % (here, p.name, p.root))
        if self.wrapper_given:
            self.wrapper = self.wrapper_path(self.wrapper_given, old, read)
        if is_headless:
            return headless.Headless(self, pl.dir, args).run()
        return self.attach(pl, old, args)

    # --- a wrapper file: where the persona sees it, and the probe ---
    def wrapper_read(self, given):
        """GIVEN (--wrapper's FILE; relative: from here) held to the grok shim's rules here: (its path, its bytes)."""
        path = lexical(given, self.ctx.here)
        data, why = checks.read_wrapper(path)
        if data is None:
            die("--wrapper %s %s" % (path, why))
        return path, data

    def wrapper_path(self, given, old, read=None):
        """GIVEN (--wrapper's FILE, READ already by wrapper_read, else read now) as a path the persona sees: in the
        projects root its container has (OLD, else the persona's) or in its scratch dir, by real path, spelt as that
        dir is mounted (as roots._in spells a place); else where its copy in its scratch dir goes, written only once
        wrapper_env has let the run go on (stage_wrapper)."""
        p = self.p
        path, data = read or self.wrapper_read(given)
        real = realpath_e(path)
        mine = old or p.root
        for d in (mine, p.scratch):
            rd = realpath_e(d)
            if real and rd and under(real, rd) and (d == mine or self.sees_scratch()):
                return d + real[len(rd):]
        rs = realpath_e(p.scratch)
        if not self.sees_scratch():
            if real and rs and under(real, rs):
                die("--wrapper %s is in %s's scratch dir %s, and its container was made without it: `nexus %s restart` adds "
                    "it (it ends its tmux sessions), or move the file into %s" % (path, p.name, p.scratch, p.name, mine))
            why, made = self.not_seen(path, real, mine, old, True)
            die("--wrapper %s %s, and %swithout its scratch dir %s, where nexus would copy it: `nexus %s restart` adds "
                "that (it ends its tmux sessions), or move the file into %s"
                % (path, why, "" if made else "its container was made ", p.scratch, p.name, mine))
        why = self.not_seen(path, real, mine, old, False)[0]
        dest = os.path.join(self.p.scratch, "wrappers", "%s-%s" % (hashlib.sha256(data).hexdigest()[:12],
                                                                  os.path.basename(path)))
        self.copy = (path, data, why, mine, dest)
        return dest

    def not_seen(self, path, real, mine, old, full):
        """Why PATH (real path REAL) isn't in MINE, the projects root the container has, as a phrase after it, and
        whether that phrase already says what its container was made with: a file in the persona's root now while the
        container has its OLD one (it does); a link in MINE, or in the persona's root now, to a file outside it; else
        outside it (FULL: naming it)."""
        p = self.p
        rp, rm = realpath_e(p.root), realpath_e(mine)
        if old and real and rp and under(real, rp):
            return ("is in %s's projects root %s, but its container was made with the old root %s"
                    % (p.name, p.root, old), True)
        named = " " + mine if full else ""
        for d, rd in ((mine, rm), (p.root, rp)):
            if under(path, d) or (rd and under(path, rd)):
                return "links to %s, outside %s's projects root%s" % (real, p.name, named), False
        return "is outside %s's projects root%s" % (p.name, named), False

    def sees_scratch(self):
        """Whether the container mounts the persona's scratch dir: one this run made does, and so does one whose
        mounts can't be read (the start that made it gave it)."""
        if self.mounts is None:
            return True
        return not self.mounts or self.p.scratch in self.mounts

    def stage_wrapper(self):
        """The copy wrapper_path worked out, if there is one: DATA (the bytes of PATH, which WHY says the persona doesn't
        see; MINE: the root it does) copied to DEST, <scratch dir>/wrappers/<sha256:12>-<name>, through a temp file
        there and a rename, so runs at once that copy the same file each see it whole; the copy stays (FRESH: it, when
        it wasn't there before). The note that says so, for once the run goes on ("": no copy)."""
        if not self.copy:
            return ""
        path, data, why, mine, dest = self.copy
        self.copy = None
        d, was = os.path.dirname(dest), os.path.exists(dest)
        tmp = ""
        try:
            if not os.path.isdir(d):
                try:
                    os.mkdir(d)
                    os.chmod(d, 0o755)
                except FileExistsError:
                    pass
            fd, tmp = tempfile.mkstemp(prefix=".tmp.", dir=d)
            with os.fdopen(fd, "wb") as f:
                f.write(data)
                os.fchmod(f.fileno(), 0o644)
            os.replace(tmp, dest)
        except OSError as e:
            if tmp:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
            die("--wrapper %s %s, and nexus can't copy it into %s (%s): move the file into %s"
                % (path, why, d, e.strerror, mine))
        self.fresh = "" if was else dest
        return "--wrapper %s %s: a copy of it, %s, goes in its place" % (path, why, dest)

    def wrapper_probe(self, *args):
        """wrapper_probe.sh in the persona with ARGS: its answers by name (shim, default, session, check), and the
        lines after check= (the shim's own, for a check that failed). Refused: a probe that fails, and an image
        whose grok isn't the shim (one built before it)."""
        p = self.p
        rc, out = docker.exec_probe(p.container, "wrapper_probe.sh", *args)
        got, lines = {}, out.split("\n")
        while lines and "=" in lines[0]:
            k, _, v = lines.pop(0).partition("=")
            got[k] = v
            if k == "check":
                break
        if rc or got.get("shim") not in ("yes", "no"):
            die("can't read %s's wrapper settings inside it (exit %d); try again, or `nexus %s restart`" % (p.name, rc, p.name))
        if got["shim"] != "yes":
            verb = "build" if (os.environ.get("REGISTRY") or self.ctx.env_get("REGISTRY") or "local") == "local" else "pull"
            die("nothing was started: %s's image was built before its grok took a wrapper file.\n"
                "       Run `nexus %s %s`, then `nexus %s restart` (this closes its tmux sessions)."
                % (p.name, p.name, verb, p.name))
        return got, [l for l in lines if l]

    def wrapper_env(self, session, wd="", cmd=()):
        """With --wrapper: the NEXUS_WRAPPER for the new SESSION (or the headless run: ""), or "" when SESSION is there
        already with that very file of its own. Refused: main (the persona's shell), a session there under another
        wrapper (one of its own, or none of its own: then the persona's default, nexus --config's, or none at all), or
        whose wrapper can't be read, and an image without the shim; the hint keeps the attach's command (CMD). Only
        then is a copy the file needs written (stage_wrapper), so a run refused here leaves none; a new session with a
        grok or agent command is checked in WD then (word_check), and its note comes once that has passed."""
        p = self.p
        if not self.wrapper:
            return ""
        again = "nexus %s --wrapper %s NAME" % (p.name, shlex.quote(self.wrapper_given)) \
            + ("".join(" " + shlex.quote(w) for w in ["--"] + list(cmd)) if cmd else "")
        if session == "main":
            die("session main is %s's shell, with %s's own wrapper file or none: name a new session, %s"
                % (p.name, p.name, again))
        got, _ = self.wrapper_probe(session)
        own = got.get("session")
        if own is None or own == self.wrapper:
            note = self.stage_wrapper()
            if own is None and cmd and bash_basename(cmd[0]) in SHIMS:
                self.word_check(session, wd, cmd)
            if note:
                self.out.note(note)
            return "" if own else self.wrapper
        if own == "?":
            die("can't read the wrapper file session %s runs under: name a new session, %s" % (session, again))
        if own and own != "-":
            how = "under the wrapper file %s" % own
        elif not own and got.get("default"):
            how = "under %s's own wrapper file (%s, from nexus --config)" % (p.name, got["default"])
        else:
            how = "without a wrapper file"
        die("session %s already runs %s: end it, or name a new session: %s" % (session, how, again))

    def dashdash_check(self, session, wd, cmd):
        """A `-- grok|agent … --wrapper F` command (CMD), for SESSION in WD: the shim's own checks of it there (the probe
        runs CMD with the shim's real grok swapped for /bin/true); refused when they fail, or when SESSION is there
        already (tmux would attach to it, and not run CMD)."""
        p = self.p
        got, lines = self.wrapper_probe(session, wd, "", *cmd)
        f = dashdash_wrapper(cmd)
        if got.get("check") == "other":
            die("%s in %s isn't the grok shim, so it would take --wrapper for an option of its own: run grok or agent"
                % (cmd[0], p.name))
        if got.get("check") != "ok":
            same = wd == self.ctx.here or (realpath_e(wd) or wd) == (realpath_e(self.ctx.here) or self.ctx.here)
            where = " (%s is read from %s, where the session starts, not from here)" \
                % (f if f.startswith(("./", "../")) else "./" + f, wd) if f and not f.startswith("/") and not same else ""
            die("nothing was started: %s --wrapper would stop at once%s:\n       %s"
                % (bash_basename(cmd[0]), where, "\n       ".join(lines or ["exit %s" % got.get("check", "?")])))
        if "session" in got:
            die("session %s is there already, and tmux would attach to it without running %s: name a new session, nexus "
                "%s NAME -- %s" % (session, " ".join(cmd), p.name, " ".join(shlex.quote(w) for w in cmd)))

    def word_check(self, session, wd, cmd):
        """The word's new SESSION in WD, with a grok or agent command (CMD): the shim's own checks of it there under
        NEXUS_WRAPPER (the file as the persona sees it; the probe runs CMD with the shim's real grok swapped for
        /bin/true), so a command that isn't the shim (grok itself, which would run without the file) or one the shim
        would stop at once (the file and CMD's own --rules over the limit, say) starts nothing; a copy this run made for
        it goes again (one there before stays: another session may run under it)."""
        p = self.p
        got, lines = self.wrapper_probe(session, wd, self.wrapper, *cmd)
        if got.get("check") == "ok":
            return
        if self.fresh:
            try:
                os.unlink(self.fresh)
            except OSError:
                pass
        if got.get("check") == "other":
            die("%s in %s isn't the grok shim, which is what reads --wrapper's file, so it would run without it: run "
                "grok or agent" % (cmd[0], p.name))
        die("nothing was started: %s would stop at once under --wrapper %s:\n       %s"
            % (bash_basename(cmd[0]), self.wrapper_given, "\n       ".join(lines or ["exit %s" % got.get("check", "?")])))

    def made_without(self, m):
        """A running container (its mounts: M) made without its scratch dir, or with another root's (NEXUS_SCRATCH_ROOT
        moved since), or without the Nexus notes (made before nexus gave them): said, with the restart that adds them.
        No mounts known (it couldn't be inspected): nothing said."""
        p = self.p
        no_dir, no_notes = p.scratch not in m, nts.TARGETS[p.flavour] not in m
        lacks = (["its scratch dir %s" % p.scratch] if no_dir else []) + (["the Nexus notes"] if no_notes else [])
        if m and lacks:
            self.out.warn("%s's container was made without %s: `nexus %s restart` adds %s (that ends its tmux sessions)"
                          % (p.name, " and ".join(lacks), p.name, "them" if no_notes else "it"))

    def tele_made_before(self, m):
        """A running container (its mounts: M) made before the persona's telemetry changed: said, with the restart
        that applies it. A claude one sends while its container mounts the drop-in, as that drop-in was when the
        container was made (a new one is written by a rename: other settings, tool details on or off); a grok one has
        the token header in its environment (with no table in its managed config, which is rewritten in place, a grok
        stops sending at once)."""
        p, a = self.p, self.ctx.agents_dir
        if p.flavour == "claude" and (p.telemetry or os.path.isfile(os.path.join(a, tele.dropin_file(p.name)))):
            made = tele.DROPIN_TARGET in m
        elif p.flavour == "grok" and p.telemetry:
            made = tele.GROK_HEADERS[0] in docker.env_keys(p.container)
        else:
            return
        if p.telemetry and not made:
            self.out.warn("%s sends telemetry now, but its container was made before that and doesn't: `nexus %s restart` "
                          "applies it (that ends its tmux sessions)" % (p.name, p.name))
        elif p.telemetry and p.flavour == "claude" and self.dropin_stale():
            self.out.warn("%s's telemetry settings changed after its container was made, and it still sends with the old "
                          "ones: `nexus %s restart` applies the new ones (that ends its tmux sessions)" % (p.name, p.name))
        elif made and not p.telemetry:
            self.out.warn("%s no longer sends telemetry, but its container was made while it did and still sends it: `nexus "
                          "%s restart` stops it (that ends its tmux sessions)" % (p.name, p.name))

    def dropin_stale(self):
        """Whether the running container's telemetry drop-in isn't the one in the checkout (a read that fails: not)."""
        p = self.p
        try:
            with open(os.path.join(self.ctx.agents_dir, tele.dropin_file(p.name)), encoding="utf-8",
                      errors="surrogateescape") as f:
                mine = f.read()
        except OSError:
            return False
        theirs = docker.read_file(p.container, tele.DROPIN_TARGET)
        return theirs is not None and theirs != mine

    def attach(self, pl, old, args):
        p, a = self.p, self.ctx.agents_dir
        why = self.ctx.step_problem()
        if why:     # a running one was made from some file: a restart makes it from the one written again
            self.out.warn("%s, then `nexus %s restart`" % (why, p.name))
        session, cmd = split_attach(args)
        session = (session or bash_basename(pl.dir).rstrip("\n")).replace(".", "_").replace(":", "_")   # $(basename …)
        wd = pl.dir
        if not pl.inside:
            if not proc.ok(["docker", "exec", p.container, "tmux", "has-session", "-t", "=" + session]):
                die(moved(p.name, old, p.root + p.root_from, "which %s isn't in" % self.ctx.here)
                    + " (until then, a session it runs can be attached to by name: nexus %s ls lists them)" % p.name)
            self.out.warn(moved(p.name, old, p.root + p.root_from,
                                "which %s isn't in; attaching to its session %s" % (self.ctx.here, session)))
            wd = old
        perm = self.mode_env(session)
        wrap = self.wrapper_env(session, wd, cmd)
        form = p.flavour == "grok" and dashdash_wrapper(cmd) is not None
        if form:
            self.dashdash_check(session, wd, cmd)
        # bash set TERM=dumb itself when it was unset, so ${TERM:-xterm-256color} only caught an empty one.
        term = self.env.get("TERM", "dumb") or "xterm-256color"
        if not proc.ok(["docker", "exec", p.container, "infocmp", term]):
            term = "xterm-256color"
        # A new session in a mode or with a wrapper of its own, or for a `-- grok … --wrapper F`, isn't attached to one
        # that is there after all (made since the probe, or named so that tmux, which expands # and \ in -s, found it
        # under another name): tmux refuses it then.
        proc.execvp(["docker", "exec", "-it", "-e", "TERM=" + term, "-w", wd, p.container,
                     "tmux", "new-session"] + ([] if (perm or wrap or form) else ["-A"]) + ["-s", session, "-c", wd]
                    + (["-e", "NEXUS_PERMISSIONS=" + perm] if perm else [])
                    + (["-e", WRAPPER_VAR + "=" + wrap] if wrap else []) + cmd)


def scratch_dir(root, name, where, a):
    """NAME's scratch dir under the shared scratch ROOT (from WHERE), made if it isn't there, mode 755 whatever the
    umask (a container that runs as another user goes through it); the root must be there already: nexus --config
    makes it once, with sudo when it has to, and nexus never runs sudo. A: the checkout, for the messages. A root the
    rules refuse: NexusError; one that is in the way as it is on the VM: NoScratch."""
    if not scratch.root_ok(root):
        die("the shared scratch root must be %s, not '%s' (%s)" % (scratch.RULE, root, where))
    if os.path.lexists(root) and not os.path.isdir(root):     # a file, or a link to nothing: install -d would fail
        raise NoScratch("the shared scratch root %s isn't a directory (%s): %s" % (root, where, scratch.NOT_DIR))
    if not os.path.isdir(root):
        raise NoScratch("the shared scratch root %s isn't there (%s): %s/nexus --config creates it, or run: %s; %s"
                        % (root, where, a, scratch.install_cmd(root), scratch.ELSEWHERE))
    d = scratch.path(root, name)
    if not os.path.isdir(d):
        try:
            os.mkdir(d)
            os.chmod(d, 0o755)
        except OSError as e:
            if e.errno in (errno.EACCES, errno.EPERM):
                raise NoScratch("can't create %s's scratch dir %s (%s): you can't write in the shared scratch root %s "
                                "(%s); make it yours: %s, %s"
                                % (name, d, e.strerror, root, where, scratch.install_cmd(root), scratch.ELSEWHERE))
            raise NoScratch("can't create %s's scratch dir %s (%s)" % (name, d, e.strerror))
    return d


def copy_gone(a, w, name):
    """The refusal for NAME's wrapper file's copy W (in the checkout A) gone, with the commands that give it again or
    none, A shell-quoted."""
    nx = shlex.quote(a + "/nexus")
    return ("%s/%s isn't there, and %s's container mounts it (its wrapper file), so nexus won't start it: give it again, "
            "%s --config --wrapper %s:PATH, or none, %s --config --wrapper %s:none" % (a, w, name, nx, name, nx, name))


def service_text(text, name):
    """NAME's service in compose.personas.yml's TEXT: from its name to the blank line after it ("" if none)."""
    start = text.find('\n  "%s":\n' % name)
    if start < 0:
        return ""
    end = text.find("\n\n", start + 1)
    return text[start:] if end < 0 else text[start:end]


def tele_leftovers(a, name, mine):
    """Once NAME's container is made from its service MINE: a telemetry file of its that the service doesn't name any
    more goes (a neutral drop-in, left for the container before, which mounted it)."""
    for f in (tele.dropin_file(name), tele.env_file(name)):
        if f not in mine:
            try:
                os.unlink(os.path.join(a, f))
            except OSError:
                pass
