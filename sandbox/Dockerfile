# The box a sandboxed session runs in. See watchtower/sandbox.py.
#
# Claude Code itself is not installed here: the panel mounts the host's own
# `claude` binary read-only, so a sandboxed session always runs the same build
# as every other session on the machine and this image never needs rebuilding
# for an update. What is here is what a session needs to do real work in a
# checkout — read it, search it, build it, run its tests, commit to it.
#
# Built on first use as claude-watchtower-sandbox:<SANDBOX_IMAGE_VERSION>. Point
# CLAUDE_WATCHTOWER_SANDBOX_IMAGE at an image of your own to use that instead —
# it needs glibc (the binary is dynamically linked), and whatever tools your
# projects build with.
FROM debian:bookworm-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      ca-certificates curl git less procps ripgrep jq unzip \
      openssh-client sshpass netcat-openbsd \
      iptables \
      build-essential pkg-config \
      python3 python3-pip python3-venv python3-pytest \
      xz-utils \
 && rm -rf /var/lib/apt/lists/*

# Node from nodejs.org rather than Debian, whose 18 is too old for projects that
# run TypeScript directly (this panel's own build needs 22.6 or newer). Plus
# TypeScript itself, so a session can type-check without installing anything.
ARG NODE_VERSION=22.14.0
RUN arch="$(dpkg --print-architecture)" \
 && case "$arch" in amd64) node=x64 ;; arm64) node=arm64 ;; *) echo "no Node for $arch" >&2; exit 1 ;; esac \
 && curl -fsSL "https://nodejs.org/dist/v${NODE_VERSION}/node-v${NODE_VERSION}-linux-${node}.tar.xz" \
    | tar -xJ -C /usr/local --strip-components=1 \
 && npm install -g typescript \
 && npm cache clean --force

# iptables is for sandbox/entry.sh, which the panel mounts in and runs first:
# it shuts this machine and its network out, then drops to your uid with no
# capabilities (setpriv, from util-linux, which Debian always has). A custom
# image needs both. The session runs as your uid so files it writes are yours;
# entry.sh gives that uid a passwd entry, which ssh refuses to run without.
#
# ssh is for the local addresses a session is allowed (the Sandbox tab), such
# as a controller. It only has the keys in the sandbox's own home
# (~/.config/claude-watchtower/sandbox/home/.ssh), never yours.
ENV LANG=C.UTF-8
