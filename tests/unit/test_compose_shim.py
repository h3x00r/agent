import importlib.util
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SHIM = os.path.join(ROOT, "image", "docker-compose-persona.py")
PY = [sys.executable, "-I", "-S"]                  # as its #! line runs it


def load():
    spec = importlib.util.spec_from_file_location("docker_compose_persona_under_test", SHIM)
    m = importlib.util.module_from_spec(spec)
    was, sys.dont_write_bytecode = sys.dont_write_bytecode, True     # no image/__pycache__
    try:
        spec.loader.exec_module(m)
    finally:
        sys.dont_write_bytecode = was
    return m


shim = load()
P = "nxwp"
PLUGIN = {"DOCKER_CLI_PLUGIN_ORIGINAL_CLI_COMMAND": "docker", "DOCKER_CLI_PLUGIN_SOCKET": "@docker_cli_0123"}
REAL_OK = os.access(shim.REAL, os.X_OK)

# A stand-in for the real plugin: it logs each call (a line of JSON to $NXW_FAKE_LOG), answers the probe with
# {"name": $NXW_FAKE_NAME} (or $NXW_FAKE_OUT, exit $NXW_FAKE_RC, after $NXW_FAKE_SLEEP s) and noise on stderr, and
# anything else with "compose ran" and exit $NXW_FAKE_EXIT.
FAKE = r'''
import json, os, sys, time
args, e = sys.argv[1:], os.environ
probe = args[-5:] == ["config", "--format", "json", "--no-env-resolution", "--no-consistency"]
rec = {"probe": probe, "args": args, "name": e.get("COMPOSE_PROJECT_NAME"),
       "plugin": sorted(k for k in e if k.startswith("DOCKER_CLI_PLUGIN_")),
       "devnull": os.path.samestat(os.fstat(0), os.stat(os.devnull)), "stdin": sys.stdin.read()}
with open(e["NXW_FAKE_LOG"], "a") as f:
    f.write(json.dumps(rec) + "\n")
if probe:
    sys.stderr.write("probe noise\n")
    time.sleep(float(e.get("NXW_FAKE_SLEEP", "0")))
    sys.stdout.write(e["NXW_FAKE_OUT"] if "NXW_FAKE_OUT" in e else json.dumps({"name": e.get("NXW_FAKE_NAME", "")}))
    sys.exit(int(e.get("NXW_FAKE_RC", "0")))
sys.stdout.write("compose ran\n")
sys.exit(int(e.get("NXW_FAKE_EXIT", "0")))
'''


def plugin(**kw):
    """The environment of a `docker compose …` (what the docker CLI adds) in persona P, KW added."""
    return dict(PLUGIN, AGENT_PERSONA=P, **kw) if "AGENT_PERSONA" not in kw else dict(PLUGIN, **kw)


def standalone(**kw):
    return dict(AGENT_PERSONA=P, **kw)


