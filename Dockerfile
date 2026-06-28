FROM python:3.12-slim

LABEL org.opencontainers.image.title="Dragon Agent"
LABEL org.opencontainers.image.description="Python agent framework with FastAPI gateway, 16 platform adapters, 80+ tools"

WORKDIR /app

# Install system dependencies
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        curl \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

EXPOSE 8000

CMD ["python", "-m", "dragon", "gateway", "start", "--feishu"]
