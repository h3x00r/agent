"""Telemetry (OpenTelemetry to a collector): the collector in .env (TELEMETRY_ENDPOINT, TELEMETRY_TOKEN, TELEMETRY_CA)
and which personas send to it, set in the menu's telemetry action and in add and change, or with the --telemetry
options; what a persona that sends gets (its drop-in or env file, the CA copied in), the restarts asked for, and
what is refused. No collector is ever reached: https://127.0.0.1:1 refuses at once, so each check after a save
says that nothing answered. The token is a low-entropy nxt-fake-… value; the CA a test certificate (no key)."""
import os

from scen import case, set_field
from scen.config_cli import YES, grep
from scen.config_interactive import MENU, claude_env, persona, tty

EP = "https://127.0.0.1:1"
CA = """-----BEGIN CERTIFICATE-----
MIIBhDCCASqgAwIBAgIUaTCpTVc5j2jnmb4mf6Moiu/6kzkwCgYIKoZIzj0EAwIw
HzEdMBsGA1UEAwwUbmV4dXMgZ29sZGVuIHRlc3QgQ0EwIBcNMjYxMDIwMTI0MDMw
WhgPMjEyNjA5MjYxMjQwMzBaMB8xHTAbBgNVBAMMFG5leHVzIGdvbGRlbiB0ZXN0
IENBMFkwEwYHKoZIzj0CAQYIKoZIzj0DAQcDQgAEHuGLKDAnXBxtrxEdtA6HITJH
ZgzPM88Ol0NTeVgrUBAuPOrAVmVMNh7OcXX9CvjuoAL1uAGj19fE5UhiRnv7/aNC
MEAwDwYDVR0TAQH/BAUwAwEB/zAOBgNVHQ8BAf8EBAMCAQYwHQYDVR0OBBYEFIGM
NwisauUgst9poI4qfrgwqBrgMAoGCCqGSM49BAMCA0gAMEUCIQD8aVC8N9DxkAHg
W1r5iGwP7IGEahkb+uDH0IEOMIOkggIgSp2piuxZw+S42K2ogVjZ0UXmkVT2FlB1
7x0n5g4YD1g=
-----END CERTIFICATE-----
"""
EP_Q = "The collector's OTLP/HTTP address"
TOKEN_Q = "Its bearer token"
CA_Q = "The CA file its certificate is signed by"


def tele_q(name, default="y/N"):
    """Whether NAME sends telemetry, to the end of its prompt."""
    return "Send %s's telemetry (OpenTelemetry: tokens, cost, sessions, tool use; no prompts) to %s? [%s]: " \
           % (name, EP, default)


def ca_file(sb, rel="keys/ca.pem"):
    return sb.write(rel, CA)


def token_file(sb, token="nxt-fake-tele-1", rel="keys/token"):
    return sb.write(rel, token + "\n")


def on(sb, *names):
    """The options that set the collector up (its token and CA) with NAMES sending to it."""
    args = ["--telemetry-endpoint", EP, "--telemetry-token-file", token_file(sb), "--telemetry-ca", ca_file(sb)]
    for n in names:
        args += ["--telemetry", n + ":on"]
    return tuple(args)


@case("config/telemetry-flags")
def _(sb):
    sb.personas("c1:claude:docker", "g1:grok:docker", "d1:claude:ddev")
    os.chmod(sb.path("a/.env"), 0o644)     # an .env from before: made private once it holds the token
    sb.run("config", *YES, *on(sb, "c1", "g1", "d1"))
    sb.run("config", "--list")
    grep(sb, "^TELEMETRY_", ".env")
    grep(sb, "telemetry|^  \"", "compose.personas.yml")
    grep(sb, "telemetry|^\\[", "personas/g1.grok.toml")
    grep(sb, ".", "personas/d1.telemetry.json")
    grep(sb, ".", "personas/g1.telemetry.env")
    sb.run("config", *YES, label="config --yes …  (again: nothing new)")
    sb.run("config", *YES, *on(sb, "c1", "g1", "d1"), label="config --yes … --telemetry…  (the same options again: nothing new)")
    sb.run("config", *YES, "--telemetry-endpoint", EP + "/", label="config --yes … --telemetry-endpoint %s/  (the same)" % EP)
    sb.run("config", *YES, "--telemetry", "c1:off", label="config --yes … --telemetry c1:off  (c1: a restart)")
    token_file(sb, "nxt-fake-tele-2")
    sb.run("config", *YES, "--telemetry-token-file", sb.path("keys/token"),
           label="config --yes … --telemetry-token-file <S>/keys/token  (a new token: g1 and d1 restart)")
    sb.run("config", *YES, "--persona", "g1:grok:docker", label="config --yes … --persona g1:grok:docker  (it still sends)")
    sb.run("config", *YES, "--telemetry-endpoint", "https://127.0.0.2:1",
           label="config --yes … --telemetry-endpoint https://127.0.0.2:1  (d1: a restart; g1: its next grok reads it)")
    sb.run("config", *YES, "--telemetry", "c1:on", "--persona", "c1:grok:docker",
           label="config --yes … --telemetry c1:on --persona c1:grok:docker  (claude, then grok: its drop-in neutral)")
    grep(sb, ".", "personas/c1.telemetry.json")
    sb.run("config", *YES, "--persona", "c1:claude:docker", label="config --yes … --persona c1:claude:docker  (and back)")
    sb.run("config", *YES, "--remove", "g1", label="config --yes … --remove g1  (its telemetry file stays)")
    sb.run("config", *YES, "--telemetry-ca", "none", label="config --yes … --telemetry-ca none  (a public certificate)")
    sb.write("keys/empty", "")
    sb.run("config", *YES, "--telemetry-token-file", sb.path("keys/empty"), label="config --yes … --telemetry-token-file <S>/keys/empty  (no token)")
    grep(sb, "HEADERS|^TELEMETRY_TOKEN", "personas/d1.telemetry.json", label="grep HEADERS personas/d1.telemetry.json  (none)")
    sb.run("config", *YES, "--telemetry-endpoint", "none", label="config --yes … --telemetry-endpoint none  (c1 and d1 stop)")
    sb.run("config", "--list")
    grep(sb, ".", "personas/d1.telemetry.json", label="grep . personas/d1.telemetry.json  (neutral, until it is restarted)")