class Decide(unittest.TestCase):
    def check(self, env, rows):
        for args, want in rows:
            self.assertEqual(shim.decide(args, env), want, args)

    def test_a_project_command_is_probed_with_its_root_flags(self):
        self.check(plugin(), [
            (["compose", "up", "-d"], []),
            (["compose", "-f", "a.yml", "--env-file=x.env", "--profile", "web", "up"],
             ["-f", "a.yml", "--env-file=x.env", "--profile", "web"]),
            (["compose", "--dry-run", "--all-resources", "--compatibility", "--verbose", "--no-ansi", "--workdir", "w",
              "--parallel", "2", "--project-directory=d", "--ansi", "never", "--progress=plain", "down", "-v"],
             ["--dry-run", "--all-resources", "--compatibility", "--verbose", "--no-ansi", "--workdir", "w",
              "--parallel", "2", "--project-directory=d", "--ansi", "never", "--progress=plain"]),
            (["compose", "bridge", "convert"], []),                # loads the project: harmless
        ])

    def test_docker_flags_before_compose_by_arity(self):
        # --context's value may be "compose": the subcommand is what follows the real "compose".
        self.check(plugin(), [
            (["--context", "compose", "compose", "ps"], []),
            (["--context=compose", "compose", "ps"], []),
            (["-c", "compose", "compose", "ps"], []),
            (["-ccompose", "compose", "ps"], []),
            (["--context", "default", "-l", "warn", "compose", "--ansi", "never", "--progress=plain", "ps"],
             ["--ansi", "never", "--progress=plain"]),
            (["-H", "unix:///var/run/docker.sock", "--tlsverify", "-D", "--debug=true", "--config", "/c", "compose",
              "up"], []),
            # short flags bundled, as pflag (docker's) takes them: booleans first, then one that takes a value
            (["-D=true", "compose", "up"], []), (["-D=false", "compose", "up"], []),
            (["-Dl", "warn", "compose", "ps"], []), (["-Dlwarn", "compose", "ps"], []),
            (["-Dl=warn", "compose", "ps"], []),
            (["-Dc", "compose", "compose", "ps"], []),            # the bundle's value flag takes the next one
            (["-D", "compose", "-f", "a.yml", "up"], ["-f", "a.yml"]),
            (["-Dx", "compose", "up"], None), (["-xD", "compose", "up"], None), (["-Dl"], None),
            (["__complete", "compose", "ps", ""], None),           # shell completion: docker's, before compose
            (["__completeNoDesc", "compose", "ps"], None),
            (["--bogus", "compose", "up"], None),
            (["--context"], None),
            (["--context", "x", "ps"], None),
            (["compose", "--context", "x", "ps"], None),           # after compose: compose refuses it
            (["compose", "-H", "unix:///x", "ps"], None),
        ])

    def test_standalone_takes_dockers_connection_flags_as_its_own(self):
        self.check(standalone(), [
            (["up"], []),
            (["-H", "unix:///x", "--context=default", "-D", "-f", "a.yml", "up"],
             ["-H", "unix:///x", "--context=default", "-D", "-f", "a.yml"]),
            (["--log-level", "warn", "--tls", "ps"], ["--log-level", "warn", "--tls"]),
            (["-c", "x", "up"], None),                             # standalone compose refuses -c/--config
            (["--config", "x", "up"], None),
            (["-p", "x", "up"], None),
            (["version"], None),
            (["--help"], None),
        ])

    def test_the_subcommand_ends_the_root_flags(self):
        self.check(plugin(), [
            (["compose", "ps", "-p", "x"], []),
            (["compose", "run", "--rm", "s", "sh", "-c", "-p"], []),
            (["compose", "exec", "-T", "s", "cat", "-f", "-"], []),
        ])

    def test_an_explicit_name_wins(self):
        for flag in (["-p", "x"], ["-px"], ["-p=x"], ["--project-name", "x"], ["--project-name=x"]):
            self.check(plugin(), [(["compose"] + flag + ["up"], None),
                                  (["--context", "default", "compose", "-f", "a.yml"] + flag + ["up"], None)])
            self.check(standalone(), [(flag + ["up"], None)])
        self.check(plugin(COMPOSE_PROJECT_NAME="mine"), [(["compose", "up"], None)])

    def test_an_empty_name_is_absent(self):
        self.check(plugin(), [
            (["compose", "-p", "", "up"], ["-p", ""]),
            (["compose", "-p=", "up"], ["-p="]),
            (["compose", "--project-name", "", "up"], ["--project-name", ""]),
            (["compose", "--project-name=", "up"], ["--project-name="]),
        ])
        self.check(plugin(COMPOSE_PROJECT_NAME=""), [(["compose", "up"], [])])

    def test_the_compose_file_on_stdin(self):
        # Or from a pipe: what the probe can't read before compose does. PIPED: run as asked, saying so.
        PIPED = shim.PIPED
        for flag in (["-f", "-"], ["-f-"], ["-f=-"], ["--file", "-"], ["--file=-"]):
            self.check(plugin(), [(["compose"] + flag + ["up"], PIPED)])
        self.check(plugin(COMPOSE_FILE="-"), [(["compose", "up"], PIPED), (["compose", "-f", "a.yml", "up"],
                                                                           ["-f", "a.yml"])])   # -f beats it
        self.check(plugin(COMPOSE_FILE="a.yml:-"), [(["compose", "up"], PIPED), (["compose", "version"], None)])
        self.check(plugin(COMPOSE_FILE="a.yml;-", COMPOSE_PATH_SEPARATOR=";"), [(["compose", "up"], PIPED)])
        self.check(plugin(COMPOSE_FILE="a.yml:b.yml"), [(["compose", "up"], [])])
        # A path to an open file descriptor: the probe has its own (stdin /dev/null, nothing else), whatever the
        # shim's is (a pipe from <(…), a terminal, a file redirected in). A FIFO the probe would drain.
        tmp = tempfile.mkdtemp(prefix="nxw-shim-")
        self.addCleanup(shutil.rmtree, tmp, True)
        r, w = os.pipe()
        self.addCleanup(os.close, r)
        self.addCleanup(os.close, w)
        fifo, plain = os.path.join(tmp, "fifo"), os.path.join(tmp, "compose.yaml")
        os.mkfifo(fifo)
        with open(plain, "w") as f:
            f.write("services: {}\n")
        for path in ("/dev/stdin", "/dev/fd/%d" % r, "/proc/self/fd/%d" % r, "/dev/fd/0", fifo):
            self.check(plugin(), [(["compose", "-f", path, "up"], PIPED),
                                  (["compose", "-f", plain, "-f", path, "up"], PIPED),
                                  (["compose", "--env-file", path, "up"], PIPED),
                                  (["compose", "-f", path, "-p", "x", "up"], None)])       # -p still wins
            self.check(plugin(COMPOSE_FILE=path), [(["compose", "up"], PIPED)])
        self.check(plugin(), [(["compose", "-f", plain, "--env-file", "/dev/null", "up"],
                               ["-f", plain, "--env-file", "/dev/null"]),
                              (["compose", "-f", os.path.join(tmp, "nothing.yml"), "up"],
                               ["-f", os.path.join(tmp, "nothing.yml")]),               # compose says it's missing
                              (["compose", "-f", "oci://example.com/x:1", "up"], ["-f", "oci://example.com/x:1"])])

    def test_no_project_commands_run_as_asked(self):
        self.check(plugin(), [
            ([], None), (["compose"], None), (["compose", "-f", "a.yml"], None),
            (["compose", "version"], None), (["compose", "-f", "a.yml", "version", "--short"], None),
            (["compose", "ls", "-a"], None), (["compose", "help", "up"], None),
            (["compose", "completion", "bash"], None), (["compose", "__complete", "up", ""], None),
            (["compose", "--help"], None), (["compose", "-h", "up"], None), (["compose", "-v"], None),
            (["compose", "--version"], None),
            (["docker-cli-plugin-hooks", "compose", "up"], None),
        ])
        # the metadata call comes without the plugin variables
        self.check(standalone(), [(["docker-cli-plugin-metadata"], None)])

    def test_what_it_doesnt_follow_runs_as_asked(self):
        self.check(plugin(), [
            (["compose", "--bogus", "up"], None), (["compose", "-x", "up"], None), (["compose", "-f"], None),
            (["compose", "--", "up"], None), (["compose", "-", "up"], None),
        ])
        for persona, want in ((None, None), ("", None), ("Agent", None), ("agent-", None), ("1agent", None),
                              ("a_b", None), ("a" * 32, None), ("a" * 31, []), ("a-b", []), ("a", [])):
            env = plugin(AGENT_PERSONA=persona) if persona is not None else dict(PLUGIN)
            self.assertEqual(shim.decide(["compose", "up"], env), want, persona)


