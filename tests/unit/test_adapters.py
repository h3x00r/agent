import http.server
import os
import shutil
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

from agentctl.adapters import compose_render
from agentctl.core import lock, proc
from agentctl.core.errors import NexusError
from agentctl.domain.persona import Persona

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
GOLDEN = os.path.join(REPO, "tests", "golden", "cases", "config", "cli-yes-first-setup", "tree.txt")


def golden_file(path, name):
    """A file's content as a golden case's tree.txt shows it."""
    out, on = [], False
    with open(path) as f:
        for line in f:
            if line.startswith(("+ ", "~ ", "- ")):
                on = line.split()[1] == name
                continue
            if on and line.startswith("    | "):
                out.append(line[6:])
    return "".join(out)


class Render(unittest.TestCase):
    @unittest.skipUnless(os.path.exists(GOLDEN), "the golden case isn't recorded")
    def test_as_the_goldens_have_it(self):
        # The services byte for byte as the bash wrote them (the cases were recorded from it), under the
        # header that says personas.json is where they come from; with the port ranges nexus --config gave them.
        ps = [Persona("c1", "claude", "docker", "colour25", True, ports="20000-20099"),
              Persona("g1", "grok", "docker", "colour28", True, ssh="personas/g1.key", ports="20100-20199")]
        self.assertEqual(compose_render.render(ps, managed=True), golden_file(GOLDEN, "a/compose.personas.yml"))

    def test_shapes(self):
        ps = [Persona("n1", "grok", "ddev", "colour90", False, ssh="none"),
              Persona("k1", "claude", "docker", "colour25", False, ssh="/keys/k")]
        out = compose_render.render(ps, managed=False, env_files={"n1": "personas/n1.env.AbC123"})
        self.assertIn("service: _agent-ddev }", out)
        self.assertIn("env_file: personas/n1.env.AbC123\n", out)
        self.assertNotIn("/etc/grok/statusline", out)
        self.assertIn("source: ./personas/n1.grok.toml,", out)
        self.assertIn("50-nexus-none.conf", out)
        self.assertIn("        source: '/keys/k'\n", out)
        self.assertNotIn("15-personas.json", out)
        self.assertIn('environment: { AGENT_PERSONA: "k1", AGENT_COLOUR: colour25, SSH_AUTH_SOCK: "", '
                      'NEXUS_SCRATCH: "${NEXUS_SCRATCH_ROOT:-/srv/nexus-scratch}/k1" }', out)
        self.assertTrue(out.endswith('volumes:\n  "n1-home":\n  "k1-home":\n'))

    def test_scratch_and_notes(self):
        """Every persona: its scratch dir by the root compose interpolates (never a path: .env doesn't change the
        bytes), at the same path inside, and the notes for its CLI; both without create_host_path."""
        ps = [Persona("c1", "claude", "docker", "colour25", ssh="none"), Persona("g1", "grok", "ddev", "colour28"),
              Persona("c2", "claude", "ddev", "colour90", False, permissions="ask", telemetry=True)]
        out = compose_render.render(ps, managed=True, ca=True)
        c1 = out[out.index('  "c1":'):out.index('  "g1":')]
        g1 = out[out.index('  "g1":'):out.index('  "c2":')]
        c2 = out[out.index('  "c2":'):out.index("\nvolumes:")]
        for name, svc in (("c1", c1), ("g1", g1), ("c2", c2)):
            d = "${NEXUS_SCRATCH_ROOT:-/srv/nexus-scratch}/" + name
            self.assertIn(', NEXUS_SCRATCH: "%s" }\n' % d, svc)
            self.assertIn('      - %s-home:/home/dev\n      # The shared scratch dir' % name, svc)
            self.assertIn('      - { type: bind, source: "%s", target: "%s", bind: { create_host_path: false } }\n'
                          % (d, d), svc)
            self.assertEqual(svc.count(".notes.md"), 1)
        for name, svc, target in (("c1", c1, "/etc/claude-code/CLAUDE.md"), ("c2", c2, "/etc/claude-code/CLAUDE.md"),
                                  ("g1", g1, "/etc/grok/rules/00-nexus.md")):
            self.assertIn("      - { type: bind, source: ./personas/%s.notes.md, target: %s, read_only: true, "
                          "bind: { create_host_path: false } }\n" % (name, target), svc)
        # after the flavour's own settings, before telemetry and ssh
        self.assertLess(g1.index("managed_config.toml"), g1.index("00-nexus.md"))
        self.assertLess(c2.index("16-ask.json"), c2.index("CLAUDE.md"))
        self.assertLess(c2.index("CLAUDE.md"), c2.index("50-telemetry.json"))
        self.assertLess(c1.index("CLAUDE.md"), c1.index("50-nexus-none.conf"))
        self.assertNotIn("/srv/nexus-scratch/", out.replace("${NEXUS_SCRATCH_ROOT:-/srv/nexus-scratch}/", ""))

    def test_ports(self):
        """NEXUS_PORTS for a persona with a range, after NEXUS_SCRATCH; none without one. older=True: the service as
        written before the scratch dir, the range and the notes, the rest of it the same."""
        ps = [Persona("c1", "claude", "docker", "colour25", ports="20000-20099"),
              Persona("g1", "grok", "ddev", "colour28", ssh="none", telemetry=True, ports="20100-20199"),
              Persona("c2", "claude", "docker", "colour90")]
        out = compose_render.render(ps, managed=True, ca=True)
        self.assertIn('environment: { AGENT_PERSONA: "c1", AGENT_COLOUR: colour25, '
                      'NEXUS_SCRATCH: "${NEXUS_SCRATCH_ROOT:-/srv/nexus-scratch}/c1", NEXUS_PORTS: "20000-20099" }\n', out)
        self.assertIn('SSH_AUTH_SOCK: "", NEXUS_SCRATCH: "${NEXUS_SCRATCH_ROOT:-/srv/nexus-scratch}/g1", '
                      'NEXUS_PORTS: "20100-20199" }\n', out)
        self.assertIn('NEXUS_SCRATCH: "${NEXUS_SCRATCH_ROOT:-/srv/nexus-scratch}/c2" }\n', out)
        self.assertEqual(out.count("NEXUS_PORTS"), 2)
        for p in ps:
            for m in (True, False):
                for ca in (True, False):
                    now, then = compose_render.service(p, m, ca), compose_render.service(p, m, ca, older=True)
                    for gone in ("NEXUS_SCRATCH", "NEXUS_PORTS", "notes.md", "scratch dir", "Nexus notes"):
                        self.assertNotIn(gone, then)
                    d = "${NEXUS_SCRATCH_ROOT:-/srv/nexus-scratch}/" + p.name
                    notes = compose_render.NOTES_CLAUDE if p.flavour == "claude" else compose_render.NOTES_GROK
                    adds = [', NEXUS_SCRATCH: "%s"' % d, compose_render.SCRATCH.replace("{dir}", d),
                            notes.replace("{notes}", "personas/%s.notes.md" % p.name)]
                    for new in adds + ([', NEXUS_PORTS: "%s"' % p.ports] if p.ports else []):
                        self.assertEqual(now.count(new), 1, new)
                        now = now.replace(new, "")
                    self.assertEqual(now, then)

    def test_ask_problem(self):
        from agentctl.adapters import checks
        d = tempfile.mkdtemp()
        try:
            f = os.path.join(d, "ask.json")
            self.assertEqual(checks.ask_problem(f)[0], "isn't there")
            for text, why in (('{"permissions": {"defaultMode": "default"}}', ""),
                              ('{"permissions": {"defaultMode": "acceptEdits"}}', ""),
                              ('{"permissions": {"defaultMode": "bypassPermissions"}}', "to bypassPermissions"),
                              ('{"permissions": {}}', "sets no permissions.defaultMode"),
                              ('[1]', "sets no permissions.defaultMode"),
                              ('{"permissions": {"defaultMode": 1}}', "sets no permissions.defaultMode"),
                              ('{nope', "isn't JSON")):
                with open(f, "w") as fh:
                    fh.write(text)
                got = checks.ask_problem(f)[0]
                self.assertTrue(why in got if why else got == "", (text, got))
        finally:
            shutil.rmtree(d)

    def test_ask(self):
        """A claude persona that asks gets claude.ask.json after claude.managed.json, whether or not that is there;
        a grok one never does."""
        ps = [Persona("c1", "claude", "docker", "colour25", permissions="ask"), Persona("c2", "claude", "docker", "colour28"),
              Persona("g1", "grok", "docker", "colour90", permissions="ask")]
        for managed in (True, False):
            out = compose_render.render(ps, managed=managed)
            self.assertEqual(out.count("16-ask.json"), 1)
            c1 = out[out.index('  "c1":'):out.index('  "c2":')]
            self.assertIn("source: ./personas/claude.ask.json, target: /etc/claude-code/managed-settings.d/16-ask.json", c1)
            if managed:
                self.assertLess(c1.index("15-personas.json"), c1.index("16-ask.json"))

    def test_telemetry(self):
        """Only a persona that sends gets anything new: claude its drop-in, grok its second env file; each the CA
        when the collector has one (a ddev claude one the bundle). Every other service is as it was."""
        off = [Persona("c1", "claude", "ddev", "colour25"), Persona("g1", "grok", "docker", "colour28", ssh="none")]
        on = [Persona("c1", "claude", "ddev", "colour25", telemetry=True),
              Persona("g1", "grok", "docker", "colour28", ssh="none", telemetry=True),
              Persona("c3", "claude", "docker", "colour90", telemetry=True), Persona("c4", "claude", "docker", "colour1")]
        for ca in (True, False):
            self.assertEqual(compose_render.render(off, managed=True, ca=ca), compose_render.render(off, managed=True))
            self.assertEqual(compose_render.service(on[3], True, ca), compose_render.service(on[3], True))
            out = compose_render.render(on, managed=True, ca=ca, tele_files={"g1": "personas/g1.telemetry.env.X1"})
            c1 = out[out.index('  "c1":'):out.index('  "g1":')]
            g1 = out[out.index('  "g1":'):out.index('  "c3":')]
            c3 = out[out.index('  "c3":'):out.index('  "c4":')]
            self.assertIn("source: ./personas/c1.telemetry.json, target: /etc/claude-code/managed-settings.d/50-telemetry.json", c1)
            self.assertIn("env_file: [personas/g1.env, personas/g1.telemetry.env.X1]\n", g1)
            self.assertNotIn("telemetry.json", g1)
            self.assertEqual("source: ./personas/telemetry-ca.ddev.pem, target: /etc/nexus/telemetry-ca.pem" in c1, ca)
            self.assertEqual("source: ./personas/telemetry-ca.pem, target: /etc/nexus/telemetry-ca.pem" in g1, ca)
            self.assertEqual("source: ./personas/telemetry-ca.pem, target: /etc/nexus/telemetry-ca.pem" in c3, ca)
            self.assertLess(g1.index("grok.toml"), g1.index("50-nexus-none.conf"))
        self.assertIn("env_file: [personas/g1.env, personas/g1.telemetry.env]\n", compose_render.render(on, managed=False))

    def test_legacy_is_what_config_tools_wrote(self):
        # The file as config-tools wrote it, before it was `nexus --config`: only its comments differ.
        ps = [Persona("c1", "claude", "docker", "colour1"), Persona("g1", "grok", "docker", "colour25")]
        now, then = compose_render.render(ps, managed=True), compose_render.render(ps, managed=True, legacy=True)
        self.assertTrue(then.startswith("# compose.personas.yml — generated by ./config-tools from personas.json"))
        self.assertIn("      # grok's managed config, written by config-tools: personas/grok.toml", then)
        self.assertNotIn("config-tools", now)
        strip = lambda t: [l for l in t.split("\n") if not l.lstrip().startswith("#")]  # noqa: E731
        self.assertEqual(strip(now), strip(then))


