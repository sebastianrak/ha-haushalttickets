"""ha-mcp over stdio: the runner's own connection and the AI's MCP config."""

from __future__ import annotations

from contextlib import AsyncExitStack, suppress
import json
import os
from pathlib import Path
from typing import Any

from .config import Config
from .tools import SERVER


class HaMcpError(Exception):
    """A tool call failed."""


def server_env(config: Config, *, yaml_edit: bool, confirm: bool) -> dict[str, str]:
    """Environment of the ha-mcp process (holds the Home Assistant token)."""
    env = {
        "HOMEASSISTANT_URL": config.ha_url,
        "HOMEASSISTANT_TOKEN": config.ha_token,
        "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
        "HOME": os.environ.get("HOME", str(config.state_dir)),
        "LANG": "C.UTF-8",
        # Reading files (packages, configuration.yaml) needs the beta file tools;
        # writing YAML (decision 52) the YAML tool. Both need ha_mcp_tools in HA.
        "ENABLE_BETA_FEATURES": "true",
        "HAMCP_ENABLE_FILESYSTEM_TOOLS": "true",
        "ENABLE_YAML_CONFIG_EDITING": "true" if yaml_edit else "false",
        "ENABLE_YAML_EDIT_CONFIRM": "true" if confirm else "false",
    }
    if config.ha_ca_file:
        env["SSL_CERT_FILE"] = config.ha_ca_file
    return env


def write_mcp_config(config: Config, path: Path, *, yaml_edit: bool) -> Path:
    """MCP config for ``claude --mcp-config`` (readable only by the runner)."""
    data = {
        "mcpServers": {
            SERVER: {
                "type": "stdio",
                "command": config.ha_mcp_command[0],
                "args": config.ha_mcp_command[1:],
                "env": server_env(config, yaml_edit=yaml_edit, confirm=True),
            }
        }
    }
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(data, handle)
    return path


def _payload(result: Any) -> Any:
    structured = getattr(result, "structuredContent", None)
    if structured:
        # FastMCP wraps non-object results as {"result": ...}.
        return (
            structured.get("result", structured) if len(structured) == 1 else structured
        )
    texts = [c.text for c in result.content if getattr(c, "type", "") == "text"]
    text = "\n".join(texts)
    try:
        return json.loads(text)
    except ValueError:
        return text


class HaMcp:
    """The runner's own ha-mcp session (exports, config check, restore)."""

    def __init__(self, config: Config, *, yaml_edit: bool = True) -> None:
        """Initialize."""
        self.config = config
        self.yaml_edit = yaml_edit
        self._stack: AsyncExitStack | None = None
        self._session: Any = None

    async def __aenter__(self) -> HaMcp:
        """Start ha-mcp and initialize the session."""
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        params = StdioServerParameters(
            command=self.config.ha_mcp_command[0],
            args=self.config.ha_mcp_command[1:],
            env=server_env(self.config, yaml_edit=self.yaml_edit, confirm=False),
            # ha-mcp reads a .env from its working directory; never inherit one
            # it may not read (e.g. /root when started by haushalttickets-agent-ctl).
            cwd=str(self.config.state_dir),
        )
        self._stack = AsyncExitStack()
        try:
            read, write = await self._stack.enter_async_context(stdio_client(params))
            self._session = await self._stack.enter_async_context(
                ClientSession(read, write)
            )
            await self._session.initialize()
        except BaseException:
            # Close in this task; otherwise asyncio complains at shutdown.
            with suppress(Exception):
                await self._stack.aclose()
            self._stack = None
            raise
        return self

    async def __aexit__(self, *exc: object) -> None:
        """Stop ha-mcp."""
        if self._stack is not None:
            await self._stack.aclose()

    async def call(self, tool: str, /, **arguments: Any) -> Any:
        """Call a tool; returns its parsed result."""
        result = await self._session.call_tool(tool, arguments)
        payload = _payload(result)
        if result.isError or (
            isinstance(payload, dict) and payload.get("success") is False
        ):
            raise HaMcpError(f"{tool}: {payload}")
        return payload
