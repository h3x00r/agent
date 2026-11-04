import os
import re
import unittest

import json

from agentctl.domain import flavour, notes, persona, roots, scratch
from agentctl.domain import telemetry as tele
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

    def test_ports(self):
        for v, want in (("20000-20099", (20000, 20099)), ("1024-1024", (1024, 1024)), ("1024-65535", (1024, 65535)),
                        ("65535-65535", (65535, 65535))):
            self.assertEqual(persona.port_range(v), want, v)
        for v in ("", "20000", "20000-", "-20099", "20099-20000", "1023-2000", "1000-2000", "20000-65536",
                  "020000-20099", "02000-20099", "20000 -20099", "20000-20099\n", "20000:20099", "２0000-20099",
                  "100000-100001"):
            self.assertIsNone(persona.port_range(v), repr(v))
        a, b, c = (Persona("a", "claude", "docker", "c", ports="20000-20099"),
                   Persona("b", "grok", "docker", "c", ports="20099-20150"), Persona("c", "grok", "docker", "c"))
        self.assertIs(persona.ports_overlap(b, [a, b, c]), a)          # one port in common is an overlap
        self.assertIsNone(persona.ports_overlap(c, [a, b, c]))         # none: nothing to overlap
        b.ports = "20100-20199"
        self.assertIsNone(persona.ports_overlap(b, [a, b, c]))
        self.assertIsNone(persona.ports_overlap(a, [a]))               # itself doesn't count

    def test_next_ports(self):
        def nxt(*ranges):
            return persona.next_ports([Persona("p%d" % i, "claude", "docker", "c", ports=r) for i, r in enumerate(ranges)])
        self.assertEqual(nxt(), "20000-20099")
        self.assertEqual(nxt(""), "20000-20099")                       # one without a range counts for nothing
        self.assertEqual(nxt("20000-20099"), "20100-20199")
        self.assertEqual(nxt("20100-20199", "20000-20099"), "20200-20299")          # the highest, in any order
        self.assertEqual(nxt("20000-20099", "20200-20299"), "20300-20399")          # a gap isn't handed on
        self.assertEqual(nxt("25050-25149"), "25200-25299")                         # by hand: the next whole block
        self.assertEqual(nxt("25050-25099"), "25100-25199")
        self.assertEqual(nxt("8000-8099"), "20000-20099")                           # below the base: from the base
        self.assertEqual(nxt("20000-20099", "65300-65399"), "65400-65499")          # the last whole block
        # Past 65535: the first free block from 20000, around what is there.
        self.assertEqual(nxt("20000-20099", "65400-65499"), "20100-20199")
        self.assertEqual(nxt("20050-20060", "20150-20250", "60000-65535"), "20300-20399")
        every = ["%d-%d" % (a, a + 99) for a in range(20000, 65500, 100)]
        self.assertEqual(nxt(*every), "")                                           # none free
        self.assertEqual(nxt(*[r for r in every if r != "43200-43299"]), "43200-43299")


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


