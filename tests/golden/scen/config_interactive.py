"""The setup tool answering questions: on a terminal (tty-in: the prompts reach stderr because stdin is a
terminal, and what the terminal echoes is kept apart) and with piped answers (no prompts shown).

Step substrings are prompt text, which only ever goes to stderr, so a step can't match a list line on
stdout that arrives late. Fake secrets are low-entropy nxt-fake-… values: they must show up in the env
files (tree.txt) and never in the echo part of a transcript.
"""
import os
import subprocess

from scen import NO_LINK, case, images, snapshot_line, two_personas  # noqa: F401

FLAVOUR = "choice [claude]: "
CONTEXT = "choice [docker]: "
SSH = "choice [agent]: "
MENU = "choice [done]: "
PICK = "choice [-]: "
AUTH = "choice [login]: "
KEEP = "Keep them? "


def sl(name, default="Y/n"):
    """The statusline yes/no for NAME, to the end of its prompt."""
    return "what nexus top shows) in %s? [%s]: " % (name, default)


def tty(sb, steps, *args, label=None):
    return sb.run("config", *(args or NO_LINK), mode="tty-in", steps=steps, label=label)


def piped(sb, answers, *args, newline=True, label=None):
    text = "\n".join(answers) + ("\n" if newline and answers else "")
    return sb.run("config", *(args or NO_LINK), stdin=text, label=label)


def vm(sb, claude=True):
    """The VM settings of a first setup: the projects root that exists already, the defaults for the rest."""
    s = [("The VM's projects root", sb.projects), ("Image registry", "")]
    if claude:
        s.append(("Claude Code version", ""))
    return s


def ident(name="", email="", gh=""):
    return [("git author name for ", name), ("git author email for ", email), ("GH_TOKEN for ", gh)]


def claude_env(auth="", name="", email="", gh=""):
    return [(AUTH, auth)] + ident(name, email, gh)


def first(name, ssh=""):
    """A first Claude persona, the defaults but its name and ssh, up to the menu's done."""
    return [(FLAVOUR, ""), (CONTEXT, ""), ("Name [claude-docker]: ", name), (sl(name), ""),
            ("Projects root for " + name, ""), (SSH, ssh), (MENU, "")]


def keygen(path, passphrase=""):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", passphrase, "-C", "golden", "-f", path],
                   check=True, stdin=subprocess.DEVNULL)
    os.unlink(path + ".pub")


def template(sb, flavour):
    with open(os.path.join(sb.a, "personas", flavour + ".env.example")) as f:
        return f.read()


# --- first setups ------------------------------------------------------------------------------------
@case("config/ask-tty-first-setup")
def _(sb):
    steps = [
        ("choice [claude]: ", "1"),
        ("choice [docker]: ", ""),
        ("Name [", "c1"),
        ("[Y/n]: ", "y"),
        ("Projects root for c1", ""),
        ("choice [agent]: ", "none"),
        ("choice [done]: ", ""),
        ("mounted at the same path inside", sb.projects),
        ("Image registry", ""),
        ("Claude Code version", ""),
        ("choice [login]: ", ""),
        ("git author name", ""),
        ("git author email", ""),
        ("GH_TOKEN", ""),
    ]
    sb.run("config", *NO_LINK, mode="tty-in", steps=steps)


@case("config/ask-piped-first-setup")
def _(sb):
    answers = "\n".join(["1", "", "c1", "y", "", "none", "", sb.projects, "", "", "", "", "", ""]) + "\n"
    sb.run("config", *NO_LINK, stdin=answers)


@case("config/ask-tty-first-grok")
def _(sb):
    # grok by number, the suggested name, no statusline, a key of its own; the key typed is hidden.
    tty(sb, [(FLAVOUR, "2"), (CONTEXT, "docker"), ("Name [grok-docker]: ", ""), (sl("grok-docker"), "n"),
             ("Projects root for grok-docker", "-"), (SSH, "new"), (MENU, "4")]
        + vm(sb, claude=False)
        + [("XAI_API_KEY (hidden; empty to skip): ", "nxt-fake-key-2")]
        + ident("Grok Tester", "grok@example.invalid", ""))


@case("config/ask-piped-first-grok")
def _(sb):
    # grok by name, the statusline on, your agent; no key typed, so the summary says to add one.
    piped(sb, ["grok", "", "g1", "", "", "agent", "", sb.projects, "", "", "", "", ""])