class Lock(unittest.TestCase):
    def test_one_holder_at_a_time(self):
        with tempfile.TemporaryDirectory() as d:
            holder = subprocess.Popen([sys.executable, "-c", "import fcntl, os, sys, time\n"
                                       "fd = os.open(sys.argv[1], os.O_RDONLY); fcntl.flock(fd, fcntl.LOCK_EX)\n"
                                       "print('held', flush=True); time.sleep(30)", d],
                                      stdout=subprocess.PIPE)
            try:
                holder.stdout.readline()
                self.assertFalse(lock.take(d))
            finally:
                holder.kill()
                holder.wait()
                holder.stdout.close()
            self.assertTrue(lock.take(d))

    @unittest.skipUnless(shutil.which("flock"), "no flock(1) here")
    def test_excludes_flock1(self):
        with tempfile.TemporaryDirectory() as d:
            holder = subprocess.Popen(["flock", "-n", d, "sleep", "30"])
            try:
                for _ in range(50):
                    if subprocess.call(["flock", "-n", d, "true"]) != 0:
                        break
                    time.sleep(0.05)
                self.assertFalse(lock.take(d))
            finally:
                holder.kill()
                holder.wait()

    def test_an_unopenable_checkout(self):
        with tempfile.TemporaryDirectory() as d:
            gone = os.path.join(d, "gone")
            with self.assertRaises(NexusError) as c:
                lock.take(gone)
            self.assertEqual(c.exception.message, "%s: No such file or directory" % gone)
            f = os.path.join(d, "file")
            open(f, "w").close()
            with self.assertRaises(NexusError) as c:
                lock.take(f)
            self.assertEqual(c.exception.message, "%s: Not a directory" % f)


