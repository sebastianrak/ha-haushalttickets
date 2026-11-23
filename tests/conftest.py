"""Fixtures: a real Haushalttickets server in a subprocess, Home Assistant in-process.

The server runs from its own Python environment, given by
``HAUSHALTTICKETS_SERVER_PYTHON`` (default ``server/.venv/bin/python``), against the
PostgreSQL database in ``HAUSHALTTICKETS_TEST_DATABASE_URL``. That database is wiped.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
import os
from pathlib import Path
import subprocess

from homeassistant.auth.models import User
from homeassistant.const import CONF_API_TOKEN, CONF_URL
from homeassistant.core import HomeAssistant
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.haushalttickets.const import DOMAIN

ROOT = Path(__file__).resolve().parent.parent
SERVER_PYTHON = os.environ.get(
    "HAUSHALTTICKETS_SERVER_PYTHON", str(ROOT / "server" / ".venv" / "bin" / "python")
)
DB_URL = os.environ.get(
    "HAUSHALTTICKETS_TEST_DATABASE_URL",
    "postgresql+psycopg://hht:hht@127.0.0.1/hht_test",
)

# Runs in the server environment: wipe everything except tokens and migrations,
# then restore the user that migration 0002 creates.
_RESET = """
import sys
from sqlalchemy import create_engine, text
from haushalttickets_server.models import Base
engine = create_engine(sys.argv[1])
keep = {"api_tokens"}
tables = ", ".join(t.name for t in Base.metadata.sorted_tables if t.name not in keep)
with engine.begin() as conn:
    conn.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
    conn.execute(text(
        "INSERT INTO users (ha_user_id, display_name, role, ha_is_admin, active) "
        "VALUES ('automation', 'Automation', 'adult', false, true)"
    ))
"""

_WIPE = """
import sys
from sqlalchemy import create_engine, text
engine = create_engine(sys.argv[1])
with engine.begin() as conn:
    conn.execute(text("DROP SCHEMA public CASCADE"))
    conn.execute(text("CREATE SCHEMA public"))
"""


@dataclass(frozen=True)
class Server:
    """A running server."""

    url: str
    token: str

    def reset(self) -> None:
        """Empty all tables except the API tokens."""
        _run("-c", _RESET, DB_URL)


def _env(**extra: str) -> dict[str, str]:
    return {
        **os.environ,
        "HAUSHALTTICKETS_ENV_FILE": "/nonexistent/server.env",
        "HAUSHALTTICKETS_DATABASE_URL": DB_URL,
        **{f"HAUSHALTTICKETS_{key.upper()}": value for key, value in extra.items()},
    }


def _run(*args: str) -> str:
    result = subprocess.run(
        [SERVER_PYTHON, *args], env=_env(), capture_output=True, text=True, check=False
    )
    if result.returncode:
        raise RuntimeError(result.stderr)
    return result.stdout


# pytest-socket blocks sockets in this process during tests, so helpers that need
# the network run in the server environment.
_FREE_PORT = """
import socket
with socket.socket() as sock:
    sock.bind(("127.0.0.1", 0))
    print(sock.getsockname()[1])
"""

_WAIT = """
import sys, time, urllib.request
for _ in range(100):
    try:
        urllib.request.urlopen(sys.argv[1] + "/api/health", timeout=1)
        sys.exit(0)
    except OSError:
        time.sleep(0.1)
sys.exit("Server did not start")
"""


@pytest.fixture(scope="session")
def server(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Server]:
    """Start the server once per test session."""
    if not Path(SERVER_PYTHON).exists():
        pytest.skip(f"No server environment at {SERVER_PYTHON}")
    try:
        _run("-c", _WIPE, DB_URL)
    except RuntimeError as err:
        if os.environ.get("HAUSHALTTICKETS_TEST_REQUIRE_DB"):
            raise
        pytest.skip(f"PostgreSQL not available: {err}")
    _run("-m", "haushalttickets_server", "migrate")
    token = _run("-m", "haushalttickets_server", "token", "create", "test").strip()

    port = int(_run("-c", _FREE_PORT))
    process = subprocess.Popen(
        [SERVER_PYTHON, "-m", "haushalttickets_server", "serve"],
        env=_env(
            host="127.0.0.1",
            port=str(port),
            allowed_clients="127.0.0.1",
            attachments_dir=str(tmp_path_factory.mktemp("attachments")),
        ),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    url = f"http://127.0.0.1:{port}"
    try:
        _run("-c", _WAIT, url)
        yield Server(url=url, token=token)
    finally:
        process.terminate()
        process.wait(timeout=10)


@pytest.fixture(autouse=True)
def _local_sockets(socket_enabled: None) -> None:
    """Allow sockets; Home Assistant's harness still only lets them reach 127.0.0.1."""


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Load custom_components in every test."""


@pytest.fixture
def clean_server(server: Server) -> Server:
    """The server with empty tables."""
    server.reset()
    return server


@dataclass
class Household:
    """Home Assistant users with person entities."""

    alex: User
    robin: User
    kind: User


@pytest.fixture
async def household(hass: HomeAssistant) -> Household:
    """Alex (HA admin), Robin and Kind, each linked to a person."""
    alex = await hass.auth.async_create_user("Alex", group_ids=["system-admin"])
    robin = await hass.auth.async_create_user("Robin", group_ids=["system-users"])
    kind = await hass.auth.async_create_user("Kind", group_ids=["system-users"])
    for name, user in (("alex", alex), ("robin", robin), ("kind", kind)):
        hass.states.async_set(f"person.{name}", "home", {"user_id": user.id})
    return Household(alex=alex, robin=robin, kind=kind)


@pytest.fixture
def entry(clean_server: Server) -> MockConfigEntry:
    """Config entry pointing at the test server."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="Haushalttickets",
        data={CONF_URL: clean_server.url, CONF_API_TOKEN: clean_server.token},
    )


@pytest.fixture
async def loaded(
    hass: HomeAssistant, entry: MockConfigEntry, household: Household
) -> AsyncIterator[MockConfigEntry]:
    """The integration set up against the test server."""
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    yield entry
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
