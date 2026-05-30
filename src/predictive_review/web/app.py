"""FastAPI app factory.

The factory is the unit of dependency injection: tests construct the app
with their own service via `app.dependency_overrides[get_service]` and
production wires through the default builder. Each method on the
SessionService opens its own DB transaction, so the service can be
treated as a singleton-per-request without harm.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from ..sessions.service import SessionService

WEB_DIR = Path(__file__).parent
TEMPLATES = Jinja2Templates(directory=str(WEB_DIR / "templates"))


def get_service() -> SessionService:
    """Production dependency that builds the default SessionService.

    Tests override this via `app.dependency_overrides[get_service]`.
    """
    from ..wiring import build_default_service

    return build_default_service()


def create_app() -> FastAPI:
    app = FastAPI(title="Predictive Review")
    app.mount(
        "/static",
        StaticFiles(directory=str(WEB_DIR / "static")),
        name="static",
    )
    from .routes import register_routes

    register_routes(app)
    return app
