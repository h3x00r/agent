"""Contexts: the kind of projects a persona works on, and what that needs from the VM.

docker: projects run with docker compose or Dockerfiles; the image has no ddev.
ddev:   ddev projects; the VM's ~/.ddev and its mkcert CA are mounted, and the CA is trusted inside.
"""


class Context:
    name = ""
    base_service = "_agent"      # the compose.yml service a persona extends
    needs_ddev_dirs = False      # ~/.ddev and ~/.local/share/mkcert must exist (or Docker makes them root's)
    trusts_mkcert_ca = False

    def describe(self):
        return ""


class Docker(Context):
    name = "docker"


class Ddev(Context):
    name = "ddev"
    base_service = "_agent-ddev"
    needs_ddev_dirs = True
    trusts_mkcert_ca = True


CONTEXTS = {c.name: c for c in (Docker(), Ddev())}


def get(name):
    return CONTEXTS.get(name)