class Proc(unittest.TestCase):
    def test_signal_exit_codes_are_the_shells(self):
        self.assertEqual(proc.run(["sh", "-c", "kill -TERM $$"]).rc, 143)
        self.assertEqual(proc.exit_code(-2), 130)

    def test_timeout_keeps_what_came(self):
        r = proc.run(["sh", "-c", "echo early; sleep 5"], timeout=0.5)
        self.assertEqual((r.rc, r.out), (124, "early\n"))

    def test_undecodable_bytes_survive(self):
        r = proc.run(["printf", "a\\377b"])
        self.assertEqual(r.out.encode("utf-8", "surrogateescape"), b"a\xffb")

    def test_missing_command(self):
        self.assertEqual(proc.run(["no-such-command-here"]).rc, 127)


if __name__ == "__main__":
    unittest.main()


class SignalsAtStart(unittest.TestCase):
    def test_one_ignored_at_start_is_never_trapped(self):
        import signal
        root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        code = ("import signal, sys; sys.path.insert(0, sys.argv[1]); from agentctl.core import sigs; "
                "old = sigs.trap((signal.SIGINT, signal.SIGTERM), lambda *a: None); "
                "print(sorted(s.name for s in old), signal.getsignal(signal.SIGINT) == signal.SIG_IGN)")

        def ignore_int():
            signal.signal(signal.SIGINT, signal.SIG_IGN)
        out = subprocess.run([sys.executable, "-I", "-c", code, root], stdout=subprocess.PIPE,
                             preexec_fn=ignore_int).stdout.decode().strip()
        self.assertEqual(out, "['SIGTERM'] True")


