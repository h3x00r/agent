# Agent persona image. One Dockerfile, two flavours x two contexts:
#   --target claude (the default) | grok        --build-arg CONTEXT=docker (the default) | ddev
# compose.personas.yml (written by ./install.sh) passes both for each persona.
#
# Deliberately NO project toolchains (php, go, java; python3 is there for the statusline and APM only).
# The agent is an orchestrator: it runs `ddev exec …` / `docker compose exec …` and the project
# containers own the runtimes.
# That is what keeps the image stack-agnostic and stops its tool versions drifting from the project's.
#
# Stages:  tools → ctx-docker | ctx-ddev (adds ddev) → base (user, tmux, entrypoint) → claude | grok.
# The vendor installers run in claude-cli, grok-cli and apm-cli, on a bare fetch stage, and only their
# output is copied on. So each flavour sees only its own pins (CLAUDE_VERSION never touches grok), and a
# version bump re-runs one installer stage, while a uid, context, node or ddev change re-runs none.
# APM goes into both flavours.

# Context: "docker" for projects run with docker compose / Dockerfiles, "ddev" adds ddev.
ARG CONTEXT=docker

FROM debian:bookworm-slim AS tools

# pipefail: a failed `curl … |` must fail the build, not feed nothing to the next command.
SHELL ["/bin/bash", "-eo", "pipefail", "-c"]
ARG DEBIAN_FRONTEND=noninteractive

