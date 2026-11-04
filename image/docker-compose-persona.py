#!/usr/bin/python3 -IS
"""docker compose in a persona, with the persona in front of the project name: `<persona>-<the name compose would
pick>`. Personas share one daemon, so without it two personas' stacks of a directory with the same name (or the same
`name:`) would be one project, and one persona's `up` or `down` would take over the other's containers.

Installed as /usr/local/lib/docker/cli-plugins/docker-compose, which the docker CLI finds before the real plugin in
/usr/libexec/docker/cli-plugins (NEXUS_COMPOSE_REAL names another one, for the tests). Run standalone, it works the
same way.

How: compose itself says which name it would pick (the probe): the real binary, standalone, with the same root
flags, `config --format json --no-env-resolution --no-consistency`, stdin /dev/null, stderr dropped, 60 s at most.
Its `.name` gets the prefix and goes in COMPOSE_PROJECT_NAME, which beats `.env`, --env-file and `name:`; then the
real plugin is exec'd with the arguments as they came, so the TTY, stdin, signals, the exit code and the docker
CLI's plugin socket are its own. Until then the socket is the shim's to watch: a docker CLI told to stop without a
TTY closes it (that is how it stops a plugin), and the shim exits 130 as compose would, before compose runs.

Run as asked, nothing added (the real plugin, exec'd at once):
- an explicit -p/--project-name, or an exported COMPOSE_PROJECT_NAME (non-empty: empty counts as absent, as for
  compose), quietly: that is how a stack is reached on purpose;
- a compose or env file from stdin or a pipe (`-f -`, /dev/stdin, `<(…)`, a FIFO; COMPOSE_FILE too): the probe
  can't read it before compose does. One line on stderr says the project has no prefix;
- the docker CLI's metadata and hook calls; no subcommand; a help or version flag; version, ls, help, completion and
  shell completion (no project there);
- anything it doesn't follow: a flag it doesn't know, before `compose` or after it (compose or docker says what is
  wrong with it), `--`, AGENT_PERSONA unset or not a persona name;
- a name that is the persona's already (the persona's own name, or `<persona>-…`): never twice;
- a probe that fails (no compose file: compose then says so, as it would have), quietly.
Not a wall: `-p other` reaches any project, as it should for stacks made before this. The docker guard is what
keeps a persona from removing another one's containers.

Speed: every `docker compose …` runs this twice (the CLI's metadata call, then the command), so what runs as asked
imports nothing (no re, json, subprocess, select or socket) before the exec: `docker compose version` takes ~15 ms
more than without it. A prefixed command pays the probe too: `docker compose config` ~47 ms more (Compose v5.6.1, 4
CPUs). A remote `include:` or `-f oci://…` is fetched twice, by the probe and by the command: an unreachable one
costs compose's own timeout twice (30 s each for a registry that doesn't answer) before its one error.

Pitfalls (each pinned where named):
- docker's own flags before `compose` are skipped by what they take: `docker --context compose compose ps` has the
  subcommand ps (test_compose_shim.Decide.test_docker_flags_before_compose_by_arity)
- short flags bundled as pflag takes them, booleans first: `docker -Dl warn compose`, -Dlwarn, -Dl=warn, -D=true
  (test_compose_shim.Flags.test_booleans_and_unknowns,
  test_compose_shim.Decide.test_docker_flags_before_compose_by_arity)
- the flags after the subcommand are its own (`ps -p x` is no explicit name: compose refuses it there)
  (test_compose_shim.Decide.test_the_subcommand_ends_the_root_flags)
- all five spellings of a flag's value: -p X, -pX, -p=X, --project-name X, --project-name=X
  (test_compose_shim.Flags.test_the_five_spellings, test_compose_shim.Decide.test_an_explicit_name_wins)
- -p "" and --project-name= count as absent, as for compose (test_compose_shim.Decide.test_an_empty_name_is_absent)
- an exported COMPOSE_PROJECT_NAME is taken as given, quietly, even one a Makefile or an .envrc exported unseen: a
  line on stderr would come with every deliberate use too (test_compose_shim.Decide.test_an_explicit_name_wins)
- a compose or env file the probe can't read first runs as asked, and says so: stdin in any spelling or in
  COMPOSE_FILE; a path to one of the shim's file descriptors (/dev/stdin, /dev/fd/N from `<(…)`), whatever it is,
  as the probe has fds of its own; a FIFO, which the probe would drain and compose then wait on for ever. Not
  /dev/null, which reads the same everywhere (test_compose_shim.Decide.test_the_compose_file_on_stdin,
  test_compose_shim.Run.test_a_piped_file_runs_as_asked_and_says_so)
- the name is the one compose picks (its .env, --env-file, name:, -f elsewhere, a subdirectory, COMPOSE_FILE, which
  reads the working directory's .env): asked, never worked out here
  (test_compose_shim.Real.test_the_name_compose_picks_gets_the_prefix)
- the probe runs the real binary standalone: with DOCKER_CLI_PLUGIN_* in its environment compose refuses `config
  --format` (test_compose_shim.Run.test_plugin_mode_probes_standalone_and_keeps_the_plugin_env)
- the probe reads no stdin, so `… | docker compose run -T s cat` still gets it
  (test_compose_shim.Run.test_stdin_reaches_compose)
- a docker CLI signalled without a TTY doesn't pass the signal on: it closes its plugin socket, and compose only
  listens on it once exec'd. While probing the shim does: closed, it kills the probe and exits 130, nothing run
  (test_compose_shim.Run.test_a_closed_plugin_socket_stops_it_before_compose,
  test_compose_shim.Run.test_an_open_plugin_socket_changes_nothing)
- --no-env-resolution and --no-consistency, so a missing env_file or an undefined network don't fail the probe; no
  --no-interpolate, which gets `name: x-${Y}` wrong (test_compose_shim.Real.test_the_probe_flags)
- never twice: a name that is the persona or starts with `<persona>-` stays; `<persona>x` doesn't count
  (test_compose_shim.Prefix.test_never_twice, test_compose_shim.Real.test_already_prefixed_stays)
- a probe that fails, hangs or says nothing usable runs the command unprefixed, quietly (compose's own error, if
  any, follows). A compose that refuses the probe's flags would make that every command: the real-compose tests
  fail on it rather than skip (test_compose_shim.Probe.test_failures,
  test_compose_shim.Run.test_a_failed_probe_runs_it_as_asked, test_compose_shim.Real.test_the_probe_flags)
- never exec itself: NEXUS_COMPOSE_REAL or the default path pointing here would loop
  (test_compose_shim.Run.test_it_refuses_to_run_itself)
- Docker Compose missing (or this shim): the CLI's metadata call still gets metadata, as the CLI would hide a failed
  one behind "unknown command: docker compose"; the command then says what is wrong
  (test_compose_shim.Run.test_it_refuses_to_run_itself, test_compose_shim.Run.test_a_missing_compose_is_one_line)
- nothing imported on the way through (test_compose_shim.Run.test_passing_through_imports_nothing)
"""
import os
import stat
import sys

