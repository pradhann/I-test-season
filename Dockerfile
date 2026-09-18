# The Railway image. DEPLOYMENT.md §10 and §11.1.
#
# One service, one process, one volume at /app/data, replicas = 1. DuckDB
# permits one writer per file, so the cap is structural and railway.toml states
# it rather than leaving it to a default.
#
# linux/amd64 and Python 3.11, which is what requires-python asks for. The
# darwin-only dependencies are excluded by their own markers in
# pyproject.toml; the final layer asserts that the markers did their job rather
# than trusting them.

# --------------------------------------------------------------------------
# Stage 1: build the virtual environment.
# --------------------------------------------------------------------------
FROM --platform=linux/amd64 python:3.11-slim-bookworm AS build

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1

# build-essential is needed by a handful of wheels that have no manylinux
# build for this platform. It stays in this stage and never reaches the
# runtime image.
RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# The dependency layer is keyed on pyproject.toml alone, so an edit to a
# Python file does not reinstall scipy.
COPY pyproject.toml /app/pyproject.toml
RUN mkdir -p /app/fpl_edge && touch /app/fpl_edge/__init__.py \
 && pip install --no-cache-dir /app

COPY fpl_edge /app/fpl_edge
COPY fpl_mcp /app/fpl_mcp
COPY scripts /app/scripts
RUN pip install --no-cache-dir --no-deps /app

# --------------------------------------------------------------------------
# Stage 2: the runtime image.
# --------------------------------------------------------------------------
FROM --platform=linux/amd64 python:3.11-slim-bookworm AS runtime

# Which commit is serving. The image carries no .git, so repo_sha() reads this
# and every panel's provenance stamp stays traceable.
ARG GIT_SHA=unknown

ENV PATH="/opt/venv/bin:$PATH"
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

# Which commit is serving, read by registry.repo_sha().
ENV FPL_EDGE_REPO_SHA=${GIT_SHA}

# Run the boot sequence at startup: mount check, tmp sweep, schema, the
# per-package migrations, the seed copy, the artefact census.
ENV FPL_EDGE_BOOT=1

# Read copies go on the volume. The container layer is small and ephemeral,
# and filling it kills the container.
ENV TMPDIR=/app/data/tmp

# No authenticated FPL request can be made from this host. routes_account is
# loopback-only and unreachable behind the proxy anyway; this makes the panels
# say so with a named reason instead of falling back silently.
ENV FPL_EDGE_DISABLE_PRIVATE=1

WORKDIR /app

COPY --from=build /opt/venv /opt/venv
COPY fpl_edge /app/fpl_edge
COPY fpl_mcp /app/fpl_mcp
COPY scripts /app/scripts
COPY docs /app/docs
COPY pyproject.toml /app/pyproject.toml
COPY web/dist /app/web/dist

# The seed: the git-tracked files under data/, which the volume mount at
# /app/data would otherwise hide. Read-only, copied out by boot step 5 only
# where the target does not already exist. .dockerignore names exactly which
# files reach the context.
COPY data /app/seed/data

# The mount point, so a container started with no volume still has a directory
# to fail the mount check against rather than a confusing path error.
RUN mkdir -p /app/data

# --------------------------------------------------------------------------
# The assertion layer. It fails the BUILD, which is the point: a check that
# ran at container start would let a bad image reach the registry.
# --------------------------------------------------------------------------
RUN set -eu; \
    ! python -c "import mlx" 2>/dev/null || (echo "mlx present"; exit 1); \
    ! python -c "import mlx_whisper" 2>/dev/null || (echo "mlx_whisper present"; exit 1); \
    ! command -v pbpaste >/dev/null 2>&1 || (echo "pbpaste present"; exit 1); \
    ! test -e "$HOME/.local/bin/claude" || (echo "claude hard path present"; exit 1); \
    ! find /app -name "*.duckdb" -o -name "*.duckdb.wal" | grep -q . || (echo "duckdb in image"; exit 1); \
    ! test -e /app/.env || (echo ".env in image"; exit 1); \
    ! test -e /app/seed/data/warehouse/jobs/telegram.log || (echo "telegram.log in the seed"; exit 1); \
    ! env | grep -q "^ANTHROPIC_" || (echo "ANTHROPIC_ set at build"; exit 1); \
    python -c "import fpl_edge.platform.boot, fpl_edge.platform.scheduler"; \
    echo "image assertions passed"

# uvicorn binds 0.0.0.0 here, not the 127.0.0.1 the Mac plist uses: inside a
# container the proxy is the only thing that can reach it, and a loopback bind
# would answer nothing. /api/query is still an unauthenticated SQL endpoint,
# which is what workstreams D and E exist to fix.
EXPOSE 8000
CMD ["sh", "-c", "exec uvicorn --factory fpl_edge.platform.app:create_app --host 0.0.0.0 --port ${PORT:-8000}"]