# --- base tooling (python3: the statusline script, and APM's venv runs on it) -------------
RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates curl gnupg git openssh-client tmux ncurses-term \
      jq ripgrep less procps tar unzip xz-utils make bash-completion python3 \
 && install -m 0755 -d /etc/apt/keyrings \
 && rm -rf /var/lib/apt/lists/*

# --- docker CLI + compose/buildx plugins (client only; the daemon is the VM's) -----------
RUN curl -fsSL https://download.docker.com/linux/debian/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg \
 && echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/debian $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
      > /etc/apt/sources.list.d/docker.list \
 && apt-get update && apt-get install -y --no-install-recommends \
      docker-ce-cli docker-compose-plugin docker-buildx-plugin \
 && rm -rf /var/lib/apt/lists/*

# --- node (for MCP servers via npx; Claude Code itself is a self-contained binary) --------
ARG NODE_MAJOR=22
RUN curl -fsSL https://deb.nodesource.com/setup_${NODE_MAJOR}.x | bash - \
 && apt-get install -y --no-install-recommends nodejs \
 && rm -rf /var/lib/apt/lists/* \
 && node --version | grep -q "^v${NODE_MAJOR}\." && command -v npx >/dev/null

# --- gh CLI ------------------------------------------------------------------------------
RUN curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg -o /etc/apt/keyrings/githubcli-archive-keyring.gpg \
 && echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
      > /etc/apt/sources.list.d/github-cli.list \
 && apt-get update && apt-get install -y --no-install-recommends gh \
 && rm -rf /var/lib/apt/lists/*


# =========================================================================================
# Context stages: only the one CONTEXT names gets built.
FROM tools AS ctx-docker

FROM tools AS ctx-ddev
ARG DEBIAN_FRONTEND=noninteractive
# --- ddev (pin DDEV_VERSION to the version installed on the VM, or router/ssh-agent containers get recreated on every switch)
# `ddev version` prints v1.x.y; apt wants 1.x.y, so a leading v is dropped.
ARG DDEV_VERSION=
RUN curl -fsSL -o /tmp/ddev.key https://pkg.ddev.com/apt/gpg.key \
 && gpg --dearmor -o /etc/apt/keyrings/ddev.gpg /tmp/ddev.key && rm /tmp/ddev.key \
 && echo "deb [signed-by=/etc/apt/keyrings/ddev.gpg] https://pkg.ddev.com/apt/ * *" > /etc/apt/sources.list.d/ddev.list \
 && v="${DDEV_VERSION#v}" \
 && apt-get update && apt-get install -y --no-install-recommends ddev${v:+=$v} \
 && rm -rf /var/lib/apt/lists/*


# =========================================================================================
# hadolint ignore=DL3006
FROM ctx-${CONTEXT} AS base
ARG CONTEXT

# --- user: same uid/gid as the VM user, so files written into the projects root are theirs
# ~/.local/share is pre-created so the mkcert bind mount (compose.yml) can't leave root-owned
# parents in the home volume when the VM user's HOME is also /home/dev.
ARG UID=1000
ARG GID=1000
RUN { getent group "$GID" >/dev/null || groupadd -g "$GID" dev; } \
 && useradd -l -m -u "$UID" -g "$GID" -s /bin/bash dev \
 && mkdir -p /home/dev/.ddev /home/dev/.ssh /home/dev/.claude /home/dev/.grok \
      /home/dev/.local/share /home/dev/.local/state \
 && chown -R "$UID:$GID" /home/dev \
 && printf 'Host *\n  StrictHostKeyChecking accept-new\n' > /etc/ssh/ssh_config.d/agent.conf

COPY tmux.conf /etc/tmux.conf
COPY --chmod=0755 entrypoint.sh /usr/local/bin/agent-entrypoint

LABEL agent.uid="$UID" agent.context="$CONTEXT"
WORKDIR /home/dev
ENTRYPOINT ["agent-entrypoint"]


# =========================================================================================
# The agent CLIs. Installed OUTSIDE /home/dev on purpose: /home/dev becomes a per-persona volume
# that is seeded from the image only once, so anything put there would never get upgraded by a rebuild.

# Just enough to fetch and run the vendor installers, so nothing in tools can re-run them.
FROM debian:bookworm-slim AS fetch
SHELL ["/bin/bash", "-eo", "pipefail", "-c"]
ARG DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl \
 && rm -rf /var/lib/apt/lists/*

# --- Claude Code: Anthropic's native installer ------------------------------------------
# It installs into $HOME (~/.local/bin/claude -> ~/.local/share/claude/versions/X, plus state with
# fresh machine/user IDs); this stage is thrown away, so only the binary and the lock are copied on.
# CLAUDE_VERSION = latest | stable | X.Y.Z; a malformed or unknown version fails the build.
FROM fetch AS claude-cli
ARG CLAUDE_VERSION=latest
# Not /: run from there, the installer can scan the whole filesystem (Anthropic's Docker note).
WORKDIR /tmp
RUN curl -fsSL -o claude-install.sh https://claude.ai/install.sh \
 && bash claude-install.sh "$CLAUDE_VERSION" \
 && install -m 0755 "$(readlink -f "$HOME/.local/bin/claude")" /usr/local/bin/claude \
 && v="$(claude --version)" \
 && case "$CLAUDE_VERSION" in latest|stable) ;; *) test "${v%% *}" = "$CLAUDE_VERSION" ;; esac
# The image is the only updater. Lock out the background updater AND `claude update`/`claude install`:
# either would put ~/.local/bin/claude into the home volume, which shadows this binary in login shells
# and is never touched by a rebuild. Managed settings outrank the user's env and settings.
RUN mkdir -p /etc/claude-code/managed-settings.d \
 && printf '%s\n' '{"env": {"DISABLE_UPDATES": "1", "DISABLE_AUTOUPDATER": "1", "DISABLE_INSTALLATION_CHECKS": "1"}}' \
      > /etc/claude-code/managed-settings.d/10-agent-image.json

# --- Grok Build: xAI's installer ---------------------------------------------------------
# It writes to $HOME/.grok, so HOME points at /opt/grok, the tree that is copied on.
FROM fetch AS grok-cli
WORKDIR /tmp
RUN curl -fsSL -o grok-install.sh https://x.ai/cli/install.sh \
 && HOME=/opt/grok bash grok-install.sh \
 && chmod -R a+rX /opt/grok \
 && test -x /opt/grok/.grok/bin/grok

# --- APM (Agent Package Manager): Microsoft's PyPI package -------------------------------
# Its release binary needs glibc 2.38 and bookworm has 2.36, so apm-cli goes into a venv on python3.
# This stage and tools are both bookworm, so /opt/apm/bin/python -> /usr/bin/python3 resolves the same.
# Self-update goes off through the hook APM keeps for packagers (apm_cli/update_policy.py), which also
# silences its "new version" notice: the image is the updater, as for Claude Code.
# APM_VERSION = latest | X.Y.Z; latest is resolved once and then cached, like a Claude channel.
FROM fetch AS apm-cli
SHELL ["/bin/bash", "-eo", "pipefail", "-c"]
ARG DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends python3 python3-venv \
 && rm -rf /var/lib/apt/lists/*
ARG APM_VERSION=latest
RUN v="${APM_VERSION#v}" \
 && case "$v" in \
      latest) spec=apm-cli ;; \
      *) printf '%s\n' "$v" | grep -Eqx '[0-9]+\.[0-9]+\.[0-9]+' \
           || { echo "APM_VERSION must be latest or X.Y.Z, not '$APM_VERSION'" >&2; exit 1; }; \
         spec="apm-cli==$v" ;; \
    esac \
 && python3 -m venv /opt/apm \
 && /opt/apm/bin/pip install --no-cache-dir --disable-pip-version-check "$spec" \
 && p="$(/opt/apm/bin/python -c 'import apm_cli.update_policy as u; print(u.__file__)')" \
 && sed -i -e 's/^SELF_UPDATE_ENABLED = True$/SELF_UPDATE_ENABLED = False/' \
      -e 's/^SELF_UPDATE_DISABLED_MESSAGE = .*/SELF_UPDATE_DISABLED_MESSAGE = "The agent image is the APM updater: set APM_VERSION in the agents .env, then agent <persona> build and restart."/' "$p" \
 && /opt/apm/bin/python -c 'import apm_cli.update_policy as u; assert not u.is_self_update_enabled()'


# =========================================================================================
FROM base AS grok
COPY --from=grok-cli /opt/grok /opt/grok
COPY --from=apm-cli /opt/apm /opt/apm
# xAI's `agent` binary goes on PATH as grok-agent: a bare `agent` would read as this repo's launcher.
RUN ln -s /opt/grok/.grok/bin/grok /usr/local/bin/grok \
 && ln -s /opt/grok/.grok/bin/agent /usr/local/bin/grok-agent \
 && ln -s /opt/apm/bin/apm /usr/local/bin/apm \
 && test -x /usr/local/bin/grok && test -x /usr/local/bin/grok-agent && apm --version && rm -rf /root/.apm
LABEL agent.flavour="grok"
USER dev

# Last stage = what a plain `docker build .` produces.
FROM base AS claude
COPY --from=claude-cli /usr/local/bin/claude /usr/local/bin/claude
COPY --from=claude-cli /etc/claude-code /etc/claude-code
COPY --from=apm-cli /opt/apm /opt/apm
# apm --version: APM runs on this image's python3 and git (it leaves a /root/.apm behind).
RUN ln -s /opt/apm/bin/apm /usr/local/bin/apm && apm --version && rm -rf /root/.apm
LABEL agent.flavour="claude"
USER dev
