.PHONY: install test lint audit check

install:  ## Install exact locked dependencies
	npm ci --ignore-scripts

test:  ## node:test with coverage (no Docker or network needed)
	npm test

lint:  ## Syntax-check all sources (no extra dependencies)
	npm run check
	for f in tests/*.mjs; do node --check $$f; done

audit:  ## Scan locked runtime dependencies for known vulnerabilities
	npm audit --omit=dev

check: lint test
