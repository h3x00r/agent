"""The setup tool answering questions: on a terminal (tty-in: the prompts reach stderr because stdin is a
terminal, and what the terminal echoes is kept apart) and with piped answers (no prompts shown).

nexus --config without --yes: a first run (no .env) asks the shared settings, then the first persona (the
launcher in between, without --no-link); then, every run, what is set up and the menu, each action saved as
it is done. Step substrings are prompt text, which only ever goes to stderr, so a step can't match a list
line on stdout that arrives late. Fake secrets are low-entropy nxt-fake-… values: they must show up in the
env files (tree.txt) and never in the echo part of a transcript.
"""
import os
import subprocess

from scen import NO_LINK, case, images, snapshot_line, two_personas  # noqa: F401

FLAVOUR = "choice [claude]: "
CONTEXT = "choice [docker]: "
SSH = "choice [agent]: "
MENU = "choice [quit]: "
MENU0 = "choice [add]: "      # the menu while there is no persona
PICK = "choice [-]: "
AUTH = "choice [login]: "


def sl(name, default="Y/n"):
    """The statusline yes/no for NAME, to the end of its prompt."""
    return "what nexus top shows) in %s? [%s]: " % (name, default)


def remove(name):
    return "Remove %s? Its env file and home volume stay [y/N]: " % name


def tty(sb, steps, *args, label=None):
    return sb.run("config", *(args or NO_LINK), mode="tty-in", steps=steps, label=label)


def piped(sb, answers, *args, newline=True, label=None):
    text = "\n".join(answers) + ("\n" if newline and answers else "")
    return sb.run("config", *(args or NO_LINK), stdin=text, label=label)


def vm(sb):
    """The shared settings of a first run: the projects root that exists already, the defaults for the rest."""
    return [("The VM's projects root", sb.projects), ("Image registry", ""), ("Claude Code version", "")]


def ident(name="", email="", gh=""):
    return [("git author name for ", name), ("git author email for ", email), ("GH_TOKEN for ", gh)]


def claude_env(auth="", name="", email="", gh=""):
    return [(AUTH, auth)] + ident(name, email, gh)


def persona(name, ssh="", name_q="Name [claude-docker]: "):
    """A Claude persona added: the defaults but its name and ssh, up to its sign-in."""
    return [(FLAVOUR, ""), (CONTEXT, ""), (name_q, name), (sl(name), ""), ("Projects root for " + name, ""), (SSH, ssh)]


def first(sb, name, ssh=""):
    """A first run up to the first persona's sign-in: the shared settings, then that persona."""
    return vm(sb) + persona(name, ssh)


def keygen(path, passphrase=""):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", passphrase, "-C", "golden", "-f", path],
                   check=True, stdin=subprocess.DEVNULL)
    os.unlink(path + ".pub")


def template(sb, flavour):
    with open(os.path.join(sb.a, "personas", flavour + ".env.example")) as f:
        return f.read()


def world(sb, **kw):
    sb.load_world()
    sb.world.update(kw)
    sb.save_world()


# --- first setups ------------------------------------------------------------------------------------
@case("config/ask-tty-first-setup")
def _(sb):
    steps = [
        ("mounted at the same path inside", sb.projects),
        ("Image registry", ""),
        ("Claude Code version", ""),
        ("choice [claude]: ", "1"),
        ("choice [docker]: ", ""),
        ("Name [", "c1"),
        ("[Y/n]: ", "y"),
        ("Projects root for c1", ""),
        ("choice [agent]: ", "none"),
        ("choice [login]: ", ""),
        ("git author name", ""),
        ("git author email", ""),
        ("GH_TOKEN", ""),
        ("choice [quit]: ", ""),
    ]
    sb.run("config", *NO_LINK, mode="tty-in", steps=steps)


@case("config/ask-piped-first-setup")
def _(sb):
    answers = "\n".join([sb.projects, "", "", "1", "", "c1", "y", "", "none", "", "", "", "", ""]) + "\n"
    sb.run("config", *NO_LINK, stdin=answers)


