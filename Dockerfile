# Agent persona image. One Dockerfile, two flavours x two contexts:
#   --target claude (the default) | grok        --build-arg CONTEXT=docker (the default) | ddev
# compose.personas.yml (written by nexus --config) passes both for each persona.
#
# Deliberately NO project toolchains (php, go, java). python3 is there for the statusline and APM, with
# venv and uv for a project's own Python tools (in a venv of the project's, at the versions it pins).
# The agent is an orchestrator: it runs `ddev exec …` / `docker compose exec …` and the project
# containers own the runtimes.
# That is what keeps the image stack-agnostic and stops its tool versions drifting from the project's.
#
# Stages:  tools → ctx-docker | ctx-ddev (adds ddev) → base (user, tmux, entrypoint) → claude | grok.
# The vendor installers run in claude-cli, grok-cli and apm-cli, and the uv and glab release tarballs are
# fetched in uv-bin and glab-cli, all on a bare fetch stage, and only their output is copied on. So each
# flavour sees only its own pins (CLAUDE_VERSION never touches grok, nor GROK_VERSION Claude), and a version
# bump re-runs one installer stage, while a uid, context, node or ddev change re-runs none.
# APM, uv and glab go into both flavours.

# Context: "docker" for projects run with docker compose / Dockerfiles, "ddev" adds ddev.
ARG CONTEXT=docker

FROM debian:bookworm-slim AS tools

# pipefail: a failed `curl … |` must fail the build, not feed nothing to the next command.
SHELL ["/bin/bash", "-eo", "pipefail", "-c"]
ARG DEBIAN_FRONTEND=noninteractive

# --- base tooling (python3: the statusline script, and APM's venv runs on it; python3-venv: `python3 -m venv`,
# which bookworm's python3 can't do without it (no ensurepip); uv makes venvs without it)
RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates curl gnupg git openssh-client tmux ncurses-term \
      jq ripgrep less procps tar unzip xz-utils make bash-completion python3 python3-venv \
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
# /run/nexus is where the docker guard (below) makes its socket. Until it does, that socket is a link to the VM's,
# so `docker` works in a container started without the entrypoint too (--entrypoint sh). Writable by every uid
# (not sticky): a persona run as a uid other than this image's can still replace the link with the guard's socket.
ARG UID=1000
ARG GID=1000
RUN { getent group "$GID" >/dev/null || groupadd -g "$GID" dev; } \
 && useradd -l -m -u "$UID" -g "$GID" -s /bin/bash dev \
 && mkdir -p /home/dev/.ddev /home/dev/.ssh /home/dev/.claude /home/dev/.grok \
      /home/dev/.local/share /home/dev/.local/state \
 && chown -R "$UID:$GID" /home/dev \
 && printf 'Host *\n  StrictHostKeyChecking accept-new\n' > /etc/ssh/ssh_config.d/agent.conf \
 && install -d -o "$UID" -g "$GID" -m 0777 /run/nexus \
 && ln -s /var/run/docker.sock /run/nexus/docker.sock && chown -h "$UID:$GID" /run/nexus/docker.sock

COPY tmux.conf /etc/tmux.conf
COPY --chmod=0755 entrypoint.sh /usr/local/bin/agent-entrypoint

# --- docker: the guard, the compose shim, nexus-apm and glab's git helper (each file says what it does) ----
# Personas share the VM's daemon. The guard (entrypoint.sh starts it, unless DOCKER_GUARD=off in the .env) is
# DOCKER_HOST: the containers, volumes and networks a persona creates are labelled as its own, and stopping or
# removing ones that aren't is refused (images: persona images, other personas', and ones a container not its own
# uses). The shim is found before the real compose plugin (/usr/libexec/docker/cli-plugins) and puts the persona
# in front of each compose project's name. nexus-apm X.Y.Z runs the APM version a project pins. glab-credential
# is git's credential helper for https://gitlab.com (the final stages, which have glab, set it).
COPY --chmod=0755 image/docker-guard.py /usr/local/libexec/nexus/docker-guard
COPY --chmod=0755 image/docker-compose-persona.py /usr/local/lib/docker/cli-plugins/docker-compose
COPY --chmod=0755 image/nexus-apm /usr/local/bin/nexus-apm
COPY --chmod=0755 image/glab-credential /usr/local/libexec/nexus/glab-credential
# The shim works with the compose just installed: its probe's flags are still taken, and a missing env_file or an
# undefined network still don't fail the probe (Compose 2.x fails it on the first); else projects go unprefixed,
# silently.
RUN d="$(mktemp -d)" && mkdir "$d/nxprobe" \
 && printf 'services: {s: {image: x, env_file: missing.env, networks: [nope]}}\n' > "$d/nxprobe/compose.yaml" \
 && AGENT_PERSONA=p docker compose -f "$d/nxprobe/compose.yaml" config --format json --no-env-resolution \
      --no-consistency > "$d/out" \
 && grep -qF '"name": "p-nxprobe"' "$d/out" \
 && rm -rf "$d"
