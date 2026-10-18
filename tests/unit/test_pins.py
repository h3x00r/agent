import ast
import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
HEAD = "Pitfalls (each pinned where named):"
PIN = r'test_\w+\.\w+\.test_\w+|golden [a-z]+/[a-z0-9-]+|run\.sh "[^"]*"'


def read(*rel):
    with open(os.path.join(ROOT, *rel)) as f:
        return f.read()


def known():
    """Every test a pin may name: unit tests as module.Class.test, golden case names, run.sh checks."""
    names = set()
    for f in os.listdir(os.path.join(ROOT, "tests", "unit")):
        if f.startswith("test_") and f.endswith(".py"):
            for c in ast.parse(read("tests", "unit", f)).body:
                if isinstance(c, ast.ClassDef):
                    names.update("%s.%s.%s" % (f[:-3], c.name, m.name) for m in c.body
                                 if isinstance(m, ast.FunctionDef) and m.name.startswith("test_"))
    for f in os.listdir(os.path.join(ROOT, "tests", "golden", "scen")):
        if f.endswith(".py"):
            names.update("golden " + n for n in re.findall(r'@case\("([^"]+)"', read("tests", "golden", "scen", f)))
    names.update('run.sh "%s"' % c.strip() for c in re.findall(r'check "([^"]*)"', read("tests", "run.sh")))
    return names


class Pins(unittest.TestCase):
    def test_every_pitfall_names_a_test_that_exists(self):
        # Each module's docstring ends with its pitfalls, each naming what pins it (or "not pinned").
        names, missing = known(), []
        for top, _, files in os.walk(os.path.join(ROOT, "agentctl")):
            for n in files:
                if not n.endswith(".py"):
                    continue
                path = os.path.join(top, n)
                with open(path) as f:
                    doc = ast.get_docstring(ast.parse(f.read()), clean=False) or ""
                if HEAD not in doc:
                    continue
                for item in re.sub(r"\n  ", " ", doc.split(HEAD, 1)[1]).split("\n- ")[1:]:
                    m = re.search(r"\(([^()]*(?:\([^()]*\)[^()]*)*)\)$", item.strip())
                    group = m.group(1) if m else ""
                    pins = re.findall(PIN, group)
                    if group != "not pinned" and (not pins or re.sub(PIN, "", group).strip(" ,")):
                        missing.append("%s: no pin in %r" % (os.path.relpath(path, ROOT), item.strip()[:60]))
                    missing += ["%s: %s" % (os.path.relpath(path, ROOT), p) for p in pins if p not in names]
        self.assertEqual(missing, [])


if __name__ == "__main__":
    unittest.main()
