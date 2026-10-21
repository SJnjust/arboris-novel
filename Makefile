PYTHON ?= $(CURDIR)/backend/.venv/bin/python
NPM ?= npm

.PHONY: test check type-check build

test:
	cd backend && $(PYTHON) -m pytest

type-check:
	cd frontend && $(NPM) run type-check

build:
	cd frontend && $(NPM) run build-only

check: test type-check build
