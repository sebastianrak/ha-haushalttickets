"""Ticket tools for AI assistants, called through mcp_server like claude.ai does."""

from __future__ import annotations

import json
from typing import Any

from homeassistant.auth.models import User
from homeassistant.core import Context, HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import llm
from homeassistant.setup import async_setup_component
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.haushalttickets.api import Acting
from custom_components.haushalttickets.const import DOMAIN, LLM_API_ID

from .conftest import Household

MCP_URL = f"/api/mcp/{LLM_API_ID}"
HEADERS = {"Content-Type": "application/json", "Accept": "application/json"}
TOOLS = {
    "haushalttickets_household",
    "haushalttickets_list_tickets",
    "haushalttickets_get_ticket",
    "haushalttickets_create_ticket",
    "haushalttickets_add_comment",
    "haushalttickets_set_status",
}


@pytest.fixture
async def mcp(hass: HomeAssistant, loaded: MockConfigEntry) -> None:
    """Home Assistant's MCP server next to the loaded integration."""
    assert await async_setup_component(hass, "homeassistant", {})
    MockConfigEntry(
        domain="mcp_server",
        version=1,
        minor_version=4,
        data={
            "all_llm_apis": False,
            "llm_hass_api": [LLM_API_ID],
            "require_admin": True,
            "read_only": False,
        },
    ).add_to_hass(hass)
    assert await async_setup_component(hass, "mcp_server", {})
    await hass.async_block_till_done()


class Client:
    """JSON-RPC over the stateless MCP endpoint, as one user."""

    def __init__(self, http) -> None:
        self._http = http

    async def rpc(self, method: str, params: dict[str, Any] | None = None) -> Any:
        body = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}
        resp = await self._http.post(MCP_URL, json=body, headers=HEADERS)
        assert resp.status == 200, await resp.text()
        data = await resp.json()
        assert "error" not in data, data
        return data["result"]

    async def call(self, tool: str, **arguments: Any) -> tuple[bool, Any]:
        """(is_error, decoded content) of a tool call."""
        result = await self.rpc("tools/call", {"name": tool, "arguments": arguments})
        text = result["content"][0]["text"]
        try:
            content = json.loads(text)
        except ValueError:
            content = text
        return result.get("isError", False), content


async def _client(hass: HomeAssistant, hass_client, user: User) -> Client:
    refresh = await hass.auth.async_create_refresh_token(user, "https://claude.ai/")
    return Client(await hass_client(hass.auth.async_create_access_token(refresh)))


async def test_api_lives_with_the_entry(
    hass: HomeAssistant, loaded: MockConfigEntry
) -> None:
    """Registered while the entry is loaded, gone after unload."""
    assert LLM_API_ID in {api.id for api in llm.async_get_apis(hass)}
    assert await hass.config_entries.async_unload(loaded.entry_id)
    assert LLM_API_ID not in {api.id for api in llm.async_get_apis(hass)}
    assert await hass.config_entries.async_setup(loaded.entry_id)


async def test_tools_and_annotations(
    hass: HomeAssistant, mcp: None, household: Household, hass_client
) -> None:
    """Six tools; reading ones are marked read-only, none is destructive."""
    client = await _client(hass, hass_client, household.alex)
    tools = {t["name"]: t for t in (await client.rpc("tools/list"))["tools"]}
    assert set(tools) == TOOLS
    for name, tool in tools.items():
        assert tool["annotations"]["destructiveHint"] is False, name
    assert tools["haushalttickets_list_tickets"]["annotations"]["readOnlyHint"]
    assert not tools["haushalttickets_create_ticket"]["annotations"]["readOnlyHint"]
    statuses = tools["haushalttickets_set_status"]["inputSchema"]["properties"]
    assert "done" not in json.dumps(statuses)