# Testcontainers bind-mounts the socket into its Ryuk container by path, and that path is looked up on the VM,
# where only the raw socket exists (Ryuk then talks to the daemon past the guard; it is still labelled, as it
# is created through it). UV_LINK_MODE: uv's cache is in the home volume and a project's venv in the projects
# root, two filesystems, where uv's default hardlinks fail (it warns, then copies). GLAB_CHECK_UPDATE: glab
# looks for a newer release once a day; the image is the updater, as for Claude Code and APM. GLAB_SHOW_WHATS_NEW:
# its banner after an upgrade, which would also land on the first `git push` after a rebuild (the git helper is
# glab). GLAB_SEND_TELEMETRY: glab's usage data, on by default.
ENV DOCKER_HOST=unix:///run/nexus/docker.sock \
    TESTCONTAINERS_DOCKER_SOCKET_OVERRIDE=/var/run/docker.sock \
    UV_LINK_MODE=copy \
    GLAB_CHECK_UPDATE=false \
    GLAB_SHOW_WHATS_NEW=false \
    GLAB_SEND_TELEMETRY=false

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
# GROK_VERSION = latest (the newest on xAI's stable channel) | X.Y.Z[-suffix]; the installer rejects a
# malformed one and an unknown one fails the download. Like CLAUDE_VERSION, a bump re-runs only this stage.
FROM fetch AS grok-cli
SHELL ["/bin/bash", "-eo", "pipefail", "-c"]
ARG GROK_VERSION=latest
WORKDIR /tmp
RUN v="${GROK_VERSION#v}" \
 && if [ "$v" = latest ]; then v=""; fi \
 && curl -fsSL -o grok-install.sh https://x.ai/cli/install.sh \
 && HOME=/opt/grok bash grok-install.sh ${v:+"$v"} \
 && chmod -R a+rX /opt/grok \
 && test -x /opt/grok/.grok/bin/grok \
 && { [ -z "$v" ] || /opt/grok/.grok/bin/grok --version | grep -qF "$v"; }

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
      -e 's/^SELF_UPDATE_DISABLED_MESSAGE = .*/SELF_UPDATE_DISABLED_MESSAGE = "The agent image is the APM updater: set APM_VERSION in the agents .env, then nexus <persona> build and restart."/' "$p" \
 && /opt/apm/bin/python -c 'import apm_cli.update_policy as u; assert not u.is_self_update_enabled()'

# --- uv (Astral): the release tarball, checked against the sha256 pinned here ---------------
# uv makes venvs and runs a tool at the version a project pins (uvx --from tool==X.Y.Z). A bump changes
# UV_VERSION and both hashes together: the release's uv-<arch>-unknown-linux-gnu.tar.gz.sha256 files (its
# sha256.sum says the same). A tarball's uv can't update itself (`uv self update` refuses).
FROM fetch AS uv-bin
SHELL ["/bin/bash", "-eo", "pipefail", "-c"]
ARG UV_VERSION=0.13.6
ARG UV_SHA256_AMD64=f29c3e94ffabd7e6f3ba521dba169a9b7ec12100bf3257087f0f24f6123f3645
ARG UV_SHA256_ARM64=229e194255c7958d86d56e8790d969a6f74736592133f0dfd0076f754c642c1d
WORKDIR /tmp
RUN v="${UV_VERSION#v}" \
 && { printf '%s\n' "$v" | grep -Eqx '[0-9]+\.[0-9]+\.[0-9]+' \
      || { echo "UV_VERSION must be X.Y.Z, not '$UV_VERSION'" >&2; exit 1; }; } \
 && arch="$(dpkg --print-architecture)" \
 && case "$arch" in amd64) t=x86_64 sum="$UV_SHA256_AMD64" ;; arm64) t=aarch64 sum="$UV_SHA256_ARM64" ;; \
      *) echo "uv: no build for $arch" >&2; exit 1 ;; esac \
 && f="uv-${t}-unknown-linux-gnu.tar.gz" \
 && curl -fsSLO "https://github.com/astral-sh/uv/releases/download/${v}/$f" \
 && { printf '%s  %s\n' "$sum" "$f" | sha256sum -c --strict - \
      || { echo "$f isn't the one pinned here: UV_VERSION and UV_SHA256_* change together" >&2; exit 1; }; } \
 && mkdir -p /opt/uv && tar -xzf "$f" --no-same-owner --strip-components=1 -C /opt/uv \
 && /opt/uv/uv --version | grep -q "^uv ${v} " && /opt/uv/uvx --version | grep -q "^uvx ${v} "

