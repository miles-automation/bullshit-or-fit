.PHONY: install dev dev-backend dev-frontend test lint format format-check typecheck check build

install:
	cd backend && uv sync --dev
	cd frontend && npm install

dev:
	@echo "Run in separate terminals: make dev-backend and make dev-frontend"

dev-backend:
	cd backend && uv run uvicorn app.main:app --reload --port 8000

dev-frontend:
	cd frontend && npm run dev

test:
	cd backend && PYTHONPATH=. uv run pytest -q
	cd frontend && npm test

lint:
	cd backend && uv run ruff check app tests

format:
	cd backend && uv run ruff format app tests

format-check:
	cd backend && uv run ruff format --check app tests

typecheck:
	cd backend && PYTHONPATH=. uv run mypy --config-file mypy.ini app
	cd frontend && npx tsc --noEmit

check: lint format-check typecheck test

build:
	docker build --platform linux/amd64 -t ghcr.io/miles-automation/bullshit-or-fit:latest .