@case("config/ask-tty-first-grok")
def _(sb):
    # grok by number, the suggested name, no statusline, a key of its own; the key typed is hidden. Quit by number.
    tty(sb, vm(sb)
        + [(FLAVOUR, "2"), (CONTEXT, "docker"), ("Name [grok-docker]: ", ""), (sl("grok-docker"), "n"),
           ("Projects root for grok-docker", "-"), (SSH, "new")]
        + [("XAI_API_KEY (hidden; empty to skip): ", "nxt-fake-key-2")]
        + ident("Grok Tester", "grok@example.invalid", "") + [(MENU, "8")])


@case("config/ask-piped-first-grok")
def _(sb):
    # grok by name, the statusline on, your agent; no key typed, so it says to add one.
    piped(sb, [sb.projects, "", "", "grok", "", "g1", "", "", "agent", "", "", "", "", ""])


@case("config/ask-tty-first-link")
def _(sb):
    # Without --no-link, a first run asks where to link the launcher, after the shared settings.
    sb.write("lbin/.keep", "")
    tty(sb, vm(sb) + [("Link the launcher as <dir>/nexus (none: skip)", sb.path("lbin"))]
        + persona("c1") + claude_env() + [(MENU, "")], "--no-build",
        label="config --no-build  (a first run: linked into <S>/lbin)")


# --- ssh ---------------------------------------------------------------------------------------------
@case("config/ask-ssh-path")
def _(sb):
    keygen(os.path.join(sb.home, "keys", "c1"))
    keygen(sb.path("keys/open"))
    os.chmod(sb.path("keys/open"), 0o644)
    keygen(sb.path("keys/locked"), passphrase="nxt-pass-1")
    sb.write("keys/notakey", "not a key\n", 0o600)
    path = "Path of c1's private key (-: back): "
    tty(sb, vm(sb) + [(FLAVOUR, ""), (CONTEXT, ""), ("Name [claude-docker]: ", "c1"), (sl("c1"), ""),
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
                      (SSH, "path"), (path, "~/keys/c1")]      # ~/ is your HOME
        + claude_env() + [(MENU, "")])


@case("config/ask-ssh-no-keygen")
def _(sb):
    # Without ssh-keygen neither a new key nor a key of yours will do: back to the choice each time.
    os.unlink(os.path.join(sb.bin, "ssh-keygen"))
    path = "Path of c1's private key (-: back): "
    tty(sb, vm(sb) + [(FLAVOUR, ""), (CONTEXT, ""), ("Name [claude-docker]: ", "c1"), (sl("c1"), ""),
                      ("Projects root for c1", ""),
                      (SSH, "new"), (SSH, "path"), (path, sb.path("keys/any")), (path, "-"), (SSH, "")]
        + claude_env() + [(MENU, "")])


# --- the menu: add, change, remove -------------------------------------------------------------------
@case("config/ask-menu-add")
def _(sb):
    sb.personas("claude-docker:claude:docker")
    sb.write("roots/g1/.keep", "")
    name = "Name [claude-docker-2]: "
    tty(sb, [(MENU, "add"), (FLAVOUR, ""), (CONTEXT, ""),
             (name, "claude-docker"), (name, "Bad!"), (name, "ls"), (name, "x1-"), (name, ""),
             (sl("claude-docker-2"), ""), ("Projects root for claude-docker-2", ""), (SSH, "none")]
        + claude_env()
        + [(MENU, "add"), (FLAVOUR, "grok"), (CONTEXT, ""), ("Name [grok-docker]: ", "g1"), (sl("g1"), "n"),
           ("Projects root for g1", sb.path("roots/g1")), (SSH, "new"), ("XAI_API_KEY (hidden", "")] + ident()
        + [(MENU, "")])


