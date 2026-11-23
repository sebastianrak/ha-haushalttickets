"""WebSocket commands for the ticket card.

``haushalttickets/request`` forwards a call to the server on behalf of the logged-in
Home Assistant user. The server decides what that user may do, so this module does
not repeat any permission rules; it only limits which paths can be reached.
"""

from __future__ import annotations

import re
from typing import Any

from homeassistant.components import websocket_api
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
import probatio

from .api import Acting, ApiError, CannotConnect, InvalidAuth
from .const import EVENT_TICKET
from .services import get_coordinator

# Only the ticket API, no query strings or dot segments in the path itself.
_PATH = re.compile(r"^/api/v1/[A-Za-z0-9_\-/]+$")
# Reserved for the integration itself.
_BLOCKED = frozenset({"/api/v1/users/sync"})


def _valid_path(value: Any) -> str:
    path = cv.string(value)
    if not _PATH.match(path) or "//" in path or path.rstrip("/") in _BLOCKED:
        raise probatio.Invalid("path not allowed")
    return path


@callback
def async_setup_websocket(hass: HomeAssistant) -> None:
    """Register the commands."""
    websocket_api.async_register_command(hass, ws_request)
    websocket_api.async_register_command(hass, ws_subscribe)


@websocket_api.websocket_command(
    {
        probatio.Required("type"): "haushalttickets/request",
        probatio.Required("method"): probatio.In(
            ("GET", "POST", "PATCH", "PUT", "DELETE")
        ),
        probatio.Required("path"): _valid_path,
        probatio.Optional("params"): dict,
        probatio.Optional("body"): object,
    }
)
@websocket_api.async_response
async def ws_request(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Call the server as the logged-in user."""
    user = connection.user
    if user.system_generated:
        connection.send_error(msg["id"], "unauthorized", "System users cannot act.")
        return
    try:
        coordinator = get_coordinator(hass)
    except ServiceValidationError:
        connection.send_error(msg["id"], "not_loaded", "Haushalttickets is not loaded.")
        return

    try:
        result = await coordinator.client.request(
            msg["method"],
            msg["path"],
            acting=Acting(user.id, "ui"),
            params=msg.get("params"),
            json=msg.get("body"),
        )
    except InvalidAuth:
        coordinator.config_entry.async_start_reauth(hass)
        connection.send_error(msg["id"], "invalid_auth", "Server token rejected.")
        return
    except CannotConnect as err:
        connection.send_error(msg["id"], "cannot_connect", str(err))
        return
    except ApiError as err:
        connection.send_error(msg["id"], err.code, err.detail)
        return

    connection.send_result(msg["id"], result)
    if msg["method"] == "GET":
        return
    if msg["path"].startswith("/api/v1/users"):
        await coordinator.async_refresh_users()
    else:
        await coordinator.async_request_refresh()


@websocket_api.websocket_command(
    {probatio.Required("type"): "haushalttickets/subscribe"}
)
@callback
def ws_subscribe(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Push every ticket event to the card."""

    @callback
    def _forward(event: Event) -> None:
        connection.send_message(websocket_api.event_message(msg["id"], event.data))

    connection.subscriptions[msg["id"]] = hass.bus.async_listen(EVENT_TICKET, _forward)
    connection.send_result(msg["id"])
