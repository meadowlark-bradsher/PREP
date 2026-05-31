"""FastAPI app factory.

The factory is the unit of dependency injection: tests construct the app
with their own service via `app.dependency_overrides[get_service]` and
production wires through the default builder. Each method on the
SessionService opens its own DB transaction, so the service can be
treated as a singleton-per-request without harm.
"""

from __future__ import annotations

import logging
import traceback
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from ..logging_config import configure_logging
from ..sessions.service import SessionService
from .markdown import render_markdown

logger = logging.getLogger("predictive_review.web")

WEB_DIR = Path(__file__).parent
TEMPLATES = Jinja2Templates(directory=str(WEB_DIR / "templates"))
TEMPLATES.env.filters["markdown"] = render_markdown


def get_service() -> SessionService:
    """Production dependency that builds the default SessionService.

    Tests override this via `app.dependency_overrides[get_service]`.
    """
    from ..wiring import build_default_service

    return build_default_service()


def create_app() -> FastAPI:
    configure_logging()
    app = FastAPI(title="Predictive Review")
    app.mount(
        "/static",
        StaticFiles(directory=str(WEB_DIR / "static")),
        name="static",
    )
    from .routes import register_routes

    register_routes(app)

    @app.exception_handler(Exception)
    async def _log_unhandled_exceptions(
        request: Request, exc: Exception
    ) -> JSONResponse:
        # Anything not caught by the route's own HTTPException branches
        # lands here. Log with traceback + request context so a 405 or 500
        # is diagnosable from the server logs alone.
        logger.error(
            "Unhandled exception on %s %s: %s\n%s",
            request.method,
            request.url.path,
            exc,
            traceback.format_exc(),
        )
        return JSONResponse(
            status_code=500,
            content={"detail": "Internal server error"},
        )

    logger.info("Predictive Review web app ready")
    return app
