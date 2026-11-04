import os
import tempfile
import unittest

from agentctl.core.errors import NexusError
from agentctl.domain.persona import Persona
from agentctl.nexus import update

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def persona(name, flavour="claude", **kw):
    return Persona(name=name, flavour=flavour, context="docker", colour="colour1", **kw)


class Plan(unittest.TestCase):
    def test_image_inputs_are_the_dockerfiles(self):
        with open(os.path.join(REPO, "Dockerfile")) as f:
            self.assertEqual(update.image_inputs(f.read()),
                             {"Dockerfile": "", "compose.yml": "", "tmux.conf": "", "entrypoint.sh": "",
                              "claude-mode.sh": "claude", "image/docker-guard.py": "",
                              "image/docker-compose-persona.py": "", "image/nexus-apm": "",
                              "image/glab-credential": ""})

    def test_image_inputs_by_stage(self):
        d = ("FROM --platform=linux/amd64 debian AS base\nCOPY ./a.sh b.conf /x/\nCOPY --from=base /x /y\n"
             "FROM base AS grok\nADD --chmod=0755 g.sh /g\nFROM base\nCOPY c.sh /c\n")
        self.assertEqual(update.image_inputs(d), {"Dockerfile": "", "compose.yml": "", "a.sh": "", "b.conf": "",
                                                  "g.sh": "grok", "c.sh": ""})

    def test_read_at_start(self):
        c, g = persona("c1"), persona("g1", "grok", statusline=False, ssh="none")
        ask, key = persona("c2", permissions="ask"), persona("c3", ssh="personas/c3.key")
        for path, who in (("compose.yml", "c1 g1 c2 c3"), ("personas/claude.managed.json", "c1 c2 c3"),
                          ("personas/claude.ask.json", "c2"), ("personas/grok.toml", "g1"),
                          ("statusline/claude/settings.json", "c1 c2 c3"), ("statusline/grok/settings.toml", ""),
                          ("statusline/claude/statusline.sh", ""), ("ssh/none.conf", "g1"), ("ssh/key.conf", "c3"),
                          ("personas/claude.env.example", ""), ("agentctl/nexus/cli.py", ""), ("Dockerfile", ""),
                          # the notes: written in place by the config run, read at the next session
                          ("personas/notes.md", ""), ("personas/c1.notes.md", "")):
            self.assertEqual(" ".join(p.name for p in (c, g, ask, key) if update.read_at_start(path, p)), who, path)

    def test_plan(self):
        c, g, k = persona("c1"), persona("g1", "grok"), persona("c2")
        inputs = {"Dockerfile": "", "compose.yml": "", "claude-mode.sh": "claude"}
        self.assertEqual(update.plan(["README.md", "agentctl/x.py"], [c, g], [], inputs), [])
        self.assertEqual(update.plan(["README.md"], [c, g, k], ["c2"], inputs), [(k, [], ["its service"])])
        self.assertEqual(update.plan(["claude-mode.sh", "personas/grok.toml"], [c, g], [], inputs),
                         [(c, ["claude-mode.sh"], ["its image"]), (g, [], ["personas/grok.toml"])])
        # compose.yml is an image input and read at start: the image says it once
        self.assertEqual(update.plan(["compose.yml", "personas/claude.json"], [c], ["c1"], inputs),
                         [(c, ["compose.yml"], ["its service", "its image", "personas/claude.json"])])


class Record(unittest.TestCase):
    def test_merged_joins_what_is_still_to_do(self):
        c, g = persona("c1"), persona("g1", "grok")
        rec = {"rev": "x", "build": {"agent-claude-docker": ["Dockerfile"]},
               "restart": {"g1": {"why": ["personas/grok.toml"], "at": 100}}}
        build, restart = update.merged(rec, [(c, ["Dockerfile", "tmux.conf"], ["its image"]),
                                             (g, [], ["personas/grok.toml"])], 200)
        self.assertEqual(build, {"agent-claude-docker": ["Dockerfile", "tmux.conf"]})
        # c1's only reason is its image: Docker says whether that is due, so no restart is kept for it; g1's
        # reason was asked for already, so its "at" stays
        self.assertEqual(restart, {"g1": {"why": ["personas/grok.toml"], "at": 100}})
        self.assertEqual(rec["build"], {"agent-claude-docker": ["Dockerfile"]})     # the record itself untouched

    def test_a_new_reason_moves_at(self):
        g = persona("g1", "grok")
        rec = {"restart": {"g1": {"why": ["personas/grok.toml"], "at": 100}}}
        _, restart = update.merged(rec, [(g, [], ["its service", "personas/grok.toml"])], 200)
        self.assertEqual(restart, {"g1": {"why": ["personas/grok.toml", "its service"], "at": 200}})

    def test_load_and_save(self):
        d = tempfile.mkdtemp()
        self.assertEqual(update.load_record(d), {})
        rec = {"rev": "abc", "build": {"agent-grok-docker": ["Dockerfile"]}, "restart": {"g1": {"why": ["x"], "at": 1}}}
        update.save_record(d, rec)
        self.assertEqual(update.load_record(d), rec)
        self.assertEqual(oct(os.stat(os.path.join(d, "update.json")).st_mode & 0o777), "0o644")
        self.assertEqual(os.listdir(d), ["update.json"])                             # no temp file left
        for bad in ("[]", "{", '{"restart": {"g1": {"why": "x", "at": 1}}}', '{"build": {"i": "Dockerfile"}}',
                    '{"rev": 3}'):
            with open(os.path.join(d, "update.json"), "w") as f:
                f.write(bad)
            self.assertEqual(update.load_record(d), {}, bad)


