import os
import shutil
import subprocess
import tempfile
import unittest

from agentctl.core import dotenv

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))

# The bash reader both tools had (config.sh's get_kv, nexus's env_get): the reference for get().
BASH_GET = r'''f="$1" k="$2"
[[ -f "$f" ]] || exit 0
sed -nE "s/\r\$//; s/^[[:space:]]*(export[[:space:]]+)?$k[[:space:]]*=[[:space:]]*//p" "$f" | tail -n 1 \
  | sed -E 's/^"((\\.|[^"\\])*)".*/\1/; t dq; s/^\x27((\\.|[^\x27\\])*)\x27.*/\1/; t sq; s/[[:space:]]+#.*//; s/[[:space:]]+$//; b
            :sq; s/\\\x27/\x27/g; b
            :dq; s/\\\\/\x01/g; s/\\n/\n/g; s/\\t/\t/g; s/\\r/\r/g; s/\\([$"])/\1/g; s/\x01/\\/g'
'''

FILES = [
    "K=plain\n",
    "K=a\nK=b\n",
    "export K = spaced  \n",
    "K=crlf\r\n",
    "K=\"dq \\\\ \\n \\t \\r \\$ \\\" \\x\" # after\n",
    "K='sq \\' x' tail\n",
    "K=bare # comment\n",
    "K=bare#nocomment\n",
    "K=\"unterminated\n",
    "K='unterminated\n",
    "# K=commented\n",
    "OTHER=1\n",
    "K=last line without a newline",
    "K=\"ends with a newline\\n\"\n",
    "  K\t=\t'tabs'\n",
    "",
]


class Get(unittest.TestCase):
    def test_cases(self):
        self.assertEqual(dotenv.get_text("K=plain\n", "K"), "plain")
        self.assertEqual(dotenv.get_text("K=a\nK=b\n", "K"), "b")
        self.assertEqual(dotenv.get_text("K='it' x\n", "K"), "it")
        self.assertEqual(dotenv.get_text("K=a # c\n", "K"), "a")
        self.assertEqual(dotenv.get_text("K=a#c\n", "K"), "a#c")
        self.assertEqual(dotenv.get_text("# K=x\n", "K"), "")

    def test_missing_file(self):
        self.assertEqual(dotenv.get("/no/such/.env", "K"), "")

    @unittest.skipUnless(shutil.which("bash") and shutil.which("sed"), "no bash or sed here")
    def test_as_the_bash_reader_did(self):
        with tempfile.TemporaryDirectory() as d:
            f = os.path.join(d, ".env")
            for text in FILES:
                with open(f, "w") as fh:
                    fh.write(text)
                want = subprocess.run(["bash", "-c", BASH_GET, "bash", f, "K"], stdout=subprocess.PIPE
                                      ).stdout.decode("utf-8", "surrogateescape").rstrip("\n")
                self.assertEqual(dotenv.get(f, "K"), want, repr(text))


class Updated(unittest.TestCase):
    def test_replaces_the_last_active_line(self):
        self.assertEqual(dotenv.updated("K=1\nX=2\nexport K =3\n", "K", "K=4"), "K=1\nX=2\nK=4\n")

    def test_else_the_first_commented_one(self):
        self.assertEqual(dotenv.updated("# K=1\n#K=2\n", "K", "K=3"), "K=3\n#K=2\n")

    def test_else_appends(self):
        self.assertEqual(dotenv.updated("X=1", "K", "K=2"), "X=1\nK=2\n")
        self.assertEqual(dotenv.updated("", "K", "K=2"), "K=2\n")

    def test_keeps_other_lines_crs(self):
        self.assertEqual(dotenv.updated("X=1\r\nK=2\r\n", "K", "K=3"), "X=1\r\nK=3\n")

    @unittest.skipUnless(shutil.which("awk"), "no awk here")
    def test_as_the_awk_did(self):
        awk = ('BEGIN { k = ENVIRON["K"]; l = ENVIRON["L"] } { line[NR] = $0 } '
               '$0 ~ "^[ \\t]*(export[ \\t]+)?" k "[ \\t]*=" { act = NR } !com && $0 ~ "^# ?" k "=" { com = NR } '
               'END { at = act ? act : com; for (i = 1; i <= NR; i++) print (i == at ? l : line[i]); if (!at) print l }')
        for text in FILES + ["#K=x\n# K=y\nK=z\n", "  export\tK=1\r\n"]:
            want = subprocess.run(["awk", awk], input=text.encode(), stdout=subprocess.PIPE,
                                  env=dict(os.environ, K="K", L="K=new")).stdout.decode()
            self.assertEqual(dotenv.updated(text, "K", "K=new"), want, repr(text))


class Quote(unittest.TestCase):
    def test_forms(self):
        self.assertEqual(dotenv.quote(""), "")
        self.assertEqual(dotenv.quote("/home/u/projects"), "/home/u/projects")
        self.assertEqual(dotenv.quote("a b"), "a b")
        self.assertEqual(dotenv.quote(" a"), "' a'")
        self.assertEqual(dotenv.quote("a$b"), "'a$b'")
        self.assertEqual(dotenv.quote("it's"), '"it\'s"')
        self.assertEqual(dotenv.quote('a\\b"$'), '"a\\\\b\\"\\$"')

    def test_round_trip(self):
        for v in ["", "x", "a b", " lead", "trail ", "a#b", "a # b", "$HOME", "it's", 'q"q', "back\\slash",
                  "mix ' \" $ \\ #", "tab\tin"]:
            self.assertEqual(dotenv.get_text("K=%s\n" % dotenv.quote(v), "K"), v, repr(v))


if __name__ == "__main__":
    unittest.main()