# --- glab (GitLab CLI): GitLab's release tarball, checked against the sha256 pinned here -------
# Not Debian's (bookworm-backports has 1.33) and GitLab keeps no apt repo. A bump changes GLAB_VERSION and
# both hashes together: the release's checksums.txt. The final stages put it behind git's credential helper for
# https://gitlab.com (image/glab-credential, GITLAB_TOKEN in the persona's .env).
FROM fetch AS glab-cli
SHELL ["/bin/bash", "-eo", "pipefail", "-c"]
ARG GLAB_VERSION=1.127.0
ARG GLAB_SHA256_AMD64=90a440866e7ddbb25101a4cc85e50f1914c73c0dabab472e6f1027b51c4a3316
ARG GLAB_SHA256_ARM64=c2c43f6a21f66b8c992fe5b70a06d52d01b94b3ab4470bdf547ef596d49a2e6b
WORKDIR /tmp
RUN v="${GLAB_VERSION#v}" \
 && { printf '%s\n' "$v" | grep -Eqx '[0-9]+\.[0-9]+\.[0-9]+' \
      || { echo "GLAB_VERSION must be X.Y.Z, not '$GLAB_VERSION'" >&2; exit 1; }; } \
 && arch="$(dpkg --print-architecture)" \
 && case "$arch" in amd64) sum="$GLAB_SHA256_AMD64" ;; arm64) sum="$GLAB_SHA256_ARM64" ;; \
      *) echo "glab: no linux_$arch build" >&2; exit 1 ;; esac \
 && f="glab_${v}_linux_${arch}.tar.gz" \
 && curl -fsSLO "https://gitlab.com/gitlab-org/cli/-/releases/v${v}/downloads/$f" \
 && { printf '%s  %s\n' "$sum" "$f" | sha256sum -c --strict - \
      || { echo "$f isn't the one pinned here: GLAB_VERSION and GLAB_SHA256_* change together" >&2; exit 1; }; } \
 && mkdir -p /opt/glab && tar -xzf "$f" --no-same-owner -C /opt/glab bin/glab \
 && GLAB_CHECK_UPDATE=false GLAB_SEND_TELEMETRY=false /opt/glab/bin/glab version | grep -q "^glab ${v} "


# =========================================================================================
FROM base AS grok
COPY --from=grok-cli /opt/grok /opt/grok
COPY --from=apm-cli /opt/apm /opt/apm
COPY --from=uv-bin /opt/uv/uv /opt/uv/uvx /usr/local/bin/
COPY --from=glab-cli /opt/glab/bin/glab /usr/local/bin/glab
# xAI's installer links grok and agent into ~/.grok/bin; on PATH under the same names.
# glab answers git's https://gitlab.com credential asks, through glab-credential (as the claude stage below says).
RUN ln -s /opt/grok/.grok/bin/grok /usr/local/bin/grok \
 && ln -s /opt/grok/.grok/bin/agent /usr/local/bin/agent \
 && ln -s /opt/apm/bin/apm /usr/local/bin/apm \
 && git config --system --add 'credential.https://gitlab.com.helper' /usr/local/libexec/nexus/glab-credential \
 && test -x /usr/local/bin/grok && test -x /usr/local/bin/agent && apm --version \
 && uv --version && glab version \
 && rm -rf /root/.apm /root/.config
LABEL agent.flavour="grok"
USER dev

# Last stage = what a plain `docker build .` produces.
FROM base AS claude
# Claude Code itself is /usr/local/libexec/claude; `claude` is claude-mode.sh, which adds the --permission-mode
# that `nexus <persona> --skip-bypass-permissions | --bypass-permissions` set for a session (NEXUS_PERMISSIONS).
COPY --from=claude-cli /usr/local/bin/claude /usr/local/libexec/claude
COPY --chmod=0755 claude-mode.sh /usr/local/bin/claude
# What an agent team's teammates start as in a session that asks, or doesn't (claude-mode.sh says).
RUN ln -s /usr/local/bin/claude /usr/local/libexec/claude-ask && ln -s /usr/local/bin/claude /usr/local/libexec/claude-bypass
COPY --from=claude-cli /etc/claude-code /etc/claude-code
COPY --from=apm-cli /opt/apm /opt/apm
COPY --from=uv-bin /opt/uv/uv /opt/uv/uvx /usr/local/bin/
COPY --from=glab-cli /opt/glab/bin/glab /usr/local/bin/glab
# apm --version: APM runs on this image's python3 and git (it leaves a /root/.apm behind, glab a /root/.config).
# glab answers git's credential asks for https://gitlab.com, through glab-credential: a system helper, so the home
# volume's ~/.gitconfig stays the user's. It hands GITLAB_TOKEN (the persona's .env) only to the host glab
# designates (gitlab.com, unless GITLAB_HOST names another GitLab), and for any other host glab answers with what
# `glab auth login` stored, if anything. A self-managed GitLab: the same helper for its host, in the persona's
# ~/.gitconfig (git config --global credential.https://<host>.helper /usr/local/libexec/nexus/glab-credential).
RUN ln -s /opt/apm/bin/apm /usr/local/bin/apm \
 && git config --system --add 'credential.https://gitlab.com.helper' /usr/local/libexec/nexus/glab-credential \
 && apm --version && uv --version && glab version \
 && rm -rf /root/.apm /root/.config
LABEL agent.flavour="claude"
USER dev