# --- ssh ---------------------------------------------------------------------------------------------
@case("config/ask-ssh-path")
def _(sb):
    keygen(os.path.join(sb.home, "keys", "c1"))
    keygen(sb.path("keys/open"))
    os.chmod(sb.path("keys/open"), 0o644)
    keygen(sb.path("keys/locked"), passphrase="nxt-pass-1")
    sb.write("keys/notakey", "not a key\n", 0o600)
    path = "Path of c1's private key (-: back): "
    tty(sb, [(FLAVOUR, ""), (CONTEXT, ""), ("Name [claude-docker]: ", "c1"), (sl("c1"), ""),
             ("Projects root for c1", ""),
             (SSH, "4"),
             (path, "keys/c1"),                       # relative
             (path, sb.path("keys/missing")),         # no such file
             (path, "/etc/passwd"),                   # root's, not yours
             (path, sb.path("keys/open")),            # mode 644
             (path, sb.path("keys/notakey")),         # not a private key
             (path, sb.path("keys/locked")),          # a passphrase
             (path, sb.path("keys/with space")),      # a space in the path
             (path, "-"),                             # back to the choice
             (SSH, "path"), (path, ""),               # empty goes back too
             (SSH, "path"), (path, "~/keys/c1"),      # ~/ is your HOME
             (MENU, "")]
        + vm(sb) + claude_env())


@case("config/ask-ssh-no-keygen")
def _(sb):
    # Without ssh-keygen neither a new key nor a key of yours will do: back to the choice each time.
    os.unlink(os.path.join(sb.bin, "ssh-keygen"))
    path = "Path of c1's private key (-: back): "
    tty(sb, [(FLAVOUR, ""), (CONTEXT, ""), ("Name [claude-docker]: ", "c1"), (sl("c1"), ""),
             ("Projects root for c1", ""),
             (SSH, "new"), (SSH, "path"), (path, sb.path("keys/any")), (path, "-"), (SSH, ""), (MENU, "")]
        + vm(sb) + claude_env())


# --- the persona menu --------------------------------------------------------------------------------
@case("config/ask-menu-add")
def _(sb):
    sb.personas("claude-docker:claude:docker")
    sb.write("roots/g1/.keep", "")
    name = "Name [claude-docker-2]: "
    tty(sb, [(MENU, "add"), (FLAVOUR, ""), (CONTEXT, ""),
             (name, "claude-docker"), (name, "Bad!"), (name, "ls"), (name, "x1-"), (name, ""),
             (sl("claude-docker-2"), ""), ("Projects root for claude-docker-2", ""), (SSH, "none"),
             (MENU, "add"), (FLAVOUR, "grok"), (CONTEXT, ""), ("Name [grok-docker]: ", "g1"), (sl("g1"), "n"),
             ("Projects root for g1", sb.path("roots/g1")), (SSH, "new"),
             (MENU, ""), (KEEP, "")]
        + claude_env() + [("XAI_API_KEY (hidden", "")] + ident())


@case("config/ask-piped-menu-add")
def _(sb):
    # A first setup that adds two more from the menu; ssh by number: agent, then new, then none.
    piped(sb, ["1", "", "c1", "", "", "1",
               "add", "", "", "c2", "n", "", "3",
               "add", "1", "2", "", "y", "-", "2",
               "",
               sb.projects, "", "",
               "", "", "", "",
               "", "", "", "",
               "", "", "", ""])


@case("config/ask-menu-change")
def _(sb):
    sb.personas("c1:claude:docker", extra=("--ssh", "c1:none"))
    sb.write("roots/c1/.keep", "")
    sb.write("blocker", "a file\n")
    root = "Projects root for c1 (-: the VM's, now "
    # Statusline off, a projects root of its own after refused ones, your agent instead of none.
    tty(sb, [(MENU, "change"), (PICK, "c1"), (FLAVOUR, ""), (CONTEXT, ""), (sl("c1"), "n"),
             (root, "rel/dir"), (root, "/"), (root, sb.path("blocker") + "/sub"), (root, sb.path("my root")),
             (root, sb.path("roots/c1") + "/"),
             ("choice [none]: ", "agent"), (MENU, ""), (KEEP, "y")],
        label="config --no-link --no-build  (change c1: statusline, root, ssh)")
    # The defaults are what it has now; taking them all changes nothing.
    tty(sb, [(MENU, "change"), (PICK, "1"), (FLAVOUR, ""), (CONTEXT, ""), (sl("c1", "y/N"), ""),
             ("[%s]: " % sb.path("roots/c1"), ""), (SSH, ""), (MENU, ""), (KEEP, "")],
        label="config --no-link --no-build  (change c1, every default)")
    # The context: ddev, with no ddev on this VM (nor mkcert).
    tty(sb, [(MENU, "change"), (PICK, "c1"), (FLAVOUR, ""), (CONTEXT, "1"), (sl("c1", "y/N"), ""),
             ("[%s]: " % sb.path("roots/c1"), ""), (SSH, ""), (MENU, ""), (KEEP, "")],
        label="config --no-link --no-build  (change c1: the ddev context)")


