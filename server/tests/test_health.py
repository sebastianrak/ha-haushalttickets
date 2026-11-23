"""Tests for the health endpoint, the client allowlist and the migration."""

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from fastapi.testclient import TestClient
from pydantic import ValidationError
import pytest
from sqlalchemy import create_engine

from haushalttickets_server import __version__
from haushalttickets_server.app import create_app
from haushalttickets_server.config import Settings
from haushalttickets_server.models import Base


def test_health(app) -> None:
    """The health endpoint reports ok and the version, without a token."""
    response = TestClient(app).get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": __version__}


def test_allowed_clients(database_url: str, tmp_path) -> None:
    """Only listed addresses may use the API; health stays reachable."""

    def client_for(allowed: str) -> TestClient:
        settings = Settings(
            database_url=database_url, attachments_dir=tmp_path, allowed_clients=allowed
        )
        return TestClient(create_app(settings), client=("172.16.1.10", 50000))

    blocked = client_for("172.16.3.0/24")
    assert blocked.get("/api/health").status_code == 200
    response = blocked.get("/api/v1/tickets")
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "client_not_allowed"

    allowed = client_for("172.16.1.10, 127.0.0.1")
    assert allowed.get("/api/v1/tickets").status_code == 401  # passes, needs token

    with pytest.raises(ValidationError):
        Settings(allowed_clients="not-an-address")


def test_migrations_match_models(database_url: str) -> None:
    """The migrations produce exactly the schema the models describe."""
    engine = create_engine(database_url)
    with engine.connect() as conn:
        diff = compare_metadata(
            MigrationContext.configure(conn, opts={"compare_type": True}),
            Base.metadata,
        )
    engine.dispose()
    assert diff == []
