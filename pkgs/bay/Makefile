.PHONY: dev test lint format clean docker-build docker-run

# Development
dev:
	uv run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

# Testing
test:
	uv run pytest -v

test-cov:
	uv run pytest -v --cov=app --cov-report=html

# Linting & Formatting
lint:
	uv run ruff check app tests

format:
	uv run ruff format app tests
	uv run ruff check --fix app tests

# Type checking
typecheck:
	uv run mypy app

# Clean
clean:
	rm -rf __pycache__ .pytest_cache .mypy_cache .ruff_cache
	rm -rf htmlcov .coverage
	rm -f *.db
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true

# Docker
docker-build:
	docker build -t bay:latest .

docker-run:
	docker run --rm -it \
		-p 8000:8000 \
		-v /var/run/docker.sock:/var/run/docker.sock \
		bay:latest

# Database
db-init:
	uv run python -c "from app.db import init_db; import asyncio; asyncio.run(init_db())"

# Dependencies
sync:
	uv sync

sync-dev:
	uv sync --all-extras
