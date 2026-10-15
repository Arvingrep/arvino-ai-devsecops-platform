"""Alembic migration environment.

Reads the database URL from $DATABASE_URL (preferred) or the alembic.ini
``sqlalchemy.url`` key (fallback).  Imports arvino.db so autogenerate can
see all mapped tables via ``Base.metadata``.

When called programmatically from ``arvino.db.init_schema`` the caller may
pass an open ``sqlalchemy.engine.Connection`` via
``config.attributes["connection"]``; in that case we reuse it instead of
creating a new engine (important for in-memory SQLite test databases).
"""
from __future__ import annotations

import os
from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool

from alembic import context

# Import the project's declarative base so autogenerate picks up all models.
from arvino.db import Base  # noqa: E402

# ---------------------------------------------------------------------------
# Alembic config object — provides access to values in alembic.ini
# ---------------------------------------------------------------------------
config = context.config

# Set up loggers defined in the .ini file.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Point autogenerate at our schema.
target_metadata = Base.metadata

# ---------------------------------------------------------------------------
# URL resolution: env var wins; ini fallback used for offline SQL dumps.
# ---------------------------------------------------------------------------
_db_url: str | None = os.environ.get("DATABASE_URL") or config.get_main_option(
    "sqlalchemy.url"
)


def run_migrations_offline() -> None:
    """Emit raw SQL to stdout without a live DB connection.

    Useful for generating migration scripts to review or apply manually.
    """
    context.configure(
        url=_db_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Apply migrations against a live DB connection.

    Supports two modes:
    1. Programmatic: caller passes an open Connection via
       ``config.attributes["connection"]`` (used by ``init_schema``).
    2. CLI / subprocess: a new engine is built from the resolved URL.
    """
    # Programmatic path — reuse an existing connection (e.g. for in-memory SQLite).
    conn = config.attributes.get("connection", None)
    if conn is not None:
        context.configure(
            connection=conn,
            target_metadata=target_metadata,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()
        return

    # CLI path — build a new engine from the URL.
    cfg_section = config.get_section(config.config_ini_section, {})
    if _db_url:
        cfg_section["sqlalchemy.url"] = _db_url

    connectable = engine_from_config(
        cfg_section,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
