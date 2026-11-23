"""FastAPI application."""

from fastapi import FastAPI

from . import __version__


def create_app() -> FastAPI:
    """Create the application."""
    app = FastAPI(title="Haushalttickets", version=__version__)

    @app.get("/api/health")
    async def health() -> dict[str, str]:
        """Liveness check used by the Home Assistant integration and monitoring."""
        return {"status": "ok", "version": __version__}

    return app
