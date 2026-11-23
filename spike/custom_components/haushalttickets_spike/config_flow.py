"""Config flow for the Haushalttickets feasibility spike."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult

from .const import DOMAIN


class SpikeConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for the spike."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm the setup."""
        if user_input is not None:
            return self.async_create_entry(
                title="Haushalttickets (feasibility spike)", data={}
            )
        return self.async_show_form(step_id="user")
