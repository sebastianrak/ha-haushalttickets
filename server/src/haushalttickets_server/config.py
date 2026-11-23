"""Server settings, read from environment variables."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Settings with the prefix HAUSHALTTICKETS_."""

    model_config = SettingsConfigDict(env_prefix="HAUSHALTTICKETS_")

    host: str = "127.0.0.1"
    port: int = 8080
