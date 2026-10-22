PYTHON ?= $(CURDIR)/backend/.venv/bin/python
NPM ?= npm

.PHONY: test check frontend-test type-check build

test:
	cd backend && $(PYTHON) -m pytest

type-check:
	cd frontend && $(NPM) run type-check

frontend-test:
	cd frontend && $(NPM) test

build:
	cd frontend && $(NPM) run build-only

check: test frontend-test type-check build
