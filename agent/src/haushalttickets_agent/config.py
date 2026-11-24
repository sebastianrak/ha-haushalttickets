"""Settings of the runner, read from the environment (``agent.env`` via systemd)."""

from __future__ import annotations

from dataclasses import dataclass, field
import os
from pathlib import Path
import shlex

PREFIX = "HT_AGENT_"


class ConfigError(Exception):
    """A required setting is missing or invalid."""


@dataclass(frozen=True)
class Config:
    """Runner settings. Secrets stay out of the AI's environment."""

    tickets_url: str
    tickets_token: str
    ha_url: str
    ha_token: str
    # CA bundle for HTTPS to Home Assistant (own xca CA); None: system default.
    ha_ca_file: str | None = None
    # Command that starts ha-mcp over stdio (pinned version in its own venv).
    ha_mcp_command: list[str] = field(
        default_factory=lambda: ["/opt/haushalttickets-agent/ha-mcp/bin/ha-mcp"]
    )
    claude_command: str = "claude"
    model: str = "opus"
    time_limit_minutes: int = 120
    language: str = "de"
    state_dir: Path = Path("/var/lib/haushalttickets-agent")
    # git@github.com:<owner>/<private repo>.git; empty: commit locally only.
    git_remote: str = ""
    git_ssh_key: str = ""
    label_name: str = "KI-Hausmeister"
    label_id: str = "ki_hausmeister"

    @property
    def repo_dir(self) -> Path:
        """Git working copy of the exported Home Assistant configuration."""
        return self.state_dir / "ha-config"

    @property
    def run_dir(self) -> Path:
        """Per-run scratch files (MCP configs, journals)."""
        return self.state_dir / "runs"

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> Config:
        """Read ``HT_AGENT_*`` variables."""
        env = dict(os.environ if env is None else env)

        def get(name: str, default: str | None = None) -> str:
            value = env.get(PREFIX + name, default)
            if value is None or value == "":
                if default is None:
                    raise ConfigError(f"{PREFIX}{name} is not set")
                return default
            return value

        try:
            limit = int(get("TIME_LIMIT_MINUTES", "120"))
        except ValueError as err:
            raise ConfigError(f"{PREFIX}TIME_LIMIT_MINUTES: {err}") from None
        if not 10 <= limit <= 600:
            raise ConfigError(f"{PREFIX}TIME_LIMIT_MINUTES must be 10-600")
        ha_mcp = get("HA_MCP", "/opt/haushalttickets-agent/ha-mcp/bin/ha-mcp")
        return cls(
            tickets_url=get("TICKETS_URL").rstrip("/"),
            tickets_token=get("TICKETS_TOKEN"),
            ha_url=get("HA_URL").rstrip("/"),
            ha_token=get("HA_TOKEN"),
            ha_ca_file=get("HA_CA_FILE", "") or None,
            ha_mcp_command=shlex.split(ha_mcp),
            claude_command=get("CLAUDE", "claude"),
            model=get("MODEL", "opus"),
            time_limit_minutes=limit,
            language=get("LANGUAGE", "de"),
            state_dir=Path(get("STATE_DIR", "/var/lib/haushalttickets-agent")),
            git_remote=get("GIT_REMOTE", ""),
            git_ssh_key=get("GIT_SSH_KEY", ""),
        )
