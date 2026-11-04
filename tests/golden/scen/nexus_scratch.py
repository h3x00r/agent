"""nexus and the docker work: each persona's scratch dir in the shared scratch root (made by the preflight; the root
must be there, nexus --config makes it), the Nexus notes its service mounts (refused at a start when the file is
gone), and the warning for a running container made without them."""
import os
import shutil

from scen import case, two_personas


@case("nexus/scratch-start")
def _(sb):
    # The root as the shell has it, else as .env has it, else /srv/nexus-scratch (never reached here); one trailing /
    # taken off. The persona's dir is made where it is missing, and left as it is where it is there.
    two_personas(sb)
    os.makedirs(sb.path("other/root"))
    os.makedirs(sb.path("dotenv/root"))
    with open(sb.path("a/.env"), "a") as f:
        f.write("NEXUS_SCRATCH_ROOT=%s\n" % sb.path("dotenv/root"))
    sb.run("nexus", "c1", "start", label="nexus c1 start  (NEXUS_SCRATCH_ROOT=<S>/scratch in the shell)")
    sb.run("nexus", "c1", "start", label="nexus c1 start  (running: its dir is there)")
    sb.run("nexus", "g1", "start", env={"NEXUS_SCRATCH_ROOT": sb.path("other/root") + "/"},
           label="nexus g1 start  (NEXUS_SCRATCH_ROOT=<S>/other/root/ in the shell)")
    # Running in a container made with another root's dir: said, with the restart that mounts this one.
    sb.run("nexus", "g1", "start", label="nexus g1 start  (running, made with <S>/other/root/g1; the root is <S>/scratch)")
    sb.run("nexus", "c1", "restart", env={"NEXUS_SCRATCH_ROOT": None},
           label="nexus c1 restart  (no NEXUS_SCRATCH_ROOT in the shell, <S>/dotenv/root in .env)")
    sb.run("nexus", "c1", "start", label="nexus c1 start  (running; the shell's <S>/scratch beats .env's)")


@case("nexus/scratch-root-missing")
def _(sb):
    # The root isn't there: refused at a start, and at an attach or a headless run that would start the persona
    # (nexus --config makes it, with sudo when it must; nexus never runs sudo), with the command that makes it, or the
    # other way out; one that is a file, with no install -d (it would fail). An attach or a headless run to a running
    # persona only warns (its container has its mount); a start or a restart of it is still refused. stop, logs, build
    # and pull don't check.
    two_personas(sb)
    sb.write("afile", "a file, not a directory\n")
    os.makedirs(sb.path("ro"))
    with open(sb.path("a/.env"), "a") as f:
        f.write("NEXUS_SCRATCH_ROOT=%s\n" % sb.path("gone"))
    sb.run("nexus", "c1", "start", env={"NEXUS_SCRATCH_ROOT": sb.path("none")},
           label="nexus c1 start  (NEXUS_SCRATCH_ROOT=<S>/none in the shell)")
    sb.run("nexus", "c1", "start", env={"NEXUS_SCRATCH_ROOT": sb.path("afile")},
           label="nexus c1 start  (NEXUS_SCRATCH_ROOT=<S>/afile, a file)")
    for v in ("rel/dir", "/", "/srv/a:b", "/srv/a b"):
        sb.run("nexus", "c1", "start", env={"NEXUS_SCRATCH_ROOT": v}, label="nexus c1 start  (NEXUS_SCRATCH_ROOT=%s)" % v)
    sb.run("nexus", "c1", "start", env={"NEXUS_SCRATCH_ROOT": None},
           label="nexus c1 start  (NEXUS_SCRATCH_ROOT=<S>/gone in .env)")
    sb.run("nexus", "c1", env={"NEXUS_SCRATCH_ROOT": None}, label="nexus c1  (not running: the attach would start it)")
    sb.container("c1")
    sb.run("nexus", "c1", env={"NEXUS_SCRATCH_ROOT": None}, label="nexus c1  (running; the root <S>/gone in .env)")
    sb.run("nexus", "c1", "headless", "nxt say hi", env={"NEXUS_SCRATCH_ROOT": None},
           label="nexus c1 headless 'nxt say hi'  (the same)")
    sb.run("nexus", "c1", env={"NEXUS_SCRATCH_ROOT": sb.path("afile")},
           label="nexus c1  (running; NEXUS_SCRATCH_ROOT=<S>/afile, a file)")
    sb.run("nexus", "c1", "start", env={"NEXUS_SCRATCH_ROOT": None}, label="nexus c1 start  (running; the root gone)")
    sb.run("nexus", "c1", "restart", env={"NEXUS_SCRATCH_ROOT": None}, label="nexus c1 restart  (the same)")
    sb.run("nexus", "c1", "stop", env={"NEXUS_SCRATCH_ROOT": None}, label="nexus c1 stop  (the same: no checks)")
    # The root is there, but the persona's dir can't be made in it: the command that makes it yours.
    mode = os.stat(sb.path("ro")).st_mode & 0o7777
    os.chmod(sb.path("ro"), 0o555)
    try:
        sb.run("nexus", "g1", "start", env={"NEXUS_SCRATCH_ROOT": sb.path("ro")},
               label="nexus g1 start  (NEXUS_SCRATCH_ROOT=<S>/ro, mode 555)")
    finally:
        os.chmod(sb.path("ro"), mode)   # as it was, so tree.txt doesn't show it and the sandbox can go


