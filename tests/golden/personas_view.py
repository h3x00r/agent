"""personas-view: the personas of the checkout in the cwd, as the `# persona:` lines the bash tools wrote,
whichever way the tree keeps them: from personas.json (the Python tools, from M4 on) in the order and
format of those lines, else as `grep -E '^# persona: ' compose.personas.yml` prints them. A scenario that
shows them reads the same in either tree. Exit 1 if there are none, as grep."""
import json
import os
import sys


def line(name, p):
    s = "%s flavour=%s context=%s colour=%s statusline=%s" % (name, p["flavour"], p["context"], p["colour"],
                                                             "yes" if p["statusline"] else "no")
    for k in ("root", "ssh"):
        if p.get(k):
            s += " %s=%s" % (k, p[k])
    return "# persona: " + s


def main():
    if os.path.isfile("personas.json"):
        with open("personas.json", encoding="utf-8", errors="surrogateescape") as f:
            lines = [line(n, p) for n, p in json.load(f)["personas"].items()]
        out = "".join(l + "\n" for l in lines)
    elif os.path.isfile("compose.personas.yml"):
        with open("compose.personas.yml", "rb") as f:
            out = b"".join(l for l in f.read().splitlines(True) if l.startswith(b"# persona: ")).decode(
                "utf-8", "surrogateescape")
        out = out if not out or out.endswith("\n") else out + "\n"
    else:
        sys.stderr.write("grep: compose.personas.yml: No such file or directory\n")
        return 2
    sys.stdout.buffer.write(out.encode("utf-8", "surrogateescape"))
    return 0 if out else 1


if __name__ == "__main__":
    sys.exit(main())
