"""End-to-end test: spike tools reach MCP clients through Home Assistant's mcp_server."""

from __future__ import annotations

import json
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import llm
from homeassistant.setup import async_setup_component
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.haushalttickets_spike.const import (
    DOMAIN,
    EVENT_TICKET_CREATED,
    LLM_API_ID,
)

MCP_URL = f"/api/mcp/{LLM_API_ID}"
PROTOCOL_HEADERS = {"Content-Type": "application/json", "Accept": "application/json"}


@pytest.fixture
async def setup_spike(hass: HomeAssistant) -> MockConfigEntry:
    """Set up the spike and Home Assistant's MCP server."""
    assert await async_setup_component(hass, "homeassistant", {})
    spike_entry = MockConfigEntry(domain=DOMAIN, data={})
    spike_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(spike_entry.entry_id)

    mcp_entry = MockConfigEntry(
        domain="mcp_server",
        version=1,
        minor_version=4,
        data={
            "all_llm_apis": False,
            "llm_hass_api": [LLM_API_ID],
            "require_admin": True,
            "read_only": False,
        },
    )
    mcp_entry.add_to_hass(hass)
    assert await async_setup_component(hass, "mcp_server", {})
    await hass.async_block_till_done()
    return spike_entry


async def _rpc(client, method: str, params: dict[str, Any] | None = None) -> Any:
    """Send one JSON-RPC request to the stateless MCP endpoint."""
    body = {"jsonrpc": "2.0", "id": 1, "method": method}
    if params is not None:
        body["params"] = params
    resp = await client.post(MCP_URL, json=body, headers=PROTOCOL_HEADERS)
    assert resp.status == 200, await resp.text()
    data = await resp.json()
    assert "error" not in data, data
    return data["result"]


async def test_api_registered_and_unregistered(
    hass: HomeAssistant, setup_spike: MockConfigEntry
) -> None:
    """The LLM API is registered with the entry and removed on unload."""
    assert LLM_API_ID in {api.id for api in llm.async_get_apis(hass)}
    assert await hass.config_entries.async_unload(setup_spike.entry_id)
    assert LLM_API_ID not in {api.id for api in llm.async_get_apis(hass)}


async def test_mcp_end_to_end(
    hass: HomeAssistant, setup_spike: MockConfigEntry, hass_client, hass_admin_user
) -> None:
    """List and call the tools through the MCP HTTP endpoint as admin."""
    client = await hass_client()

    init = await _rpc(
        client,
        "initialize",
        {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "test", "version": "0"},
        },
    )
    assert "tools" in init["capabilities"]

    tools = {tool["name"]: tool for tool in (await _rpc(client, "tools/list"))["tools"]}
    assert set(tools) == {
        "haushalttickets_spike_whoami",
        "haushalttickets_spike_list_tickets",
        "haushalttickets_spike_create_ticket",
    }
    assert tools["haushalttickets_spike_list_tickets"]["annotations"]["readOnlyHint"]
    create = tools["haushalttickets_spike_create_ticket"]
    assert not create["annotations"]["readOnlyHint"]
    assert not create["annotations"]["destructiveHint"]
    assert create["inputSchema"]["required"] == ["title"]

    who = await _rpc(
        client, "tools/call", {"name": "haushalttickets_spike_whoami", "arguments": {}}
    )
    who_data = json.loads(who["content"][0]["text"])
    assert who_data["user_id"] == hass_admin_user.id
    assert who_data["is_admin"] is True
    assert who_data["platform"] == "mcp_server"

    events = []
    hass.bus.async_listen(EVENT_TICKET_CREATED, events.append)
    created = await _rpc(
        client,
        "tools/call",
        {
            "name": "haushalttickets_spike_create_ticket",
            "arguments": {"title": "Wasserhahn tropft", "type": "repair"},
        },
    )
    ticket = json.loads(created["content"][0]["text"])["created"]
    assert ticket["status"] == "new"
    assert ticket["requested_by"] == hass_admin_user.name
    await hass.async_block_till_done()
    assert len(events) == 1
    assert events[0].context.user_id == hass_admin_user.id

    listed = await _rpc(
        client,
        "tools/call",
        {"name": "haushalttickets_spike_list_tickets", "arguments": {"status": "new"}},
    )
    assert json.loads(listed["content"][0]["text"])["count"] == 1


async def test_invalid_arguments_are_reported(
    hass: HomeAssistant, setup_spike: MockConfigEntry, hass_client
) -> None:
    """Schema violations come back as tool errors, not crashes."""
    client = await hass_client()
    result = await _rpc(
        client,
        "tools/call",
        {"name": "haushalttickets_spike_create_ticket", "arguments": {"title": ""}},
    )
    assert result["isError"] is True


async def test_non_admin_is_rejected(
    hass: HomeAssistant, setup_spike: MockConfigEntry, hass_client
) -> None:
    """The per-API MCP endpoint refuses non-admin users."""
    user = await hass.auth.async_create_user("Kind", group_ids=["system-users"])
    assert not user.is_admin
    refresh_token = await hass.auth.async_create_refresh_token(
        user, "https://example.com/app"
    )
    client = await hass_client(hass.auth.async_create_access_token(refresh_token))
    resp = await client.post(
        MCP_URL,
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        headers=PROTOCOL_HEADERS,
    )
    # 2026.10 answers 401 (require_admin decorator), 2026.11 answers 403.
    assert resp.status in (401, 403)
