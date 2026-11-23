"""Tests for the health endpoint and the migration."""

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from haushalttickets_server import __version__
from haushalttickets_server.models import Base


def test_health(app) -> None:
    """The health endpoint reports ok and the version, without a token."""
    response = TestClient(app).get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": __version__}


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
