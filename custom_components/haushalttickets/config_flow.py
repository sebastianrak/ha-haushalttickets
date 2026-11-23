"""Config flow: server URL and API token."""

from __future__ import annotations

from collections.abc import Mapping
import logging
from typing import Any

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_API_TOKEN, CONF_URL
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)
import probatio
from yarl import URL

from .api import ApiError, CannotConnect, HaushaltticketsClient, InvalidAuth
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

_URL_SELECTOR = TextSelector(TextSelectorConfig(type=TextSelectorType.URL))
_TOKEN_SELECTOR = TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))


def _normalize_url(value: str) -> str | None:
    """``http(s)://host[:port][/prefix]`` without trailing slash, or None."""
    try:
        url = URL(value.strip())
    except ValueError:
        return None
    if url.scheme not in ("http", "https") or not url.host or url.query_string:
        return None
    return str(url.with_fragment(None)).rstrip("/")


class HaushaltticketsConfigFlow(ConfigFlow, domain=DOMAIN):
    """Set up the connection to the Haushalttickets server."""

    VERSION = 1

    async def _async_validate(self, url: str, token: str) -> dict[str, str]:
        """Errors keyed by field, empty when the server accepts the token."""
        client = HaushaltticketsClient(async_get_clientsession(self.hass), url, token)
        try:
            await client.health()
            await client.users()
        except InvalidAuth:
            return {"base": "invalid_auth"}
        except CannotConnect:
            return {"base": "cannot_connect"}
        except ApiError as err:
            if err.code == "client_not_allowed":
                return {"base": "client_not_allowed"}
            _LOGGER.warning("Server refused the connection test: %s", err)
            return {"base": "unknown"}
        except Exception:
            _LOGGER.exception("Unexpected error while testing the connection")
            return {"base": "unknown"}
        return {}

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for URL and token."""
        errors: dict[str, str] = {}
        if user_input is not None:
            url = _normalize_url(user_input[CONF_URL])
            if url is None:
                errors[CONF_URL] = "invalid_url"
            else:
                errors = await self._async_validate(url, user_input[CONF_API_TOKEN])
            if not errors:
                return self.async_create_entry(
                    title="Haushalttickets",
                    data={CONF_URL: url, CONF_API_TOKEN: user_input[CONF_API_TOKEN]},
                )
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(
                probatio.Schema(
                    {
                        probatio.Required(CONF_URL): _URL_SELECTOR,
                        probatio.Required(
                            probatio.Secret(CONF_API_TOKEN)
                        ): _TOKEN_SELECTOR,
                    }
                ),
                {CONF_URL: (user_input or {}).get(CONF_URL)},
            ),
            errors=errors,
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """The server rejected the token."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for a new token."""
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = await self._async_validate(
                entry.data[CONF_URL], user_input[CONF_API_TOKEN]
            )
            if not errors:
                return self.async_update_reload_and_abort(
                    entry, data_updates={CONF_API_TOKEN: user_input[CONF_API_TOKEN]}
                )
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=probatio.Schema(
                {probatio.Required(probatio.Secret(CONF_API_TOKEN)): _TOKEN_SELECTOR}
            ),
            description_placeholders={"url": entry.data[CONF_URL]},
            errors=errors,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Change the URL, for example after the container got a new address."""
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            url = _normalize_url(user_input[CONF_URL])
            if url is None:
                errors[CONF_URL] = "invalid_url"
            else:
                errors = await self._async_validate(url, entry.data[CONF_API_TOKEN])
            if not errors:
                return self.async_update_reload_and_abort(
                    entry, data_updates={CONF_URL: url}
                )
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=self.add_suggested_values_to_schema(
                probatio.Schema({probatio.Required(CONF_URL): _URL_SELECTOR}),
                {CONF_URL: (user_input or {}).get(CONF_URL, entry.data[CONF_URL])},
            ),
            errors=errors,
        )
