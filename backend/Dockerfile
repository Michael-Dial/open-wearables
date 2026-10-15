FROM python:3.14-slim AS builder

    RUN apt-get update && \
    apt-get install -y --no-install-recommends build-essential libpq-dev && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*
    RUN pip install --no-cache-dir --upgrade pyopenssl

    COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

    WORKDIR /root_project

    # Install dependencies first (cached unless lock/pyproject change)
    COPY uv.lock pyproject.toml ./
    ENV VIRTUAL_ENV=/opt/venv
    ENV UV_PROJECT_ENVIRONMENT=/opt/venv
    RUN uv venv $VIRTUAL_ENV
    ENV PATH="$VIRTUAL_ENV/bin:$PATH"
    RUN uv sync --locked --no-dev --extra otel --no-install-project

    # Copy source and install the project itself (fast, deps already cached)
    COPY . .
    RUN uv sync --locked --no-dev --extra otel --no-editable

FROM python:3.14-slim

    RUN apt-get update && \
    apt-get install -y --no-install-recommends libpq5 && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

    WORKDIR /root_project

    COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

    COPY --from=builder /opt/venv /opt/venv
    COPY --from=builder /root_project /root_project
    ENV VIRTUAL_ENV=/opt/venv
    ENV PATH="/opt/venv/bin:$PATH"
    ENV UV_PROJECT_ENVIRONMENT=/opt/venv
    ENV UV_NO_SYNC=true

    # Run as non-root: OSU Container Services rejects UID 0. UID 1001 in group 0
    # owns the working dir, where beat writes celerybeat.pid and its schedule.
    # uv writes a cache under $HOME, which this user doesn't have, so point both
    # at /tmp. See docs/fork/DECISIONS.md.
    RUN useradd -r -u 1001 -g 0 appuser && \
        chown -R 1001:0 /root_project && \
        chmod -R g=u /root_project
    ENV UV_CACHE_DIR=/tmp/uv-cache HOME=/tmp

    # Commit SHA for Sentry release tracking. Optional: defaults to empty when
    # not passed (e.g. local builds), in which case the app falls back to the
    # plain package version.
    ARG GIT_SHA=""
    ENV GIT_SHA=$GIT_SHA

    EXPOSE 8000

    USER 1001
