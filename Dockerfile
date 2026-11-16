# --- shared base ---
FROM python:3.14-slim AS base

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

# UV_PYTHON_DOWNLOADS=0: use the base image Python only; never download a managed interpreter.
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0 \
    PATH="/app/.venv/bin:$PATH"

# llama-cpp-python may need a compiler when no matching wheel exists for this Python.
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-install-project --no-dev

COPY src ./src
COPY migrations ./migrations
COPY alembic.ini ./

# --- production / default (api + worker) ---
FROM base AS runtime
RUN uv sync --locked --no-dev
CMD ["fastapi", "run", "--host", "0.0.0.0", "--port", "8000"]

# --- tests only ---
FROM base AS test
COPY tests ./tests
RUN uv sync --locked --group dev
CMD ["uv", "run", "pytest"]