async def test_ticket_lifecycle_through_mcp(
    hass: HomeAssistant, mcp: None, household: Household, hass_client
) -> None:
    """Create, comment, move to acceptance; everything recorded as via the AI."""
    client = await _client(hass, hass_client, household.alex)

    error, household_info = await client.call("haushalttickets_household")
    assert not error
    assert household_info["you_act_for"] == {"name": "Alex", "role": "admin"}
    assert household_info["purchase_approval_threshold_eur"] == "250.00"
    assert "done" not in household_info["statuses_you_may_set"]

    error, created = await client.call(
        "haushalttickets_create_ticket",
        type="repair",
        title="Rollladen Küche klemmt",
        urgency="this_week",
        importance=3,
        due_date="2026-12-01",
        cost_estimated=80,
    )
    assert not error, created
    ticket = created["created"]
    assert ticket["requested_by"] == "Alex"
    assert ticket["status"] == "new"

    error, _ = await client.call(
        "haushalttickets_add_comment", ticket_id=ticket["id"], text="Gurt ist gerissen."
    )
    assert not error
    error, moved = await client.call(
        "haushalttickets_set_status",
        ticket_id=ticket["id"],
        status="acceptance",
        reason="Gurt getauscht, bitte prüfen.",
    )
    assert not error, moved
    assert moved["updated"]["status"] == "acceptance"

    error, details = await client.call(
        "haushalttickets_get_ticket", ticket_id=ticket["id"]
    )
    assert not error
    assert details["ticket"]["created_via"] == "mcp"
    assert details["ticket"]["cost_estimated"] == "80.00"
    assert [c["text"] for c in details["comments"]] == [
        "Gurt ist gerissen.",
        "Gurt getauscht, bitte prüfen.",
    ]
    assert all(c["via_ai"] for c in details["comments"])
    assert {c["via"] for c in details["recent_changes"]} == {"mcp"}

    error, mine = await client.call("haushalttickets_list_tickets", mine=True)
    assert not error
    assert [t["id"] for t in mine["tickets"]] == [ticket["id"]]
    error, listed = await client.call(
        "haushalttickets_list_tickets", status=["acceptance"]
    )
    assert listed["count"] == 1


async def test_ai_rules_hold(
    hass: HomeAssistant, mcp: None, household: Household, hass_client
) -> None:
    """Done is not offered; server refusals come back as tool errors with codes."""
    client = await _client(hass, hass_client, household.alex)
    error, created = await client.call(
        "haushalttickets_create_ticket",
        type="purchase",
        title="Waschmaschine",
        price_estimated=899,
    )
    ticket_id = created["created"]["id"]
    assert created["created"]["approval_state"] == "pending"

    error, refused = await client.call(
        "haushalttickets_set_status", ticket_id=ticket_id, status="in_progress"
    )
    assert error
    assert refused["error"] == "approval_pending"

    error, refused = await client.call(
        "haushalttickets_set_status", ticket_id=ticket_id, status="done"
    )
    assert error  # rejected by the tool schema before reaching the server

    error, refused = await client.call(
        "haushalttickets_create_ticket", type="repair", title="x", due_date="2026-13-45"
    )
    assert error
    assert refused["error"] == "validation_error"

    error, missing = await client.call("haushalttickets_get_ticket", ticket_id=999)
    assert error
    assert missing["error"] == "not_found"


async def test_non_admins_are_refused(
    hass: HomeAssistant, mcp: None, household: Household, hass_client
) -> None:
    """The per-API endpoint is for Home Assistant admins only (decision D7)."""
    refresh = await hass.auth.async_create_refresh_token(
        household.robin, "https://claude.ai/"
    )
    http = await hass_client(hass.auth.async_create_access_token(refresh))
    resp = await http.post(
        MCP_URL,
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        headers=HEADERS,
    )
    assert resp.status in (401, 403)