@case("config/ask-menu-change-path-default")
def _(sb):
    # A persona with a key of yours: the ssh default is path, and the path prompt offers that key.
    keygen(sb.path("keys/c1"))
    sb.personas("c1:claude:docker", extra=("--ssh", "c1:" + sb.path("keys/c1")))
    tty(sb, [(MENU, "change"), (PICK, ""), (MENU, "change"), (PICK, "c1"), (FLAVOUR, ""), (CONTEXT, ""),
             (sl("c1"), ""), ("Projects root for c1", ""), ("choice [path]: ", ""),
             ("Path of c1's private key (-: back) [%s]: " % sb.path("keys/c1"), ""), (MENU, ""), (KEEP, "")])


@case("config/ask-flavour-change")
def _(sb):
    sb.personas("c1:claude:docker", "c2:claude:docker")
    new_env = "changed flavour: write a new personas/"
    tty(sb, [(MENU, "change"), (PICK, "c1"), (FLAVOUR, "grok"), (CONTEXT, ""), (sl("c1"), ""),
             ("Projects root for c1", ""), (SSH, ""), (MENU, ""), (KEEP, ""),
             (new_env, ""), ("XAI_API_KEY (hidden", "nxt-fake-key-2")] + ident(),
        label="config --no-link --no-build  (c1 to grok, a new env file)")
    tty(sb, [(MENU, "change"), (PICK, "c2"), (FLAVOUR, "2"), (CONTEXT, ""), (sl("c2"), ""),
             ("Projects root for c2", ""), (SSH, ""), (MENU, ""), (KEEP, ""), (new_env, "n")],
        label="config --no-link --no-build  (c2 to grok, its env file kept)")
    tty(sb, [(MENU, "change"), (PICK, "c1"), ("choice [grok]: ", "claude"), (CONTEXT, ""), (sl("c1"), ""),
             ("Projects root for c1", ""), (SSH, ""), (MENU, ""), (KEEP, ""), (new_env, "y")] + claude_env(),
        label="config --no-link --no-build  (c1 back to claude, a second backup)")


@case("config/ask-menu-remove")
def _(sb):
    two_personas(sb)
    tty(sb, [(MENU, "remove"), (PICK, "-"),
             (MENU, "change"), (PICK, "3"),
             (MENU, "remove"), (PICK, "g1"),
             (MENU, "remove"), (PICK, "1"),
             (MENU, "change"), (MENU, "remove"),
             (MENU, "done"), (KEEP, "y")])


# --- orphan env files --------------------------------------------------------------------------------
@case("config/ask-adopt-first")
def _(sb):
    sb.write("a/personas/d1.env", template(sb, "claude"), 0o600)
    sb.write("a/personas/o2.env", "GIT_AUTHOR_NAME=Someone\n", 0o600)
    sb.write("a/personas/x1.env", template(sb, "grok"), 0o600)
    sb.write("a/personas/Bad.env", "XAI_API_KEY=\n", 0o600)
    tty(sb, [("Found personas/d1.env (flavour claude): set it up as persona d1? [Y/n]: ", ""), (CONTEXT, ""),
             (sl("d1"), ""),
             ("Found personas/o2.env: set it up as persona o2? [Y/n]: ", "y"), (FLAVOUR, "2"), (CONTEXT, ""),
             (sl("o2"), "n"),
             ("Found personas/x1.env (flavour grok): set it up as persona x1? [Y/n]: ", "n"),
             (MENU, "")] + vm(sb))


@case("config/ask-adopt-later")
def _(sb):
    sb.personas("c1:claude:docker")
    sb.write("a/personas/o1.env", template(sb, "claude"), 0o600)
    tty(sb, [("Found personas/o1.env (flavour claude): set it up as persona o1? [y/N]: ", ""), (MENU, ""),
             (KEEP, "")], label="config --no-link --no-build  (o1 not taken)")
    piped(sb, ["y", "", "", "", ""], label="config --no-link --no-build  (o1 taken, piped)")