class Telemetry(unittest.TestCase):
    def test_rules(self):
        for url in ("https://collector.lan:4318", "http://10.0.0.5:4318", "https://c.lan", "https://c.lan:4318/otlp"):
            self.assertTrue(tele.valid_endpoint(url), url)
        for url in ("collector.lan:4318", "grpc://c:4317", "https://", "https://c lan", "https://c.lan:4318,x",
                    "https://c.lan:4318?x=1", 'https://c"x'):
            self.assertFalse(tele.valid_endpoint(url), url)
        self.assertEqual(tele.norm_endpoint("https://c.lan:4318//"), "https://c.lan:4318")
        self.assertEqual(tele.norm_endpoint("https://"), "https://")
        for t in ("0a1b2c3d", "a.b_c~d+e/f=g-h", "x" * 4096):
            self.assertTrue(tele.valid_token(t))
        for t in ("", "a b", "a,b", "a%20b", "x" * 4097, 'a"b'):
            self.assertFalse(tele.valid_token(t), t)
        self.assertEqual(tele.PROTOCOL, "http/protobuf")
        for h, want in (("vm-a1", "vm-a1"), ("vm1", "vm1"), ("vm,persona=x", ""), ("a b", ""), ("-x", ""), ("", "")):
            self.assertEqual(tele.safe_host(h), want, h)

    def test_claude_dropin(self):
        env = json.loads(tele.claude_dropin("c1", "vm1", "https://c.lan:4318", "tok", True))["env"]
        self.assertEqual(env["OTEL_EXPORTER_OTLP_HEADERS"], "Authorization=Bearer tok")   # a space, not %20
        self.assertEqual(env["OTEL_RESOURCE_ATTRIBUTES"], "host.name=vm1,persona=c1")
        self.assertEqual(env["NODE_EXTRA_CA_CERTS"], tele.CA_TARGET)
        self.assertEqual((env["CLAUDE_CODE_ENABLE_TELEMETRY"], env["OTEL_EXPORTER_OTLP_PROTOCOL"]), ("1", "http/protobuf"))
        self.assertNotIn("OTEL_LOG_USER_PROMPTS", env)
        env = json.loads(tele.claude_dropin("c1", "", "https://c.lan:4318", "", False))["env"]
        self.assertEqual(env["OTEL_RESOURCE_ATTRIBUTES"], "persona=c1")
        self.assertNotIn("OTEL_EXPORTER_OTLP_HEADERS", env)
        self.assertNotIn("NODE_EXTRA_CA_CERTS", env)

    def test_grok(self):
        t = tele.grok_table("https://c.lan:4318", True)
        self.assertIn('\n[telemetry]\notel_enabled = true\n', t)
        self.assertIn('otel_endpoint = "https://c.lan:4318"\n', t)
        self.assertIn('otel_certificate = "%s"\n' % tele.CA_TARGET, t)
        self.assertNotIn("otel_certificate", tele.grok_table("https://c.lan:4318", False))
        e = tele.grok_env("g1", "vm1", "tok")
        for v in ("OTEL_EXPORTER_OTLP_METRICS_HEADERS", "OTEL_EXPORTER_OTLP_LOGS_HEADERS"):
            self.assertIn("\n%s='Authorization=Bearer tok,X-Nexus-Persona=g1,X-Nexus-Host=vm1'\n" % v, e)
        self.assertNotIn("\nOTEL_EXPORTER_OTLP_HEADERS=", e)
        self.assertIn("\nOTEL_EXPORTER_OTLP_LOGS_HEADERS='X-Nexus-Persona=g1'\n", tele.grok_env("g1", "", ""))

    def test_details(self):
        off = tele.claude_dropin("c1", "vm1", "https://c.lan:4318", "tok", True)
        self.assertNotIn("OTEL_LOG_TOOL", off)
        on = tele.claude_dropin("c1", "vm1", "https://c.lan:4318", "tok", True, (tele.DETAILS,))
        self.assertEqual(json.loads(on)["env"]["OTEL_LOG_TOOL_DETAILS"], "1")
        self.assertEqual([l for l in on.split("\n") if "TOOL_DETAILS" not in l], off.split("\n"))   # nothing else
        both = tele.claude_dropin("c1", "vm1", "https://c.lan:4318", "tok", True, (tele.CONTENT, tele.DETAILS))
        keys = list(json.loads(both)["env"])
        self.assertEqual(keys[keys.index("CLAUDE_CODE_ENHANCED_TELEMETRY_BETA") + 1:][:2],
                         ["OTEL_LOG_TOOL_DETAILS", "OTEL_LOG_TOOL_CONTENT"])      # in EXTRAS order, whatever was given
        self.assertNotIn("TOOL_DETAILS", tele.claude_dropin("c1", "", "https://c.lan:4318", "", False, (tele.CONTENT,)))
        self.assertNotIn("otel_log_tool", tele.grok_table("https://c.lan:4318", True))
        t = tele.grok_table("https://c.lan:4318", True, (tele.DETAILS,))
        self.assertTrue(t.endswith('otel_certificate = "%s"\notel_log_tool_details = true\n' % tele.CA_TARGET))
        self.assertTrue(tele.grok_table("https://c.lan:4318", False, (tele.CONTENT, tele.DETAILS))
                        .endswith("\notel_log_tool_details = true\notel_log_tool_content = true\n"))
        self.assertEqual([(x.field, x.env, x.flag) for x in tele.EXTRAS],
                         [("tool_details", "TELEMETRY_TOOL_DETAILS", "--tool-details"),
                          ("tool_content", "TELEMETRY_TOOL_CONTENT", "--tool-content")])
        sends, own = Persona("c1", "claude", "docker", "colour1", telemetry=True), \
            Persona("c2", "claude", "docker", "colour1", telemetry=True, tool_details=True)
        quiet = Persona("g1", "grok", "docker", "colour1", tool_details=True, tool_content=True)
        for x, shared, want in ((tele.DETAILS, False, [False, True, False]), (tele.DETAILS, True, [True, True, False]),
                                (tele.CONTENT, False, [False, False, False]), (tele.CONTENT, True, [True, True, False])):
            self.assertEqual([tele.extra_on(p, x, shared) for p in (sends, own, quiet)], want, (x.key, shared))

    def test_ca_source(self):
        self.assertEqual(tele.ca_source(Persona("c1", "claude", "ddev", "colour1"), True), tele.DDEV_BUNDLE)
        self.assertEqual(tele.ca_source(Persona("g1", "grok", "ddev", "colour1"), True), tele.CA_FILE)
        self.assertEqual(tele.ca_source(Persona("c1", "claude", "docker", "colour1"), True), tele.CA_FILE)
        self.assertEqual(tele.ca_source(Persona("c1", "claude", "ddev", "colour1"), False), "")

    def test_bundle(self):
        self.assertEqual(tele.bundle(b"CA", b"MK\n"), b"CA\nMK\n")
        self.assertEqual(tele.bundle(b"CA\n", b""), b"CA\n")


