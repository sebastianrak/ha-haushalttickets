"""The real stdio plumbing: ha-mcp client and the claude subprocess."""

from __future__ import annotations

import asyncio
from pathlib import Path
import sys

import pytest

from haushalttickets_agent import claude
from haushalttickets_agent.config import Config
from haushalttickets_agent.hamcp import HaMcp, HaMcpError

FAKES = Path(__file__).parent / "fakes"


def config(tmp_path: Path) -> Config:
    return Config(
        tickets_url="http://t",
        tickets_token="r",
        ha_url="https://ha.example:8123",
        ha_token="h",
        ha_mcp_command=[sys.executable, str(FAKES / "fake_ha_mcp.py")],
        claude_command=str(FAKES / "claude"),
        state_dir=tmp_path,
    )


def test_ha_mcp_client(tmp_path: Path) -> None:
    async def go() -> None:
        async with HaMcp(config(tmp_path), yaml_edit=False) as ha:
            assert (await ha.call("ha_read_file", path="configuration.yaml"))[
                "content"
            ] == "# configuration.yaml\n"
            # A tool argument may be called "name".
            label = await ha.call("ha_config_set_label", name="KI-Hausmeister")
            assert label["label"]["name"] == "KI-Hausmeister"
            assert await ha.call("ha_whoami") == {
                "url": "https://ha.example:8123",
                "yaml": "false",
                "confirm": "false",
                "cwd": str(tmp_path),
                "settings_ui": "true",
            }
            with pytest.raises(HaMcpError):
                await ha.call("ha_fails")

    asyncio.run(go())


def test_ha_mcp_that_dies_at_start(tmp_path: Path) -> None:
    """A server that exits at once fails cleanly (no error at asyncio shutdown)."""
    config = Config(
        tickets_url="http://t",
        tickets_token="r",
        ha_url="https://ha",
        ha_token="h",
        ha_mcp_command=[sys.executable, "-c", "import sys; sys.exit(1)"],
        state_dir=tmp_path,
    )

    async def go() -> None:
        with pytest.raises(Exception):  # noqa: B017 - whatever the SDK raises
            async with HaMcp(config):
                pass

    asyncio.run(go())


def test_claude_subprocess(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HT_AGENT_HA_TOKEN", "secret")
    result = claude.run(
        config(tmp_path),
        phase="draft",
        prompt="Ticket #1",
        system_prompt="sys",
        timeout=30,
        workdir=tmp_path / "w",
        schema={"type": "object"},
    )
    assert result.ok, result.error
    assert result.data["prompt"] == "Ticket #1"
    assert result.data["phase"] == "draft"
    assert result.data["has_ha_token"] is False
    assert "--json-schema" in result.data["args"]
