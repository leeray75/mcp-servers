# syntax=docker/dockerfile:1.7

# Pin the minor version here. For fully reproducible builds also pin the digest:
#   docker buildx imagetools inspect node:22-alpine   # then use node:22-alpine@sha256:<digest>
ARG NODE_IMAGE=node:22-alpine

# ---------------------------------------------------------------------------
# Builder: installs lockfile-pinned production dependencies (no install scripts).
# ---------------------------------------------------------------------------
FROM ${NODE_IMAGE} AS builder
WORKDIR /build
COPY package.json package-lock.json ./
RUN npm ci --omit=dev --ignore-scripts --no-audit --no-fund

# ---------------------------------------------------------------------------
# Runtime: non-root, nothing but node_modules and the gateway/supervisor source.
# ---------------------------------------------------------------------------
FROM ${NODE_IMAGE} AS runtime

ENV NODE_ENV=production \
    HOST=0.0.0.0 \
    PORT=8000

RUN addgroup -S -g 10001 firecrawlmcp \
    && adduser -S -u 10001 -G firecrawlmcp -H -h /nonexistent -s /sbin/nologin firecrawlmcp

WORKDIR /app
COPY --from=builder /build/node_modules /app/node_modules
COPY package.json /app/package.json
COPY src /app/src

USER 10001:10001
EXPOSE 8000

# No curl in the image: probe with node. Host 127.0.0.1:8000 is allowed by the Host-header guard.
# Run with `init: true` (compose does) so tini is PID 1.
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --start-interval=2s --retries=3 \
    CMD ["node", "-e", "fetch('http://127.0.0.1:'+(process.env.PORT||8000)+'/healthz',{signal:AbortSignal.timeout(3000)}).then(r=>process.exit(r.status===200?0:1),()=>process.exit(1))"]

CMD ["node", "src/main.mjs"]
