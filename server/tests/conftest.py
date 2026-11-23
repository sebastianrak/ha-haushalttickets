"""Test fixtures. Tests run against a real PostgreSQL database.

Set ``HAUSHALTTICKETS_TEST_DATABASE_URL``; the database is wiped.
"""

from __future__ import annotations

from collections.abc import Iterator
import os

# Never pick up the settings file of a real installation.
os.environ["HAUSHALTTICKETS_ENV_FILE"] = "/nonexistent/server.env"
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient
import httpx
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError

from haushalttickets_server import services
from haushalttickets_server.app import create_app
from haushalttickets_server.config import Settings
from haushalttickets_server.db import upgrade
from haushalttickets_server.enums import AUTOMATION_USER_ID, Role
from haushalttickets_server.models import Base, User
from haushalttickets_server.schemas import UserSyncItem

DB_URL = os.environ.get(
    "HAUSHALTTICKETS_TEST_DATABASE_URL",
    "postgresql+psycopg://hht:hht@127.0.0.1/hht_test",
)

# name -> (Home Assistant user id, display name, HA admin, role)
HOUSEHOLD = {
    "sebastian": ("u-sebastian", "Sebastian", True, Role.ADMIN),
    "sarah": ("u-sarah", "Sarah", False, Role.ADULT),
    "kind": ("u-kind", "Kind", False, Role.CHILD),
    "ki": ("u-ki", "KI-Hausmeister", True, Role.AI_AGENT),
}


def uid(name: str) -> str:
    """Home Assistant user id of a household member (or ``automation``)."""
    if name == AUTOMATION_USER_ID:
        return AUTOMATION_USER_ID
    return HOUSEHOLD[name][0]


@pytest.fixture(scope="session")
def database_url() -> str:
    """Fresh schema, migrated to head."""
    engine = create_engine(DB_URL)
    try:
        with engine.connect() as conn:
            conn.execute(text("DROP SCHEMA public CASCADE"))
            conn.execute(text("CREATE SCHEMA public"))
            conn.commit()
    except OperationalError:
        if os.environ.get("HAUSHALTTICKETS_TEST_REQUIRE_DB"):
            raise
        pytest.skip("PostgreSQL not available")
    finally:
        engine.dispose()
    upgrade(DB_URL)
    return DB_URL


@pytest.fixture
def app(database_url: str, tmp_path) -> Iterator[FastAPI]:
    """Application with an empty database."""
    application = create_app(
        Settings(
            database_url=database_url,
            attachments_dir=tmp_path / "attachments",
            max_attachment_bytes=1024,
        )
    )
    yield application
    tables = ", ".join(table.name for table in Base.metadata.sorted_tables)
    with application.state.engine.begin() as conn:
        conn.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
    application.state.engine.dispose()


@pytest.fixture
def token(app: FastAPI) -> str:
    """API token."""
    with app.state.sessionmaker() as session:
        value = services.create_token(session, "test")
        session.commit()
    return value


@pytest.fixture
def household(app: FastAPI) -> None:
    """Sebastian (admin), Sarah (adult), Kind (child), KI-Hausmeister (AI agent)."""
    with app.state.sessionmaker() as session:
        services.sync_users(
            session,
            [
                UserSyncItem(ha_user_id=user_id, display_name=name, is_admin=is_admin)
                for user_id, name, is_admin, _ in HOUSEHOLD.values()
            ],
        )
        for user_id, _, _, role in HOUSEHOLD.values():
            session.get(User, user_id).role = role
        # Migration 0002 creates this user; the per-test TRUNCATE removes it again.
        session.execute(
            text(
                "INSERT INTO users (ha_user_id, display_name, role, ha_is_admin, active) "
                "VALUES ('automation', 'Automation', 'adult', false, true) "
                "ON CONFLICT DO NOTHING"
            )
        )
        session.commit()


class _As:
    def __init__(self, client: TestClient, headers: dict[str, str]) -> None:
        self._client = client
        self._headers = headers

    def request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        headers = {**self._headers, **kwargs.pop("headers", {})}
        return self._client.request(method, url, headers=headers, **kwargs)

    def get(self, url: str, **kwargs: Any) -> httpx.Response:
        return self.request("GET", url, **kwargs)

    def post(self, url: str, **kwargs: Any) -> httpx.Response:
        return self.request("POST", url, **kwargs)

    def patch(self, url: str, **kwargs: Any) -> httpx.Response:
        return self.request("PATCH", url, **kwargs)

    def put(self, url: str, **kwargs: Any) -> httpx.Response:
        return self.request("PUT", url, **kwargs)

    def delete(self, url: str, **kwargs: Any) -> httpx.Response:
        return self.request("DELETE", url, **kwargs)


class Api:
    """Test client acting as a household member: ``api("sarah").post(...)``."""

    def __init__(self, client: TestClient, token: str) -> None:
        self.client = client
        self.token = token

    def __call__(self, name: str | None = None, via: str = "ui") -> _As:
        headers = {"Authorization": f"Bearer {self.token}"}
        if name is not None:
            headers["X-Acting-User"] = uid(name)
            headers["X-Acting-Via"] = via
        return _As(self.client, headers)


@pytest.fixture
def api(app: FastAPI, token: str, household: None) -> Api:
    """Authenticated client with the household set up."""
    return Api(TestClient(app), token)


def create(api: Api, name: str = "sebastian", via: str = "ui", **body: Any) -> dict:
    """Create a ticket and return it."""
    body.setdefault("type", "repair")
    body.setdefault("title", "Wasserhahn Küche tropft")
    response = api(name, via).post("/api/v1/tickets", json=body)
    assert response.status_code == 201, response.text
    return response.json()


def error_code(response: httpx.Response) -> str:
    """Error code of a failed request."""
    return response.json()["detail"]["code"]
