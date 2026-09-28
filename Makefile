.PHONY: install web serve dev test lint check docker

PY ?= python3

install:            ## Python package (editable) + dev tools, and the web UI dependencies
	$(PY) -m pip install -e ".[dev]"
	cd web && npm install

web:                ## Build the web UI into web/dist (served by `trades serve`)
	cd web && npm run build

serve: web          ## Build the UI and start the app on http://127.0.0.1:8000
	trades serve

dev:                ## API on :8000 plus the Vite dev server with hot reload on :5173
	trades serve & cd web && npm run dev

test:               ## Python test suite
	$(PY) -m pytest

lint:
	ruff check trades tests
	cd web && npm run typecheck

check: lint test web

docker:             ## Build and run in Docker (http://127.0.0.1:8000)
	docker compose up --build