class Flags(unittest.TestCase):
    def test_the_five_spellings(self):
        for long, short, value in (("--project-name", "-p", "x"), ("--file", "-f", "a.yml")):
            for args, flag in (([short, value], short), ([short + value], short), ([short + "=" + value], short),
                               ([long, value], long), ([long + "=" + value], long)):
                self.assertEqual(shim.scan(args + ["up"], 0, shim.COMPOSE_VAL, shim.COMPOSE_BOOL),
                                 (len(args), [(flag, value)]), args)

    def test_booleans_and_unknowns(self):
        sv, sb, dv, db = shim.COMPOSE_VAL, shim.COMPOSE_BOOL, shim.DOCKER_VAL, shim.DOCKER_BOOL
        for tok, flags, want in (
                ("--dry-run", "s", [("--dry-run", None, False)]), ("--dry-run=true", "s", [("--dry-run", None, False)]),
                ("-D", "d", [("-D", None, False)]), ("-D=true", "d", [("-D", None, False)]),
                ("-D=false", "d", [("-D", None, False)]),
                ("-Dl", "d", [("-D", None, False), ("-l", None, True)]),          # -l's value: the next argument
                ("-Dlwarn", "d", [("-D", None, False), ("-l", "warn", True)]),
                ("-Dl=warn", "d", [("-D", None, False), ("-l", "warn", True)]),
                ("-lD", "d", [("-l", "D", True)]),                                 # a value flag takes the rest
                ("-Dx", "d", None), ("-Dtrue", "d", None), ("--bogus", "s", None), ("-x", "s", None),
                ("-p", "s", [("-p", None, True)]), ("-p=", "s", [("-p", "", True)]),
                ("--project-name=", "s", [("--project-name", "", True)])):
            self.assertEqual(shim.split_flag(tok, *((dv, db) if flags == "d" else (sv, sb))), want, tok)
        self.assertEqual(shim.scan(["-p=", "up"], 0, sv, sb), (1, [("-p", "")]))
        self.assertEqual(shim.scan(["-Dl", "warn", "compose"], 0, dv, db), (2, [("-D", None), ("-l", "warn")]))
        self.assertIsNone(shim.scan(["-f", "a.yml", "-p"], 0, sv, sb))   # a value missing at the end
        self.assertIsNone(shim.scan(["-Dl"], 0, dv, db))