class Scratch(unittest.TestCase):
    def test_root(self):
        self.assertEqual(scratch.root("", ""), "/srv/nexus-scratch")
        self.assertEqual(scratch.root("/sh", "/dot"), "/sh")              # the shell's, as with PROJECTS_ROOT
        self.assertEqual(scratch.root("", "/dot/"), "/dot")               # an empty one is unset; one / goes
        self.assertEqual(scratch.root("//", ""), "/")                     # and the rules then refuse it
        # Cleaned as the daemon cleans a mount's destination, or a container made with it never shows the mount.
        for r in ("/srv//x", "//srv/x", "/srv/./x", "/srv/x//", "/srv/y/../x", "///srv/x/"):
            self.assertEqual(scratch.root("", r), "/srv/x", r)
        self.assertEqual(scratch.root("rel/x/", ""), "rel/x")              # not absolute: as it is, for root_ok
        self.assertEqual(scratch.path("/srv/nexus-scratch", "c1"), "/srv/nexus-scratch/c1")
        self.assertEqual(scratch.COMPOSE, "${NEXUS_SCRATCH_ROOT:-/srv/nexus-scratch}")
        self.assertEqual(scratch.install_cmd("/srv/s"), "sudo install -d -o $(id -u) -g $(id -g) -m 755 /srv/s")
        # Pasted into a shell: quoted, as root_ok lets $ ; & through.
        self.assertTrue(scratch.root_ok("/srv/$U;x&y"))
        self.assertEqual(scratch.install_cmd("/srv/$U;x&y"),
                         "sudo install -d -o $(id -u) -g $(id -g) -m 755 /srv/\\$U\\;x\\&y")

    def test_root_ok(self):
        for r in ("/srv/nexus-scratch", "/s", "//x"):
            self.assertTrue(scratch.root_ok(r), r)
        for r in ("", "/", "rel/x", "/a:b", "/a b", "/a\tb", "/a\n"):
            self.assertFalse(scratch.root_ok(r), repr(r))


class Notes(unittest.TestCase):
    def test_render(self):
        t = "{{persona}} {{cli}} {{projects_root}} {{scratch}} {{ports}} {{guard}} {{persona}}"
        self.assertEqual(notes.render(t, "c1", "Claude Code", "/p", "/s/c1", "20000-20099", "on"),
                         "c1 Claude Code /p /s/c1 20000-20099 on c1")
        self.assertIn(" none assigned ", notes.render(t, "c1", "x", "/p", "/s", "", "off"))
        # A value is no placeholder: replaced once, never read again.
        self.assertEqual(notes.render("{{projects_root}}", "c1", "x", "/p/{{guard}}", "/s", "", "on"), "/p/{{guard}}")
        for bad, named in (("{{persona}} {{port}}", "{{port}}"), ("{{ persona }}", "{{ persona }}"),
                           ("{{}} {{x-y}}", "{{x-y}}, {{}}")):
            with self.assertRaises(notes.BadTemplate) as e:
                notes.render(bad, "c1", "x", "/p", "/s", "", "on")
            self.assertIn("has " + named + ",", str(e.exception))
        # The template as committed: every placeholder known, every one filled.
        with open(os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                               notes.TEMPLATE), encoding="utf-8") as f:
            text = f.read()
        out = notes.render(text, "g1", notes.CLI["grok"], "/p", "/s/g1", "20100-20199", "on")
        self.assertNotIn("{{", out)
        self.assertEqual(sorted(set(re.findall(r"\{\{(\w+)\}\}", text))), sorted(notes.KNOWN))
        self.assertEqual(notes.file("c1"), "personas/c1.notes.md")

    def test_guard(self):
        for shell, dot, want in (("", "", "on"), ("", "off", "off"), ("on", "off", "on"), ("off", "on", "off"),
                                 ("", "on", "on"), ("OFF", "", "on"), ("no", "", "on")):
            self.assertEqual(notes.guard(shell, dot), want, (shell, dot))