REAL = "/usr/libexec/docker/cli-plugins/docker-compose"
PROBE = ["config", "--format", "json", "--no-env-resolution", "--no-consistency"]
PROBE_TIMEOUT = 60
# What the metadata call says when Docker Compose can't run: the CLI lists the plugin (`docker --help`) and runs it.
METADATA = ('{"SchemaVersion": "0.1.0", "Vendor": "nexus", "Version": "missing", '
            '"ShortDescription": "Docker Compose is missing (nexus compose shim)"}\n')
# decide()'s answer for a compose or env file from stdin or a pipe: run as asked, saying this (%s: the persona).
PIPED = ("a compose or env file from stdin or a pipe can't be read twice, so the project has no %s- prefix (use a "
         "file, or -p %s-<name>)")

# The docker CLI's global flags, which come before "compose" in what a plugin is given: taking a value, or not.
DOCKER_VAL = {"--config", "--context", "-c", "--host", "-H", "--log-level", "-l", "--tlscacert", "--tlscert",
              "--tlskey"}
DOCKER_BOOL = {"--debug", "-D", "--tls", "--tlsverify", "--version", "-v", "--help", "-h"}
# compose's root flags (its hidden ones too), before the subcommand. Standalone, it takes docker's connection flags
# as well, but not --config/-c.
HELPISH = {"-h", "--help", "-v", "--version"}
COMPOSE_VAL = {"--project-name", "-p", "--file", "-f", "--env-file", "--project-directory", "--workdir",
               "--profile", "--progress", "--ansi", "--parallel"}
