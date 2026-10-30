# syntax=docker/dockerfile:1.7

# Pin the minor version here. For fully reproducible builds also pin the digest:
#   docker buildx imagetools inspect python:3.12-slim   # then use python:3.12-slim@sha256:<digest>
ARG PYTHON_IMAGE=python:3.12-slim

# ---------------------------------------------------------------------------
# Builder: installs hash-pinned dependencies into a venv.
# ---------------------------------------------------------------------------
FROM ${PYTHON_IMAGE} AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1

RUN python -m venv /opt/venv
WORKDIR /build

# Dependencies first (cached unless requirements.txt changes). --require-hashes makes pip refuse
# anything not pinned with a hash; --only-binary avoids running arbitrary build scripts.
COPY requirements.txt ./
RUN /opt/venv/bin/pip install --require-hashes --only-binary=:all: -r requirements.txt

# ---------------------------------------------------------------------------
# Runtime: slim image, non-root, nothing but the venv and the application source.
# ---------------------------------------------------------------------------
FROM ${PYTHON_IMAGE} AS runtime

ENV PATH="/opt/venv/bin:${PATH}" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONHASHSEED=random \
    PYTHONPATH=/app/src

RUN groupadd --system --gid 10001 twitchmcp \
    && useradd --system --uid 10001 --gid 10001 --no-create-home --home-dir /nonexistent \
        --shell /usr/sbin/nologin twitchmcp

COPY --from=builder /opt/venv /opt/venv
# Pure Python, run from source: no build backend (and so no unhashed build-time download).
COPY src /app/src

# R2/R48: the user-OAuth token folder. Owned by the runtime user; only the compose override mounts a
# host folder here (docker-compose.oauth.yml). Harmless when unmounted.
RUN mkdir -p /data && chown 10001:10001 /data

USER 10001:10001
WORKDIR /app
EXPOSE 8000

# No curl in the image: probe with the interpreter. The Host header 127.0.0.1:<port> is allowed by
# the Host guard. The probe follows the server's own transport: with TWITCH_TLS_CERT_FILE set the
# listener speaks HTTPS (T3), so it asks https and skips certificate verification for this one
# self-probe (T-D8) — the certificate is the operator's own local-CA leaf, and no outbound call this
# process makes ever disables verification (T7). Signals/zombie reaping: run with `init: true`
# (compose does) so tini is PID 1.
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --start-interval=2s --retries=3 \
    CMD ["python", "-c", "import os,ssl,sys,urllib.request as u; t=os.environ.get('TWITCH_TLS_CERT_FILE','').strip(); s='https' if t else 'http'; c=ssl._create_unverified_context() if t else None; sys.exit(0 if u.urlopen(s+'://127.0.0.1:8000/healthz',timeout=3,context=c).status == 200 else 1)"]

CMD ["python", "-m", "twitch_mcp"]
