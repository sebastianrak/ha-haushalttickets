"""LLM API of the Haushalttickets feasibility spike.

The tools only touch an in-memory demo store. Their purpose is to prove that
tools registered by a custom integration reach MCP clients (claude.ai via
Nabu Casa) through Home Assistant's ``mcp_server`` and that the calling
Home Assistant user is known inside the tool.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from homeassistant.const import __version__ as HA_VERSION
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers import llm
from homeassistant.util import dt as dt_util
import probatio

from .const import (
    DOMAIN,
    EVENT_TICKET_CREATED,
    LLM_API_ID,
    LLM_API_NAME,
    TICKET_STATUSES,
    TICKET_TYPES,
)

API_PROMPT = (
    "Test tools of the Haushalttickets feasibility spike. Tickets are demo data "
    "held in memory only and are lost when Home Assistant restarts."
)

_READ_ONLY = llm.ToolAnnotations(
    read_only=True, destructive=False, idempotent=True, open_world=False
)
_CREATE = llm.ToolAnnotations(
    read_only=False, destructive=False, idempotent=False, open_world=False
)


@dataclass
class SpikeStore:
    """In-memory demo ticket store."""

    tickets: list[dict[str, Any]] = field(default_factory=list)
    next_id: int = 1


async def _caller(hass: HomeAssistant, llm_context: llm.LLMContext) -> dict[str, Any]:
    """Describe the Home Assistant user behind the tool call."""
    context = llm_context.context
    user = None
    if context is not None and context.user_id:
        user = await hass.auth.async_get_user(context.user_id)
    return {
        "user_id": context.user_id if context else None,
        "user_name": user.name if user else None,
        "is_admin": user.is_admin if user else None,
    }


class _SpikeTool(llm.Tool):
    """Base class tagging the tools with this integration."""

    integration = DOMAIN

    def __init__(self, store: SpikeStore) -> None:
        """Initialize the tool."""
        self._store = store


class WhoAmITool(_SpikeTool):
    """Report who and what is calling."""

    name = "haushalttickets_spike_whoami"
    title = "Who am I"
    description = (
        "Return the Home Assistant user, platform and assistant this tool call "
        "arrives with. Use it to verify the connection."
    )
    annotations = _READ_ONLY

    async def async_call(
        self,
        hass: HomeAssistant,
        tool_input: llm.ToolInput,
        llm_context: llm.LLMContext,
    ) -> llm.ToolResult:
        """Return caller information."""
        return llm.ToolResult(
            data={
                **await _caller(hass, llm_context),
                "platform": llm_context.platform,
                "assistant": llm_context.assistant,
                "language": llm_context.language,
                "ha_version": HA_VERSION,
                "time": dt_util.now().isoformat(),
            }
        )


class ListTicketsTool(_SpikeTool):
    """List demo tickets."""

    name = "haushalttickets_spike_list_tickets"
    title = "List demo tickets"
    description = "List the demo tickets of the spike, optionally filtered by status."
    parameters = probatio.Schema(
        {
            probatio.Optional(
                "status", description="Only tickets with this status."
            ): probatio.In(TICKET_STATUSES),
        }
    )
    annotations = _READ_ONLY

    async def async_call(
        self,
        hass: HomeAssistant,
        tool_input: llm.ToolInput,
        llm_context: llm.LLMContext,
    ) -> llm.ToolResult:
        """Return the matching tickets."""
        args = self.parameters(tool_input.tool_args)
        tickets = [
            ticket
            for ticket in self._store.tickets
            if "status" not in args or ticket["status"] == args["status"]
        ]
        return llm.ToolResult(data={"count": len(tickets), "tickets": tickets})


class CreateTicketTool(_SpikeTool):
    """Create a demo ticket."""

    name = "haushalttickets_spike_create_ticket"
    title = "Create demo ticket"
    description = (
        "Create a demo ticket. The ticket starts in status 'new' and records the "
        "calling Home Assistant user as requester."
    )
    parameters = probatio.Schema(
        {
            probatio.Required("title", description="Short title of the ticket."): (
                probatio.All(str, probatio.Length(min=1, max=200))
            ),
            probatio.Optional(
                "type", description="Kind of ticket.", default="repair"
            ): probatio.In(TICKET_TYPES),
            probatio.Optional(
                "description", description="Optional details.", default=""
            ): probatio.All(str, probatio.Length(max=2000)),
        }
    )
    annotations = _CREATE

    async def async_call(
        self,
        hass: HomeAssistant,
        tool_input: llm.ToolInput,
        llm_context: llm.LLMContext,
    ) -> llm.ToolResult:
        """Create the ticket and fire an event."""
        args = self.parameters(tool_input.tool_args)
        caller = await _caller(hass, llm_context)
        ticket = {
            "id": self._store.next_id,
            "title": args["title"],
            "type": args["type"],
            "description": args["description"],
            "status": "new",
            "requested_by": caller["user_name"],
            "via": f"{llm_context.platform}/{llm_context.assistant}",
            "created_at": dt_util.now().isoformat(),
        }
        self._store.next_id += 1
        self._store.tickets.append(ticket)
        hass.bus.async_fire(
            EVENT_TICKET_CREATED,
            {"ticket_id": ticket["id"], "title": ticket["title"]},
            context=llm_context.context,
        )
        return llm.ToolResult(data={"created": ticket})


@dataclass(kw_only=True)
class SpikeLLMAPI(llm.API):
    """LLM API exposing the spike tools."""

    store: SpikeStore
    _tools: list[llm.Tool] = field(init=False)

    def __post_init__(self) -> None:
        """Build the tools once."""
        self._tools = [
            WhoAmITool(self.store),
            ListTicketsTool(self.store),
            CreateTicketTool(self.store),
        ]

    async def async_get_api_instance(
        self, llm_context: llm.LLMContext
    ) -> llm.APIInstance:
        """Return the instance of the API."""
        return llm.APIInstance(
            api=self,
            api_prompt=API_PROMPT,
            llm_context=llm_context,
            tools=self._tools,
        )


@callback
def async_register_llm_api(hass: HomeAssistant, store: SpikeStore) -> CALLBACK_TYPE:
    """Register the spike LLM API and return a callback to unregister it."""
    return llm.async_register_api(
        hass,
        SpikeLLMAPI(hass=hass, id=LLM_API_ID, name=LLM_API_NAME, store=store),
    )
