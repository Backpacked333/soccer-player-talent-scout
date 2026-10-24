PYTHON ?= 3.11
UV ?= uv
NPM ?= npm
WEB_HOST ?= 127.0.0.1
BACKEND_EXTRAS ?= --extra dev
RUN = $(UV) run --project backend --frozen $(BACKEND_EXTRAS)

.PHONY: setup db api worker worker-once web lint typecheck test build check audit

setup:
	$(UV) sync --project backend --python $(PYTHON) --frozen $(BACKEND_EXTRAS)
	$(NPM) --prefix frontend ci

db:
	$(RUN) python -m app.db

api:
	$(RUN) uvicorn app.main:app --host 127.0.0.1 --port 8000

worker:
	$(RUN) python -m app.worker

worker-once:
	$(RUN) python -m app.worker --once

web:
	$(NPM) --prefix frontend run dev -- --host $(WEB_HOST) --port 5173

lint:
	$(RUN) ruff check --config backend/pyproject.toml backend/app backend/tests
	$(NPM) --prefix frontend run lint

typecheck:
	$(RUN) mypy --config-file backend/pyproject.toml backend/app
	$(NPM) --prefix frontend run typecheck

test:
	$(RUN) pytest backend/tests
	$(NPM) --prefix frontend test

build:
	$(NPM) --prefix frontend run build

check: lint typecheck test build

audit:
	$(RUN) pip-audit --local --skip-editable
	$(NPM) --prefix frontend audit --audit-level=low
