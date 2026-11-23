"""FastAPI application."""

from ipaddress import ip_address

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from . import __version__
from .api import router
from .config import Settings
from .db import make_engine, make_sessionmaker
from .permissions import PermissionDenied
from .services import Invalid, NotFound


def create_app(settings: Settings | None = None) -> FastAPI:
    """Create the application."""
    settings = settings or Settings()
    app = FastAPI(title="Haushalttickets", version=__version__)
    app.state.settings = settings
    app.state.engine = make_engine(settings.database_url)
    app.state.sessionmaker = make_sessionmaker(app.state.engine)

    @app.middleware("http")
    async def allowed_clients(request: Request, call_next):
        """Refuse clients outside ``allowed_clients`` (health stays reachable)."""
        networks = settings.allowed_networks
        if networks and request.url.path != "/api/health":
            try:
                client = ip_address(request.client.host if request.client else "")
            except ValueError:
                client = None
            if client is None or not any(client in network for network in networks):
                return JSONResponse(
                    status_code=403,
                    content={
                        "detail": {
                            "code": "client_not_allowed",
                            "detail": "This address may not use the API.",
                        }
                    },
                )
        return await call_next(request)

    @app.get("/api/health")
    async def health() -> dict[str, str]:
        """Liveness check used by the Home Assistant integration and monitoring."""
        return {"status": "ok", "version": __version__}

    @app.exception_handler(PermissionDenied)
    async def permission_denied(
        request: Request, exc: PermissionDenied
    ) -> JSONResponse:
        return JSONResponse(
            status_code=403,
            content={"detail": {"code": exc.code, "detail": exc.message}},
        )

    @app.exception_handler(NotFound)
    async def not_found(request: Request, exc: NotFound) -> JSONResponse:
        return JSONResponse(
            status_code=404,
            content={"detail": {"code": "not_found", "detail": str(exc)}},
        )

    @app.exception_handler(Invalid)
    async def invalid(request: Request, exc: Invalid) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={"detail": {"code": exc.code, "detail": exc.message}},
        )

    app.include_router(router)
    return app