@case("config/ask-orphan-other-flavour")
def _(sb):
    # A grok env file left over, declined, then a Claude persona of that name: a new env file is offered.
    sb.personas("c1:claude:docker")
    sb.write("a/personas/d1.env", template(sb, "grok"), 0o600)
    tty(sb, [("Found personas/d1.env (flavour grok): set it up as persona d1? [y/N]: ", "n"),
             (MENU, "add"), (FLAVOUR, "claude"), (CONTEXT, ""), ("Name [claude-docker]: ", "d1"), (sl("d1"), ""),
             ("Projects root for d1", ""), (SSH, ""), (MENU, ""), (KEEP, ""),
             ("personas/d1.env is for a grok persona: write a new personas/d1.env (the old one is kept as a .bak)? "
              "[Y/n]: ", "")]
        + claude_env())


# --- the VM settings on a re-run ---------------------------------------------------------------------
@case("config/ask-keep-vm-no")
def _(sb):
    sb.personas("c1:claude:docker")
    sb.write("vmroot/.keep", "")
    sb.write("blocker", "a file\n")
    root = "The VM's projects root"
    tty(sb, [(MENU, ""), (KEEP, "n"),
             (root, "rel/dir"), (root, "/"), (root, sb.path("blocker") + "/sub"), (root, sb.path("vmroot")),
             ("Image registry", "Bad Registry!"), ("Image registry", "registry.lan:5000/team/"),
             ("Claude Code version", "1.2"), ("Claude Code version", "stable")])


@case("config/ask-keep-vm-yes")
def _(sb):
    # A grok persona only, and a registry: the question names no Claude Code version.
    sb.personas("g1:grok:docker", extra=("--registry", "registry.lan:5000"))
    tty(sb, [(MENU, ""), (KEEP, "")])


# --- secrets -----------------------------------------------------------------------------------------
@case("config/ask-secret-apikey")
def _(sb):
    tty(sb, first("c1") + vm(sb)
        + [(AUTH, "apikey"), ("ANTHROPIC_API_KEY (hidden; empty to skip): ", "nxt-fake-key-1")]
        + ident("", "", "nxt-fake-gh-1"))


@case("config/ask-secret-token")
def _(sb):
    tty(sb, first("c1") + vm(sb)
        + [(AUTH, "3"), ("CLAUDE_CODE_OAUTH_TOKEN (hidden; empty to skip): ", "nxt-fake-token-1")] + ident())


@case("config/ask-secret-empty")
def _(sb):
    # No key typed, and no git identity to offer (no ~/.gitconfig): a name alone isn't enough.
    os.unlink(os.path.join(sb.home, ".gitconfig"))
    tty(sb, first("c1") + vm(sb)
        + [(AUTH, "apikey"), ("ANTHROPIC_API_KEY (hidden; empty to skip): ", ""),
           ("git author name for c1: ", "Only Name"), ("git author email for c1: ", ""), ("GH_TOKEN for ", "")])


# --- answers that aren't one of the choices ----------------------------------------------------------
@case("config/ask-invalid-choices")
def _(sb):
    tty(sb, [(FLAVOUR, "0"), (FLAVOUR, "3"), (FLAVOUR, "010"), (FLAVOUR, "abc"), (FLAVOUR, "9999999999"),
             (FLAVOUR, "  claude  "),
             (CONTEXT, "02"), ("Name [claude-docker]: ", ""), (sl("claude-docker"), "maybe"),
             ("Projects root for claude-docker", ""), (SSH, "Agent"), (SSH, ""),
             (MENU, "5"), (MENU, "")]
        + vm(sb) + claude_env("1"))


@case("config/ask-invalid-choices-piped")
def _(sb):
    piped(sb, ["claude\r", "1 2", "  grok  ", "", "g1\r", "g1", "", "", "", "", sb.projects, "", "", "", "", ""])


# --- end of input, Ctrl-C ----------------------------------------------------------------------------
@case("config/ask-eof-piped")
def _(sb):
    head = ["1", "", "c1", "", "", "none", "", sb.projects, "", ""]
    piped(sb, [], label="config --no-link --no-build  (no input: the flavour choice)")
    piped(sb, ["1", ""], label="config --no-link --no-build  (input ends at the name)")
    piped(sb, ["1", "", "c1"], label="config --no-link --no-build  (input ends at the statusline yes/no)")
    piped(sb, head + ["apikey"], label="config --no-link --no-build  (input ends at the hidden API key)")
    piped(sb, head + ["", "", ""], label="config --no-link --no-build  (input ends at the hidden GH_TOKEN)")


