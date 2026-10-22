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
                              "claude-mode.sh": "claude"})

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
                          ("personas/claude.env.example", ""), ("agentctl/nexus/cli.py", ""), ("Dockerfile", "")):
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


if __name__ == "__main__":
    unittest.main()
