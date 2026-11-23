"""Diagnostics without names, titles, URL or token."""

from __future__ import annotations

from collections import Counter
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_API_TOKEN, CONF_URL
from homeassistant.core import HomeAssistant

from .coordinator import HaushaltticketsConfigEntry


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: HaushaltticketsConfigEntry
) -> dict[str, Any]:
    """Counts only; ticket contents and household names stay private."""
    coordinator = entry.runtime_data
    data = coordinator.data
    return {
        "entry": async_redact_data(dict(entry.data), {CONF_API_TOKEN, CONF_URL}),
        "server_version": coordinator.server_version,
        "last_update_success": coordinator.last_update_success,
        "last_seq": data.last_seq,
        "open_tickets": len(data.tickets),
        "by_status": dict(Counter(t["status"] for t in data.tickets)),
        "by_type": dict(Counter(t["type"] for t in data.tickets)),
        "users": [
            {
                "role": user["role"],
                "active": user["active"],
                "ha_is_admin": user["ha_is_admin"],
                "has_person": user["ha_person_entity_id"] is not None,
            }
            for user in data.users.values()
        ],
    }
