"""ASGI entrypoint: ``uvicorn arvino.main:app``."""

from __future__ import annotations

import logging

from arvino.api import create_app
from arvino.bootstrap import build_engine
from arvino.settings import Settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

_settings = Settings()
app = create_app(build_engine(_settings), _settings)
