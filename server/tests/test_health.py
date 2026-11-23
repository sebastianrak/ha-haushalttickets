"""Tests for the health endpoint."""

from fastapi.testclient import TestClient

from haushalttickets_server import __version__
from haushalttickets_server.app import create_app


def test_health() -> None:
    """The health endpoint reports ok and the version."""
    client = TestClient(create_app())
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": __version__}
