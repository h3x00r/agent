import json, os, sys, tempfile
home = os.path.join(os.path.expanduser("~"), ".claude")
path, ledger = os.path.join(home, "settings.json"), os.path.join(home, ".nexus-defaults.json")
GONE = object()
def fail(why):
    print(why); sys.exit(3)
def load(p):
    try:
        with open(p, "rb") as f:
            return json.load(f)
    except FileNotFoundError:
        return None
new = json.load(sys.stdin.buffer)
try:
    user = load(path)
except ValueError:
    fail("its ~/.claude/settings.json is not valid JSON, so the defaults stayed out of it")
if user is None:
    user = {}
if not isinstance(user, dict) or not isinstance(new, dict):
    fail("its ~/.claude/settings.json or personas/claude.json is not a JSON object, so nothing was merged")
if os.path.exists(path) and not os.access(path, os.W_OK):
    fail("its ~/.claude/settings.json can not be written, so the defaults stayed out of it")
try:
    old = load(ledger)
except ValueError:
    old = None
old = old if isinstance(old, dict) else {}
def same(k, uv, ov):   # still what nexus wrote, also after a migration of Claude Code s own
    return uv == ov or k == "model" and isinstance(ov, str) and uv == ov + "[1m]"
done = []
def merge(u, d, o, at):   # merges d into u, given o (what nexus owned); returns what nexus owns now
    own = {}
    for k in sorted(set(d) | set(o)):
        dv, ov, uv, p = d.get(k, GONE), o.get(k, GONE), u.get(k, GONE), at + k
        if isinstance(dv, dict) and not (uv is not GONE and not isinstance(uv, dict)) and not (uv is GONE and ov is not GONE and not isinstance(ov, dict)):
            if uv is GONE:
                u[k] = uv = {}
            sub = merge(uv, dv, ov if isinstance(ov, dict) else {}, p + ".")
            if sub:
                own[k] = sub
            if not uv:
                del u[k]
        elif dv is not GONE:
            if uv is GONE and ov is GONE:                 # new to the persona: the default
                u[k] = own[k] = dv; done.append(p)
            elif uv is GONE:                              # nexus wrote it, the persona took it out
                own[k] = ov
            elif ov is not GONE and same(k, uv, ov):      # still as nexus wrote it: follows the default
                if uv != dv:
                    u[k] = dv; done.append(p)
                own[k] = dv
        elif ov is not GONE and uv is not GONE and not isinstance(ov, dict) and same(k, uv, ov):
            del u[k]; done.append(p + " (no longer a default)")
        elif isinstance(ov, dict) and isinstance(uv, dict):
            merge(uv, {}, ov, p + ".")
            if not uv:
                del u[k]
    return own
owned = merge(user, new, old, "")
def put(p, data, mode):   # through a symlink, to its target; replaced whole, so never half written
    real = os.path.realpath(p)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(real), prefix=".nexus.")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2); f.write("\n")
        os.chmod(tmp, mode); os.replace(tmp, real)
    except OSError as e:
        os.unlink(tmp); fail("writing %s failed (%s), so it is as it was" % (p.replace(os.path.expanduser("~"), "~"), e.strerror))
os.makedirs(home, exist_ok=True)
if done:
    put(path, user, os.stat(path).st_mode & 0o777 if os.path.exists(path) else 0o644)
    print("Claude defaults from personas/claude.json: " + ", ".join(done))
if owned != old:
    put(ledger, owned, 0o644)