@case("config/ask-piped-menu-add")
def _(sb):
    # A first run that adds two more from the menu; ssh by number: agent, then new, then none.
    piped(sb, [sb.projects, "", "",
               "1", "", "c1", "", "", "1", "", "", "", "",
               "add", "", "", "c2", "n", "", "3", "", "", "", "",
               "add", "1", "2", "", "y", "-", "2", "", "", "", "",
               ""])


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
             ("choice [none]: ", "agent"), (MENU, "")],
        label="config --no-link --no-build  (change c1: statusline, root, ssh)")
    # The defaults are what it has now; taking them all changes nothing.
    tty(sb, [(MENU, "change"), (PICK, "1"), (FLAVOUR, ""), (CONTEXT, ""), (sl("c1", "y/N"), ""),
             ("[%s]: " % sb.path("roots/c1"), ""), (SSH, ""), (MENU, "")],
        label="config --no-link --no-build  (change c1, every default)")
    # The context: ddev, with no ddev on this VM (nor mkcert).
    tty(sb, [(MENU, "change"), (PICK, "c1"), (FLAVOUR, ""), (CONTEXT, "1"), (sl("c1", "y/N"), ""),
             ("[%s]: " % sb.path("roots/c1"), ""), (SSH, ""), (MENU, "")],
        label="config --no-link --no-build  (change c1: the ddev context)")


@case("config/ask-menu-change-path-default")
def _(sb):
    # A persona with a key of yours: the ssh default is path, and the path prompt offers that key.
    keygen(sb.path("keys/c1"))
    sb.personas("c1:claude:docker", extra=("--ssh", "c1:" + sb.path("keys/c1")))
    tty(sb, [(MENU, "change"), (PICK, ""), (MENU, "change"), (PICK, "c1"), (FLAVOUR, ""), (CONTEXT, ""),
             (sl("c1"), ""), ("Projects root for c1", ""), ("choice [path]: ", ""),
             ("Path of c1's private key (-: back) [%s]: " % sb.path("keys/c1"), ""), (MENU, "")])


@case("config/ask-flavour-change")
def _(sb):
    sb.personas("c1:claude:docker", "c2:claude:docker")
    new_env = "changed flavour: write a new personas/"
    tty(sb, [(MENU, "change"), (PICK, "c1"), (FLAVOUR, "grok"), (CONTEXT, ""), (sl("c1"), ""),
             ("Projects root for c1", ""), (SSH, ""),
             (new_env, ""), ("XAI_API_KEY (hidden", "nxt-fake-key-2")] + ident() + [(MENU, "")],
        label="config --no-link --no-build  (c1 to grok, a new env file)")
    tty(sb, [(MENU, "change"), (PICK, "c2"), (FLAVOUR, "2"), (CONTEXT, ""), (sl("c2"), ""),
             ("Projects root for c2", ""), (SSH, ""), (new_env, "n"), (MENU, "")],
        label="config --no-link --no-build  (c2 to grok, its env file kept)")
    tty(sb, [(MENU, "change"), (PICK, "c1"), ("choice [grok]: ", "claude"), (CONTEXT, ""), (sl("c1"), ""),
             ("Projects root for c1", ""), (SSH, ""), (new_env, "y")] + claude_env() + [(MENU, "")],
        label="config --no-link --no-build  (c1 back to claude, a second backup)")


@case("config/ask-menu-remove")
def _(sb):
    # Back from the pick; c1 not removed after all, then removed; g1 too; then nothing left to change or remove.
    two_personas(sb)
    tty(sb, [(MENU, "remove"), (PICK, "-"),
             (MENU, "change"), (PICK, "3"),
             (MENU, "remove"), (PICK, "c1"), (remove("c1"), ""),
             (MENU, "remove"), (PICK, "1"), (remove("c1"), "y"),
             (MENU, "remove"), (PICK, "g1"), (remove("g1"), "yes"),
             (MENU0, "change"), (MENU0, "remove"),
             (MENU0, "quit")])


