"""Telemetry (OpenTelemetry to a collector): the collector in .env (TELEMETRY_ENDPOINT, TELEMETRY_TOKEN, TELEMETRY_CA)
and which personas send to it, set in the menu's telemetry action and in add and change, or with the --telemetry
options; what a persona that sends gets (its drop-in or env file, the CA copied in), the restarts asked for, and
what is refused. No collector is ever reached: https://127.0.0.1:1 refuses at once, so each check after a save
says that nothing answered. The token is a low-entropy nxt-fake-… value; the CA a test certificate (no key)."""
import os

from scen import case, set_field
from scen.config_cli import YES, grep
from scen.config_interactive import CONTEXT, FLAVOUR, MENU, PICK, claude_env, perm, persona, sl, tty

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


def td(default="none"):
    """The choice of whose tool details the collector gets, to the end of its prompt. The choice of whose tool content
    (tc) ends the same, so the steps follow their order: tool details, then tool content."""
    return "choice [%s]: " % default


tc = td


def td_one(name, default="y/N"):
    """Whether NAME sends its own tool details, to the end of its prompt."""
    return "Send %s's tool details too (the commands, file paths and other input the tools ran with, which can hold " \
           "secrets)? [%s]: " % (name, default)


def tc_one(name, default="y/N"):
    """Whether NAME sends its own tool content, to the end of its prompt."""
    return "Send %s's tool content too (what the tools read, wrote and printed, such as whole files, command output " \
           "and web pages, which can hold secrets)? [%s]: " % (name, default)


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
             (tele_q("c1"), "y"), (tele_q("g1"), ""), (td(), ""), (tc(), ""), (MENU, "list"), (MENU, "")],
        label="config --no-link --no-build  (telemetry: set up, c1 sends)")
    # The same answers again: nothing to save, and no check. Then a new persona is asked whether it sends.
    tty(sb, [(MENU, "telemetry"), (EP_Q, ""), (TOKEN_Q, ""), (CA_Q, ""), (tele_q("c1", "Y/n"), ""), (tele_q("g1"), ""),
             (td(), ""), (tc(), ""), (MENU, "add")] + persona("c2") + [(tele_q("c2"), "y"), (td_one("c2"), ""),
                                                                      (tc_one("c2"), "")] + claude_env()
        + [(MENU, "")],
        label="config --no-link --no-build  (telemetry as it was; c2 added, sending)")
    # The CA copied in is gone: the menu asks for it again.
    os.unlink(sb.path("a/personas/telemetry-ca.pem"))
    tty(sb, [(MENU, "telemetry"), (EP_Q, ""), (TOKEN_Q, ""), (CA_Q, ""), (CA_Q, ca), (tele_q("c1", "Y/n"), ""),
             (tele_q("g1"), ""), (tele_q("c2", "Y/n"), "n"), (td(), ""), (tc(), ""), (MENU, "")],
        label="config --no-link --no-build  (the CA gone: refused, then given again; c2 stops)")
    # The token: - for none. The CA in place replaced by something else: asked for again.
    sb.write("a/personas/telemetry-ca.pem", "not a certificate\n")
    tty(sb, [(MENU, "telemetry"), (EP_Q, ""), (TOKEN_Q, "-"), (CA_Q, ""), (CA_Q, ca), (tele_q("c1", "Y/n"), ""),
             (tele_q("g1"), ""), (tele_q("c2"), ""), (td(), ""), (tc(), ""), (MENU, "list"), (MENU, "")],
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
    # Tool details by hand: TELEMETRY_TOOL_DETAILS neither on nor off, then off; personas.json's tool_details
    # without telemetry.
    env(TELEMETRY_TOOL_DETAILS="yes")
    sb.run("config", *YES, label="config --yes …  (TELEMETRY_TOOL_DETAILS by hand: yes)")
    env(TELEMETRY_TOOL_DETAILS="off")
    sb.run("config", *YES, label="config --yes …  (TELEMETRY_TOOL_DETAILS by hand: off, as empty)")
    env(TELEMETRY_TOOL_CONTENT="maybe")
    sb.run("config", *YES, label="config --yes …  (TELEMETRY_TOOL_CONTENT by hand: maybe)")
    env(TELEMETRY_TOOL_CONTENT=None)
    set_field(sb, "c1", "tool_details", True)
    set_field(sb, "c1", "telemetry", None)
    sb.run("config", "--list", label="config --list  (c1: tool_details without telemetry)")
    set_field(sb, "c1", "tool_details", None)
    set_field(sb, "c1", "tool_content", True)
    sb.run("config", "--list", label="config --list  (c1: tool_content without telemetry)")


@case("config/telemetry-details-flags")
def _(sb):
    """Tool details (OTEL_LOG_TOOL_DETAILS) with --tool-details: per persona, then all:on (TELEMETRY_TOOL_DETAILS in
    .env, every persona that sends, one that sends later too), back to per persona, and what is refused."""
    sb.personas("c1:claude:docker", "g1:grok:docker", "c2:claude:docker")
    sb.setup("config", *YES, *on(sb, "c1", "g1"))
    for args in (("--tool-details",), ("--tool-details", "c1"), ("--tool-details", "c1:yes"), ("--tool-details", "Bad!:on"),
                 ("--tool-details", "all"), ("--tool-details", "nobody:on"), ("--tool-details", "c2:on")):
        sb.run("config", *YES, *args)
    sb.run("config", *YES, "--tool-details", "c1:on", label="config --yes … --tool-details c1:on  (its drop-in: a restart)")
    grep(sb, ".", "personas/c1.telemetry.json")
    grep(sb, "telemetry|tool_details", "personas.json")
    sb.run("config", *YES, "--tool-details", "g1:on", label="config --yes … --tool-details g1:on  (its next grok reads it)")
    grep(sb, "^\\[telemetry|^otel_", "personas/g1.grok.toml")
    sb.run("config", "--list")
    sb.run("config", *YES, "--tool-details", "g1:on", label="config --yes … --tool-details g1:on  (again: nothing new)")
    sb.run("config", *YES, "--tool-details", "all:on",
           label="config --yes … --tool-details all:on  (.env; the personas' own go; their files stay as they are)")
    grep(sb, "^TELEMETRY_", ".env")
    grep(sb, "telemetry|tool_details", "personas.json")
    sb.run("config", *YES, "--tool-details", "c1:off", label="config --yes … --tool-details c1:off  (refused under all)")
    sb.run("config", *YES, "--tool-details", "c1:on", label="config --yes … --tool-details c1:on  (under all: it does already)")
    sb.run("config", *YES, "--telemetry", "c2:on", label="config --yes … --telemetry c2:on  (its tool details too)")
    grep(sb, "TOOL_DETAILS", "personas/c2.telemetry.json")
    sb.run("config", "--list")
    sb.run("config", *YES, "--tool-details", "c2:on", "--tool-details", "all:off",
           label="config --yes … --tool-details c2:on --tool-details all:off  (c2 alone: all:… comes first)")
    grep(sb, "^TELEMETRY_TOOL", ".env")
    sb.run("config", *YES, "--telemetry", "c2:off", label="config --yes … --telemetry c2:off  (its tool details go too)")
    grep(sb, "telemetry|tool_details", "personas.json")
    sb.run("config", *YES, "--tool-details", "all:on", "--telemetry-endpoint", "none",
           label="config --yes … --tool-details all:on --telemetry-endpoint none  (refused: off comes first)")
    sb.run("config", *YES, "--tool-details", "all:on", label="config --yes … --tool-details all:on")
    sb.run("config", *YES, "--telemetry-endpoint", "none", label="config --yes … --telemetry-endpoint none  (all off)")
    grep(sb, "^TELEMETRY_", ".env")
    sb.run("config", *YES, *on(sb, "c1"), label="config --yes … --telemetry c1:on  (on again: no tool details)")
    grep(sb, "TOOL_DETAILS", "personas/c1.telemetry.json", label="grep TOOL_DETAILS personas/c1.telemetry.json  (none)")


@case("config/telemetry-details-menu")
def _(sb):
    """Tool details in the menu: none, some (each persona asked), all; add and change under all; as it was."""
    sb.personas("c1:claude:docker", "g1:grok:docker")
    sb.setup("config", *YES, *on(sb, "c1", "g1"))
    sends = [(MENU, "telemetry"), (EP_Q, ""), (TOKEN_Q, ""), (CA_Q, ""), (tele_q("c1", "Y/n"), ""),
             (tele_q("g1", "Y/n"), "")]
    tty(sb, sends + [(td(), "some"), (td_one("c1"), "y"), (td_one("g1"), ""), (tc(), ""), (MENU, "list"), (MENU, "")],
        label="config --no-link --no-build  (tool details: some; c1's)")
    grep(sb, "TOOL_DETAILS", "personas/c1.telemetry.json")
    tty(sb, sends + [(td("some"), ""), (td_one("c1", "Y/n"), ""), (td_one("g1"), ""), (tc(), ""), (MENU, "")],
        label="config --no-link --no-build  (the same answers: as it was)")
    tty(sb, sends + [(td("some"), ""), (td_one("c1", "Y/n"), "n"), (td_one("g1"), "y"), (tc(), ""), (MENU, "")],
        label="config --no-link --no-build  (g1's instead: its next grok reads them; c1 restarts)")
    tty(sb, sends + [(td("some"), "all"), (tc(), ""), (MENU, "list"), (MENU, "")],
        label="config --no-link --no-build  (all: every persona that sends)")
    grep(sb, "^TELEMETRY_TOOL", ".env")
    grep(sb, "telemetry|tool_details", "personas.json")
    tty(sb, [(MENU, "add")] + persona("c2") + [(tele_q("c2"), "y"), (tc_one("c2"), "")] + claude_env()
        + [(MENU, "change"), (PICK, "g1"), ("choice [grok]: ", ""), (CONTEXT, ""), (sl("g1"), ""),
           ("Projects root for g1", ""), ("choice [agent]: ", ""), (tele_q("g1", "Y/n"), ""), (tc_one("g1"), ""),
           (MENU, "")],
        label="config --no-link --no-build  (under all: c2 added, g1 changed; neither asked)")
    grep(sb, "TOOL_DETAILS", "personas/c2.telemetry.json")
    tty(sb, sends + [(tele_q("c2", "Y/n"), ""), (td("all"), "some"), (td_one("c1", "Y/n"), "n"), (td_one("g1", "Y/n"), "n"),
                     (td_one("c2", "Y/n"), ""), (tc(), ""), (MENU, "")],
        label="config --no-link --no-build  (some again: c2 alone)")
    tty(sb, [(MENU, "change"), (PICK, "c2"), (FLAVOUR, ""), (CONTEXT, ""), (sl("c2"), ""), ("Projects root for c2", ""),
             ("choice [agent]: ", ""), (perm("c2"), ""), (tele_q("c2", "Y/n"), ""), (td_one("c2", "Y/n"), "n"),
             (tc_one("c2"), ""), (MENU, "list"), (MENU, "")],
        label="config --no-link --no-build  (change c2: no tool details)")
    tty(sb, sends + [(tele_q("c2", "Y/n"), ""), (td(), ""), (tc(), ""), (MENU, "")],
        label="config --no-link --no-build  (none: as it was)")


@case("config/telemetry-content-flags")
def _(sb):
    """Tool content (OTEL_LOG_TOOL_CONTENT) with --tool-content, as tool details: per persona, all:on and back, what is
    refused; with tool details as well (both in the files, details first); and the note for a persona that sends tool
    content without tool details (once, when it comes to that): a Claude one leaves out what it writes to files, a
    grok one sends its commands all the same."""
    sb.personas("c1:claude:docker", "g1:grok:docker", "c2:claude:docker")
    sb.setup("config", *YES, *on(sb, "c1", "g1"))
    for args in (("--tool-content", "c1"), ("--tool-content", "all:yes"), ("--tool-content", "c2:on")):
        sb.run("config", *YES, *args)
    sb.run("config", *YES, "--tool-content", "c1:on",
           label="config --yes … --tool-content c1:on  (a restart, and the note: no Write and Edit text without details)")
    grep(sb, "OTEL_LOG", "personas/c1.telemetry.json")
    sb.run("config", *YES, "--tool-content", "c1:on", label="config --yes … --tool-content c1:on  (again: no note)")
    sb.run("config", *YES, "--tool-details", "c1:on", label="config --yes … --tool-details c1:on  (both)")
    grep(sb, "OTEL_LOG", "personas/c1.telemetry.json")
    sb.run("config", *YES, "--tool-content", "g1:on", label="config --yes … --tool-content g1:on  (grok: its own note)")
    grep(sb, "^\\[telemetry|^otel_log", "personas/g1.grok.toml")
    grep(sb, "telemetry|tool_", "personas.json")
    sb.run("config", "--list")
    sb.run("config", *YES, "--tool-content", "all:on", label="config --yes … --tool-content all:on  (the personas' own go)")
    grep(sb, "^TELEMETRY_", ".env")
    sb.run("config", *YES, "--tool-content", "g1:off", label="config --yes … --tool-content g1:off  (refused under all)")
    sb.run("config", *YES, "--telemetry", "c2:on", label="config --yes … --telemetry c2:on  (its tool content too: the note)")
    sb.run("config", *YES, "--tool-content", "c2:on", "--tool-content", "all:off",
           label="config --yes … --tool-content c2:on --tool-content all:off  (c2 alone: all:… comes first)")
    grep(sb, "telemetry|tool_", "personas.json")
    sb.run("config", *YES, "--telemetry-endpoint", "none", label="config --yes … --telemetry-endpoint none  (all off)")
    grep(sb, "^TELEMETRY_", ".env")


@case("config/telemetry-content-menu")
def _(sb):
    """Tool content in the menu, the question after tool details (both end in "choice [none]: ", so the steps follow
    their order): some, then all for both; add and change under all; tool details off with content on, and the note
    for the Claude personas then."""
    sb.personas("c1:claude:docker", "g1:grok:docker")
    sb.setup("config", *YES, *on(sb, "c1", "g1"))
    sends = [(MENU, "telemetry"), (EP_Q, ""), (TOKEN_Q, ""), (CA_Q, ""), (tele_q("c1", "Y/n"), ""),
             (tele_q("g1", "Y/n"), "")]
    tty(sb, sends + [(td(), ""), (tc(), "some"), (tc_one("c1"), "y"), (tc_one("g1"), "y"), (MENU, "list"), (MENU, "")],
        label="config --no-link --no-build  (tool content: some, c1's and g1's; no tool details)")
    grep(sb, "OTEL_LOG", "personas/c1.telemetry.json")
    grep(sb, "^otel_log", "personas/g1.grok.toml")
    tty(sb, sends + [(td(), "all"), (tc("some"), "all"), (MENU, "list"), (MENU, "")],
        label="config --no-link --no-build  (both, for all)")
    grep(sb, "^TELEMETRY_TOOL", ".env")
    tty(sb, [(MENU, "add")] + persona("c2") + [(tele_q("c2"), "y")] + claude_env()
        + [(MENU, "change"), (PICK, "g1"), ("choice [grok]: ", ""), (CONTEXT, ""), (sl("g1"), ""),
           ("Projects root for g1", ""), ("choice [agent]: ", ""), (tele_q("g1", "Y/n"), ""), (MENU, "")],
        label="config --no-link --no-build  (under all: c2 added, g1 changed; neither asked)")
    tty(sb, sends + [(tele_q("c2", "Y/n"), ""), (td("all"), "none"), (tc("all"), ""), (MENU, "")],
        label="config --no-link --no-build  (tool details off, content still for all: the note for c1 and c2)")
    tty(sb, sends + [(tele_q("c2", "Y/n"), ""), (td(), ""), (tc("all"), "none"), (MENU, "list"), (MENU, "")],
        label="config --no-link --no-build  (tool content off too)")
