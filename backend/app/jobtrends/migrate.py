"""Programmatic `alembic upgrade head`.

The worker (and CLI) own migrations for the jobtrends engine — deliberately NOT
the web container's entrypoint, so the public landing/lead app never depends on
Postgres being reachable at boot. Running here keeps the schema current wherever
ingestion runs, regardless of the process's working directory.
"""

from __future__ import annotations

import logging
from pathlib import Path

from alembic import command
from alembic.config import Config

logger = logging.getLogger(__name__)

# app/jobtrends/migrate.py -> parents[2] == backend/
_BACKEND_DIR = Path(__file__).resolve().parents[2]


def _alembic_config() -> Config:
    cfg = Config(str(_BACKEND_DIR / "alembic.ini"))
    # Absolute script_location so it resolves no matter the cwd (container /app or local backend/).
    cfg.set_main_option("script_location", str(_BACKEND_DIR / "alembic"))
    return cfg


def upgrade_to_head() -> None:
    logger.info("jobtrends: applying migrations (alembic upgrade head)")
    command.upgrade(_alembic_config(), "head")