@case("config/ask-eof-tty")
def _(sb):
    tty(sb, [(FLAVOUR, "<EOF>")], label="config --no-link --no-build  (^D at the flavour choice)")
    tty(sb, [(FLAVOUR, ""), (CONTEXT, ""), ("Name [claude-docker]: ", "<EOF>")],
        label="config --no-link --no-build  (^D at the name)")
    tty(sb, [(FLAVOUR, ""), (CONTEXT, ""), ("Name [claude-docker]: ", "c1"), (sl("c1"), "<EOF>")],
        label="config --no-link --no-build  (^D at the statusline yes/no)")
    tty(sb, first("c1", "none") + vm(sb) + [(AUTH, "apikey"), ("ANTHROPIC_API_KEY (hidden", "<EOF>")],
        label="config --no-link --no-build  (^D at the hidden API key)")


@case("config/ask-int-tty")
def _(sb):
    tty(sb, [(FLAVOUR, "<INT>")], label="config --no-link --no-build  (^C at the flavour choice)")
    tty(sb, first("c1") + vm(sb) + [(AUTH, ""), ("git author name for ", ""), ("git author email for ", "<INT>")],
        label="config --no-link --no-build  (^C at the git email, its env file staged)")
    tty(sb, [(FLAVOUR, "grok"), (CONTEXT, ""), ("Name [grok-docker]: ", "g1"), (sl("g1"), ""),
             ("Projects root for g1", ""), (SSH, ""), (MENU, "")] + vm(sb, claude=False)
        + [("XAI_API_KEY (hidden", "<INT>")],
        label="config --no-link --no-build  (^C at the hidden XAI key)")


@case("config/ask-last-line-no-newline")
def _(sb):
    piped(sb, ["1", "", "c1", "", "", "none", "", sb.projects, "", "", "", "", "", "nxt-fake-gh-1"], newline=False,
          label="config --no-link --no-build  (the GH_TOKEN line has no newline)")
    piped(sb, ["1"], newline=False, label="config --no-link --no-build  (a re-run: just 1 for add, no newline)")


# --- the launcher link, mkcert and the images: the questions at the end ------------------------------
@case("config/ask-link-replace")
def _(sb):
    sb.personas("c1:claude:docker")
    sb.write("lbin/nexus", "#!/bin/sh\necho another nexus\n", 0o755)
    sb.symlink("lbin/agent", os.path.join(sb.a, "agent"))   # the launcher's old name, dangling
    os.makedirs(sb.path("ro"))
    os.chmod(sb.path("ro"), 0o555)
    link = "Link the launcher as <dir>/nexus (none: skip)"
    args = ("--link", sb.path("lbin"), "--no-build")
    tty(sb, [(MENU, ""), (KEEP, ""), (link, "rel/bin"), (link, sb.path("lbin")), ("replace it? [y/N]: ", "")],
        *args, label="config --link <S>/lbin --no-build  (not replaced)")
    tty(sb, [(MENU, ""), (KEEP, ""), (link, ""), ("replace it? [y/N]: ", "y")],
        *args, label="config --link <S>/lbin --no-build  (replaced)")
    tty(sb, [(MENU, ""), (KEEP, ""), (link, "")], *args, label="config --link <S>/lbin --no-build  (linked already)")
    tty(sb, [(MENU, ""), (KEEP, ""), (link, sb.path("ro"))], *args,
        label="config --link <S>/lbin --no-build  (a dir it can't write: sudo)")
    tty(sb, [(MENU, ""), (KEEP, ""), (link, "none")], *args, label="config --link <S>/lbin --no-build  (none)")


@case("config/ask-ddev-mkcert", fakes=("ddev", "mkcert"))
def _(sb):
    trust = "Trust ddev's local CA on this VM now (mkcert -install)? [Y/n]: "
    tty(sb, [(FLAVOUR, ""), ("choice [ddev]: ", ""), ("Name [claude-ddev]: ", "d1"), (sl("d1"), ""),
             ("Projects root for d1", ""), (SSH, ""), (MENU, "")] + vm(sb) + claude_env() + [(trust, "n")],
        label="config --no-link --no-build  (a first setup, the CA not trusted)")
    tty(sb, [(MENU, ""), (KEEP, ""), (trust, "")], label="config --no-link --no-build  (the CA trusted)")


@case("config/ask-build-low-disk")
def _(sb):
    sb.personas("c1:claude:docker")
    sb.load_world()
    sb.world["free_gb"] = 1
    sb.save_world()
    build = "Build the images now? (the first build takes a few minutes) [Y/n]: "
    tty(sb, [(MENU, ""), (KEEP, ""), (build, ""), ("Build anyway? [y/N]: ", "")], "--no-link",
        label="config --no-link  (low on disk: not built)")
    tty(sb, [(MENU, ""), (KEEP, ""), (build, "n")], "--no-link", label="config --no-link  (no build)")