@case("nexus/notes-missing")
def _(sb):
    # A persona's notes file gone (a checkout copied without it, say): refused before Docker would make the mount's
    # source a root-owned directory, as for the grok config, which is checked first. A running one needs nothing.
    two_personas(sb)
    shutil.copy(sb.path("a/personas/g1.grok.toml"), sb.path("g1.grok.toml"))
    os.unlink(sb.path("a/personas/c1.notes.md"))
    sb.run("nexus", "c1", "start", label="nexus c1 start  (personas/c1.notes.md gone)")
    os.unlink(sb.path("a/personas/g1.grok.toml"))
    os.unlink(sb.path("a/personas/g1.notes.md"))
    sb.run("nexus", "g1", "start", label="nexus g1 start  (personas/g1.grok.toml and g1.notes.md gone)")
    shutil.copy(sb.path("g1.grok.toml"), sb.path("a/personas/g1.grok.toml"))
    sb.run("nexus", "g1", "start", label="nexus g1 start  (personas/g1.notes.md gone)")
    sb.container("c1")
    sb.run("nexus", "c1", "start", label="nexus c1 start  (running; its notes file still gone)")
    sb.run("config", "--yes", "--no-link", "--no-build", label="config --yes  (writes them again)")
    sb.run("nexus", "g1", "start")


@case("nexus/scratch-made-without")
def _(sb):
    # Running in a container made before nexus gave it its scratch dir and the notes (before a git pull and
    # `nexus --config --yes`): one warning naming what it lacks, at a start, an attach and a headless run; the restart
    # makes it anew, with both.
    two_personas(sb)
    sb.container("c1", nexus_mounts=False, mounts=["/home/dev"])
    sb.container("g1", nexus_mounts=False, mounts=["/home/dev", sb.path("scratch/g1")])
    sb.run("nexus", "c1", "start", label="nexus c1 start  (running, made without its scratch dir and the notes)")
    sb.run("nexus", "c1", label="nexus c1  (an attach: the same)")
    sb.run("nexus", "c1", "headless", "nxt say hi", label="nexus c1 headless 'nxt say hi'  (the same)")
    sb.run("nexus", "g1", "start", label="nexus g1 start  (running, made with its scratch dir, without the notes)")
    sb.run("nexus", "c1", "restart")
    sb.run("nexus", "c1", "start", label="nexus c1 start  (running, made with both)")
