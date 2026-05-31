"""Logging setup, called once per process from web app and CLI startup.

Module loggers live under the predictive_review.* namespace so callers
can dial them up or down independently of uvicorn / sqlalchemy. The
default level is INFO; set LOG_LEVEL=DEBUG in .env to see the chatty
component-level traces (LLM call shapes, transition details).
"""

from __future__ import annotations

import logging
import os

_CONFIGURED = False


def configure_logging() -> None:
    """Idempotent. Safe to call from CLI and web entrypoints both."""
    global _CONFIGURED
    if _CONFIGURED:
        return
    level_name = os.environ.get("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)

    handler = logging.StreamHandler()
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s %(levelname)-5s %(name)s — %(message)s",
            datefmt="%H:%M:%S",
        )
    )
    root_logger = logging.getLogger("predictive_review")
    root_logger.setLevel(level)
    root_logger.addHandler(handler)
    root_logger.propagate = False  # don't double-log through root

    _CONFIGURED = True