# --- env files left from personas removed earlier -----------------------------------------------------
@case("config/menu-leftover-first")
def _(sb):
    # A first run with env files of no persona: not asked about; the menu lists them, and a persona of that
    # name reuses its file (one whose flavour it can't tell, too).
    sb.write("a/personas/d1.env", template(sb, "claude"), 0o600)
    sb.write("a/personas/o2.env", "GIT_AUTHOR_NAME=Someone\n", 0o600)
    sb.write("a/personas/x1.env", template(sb, "grok"), 0o600)
    sb.write("a/personas/Bad.env", "XAI_API_KEY=\n", 0o600)
    tty(sb, vm(sb) + persona("d1")
        + [(MENU, "add"), (FLAVOUR, "2"), (CONTEXT, ""), ("Name [grok-docker]: ", "o2"), (sl("o2"), "n"),
           ("Projects root for o2", ""), (SSH, ""), (MENU, "")])


@case("config/menu-leftover-later")
def _(sb):
    sb.personas("c1:claude:docker")
    sb.write("a/personas/o1.env", template(sb, "claude"), 0o600)
    tty(sb, [(MENU, "")], label="config --no-link --no-build  (o1's file listed)")
    piped(sb, ["add", "", "", "o1", "", "", "", ""], label="config --no-link --no-build  (o1 added, piped)")


@case("config/ask-orphan-other-flavour")
def _(sb):
    # A grok env file left over, then a Claude persona of that name: a new env file is offered.
    sb.personas("c1:claude:docker")
    sb.write("a/personas/d1.env", template(sb, "grok"), 0o600)
    tty(sb, [(MENU, "add"), (FLAVOUR, "claude"), (CONTEXT, ""), ("Name [claude-docker]: ", "d1"), (sl("d1"), ""),
             ("Projects root for d1", ""), (SSH, ""),
             ("personas/d1.env is for a grok persona: write a new personas/d1.env (the old one is kept as a .bak)? "
              "[Y/n]: ", "")]
        + claude_env() + [(MENU, "")])


# --- the shared settings, later -----------------------------------------------------------------------
@case("config/menu-shared")
def _(sb):
    sb.personas("c1:claude:docker")
    sb.write("vmroot/.keep", "")
    sb.write("blocker", "a file\n")
    root = "The VM's projects root"
    tty(sb, [(MENU, "shared"),
             (root, "rel/dir"), (root, "/"), (root, sb.path("blocker") + "/sub"), (root, sb.path("vmroot")),
             ("Image registry", "Bad Registry!"), ("Image registry", "registry.lan:5000/team/"),
             ("Claude Code version", "1.2"), ("Claude Code version", "stable"), (MENU, "")])


@case("config/menu-shared-kept")
def _(sb):
    # A grok persona only, and a registry: the defaults are what .env has; taking them writes nothing new.
    sb.personas("g1:grok:docker", extra=("--registry", "registry.lan:5000"))
    tty(sb, [(MENU, "shared"), ("The VM's projects root", ""), ("Image registry", ""), ("Claude Code version", ""),
             (MENU, "")])


@case("config/menu-shared-images")
def _(sb):
    # Builds on: a registry changed offers every image again (to pull now), declined; a Claude Code version
    # changed, the Claude ones only, pulled.
    sb.personas("c1:claude:docker", "g1:grok:docker")
    images(sb)
    tty(sb, [(MENU, "shared"), ("The VM's projects root", ""), ("Image registry", "registry.lan:5000"),
             ("Claude Code version", ""), ("Pull the images again, for the new registry?", "n"), (MENU, "")], "--no-link",
        label="config --no-link  (a registry)")
    tty(sb, [(MENU, "shared"), ("The VM's projects root", ""), ("Image registry", ""),
             ("Claude Code version", "stable"), ("Pull the Claude images again, for Claude Code stable?", ""), (MENU, "")],
        "--no-link", label="config --no-link  (Claude Code stable)")


# --- secrets -----------------------------------------------------------------------------------------
@case("config/ask-secret-apikey")
def _(sb):
    tty(sb, first(sb, "c1")
        + [(AUTH, "apikey"), ("ANTHROPIC_API_KEY (hidden; empty to skip): ", "nxt-fake-key-1")]
        + ident("", "", "nxt-fake-gh-1") + [(MENU, "")])