@case("config/telemetry-refused")
def _(sb):
    sb.personas("c1:claude:docker", "g1:grok:docker")
    sb.write("keys/two", "nxt-fake-tele-1 nxt-fake-tele-2\n")
    sb.write("keys/comma", "nxt-fake,tele\n")
    sb.write("keys/junk.pem", "not a certificate\n")
    sb.write("keys/withkey.pem", "-----BEGIN EC PRIVATE KEY-----\nAAAA\n-----END EC PRIVATE KEY-----\n" + CA)
    for args in (("--telemetry",), ("--telemetry", "c1"), ("--telemetry", "c1:maybe"), ("--telemetry", "Bad!:on"),
                 ("--telemetry", "nobody:on"), ("--telemetry-endpoint", "collector.lan:4318"),
                 ("--telemetry-endpoint", "https://collector.lan:4318?x=1"),
                 ("--telemetry-token-file", sb.path("keys/missing")), ("--telemetry-token-file", sb.path("keys/two")),
                 ("--telemetry-token-file", sb.path("keys/comma")), ("--telemetry-ca", sb.path("keys/missing.pem")),
                 ("--telemetry-ca", sb.path("keys/junk.pem")), ("--telemetry-ca", sb.path("keys/withkey.pem")),
                 ("--telemetry-ca", "keys/ca.pem"), ("--telemetry-token-file", "keys/token"), ("--telemetry", "c1:on")):
        sb.run("config", *YES, *args)
    sb.setup("config", *YES, *on(sb, "g1"))
    os.unlink(sb.path("a/personas/telemetry-ca.pem"))
    sb.run("config", *YES, label="config --yes …  (the CA copied in is gone)")
    sb.setup("config", *YES, "--telemetry-ca", ca_file(sb))
    with open(sb.path("a/personas/grok.toml"), "a") as f:
        f.write("\n[telemetry]\notel_enabled = false\n")
    sb.run("config", *YES, label="config --yes …  (personas/grok.toml has a [telemetry] of its own)")
    sb.run("config", *YES, "--telemetry", "g1:off", label="config --yes … --telemetry g1:off  (then it may)")
    set_field(sb, "c1", "telemetry", "yes")
    sb.run("config", "--list", label="config --list  (c1's telemetry: 'yes')")


@case("config/telemetry-menu")
def _(sb):
    sb.personas("c1:claude:docker", "g1:grok:docker")
    ca = ca_file(sb)
    # The collector, with a wrong answer to each question first; c1 sends, g1 doesn't. Then the check: nothing
    # answered (the probe ignores the https_proxy every scenario has).
    tty(sb, [(MENU, "telemetry"), (EP_Q, "collector.lan:4318"), (EP_Q, EP + "/"), (TOKEN_Q, "nxt fake"),
             (TOKEN_Q, "nxt-fake-tele-1"), (CA_Q, "rel.pem"), (CA_Q, sb.path("keys/missing.pem")), (CA_Q, ca),
             (tele_q("c1"), "y"), (tele_q("g1"), ""), (MENU, "list"), (MENU, "")],
        label="config --no-link --no-build  (telemetry: set up, c1 sends)")
    # The same answers again: nothing to save, and no check. Then a new persona is asked whether it sends.
    tty(sb, [(MENU, "telemetry"), (EP_Q, ""), (TOKEN_Q, ""), (CA_Q, ""), (tele_q("c1", "Y/n"), ""), (tele_q("g1"), ""),
             (MENU, "add")] + persona("c2") + [(tele_q("c2"), "y")] + claude_env() + [(MENU, "")],
        label="config --no-link --no-build  (telemetry as it was; c2 added, sending)")
    # The CA copied in is gone: the menu asks for it again.
    os.unlink(sb.path("a/personas/telemetry-ca.pem"))
    tty(sb, [(MENU, "telemetry"), (EP_Q, ""), (TOKEN_Q, ""), (CA_Q, ""), (CA_Q, ca), (tele_q("c1", "Y/n"), ""),
             (tele_q("g1"), ""), (tele_q("c2", "Y/n"), "n"), (MENU, "")],
        label="config --no-link --no-build  (the CA gone: refused, then given again; c2 stops)")
    # The token: - for none. The CA in place replaced by something else: asked for again.
    sb.write("a/personas/telemetry-ca.pem", "not a certificate\n")
    tty(sb, [(MENU, "telemetry"), (EP_Q, ""), (TOKEN_Q, "-"), (CA_Q, ""), (CA_Q, ca), (tele_q("c1", "Y/n"), ""),
             (tele_q("g1"), ""), (tele_q("c2"), ""), (MENU, "list"), (MENU, "")],
        label="config --no-link --no-build  (no token; the CA in place isn't one: given again)")
    tty(sb, [(MENU, "telemetry"), (EP_Q, "none"), (MENU, "list"), (MENU, "")],
        label="config --no-link --no-build  (telemetry off)")


