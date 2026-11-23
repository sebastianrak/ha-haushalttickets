"""Server settings, read from environment variables."""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Settings with the prefix HAUSHALTTICKETS_."""

    model_config = SettingsConfigDict(env_prefix="HAUSHALTTICKETS_")

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