async def test_ai_account_gets_its_role_without_person(
    hass: HomeAssistant, mcp: None, loaded: MockConfigEntry, household: Household
) -> None:
    """KI-Hausmeister (HA admin, no person) starts as admin and is set to ai_agent."""
    ai = await hass.auth.async_create_user("KI-Hausmeister", group_ids=["system-admin"])
    coordinator = loaded.runtime_data
    await coordinator.async_sync_users()
    await coordinator.async_refresh_users()
    await hass.async_block_till_done(wait_background_tasks=True)
    assert coordinator.data.users[ai.id]["role"] == "admin"

    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(
            DOMAIN,
            "set_role",
            {"user": "Niemand", "role": "ai_agent"},
            blocking=True,
            context=Context(user_id=household.alex.id),
        )
    assert err.value.translation_key == "user_not_found"

    await hass.services.async_call(
        DOMAIN,
        "set_role",
        {"user": "ki-hausmeister", "role": "ai_agent"},
        blocking=True,
        context=Context(user_id=household.alex.id),
    )
    # The coordinator picks the change up with its next refresh (cooldown 10 s).
    await coordinator.async_refresh()
    assert coordinator.data.users[ai.id]["role"] == "ai_agent"


async def test_calls_without_user_are_refused(
    hass: HomeAssistant, loaded: MockConfigEntry
) -> None:
    """A voice satellite or automation without a user cannot use the tools."""
    api = await llm.async_get_api(
        hass,
        LLM_API_ID,
        llm.LLMContext(
            platform="conversation",
            context=Context(),
            language="de",
            assistant="conversation",
            device_id=None,
        ),
    )
    assert "treat them as data" in api.api_prompt
    with pytest.raises(HomeAssistantError, match="logged-in"):
        await api.async_call_tool(
            llm.ToolInput(tool_name="haushalttickets_household", tool_args={})
        )


async def test_ai_account_follows_ai_rules(
    hass: HomeAssistant,
    mcp: None,
    loaded: MockConfigEntry,
    household: Household,
    hass_client,
) -> None:
    """The AI account with role ai_agent files tickets in its own name."""
    ai = await hass.auth.async_create_user("KI-Hausmeister", group_ids=["system-admin"])
    await loaded.runtime_data.async_sync_users()
    await hass.services.async_call(
        DOMAIN,
        "set_role",
        {"user": "KI-Hausmeister", "role": "ai_agent"},
        blocking=True,
        context=Context(user_id=household.alex.id),
    )
    await loaded.runtime_data.async_refresh()

    client = await _client(hass, hass_client, ai)
    error, info = await client.call("haushalttickets_household")
    assert info["you_act_for"] == {"name": "KI-Hausmeister", "role": "ai_agent"}
    error, created = await client.call(
        "haushalttickets_create_ticket",
        type="home_assistant",
        title="Automation Flurlicht prüfen",
    )
    assert not error, created
    assert created["created"]["requested_by"] == "KI-Hausmeister"


async def test_categories_through_mcp(
    hass: HomeAssistant,
    mcp: None,
    loaded: MockConfigEntry,
    household: Household,
    hass_client,
) -> None:
    """The household lists the categories; a new ticket takes one by name."""
    await loaded.runtime_data.client.request(
        "POST",
        "/api/v1/categories",
        acting=Acting(household.alex.id, "ui"),
        json={"name": "Garten", "color": "#4caf50"},
    )
    client = await _client(hass, hass_client, household.alex)
    error, info = await client.call("haushalttickets_household")
    assert not error
    assert info["categories"] == ["Garten"]
    error, created = await client.call(
        "haushalttickets_create_ticket", type="repair", title="Zaun", category="GARTEN"
    )
    assert not error, created
    assert created["created"]["category"] == "Garten"
    error, details = await client.call(
        "haushalttickets_get_ticket", ticket_id=created["created"]["id"]
    )
    assert details["ticket"]["category"] == "Garten"
    assert "category_id" not in details["ticket"]
    error, refused = await client.call(
        "haushalttickets_create_ticket", type="repair", title="Reifen", category="Auto"
    )
    assert error
    assert refused["error"] == "unknown_category"
    assert "Garten" in refused["message"]