@case("config/telemetry-hand-edits")
def _(sb):
    """.env's TELEMETRY_* edited by hand: a CA path of your own is checked and copied in, whoever sends; an address or
    token the options would refuse is refused for a persona that sends; a token changed there asks for the restarts
    and the check, as one given with the options does. And a menu run whose options are refused keeps .env's."""
    sb.personas("c1:claude:docker", "g1:grok:docker")
    ca_file(sb)
    sb.write("keys/junk.pem", "not a certificate\n")

    def env(**kv):
        p = sb.path("a/.env")
        with open(p) as f:
            lines = [l for l in f.read().split("\n") if l and not any(l.startswith(k + "=") for k in kv)]
        with open(p, "w") as f:
            f.write("\n".join(lines + ["%s=%s" % kv_ for kv_ in kv.items() if kv_[1] is not None]) + "\n")

    for path, what in (("keys/missing.pem", "missing"), ("keys/junk.pem", "not a certificate")):
        env(TELEMETRY_CA=sb.path(path))
        sb.run("config", *YES, label="config --yes …  (TELEMETRY_CA by hand: %s; nobody sends)" % what)
        sb.run("config", "--no-link", "--no-build", mode="tty-in", steps=[(MENU, "")],
               label="config --no-link --no-build  (the same: refused, then the menu)")
    env(TELEMETRY_CA=sb.path("keys/ca.pem"))
    sb.run("config", *YES, label="config --yes …  (TELEMETRY_CA by hand: a certificate, copied in)")
    grep(sb, "^TELEMETRY_", ".env")
    sb.setup("config", *YES, "--telemetry-endpoint", EP, "--telemetry-token-file", token_file(sb), "--telemetry", "c1:on",
             "--telemetry", "g1:on")
    env(TELEMETRY_TOKEN="nxt-fake-tele-hand")
    sb.run("config", *YES, label="config --yes …  (TELEMETRY_TOKEN changed by hand: restarts, and the check)")
    env(TELEMETRY_TOKEN="nxt fake")
    sb.run("config", *YES, label="config --yes …  (TELEMETRY_TOKEN by hand, with a space)")
    env(TELEMETRY_TOKEN="nxt-fake-tele-hand", TELEMETRY_ENDPOINT="collector")
    sb.run("config", *YES, label="config --yes …  (TELEMETRY_ENDPOINT by hand, without https://)")
    env(TELEMETRY_ENDPOINT=EP)
    # A Claude persona with a CA of its own for Node: the drop-in's would replace it.
    with open(sb.path("a/personas/c1.env"), "a") as f:
        f.write("NODE_EXTRA_CA_CERTS=/etc/ssl/corp-ca.pem\n")
    sb.run("config", *YES, label="config --yes …  (c1's env file sets NODE_EXTRA_CA_CERTS)")
    with open(sb.path("a/personas/c1.env")) as f:
        text = f.read()
    with open(sb.path("a/personas/c1.env"), "w") as f:
        f.write(text.replace("NODE_EXTRA_CA_CERTS=/etc/ssl/corp-ca.pem\n", ""))
    # The menu's options refused (claude.ask.json gone): .env's telemetry stands, and the next action saves it.
    os.unlink(sb.path("a/personas/claude.ask.json"))
    sb.run("config", "--no-link", "--no-build", "--permissions", "c1:ask", "--telemetry-endpoint", "none", mode="tty-in",
           steps=[(MENU, "list"), (MENU, "remove"), ("choice [-]: ", "g1"), ("Remove g1? Its env file and home volume stay [y/N]: ", "y"),
                  (MENU, "")],
           label="config --no-link --no-build --permissions c1:ask --telemetry-endpoint none  (refused; then remove g1)")
    grep(sb, "^TELEMETRY_ENDPOINT", ".env")
