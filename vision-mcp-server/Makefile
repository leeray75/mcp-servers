.PHONY: install lint format typecheck test audit lock check

PYTHON ?= python
VENV ?= .venv
BIN := $(VENV)/bin

install:  ## Create a venv with the hash-pinned dev dependencies (Python >= 3.12)
	$(PYTHON) -m venv $(VENV)
	$(BIN)/pip install --require-hashes --only-binary=:all: -r requirements-dev.txt
	$(BIN)/pip install --no-deps -e .

lint:  ## ruff lint + format check
	$(BIN)/ruff check .
	$(BIN)/ruff format --check .

format:  ## Apply ruff formatting and safe fixes
	$(BIN)/ruff check --fix .
	$(BIN)/ruff format .

typecheck:  ## mypy --strict
	$(BIN)/mypy

test:  ## pytest with coverage gate (>= 85%)
	$(BIN)/pytest

audit:  ## Scan locked runtime dependencies for known vulnerabilities
	$(BIN)/pip-audit --requirement requirements.txt --require-hashes --disable-pip

lock:  ## Re-resolve and re-hash the dependency locks
	$(BIN)/uv pip compile pyproject.toml --generate-hashes --universal --python-version 3.12 --no-header -o requirements.txt
	$(BIN)/uv pip compile pyproject.toml --extra dev --generate-hashes --universal --python-version 3.12 --no-header -o requirements-dev.txt

check: lint typecheck test  ## Everything CI would run
