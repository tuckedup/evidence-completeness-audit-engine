FROM python:3.12.11-slim-bookworm AS builder
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_NO_CACHE_DIR=1
WORKDIR /build
COPY pyproject.toml README.md ./
COPY src ./src
RUN python -m venv /opt/venv && /opt/venv/bin/pip install --upgrade pip==25.2 && \
    /opt/venv/bin/pip install .

FROM python:3.12.11-slim-bookworm AS runtime
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONHASHSEED=0 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    ECSAE_REDIS_URL=redis://redis:6379/0
RUN groupadd --system ecsae && useradd --system --gid ecsae --home-dir /app ecsae
COPY --from=builder /opt/venv /opt/venv
WORKDIR /app
USER ecsae
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2)"]
CMD ["gunicorn", "ecsae.api.main:app", "-k", "uvicorn.workers.UvicornWorker", "-w", "4", "--keep-alive", "5", "-b", "0.0.0.0:8000"]
