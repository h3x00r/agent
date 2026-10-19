import os
import unittest

from agentctl.domain import flavour, persona, roots
from agentctl.domain.persona import Persona


class Rules(unittest.TestCase):
    def test_names(self):
        for n in ("c1", "claude-docker", "a" * 31):
            self.assertTrue(persona.valid_name(n), n)
        for n in ("", "1a", "A", "a-", "a" * 32, "ls", "top", "all", "start", "a_b", "c1\n"):
            self.assertFalse(persona.valid_name(n), repr(n))

    def test_norm_dir(self):
        self.assertEqual(persona.norm_dir("~", "/h"), "/h")
        self.assertEqual(persona.norm_dir("~/p/", "/h"), "/h/p")
        self.assertEqual(persona.norm_dir("/", "/h"), "/")
        self.assertEqual(persona.norm_dir("/x//", "/h"), "/x/")
        self.assertEqual(persona.norm_dir("~u/x", "/h"), "~u/x")

    def test_roots_and_keys(self):
        self.assertTrue(persona.root_ok("//"))
        self.assertFalse(persona.root_ok("/"))
        self.assertFalse(persona.root_ok("/a:b"))
        self.assertTrue(persona.root_ok("/a b"))
        self.assertFalse(persona.persona_root_ok("/a b"))
        self.assertTrue(persona.key_path_ok("g1", "personas/g1.key"))
        self.assertFalse(persona.key_path_ok("g1", "personas/x.key"))
        self.assertFalse(persona.key_path_ok("g1", "/k/$x"))
        self.assertFalse(persona.key_path_ok("g1", "rel/key"))

    def test_registry_and_version(self):
        for r in ("local", "registry.lan", "registry.lan:5000", "registry.lan:5000/team", "a/b_c/d--e"):
            self.assertTrue(persona.valid_registry(r), r)
        for r in ("", "-x", "reg:port", "reg/Team", "reg/"):
            self.assertFalse(persona.valid_registry(r), r)
        for v in ("latest", "stable", "2.1.302", "2.1.302-beta.1"):
            self.assertTrue(persona.valid_claude_version(v), v)
        for v in ("2.1", "v2.1.3", "2.1.3-", "2.1.3 x"):
            self.assertFalse(persona.valid_claude_version(v), v)

    def test_colours_and_names(self):
        ps = [Persona("a", "claude", "docker", "colour25"), Persona("b", "grok", "docker", "colour90")]
        self.assertEqual(persona.next_colour(ps), "colour28")
        full = [Persona("p%d" % i, "claude", "docker", c) for i, c in enumerate(persona.COLOURS)]
        self.assertEqual(persona.next_colour(full), persona.COLOURS[0])
        self.assertEqual(persona.suggest_name([Persona("claude-docker", "claude", "docker", "c")], "claude", "docker"),
                         "claude-docker-2")


class Lines(unittest.TestCase):
    def test_a_line_as_config_sh_wrote_it(self):
        # What the move into personas.json reads.
        line = "g1 flavour=grok context=docker colour=colour28 statusline=yes root=/srv/p ssh=personas/g1.key"
        self.assertEqual(persona.parse_meta_line(line),
                         Persona("g1", "grok", "docker", "colour28", True, "/srv/p", "personas/g1.key"))

    def test_reading_rules(self):
        p = persona.parse_meta_line("  c1  flavour=x flavour=claude context=docker colour=colour1 statusline=no odd=1 ")
        self.assertEqual((p.flavour, p.statusline), ("claude", False))
        for bad in ("c1 flavour=claude context=docker colour=colour1",
                    "c1 flavour=claude context=k8s colour=colour1 statusline=yes",
                    "C1 flavour=claude context=docker colour=colour1 statusline=yes",
                    "c1 flavour=claude context=docker colour=colour1 statusline=yes root=rel",
                    "c1 flavour=claude context=docker colour=colour1 statusline=yes ssh=rel/key"):
            with self.assertRaises(persona.BadLine, msg=bad):
                persona.parse_meta_line(bad)

    def test_key_file(self):
        self.assertEqual(persona.key_file(Persona("g", "grok", "docker", "c", ssh="personas/g.key"), "/a"),
                         "/a/personas/g.key")
        self.assertEqual(persona.key_file(Persona("g", "grok", "docker", "c", ssh="none"), "/a"), "")


class Flavours(unittest.TestCase):
    def test_headless(self):
        c, g = flavour.get("claude"), flavour.get("grok")
        self.assertEqual(c.headless_argv("hi", False, ["-v"]), (["claude", "-p", "-v", "--", "hi"], ""))
        self.assertEqual(c.headless_argv("", True, []), (["claude", "-p"], ""))
        self.assertEqual(g.headless_argv("hi", False, ["-v"]), (["grok", "--single=hi", "-v"], ""))
        self.assertEqual(g.headless_argv("hi", True, []), (["grok", "--prompt-file", "/dev/stdin"], "hi"))
        self.assertIsNone(flavour.get("gpt"))

    def test_pins(self):
        self.assertEqual(flavour.get("claude").image_tag, "${CLAUDE_TAG:-${CLAUDE_VERSION:-latest}}")
        self.assertEqual(flavour.get("grok").newest_url("alpha"), "https://x.ai/cli/alpha")


class Places(unittest.TestCase):
    """roots.place against a pretend filesystem: /link is a symlink to /p/app."""

    @staticmethod
    def real(p):
        table = {"/p": "/p", "/p/app": "/p/app", "/link": "/p/app", "/old": "/old", "/old/billing": "/old/billing",
                 "/oldlink": "/old", "/elsewhere": "/elsewhere", "/new": "/new", "/new/x": "/new/x"}
        if p in table:
            return table[p]
        if p.startswith("/link/"):
            return "/p/app" + p[5:]
        return None

    def test_in_the_root(self):
        self.assertEqual(roots.place("/p/app", "/p", "", self.real), roots.Placement("/p/app"))

    def test_by_a_symlink_gets_the_roots_spelling(self):
        self.assertEqual(roots.place("/link", "/p", "", self.real).dir, "/p/app")

    def test_outside_starts_in_the_root(self):
        self.assertEqual(roots.place("/elsewhere", "/p", "", self.real), roots.Placement("/p", outside_root=True))
        self.assertTrue(roots.place("/elsewhere", "/p", "", self.real, headless=True).refused)

    def test_drift_in_the_old_root(self):
        self.assertEqual(roots.place("/old/billing", "/new", "/old", self.real), roots.Placement("/old/billing"))
        self.assertEqual(roots.place("/oldlink", "/new", "/old", self.real).dir, "/old")

    def test_drift_outside_the_old_root(self):
        p = roots.place("/new/x", "/new", "/old", self.real)
        self.assertEqual((p.dir, p.inside, p.outside_root), ("/new/x", False, False))
        p = roots.place("/elsewhere", "/new", "/old", self.real)
        self.assertEqual((p.dir, p.inside, p.outside_root), ("/new", False, True))
        self.assertTrue(roots.place("/new/x", "/new", "/old", self.real, headless=True).refused)

    def test_moved_text(self):
        self.assertEqual(roots.moved("c1", "/old", "/new", "which /x isn't in"),
                         "c1 still runs with the projects root it was created with, /old, which /x isn't in: "
                         "nexus c1 restart applies /new, and ends its sessions")


if __name__ == "__main__":
    unittest.main()
