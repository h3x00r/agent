import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SHIM = os.path.join(ROOT, "grok-mode.sh")
SH = shutil.which("sh") or "/bin/sh"               # dash here and in the image; the shim's #! is /bin/sh
DEFAULT_BIN = "/opt/grok/.grok/bin"                 # where the image's real grok is; absent on a test host

# A stand-in for the real grok: one line of JSON on stdout, with what it was given, then exit $NXW_STUB_EXIT.
STUB = r'''
import json, os, sys
e = os.environ
sys.stdout.write(json.dumps({"argv0": sys.argv[0], "argv": sys.argv[1:], "pid": os.getpid(),
                             "wrapper_set": "NEXUS_WRAPPER" in e, "wrapped": e.get("NEXUS_WRAPPED"),
                             "wrapped_set": "NEXUS_WRAPPED" in e, "stdin": sys.stdin.read()}) + "\n")
sys.exit(int(e.get("NXW_STUB_EXIT", "0")))
'''

NEEDS = "grok: --wrapper needs a file: --wrapper FILE, or --wrapper=FILE\n"
TWICE = "grok: --wrapper given twice: give one file\n"
BOTH = "grok: --wrapper with --no-wrapper: give one of them\n"
MISSING = ("no such file here (a persona sees only its projects root and its scratch dir): nexus <persona> --wrapper "
           "FILE copies a file in")
PERSONA_WRAPPER = "/etc/grok/wrapper.md"            # where a persona's own wrapper is mounted (nexus --config's copy)
MISSING_PERSONA = ("no such file here (the persona's copy of its wrapper file, personas/<persona>.wrapper.md, is "
                   "missing): re-run nexus --config")
NOT_REGULAR = "not a regular file: name a text file"
UNREADABLE = "can't be read: make it readable (chmod a+r)"
HAS_NUL = "holds a NUL byte: save it as UTF-8 text"
NOT_UTF8 = "isn't valid UTF-8: save it as UTF-8"
EMPTY = "is empty (or only newlines): write the instructions in it"
NO_PYTHON = "can't be checked, as this image has no python3: rebuild it (nexus <persona> build)"
LIMIT = 122880


def too_big(size, rsize=0):
    return ("is too big: %d bytes, %d of --rules and the 2 always kept for a blank line come to %d, over the 122880 "
            "(120 KiB) one argument takes: shorten it" % (size, rsize, size + rsize + 2))


def refused(f, why, env=False):
    """The line a refused FILE stops the shim with (exit 2)."""
    return "grok: --wrapper %s%s: %s\n" % (f, " (NEXUS_WRAPPER)" if env else "", why)


def resumed(f, env=False):
    """The note a wrapped resume gets."""
    return ("grok: --wrapper %s%s: a resumed session keeps the system prompt it was created with, so it has a wrapper "
            "only if it was created with one; for this one, start a new session\n"
            % (f, " (NEXUS_WRAPPER)" if env else ""))


def resumed_without(f, why):
    """The warning a resume gets when NEXUS_WRAPPER's FILE fails a check: grok runs without it."""
    return ("grok: --wrapper %s (NEXUS_WRAPPER): %s; resuming without it, as a resumed session keeps the system prompt "
            "it was created with\n" % (f, why))


def leader(f, env=False):
    """The warning a wrapped call with --leader gets."""
    return ("grok: --wrapper %s%s: with --leader, the leader gives every session it serves its first client's rules, "
            "so this one may get another wrapper or none: leave out --leader\n"
            % (f, " (NEXUS_WRAPPER)" if env else ""))


def not_applied(why):
    return "grok: --wrapper: not applied: %s\n" % why


HELP = not_applied("a help/version call")


def sub(word):
    return not_applied("'%s' is a subcommand" % word)


def lists():
    """The shim's word lists, as a check against a newer grok's help takes them out: {label: [lines between the
    BEGIN and END markers]}, the variable's own lines included."""
    with open(SHIM) as f:
        text = f.read()
    out = {}
    for label in re.findall(r"^# BEGIN grok (.+)$", text, re.M):
        m = re.search(r"^# BEGIN grok %s\n(.*?)^# END grok %s$" % (re.escape(label), re.escape(label)), text,
                      re.M | re.S)
        out[label] = m.group(1).splitlines() if m else None
    return text, out


def words(block):
    return [w for w in block if not (w.startswith("#") or w.startswith("'") or w.endswith("='"))]


_, LISTS = lists()
SUBCOMMANDS = words(LISTS.get("subcommands") or [])
VALUE_OPTIONS = words(LISTS.get("value options") or [])
OPTIONAL_VALUE_OPTIONS = words(LISTS.get("optional-value options") or [])
HIDDEN_VALUE_OPTIONS = words(LISTS.get("hidden value options") or [])
# The hidden value options step 2 of the plan found on grok 1.0.77 (cap/c08, and the WF2 check): a lower bound of the
# block, which the inventory in the shim's header completes.
HIDDEN_KNOWN = ("--print", "--load", "--mode", "--header", "--compaction-mode", "--compaction-detail", "--storage-mode",
                "--hunk-tracker-mode", "--background-shell-timeout", "--background-wait-timeout", "--client-identifier",
                "--conversation-history-file")


