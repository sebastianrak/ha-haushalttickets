"""Haushalttickets: household tickets from a separate server."""

from __future__ import annotations

from pathlib import Path

from homeassistant.components.frontend import add_extra_js_url
from homeassistant.components.http import StaticPathConfig
from homeassistant.const import CONF_API_TOKEN, CONF_URL, Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.start import async_at_started
from homeassistant.helpers.typing import ConfigType
from homeassistant.loader import async_get_integration

from .api import ApiError, CannotConnect, HaushaltticketsClient, InvalidAuth
from .const import CARD_URL, DOMAIN
from .coordinator import HaushaltticketsConfigEntry, HaushaltticketsCoordinator
from .llm_api import async_register_llm_api
from .services import async_setup_services
from .views import AttachmentView, UploadView
from .websocket import async_setup_websocket

PLATFORMS = [Platform.SENSOR]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Actions, WebSocket commands, file views and the card, even while the server is down."""
    async_setup_services(hass)
    async_setup_websocket(hass)
    hass.http.register_view(AttachmentView())
    hass.http.register_view(UploadView())
    # The card ships with the integration; no dashboard resource needed.
    version = (await async_get_integration(hass, DOMAIN)).version
    await hass.http.async_register_static_paths(
        [
            StaticPathConfig(
                CARD_URL,
                str(Path(__file__).parent / "frontend" / "haushalttickets-card.js"),
                cache_headers=True,
            )
        ]
    )
    if "frontend" in hass.config.components:
        add_extra_js_url(hass, f"{CARD_URL}?v={version}")
    return True


async def async_setup_entry(
    hass: HomeAssistant, entry: HaushaltticketsConfigEntry
) -> bool:
    """Connect to the server, sync users, start polling."""
    client = HaushaltticketsClient(
        async_get_clientsession(hass), entry.data[CONF_URL], entry.data[CONF_API_TOKEN]
    )
    coordinator = HaushaltticketsCoordinator(hass, entry, client)
    try:
        coordinator.server_version = (await client.health()).get("version")
        await coordinator.async_sync_users()
    except InvalidAuth as err:
        raise ConfigEntryAuthFailed(
            translation_domain=DOMAIN, translation_key="invalid_auth"
        ) from err
    except (CannotConnect, ApiError) as err:
        raise ConfigEntryNotReady(
            translation_domain=DOMAIN,
            translation_key="cannot_connect",
            translation_placeholders={"error": str(err)},
        ) from err

    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    coordinator.async_setup_user_listeners()
    # Ticket tools for AI assistants (mcp_server serves them to claude.ai).
    entry.async_on_unload(async_register_llm_api(hass, coordinator))

    @callback
    def _sync_after_start(_hass: HomeAssistant) -> None:
        # Person entities may still have been loading during startup.
        coordinator.async_schedule_user_sync()

    if not hass.is_running:
        entry.async_on_unload(async_at_started(hass, _sync_after_start))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(
    hass: HomeAssistant, entry: HaushaltticketsConfigEntry
) -> bool:
    """Stop polling and remove the entities."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
