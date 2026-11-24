"""Server settings, read from environment variables."""

from functools import cached_property
from ipaddress import IPv4Network, IPv6Network, ip_network
import os
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Settings with the prefix HAUSHALTTICKETS_."""

    model_config = SettingsConfigDict(
        env_prefix="HAUSHALTTICKETS_",
        # Read in the LXC; environment variables take precedence.
        env_file=os.environ.get(
            "HAUSHALTTICKETS_ENV_FILE", "/etc/haushalttickets/server.env"
        ),
        extra="ignore",
    )

    host: str = "127.0.0.1"
    port: int = 8080
    # Peer authentication over the local socket inside the LXC.
    database_url: str = "postgresql+psycopg:///haushalttickets"
    attachments_dir: Path = Path("/var/lib/haushalttickets/attachments")
    max_attachment_bytes: int = 20 * 1024 * 1024
    allowed_attachment_types: tuple[str, ...] = (
        "image/jpeg",
        "image/png",
        "image/heic",
        "image/webp",
        "application/pdf",
    )
    # Comma-separated addresses or networks allowed to call the API, e.g.
    # "172.16.1.10,127.0.0.1". Empty allows every client (the token still applies).
    allowed_clients: str = ""
    # Recurring tickets: local days are counted in this time zone, and the server
    # looks for rounds to create every so many minutes (0 = only on changes).
    timezone: str = "Europe/Berlin"
    recurrence_minutes: int = 15
    # Emergency web UI (HTTPS on its own port) for when Home Assistant is down. It is
    # off while this is empty: comma-separated addresses or networks that may open it,
    # e.g. "172.16.0.0/16,100.64.0.0/10".
    emergency_clients: str = ""
    emergency_port: int = 8443
    tls_dir: Path = Path("/var/lib/haushalttickets/tls")
    # Language of tickets the server files itself (e.g. "renew the certificate").
    language: str = "de"

    @field_validator("allowed_clients", "emergency_clients")
    @classmethod
    def _validate_networks(cls, value: str) -> str:
        _networks(value)
        return value

    @cached_property
    def allowed_networks(self) -> list[IPv4Network | IPv6Network]:
        """Parsed ``allowed_clients``."""
        return _networks(self.allowed_clients)

    @cached_property
    def emergency_networks(self) -> list[IPv4Network | IPv6Network]:
        """Parsed ``emergency_clients``."""
        return _networks(self.emergency_clients)


def _networks(value: str) -> list[IPv4Network | IPv6Network]:
    return [
        ip_network(part, strict=False)
        for part in (p.strip() for p in value.split(","))
        if part
    ]