@case("config/ask-secret-token")
def _(sb):
    tty(sb, first(sb, "c1")
        + [(AUTH, "3"), ("CLAUDE_CODE_OAUTH_TOKEN (hidden; empty to skip): ", "nxt-fake-token-1")] + ident()
        + [(MENU, "")])


@case("config/ask-secret-empty")
def _(sb):
    # No key typed, and no git identity to offer (no ~/.gitconfig): a name alone isn't enough.
    os.unlink(os.path.join(sb.home, ".gitconfig"))
    tty(sb, first(sb, "c1")
        + [(AUTH, "apikey"), ("ANTHROPIC_API_KEY (hidden; empty to skip): ", ""),
           ("git author name for c1: ", "Only Name"), ("git author email for c1: ", ""), ("GH_TOKEN for ", ""),
           (MENU, "")])


# --- answers that aren't one of the choices ----------------------------------------------------------
@case("config/ask-invalid-choices")
def _(sb):
    tty(sb, vm(sb)
        + [(FLAVOUR, "0"), (FLAVOUR, "3"), (FLAVOUR, "010"), (FLAVOUR, "abc"), (FLAVOUR, "9999999999"),
           (FLAVOUR, "  claude  "),
           (CONTEXT, "02"), ("Name [claude-docker]: ", ""), (sl("claude-docker"), "maybe"),
           ("Projects root for claude-docker", ""), (SSH, "Agent"), (SSH, "")]
        + claude_env("1") + [(MENU, "9"), (MENU, "0"), (MENU, "")])


@case("config/ask-invalid-choices-piped")
def _(sb):
    piped(sb, [sb.projects, "", "", "claude\r", "1 2", "  grok  ", "", "g1\r", "g1", "", "", "",
               "", "", "", "", ""])


# --- end of input, Ctrl-C ----------------------------------------------------------------------------
@case("config/ask-eof-piped")
def _(sb):
    c1 = ["", "1", "", "c1", "", "", "none"]     # the menu's add (its default with no persona), then c1
    piped(sb, [], label="config --no-link --no-build  (no input: the VM's projects root)")
    piped(sb, [sb.projects, "", "", "1", ""], label="config --no-link --no-build  (input ends at the name; .env saved)")
    piped(sb, c1[:4], label="config --no-link --no-build  (input ends at the statusline yes/no)")
    piped(sb, c1 + ["apikey"], label="config --no-link --no-build  (input ends at the hidden API key)")
    piped(sb, c1 + ["", "", ""], label="config --no-link --no-build  (input ends at the hidden GH_TOKEN)")
    piped(sb, c1 + ["", "", "", ""], label="config --no-link --no-build  (input ends at the menu: c1 saved, a quit)")


@case("config/ask-eof-tty")
def _(sb):
    tty(sb, [("The VM's projects root", "<EOF>")], label="config --no-link --no-build  (^D at the VM's projects root)")
    tty(sb, vm(sb) + [(FLAVOUR, ""), (CONTEXT, ""), ("Name [claude-docker]: ", "<EOF>")],
        label="config --no-link --no-build  (^D at the name; .env saved)")
    tty(sb, [(MENU0, ""), (FLAVOUR, ""), (CONTEXT, ""), ("Name [claude-docker]: ", "c1"), (sl("c1"), "<EOF>")],
        label="config --no-link --no-build  (^D at the statusline yes/no)")
    tty(sb, [(MENU0, "")] + persona("c1", "none") + [(AUTH, "apikey"), ("ANTHROPIC_API_KEY (hidden", "<EOF>")],
        label="config --no-link --no-build  (^D at the hidden API key)")
    tty(sb, [(MENU0, "<EOF>")], label="config --no-link --no-build  (^D at the menu: a quit)")


