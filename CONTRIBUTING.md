# Contributing

Thanks for helping improve these servers. This guide covers the conventions every change must follow. Each server's
`CLAUDE.md` is authoritative for that server's details; read it before changing code there.

## Ground rules

- **Work inside one server directory.** There is no root build, shared package or workspace tooling.
- **The servers are copies, not a shared library.** `security.py`, `logging_setup.py` and `scripts/lib/` were copied
  between servers and have drifted. If you fix a bug in one of these shared patterns, check the sibling servers for the
  same bug.
- **Never commit secrets.** `.env` holds real credentials and is git-ignored at the root and in each server. Update
  `.env.example` when you add a setting. Config errors must name the field, never the value.
- **Keep the attack surface fixed.** Each server has a test that pins its exact tool set. Adding, removing or widening
  a tool is a deliberate, spec'd change, not a side effect.
- **Keep networking contained.** Containers publish on `127.0.0.1` only. In the Python servers, outbound network
  access lives in one module, and tests never touch the network.

## Development setup

All commands run in WSL2 from inside the server's directory.

Python servers (`gmail`, `vision`, `sports`) need Python 3.12 and `uv`. The checkout lives on `/mnt/c`, where POSIX file
modes aren't enforced, so put the virtualenv on the Linux filesystem:

```bash
make install VENV=~/.venvs/sports-mcp PYTHON=python3
```

firecrawl needs Node 22:

```bash
make install
```

## Before you open a change

Run the full gate for every server you touched:

```bash
make check
```

For Python servers this runs `ruff`, `mypy --strict` and pytest with the coverage gate (85%, sports 90%); for firecrawl
it runs a syntax check and the tests. Run `make audit` as well when you change dependencies. To run a single Python test
without failing the coverage gate:

```bash
pytest tests/test_security.py --no-cov
```

If you touched the container or scripts, also verify end to end:

```bash
scripts/rebuild.sh && scripts/smoke.sh
```

## Dependencies

- Python `requirements*.txt` files are **hash-pinned**. Regenerate them with `make lock`; never edit them by hand.
- firecrawl pins the upstream `firecrawl-mcp` to an exact version. Bumping it means re-checking the gateway and API
  filter against the new release and noting it in that server's changelog.

## Scripts and line endings

- Every operations script ships as a `.sh` plus a `.ps1` twin that forwards into WSL2. Change both together.
- `.sh` files must stay LF. Each server's `.gitattributes` enforces this; don't override it in your editor.

## Feature workflow and documentation

This project uses **Spec-Driven Development (SDD)**, whether the work is done by a person or an AI agent. The full
workflow, including the rules every AI agent must follow, is in [AGENTS.md](AGENTS.md). In short:

1. **Plan** with the human: resolve every ambiguity up front, as choices with a recommended option and an "Other" option.
2. **Spec**: update `docs/SPEC.md`, or add an amendment (for example `docs/SPEC-search-filter.md`) that states what it overrides.
3. **Approval gate**: no code is written until the human explicitly approves the spec (approve / revise / reject / wait / other).
4. **Implementation**, with a report: `docs/reports/<feature>/implementation-report.md`.
5. **Independent review** and **fix rounds**: `review-round-N.md` / `fix-round-N.md`, for at most 5 rounds before escalating to the human.
6. **Summary report**: `summary-report.md`.

Also, for every user-visible change:

- Add an entry under `## [Unreleased]` in the server's `CHANGELOG.md`
  ([Keep a Changelog](https://keepachangelog.com/en/1.1.0/) format: Added / Changed / Deprecated / Removed / Fixed / Security).
- Update the server's `README.md` if usage, configuration or tools changed.
- Use the root `CHANGELOG.md` only for changes that span the repository.

## Versioning and releases

Servers are versioned independently with [Semantic Versioning](https://semver.org/). The version lives in
`pyproject.toml` (Python) or `package.json` (firecrawl). To release:

1. Move the `[Unreleased]` entries into a new `## [x.y.z] - YYYY-MM-DD` section.
2. Bump the version in the manifest.
3. Tag the commit with the server name as a prefix, for example `sports-mcp-server/v0.2.0`.

## Commits

- Write commit messages in the imperative mood ("Add odds caching", not "Added…").
- Scope a commit to one server where possible, and name it in the subject when it isn't obvious, for example
  `sports: handle postponed games`.
- Keep unrelated changes in separate commits.
