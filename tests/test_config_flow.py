"""Config flow against the real server."""

from __future__ import annotations

from homeassistant.config_entries import SOURCE_USER
from homeassistant.const import CONF_API_TOKEN, CONF_URL
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.haushalttickets.const import DOMAIN

from .conftest import Server


async def _start(hass: HomeAssistant) -> str:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    return result["flow_id"]


async def test_user_flow(hass: HomeAssistant, clean_server: Server) -> None:
    """URL is normalized, the entry stores URL and token."""
    flow_id = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        flow_id,
        {CONF_URL: f" {clean_server.url}/ ", CONF_API_TOKEN: clean_server.token},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Haushalttickets"
    assert result["data"] == {
        CONF_URL: clean_server.url,
        CONF_API_TOKEN: clean_server.token,
    }


async def test_user_flow_errors(hass: HomeAssistant, clean_server: Server) -> None:
    """Bad URL, wrong token and no server are reported; the flow recovers."""
    flow_id = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        flow_id, {CONF_URL: "ftp://example", CONF_API_TOKEN: "x"}
    )
    assert result["errors"] == {CONF_URL: "invalid_url"}
    result = await hass.config_entries.flow.async_configure(
        flow_id, {CONF_URL: clean_server.url, CONF_API_TOKEN: "wrong"}
    )
    assert result["errors"] == {"base": "invalid_auth"}
    result = await hass.config_entries.flow.async_configure(
        flow_id, {CONF_URL: "http://127.0.0.1:9", CONF_API_TOKEN: "x"}
    )
    assert result["errors"] == {"base": "cannot_connect"}
    result = await hass.config_entries.flow.async_configure(
        flow_id, {CONF_URL: clean_server.url, CONF_API_TOKEN: clean_server.token}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_client_not_allowed(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """The allowlist of the server gets its own message."""
    url = "http://10.0.0.5:8080"
    aioclient_mock.get(f"{url}/api/health", json={"status": "ok", "version": "0"})
    aioclient_mock.get(
        f"{url}/api/v1/users",
        status=403,
        json={"detail": {"code": "client_not_allowed", "detail": "Not allowed."}},
    )
    flow_id = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        flow_id, {CONF_URL: url, CONF_API_TOKEN: "x"}
    )
    assert result["errors"] == {"base": "client_not_allowed"}


async def test_single_instance(hass: HomeAssistant) -> None:
    """Only one server per Home Assistant."""
    MockConfigEntry(domain=DOMAIN, data={}).add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "single_instance_allowed"


async def test_reauth(hass: HomeAssistant, clean_server: Server) -> None:
    """A new token replaces the old one."""
    entry = MockConfigEntry(
        domain=DOMAIN, data={CONF_URL: clean_server.url, CONF_API_TOKEN: "old"}
    )
    entry.add_to_hass(hass)
    result = await entry.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_confirm"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_API_TOKEN: "still-wrong"}
    )
    assert result["errors"] == {"base": "invalid_auth"}
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_API_TOKEN: clean_server.token}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data[CONF_API_TOKEN] == clean_server.token


async def test_reconfigure(hass: HomeAssistant, clean_server: Server) -> None:
    """The URL can change, the token stays."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_URL: "http://127.0.0.1:9", CONF_API_TOKEN: clean_server.token},
    )
    entry.add_to_hass(hass)
    result = await entry.start_reconfigure_flow(hass)
    assert result["step_id"] == "reconfigure"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_URL: clean_server.url}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert entry.data == {
        CONF_URL: clean_server.url,
        CONF_API_TOKEN: clean_server.token,
    }