class Shim(unittest.TestCase):
    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="nxw-grok-"))   # sh's pwd is the physical one
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.stub = os.path.join(self.tmp, "real-grok")
        with open(self.stub, "w") as f:
            f.write("#!%s -I\n%s" % (sys.executable, STUB))
        os.chmod(self.stub, 0o755)
        self.write("w.md", b"wrap\n")
        self.write("g.md", b"other\n")

    def write(self, name, data):
        path = os.path.join(self.tmp, name)
        d = os.path.dirname(path)
        if not os.path.isdir(d):
            os.makedirs(d)
        with open(path, "wb") as f:
            f.write(data)
        return path

    def env(self, **kw):
        """A clean environment (no NEXUS_*, CDPATH, PWD or OLDPWD of this one's), KW added; None drops."""
        e = {"PATH": os.environ.get("PATH", os.defpath), "HOME": self.tmp, "NEXUS_GROK_REAL": self.stub}
        e.update(kw)
        return {k: v for k, v in e.items() if v is not None}

    def run_shim(self, args, env=None, data=None, path=SHIM):
        """sh PATH ARGS in self.tmp: (rc, what the stub saw or None if it didn't run, stderr). The stub, when it ran,
        is the process sh was started as: exec, never a child."""
        p = subprocess.Popen([SH, path] + args, cwd=self.tmp, env=self.env() if env is None else env,
                             stdin=subprocess.PIPE if data is not None else subprocess.DEVNULL,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        out, err = p.communicate(data, timeout=60)
        seen = json.loads(out.decode()) if out else None
        if seen is not None:
            self.assertEqual(seen["pid"], p.pid, "not exec'd")
            self.assertFalse(seen["wrapper_set"], "NEXUS_WRAPPER left set")
        return p.returncode, seen, err.decode()

    def wrapped(self, args, text, rest, env=None, note="", file="w.md", data=None, path=SHIM, rc=0):
        """ARGS run grok as --rules=TEXT then REST, with NEXUS_WRAPPED the FILE's absolute path, NOTE on stderr."""
        got, seen, err = self.run_shim(args, env, data, path)
        self.assertIsNotNone(seen, (args, err))
        want = file if file.startswith("/") else os.path.join(self.tmp, file)
        self.assertEqual((got, seen["argv"], seen["wrapped"], err), (rc, ["--rules=" + text] + rest, want, note), args)
        return seen

    def through(self, args, rest, env=None, note="", data=None, path=SHIM, rc=0):
        """ARGS run grok as REST, without a wrapper: NEXUS_WRAPPED unset, NOTE on stderr."""
        got, seen, err = self.run_shim(args, env, data, path)
        self.assertIsNotNone(seen, (args, err))
        self.assertEqual((got, seen["argv"], seen["wrapped_set"], err), (rc, rest, False, note), args)
        return seen

    def dies(self, args, line, env=None):
        """ARGS stop the shim with LINE and exit 2; grok never runs."""
        got, seen, err = self.run_shim(args, env)
        self.assertEqual((got, seen, err), (2, None, line), args)


class Source(Shim):
    def test_both_flag_spellings_and_the_env(self):
        self.wrapped(["--wrapper=w.md", "-p", "hi"], "wrap", ["-p", "hi"])
        self.wrapped(["--wrapper", "w.md", "-p", "hi"], "wrap", ["-p", "hi"])
        self.wrapped(["-p", "hi", "--wrapper", "w.md"], "wrap", ["-p", "hi"])          # anywhere before a bare --
        self.wrapped(["-p", "hi"], "wrap", ["-p", "hi"], env=self.env(NEXUS_WRAPPER="w.md"), note="")
        self.wrapped([], "wrap", [], env=self.env(NEXUS_WRAPPER="w.md"))
        # the flag beats the env
        self.wrapped(["--wrapper=g.md"], "other", [], env=self.env(NEXUS_WRAPPER="w.md"), file="g.md")
        self.wrapped(["--wrapper", "g.md"], "other", [], env=self.env(NEXUS_WRAPPER="w.md"), file="g.md")

    def test_no_wrapper(self):
        self.through(["--no-wrapper", "-p", "hi"], ["-p", "hi"], env=self.env(NEXUS_WRAPPER="w.md"))
        self.through(["-p", "hi", "--no-wrapper"], ["-p", "hi"], env=self.env(NEXUS_WRAPPER="w.md"))
        self.through(["--no-wrapper"], [])
        # an empty NEXUS_WRAPPER is none; nothing at all runs grok as asked
        self.through(["-p", "hi"], ["-p", "hi"], env=self.env(NEXUS_WRAPPER=""))
        self.through(["-p", "hi"], ["-p", "hi"])
        # a missing FILE in the env is never looked at once dropped
        self.through(["--no-wrapper"], [], env=self.env(NEXUS_WRAPPER="nothing.md"))

    def test_refused_words(self):
        for args in (["--wrapper=w.md", "--wrapper=g.md"], ["--wrapper", "w.md", "--wrapper", "g.md"],
                     ["--wrapper=w.md", "--wrapper", "w.md"], ["--wrapper", "w.md", "--wrapper=w.md"],
                     ["--wrapper=w.md", "--wrapper=g.md", "update"]):     # a syntax error beats a pass-through
            self.dies(args, TWICE)
        for args in (["--wrapper=w.md", "--no-wrapper"], ["--no-wrapper", "--wrapper", "w.md"]):
            self.dies(args, BOTH)
        for args in (["--wrapper"], ["-p", "hi", "--wrapper"], ["--wrapper", ""], ["--wrapper="],
                     ["--wrapper", "--"], ["--wrapper", "-x"], ["--wrapper", "-"], ["--wrapper", "--no-wrapper"],
                     ["--wrapper=", "update"], ["--wrapper", "", "--version"]):
            self.dies(args, NEEDS)
        self.dies(["--wrapper", "-x"], NEEDS, env=self.env(NEXUS_WRAPPER="w.md"))

    def test_a_file_named_with_a_dash(self):
        self.write("-x", b"dash x\n")
        self.wrapped(["--wrapper=-x"], "dash x", [], file="-x")
        self.wrapped(["--wrapper", "./-x"], "dash x", [], file="-x")

    def test_after_a_bare_dashdash(self):
        # The shim's words end at a bare --: what follows goes to grok as it is, the -- too.
        self.wrapped(["--wrapper=w.md", "--", "--wrapper=g.md"], "wrap", ["--", "--wrapper=g.md"])
        self.wrapped(["--", "--wrapper=g.md", "--no-wrapper", "--help", "update"], "wrap",
                     ["--", "--wrapper=g.md", "--no-wrapper", "--help", "update"], env=self.env(NEXUS_WRAPPER="w.md"))
        self.wrapped(["--wrapper", "w.md", "--", "update"], "wrap", ["--", "update"])
        self.wrapped(["--wrapper=w.md", "-p", "--", "update"], "wrap", ["-p", "--", "update"])   # -- is no value
        self.wrapped(["--wrapper=w.md", "--", "--rules", "R"], "wrap", ["--", "--rules", "R"])
        self.through(["--", "--wrapper=w.md"], ["--", "--wrapper=w.md"])
        self.through(["--", "--wrapper"], ["--", "--wrapper"])


class Checks(Shim):
    def test_what_a_file_must_be(self):
        os.mkdir(os.path.join(self.tmp, "dir"))
        self.write("empty.md", b"")
        self.write("newlines.md", b"\n\n\n")
        self.write("nul.md", b"rule\0rule\n")
        for name, why in (("nothing.md", MISSING), ("dir", NOT_REGULAR), ("empty.md", EMPTY),
                          ("newlines.md", EMPTY), ("nul.md", HAS_NUL)):
            self.dies(["--wrapper=" + name, "-p", "hi"], refused(name, why))
            self.dies(["--wrapper", name], refused(name, why))
            self.dies(["-p", "hi"], refused(name, why, env=True), env=self.env(NEXUS_WRAPPER=name))
        self.dies(["--wrapper=sub/nothing.md"], refused("sub/nothing.md", MISSING))
        self.dies(["--wrapper=" + os.path.join(self.tmp, "nothing.md")],
                  refused(os.path.join(self.tmp, "nothing.md"), MISSING))

    @unittest.skipIf(os.path.exists(PERSONA_WRAPPER), "this host has a persona's wrapper")
    def test_the_persona_s_own_file_missing(self):
        # NEXUS_WRAPPER from nexus --config names the copy mounted there: missing, nexus --config writes it again.
        w = PERSONA_WRAPPER
        self.dies(["-p", "hi"], refused(w, MISSING_PERSONA, env=True), env=self.env(NEXUS_WRAPPER=w))
        self.dies(["--wrapper=" + w], refused(w, MISSING_PERSONA))
        self.through(["-c"], ["-c"], env=self.env(NEXUS_WRAPPER=w), note=resumed_without(w, MISSING_PERSONA))
        # any other path keeps the hint about copying a file in
        self.dies(["-p", "hi"], refused("/etc/grok/other.md", MISSING, env=True),
                  env=self.env(NEXUS_WRAPPER="/etc/grok/other.md"))

    def test_unreadable(self):
        if os.geteuid() == 0:
            self.skipTest("root reads any file")
        os.chmod(self.write("locked.md", b"rule\n"), 0)
        self.dies(["--wrapper=locked.md"], refused("locked.md", UNREADABLE))
        self.dies([], refused("locked.md", UNREADABLE, env=True), env=self.env(NEXUS_WRAPPER="locked.md"))

    def test_strict_utf8(self):
        # As strict as grok's argv is: glibc's iconv lets 5-byte and over-U+10FFFF sequences through.
        for i, data in enumerate((b"\xff\n", b"caf\xe9\n", b"\xc0\xaf\n", b"\xf8\x88\x80\x80\x80\n",
                                  b"\xf4\x90\x80\x80\n", b"\xed\xa0\x80\n", b"ok \xe2\x82\n")):
            name = "bad%d.md" % i
            self.write(name, data)
            self.dies(["--wrapper=" + name], refused(name, NOT_UTF8))
        # multibyte that is valid goes in as it is
        self.write("utf8.md", u"règle € \U0001f600\n".encode("utf-8"))
        self.wrapped(["--wrapper=utf8.md"], u"règle € \U0001f600", [], file="utf8.md")

    def test_without_python3(self):
        tools = [shutil.which(t) for t in ("wc", "cat", "dirname")]
        if not all(tools):
            self.skipTest("no wc, cat or dirname on PATH")
        bindir = os.path.join(self.tmp, "bin")
        os.mkdir(bindir)
        for t in tools:
            os.symlink(t, os.path.join(bindir, os.path.basename(t)))
        self.dies(["--wrapper=w.md"], refused("w.md", NO_PYTHON), env=self.env(PATH=bindir))

    def test_the_size_limit_alone(self):
        # bytes(FILE) + 2 <= 122880: a file alone may be 122878 bytes, counted in bytes (2-byte characters here),
        # also where wc -m would count characters (personas run with LANG=C.UTF-8)
        self.write("limit.md", b"\xc3\xa9" * 61439)
        self.write("over1.md", b"\xc3\xa9" * 61439 + b"a")
        for lc in ("C", "C.UTF-8"):
            env = self.env(LC_ALL=lc)
            self.wrapped(["--wrapper=limit.md"], u"é" * 61439, [], env=env, file="limit.md")
            self.dies(["--wrapper=over1.md"], refused("over1.md", too_big(122879)), env=env)
        self.write("over2.md", b"\xc3\xa9" * 61439 + b"ab")
        self.dies(["--wrapper=over2.md"], refused("over2.md", too_big(122880)))
        self.dies([], refused("over2.md", too_big(122880), env=True), env=self.env(NEXUS_WRAPPER="over2.md"))
        # the trailing newline is in the count, though not in the text
        self.write("nl.md", b"x" * 122877 + b"\n")
        self.wrapped(["--wrapper=nl.md"], "x" * 122877, [], file="nl.md")
        self.write("nl2.md", b"x" * 122878 + b"\n")
        self.dies(["--wrapper=nl2.md"], refused("nl2.md", too_big(122879)))
        # size first: a file too big is refused unread, whatever else is wrong with it
        self.write("big.md", b"\xff" * 122879)
        self.dies(["--wrapper=big.md"], refused("big.md", too_big(122879)))

    def test_the_size_limit_with_user_rules(self):
        # bytes(FILE) + bytes(user rules, joined by a blank line) + 2 <= 122880, bytes whatever the locale.
        self.write("ten.md", b"wrapper!!\n")
        for lc in ("C", "C.UTF-8"):
            env = self.env(LC_ALL=lc)
            rules = "r" * (LIMIT - 12)
            self.wrapped(["--wrapper=ten.md", "--rules", rules], "wrapper!!\n\n" + rules, [], env=env, file="ten.md")
            self.dies(["--wrapper=ten.md", "--rules", rules + "r"], refused("ten.md", too_big(10, LIMIT - 11)),
                      env=env)
            rules = u"é" * ((LIMIT - 12) // 2)
            self.wrapped(["--wrapper=ten.md", "--rules=" + rules], "wrapper!!\n\n" + rules, [], env=env,
                         file="ten.md")
            self.dies(["--wrapper=ten.md", "--rules=" + rules + "a"], refused("ten.md", too_big(10, LIMIT - 11)),
                      env=env)
            # two rules: their blank line is counted too
            r1, r2 = "a" * 61000, "b" * (LIMIT - 12 - 61000 - 2)
            self.wrapped(["--wrapper=ten.md", "--rules", r1, "--append-system-prompt=" + r2],
                         "wrapper!!\n\n" + r1 + "\n\n" + r2, [], env=env, file="ten.md")
            self.dies(["--wrapper=ten.md", "--rules", r1, "--append-system-prompt=" + r2 + "b"],
                      refused("ten.md", too_big(10, LIMIT - 11)), env=env)


class Text(Shim):
    def test_one_word_put_first(self):
        self.write("fm.md", b"---\nname: x\n---\nbody\n")
        self.wrapped(["--wrapper=fm.md", "-p", "hi"], "---\nname: x\n---\nbody", ["-p", "hi"], file="fm.md")
        self.write("bullets.md", b"- be terse\n- no fluff\n\n\n")
        self.wrapped(["-p", "hi", "--wrapper", "bullets.md", "--trust"], "- be terse\n- no fluff",
                     ["-p", "hi", "--trust"], file="bullets.md")
        self.write("inner.md", b"\na\n\n\nb\n\n")
        self.wrapped(["--wrapper=inner.md"], "\na\n\n\nb", [], file="inner.md")

    def test_the_users_rules_are_merged(self):
        for args in (["--rules=be brief", "-p", "hi"], ["--rules", "be brief", "-p", "hi"],
                     ["-p", "hi", "--append-system-prompt=be brief"],
                     ["--append-system-prompt", "be brief", "-p", "hi"]):
            self.wrapped(["--wrapper=w.md"] + args, "wrap\n\nbe brief", ["-p", "hi"])
            self.wrapped(args, "wrap\n\nbe brief", ["-p", "hi"], env=self.env(NEXUS_WRAPPER="w.md"))
            # without a wrapper, they go to grok as they are
            self.through(args, args)
            self.through(["--no-wrapper"] + args, args, env=self.env(NEXUS_WRAPPER="w.md"))
        self.wrapped(["--rules", "one", "--wrapper=w.md", "--append-system-prompt=two", "hi"],
                     "wrap\n\none\n\ntwo", ["hi"])
        # --rules with nothing after it, or before a bare --, is left for grok to refuse
        self.wrapped(["--wrapper=w.md", "--rules"], "wrap", ["--rules"])
        self.wrapped(["--wrapper=w.md", "--rules", "--", "x"], "wrap", ["--rules", "--", "x"])

    def test_a_word_starting_with_a_dash_is_no_rule(self):
        # As clap reads them (WF2 case 8): --rules/--append-system-prompt take the next word unless it starts with -
        # (a lone - is a rule). Untaken, the option and the word go on as they are, the word read as any other, and
        # grok refuses the call as it would without the shim. (This replaced the pins that took '- be terse' and
        # '---' as rules: with them a wrapped `grok --rules --version` started a session that plain grok refuses.)
        for opt in ("--rules", "--append-system-prompt"):
            for env, wrapper, note in ((None, ["--wrapper=w.md"], ""), (self.env(NEXUS_WRAPPER="w.md"), [], "")):
                for args in ([opt, "- be terse", "-p", "hi"], [opt, "---", "hi"], [opt, "-x"], [opt, "-p", "hi"],
                             [opt, "--", "x"], [opt, "--trust", "-p", "hi"], ["-p", "hi", opt]):
                    self.wrapped(wrapper + args, "wrap", args, env=env, note=note)
                # the word left is read as any other: -c resumes, --version passes through
                self.wrapped(wrapper + [opt, "-c"], "wrap", [opt, "-c"], env=env,
                             note=resumed("w.md", env=env is not None))
                self.through(wrapper + [opt, "--version"], [opt, "--version"], env=env,
                             note=HELP if wrapper else "")
                # a lone - is a rule; so is a word after a rule's -, and the rule is merged
                self.wrapped(wrapper + [opt, "-", "hi"], "wrap\n\n-", ["hi"], env=env)
                self.wrapped(wrapper + [opt, "a - b", "hi"], "wrap\n\na - b", ["hi"], env=env)
                # beside a taken one: only the taken one is merged
                self.wrapped(wrapper + ["--rules=one", opt, "-x", "--append-system-prompt", "two"],
                             "wrap\n\none\n\ntwo", [opt, "-x"], env=env)
            # without a wrapper, all go to grok as they are
            for args in ([opt, "- be terse", "-p", "hi"], [opt, "-x"], [opt, "--", "x"]):
                self.through(args, args)
                self.through(["--no-wrapper"] + args, args, env=self.env(NEXUS_WRAPPER="w.md"))
            # a short cluster ends at a letter that is none of grok's shorts: '- be terse' resumes nothing for its r
            self.wrapped(["--wrapper=w.md", opt, "-xc"], "wrap", [opt, "-xc"])

    def test_the_shims_words_after_a_rules_option(self):
        # --rules never takes a word that starts with -, so --wrapper and --no-wrapper after it are the shim's (as
        # nexus's own -- … --wrapper reads them); the --rules goes on alone, for grok to refuse.
        for opt in ("--rules", "--append-system-prompt"):
            self.wrapped([opt, "--wrapper", "w.md", "-p", "hi"], "wrap", [opt, "-p", "hi"])
            self.wrapped([opt, "--wrapper=w.md"], "wrap", [opt])
            self.wrapped([opt, "--wrapper", "g.md"], "other", [opt], env=self.env(NEXUS_WRAPPER="w.md"), file="g.md")
            self.through([opt, "--no-wrapper", "-p", "hi"], [opt, "-p", "hi"], env=self.env(NEXUS_WRAPPER="w.md"))
            self.dies([opt, "--wrapper"], NEEDS)


class PassThrough(Shim):
    def test_the_lists_are_there(self):
        # The words a check holds against a newer grok's help (that check, with the real binary, is step 2's).
        text, found = lists()
        self.assertEqual(sorted(found), ["hidden value options", "optional-value options", "subcommands",
                                         "value options"])
        for label, block in found.items():
            self.assertEqual((text.count("# BEGIN grok %s\n" % label), text.count("# END grok %s\n" % label)),
                             (1, 1), label)
            self.assertIsNotNone(block, label)
            self.assertRegex(block[0], r"^[a-z_]+='$", label)
            self.assertEqual(block[-1], "'", label)
            w = words(block)
            self.assertEqual(w, block[1:-1], label)
            self.assertTrue(w, label)
            self.assertEqual(len(w), len(set(w)), label)
        for w in SUBCOMMANDS:
            self.assertRegex(w, r"^[a-z][a-z-]*$")
        for w in VALUE_OPTIONS + OPTIONAL_VALUE_OPTIONS:
            self.assertRegex(w, r"^(-[A-Za-z]|--[A-Za-z][A-Za-z-]*)$")
        self.assertEqual(set(SUBCOMMANDS) & set(VALUE_OPTIONS + OPTIONAL_VALUE_OPTIONS), set())
        self.assertEqual(set(VALUE_OPTIONS) & set(OPTIONAL_VALUE_OPTIONS), set())
        # The hidden value options: a block of their own, so the visible ones stay equal to grok help's; long ones
        # only (the inventory found no hidden short), none in another list.
        for w in HIDDEN_VALUE_OPTIONS:
            self.assertRegex(w, r"^--[A-Za-z][A-Za-z-]*$")
        self.assertEqual(set(HIDDEN_VALUE_OPTIONS) & set(SUBCOMMANDS + VALUE_OPTIONS + OPTIONAL_VALUE_OPTIONS), set())
        self.assertEqual(set(HIDDEN_KNOWN) - set(HIDDEN_VALUE_OPTIONS), set())
        self.assertIn("--append-system-prompt", HIDDEN_VALUE_OPTIONS)
        self.assertEqual(OPTIONAL_VALUE_OPTIONS, ["-r", "--resume", "-w", "--worktree"])
        for w in ("update", "agent", "dashboard", "help", "version", "v", "disk-usage", "inspect", "sessions"):
            self.assertIn(w, SUBCOMMANDS)
        for w in ("-p", "--single", "-m", "--model", "--agent", "--cwd", "--rules", "--prompt-file", "--effort"):
            self.assertIn(w, VALUE_OPTIONS)

    def test_every_subcommand(self):
        for word in SUBCOMMANDS:
            self.through(["--wrapper=w.md", word, "x"], [word, "x"], note=sub(word))
            self.through([word, "--wrapper", "w.md"], [word], note=sub(word))
            # from the env: quietly, and the user's rules stay grok's to read
            self.through([word, "--rules", "R"], [word, "--rules", "R"], env=self.env(NEXUS_WRAPPER="w.md"))
            self.through(["--no-wrapper", word], [word], env=self.env(NEXUS_WRAPPER="w.md"))
        # a word is matched whole: one with a newline is no subcommand, though each of its lines is
        self.wrapped(["--wrapper=w.md", "du\ndisk-usage"], "wrap", ["du\ndisk-usage"])

    def test_help_and_version(self):
        for flag in ("-h", "--help", "-v", "-V", "--version"):
            self.through(["--wrapper=w.md", flag], [flag], note=HELP)
            self.through(["-p", "hi", flag, "--wrapper", "w.md", "--rules=R"], ["-p", "hi", flag, "--rules=R"],
                         note=HELP)
            self.through([flag], [flag], env=self.env(NEXUS_WRAPPER="w.md"))
            self.through(["--no-wrapper", flag], [flag], env=self.env(NEXUS_WRAPPER="w.md"))
        # in a short cluster, before the letter that takes a value
        self.through(["--wrapper=w.md", "-ch"], ["-ch"], note=HELP)
        # a value option never takes a word that starts with - (clap's default)
        self.through(["--wrapper=w.md", "-p", "--help"], ["-p", "--help"], note=HELP)
        self.through(["--wrapper=w.md", "-r", "-h"], ["-r", "-h"], note=HELP)
        # the first reason found is the one given
        self.through(["--wrapper=w.md", "--version", "update"], ["--version", "update"], note=HELP)
        self.through(["--wrapper=w.md", "update", "--version"], ["update", "--version"], note=sub("update"))

    def test_the_file_isnt_looked_at(self):
        self.through(["--wrapper=nothing.md", "update"], ["update"], note=sub("update"))
        self.through(["update"], ["update"], env=self.env(NEXUS_WRAPPER="nothing.md"))
        self.through(["--wrapper", "nothing.md", "--version"], ["--version"], note=HELP)

    def test_grok_s_exit_status(self):
        env = self.env(NXW_STUB_EXIT="3")
        self.through(["update"], ["update"], env=env, rc=3)
        self.wrapped(["--wrapper=w.md", "-p", "hi"], "wrap", ["-p", "hi"], env=env, rc=3)

    def test_an_options_value_is_no_subcommand(self):
        for args in (["-p", "update"], ["--single", "update"], ["-pupdate"], ["--single=update"],
                     ["--agent", "worker"], ["-m", "v"], ["-mv"], ["--model=v"], ["--cwd", "export"],
                     ["--effort", "update"], ["-p", "-", "--model", "help"], ["-ph"], ["-w", "update"],
                     ["--worktree", "update"]):
            self.wrapped(["--wrapper=w.md"] + args, "wrap", args)
            self.wrapped(args, "wrap", args, env=self.env(NEXUS_WRAPPER="w.md"))
        # each listed option, in each of its spellings, takes the next word (or the rest of a short one's word)
        for opt in VALUE_OPTIONS + OPTIONAL_VALUE_OPTIONS:
            spellings = [[opt, "update"]] + ([[opt + "update"]] if len(opt) == 2 else [[opt + "=update"]])
            if len(opt) == 2:
                spellings.append(["-c" + opt[1], "update"])       # a cluster's value letter takes the next word
            for args in spellings:
                note = resumed("w.md") if opt in ("-r", "--resume") or args[0].startswith("-c") else ""
                if opt == "--rules":                                  # merged into the text, not passed on
                    self.wrapped(["--wrapper=w.md"] + args, "wrap\n\nupdate", [])
                else:
                    self.wrapped(["--wrapper=w.md"] + args, "wrap", args, note=note)
        # each hidden one too (WF2: --print update ran a session unwrapped), both spellings, from the flag or the env
        self.assertTrue(HIDDEN_VALUE_OPTIONS)
        for opt in HIDDEN_VALUE_OPTIONS:
            for args in ([opt, "update"], [opt + "=update"], [opt, "sessions", "-p", "hi"]):
                for env in (None, self.env(NEXUS_WRAPPER="w.md")):
                    wrapper = [] if env else ["--wrapper=w.md"]
                    if opt == "--append-system-prompt":               # merged into the text, as --rules is
                        value, rest = (args[0].split("=", 1)[1], args[1:]) if "=" in args[0] else (args[1], args[2:])
                        self.wrapped(wrapper + args, "wrap\n\n" + value, rest, env=env)
                    else:
                        note = resumed("w.md", env=bool(env)) if opt == "--load" else ""
                        self.wrapped(wrapper + args, "wrap", args, env=env, note=note)
            # and a word starting with - is no value of theirs
            self.through(["--wrapper=w.md", opt, "--version"], [opt, "--version"], note=HELP)
        # once a value is taken, the next plain word counts again
        for args in (["-pupdate", "update"], ["--model=x", "update"], ["-mx", "-p", "-", "update"],
                     ["-p", "hi", "update"], ["--agent", "a", "worker"],
                     # a long flag that takes no value leaves the next word alone
                     ["--always-approve", "update"], ["--debug", "sessions"]):
            word = args[-1]
            self.through(["--wrapper=w.md"] + args, args, note=sub(word))
            self.through(args, args, env=self.env(NEXUS_WRAPPER="w.md"))


class Resume(Shim):
    def test_a_resume_gets_a_note(self):
        for args in (["-c"], ["--continue"], ["-r"], ["--resume"], ["--resume=abc"], ["-r", "abc"],
                     ["--fork-session", "-r", "abc"], ["-c", "--fork-session"], ["-cp", "hi"], ["-cpupdate"],
                     ["-r", "--", "update"], ["--resume", "help"], ["-r", "update"], ["-p", "hi", "-c"],
                     # --load, a hidden grok option that resumes too (its value required)
                     ["--load", "abc"], ["--load=abc"], ["--load", "update"], ["-p", "hi", "--load", "abc"]):
            self.wrapped(["--wrapper=w.md"] + args, "wrap", args, note=resumed("w.md"))
            self.wrapped(args, "wrap", args, env=self.env(NEXUS_WRAPPER="w.md"), note=resumed("w.md", env=True))
        # -pc is -p with the value "c": no resume
        self.wrapped(["--wrapper=w.md", "-pc", "hi"], "wrap", ["-pc", "hi"])
        # --fork-session alone is none either (grok's help: it applies to --resume / --continue)
        self.wrapped(["--wrapper=w.md", "--fork-session", "-p", "hi"], "wrap", ["--fork-session", "-p", "hi"])
        self.wrapped(["--fork-session"], "wrap", ["--fork-session"], env=self.env(NEXUS_WRAPPER="w.md"))
        self.wrapped(["--wrapper=w.md", "-p", "-c"], "wrap", ["-p", "-c"], note=resumed("w.md"))
        # a cluster ends at a letter that is none of grok's shorts (clap refuses it): no resume after it
        self.wrapped(["--wrapper=w.md", "-xc"], "wrap", ["-xc"])
        self.wrapped(["--wrapper=w.md", "-cx"], "wrap", ["-cx"], note=resumed("w.md"))

    def test_an_unusable_file_from_the_env_only_warns(self):
        self.write("empty.md", b"\n")
        self.write("nul.md", b"\0")
        unusable = [("nothing.md", MISSING), ("empty.md", EMPTY), ("nul.md", HAS_NUL)]
        if os.geteuid() != 0:                                         # root reads any file
            os.chmod(self.write("locked.md", b"rule\n"), 0)
            unusable.append(("locked.md", UNREADABLE))
        for name, why in unusable:
            env = self.env(NEXUS_WRAPPER=name, NEXUS_WRAPPED="/somewhere/else.md")
            for args in (["-c"], ["--continue", "-p", "hi"], ["-r", "abc"], ["--resume=abc"],
                         ["--fork-session", "-c"], ["-cp", "hi"], ["--load", "abc"], ["--load=abc"]):
                self.through(args, args, env=env, note=resumed_without(name, why))
            # not a resume (--fork-session alone too): refused
            self.dies(["-pc", "hi"], refused(name, why, env=True), env=env)
            self.dies(["--fork-session", "-p", "hi"], refused(name, why, env=True), env=env)
            # from the flag: refused, resume or not
            self.dies(["--wrapper=" + name, "-c"], refused(name, why))


class Leader(Shim):
    def test_a_wrapped_leader_call_is_warned(self):
        # The leader gives every session it serves its first client's rules (WF2 case 14): one line, grok runs as
        # asked, no --no-leader put in.
        for args in (["--leader"], ["--leader", "-p", "hi"], ["-p", "hi", "--leader", "--leader-socket", "s"],
                     ["-p", "--leader"]):
            self.wrapped(["--wrapper=w.md"] + args, "wrap", args, note=leader("w.md"))
            self.wrapped(args, "wrap", args, env=self.env(NEXUS_WRAPPER="w.md"), note=leader("w.md", env=True))
        # after the resume note
        self.wrapped(["--wrapper=w.md", "--leader", "-c"], "wrap", ["--leader", "-c"],
                     note=resumed("w.md") + leader("w.md"))
        # with the user's rules merged
        self.wrapped(["--wrapper=w.md", "--leader", "--rules", "R"], "wrap\n\nR", ["--leader"], note=leader("w.md"))

    def test_no_warning_otherwise(self):
        env = self.env(NEXUS_WRAPPER="w.md")
        # without a wrapper
        self.through(["--leader", "-p", "hi"], ["--leader", "-p", "hi"])
        self.through(["--no-wrapper", "--leader"], ["--leader"], env=env)
        # without --leader: other words that look like it
        for args in (["--leader-socket", "s"], ["--leader-socket=s"], ["--no-leader"], ["-p", "--leader-x"],
                     ["--", "--leader"], ["--rules=--leader"], ["--prompt-json", "--leader=x"]):
            self.wrapped(["--wrapper=w.md"] + args, "wrap\n\n--leader" if args[0].startswith("--rules=") else "wrap",
                         [] if args[0].startswith("--rules=") else args)
        # a call that passes through: no wrapper, no warning
        self.through(["--wrapper=w.md", "--leader", "update"], ["--leader", "update"], note=sub("update"))
        self.through(["leader", "list"], ["leader", "list"], env=env)
        self.through(["--leader", "--version"], ["--leader", "--version"], env=env)


class Env(Shim):
    def test_nexus_wrapped(self):
        # unset unless a wrapper is on, whatever came in
        env = self.env(NEXUS_WRAPPED="/somewhere/else.md")
        self.through(["-p", "hi"], ["-p", "hi"], env=env)
        self.through(["--wrapper=w.md", "update"], ["update"], env=env, note=sub("update"))
        self.through(["--no-wrapper"], [], env=dict(env, NEXUS_WRAPPER="w.md"))
        self.wrapped(["--wrapper=w.md"], "wrap", [], env=env)
        # the FILE's absolute path: from the cwd when relative, as given when absolute
        self.write("sub/s.md", b"in sub\n")
        self.wrapped(["--wrapper=sub/s.md"], "in sub", [], file="sub/s.md")
        self.wrapped(["--wrapper=./w.md"], "wrap", [], file="w.md")
        self.wrapped(["--wrapper=sub/../w.md"], "wrap", [], file="w.md")
        absolute = os.path.join(self.tmp, "sub", "..", "w.md")
        self.wrapped(["--wrapper=" + absolute], "wrap", [], file=absolute)
        self.wrapped([], "in sub", [], env=self.env(NEXUS_WRAPPER=os.path.join(self.tmp, "sub/s.md")),
                     file=os.path.join(self.tmp, "sub/s.md"))

    def test_a_directory_named_dash(self):
        # cd takes - for $OLDPWD: here a directory that has a -/w.md too, so a slip shows.
        self.write("-/w.md", b"in dash\n")
        self.write("other/-/w.md", b"elsewhere\n")
        env = self.env(OLDPWD=os.path.join(self.tmp, "other"))
        self.wrapped(["--wrapper=-/w.md"], "in dash", [], env=env, file="-/w.md")
        self.wrapped(["--wrapper", "./-/w.md"], "in dash", [], env=env, file="-/w.md")
        self.wrapped([], "in dash", [], env=dict(env, NEXUS_WRAPPER="-/w.md"), file="-/w.md")
        self.dies(["--wrapper", "-/w.md"], NEEDS, env=env)

    def test_cdpath(self):
        # A CDPATH match would send cd elsewhere, and cd would print where: NEXUS_WRAPPED is one line, the cwd's.
        self.write("sub/w.md", b"here\n")
        self.write("other/sub/w.md", b"there\n")
        for cdpath in (os.path.join(self.tmp, "other"), os.path.join(self.tmp, "other") + ":."):
            seen = self.wrapped(["--wrapper", "sub/w.md"], "here", [], env=self.env(CDPATH=cdpath), file="sub/w.md")
            self.assertEqual(seen["wrapped"].splitlines(), [os.path.join(self.tmp, "sub", "w.md")])

    def test_a_file_named_dash(self):
        # Read as a file, never as stdin: the prompt on stdin is still grok's.
        self.write("-", b"FILERULES\n")
        for args, env in ((["--wrapper=-", "--prompt-file", "/dev/stdin"], None),
                          (["--wrapper", "./-", "--prompt-file", "/dev/stdin"], None),
                          (["--prompt-file", "/dev/stdin"], self.env(NEXUS_WRAPPER="-"))):
            seen = self.wrapped(args, "FILERULES", ["--prompt-file", "/dev/stdin"], env=env, data=b"PROMPT",
                                file="-")
            self.assertEqual(seen["stdin"], "PROMPT")
        self.write("-", b"FILE\0RULES\n")
        got, seen, err = self.run_shim(["--wrapper=-"], data=b"PROMPT")
        self.assertEqual((got, seen, err), (2, None, refused("-", HAS_NUL)))
        os.unlink(os.path.join(self.tmp, "-"))
        got, seen, err = self.run_shim(["--wrapper=-"], data=b"PROMPT")
        self.assertEqual((got, seen, err), (2, None, refused("-", MISSING)))


class Argv0(Shim):
    def link(self, name):
        path = os.path.join(self.tmp, name)
        os.symlink(SHIM, path)
        return path

    def test_the_shim_works_under_either_name(self):
        for name in ("agent", "grok"):
            path = self.link(name)
            seen = self.wrapped(["--wrapper=w.md", "-p", "hi"], "wrap", ["-p", "hi"], path=path)
            self.assertEqual(seen["argv0"], self.stub)                  # NEXUS_GROK_REAL, whatever the name
            self.through(["--wrapper=w.md", "update"], ["update"], path=path, note=sub("update"))

    @unittest.skipIf(os.path.exists(DEFAULT_BIN), "a real grok is installed here")
    def test_which_binary(self):
        # Without NEXUS_GROK_REAL: agent runs .../bin/agent, any other name .../bin/grok. Absent here, so the shell's
        # error names the path it tried.
        for name, want in (("agent", "agent"), ("grok", "grok"), ("grok-mode.sh", "grok"), ("agent.sh", "grok")):
            path = SHIM if name == "grok-mode.sh" else self.link(name)
            for args in (["--version"], ["--wrapper=w.md", "-p", "hi"]):
                got, seen, err = self.run_shim(args, env=self.env(NEXUS_GROK_REAL=None), path=path)
                tried = re.findall(re.escape(DEFAULT_BIN) + r"/[a-z]+", err)
                self.assertEqual((got, seen, tried), (127, None, [DEFAULT_BIN + "/" + want]), (name, err))


if __name__ == "__main__":
    unittest.main()
