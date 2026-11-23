"""Feasibility spike for Haushalttickets.

Registers an LLM API with a few ticket tools so that Home Assistant's built-in
MCP server (``mcp_server``) can expose them at ``/api/mcp/haushalttickets_spike``.
Tickets are demo data held in memory only and are lost on restart.
"""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .llm_api import SpikeStore, async_register_llm_api

type SpikeConfigEntry = ConfigEntry[SpikeStore]


async def async_setup_entry(hass: HomeAssistant, entry: SpikeConfigEntry) -> bool:
    """Set up the spike from a config entry."""
    entry.runtime_data = SpikeStore()
    entry.async_on_unload(async_register_llm_api(hass, entry.runtime_data))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: SpikeConfigEntry) -> bool:
    """Unload a config entry."""
    return True
