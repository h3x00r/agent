"""Telemetry's I/O: whether a file is a CA certificate, whether the collector takes an export, and the ddev bundle.

ca_problem(path): why PATH isn't a PEM certificate a TLS client can trust, with no private key in it ("" if it is),
asked of Python's ssl.
probe(endpoint, token, ca): one empty export (no data) to ENDPOINT/v1/metrics, as the personas will send theirs
(the token as a bearer header, the CA trusted besides the usual ones, no proxy: compose gives the personas none),
and what came of it: ("ok", ""), ("auth", code), ("status", code: another answer, a 200 that isn't JSON among
them, a redirect too), ("cert", ""): its certificate isn't one the CA vouches for, ("tls", ""): no TLS handshake
there (plain HTTP, say), ("ca", ""): the CA file can't be loaded, or ("connect", "").
write_bundle(checkout, home): personas/telemetry-ca.ddev.pem, the collector's CA then mkcert's root CA (when the
VM has one), written only when it changed, by a rename (a persona reading it sees the old file or the new one).

Pitfalls (each pinned where named):
- the probe's outcome in words of its own, never an errno's or an exception's text, which differ by machine and
  Python; it never raises, whatever the endpoint or the CA file (test_adapters.Telemetry.test_probe_outcomes)
- the probe ignores https_proxy, as the personas do (test_adapters.Telemetry.test_probe_ignores_proxy)
- the probe follows no redirect: the token never goes to a host the user didn't name, nor over plain http
  (test_adapters.Telemetry.test_probe_outcomes)
- a CA file holding a private key is refused: it would be copied world-readable and mounted into the personas
  (test_adapters.Telemetry.test_ca_problem)
- the bundle rewritten only when its bytes change, so a start doesn't touch it otherwise
  (test_adapters.Telemetry.test_write_bundle)
"""
import os
import re
import ssl
import tempfile
import urllib.error
import urllib.request

from agentctl.domain import telemetry as tele

TIMEOUT = 5
_KEY = re.compile(rb"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----")


def ca_problem(path):
    if not os.path.isfile(path):
        return "isn't a file"
    try:
        with open(path, "rb") as f:
            data = f.read()
        if b"-----BEGIN CERTIFICATE-----" not in data:
            return "has no certificate in it (it takes a PEM file: -----BEGIN CERTIFICATE----- …)"
        if _KEY.search(data):
            return "holds a private key as well: give the CA's certificate alone (its -----BEGIN CERTIFICATE----- block)"
        ssl.create_default_context(cafile=path)
    except ssl.SSLError:     # an OSError too: first
        return "isn't a certificate a TLS client can load (it takes a PEM file: -----BEGIN CERTIFICATE----- …)"
    except OSError as e:
        return "can't be read (%s)" % e.strerror
    return ""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """A redirect is an answer, not a place to go: the token isn't sent on to another host."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _tls(err):
    return ("cert", "") if isinstance(err, ssl.SSLCertVerificationError) else ("tls", "")


def probe(endpoint, token, ca):
    ctx = ssl.create_default_context()
    if ca:
        try:
            ctx.load_verify_locations(cafile=ca)
        except (ssl.SSLError, OSError):
            return "ca", ""
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    try:
        opener = urllib.request.build_opener(_NoRedirect, urllib.request.ProxyHandler({}),
                                             urllib.request.HTTPSHandler(context=ctx))
        req = urllib.request.Request(endpoint + "/v1/metrics", data=b'{"resourceMetrics":[]}', headers=headers,
                                     method="POST")
        with opener.open(req, timeout=TIMEOUT) as r:
            r.read()
            kind = r.headers.get("Content-Type", "")
        # A collector answers in the request's encoding; a web server on that port (Grafana's, say) with a page.
        return ("ok", "") if kind.startswith("application/json") else ("status", "200")
    except urllib.error.HTTPError as e:
        return ("auth" if e.code in (401, 403) else "status"), str(e.code)
    except urllib.error.URLError as e:
        return _tls(e.reason) if isinstance(e.reason, ssl.SSLError) else ("connect", "")
    except ssl.SSLError as e:     # an OSError too: first
        return _tls(e)
    except (OSError, ValueError):
        return "connect", ""


def write_bundle(checkout, home):
    """The ddev bundle in CHECKOUT, from its CA and HOME's mkcert root CA; an error message, or ""."""
    src, dst = os.path.join(checkout, tele.CA_FILE), os.path.join(checkout, tele.DDEV_BUNDLE)
    try:
        with open(src, "rb") as f:
            ca = f.read()
    except OSError as e:
        return "can't read %s (%s)" % (src, e.strerror)
    mkcert = b""
    root = os.path.join(home, ".local/share/mkcert/rootCA.pem") if home else ""
    if root and os.path.isfile(root):
        try:
            with open(root, "rb") as f:
                mkcert = f.read()
        except OSError:
            mkcert = b""
    data = tele.bundle(ca, mkcert)
    try:
        with open(dst, "rb") as f:
            if f.read() == data:
                return ""
    except OSError:
        pass
    tmp = ""
    try:
        fd, tmp = tempfile.mkstemp(prefix=os.path.basename(dst) + ".", dir=os.path.dirname(dst))
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.chmod(tmp, 0o644)
        os.rename(tmp, dst)
    except OSError as e:
        if tmp and os.path.exists(tmp):
            os.unlink(tmp)
        return "can't write %s (%s)" % (dst, e.strerror)
    return ""