class PersonasJson(unittest.TestCase):
    from agentctl.adapters import personas_store as store

    def load(self, text):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        p = os.path.join(d, "personas.json")
        with open(p, "wb") as f:
            f.write(text.encode("utf-8", "surrogateescape"))
        return self.store.load_json(p)

    def test_a_round_trip_keeps_order_and_bytes(self):
        ps = [Persona("z9", "grok", "ddev", "colour28", False, "/srv/caf\udce9", "personas/z9.key"),
              Persona("a1", "claude", "docker", "colour25", True, "", "none"),
              Persona("k2", "claude", "docker", "colour90", permissions="ask"),
              Persona("t1", "grok", "docker", "colour1", telemetry=True),
              Persona("d1", "claude", "docker", "colour1", telemetry=True, tool_details=True),
              Persona("e1", "grok", "docker", "colour1", telemetry=True, tool_content=True),
              Persona("r1", "claude", "docker", "colour1", telemetry=True, ports="20100-20199")]
        text = self.store.dumps(ps)
        self.assertTrue(text.endswith("}\n") and text.index('"z9"') < text.index('"a1"'))
        self.assertEqual(text.count('"permissions"'), 1)   # the default isn't written
        self.assertEqual(text.count('"telemetry": true'), 4)
        self.assertEqual(text.count('"tool_details": true'), 1)
        self.assertEqual(text.count('"tool_content": true'), 1)
        self.assertEqual(text.count('"ports"'), 1)          # only where there is a range, and last
        self.assertIn('"telemetry": true,\n      "ports": "20100-20199"\n    }', text)
        self.assertEqual(self.load(text), ps)
        # A personas.json from before the ports reads the same, and is written back byte for byte.
        before = ('{\n  "version": 1,\n  "personas": {\n    "c1": {\n      "flavour": "claude",\n      "context": "docker",\n'
                  '      "colour": "colour25",\n      "statusline": true\n    },\n    "g1": {\n      "flavour": "grok",\n'
                  '      "context": "ddev",\n      "colour": "colour28",\n      "statusline": false,\n      "ssh": "none"\n'
                  '    }\n  }\n}\n')
        old = self.load(before)
        self.assertEqual([p.ports for p in old], ["", ""])
        self.assertEqual(self.store.dumps(old), before)
        # tool details go with telemetry: a persona that doesn't send has none written
        self.assertNotIn("tool_", self.store.dumps([Persona("q1", "grok", "docker", "colour1", tool_details=True,
                                                            tool_content=True)]))

    def test_what_it_refuses(self):
        ok = '"flavour": "claude", "context": "docker", "colour": "colour25", "statusline": true'
        for text, why in (
                ('{"version": 1, "personas": {"c1": {%s}, "c1": {%s}}}' % (ok, ok), "has 'c1' twice"),
                ('{"version": 1, "personas": {"c1": {%s, "colour": "colour1"}}}' % ok, "has 'colour' twice"),
                ('{"version": 1, "personas": {"c1": {%s, "x": 1}}}' % ok, "doesn't take: 'x'"),
                ('{"version": 1, "personas": {}, "x": 1}', "doesn't take: 'x'"),
                ('{"version": 2, "personas": {}}', "is version 2"),
                ('{"version": 1}', 'no "personas"'),
                ('{"version": 1, "personas": {"c1": {%s}}}' % ok.replace("true", '"yes"'), "true or false"),
                ('{"version": 1, "personas": {"C1": {%s}}}' % ok, "isn't a persona name"),
                ('{"version": 1, "personas": {"c1": {%s}}}' % ok.replace('"claude"', '"other"'), "flavour 'other'"),
                ('{"version": 1, "personas": {"c1": {%s, "root": "rel/dir"}}}' % ok, "root 'rel/dir'"),
                ('{"version": 1, "personas": {"c1": {%s, "ssh": "personas/g1.key"}}}' % ok, "ssh 'personas/g1.key'"),
                ('{"version": 1, "personas": {"c1": {%s, "permissions": "maybe"}}}' % ok, "permissions 'maybe'"),
                ('{"version": 1, "personas": {"c1": {%s, "permissions": false}}}' % ok, "permissions that isn't a string"),
                ('{"version": 1, "personas": {"g1": {%s, "permissions": "ask"}}}' % ok.replace('"claude"', '"grok"'),
                 "only a claude persona takes"),
                ('{"version": 1, "personas": {"c1": {%s, "telemetry": "yes"}}}' % ok, "telemetry that isn't true or false"),
                ('{"version": 1, "personas": {"c1": {%s, "telemetry": 1}}}' % ok, "telemetry that isn't true or false"),
                ('{"version": 1, "personas": {"c1": {%s, "telemetry": true, "tool_details": "on"}}}' % ok,
                 "tool_details that isn't true or false"),
                ('{"version": 1, "personas": {"c1": {%s, "tool_details": true}}}' % ok, 'without "telemetry": true'),
                ('{"version": 1, "personas": {"c1": {%s, "telemetry": false, "tool_details": true}}}' % ok,
                 'without "telemetry": true'),
                ('{"version": 1, "personas": {"c1": {%s, "telemetry": true, "tool_content": 1}}}' % ok,
                 "tool_content that isn't true or false"),
                ('{"version": 1, "personas": {"c1": {%s, "tool_content": true}}}' % ok,
                 '"tool_content": true without "telemetry": true (only a persona that sends telemetry can send tool '
                 'content)'),
                ('{"version": 1, "personas": {"c1": {%s, "ports": 20000}}}' % ok, "ports that isn't a string"),
                ('{"version": 1, "personas": {"c1": {%s, "ports": ""}}}' % ok, "has ports '': it takes FROM-TO, two"),
                ('{"version": 1, "personas": {"c1": {%s, "ports": "20099-20000"}}}' % ok, "has ports '20099-20000'"),
                ('{"version": 1, "personas": {"c1": {%s, "ports": "80-180"}}}' % ok, "from 1024 to 65535"),
                ('{"version": 1, "personas": {"c1": {%s, "ports": "20000-20099"}, "c2": {%s, "ports": "21000-21099"}, '
                 '"c3": {%s, "ports": "20050-20149"}}}' % (ok, ok, ok),
                 "persona 'c3' has ports 20050-20149, which overlap c1's (20000-20099)"),
                ("{nope", "isn't valid JSON")):
            with self.assertRaises(self.store.BadPersonasJson) as c:
                self.load(text)
            self.assertIn(why, str(c.exception), text)


