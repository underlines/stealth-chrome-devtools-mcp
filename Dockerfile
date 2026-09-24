# syntax=docker/dockerfile:1
# Container packaging for stealth-chrome-devtools-mcp.
#
# The image runs the standalone HTTP transport backend (no stdio proxy, no
# desktop). Linux only; linux/amd64 (Google Chrome Stable is published for
# amd64 only). Headless browsing only — a container has no display, so spawn
# browsers with headless=true.

ARG PYTHON_VERSION=3.11

# ---------------------------------------------------------------------------
# Build stage: resolve the pinned dependency tree into a standalone venv.
# uv + uv.lock make this reproducible; --frozen refuses to drift from the lock.
# ---------------------------------------------------------------------------
FROM python:${PYTHON_VERSION}-slim-bookworm AS builder

COPY --from=ghcr.io/astral-sh/uv:0.11.8 /uv /usr/local/bin/uv

ENV UV_PYTHON=/usr/local/bin/python3.11 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app

# README.md is part of the package metadata (hatchling refuses to build without it).
COPY pyproject.toml uv.lock README.md ./
COPY src ./src

RUN uv sync --frozen --no-dev --no-editable

# ---------------------------------------------------------------------------
# Final stage: Python package + Google Chrome Stable (linux/amd64) + its
# runtime shared libraries (the .deb pulls libnss3/libgtk-3/libgbm/... itself;
# the fonts are declared so text renders with real metrics).
# ---------------------------------------------------------------------------
FROM python:${PYTHON_VERSION}-slim-bookworm

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates curl gnupg fonts-liberation fonts-noto-color-emoji \
    && curl -fsSL https://dl.google.com/linux/linux_signing_key.pub \
        | gpg --dearmor -o /usr/share/keyrings/google-chrome.gpg \
    && echo "deb [arch=amd64 signed-by=/usr/share/keyrings/google-chrome.gpg] https://dl.google.com/linux/chrome/deb/ stable main" \
        > /etc/apt/sources.list.d/google-chrome.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends google-chrome-stable \
    && apt-get purge -y curl gnupg \
    && apt-get autoremove -y \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# KNOWN ISSUE (measured 2026-09-24, Chrome 154.0.8037.57): the server detects
# containers via /.dockerenv and auto-appends --single-process to every launch
# (platform_utils.get_required_sandbox_args). In --headless, Chrome 154 exits
# with SIGTRAP ~1.5 s into a --single-process launch and its DevTools endpoint
# never opens, so every spawn_browser fails ("Failed to connect to browser");
# without that one flag the same Chrome serves the endpoint fine in this image.
# The product's own stealth filter also refuses to pass --single-process from
# callers ("detectable process architecture"), so this image drops it at the
# binary seam instead: /usr/bin/google-chrome{,-stable} become a shim that
# filters the flag and execs the real binary. Everything else (including
# --version, which the masked-User-Agent derivation probes) is forwarded.
# DELETE this shim when platform_utils stops adding --single-process in
# containers; until then it is the difference between a working container and
# one that cannot spawn a browser.
RUN printf '%s\n' \
      '#!/bin/bash' \
      'args=()' \
      'for a in "$@"; do' \
      '  [ "$a" = "--single-process" ] || args+=("$a")' \
      'done' \
      'exec /opt/google/chrome/google-chrome "${args[@]}"' \
      > /usr/local/bin/google-chrome-stealth-shim \
    && chmod +x /usr/local/bin/google-chrome-stealth-shim \
    && ln -sf /usr/local/bin/google-chrome-stealth-shim /usr/bin/google-chrome \
    && ln -sf /usr/local/bin/google-chrome-stealth-shim /usr/bin/google-chrome-stable

# Non-root user. Chrome still runs with the sandbox disabled here, but the
# server adds --no-sandbox / --disable-setuid-sandbox itself the moment it
# detects a container (/.dockerenv) — no flags are baked in and none are
# needed in the CMD. The two state directories are pre-created with this
# user's ownership so named volumes inherit it on first mount.
RUN useradd --create-home --uid 1000 stealth \
    && mkdir -p /home/stealth/.stealth-mcp /home/stealth/.stealth-mcp-browser-sessions \
    && chown -R stealth:stealth /home/stealth

COPY --from=builder /app/.venv /app/.venv

ENV PATH="/app/.venv/bin:${PATH}" \
    HOME=/home/stealth \
    PORT=8000

USER stealth
WORKDIR /home/stealth

EXPOSE 8000

# TCP liveness probe: the server is up when it accepts connections on its
# port. Reads the same PORT env var the server reads (default 8000).
HEALTHCHECK --interval=30s --timeout=10s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import os,socket; socket.create_connection(('127.0.0.1', int(os.environ.get('PORT') or 8000)), timeout=5).close()"]

# HTTP transport: a standalone MCP server over streamable HTTP. --host 0.0.0.0
# is required so the published port reaches the server (the loopback default
# is only reachable inside the container); the container's host-side exposure
# is controlled by the ports mapping, defaulting to the host's loopback.
# --port is deliberately not passed: it defaults to the PORT env var above.
CMD ["stealth-chrome-devtools-mcp", "--transport", "http", "--host", "0.0.0.0"]