class Args(unittest.TestCase):
    def test_refused_before_anything_is_read(self):
        for argv, msg in ((["--bogus"], "--update: unknown option '--bogus'"), (["--since"], "--since needs a commit"),
                          (["--since", ""], "--since needs a commit")):
            with self.assertRaises(NexusError) as e:
                update.main("/nonexistent", argv, None)
            self.assertIn(msg, e.exception.message)


class Offer(unittest.TestCase):
    def offer(self, d, build=None, behind=False):
        """Apply.offer() under -y for persona c1, whose service changed (and whose image is due a build with BUILD):
        rc, what it said, the commands it ran."""
        from unittest import mock
        said, ran = [], []
        out = type("Out", (), {"say": lambda self, m="": said.append(m)})()
        p = type("P", (), {"name": "c1", "container": "agent-c1", "ssh": "", "flavour": "claude", "context": "docker"})()
        ap = update.Apply(d, "HEAD", True, out)
        ap.head = "f" * 40
        ap.rec = {"build": dict(build or {}), "restart": {"c1": {"at": 200, "why": ["its service changed"]}}}
        ctx = type("Ctx", (), {"scratch_root": os.path.join(d, "scratch")})()
        with mock.patch.object(update, "started", return_value=100), \
                mock.patch.object(update, "image", return_value="agent-claude-docker"), \
                mock.patch.object(update, "image_behind", return_value=behind), \
                mock.patch.object(update, "sessions", return_value=[]), \
                mock.patch.object(update, "save_record"), \
                mock.patch.object(update, "Context", return_value=ctx), \
                mock.patch.object(update, "is_socket", return_value=True), \
                mock.patch.object(update.proc, "run", side_effect=lambda argv, **kw: ran.append(argv) or proc_ok()):
            rc = ap.offer([p], "build", False)
        return rc, "\n".join(said), ran

    def test_scratch_root_missing(self):
        # A restart is due, but the shared scratch root isn't there (or is a file, or isn't yours with c1's dir still to
        # make): the launcher's preflight would refuse it, so it is answered no (under -y too), with what fixes it, and
        # kept for the next run. A root you can't write in that has c1's dir already is no reason.
        with tempfile.TemporaryDirectory() as d:
            root = os.path.join(d, "scratch")
            rc, text, ran = self.offer(d)
            self.assertEqual((rc, ran), (0, []))
            self.assertIn("nexus: but restarting c1 needs the shared scratch root %s, which isn't there: "
                          "sudo install -d -o $(id -u) -g $(id -g) -m 755 %s; or point NEXUS_SCRATCH_ROOT (.env, or your "
                          "shell) at a directory of your own" % (root, root), text)
            self.assertIn("nexus: not applied. It's kept", text)
            self.assertIn("  nexus c1 restart", text)
            with open(root, "w"):
                pass
            rc, text, ran = self.offer(d)
            self.assertEqual((rc, ran), (0, []))
            self.assertIn("nexus: but restarting c1 needs the shared scratch root %s, which isn't a directory: move it "
                          "aside" % root, text)
            self.assertNotIn("install -d", text)
            os.unlink(root)
            if os.geteuid() != 0:
                os.mkdir(root, 0o555)
                try:
                    rc, text, ran = self.offer(d)
                    self.assertEqual((rc, ran), (0, []))
                    self.assertIn("nexus: but restarting c1 needs the shared scratch root %s, which you can't write in, "
                                  "to make a persona's dir: sudo install -d -o $(id -u) -g $(id -g) -m 755 %s, or point "
                                  % (root, root), text)
                    os.chmod(root, 0o755)
                    os.mkdir(os.path.join(root, "c1"))
                    os.chmod(root, 0o555)
                    rc, text, ran = self.offer(d)
                    self.assertEqual(ran, [[os.path.join(d, "nexus"), "c1", "restart"]])
                    self.assertNotIn("scratch root", text)
                finally:
                    os.chmod(root, 0o755)
            os.makedirs(root, exist_ok=True)
            rc, text, ran = self.offer(d)
            self.assertEqual(ran, [[os.path.join(d, "nexus"), "c1", "restart"]])
            self.assertNotIn("scratch root", text)

    def test_builds_go_on(self):
        # What holds the restarts back doesn't hold the builds: they need neither the scratch root nor an ssh-agent.
        # Built (under -y), then the restarts kept, listed one a step; no build left in the list.
        with tempfile.TemporaryDirectory() as d:
            rc, text, ran = self.offer(d, build={"agent-claude-docker": ["Dockerfile"]}, behind=True)
            self.assertEqual((rc, ran), (0, [[os.path.join(d, "nexus"), "c1", "build"]]))
            self.assertIn("nexus: but restarting c1 needs the shared scratch root", text)
            self.assertIn("-- build agent-claude-docker, for c1", text)
            self.assertIn("nexus: no persona restarted. Its restart is kept: the next nexus --update offers it again.", text)
            self.assertIn("  nexus c1 restart", text)
            self.assertNotIn("  nexus c1 build", text)
            self.assertNotIn("nexus: applied", text)


def proc_ok():
    return type("R", (), {"ok": True, "rc": 0, "out": "", "err": ""})()


if __name__ == "__main__":
    unittest.main()
