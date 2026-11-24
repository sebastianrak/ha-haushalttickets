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

    @field_validator("allowed_clients")
    @classmethod
    def _validate_allowed_clients(cls, value: str) -> str:
        for part in filter(None, (p.strip() for p in value.split(","))):
            ip_network(part, strict=False)
        return value

    @cached_property
    def allowed_networks(self) -> list[IPv4Network | IPv6Network]:
        """Parsed ``allowed_clients``."""
        return [
            ip_network(part, strict=False)
            for part in (p.strip() for p in self.allowed_clients.split(","))
            if part
        ]