@case("config/ask-int-tty")
def _(sb):
    tty(sb, [("The VM's projects root", "<INT>")], label="config --no-link --no-build  (^C at the VM's projects root)")
    tty(sb, first(sb, "c1") + [(AUTH, ""), ("git author name for ", ""), ("git author email for ", "<INT>")],
        label="config --no-link --no-build  (^C at the git email, .env saved, c1's env file staged)")
    tty(sb, [(MENU0, ""), (FLAVOUR, "grok"), (CONTEXT, ""), ("Name [grok-docker]: ", "g1"), (sl("g1"), ""),
             ("Projects root for g1", ""), (SSH, ""), ("XAI_API_KEY (hidden", "<INT>")],
        label="config --no-link --no-build  (^C at the hidden XAI key)")


@case("config/ask-last-line-no-newline")
def _(sb):
    piped(sb, [sb.projects, "", "", "1", "", "c1", "", "", "none", "", "", "", "nxt-fake-gh-1"], newline=False,
          label="config --no-link --no-build  (the GH_TOKEN line has no newline)")
    piped(sb, ["2"], newline=False, label="config --no-link --no-build  (a re-run: just 2 for add, no newline)")


@case("config/menu-add-then-int")
def _(sb):
    # Saved as each action is done: c1 (a first run), then g1 from the menu; ^C at the menu keeps both.
    tty(sb, first(sb, "c1") + claude_env()
        + [(MENU, "add"), (FLAVOUR, "grok"), (CONTEXT, ""), ("Name [grok-docker]: ", "g1"), (sl("g1"), ""),
           ("Projects root for g1", ""), (SSH, ""), ("XAI_API_KEY (hidden", "")] + ident() + [(MENU, "<INT>")])


@case("config/menu-int-mid-add")
def _(sb):
    # ^C in the middle of an add: nothing of it written (c1, saved before, as it was).
    sb.personas("c1:claude:docker")
    tty(sb, [(MENU, "add")] + persona("c2") + [(AUTH, ""), ("git author name for ", "<INT>")])


@case("config/menu-eof")
def _(sb):
    sb.personas("c1:claude:docker")
    piped(sb, [], label="config --no-link --no-build  (no input: the menu quits)")
    piped(sb, ["list"], label="config --no-link --no-build  (list, then the end of input)")
    piped(sb, ["add", ""], label="config --no-link --no-build  (input ends within the add: nothing of it saved)")


@case("config/menu-refused")
def _(sb):
    # Actions the check refuses: each said, nothing of it written, none of its notes (no restart, no key to add,
    # no commands deleting what is still set up), the personas and shared settings as saved; then the menu.
    sb.personas("c1:claude:docker")
    sb.write("vmroot/.keep", "")
    world(sb, compose={"config_rc": 15, "config_out": "invalid compose project"})
    tty(sb, [(MENU, "add")] + persona("c2") + [(AUTH, "apikey"), ("ANTHROPIC_API_KEY (hidden", "")] + ident()
        + [(MENU, "remove"), (PICK, "c1"), (remove("c1"), "y"),
           (MENU, "change"), (PICK, "c1"), (FLAVOUR, ""), (CONTEXT, ""), (sl("c1"), "n"), ("Projects root for c1", ""),
           (SSH, ""),
           (MENU, "shared"), ("The VM's projects root", sb.path("vmroot")), ("Image registry", "registry.lan:5000"),
           ("Claude Code version", "stable"),
           (MENU, "list"), (MENU, "add"), (FLAVOUR, ""), (CONTEXT, ""), ("Name [claude-docker]: ", "c3"), (sl("c3"), ""),
           ("Projects root for c3 (-: the VM's, now %s) [-]: " % sb.projects, "<INT>")])


@case("config/menu-first-refused")
def _(sb):
    # A first run whose first persona the checks refuse (here a broken personas/claude.json): said, the shared
    # settings kept, and the menu all the same.
    sb.write("a/personas/claude.json", "{ not json\n")
    tty(sb, first(sb, "c1") + claude_env() + [(MENU0, "quit")])


