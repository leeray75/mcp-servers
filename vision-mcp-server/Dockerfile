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

# Dependencies first (cached unless requirements.txt changes). --require-hashes
# makes pip refuse anything not pinned with a hash; --only-binary avoids running
# arbitrary build scripts and keeps compilers out of the image (Pillow ships wheels).
COPY requirements.txt ./
RUN /opt/venv/bin/pip install --require-hashes --only-binary=:all: -r requirements.txt

# ---------------------------------------------------------------------------
# Runtime: slim image, non-root, nothing but the venv.
# ---------------------------------------------------------------------------
FROM ${PYTHON_IMAGE} AS runtime

ENV PATH="/opt/venv/bin:${PATH}" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONHASHSEED=random \
    PYTHONPATH=/app/src \
    PORT=8000 \
    IMAGES_DIR=/images

RUN groupadd --system --gid 10001 visionmcp \
    && useradd --system --uid 10001 --gid 10001 --no-create-home --home-dir /nonexistent \
        --shell /usr/sbin/nologin visionmcp \
    && mkdir /images \
    && chown 10001:10001 /images

COPY --from=builder /opt/venv /opt/venv
# The application is pure Python and is run from source: no build backend (and so no
# unhashed build-time download) is involved.
COPY src /app/src

USER 10001:10001
WORKDIR /app
# Images are bind-mounted here read-only by docker-compose.yml.
VOLUME ["/images"]
EXPOSE 8000

# No curl in the image: probe with the interpreter. Host is 127.0.0.1:<port>,
# which the Host-header guard allows. Signals/zombie reaping: run with `init: true`
# (compose does) so tini is PID 1.
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --start-interval=2s --retries=3 \
    CMD ["python", "-c", "import os,sys,urllib.request as u; sys.exit(0 if u.urlopen('http://127.0.0.1:%s/healthz' % os.environ.get('PORT','8000'), timeout=3).status == 200 else 1)"]

CMD ["python", "-m", "vision_mcp"]
