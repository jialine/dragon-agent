FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml .
RUN pip install --no-cache-dir -e ".[dev]" 2>/dev/null || pip install --no-cache-dir pydantic pyyaml python-dotenv structlog fastapi uvicorn openai httpx tenacity networkx

COPY panda/ panda/
COPY config.yaml.example config.yaml

VOLUME ["/app/models", "/app/panda_data"]
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

CMD ["python", "-m", "uvicorn", "panda.main:app", "--host", "0.0.0.0", "--port", "8000"]