COMPOSE_BOOL = {"--all-resources", "--compatibility", "--dry-run", "--verbose", "--no-ansi"} | HELPISH
STANDALONE_VAL = COMPOSE_VAL | (DOCKER_VAL - {"-c", "--config"})
STANDALONE_BOOL = COMPOSE_BOOL | DOCKER_BOOL
NO_PROJECT = {"version", "ls", "help", "completion", "__complete", "__completeNoDesc"}
# Paths to a process's own file descriptors: the probe, another process, would open its own.
FD_PATHS = ("/dev/fd/", "/proc/self/fd/", "/proc/thread-self/fd/")

PERSONA_CHARS = frozenset("abcdefghijklmnopqrstuvwxyz0123456789-")
PROJECT_CHARS = PERSONA_CHARS | {"_"}


def persona_ok(p):
    """A persona name as nexus makes them ([a-z][a-z0-9-]{0,30}, no trailing -), checked without re (startup)."""
    return 0 < len(p) <= 31 and "a" <= p[0] <= "z" and p[-1] != "-" and set(p) <= PERSONA_CHARS


def split_flag(tok, val_flags, bool_flags):
    """TOK as [(flag, its value in TOK or None, whether it takes one)], or None for a flag not in either set.
    pflag's spellings: --flag value, --flag=value, -x value, -x=value, -xvalue; --bool=true, -b=true too; and short
    ones bundled, booleans first, as -bx value, -bxvalue, -bx=value."""
    if tok.startswith("--"):
        name, eq, val = tok.partition("=")
        if name in val_flags:
            return [(name, (val if eq else None), True)]
        return [(name, None, False)] if name in bool_flags else None
    seen, rest = [], tok[1:]
    while rest:
        name, rest = "-" + rest[0], rest[1:]
        if name in val_flags:                                          # the rest of TOK: -p= is "", -p None
            return seen + [(name, (rest[1:] if rest.startswith("=") else rest or None), True)]
        if name not in bool_flags:
            return None
        seen.append((name, None, False))
        if rest.startswith("="):                                       # -D=true: the boolean's value ends TOK
            break
    return seen


def scan(args, i, val_flags, bool_flags):
    """The flags from args[i] on: (index of the first argument that isn't one, [(flag, value or None)]), or None
    for a flag in neither set or a value missing at the end."""
    seen = []
    while i < len(args):
        tok = args[i]
        if tok == "--" or tok == "-" or not tok.startswith("-"):
            break
        flags = split_flag(tok, val_flags, bool_flags)
        if flags is None:
            return None
        name, val, takes = flags[-1]
        if takes and val is None:                                      # the value is the next argument
            if i + 1 >= len(args):
                return None
            i += 1
            flags[-1] = (name, args[i], takes)
        seen += [(name, val) for name, val, _ in flags]
        i += 1
    return i, seen


def piped(path):
    """Whether compose reads PATH (a compose or env file) where the probe can't read it first: stdin, a path to an
    open file descriptor (the probe has its own), or a FIFO (the probe would drain it)."""
    if path == "-":
        return True
    full = os.path.abspath(path)
    if full == "/dev/stdin" or full.startswith(FD_PATHS):
        return True
    try:
        return stat.S_ISFIFO(os.stat(path).st_mode)
    except OSError:                                                    # not there: compose says so (or fetches it)
        return False


def decide(args, env):
    """Whether ARGS (what the plugin was given, argv[0] aside) run under a prefixed name: the compose root flags to
    probe with (a list, maybe empty); None to run them as asked; PIPED to run them as asked and say so."""
    if not args or args[0].startswith("docker-cli-plugin-"):          # metadata, hooks
        return None
    if not persona_ok(env.get("AGENT_PERSONA", "")) or env.get("COMPOSE_PROJECT_NAME"):
        return None
    if env.get("DOCKER_CLI_PLUGIN_ORIGINAL_CLI_COMMAND"):            # plugin: <docker flags> compose <…>
        r = scan(args, 0, DOCKER_VAL, DOCKER_BOOL)
        if r is None or r[0] >= len(args) or args[r[0]] != "compose":
            return None                                                # __complete, shapes it doesn't know
        start, val_flags, bool_flags = r[0] + 1, COMPOSE_VAL, COMPOSE_BOOL
    else:
        start, val_flags, bool_flags = 0, STANDALONE_VAL, STANDALONE_BOOL
    r = scan(args, start, val_flags, bool_flags)
    if r is None:
        return None                                                    # compose says what is wrong
    sub, flags = r
    if sub >= len(args) or args[sub] in NO_PROJECT or args[sub] in ("--", "-"):
        return None
    for name, val in flags:
        if name in HELPISH or name in ("-p", "--project-name") and val:     # "" is absent, as for compose
            return None
    # -f beats COMPOSE_FILE, as for compose
    files = [val for name, val in flags if name in ("-f", "--file")]
    files = files or env.get("COMPOSE_FILE", "").split(env.get("COMPOSE_PATH_SEPARATOR") or os.pathsep)
    if any(piped(f) for f in files if f) or any(piped(val) for name, val in flags if name == "--env-file"):
        return PIPED
    return args[start:sub]


