# Panda Agent Makefile
# =====================
# Common operations for development, testing, and deployment.
#
# Usage:
#   make test       Run unit tests
#   make test-cov   Run tests with coverage report
#   make deploy     One-click deployment (interactive)
#   make deploy-q   One-click deployment (non-interactive)
#   make clean      Remove build artifacts
#   make lint       Run ruff linter on the project
#   make format     Auto-format code with ruff
#   make serve      Start development server
#   make install    Install dependencies in current environment

VENV := .venv
PYTHON := python3
PIP := $(VENV)/bin/pip
PYTEST := $(VENV)/bin/pytest
UVICORN := $(VENV)/bin/uvicorn
RUFF := $(VENV)/bin/ruff

.PHONY: test test-cov deploy deploy-q clean lint format serve install help

# ── Default target ──────────────────────────────────────────────────

help:
	@echo "Panda Agent — available targets:"
	@echo ""
	@echo "  make test        Run unit tests"
	@echo "  make test-cov    Run tests with HTML coverage report"
	@echo "  make deploy      One-click deployment (interactive)"
	@echo "  make deploy-q    One-click deployment (non-interactive)"
	@echo "  make clean       Remove __pycache__, .pytest_cache, etc."
	@echo "  make lint        Lint with ruff"
	@echo "  make format      Auto-format with ruff"
	@echo "  make serve       Start development server (port 8000)"
	@echo "  make install     Install all deps in current environment"

# ── Testing ─────────────────────────────────────────────────────────

test:
	@echo "=== Running unit tests ==="
	PYTHONPATH=. $(PYTHON) -m pytest tests/ -v --tb=short

test-cov:
	@echo "=== Running tests with coverage ==="
	PYTHONPATH=. $(PYTHON) -m pytest tests/ -v --tb=short \
		--cov=panda --cov-report=html --cov-report=term

# ── Deployment ──────────────────────────────────────────────────────

deploy:
	bash scripts/deploy.sh

deploy-q:
	bash scripts/deploy.sh --quick

# ── Server ──────────────────────────────────────────────────────────

serve:
	PYTHONPATH=. $(PYTHON) -m uvicorn panda.main:app \
		--host 0.0.0.0 --port 8000 --reload --log-level info

# ── Code Quality ────────────────────────────────────────────────────

lint:
	@echo "=== Linting ==="
	$(PYTHON) -m ruff check panda/ tests/

format:
	@echo "=== Formatting ==="
	$(PYTHON) -m ruff check --fix panda/ tests/
	$(PYTHON) -m ruff format panda/ tests/

# ── Install ─────────────────────────────────────────────────────────

install:
	@echo "=== Installing Panda Agent ==="
	$(PIP) install --upgrade pip
	$(PIP) install \
		fastapi uvicorn pydantic pyyaml python-dotenv \
		httpx openai tenacity networkx \
		llama-cpp-python
	@echo ""
	@echo "For knowledge base (optional, ~500MB extra):"
	@echo "  $(PIP) install chromadb sentence-transformers"
	@echo ""
	@echo "For development:"
	@echo "  $(PIP) install pytest pytest-asyncio pytest-cov ruff"

# ── Clean ───────────────────────────────────────────────────────────

clean:
	@echo "=== Cleaning ==="
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .pytest_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name htmlcov -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name .coverage -delete 2>/dev/null || true
	find . -type f -name '*.pyc' -delete 2>/dev/null || true
	@echo "Clean."
