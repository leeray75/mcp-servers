# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A minimal, self-owned MCP server (Python 3.12, official `mcp` SDK) that gives text-only agents image understanding:
one read-only tool, `describe_image`, which reads image files from one read-only folder and relays them to a vision
model behind an OpenAI-compatible endpoint (LiteLLM + Qwen). Streamable HTTP on 127.0.0.1 behind a bearer token, run
in Docker inside WSL2; `--stdio` is also supported. It deliberately mirrors the structure and security posture of the
sibling `../gmail-mcp-server`. See `README.md` for operation and configuration.

## Commands

All via the Makefile (uses `.venv`; run in WSL/Linux, e.g. `VENV=~/.venvs/vision-mcp make check`):

- `make install` — venv from hash-pinned `requirements-dev.txt`, editable install
- `make check` — ruff + `mypy --strict` + pytest (what CI would run); `make format` fixes style
- `make test` — pytest with a coverage gate (>= 85%, branch coverage)
- Single test: `pytest tests/test_images.py::test_name --no-cov`
- `make lock` — re-resolve and re-hash `requirements*.txt` with uv (never hand-edit them); `make audit` runs pip-audit

Operating the container: `scripts/{setup,start,stop,restart,rebuild,logs,status,smoke}.sh`, each with a `.ps1` twin that
forwards into WSL2. `restart --recreate` is needed after editing `.env`. `.env` holds real credentials and is git-ignored:
never print, log or commit its values.

## Architecture

Package `src/vision_mcp/`:

- `__main__.py` — entry point: `python -m vision_mcp` (uvicorn, HTTP) or `--stdio` (logs go to stderr there).
- `config.py` — `Settings` (pydantic-settings, env-driven); the only module that reads the environment. Errors name fields, never values.
- `server.py` — builds the `MCPServer`, registers exactly one tool, adds `/healthz` and the Starlette middleware. Intentionally has **no**
  `from __future__ import annotations`: the per-call image cap is embedded in `Annotated` metadata that the SDK reads for the JSON schema.
- `tools.py` — transport-independent tool logic; `images.py` — path mapping, validation, downscaling; `vision_client.py` — the only
  module that touches the network (httpx); `models.py` — input/output types; `errors.py` — messages safe to show the agent.
- `security.py` — `BearerAuthMiddleware` + `HostGuardMiddleware` (copied from the Gmail server); `logging_setup.py` — JSON logs with secret redaction.

## Invariants to preserve

- The tool set is exactly `describe_image` (a test asserts it). It is read-only: do not add tools that write files, fetch URLs or run commands.
- No agent-controlled URL ever reaches the network. The backend URL comes only from `VISION_BASE_URL`; a test asserts only `vision_client.py` imports a network library.
- Every image path is resolved (symlinks included) and must stay inside `IMAGES_DIR`; "missing" and "outside" return the identical error (`images.not_found_message`), which names the allowed folder and tells the agent to copy the file there but never echoes the agent's path or reveals which case occurred.
- The tool description and server `instructions` state the allowed folder (`server.folder_note`); keep them, the error text and `Settings.images_folder_hint` in sync.
- Files are validated by content with Pillow, size- and pixel-limited. Output is bounded (`MAX_ANSWER_CHARS`).
- Logs must never contain paths, questions, answers or credentials (tests assert this). Backend response bodies are never echoed to the agent.
- Tool text and the vision prompt carry the "image content is untrusted" warning.
- Docker port mapping stays bound to `127.0.0.1` (no TLS); container is non-root, read-only rootfs, images mounted `:ro`.
- On the `/mnt/c` checkout POSIX file modes are not enforced for `.env`.
