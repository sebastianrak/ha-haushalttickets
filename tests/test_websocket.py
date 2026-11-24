"""WebSocket commands act as the logged-in user."""

from __future__ import annotations

from homeassistant.auth.models import User
from homeassistant.core import Context, HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.typing import WebSocketGenerator

from custom_components.haushalttickets.const import DOMAIN

from .conftest import Household


async def _client_for(hass: HomeAssistant, ws: WebSocketGenerator, user: User):
    refresh = await hass.auth.async_create_refresh_token(user, "https://example.test/")
    return await ws(hass, hass.auth.async_create_access_token(refresh))


async def test_request_as_logged_in_user(
    hass: HomeAssistant,
    loaded: MockConfigEntry,
    household: Household,
    hass_ws_client: WebSocketGenerator,
) -> None:
    """Robin creates a ticket through the card; the server records her."""
    client = await _client_for(hass, hass_ws_client, household.robin)
    await client.send_json_auto_id(
        {
            "type": "haushalttickets/request",
            "method": "POST",
            "path": "/api/v1/tickets",
            "body": {"type": "repair", "title": "Rollladen klemmt"},
        }
    )
    response = await client.receive_json()
    assert response["success"], response
    assert response["result"]["requested_by_id"] == household.robin.id

    await client.send_json_auto_id(
        {
            "type": "haushalttickets/request",
            "method": "GET",
            "path": "/api/v1/tickets",
            "params": {"include_closed": False},
        }
    )
    response = await client.receive_json()
    assert response["result"]["count"] == 1
    assert response["result"]["tickets"][0]["comment_count"] == 0

    # Several statuses at once, as the card asks for the archive.
    await client.send_json_auto_id(
        {
            "type": "haushalttickets/request",
            "method": "GET",
            "path": "/api/v1/tickets",
            "params": {"status": ["done", "rejected"]},
        }
    )
    response = await client.receive_json()
    assert response["success"], response
    assert response["result"]["count"] == 0


async def test_server_rules_and_blocked_paths(
    hass: HomeAssistant,
    loaded: MockConfigEntry,
    household: Household,
    hass_ws_client: WebSocketGenerator,
) -> None:
    """Permission errors come back with the server code; sync is not reachable."""
    ticket = await hass.services.async_call(
        DOMAIN,
        "create_ticket",
        {"type": "repair", "title": "Heizung"},
        blocking=True,
        return_response=True,
        context=Context(user_id=household.alex.id),
    )
    client = await _client_for(hass, hass_ws_client, household.kind)
    await client.send_json_auto_id(
        {
            "type": "haushalttickets/request",
            "method": "POST",
            "path": f"/api/v1/tickets/{ticket['id']}/status",
            "body": {"status": "done"},
        }
    )
    response = await client.receive_json()
    assert not response["success"]
    assert response["error"]["code"] == "child_status_forbidden"

    for path in ("/api/v1/users/sync", "/api/health", "/api/v1/../health"):
        await client.send_json_auto_id(
            {"type": "haushalttickets/request", "method": "POST", "path": path}
        )
        response = await client.receive_json()
        assert response["error"]["code"] == "invalid_format", path


async def test_subscribe(
    hass: HomeAssistant,
    loaded: MockConfigEntry,
    household: Household,
    hass_ws_client: WebSocketGenerator,
) -> None:
    """Ticket events are pushed to the card."""
    client = await _client_for(hass, hass_ws_client, household.robin)
    await client.send_json_auto_id({"type": "haushalttickets/subscribe"})
    assert (await client.receive_json())["success"]

    await hass.services.async_call(
        DOMAIN,
        "create_ticket",
        {"type": "purchase", "title": "Akkuschrauber"},
        blocking=True,
        context=Context(user_id=household.alex.id),
    )
    message = await client.receive_json()
    assert message["type"] == "event"
    assert message["event"]["type"] == "ticket_created"
    assert message["event"]["ticket"]["title"] == "Akkuschrauber"