@case("config/menu-flags-shared")
def _(sb):
    # The shared settings given as options without --yes: saved first; then .env's are the defaults, so a change
    # made in `shared` stays when its defaults are taken again.
    sb.personas("c1:claude:docker")
    sb.write("vmroot/.keep", "")
    tty(sb, [(MENU, "shared"), ("The VM's projects root", ""), ("Image registry", "local"), ("Claude Code version", "latest"),
             (MENU, "shared"), ("The VM's projects root", ""), ("Image registry", ""), ("Claude Code version", ""),
             (MENU, "")], *NO_LINK, "--projects-root", sb.path("vmroot"), "--registry", "registry.lan:5000",
        "--claude-version", "stable", label="config --no-link --no-build --projects-root … --registry … --claude-version …")


@case("config/menu-change-image", fakes=("mkcert",))
def _(sb):
    # A context changed: that image offered (declined), and the CA, which ddev wants; a persona added by --persona
    # gets its image offered too.
    sb.personas("c1:claude:docker")
    trust = "Trust ddev's local CA on this VM now (mkcert -install)? [Y/n]: "
    tty(sb, [(MENU, "change"), (PICK, "c1"), (FLAVOUR, ""), (CONTEXT, "ddev"), (sl("c1"), ""), ("Projects root for c1", ""),
             (SSH, ""), ("Build agent-claude-ddev now, for c1?", "n"), (trust, "n"), (MENU, "")], "--no-link",
        label="config --no-link  (c1 to the ddev context)")
    tty(sb, claude_env() + [("Build agent-claude-docker now, for c2?", "n"), (MENU, "")], "--no-link",
        "--persona", "c2:claude:docker", label="config --no-link --persona c2:claude:docker")


@case("config/menu-add-key")
def _(sb):
    # A new persona with a key of its own: made after its save, told as a new one's (no restart).
    sb.personas("c1:claude:docker")
    tty(sb, [(MENU, "add"), (FLAVOUR, "grok"), (CONTEXT, ""), ("Name [grok-docker]: ", "g1"), (sl("g1"), ""),
             ("Projects root for g1", ""), (SSH, "new"), ("XAI_API_KEY (hidden", "")] + ident() + [(MENU, "")])


@case("config/menu-images")
def _(sb):
    # The images action: each flavour and context once; a build that fails makes the exit code 1.
    sb.personas("c1:claude:docker", "c2:claude:docker", "g1:grok:docker")
    images(sb)
    tty(sb, [(MENU, "images"), (MENU, "")], "--no-link", label="config --no-link  (images: built)")
    world(sb, compose={"build_rc": 1})
    tty(sb, [(MENU, "images"), (MENU, "")], "--no-link", label="config --no-link  (images: the builds fail)")


@case("config/menu-overview")
def _(sb):
    # What is set up, and list again; the launcher linked (by hand) where PATH finds it.
    two_personas(sb)
    sb.write("lbin/.keep", "")
    sb.symlink("lbin/nexus", os.path.join(sb.a, "nexus"))
    tty(sb, [(MENU, "list"), (MENU, "")], label="config --no-link --no-build  (not on PATH)")
    sb.run("config", *NO_LINK, mode="tty-in", steps=[(MENU, "")], env={"PATH": sb.path("lbin") + ":" + sb.bin},
           label="config --no-link --no-build  (linked, <S>/lbin on PATH)")


@case("config/menu-refresh")
def _(sb):
    # A run brings what it generates up to date first, as a re-run of the setup did, and says which files it
    # wrote: a setting added to personas/grok.toml, a key deleted (made again), GROK_TAG=latest; then nothing.
    two_personas(sb)
    with open(sb.path("a/personas/grok.toml"), "a") as f:
        f.write('\n[ui]\ntheme = "nxt-golden"\n')
    for k in ("a/personas/g1.key", "a/personas/g1.key.pub"):
        os.unlink(sb.path(k))
    with open(sb.path("a/.env"), "a") as f:
        f.write("GROK_TAG=latest\n")
    tty(sb, [(MENU, "")], label="config --no-link --no-build  (out of date)")
    tty(sb, [(MENU, "")], label="config --no-link --no-build  (up to date)")


