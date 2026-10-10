# Vision MCP server

A small, self-owned [MCP](https://modelcontextprotocol.io) server that gives **text-only agents the
ability to see images**. It exposes one tool, `describe_image`, which reads an image file from a
single read-only folder, sends it with your question to a vision model behind any
OpenAI-compatible endpoint (built for a self-hosted LiteLLM + Qwen), and returns the answer as text.

Why it exists: some agents (for example Goose) decide from a built-in model catalog whether a model
can take images, and silently strip images for self-hosted models they do not recognise, even
when the model supports vision. An MCP tool sidesteps that: the image never travels through the
agent's model, only the text answer does.

```
agent (text-only model) ──MCP──▶ vision-mcp ──HTTPS/HTTP──▶ LiteLLM ──▶ vision model (Qwen)
                     describe_image(paths, question)        image + question  ▶  text
```

- Python 3.12, official `mcp` SDK, Starlette/uvicorn, Pillow, httpx. Hash-pinned dependencies.
- Streamable HTTP on `127.0.0.1` behind a bearer token, run in Docker (Engine inside WSL2).
  `python -m vision_mcp --stdio` also works for clients that launch the server themselves.
- `describe_image` is the only tool, read-only, and has no access to anything outside the images folder.

## Quick start (Windows + WSL2 + Docker Engine)

```powershell
cd "C:\Users\hoyen\Documents\Roo Code Workspace\mcp-servers\vision-mcp-server"
.\scripts\setup.ps1     # interactive: backend key, bearer token, images folder -> writes .env
.\scripts\start.ps1     # builds the image, starts the container, prints the Goose config
.\scripts\smoke.ps1 "C:\Users\me\Pictures\some.png"    # optional end-to-end check
```

Every script has a `.sh` original (run it in WSL2 or Linux) and a `.ps1` twin that forwards into WSL2.

| Script | What it does |
|---|---|
| `setup` | Interactive `.env` creation; secrets are read silently and never printed |
| `start` / `stop` | Build + start (waits for healthy) / stop and remove |
| `restart [--recreate]` | Restart; `--recreate` is needed after editing `.env` |
| `rebuild [--keep-cache]` | Rebuild the image and recreate the container |
| `logs [-n N] [--no-follow]` | JSON-line logs |
| `status` | State, health, endpoint (exit 1 if not running) |
| `smoke [IMAGE] [QUESTION]` | Health, auth, tool list and (optionally) a real `describe_image` call |

## Configuration (`.env`)

`.env` is git-ignored and excluded from the Docker build. Placeholders beginning `replace-me` are rejected at startup.

| Variable | Default | Meaning |
|---|---|---|
| `VISION_API_KEY` | required | Key for the vision endpoint (LiteLLM key) |
| `VISION_BASE_URL` | `http://spark-8013.taileaa4a8.ts.net:4000/v1` | OpenAI-compatible base URL (`/chat/completions` is appended) |
| `VISION_MODEL` | `qwen3.8-27b` | Model that receives the images |
| `MCP_AUTH_TOKEN` | required (HTTP) | Bearer token clients must send; at least 32 characters |
| `HOST_IMAGES_DIR` | required | WSL path of the **only** folder mounted into the container (read-only) |
| `WINDOWS_IMAGES_DIR` | none | The same folder spelled as Windows paths, so agents can pass paths they see |
| `HOST_PORT` | `8766` | Published on `127.0.0.1` only |
| `VISION_TIMEOUT_SECONDS` / `VISION_MAX_TOKENS` / `VISION_TEMPERATURE` | `180` / `3000` / `0.2` | Backend call limits; tokens include the model's reasoning |
| `MAX_IMAGES_PER_CALL` / `MAX_IMAGE_BYTES` / `MAX_IMAGE_SIDE` / `MAX_ANSWER_CHARS` | `4` / `20 MiB` / `2048` / `20000` | Bounds; larger images are downscaled |
| `LOG_LEVEL` | `INFO` | |

### Which images can the agent use?

Only files under `HOST_IMAGES_DIR`. Paths are accepted in three spellings and mapped into the container:

| Agent passes | Mapped via |
|---|---|
| `C:\Users\me\Pictures\shots\a.png` (any slash style, case-insensitive) | `WINDOWS_IMAGES_DIR` |
| `/mnt/c/Users/me/Pictures/shots/a.png` | `HOST_IMAGES_DIR` |
| `shots/a.png` or `/images/shots/a.png` | the container folder directly |

**Keep the folder narrow.** Anything image-like beneath it can be read and sent to the vision model
if an agent (or an injected instruction) asks. A dedicated folder (for example
`C:\Users\me\Pictures\agent-images`) is safer than a whole profile folder. To let an agent see an image
elsewhere, copy it into that folder first. Missing files and files outside the folder produce the
same generic error, so the tool cannot be used to probe the filesystem.

### Telling the agent where images must go

The agent chooses file paths (a browser tool may save a screenshot anywhere), so the server tells it the
rule in three places, all built from `WINDOWS_IMAGES_DIR` / `HOST_IMAGES_DIR`:

1. **Tool description** and 2. **server `instructions`**: "Images must be inside `<folder>`; if you captured a
   screenshot elsewhere, copy or save it there first, then pass its path here." Clients decide whether they show
   `instructions` to the model; the description is always shown.
3. **The error** for a missing or out-of-bounds image names the folder and says to copy the file there and retry.
   It is identical for "missing" and "outside", so it still cannot be used to probe the filesystem.

In a test, Goose (which first tried its own image tool) copied a screenshot from Temp into the folder and
retried successfully. This is guidance to a model, not enforcement: for deterministic placement, also set the
screenshot tool's own output folder to a path under `HOST_IMAGES_DIR`.

## Connecting a client

**Goose** (`config.yaml`, `extensions:` section; `scripts/start.ps1` prints this with the right port):

```yaml
  vision:
    name: vision
    description: Image understanding for text-only models (describe_image)
    type: streamable_http
    enabled: true
    uri: http://127.0.0.1:8766/mcp
    headers:
      Authorization: "Bearer <MCP_AUTH_TOKEN from .env>"
    timeout: 300
```

Start a new Goose chat after adding it (extensions are fixed when a chat starts).

**Claude Code** (`.mcp.json`):

```json
{ "mcpServers": { "vision": { "type": "http", "url": "http://127.0.0.1:8766/mcp",
  "headers": { "Authorization": "Bearer <MCP_AUTH_TOKEN>" } } } }
```

**stdio** (no Docker, no bearer token; the client launches it):

```bash
VISION_API_KEY=... IMAGES_DIR=/path/to/images python -m vision_mcp --stdio
```

## The tool

`describe_image(paths: string[1..4], question?: string)` returns
`{ description, model, images: [{width, height, mime, downscaled}], truncated }`.
`question` defaults to a general description plus exact text transcription. For text that must be exact,
ask a second, narrower question to cross-check the first answer.

## Security model

- **Network:** the port is published on `127.0.0.1` only (no TLS). Every route except `GET /healthz` needs
  `Authorization: Bearer <token>` (constant-time comparison); Host/Origin must be loopback (DNS-rebinding defence).
- **Container:** non-root (UID 10001), read-only root filesystem, all capabilities dropped,
  `no-new-privileges`, pids/memory/CPU limits, images mounted read-only.
- **Files:** each path is mapped into the images folder, fully resolved (symlinks included) and checked to stay
  inside it. Files are verified with Pillow (not trusted by extension), size-limited, and pixel-limited against
  decompression bombs.
- **No agent-controlled URLs:** the backend URL comes from configuration only, so the tool cannot be steered into
  requests to other hosts (no SSRF). URL inputs are rejected as "not found".
- **Untrusted output:** the answer comes from a model reading arbitrary images; text inside an image is data, not
  instructions. The tool description and the prompt sent to the vision model both say so.
- **Privacy:** images go only to your configured backend. Logs hold tool name, duration and outcome, never paths,
  questions, answers or credentials, and a redaction filter scrubs anything resembling a key as a safety net.
- On a `/mnt/c` checkout POSIX file modes are not enforced for `.env`; clone into the WSL filesystem for stronger isolation.

## Development

```bash
make install     # venv from the hash-pinned requirements-dev.txt (Python >= 3.12)
make check       # ruff + mypy --strict + pytest (coverage gate >= 85%)
make lock        # re-resolve and re-hash requirements*.txt with uv (never hand-edit them)
make audit       # pip-audit the locked runtime dependencies
```

Run `make` inside WSL/Linux (the Makefile uses `.venv/bin`); `VENV=~/.venvs/vision-mcp make check` keeps the venv
off the Windows drive.

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| `Image not found, or it is outside the folder this server may read.` | Path is not under `HOST_IMAGES_DIR`, or `WINDOWS_IMAGES_DIR` does not match how the path is spelled |
| `Cannot reach the vision backend` | Tailscale/Spark down, or the container cannot resolve the host (test: `docker exec vision-mcp python -c "import urllib.request as u;print(u.urlopen('<base>/health/liveliness').status)"`) |
| `rejected the API key` | Wrong `VISION_API_KEY` (edit `.env`, then `restart --recreate`) |
| `produced no answer` | The model spent all `VISION_MAX_TOKENS` thinking; raise it or ask a narrower question |
| Container exits at start | `scripts/logs.ps1 -n 30 --no-follow`; invalid config prints field names, never values |
