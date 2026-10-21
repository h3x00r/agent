"""nexus and a persona that sends telemetry: the files its service mounts must be there before Docker creates the
container (a missing one would become a root-owned directory), a ddev claude persona's bundle (the collector's
CA, then mkcert's) written at each start, and compose.personas.yml in step whether or not .env still names a CA."""
import os

from scen import case
from scen.config_cli import YES
from scen.config_telemetry import on


def env_without_ca(sb):
    p = sb.path("a/.env")
    with open(p) as f:
        text = f.read()
    with open(p, "w") as f:
        f.write("".join(l for l in text.splitlines(True) if not l.startswith("TELEMETRY_CA=")))


@case("nexus/telemetry-start")
def _(sb):
    sb.personas("c1:claude:docker", "d1:claude:ddev", "g1:grok:docker", "n1:claude:docker")
    sb.setup("config", *YES, *on(sb, "c1", "d1", "g1"))
    # d1 (ddev, claude): its bundle, the CA alone while the VM has no mkcert CA; then with mkcert's after it.
    sb.run("nexus", "d1", "start", label="nexus d1 start  (no mkcert CA yet)")
    sb.write("home/.local/share/mkcert/rootCA.pem", "fake mkcert CA\n")
    sb.run("nexus", "d1", "restart", label="nexus d1 restart  (after mkcert -install)")
    sb.run("nexus", "d1", "restart", label="nexus d1 restart  (nothing changed since)")
    sb.run("nexus", "c1", "start")
    sb.run("nexus", "g1", "start")
    sb.run("nexus", "n1", "start", label="nexus n1 start  (sends nothing)")
    # .env edited by hand, TELEMETRY_CA gone: the compose file nexus --config wrote is still in step.
    env_without_ca(sb)
    sb.run("nexus", "c1", "restart", label="nexus c1 restart  (TELEMETRY_CA taken out of .env by hand)")
    # A file a service mounts gone: refused before Docker would make it a root-owned directory.
    os.unlink(sb.path("a/personas/c1.telemetry.json"))
    sb.run("nexus", "c1", "restart", label="nexus c1 restart  (its drop-in gone)")
    os.unlink(sb.path("a/personas/g1.telemetry.env"))
    sb.run("nexus", "g1", "restart", label="nexus g1 restart  (its env file gone)")
    os.unlink(sb.path("a/personas/telemetry-ca.pem"))
    sb.run("nexus", "d1", "restart", label="nexus d1 restart  (the CA gone)")
    sb.run("nexus", "n1", "restart", label="nexus n1 restart  (the CA gone; n1 sends nothing)")


@case("nexus/telemetry-not-restarted")
def _(sb):
    # Telemetry turned on, then off, for running personas: until a restart their containers do as they were made, and a
    # start or an attach says so; a claude one's neutral drop-in goes once its new container is made.
    sb.personas("c1:claude:docker", "g1:grok:docker")
    sb.write("projects/app/.keep", "")
    sb.setup("nexus", "c1", "start")
    sb.setup("nexus", "g1", "start")
    sb.setup("config", *YES, *on(sb, "c1", "g1"))
    sb.run("nexus", "c1", "start", label="nexus c1 start  (running, made before it sent)")
    sb.run("nexus", "g1", cwd="projects/app", label="nexus g1  (an attach, made before it sent)")
    sb.run("nexus", "c1", "restart")
    sb.run("nexus", "g1", "restart")
    sb.run("nexus", "c1", "start", label="nexus c1 start  (restarted: nothing to say)")
    sb.setup("config", *YES, "--telemetry", "c1:off", "--telemetry", "g1:off")
    sb.run("nexus", "c1", "start", label="nexus c1 start  (running, made while it sent)")
    sb.run("nexus", "g1", "start", label="nexus g1 start  (grok stops at its next start: nothing to say)")
    sb.run("nexus", "c1", "restart", label="nexus c1 restart  (its neutral drop-in goes)")
    sb.run("nexus", "c1", "start", label="nexus c1 start  (nothing to say)")
    # Sending, with its settings changed since its container was made (tool details on, then off): its container
    # sends as its drop-in was then, and a start or an attach says so until a restart.
    sb.setup("config", *YES, "--telemetry", "c1:on")
    sb.setup("nexus", "c1", "restart")
    sb.run("nexus", "c1", "start", label="nexus c1 start  (sending, as made: nothing to say)")
    sb.setup("config", *YES, "--tool-details", "c1:on")
    sb.run("nexus", "c1", "start", label="nexus c1 start  (tool details on since it was made)")
    sb.run("nexus", "c1", cwd="projects/app", label="nexus c1  (an attach: the same)")
    sb.run("nexus", "c1", "restart")
    sb.run("nexus", "c1", "start", label="nexus c1 start  (restarted: nothing to say)")