class Prefix(unittest.TestCase):
    def test_never_twice(self):
        for name, want in (("proj", "nxwp-proj"), ("nxwp", ""), ("nxwp-proj", ""), ("nxwp-", ""),
                           ("nxwpx", "nxwp-nxwpx"), ("", "")):
            self.assertEqual(shim.prefixed(name, P), want, name)
        # persona agent with x-app and persona agent-x with app meet: the separator's known ambiguity
        self.assertEqual(shim.prefixed("x-app", "agent"), shim.prefixed("app", "agent-x"))

    def test_only_names_compose_takes(self):
        for name, want in (("my_proj-1", "nxwp-my_proj-1"), ("1x", "nxwp-1x"), ("a b", ""), ("Up", ""),
                           ("a.b", ""), ("a/b", "")):
            self.assertEqual(shim.prefixed(name, P), want, name)

    def test_the_default_real_plugin(self):
        self.assertEqual(shim.REAL, "/usr/libexec/docker/cli-plugins/docker-compose")


class FakeCompose(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="nxw-shim-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.fake = os.path.join(self.tmp, "docker-compose")
        with open(self.fake, "w") as f:
            f.write("#!%s -I\n%s" % (sys.executable, FAKE))
        os.chmod(self.fake, 0o755)
        self.log = os.path.join(self.tmp, "calls.jsonl")

    def env(self, **kw):
        """A clean environment (nothing of this one's AGENT_PERSONA, COMPOSE_* or DOCKER_*), KW added; None drops."""
        e = {"PATH": os.environ.get("PATH", os.defpath), "HOME": self.tmp, "AGENT_PERSONA": P,
             "NEXUS_COMPOSE_REAL": self.fake, "NXW_FAKE_LOG": self.log}
        e.update(kw)
        return {k: v for k, v in e.items() if v is not None}

    def reset(self):
        if os.path.exists(self.log):
            os.unlink(self.log)

    def calls(self):
        if not os.path.exists(self.log):
            return []
        with open(self.log) as f:
            return [json.loads(line) for line in f]

    def shim(self, args, env, data=None):
        """The shim run as the docker CLI runs it: (rc, stdout, stderr), its calls in self.calls()."""
        io = {"input": data} if data is not None else {"stdin": subprocess.DEVNULL}
        r = subprocess.run(PY + [SHIM] + args, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           timeout=60, **io)
        return r.returncode, r.stdout.decode(), r.stderr.decode()


class Probe(FakeCompose):
    def test_it_asks_compose_standalone_with_nothing_on_stdin(self):
        self.assertEqual(shim.probe(self.fake, ["-f", "a.yml"], self.env(NXW_FAKE_NAME="proj", **PLUGIN)), "proj")
        (c,) = self.calls()
        self.assertEqual((c["args"], c["plugin"], c["devnull"]), (["-f", "a.yml"] + shim.PROBE, [], True))

    def test_failures(self):
        for kw in ({"NXW_FAKE_RC": "1", "NXW_FAKE_NAME": "proj"}, {"NXW_FAKE_OUT": "not json"},
                   {"NXW_FAKE_OUT": '{"services": {}}'}, {"NXW_FAKE_OUT": '{"name": 3}'}, {"NXW_FAKE_OUT": "[]"},
                   {"NXW_FAKE_OUT": ""}):
            self.assertEqual(shim.probe(self.fake, [], self.env(**kw)), "", kw)
        self.assertEqual(shim.probe(os.path.join(self.tmp, "nothing"), [], self.env()), "")
        self.assertEqual(shim.probe(self.log, [], self.env()), "")                 # not executable
        self.assertEqual(shim.probe(self.fake, [], self.env(NXW_FAKE_SLEEP="20", NXW_FAKE_NAME="proj"),
                                    timeout=0.5), "")                                # hung

    def test_plan(self):
        env = self.env(NXW_FAKE_NAME="proj", **PLUGIN)
        self.assertEqual(shim.plan(self.fake, ["compose", "up"], env), dict(env, COMPOSE_PROJECT_NAME="nxwp-proj"))
        self.assertIs(shim.plan(self.fake, ["compose", "-p", "x", "up"], env), env)
        already = self.env(NXW_FAKE_NAME="nxwp-proj", **PLUGIN)
        self.assertIs(shim.plan(self.fake, ["compose", "up"], already), already)

    def test_is_self(self):
        link = os.path.join(self.tmp, "link")
        os.symlink(SHIM, link)
        self.assertEqual([shim.is_self(p) for p in (SHIM, link, self.fake, os.path.join(self.tmp, "nothing"))],
                         [True, True, False, False])


class Run(FakeCompose):
    def test_standalone_gets_the_prefix(self):
        rc, out, err = self.shim(["up", "-d"], self.env(NXW_FAKE_NAME="proj"))
        self.assertEqual((rc, out, err), (0, "compose ran\n", ""))               # the probe's stderr dropped
        probe, run = self.calls()
        self.assertEqual((probe["probe"], probe["args"]), (True, shim.PROBE))
        self.assertEqual((run["probe"], run["args"], run["name"]), (False, ["up", "-d"], "nxwp-proj"))

    def test_plugin_mode_probes_standalone_and_keeps_the_plugin_env(self):
        args = ["--context", "compose", "compose", "-f", "a.yml", "--env-file=x.env", "up", "-d"]
        rc, out, err = self.shim(args, self.env(NXW_FAKE_NAME="proj", **PLUGIN))
        self.assertEqual((rc, err), (0, ""))
        probe, run = self.calls()
        self.assertEqual((probe["args"], probe["plugin"]), (["-f", "a.yml", "--env-file=x.env"] + shim.PROBE, []))
        self.assertEqual((run["args"], run["plugin"], run["name"]), (args, sorted(PLUGIN), "nxwp-proj"))

    def test_stdin_reaches_compose(self):
        rc, out, err = self.shim(["compose", "run", "--rm", "-T", "s", "cat"],
                                 self.env(NXW_FAKE_NAME="proj", **PLUGIN), data=b"hello\n")
        probe, run = self.calls()
        self.assertEqual((probe["devnull"], probe["stdin"]), (True, ""))
        self.assertEqual((run["stdin"], run["name"]), ("hello\n", "nxwp-proj"))

    def test_passing_through(self):
        # (args, env): run as asked, no probe, COMPOSE_PROJECT_NAME as it was
        for args, kw, name in ((["docker-cli-plugin-metadata"], {}, None),
                               (["compose", "-p", "x", "up"], PLUGIN, None),
                               (["compose", "up"], dict(PLUGIN, COMPOSE_PROJECT_NAME="mine"), "mine"),
                               (["compose", "up"], dict(PLUGIN, AGENT_PERSONA=None), None),
                               (["compose", "version"], PLUGIN, None),
                               (["compose", "--bogus", "up"], PLUGIN, None)):
            self.reset()
            rc, out, err = self.shim(args, self.env(NXW_FAKE_NAME="proj", **kw))
            self.assertEqual((rc, out, err), (0, "compose ran\n", ""), args)
            (run,) = self.calls()
            self.assertEqual((run["args"], run["name"]), (args, name), args)

    def test_a_piped_file_runs_as_asked_and_says_so(self):
        # The compose file on stdin reaches compose whole; a FIFO isn't opened (the probe would drain it, and compose
        # then wait for a writer for ever). One line on stderr each.
        say = "nexus compose shim: %s\n" % (shim.PIPED % (P, P))
        rc, out, err = self.shim(["compose", "-f", "-", "up"], self.env(NXW_FAKE_NAME="proj", **PLUGIN),
                                 data=b"services: {}\n")
        self.assertEqual((rc, out, err), (0, "compose ran\n", say))
        (run,) = self.calls()
        self.assertEqual((run["probe"], run["name"], run["stdin"]), (False, None, "services: {}\n"))
        fifo = os.path.join(self.tmp, "fifo")
        os.mkfifo(fifo)
        for args in (["compose", "-f", fifo, "up"], ["compose", "--env-file", fifo, "up"], ["-f", fifo, "up"]):
            self.reset()
            rc, out, err = self.shim(args, self.env(NXW_FAKE_NAME="proj", **(PLUGIN if args[0] == "compose" else {})))
            self.assertEqual((rc, out, err), (0, "compose ran\n", say), args)
            self.assertEqual([(c["probe"], c["name"]) for c in self.calls()], [(False, None)], args)

    def test_a_failed_probe_runs_it_as_asked(self):
        for kw in ({"NXW_FAKE_RC": "1"}, {"NXW_FAKE_NAME": "nxwp-proj"}, {"NXW_FAKE_NAME": ""},
                   {"NXW_FAKE_OUT": "not json"}):
            self.reset()
            rc, out, err = self.shim(["compose", "up"], self.env(**dict(PLUGIN, **kw)))
            self.assertEqual((rc, out, err), (0, "compose ran\n", ""), kw)
            probe, run = self.calls()
            self.assertEqual((run["args"], run["name"]), (["compose", "up"], None), kw)

    def test_the_exit_code_is_composes(self):
        rc, out, err = self.shim(["compose", "up"], self.env(NXW_FAKE_NAME="proj", NXW_FAKE_EXIT="3", **PLUGIN))
        self.assertEqual(rc, 3)

    def metadata(self, out):
        """OUT as the docker CLI reads a plugin's metadata: the fields it requires, and what `docker --help` shows."""
        m = json.loads(out)
        return m["SchemaVersion"], bool(m["Vendor"]), m["ShortDescription"]

    def test_it_refuses_to_run_itself(self):
        link = os.path.join(self.tmp, "link")
        os.symlink(SHIM, link)
        for real in (SHIM, link):
            rc, out, err = self.shim(["up"], self.env(NEXUS_COMPOSE_REAL=real, NXW_FAKE_NAME="proj"))
            self.assertEqual((rc, out, err),
                             (127, "", "nexus compose shim: %s is this shim, not Docker Compose\n" % real))
            # The metadata call answers, so that `docker compose …` runs the shim, which says the above.
            rc, out, err = self.shim(["docker-cli-plugin-metadata"], self.env(NEXUS_COMPOSE_REAL=real))
            self.assertEqual((rc, err), (0, ""))
            self.assertEqual(self.metadata(out), ("0.1.0", True, "Docker Compose is missing (nexus compose shim)"))
        self.assertEqual(self.calls(), [])

    def test_a_missing_compose_is_one_line(self):
        gone = os.path.join(self.tmp, "nothing")
        rc, out, err = self.shim(["up"], self.env(NEXUS_COMPOSE_REAL=gone))
        self.assertEqual((rc, err), (127, "nexus compose shim: can't run Docker Compose at %s: No such file or "
                                          "directory\n" % gone))
        noexec = os.path.join(self.tmp, "noexec")
        open(noexec, "w").close()
        rc, out, err = self.shim(["compose", "up"], self.env(NEXUS_COMPOSE_REAL=noexec, **PLUGIN))
        self.assertEqual((rc, err), (126, "nexus compose shim: can't run Docker Compose at %s: Permission "
                                          "denied\n" % noexec))
        for real in (gone, noexec):
            rc, out, err = self.shim(["docker-cli-plugin-metadata"], self.env(NEXUS_COMPOSE_REAL=real))
            self.assertEqual((rc, err), (0, ""))
            self.assertEqual(self.metadata(out), ("0.1.0", True, "Docker Compose is missing (nexus compose shim)"))

    def listen(self):
        """A stand-in for the docker CLI's plugin socket, abstract as the CLI's: (its address as the CLI gives it in
        DOCKER_CLI_PLUGIN_SOCKET, the listener)."""
        name = "nxw_shim_test_%d_%d" % (os.getpid(), id(self))
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        srv.bind("\0" + name)
        srv.listen(4)
        srv.settimeout(20)
        self.addCleanup(srv.close)
        return "@" + name, srv

    def test_a_closed_plugin_socket_stops_it_before_compose(self):
        # The docker CLI, signalled without a TTY, closes its plugin socket to stop the plugin. During the probe
        # (20 s here) the shim watches it: the probe is killed, compose never runs, and it exits 130 as compose would.
        addr, srv = self.listen()

        def cli():
            c = srv.accept()[0]
            time.sleep(0.5)
            c.close()
            srv.close()
        t = threading.Thread(target=cli, daemon=True)
        t.start()
        env = self.env(NXW_FAKE_NAME="proj", NXW_FAKE_SLEEP="20", **dict(PLUGIN, DOCKER_CLI_PLUGIN_SOCKET=addr))
        began = time.monotonic()
        rc, out, err = self.shim(["compose", "up", "-d"], env)
        self.assertEqual((rc, out, err), (130, "", ""))
        self.assertLess(time.monotonic() - began, 10)
        self.assertEqual([c for c in self.calls() if not c["probe"]], [])          # compose never ran
        t.join(5)

    def test_an_open_plugin_socket_changes_nothing(self):
        # A CLI that isn't stopping: the shim's connection is gone before compose runs (compose makes its own).
        addr, srv = self.listen()
        conns = []
        t = threading.Thread(target=lambda: conns.append(srv.accept()[0]), daemon=True)
        t.start()
        rc, out, err = self.shim(["compose", "up", "-d"],
                                 self.env(NXW_FAKE_NAME="proj", **dict(PLUGIN, DOCKER_CLI_PLUGIN_SOCKET=addr)))
        self.assertEqual((rc, out, err), (0, "compose ran\n", ""))
        probe, run = self.calls()
        self.assertEqual((run["name"], run["plugin"]), ("nxwp-proj", sorted(PLUGIN)))
        t.join(5)
        (c,) = conns
        self.addCleanup(c.close)
        c.settimeout(5)
        self.assertEqual(c.recv(1), b"")                                          # closed: EOF

    def test_passing_through_imports_nothing(self):
        # Every `docker compose …` runs it twice; re, json and subprocess cost ~10 ms. Only the probe imports them
        # (and select and socket, to watch the docker CLI's plugin socket meanwhile).
        def imported(args, env):
            r = subprocess.run(PY + ["-X", "importtime", SHIM] + args, env=env, stdin=subprocess.DEVNULL,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
            return {line.rsplit("|", 1)[-1].strip() for line in r.stderr.decode().splitlines()
                    if line.startswith("import time:")}
        heavy = {"re", "json", "subprocess", "select", "socket"}
        self.assertEqual(imported(["docker-cli-plugin-metadata"], self.env()) & heavy, set())
        self.assertEqual(imported(["compose", "-p", "x", "up"], self.env(**PLUGIN)) & heavy, set())
        self.assertEqual(imported(["compose", "up"], self.env(NXW_FAKE_NAME="proj", **PLUGIN)) & heavy, heavy)


COMPOSE = "services:\n  s:\n    image: busybox:1.36.1-musl\n    pull_policy: never\n"


@unittest.skipUnless(REAL_OK, "no Docker Compose at %s" % shim.REAL)
class Real(unittest.TestCase):
    """The shim with the real plugin, `config --format json` only: no daemon, no image."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="nxw-shim-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.proj = self.write("proj/compose.yaml", COMPOSE)
        # A compose that refuses the probe's flags fails here, not skips: with it every probe would fail, and every
        # project run unprefixed, silently.
        r = subprocess.run([shim.REAL, "-f", self.proj] + shim.PROBE, env=self.env(), stdin=subprocess.DEVNULL,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.assertEqual(r.returncode, 0, "this compose doesn't take the shim's probe: %s" % r.stderr.decode().strip())

    def write(self, rel, text):
        path = os.path.join(self.tmp, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(text)
        return path

    def env(self, **kw):
        e = {"PATH": os.environ.get("PATH", os.defpath), "HOME": self.tmp, "AGENT_PERSONA": P,
             "DOCKER_CONFIG": os.path.join(self.tmp, "docker-config")}
        e.update(kw)
        return {k: v for k, v in e.items() if v is not None}

    def name(self, args, cwd="proj", cmd=PY + [SHIM], **kw):
        """The project name `<cmd> ARGS config --format json` shows, run in CWD (under the temp dir)."""
        r = subprocess.run(cmd + args + ["config", "--format", "json"], cwd=os.path.join(self.tmp, cwd),
                           env=self.env(**kw), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr.decode())
        return json.loads(r.stdout)["name"]

    def test_the_name_compose_picks_gets_the_prefix(self):
        self.write("proj/sub/.keep", "")
        self.write("named/compose.yaml", "name: nxw-fromfile\n" + COMPOSE)
        self.write("dotenv/compose.yaml", "name: nxw-fromfile\n" + COMPOSE)
        self.write("dotenv/.env", "COMPOSE_PROJECT_NAME=nxw-fromdotenv\n")
        self.write("cwdenv/.env", "COMPOSE_PROJECT_NAME=nxw-notthis\n")
        envfile = self.write("x.env", "COMPOSE_PROJECT_NAME=nxw-envfile\n")
        for args, cwd, kw, want in (
                ([], "proj", {}, "nxwp-proj"),
                ([], "proj/sub", {}, "nxwp-proj"),                         # compose walks up
                ([], "named", {}, "nxwp-nxw-fromfile"),
                ([], "dotenv", {}, "nxwp-nxw-fromdotenv"),                 # .env beats name:
                ([], "dotenv", {"COMPOSE_PROJECT_NAME": ""}, "nxwp-nxw-fromfile"),   # empty hides .env
                (["-f", "../proj/compose.yaml"], "cwdenv", {}, "nxwp-proj"),          # .env of the file's dir
                (["--project-directory=../named", "-f", "../proj/compose.yaml"], "cwdenv", {}, "nxwp-named"),
                (["--project-directory", "../dotenv", "-f", "../proj/compose.yaml"], "cwdenv", {},
                 "nxwp-nxw-fromdotenv"),                                  # and its .env
                (["--env-file", envfile], "named", {}, "nxwp-nxw-envfile"),
                ([], "proj/sub", {"COMPOSE_FILE": os.path.join(self.tmp, "named/compose.yaml")}, "nxwp-nxw-fromfile"),
                # COMPOSE_FILE from the environment: compose reads the working directory's .env (it could set
                # COMPOSE_FILE), unlike -f. Whatever compose picks, the shim follows.
                ([], "cwdenv", {"COMPOSE_FILE": os.path.join(self.tmp, "named/compose.yaml")}, "nxwp-nxw-notthis"),
                (["--project-name="], "proj", {}, "nxwp-proj"),
                (["-H", "unix:///nonexistent.sock", "--ansi", "never"], "proj", {}, "nxwp-proj")):
            self.assertEqual(self.name(args, cwd, **kw), want, (args, cwd, kw))

    def test_explicit_names_stay(self):
        self.assertEqual(self.name(["-p", "nxw-x"]), "nxw-x")
        self.assertEqual(self.name([], COMPOSE_PROJECT_NAME="nxw-env"), "nxw-env")

    def test_already_prefixed_stays(self):
        self.write("already/compose.yaml", "name: nxwp-already\n" + COMPOSE)
        self.assertEqual(self.name([], "already"), "nxwp-already")

    def test_the_probe_flags(self):
        # A missing env_file and an undefined network fail plain `config`, not the probe; `name:` is interpolated.
        path = self.write("lax/compose.yaml", "name: nxw-${NXW_SUFFIX:-dflt}\nservices:\n  s:\n"
                                              "    image: busybox:1.36.1-musl\n    env_file: missing.env\n"
                                              "    networks: [nope]\n")
        env = self.env(NXW_SUFFIX="abc")
        plain = subprocess.run([shim.REAL, "-f", path, "config", "--format", "json"], env=env,
                               stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.assertNotEqual(plain.returncode, 0)
        self.assertEqual(shim.probe(shim.REAL, ["-f", path], env), "nxw-abc")

    @unittest.skipUnless(shutil.which("docker"), "no docker CLI")
    def test_through_the_docker_cli(self):
        # The shim as the docker CLI's compose plugin (found first, in $DOCKER_CONFIG/cli-plugins); no daemon.
        plug = self.write("docker-config/cli-plugins/docker-compose",
                          "#!/bin/sh\nexec '%s' -I -S '%s' \"$@\"\n" % (sys.executable, SHIM))
        os.chmod(plug, 0o755)
        docker = {"cmd": ["docker"], "DOCKER_HOST": "tcp://127.0.0.1:1"}
        self.assertEqual(self.name(["compose"], **docker), "nxwp-proj")
        self.assertEqual(self.name(["--log-level", "warn", "compose", "-f", "compose.yaml"], **docker), "nxwp-proj")
        self.assertEqual(self.name(["compose", "-p", "nxw-x"], **docker), "nxw-x")
        r = subprocess.run(["docker", "compose", "version"], env=self.env(DOCKER_HOST="tcp://127.0.0.1:1"),
                           stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr.decode())
        self.assertIn("Docker Compose version", r.stdout.decode())


if __name__ == "__main__":
    unittest.main()