@case("config/menu-env-no-personas")
def _(sb):
    # .env there but no persona (a first run cut short, say): no first-run questions; the menu offers add.
    sb.write("a/.env", "PROJECTS_ROOT=%s\n" % sb.projects)
    tty(sb, [(MENU0, "")] + persona("c1") + claude_env() + [(MENU, "")])


@case("config/menu-flags")
def _(sb):
    # Options without --yes: saved first (c2's sign-in asked for), then the menu.
    sb.personas("c1:claude:docker")
    tty(sb, claude_env() + [(MENU, "")], "--no-link", "--no-build", "--persona", "c2:claude:docker",
        "--registry", "registry.lan:5000", label="config --no-link --no-build --persona c2:claude:docker --registry …")


# --- the launcher, mkcert and the images -------------------------------------------------------------
@case("config/ask-link-replace")
def _(sb):
    sb.personas("c1:claude:docker")
    sb.write("lbin/nexus", "#!/bin/sh\necho another nexus\n", 0o755)
    sb.symlink("lbin/agent", os.path.join(sb.a, "agent"))   # the launcher's old name, dangling
    os.makedirs(sb.path("ro"))
    os.chmod(sb.path("ro"), 0o555)
    link = "Link the launcher as <dir>/nexus (none: skip)"
    args = ("--link", sb.path("lbin"), "--no-build")
    tty(sb, [(MENU, "launcher"), (link, "rel/bin"), (link, sb.path("lbin")), ("replace it? [y/N]: ", ""), (MENU, "")],
        *args, label="config --link <S>/lbin --no-build  (not replaced)")
    tty(sb, [(MENU, "launcher"), (link, ""), ("replace it? [y/N]: ", "y"), (MENU, "")],
        *args, label="config --link <S>/lbin --no-build  (replaced)")
    tty(sb, [(MENU, "launcher"), (link, ""), (MENU, "")], *args,
        label="config --link <S>/lbin --no-build  (linked already)")
    tty(sb, [(MENU, "launcher"), (link, sb.path("ro")), (MENU, "")], *args,
        label="config --link <S>/lbin --no-build  (a dir it can't write: sudo)")
    tty(sb, [(MENU, "launcher"), (link, "none"), (MENU, "")], *args,
        label="config --link <S>/lbin --no-build  (none)")


@case("config/ask-ddev-mkcert", fakes=("ddev", "mkcert"))
def _(sb):
    trust = "Trust ddev's local CA on this VM now (mkcert -install)? [Y/n]: "
    tty(sb, vm(sb) + [(FLAVOUR, ""), ("choice [ddev]: ", ""), ("Name [claude-ddev]: ", "d1"), (sl("d1"), ""),
                      ("Projects root for d1", ""), (SSH, "")] + claude_env() + [(trust, "n"), (MENU, "")],
        label="config --no-link --no-build  (a first setup, the CA not trusted)")
    tty(sb, [(MENU, "add"), (FLAVOUR, ""), ("choice [ddev]: ", ""), ("Name [claude-ddev]: ", "d2"), (sl("d2"), ""),
             ("Projects root for d2", ""), (SSH, "")] + claude_env() + [(trust, ""), (MENU, "")],
        label="config --no-link --no-build  (a second ddev persona, the CA trusted)")


@case("config/ask-build-low-disk")
def _(sb):
    sb.personas("c1:claude:docker")
    sb.load_world()
    sb.world["free_gb"] = 1
    sb.save_world()
    tty(sb, [(MENU, "images"), ("Build anyway? [y/N]: ", ""), (MENU, "")], "--no-link",
        label="config --no-link  (low on disk: not built)")
    tty(sb, [(MENU, "add")] + persona("c2") + claude_env() + [("Build agent-claude-docker now, for c2?", "n"), (MENU, "")],
        "--no-link", label="config --no-link  (c2 added, its image not built)")
