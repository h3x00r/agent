"""image/glab-credential, run with sh as git runs it, in front of a stand-in for glab (NEXUS_GLAB) that answers
`config get host` with the host each test has glab designate, and records what each `auth git-credential` got."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
HELPER = os.path.join(REPO, "image", "glab-credential")
TOKENS = ("GITLAB_TOKEN", "GITLAB_ACCESS_TOKEN", "OAUTH_TOKEN")

# The stand-in: `config get host` prints $NXW_HOST (exit $NXW_HOST_RC, nothing printed when it isn't 0); anything
# else is logged as a line of JSON to $NXW_LOG (its arguments, stdin, the token variables it sees) and answers as
# glab would with a token, exit $NXW_RC.
FAKE = r'''
import json, os, sys
e = os.environ
if sys.argv[1:3] == ["config", "get"]:
    with open(e["NXW_LOG"], "a") as f:
        f.write(json.dumps({"lookup": sys.argv[3:], "stdin": sys.stdin.read()}) + "\n")
    if e.get("NXW_HOST_RC", "0") == "0":
        print(e.get("NXW_HOST", ""))
    sys.exit(int(e.get("NXW_HOST_RC", "0")))
rec = {"args": sys.argv[1:], "stdin": sys.stdin.read(),
       "tokens": {k: e[k] for k in ("GITLAB_TOKEN", "GITLAB_ACCESS_TOKEN", "OAUTH_TOKEN") if k in e},
       "other": e.get("NXW_OTHER")}
with open(e["NXW_LOG"], "a") as f:
    f.write(json.dumps(rec) + "\n")
print("username=glab\npassword=" + rec["tokens"].get("GITLAB_TOKEN", "stored"))
sys.exit(int(e.get("NXW_RC", "0")))
'''


def request(host, *more):
    return "protocol=https\n" + ("host=%s\n" % host if host is not None else "") + "".join(m + "\n" for m in more)


class Helper(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="nxw-glabcred-")
        self.addCleanup(shutil.rmtree, self.tmp)
        self.fake, self.log = os.path.join(self.tmp, "glab"), os.path.join(self.tmp, "log")
        with open(self.fake, "w") as f:
            f.write("#!%s -I\n%s" % (sys.executable, FAKE))
        os.chmod(self.fake, 0o755)

    def run_helper(self, req, host, op="get", **env):
        """The helper's (exit code, stdout) for git's request REQ with glab designating HOST, and what glab got;
        its stderr in self.err."""
        if os.path.exists(self.log):
            os.remove(self.log)
        e = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "NEXUS_GLAB": self.fake, "NXW_LOG": self.log,
             "NXW_HOST": host, "NXW_OTHER": "kept", "GITLAB_TOKEN": "glpat-a", "GITLAB_ACCESS_TOKEN": "glpat-b",
             "OAUTH_TOKEN": "glpat-c"}
        e.update(env)
        r = subprocess.run(["sh", HELPER, op], input=req, env=e, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           universal_newlines=True, timeout=30)
        self.err = r.stderr
        with open(self.log) as f:
            calls = [json.loads(line) for line in f]
        lookups = [c for c in calls if "lookup" in c]
        self.assertEqual([c["lookup"] for c in lookups], [["host"]])
        self.assertEqual(lookups[0]["stdin"], "")                    # git's request isn't the lookup's
        return r.returncode, r.stdout, [c for c in calls if "args" in c]

    def test_the_host_glab_designates_gets_the_env_tokens(self):
        for asked, host in (("gitlab.com", "gitlab.com"), ("gitlab.example.org", "gitlab.example.org"),
                            ("gitlab.example.org", "https://GitLab.Example.org/"),   # as GITLAB_HOST may name it
                            ("GitLab.com", "gitlab.com"),
                            ("gitlab.example.org:8443", "gitlab.example.org:8443")):
            req = request(asked, "wwwauth[]=Basic realm=\"GitLab\"")
            rc, out, glab = self.run_helper(req, host)
            self.assertEqual((rc, out), (0, "username=glab\npassword=glpat-a\n"), (asked, host))
            self.assertEqual(glab, [{"args": ["auth", "git-credential", "get"], "stdin": req, "other": "kept",
                                     "tokens": {"GITLAB_TOKEN": "glpat-a", "GITLAB_ACCESS_TOKEN": "glpat-b",
                                                "OAUTH_TOKEN": "glpat-c"}}], (asked, host))

    def test_any_other_host_gets_glab_without_them(self):
        # A self-managed GitLab's token never goes to gitlab.com; a gitlab.com token never to a second GitLab.
        for asked, host in (("gitlab.com", "gitlab.example.org"), ("gitlab.example.org", "gitlab.com"),
                            ("gitlab.example.org", "gitlab.example.org:8443"),       # glab keeps the port too
                            ("gitlab.com", "gitlab.com.example.org"), ("evil.gitlab.com", "gitlab.com")):
            req = request(asked)
            rc, out, glab = self.run_helper(req, host)
            self.assertEqual((rc, out), (0, "username=glab\npassword=stored\n"), (asked, host))
            self.assertEqual(glab, [{"args": ["auth", "git-credential", "get"], "stdin": req, "other": "kept",
                                     "tokens": {}}], (asked, host))

    def test_no_answer_to_compare_strips_them(self):
        for req, env in ((request("gitlab.com"), {"NXW_HOST_RC": "1"}),     # the lookup failed
                         (request(None), {}),                               # no host= line
                         (request(None), {"NXW_HOST": ""})):
            rc, out, glab = self.run_helper(req, "gitlab.com", **env)
            self.assertEqual(glab[0]["tokens"], {}, (req, env))

    def test_store_and_erase_alike_and_glabs_exit_code(self):
        for op in ("store", "erase"):
            req = request("gitlab.com", "username=glab", "password=glpat-a")
            rc, _, glab = self.run_helper(req, "gitlab.example.org", op=op, NXW_RC="1")
            self.assertEqual((rc, glab[0]["args"], glab[0]["tokens"], glab[0]["stdin"]),
                             (1, ["auth", "git-credential", op], {}, req))
            rc, _, glab = self.run_helper(req, "gitlab.com", op=op)
            self.assertEqual((rc, glab[0]["tokens"].get("GITLAB_TOKEN")), (0, "glpat-a"))

    def test_a_failed_get_without_the_token_names_the_tokens_host(self):
        rc, out, _ = self.run_helper(request("gitlab.com"), "https://gitlab.example.org/", NXW_RC="1")
        self.assertEqual((rc, self.err), (1, "glab-credential: the GitLab token in the environment is for "
                                             "gitlab.example.org, so gitlab.com doesn't get it (a login of its "
                                             "own: glab auth login --hostname gitlab.com)\n"))
        none = {"GITLAB_TOKEN": "", "GITLAB_ACCESS_TOKEN": "", "OAUTH_TOKEN": ""}
        for op, host, env in (("get", "gitlab.example.org", {}),                 # glab answered
                              ("erase", "gitlab.example.org", {"NXW_RC": "1"}),
                              ("get", "gitlab.example.org", dict(none, NXW_RC="1")),   # no token to keep back
                              ("get", "gitlab.com", {"NXW_RC": "1"})):          # its host: glab's own words
            self.run_helper(request("gitlab.com"), host, op=op, **env)
            self.assertEqual(self.err, "", (op, host, env))


if __name__ == "__main__":
    unittest.main()