def prefixed(name, persona):
    """<persona>-NAME, or "" when NAME is empty, the persona's already, or not a project name compose takes."""
    if not name or name == persona or name.startswith(persona + "-"):
        return ""
    return persona + "-" + name if set(name) <= PROJECT_CHARS else ""


def cli_conn(addr):
    """A connection to the docker CLI's plugin socket ADDR ("@…": abstract), or None: none (standalone), or one it
    can't reach (compose ignores those too)."""
    if not addr:
        return None
    import socket
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        s.connect("\0" + addr[1:] if addr.startswith("@") else addr)
    except OSError:
        s.close()
        return None
    return s


def probe(real, flags, env, timeout=PROBE_TIMEOUT):
    """The project name compose picks with root FLAGS here, or "" when it can't say. Exits 130 when the docker CLI
    closes its plugin socket meanwhile."""
    import json
    import select
    import subprocess
    import time
    cli = cli_conn(env.get("DOCKER_CLI_PLUGIN_SOCKET", ""))
    env = {k: v for k, v in env.items() if not k.startswith("DOCKER_CLI_PLUGIN_")}
    p, out, stop, name = None, b"", False, ""
    try:
        p = subprocess.Popen([real] + flags + PROBE, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                             stderr=subprocess.DEVNULL, env=env)
        end = time.monotonic() + timeout
        while True:
            left = end - time.monotonic()
            if left <= 0:
                return ""                                              # hung
            ready = select.select([p.stdout] + ([cli] if cli else []), [], [], left)[0]
            if cli is not None and cli in ready:
                try:
                    stop = not cli.recv(1)                             # the CLI closed it: told to stop
                except OSError:
                    stop = True
                if stop:
                    break
            if p.stdout in ready:
                chunk = os.read(p.stdout.fileno(), 65536)
                if not chunk:
                    break
                out += chunk
        if not stop and p.wait(max(end - time.monotonic(), 1)) == 0:
            name = json.loads(out)["name"]
    except Exception:                                                  # gone, hung, not JSON, no name
        return ""
    finally:
        if p is not None:
            if p.poll() is None:
                p.kill()
            p.wait()
            p.stdout.close()
        if cli is not None:
            cli.close()
    if stop:
        sys.exit(130)                                                  # compose's code, stopped so
    return name if isinstance(name, str) else ""


def plan(real, args, env):
    """The environment to run the real plugin with: ENV, or a copy with COMPOSE_PROJECT_NAME set. A piped file
    says on stderr that the project has no prefix."""
    flags = decide(args, env)
    if flags is PIPED:
        sys.stderr.write("nexus compose shim: %s\n" % (PIPED % (env["AGENT_PERSONA"], env["AGENT_PERSONA"])))
    if not isinstance(flags, list):
        return env
    name = prefixed(probe(real, flags, env), env["AGENT_PERSONA"])
    return dict(env, COMPOSE_PROJECT_NAME=name) if name else env


def is_self(path):
    try:
        return os.path.samefile(path, __file__)
    except OSError:
        return False


def die(args, msg, code):
    """Say MSG and exit CODE; but the docker CLI's metadata call gets METADATA, so the command runs and says it."""
    if args[:1] == ["docker-cli-plugin-metadata"]:
        sys.stdout.write(METADATA)
        sys.exit(0)
    sys.stderr.write("nexus compose shim: %s\n" % msg)
    sys.exit(code)


def main():
    args, env = sys.argv[1:], os.environ
    real = env.get("NEXUS_COMPOSE_REAL") or REAL
    if is_self(real):                                                  # it would run itself for ever
        die(args, "%s is this shim, not Docker Compose" % real, 127)
    try:
        env = plan(real, args, env)
    except KeyboardInterrupt:                                          # ^C during the probe
        sys.exit(130)
    try:
        os.execve(real, [real] + args, env)
    except OSError as e:
        die(args, "can't run Docker Compose at %s: %s" % (real, e.strerror),
            127 if isinstance(e, FileNotFoundError) else 126)


if __name__ == "__main__":
    main()
