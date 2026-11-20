# syntax=docker/dockerfile:1
# One Dockerfile, four targets:
#   api         FastAPI service. No ML libraries.
#   dev         api + lint, type-check and test tools. Source is bind-mounted.
#   worker-gpu  adds PyTorch. Used for GPU jobs and the GPU spikes.
#   worker-cpu  api + PyTorch's CPU build (ml-cpu extra), for CPU training (ADR-0014 rule 11).

FROM ghcr.io/astral-sh/uv:0.9.30 AS uv

FROM python:3.12-slim-bookworm AS base
COPY --from=uv /uv /uvx /bin/
# Machine-specific extra CAs (docker/certs/README.md). An empty folder is fine.
COPY docker/certs/ /usr/local/share/ca-certificates/stratal/
RUN update-ca-certificates
ENV SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt \
    REQUESTS_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt \
    NODE_EXTRA_CA_CERTS=/etc/ssl/certs/ca-certificates.crt \
    UV_NATIVE_TLS=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_PYTHON_DOWNLOADS=never \
    PATH=/opt/venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1
WORKDIR /app

FROM base AS api
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project
COPY alembic.ini ./
COPY migrations ./migrations
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev
EXPOSE 8000
CMD ["python", "-m", "stratal.api"]

FROM base AS dev
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project
COPY . .
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen
# The pyright wrapper fetches the type checker on first use; do it now so later runs are offline.
RUN pyright --version
CMD ["pytest"]

FROM base AS worker-gpu
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --group test --extra gpu --no-install-project
COPY alembic.ini ./
COPY migrations ./migrations
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --group test --extra gpu
CMD ["python", "-c", "import torch; print(torch.cuda.is_available())"]

FROM base AS worker-cpu
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --group test --extra ml-cpu --no-install-project
COPY alembic.ini ./
COPY migrations ./migrations
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --group test --extra ml-cpu
CMD ["celery", "-A", "stratal.runtime.celery_app", "worker", "-Q", "cpu", "-n", "cpu@%h"]