class _Answer(http.server.BaseHTTPRequestHandler):
    """A stand-in for the collector, or for what else may sit at its address: answers each POST as self.server.answer
    says ((code, content type, extra headers)), and records what it got."""
    def log_message(self, *args):
        pass

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length", 0)))
        code, kind, extra = self.server.answer
        self.server.got.append((self.command, self.path, self.headers.get("Authorization", "")))
        body = b'{"partialSuccess":{}}' if kind == "application/json" else b"<html></html>"
        self.send_response(code)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        for k, v in extra:
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    do_GET = do_POST


def _serve(answer, tls=None):
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Answer)
    srv.answer, srv.got, srv.daemon_threads = answer, [], True
    if tls:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(*tls)
        srv.socket = ctx.wrap_socket(srv.socket, server_side=True)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, "127.0.0.1:%d" % srv.server_address[1]


def _closed_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class Telemetry(unittest.TestCase):
    from agentctl.adapters import telemetry as io

    def test_probe_outcomes(self):
        """Each outcome, in words of its own; never a raise; the token never follows a redirect."""
        servers = []
        try:
            ok, ok_at = _serve((200, "application/json", ()))
            page, page_at = _serve((200, "text/html", ()))
            refused, refused_at = _serve((401, "application/json", ()))
            other, other_at = _serve((200, "application/json", ()))
            moved, moved_at = _serve((302, "text/html", (("Location", "http://%s/sso" % other_at),)))
            servers += [ok, page, refused, other, moved]
            self.assertEqual(self.io.probe("http://" + ok_at, "tok", ""), ("ok", ""))
            self.assertEqual(ok.got, [("POST", "/v1/metrics", "Bearer tok")])
            self.assertEqual(self.io.probe("http://" + page_at, "tok", ""), ("status", "200"))
            self.assertEqual(self.io.probe("http://" + refused_at, "tok", ""), ("auth", "401"))
            self.assertEqual(self.io.probe("http://" + moved_at, "tok", ""), ("status", "302"))
            self.assertEqual(other.got, [])                     # the token went nowhere else
            self.assertEqual(self.io.probe("https://" + ok_at, "tok", ""), ("tls", ""))   # plain HTTP there
            self.assertEqual(self.io.probe("http://127.0.0.1:%d" % _closed_port(), "", ""), ("connect", ""))
            self.assertEqual(self.io.probe("collector", "", ""), ("connect", ""))
            with tempfile.TemporaryDirectory() as d:
                junk = os.path.join(d, "junk.pem")
                with open(junk, "w") as f:
                    f.write("-----BEGIN CERTIFICATE-----\nAAAA\n-----END CERTIFICATE-----\n")
                self.assertEqual(self.io.probe("http://" + ok_at, "", junk), ("ca", ""))
                self.assertEqual(self.io.probe("http://" + ok_at, "", os.path.join(d, "gone.pem")), ("ca", ""))
                if shutil.which("openssl"):
                    ca, cert, key = _test_ca(d)
                    tls, tls_at = _serve((200, "application/json", ()), tls=(cert, key))
                    servers.append(tls)
                    self.assertEqual(self.io.probe("https://" + tls_at, "", ""), ("cert", ""))
                    self.assertEqual(self.io.probe("https://" + tls_at, "", ca), ("ok", ""))
        finally:
            for srv in servers:
                srv.shutdown()
                srv.server_close()

    def test_probe_ignores_proxy(self):
        """The personas get no proxy from compose: nor does the probe. A probe that used one would leave a connection
        in this listener's backlog."""
        lst = socket.socket()
        lst.bind(("127.0.0.1", 0))
        lst.listen(1)
        lst.settimeout(1)
        proxy = "http://127.0.0.1:%d" % lst.getsockname()[1]
        try:
            with mock.patch.dict(os.environ, {"https_proxy": proxy, "http_proxy": proxy, "HTTPS_PROXY": proxy,
                                              "HTTP_PROXY": proxy, "no_proxy": "", "NO_PROXY": ""}):
                port = _closed_port()
                self.assertEqual(self.io.probe("https://127.0.0.1:%d" % port, "tok", ""), ("connect", ""))
                self.assertEqual(self.io.probe("http://127.0.0.1:%d" % port, "tok", ""), ("connect", ""))
            with self.assertRaises(socket.timeout):
                lst.accept()
        finally:
            lst.close()

    def test_grok_sets_telemetry(self):
        from agentctl.adapters import checks
        with tempfile.TemporaryDirectory() as d:
            f = os.path.join(d, "grok.toml")
            for text, want in (("[telemetry]\nx = 1\n", True), ("[ telemetry ]\n", True), ("[telemetry.sub]\n", True),
                               ("['telemetry']\n", True), ("[[telemetry]]\n", True), ('[ "telemetry" . x ]\n', True),
                               ("telemetry.otel_enabled = true\n", True), ("telemetry = { otel_enabled = true }\n", True),
                               ("[features]\ntelemetry = false\n", False), ("features.telemetry = false\n", False),
                               ("[ui]\ntelemetry_x = 1\n", False), ("# [telemetry]\n", False),
                               ("a = 1\n[x]\ntelemetry.y = 1\n", False)):
                with open(f, "w") as fh:
                    fh.write(text)
                self.assertEqual(checks.grok_sets_telemetry(f), want, text)
            self.assertFalse(checks.grok_sets_telemetry(os.path.join(d, "none.toml")))

    def test_grok_has_paths(self):
        # Any [paths] at all, whatever its keys: nexus --config's own would be a second one.
        from agentctl.adapters import checks
        with tempfile.TemporaryDirectory() as d:
            f = os.path.join(d, "grok.toml")
            for text, want in (("[paths]\nextra_rule_dirs = []\n", True), ("[paths]\nfoo = 1\n", True),
                               ("a = 1\n['paths']\n", True), ('paths.extra_rule_dirs = ["/x"]\n', True),
                               ("paths = { x = 1 }\n", True), ("[ui]\npaths = 1\n", False), ("# [paths]\n", False),
                               ("[pathsx]\n", False), ("", False)):
                with open(f, "w") as fh:
                    fh.write(text)
                self.assertEqual(checks.grok_has_paths(f), want, text)
            self.assertFalse(checks.grok_has_paths(os.path.join(d, "none.toml")))

    def test_ca_problem(self):
        with tempfile.TemporaryDirectory() as d:
            f = os.path.join(d, "ca.pem")
            self.assertEqual(self.io.ca_problem(f), "isn't a file")
            with open(f, "w") as fh:
                fh.write("not a cert\n")
            self.assertIn("has no certificate", self.io.ca_problem(f))
            with open(f, "w") as fh:
                fh.write("-----BEGIN CERTIFICATE-----\nAAAA\n-----END CERTIFICATE-----\n")
            self.assertIn("isn't a certificate a TLS client can load", self.io.ca_problem(f))
            if os.path.isfile("/etc/ssl/certs/ca-certificates.crt"):
                with open("/etc/ssl/certs/ca-certificates.crt", "rb") as src, open(f, "wb") as fh:
                    fh.write(b"-----BEGIN PRIVATE KEY-----\nAAAA\n-----END PRIVATE KEY-----\n" + src.read())
                self.assertIn("holds a private key as well", self.io.ca_problem(f))
        if os.path.isfile("/etc/ssl/certs/ca-certificates.crt"):
            self.assertEqual(self.io.ca_problem("/etc/ssl/certs/ca-certificates.crt"), "")

    def test_write_bundle(self):
        with tempfile.TemporaryDirectory() as d:
            a, home = os.path.join(d, "a"), os.path.join(d, "home")
            os.makedirs(os.path.join(a, "personas"))
            self.assertIn("can't read", self.io.write_bundle(a, home))
            with open(os.path.join(a, "personas", "telemetry-ca.pem"), "wb") as f:
                f.write(b"CA")
            dst = os.path.join(a, "personas", "telemetry-ca.ddev.pem")
            self.assertEqual(self.io.write_bundle(a, home), "")
            with open(dst, "rb") as f:
                self.assertEqual(f.read(), b"CA\n")
            self.assertEqual(os.stat(dst).st_mode & 0o777, 0o644)
            ino = os.stat(dst).st_ino
            self.assertEqual(self.io.write_bundle(a, home), "")
            self.assertEqual(os.stat(dst).st_ino, ino)          # the same bytes: not written again
            os.makedirs(os.path.join(home, ".local/share/mkcert"))
            with open(os.path.join(home, ".local/share/mkcert/rootCA.pem"), "wb") as f:
                f.write(b"MK\n")
            self.assertEqual(self.io.write_bundle(a, home), "")  # mkcert -install since: in it now
            with open(dst, "rb") as f:
                self.assertEqual(f.read(), b"CA\nMK\n")
            self.assertEqual(os.listdir(os.path.join(a, "personas")), sorted(["telemetry-ca.pem", "telemetry-ca.ddev.pem"],
                                                                            key=os.listdir(os.path.join(a, "personas")).index))


