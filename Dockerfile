# syntax=docker/dockerfile:1
FROM python:3.12-slim-bookworm AS build
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /src
COPY pyproject.toml README.md alembic.ini ./
COPY src ./src
COPY alembic ./alembic
RUN python -m venv /opt/venv && /opt/venv/bin/pip install --upgrade pip && /opt/venv/bin/pip install ".[temporal]"

FROM python:3.12-slim-bookworm
ENV PATH=/opt/venv/bin:$PATH PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 ARVINO_ALEMBIC_INI=/app/alembic.ini
RUN groupadd --gid 10001 arvino && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin arvino
COPY --from=build /opt/venv /opt/venv
COPY --from=build --chown=10001:10001 /src/alembic.ini /app/alembic.ini
COPY --from=build --chown=10001:10001 /src/alembic /app/alembic
USER 10001:10001
WORKDIR /tmp
EXPOSE 8080
HEALTHCHECK --interval=15s --timeout=3s --retries=5 CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=2).status == 200 else 1)"]
CMD ["uvicorn", "arvino.main:app", "--host", "0.0.0.0", "--port", "8080"]
