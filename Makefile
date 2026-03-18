.PHONY: install dev dev-backend dev-frontend test lint format format-check typecheck check build

install:
	python3 -m venv .venv
	.venv/bin/pip install --upgrade pip
	.venv/bin/pip install -r backend/requirements-dev.txt
	cd frontend && npm install

dev:
	@echo "Run in separate terminals: make dev-backend and make dev-frontend"

dev-backend:
	cd backend && ../.venv/bin/uvicorn app.main:app --reload --port 8000

dev-frontend:
	cd frontend && npm run dev

test:
	cd backend && PYTHONPATH=. ../.venv/bin/pytest -q
	cd frontend && npm test

lint:
	cd backend && ../.venv/bin/ruff check app tests

format:
	cd backend && ../.venv/bin/ruff format app tests

format-check:
	cd backend && ../.venv/bin/ruff format --check app tests

typecheck:
	cd backend && PYTHONPATH=. ../.venv/bin/mypy --config-file mypy.ini app

check: lint format-check typecheck test

build:
	docker build --platform linux/amd64 -t ghcr.io/miles-automation/bullshit-or-fit:latest .