def _test_ca(d):
    """A CA, and a certificate for 127.0.0.1 it signs (openssl, as the collector stack's certs service makes them):
    (ca, cert, key) paths in D."""
    cnf = os.path.join(d, "ssl.cnf")
    with open(cnf, "w") as f:
        f.write("[req]\ndistinguished_name = dn\n[dn]\n[ca]\nbasicConstraints = critical,CA:TRUE\n"
                "keyUsage = critical,keyCertSign,cRLSign\n[srv]\nsubjectAltName = IP:127.0.0.1\n"
                "basicConstraints = CA:FALSE\nextendedKeyUsage = serverAuth\n")
    p = lambda n: os.path.join(d, n)  # noqa: E731
    run = lambda *a: subprocess.run(["openssl"] + list(a), check=True, stdout=subprocess.DEVNULL,  # noqa: E731
                                    stderr=subprocess.DEVNULL)
    run("req", "-x509", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:P-256", "-nodes", "-days", "2", "-subj", "/CN=t CA",
        "-config", cnf, "-extensions", "ca", "-keyout", p("ca.key"), "-out", p("ca.pem"))
    run("req", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:P-256", "-nodes", "-subj", "/CN=t", "-config", cnf,
        "-keyout", p("srv.key"), "-out", p("srv.csr"))
    run("x509", "-req", "-in", p("srv.csr"), "-CA", p("ca.pem"), "-CAkey", p("ca.key"), "-CAcreateserial", "-days", "2",
        "-extfile", cnf, "-extensions", "srv", "-out", p("srv.pem"))
    return p("ca.pem"), p("srv.pem"), p("srv.key")
