"""Polls the event feed of the server and keeps open tickets and users."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_STATE_CHANGED
from homeassistant.core import Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.debounce import Debouncer
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import ApiError, CannotConnect, HaushaltticketsClient, InvalidAuth
from .const import AUTOMATION_USER_ID, DOMAIN, EVENT_TICKET, SCAN_INTERVAL
from .notifications import fire, notifications_for

_LOGGER = logging.getLogger(__name__)

# Home Assistant auth events (homeassistant.auth).
EVENT_USER_ADDED = "user_added"
EVENT_USER_UPDATED = "user_updated"
EVENT_USER_REMOVED = "user_removed"

type HaushaltticketsConfigEntry = ConfigEntry[HaushaltticketsCoordinator]


@dataclass
class TicketData:
    """Open tickets and users of the server."""

    tickets: list[dict[str, Any]] = field(default_factory=list)
    users: dict[str, dict[str, Any]] = field(default_factory=dict)
    last_seq: int = 0

    def person_of(self, user_id: str | None) -> str | None:
        """Person entity linked to a server user."""
        user = self.users.get(user_id) if user_id else None
        return user.get("ha_person_entity_id") if user else None


class HaushaltticketsCoordinator(DataUpdateCoordinator[TicketData]):
    """Polls ``/api/v1/events`` and reloads tickets only when something changed."""

    config_entry: HaushaltticketsConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: HaushaltticketsConfigEntry,
        client: HaushaltticketsClient,
    ) -> None:
        """Set up polling."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=SCAN_INTERVAL,
            always_update=False,
        )
        self.client = client
        self.server_version: str | None = None
        # User changes produce no events; reload users on the next poll.
        self._users_stale = False
        self._users_debouncer = Debouncer(
            hass,
            _LOGGER,
            cooldown=2,
            immediate=False,
            function=self._async_sync_users_safe,
        )

    async def _async_update_data(self) -> TicketData:
        try:
            return await self._async_poll()
        except InvalidAuth as err:
            raise ConfigEntryAuthFailed(
                translation_domain=DOMAIN, translation_key="invalid_auth"
            ) from err
        except (CannotConnect, ApiError) as err:
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="update_failed",
                translation_placeholders={"error": str(err)},
            ) from err

    async def _async_poll(self) -> TicketData:
        if self.data is None:
            # Start at the current end of the feed; older events are not replayed.
            feed = await self.client.events(after=0, limit=1)
            return await self._async_load(feed["last_seq"])

        last_seq = self.data.last_seq
        feed = await self.client.events(after=last_seq)
        if feed["last_seq"] < last_seq:
            _LOGGER.warning(
                "Event feed went back from %s to %s (server database restored?); "
                "reloading without replaying events",
                last_seq,
                feed["last_seq"],
            )
            return await self._async_load(feed["last_seq"])
        if not feed["events"]:
            if self._users_stale:
                self._users_stale = False
                users = {user["ha_user_id"]: user for user in await self.client.users()}
                return replace(self.data, users=users)
            return self.data

        events = list(feed["events"])
        while events[-1]["seq"] < feed["last_seq"]:
            feed = await self.client.events(after=events[-1]["seq"])
            if not feed["events"]:
                break
            events.extend(feed["events"])

        data = await self._async_load(events[-1]["seq"])
        await self._async_fire(events, data)
        return data

    async def _async_load(self, last_seq: int) -> TicketData:
        self._users_stale = False
        tickets = await self.client.open_tickets()
        users = {user["ha_user_id"]: user for user in await self.client.users()}
        return TicketData(tickets=tickets, users=users, last_seq=last_seq)

    async def _async_fire(self, events: list[dict[str, Any]], data: TicketData) -> None:
        """Fire one bus event per server event, with the current ticket attached.

        Each server event also yields the notifications for the people concerned.
        """
        known = {ticket["id"]: ticket for ticket in data.tickets}
        for event in events:
            ticket_id = event.get("ticket_id")
            ticket = known.get(ticket_id) if ticket_id is not None else None
            if ticket is None and ticket_id is not None:
                try:
                    ticket = await self.client.ticket(ticket_id)
                except ApiError:
                    ticket = None  # deleted in the meantime
                known[ticket_id] = ticket
            bus_event = {
                "seq": event["seq"],
                "type": event["type"],
                "ticket_id": ticket_id,
                "actor_id": event.get("actor_id"),
                "actor_person": data.person_of(event.get("actor_id")),
                "via": event["via"],
                "payload": event.get("payload") or {},
                "created_at": event.get("created_at"),
                "ticket": ticket_summary(ticket, data) if ticket else None,
            }
            self.hass.bus.async_fire(EVENT_TICKET, bus_event)
            comment = (
                await self._async_comment(event)
                if event["type"] == "comment_added"
                else None
            )
            fire(
                self.hass,
                notifications_for(bus_event, data, self.hass.config.language, comment),
            )

    async def _async_comment(self, event: dict[str, Any]) -> str | None:
        """Text of the comment an event refers to; None if it is gone."""
        comment_id = (event.get("payload") or {}).get("comment_id")
        try:
            comments = await self.client.comments(event["ticket_id"])
        except (ApiError, CannotConnect):
            return None
        return next((c["body"] for c in comments if c["id"] == comment_id), None)

    # Users

    async def async_sync_users(self) -> None:
        """Send all Home Assistant users to the server; removed ones become inactive.

        Raises the client errors.
        """
        persons = {
            state.attributes["user_id"]: state.entity_id
            for state in self.hass.states.async_all("person")
            if state.attributes.get("user_id")
        }
        items = [
            {
                "ha_user_id": user.id,
                "display_name": user.name or user.id,
                "is_admin": user.is_admin,
                "is_active": user.is_active,
                "person_entity_id": persons.get(user.id),
            }
            for user in await self.hass.auth.async_get_users()
            if not user.system_generated
        ]
        present = {item["ha_user_id"] for item in items}
        items.extend(
            {
                "ha_user_id": user["ha_user_id"],
                "display_name": user["display_name"],
                "is_admin": False,
                "is_active": False,
                "person_entity_id": None,
            }
            for user in await self.client.users()
            if user["ha_user_id"] not in present
            and user["ha_user_id"] != AUTOMATION_USER_ID
            and user["active"]
        )
        await self.client.sync_users(items)

    async def _async_sync_users_safe(self) -> None:
        try:
            await self.async_sync_users()
        except (CannotConnect, ApiError, InvalidAuth) as err:
            _LOGGER.warning("Could not sync users to the server: %s", err)
            return
        await self.async_refresh_users()

    async def async_refresh_users(self) -> None:
        """Reload the users soon, for example after a role change."""
        self._users_stale = True
        await self.async_request_refresh()

    @callback
    def async_schedule_user_sync(self, _event: Event | None = None) -> None:
        """Sync users soon (debounced)."""
        self._users_debouncer.async_schedule_call()

    @callback
    def async_setup_user_listeners(self) -> None:
        """Re-sync on changed HA users and on changed person links."""
        entry = self.config_entry
        for event_type in (EVENT_USER_ADDED, EVENT_USER_UPDATED, EVENT_USER_REMOVED):
            entry.async_on_unload(
                self.hass.bus.async_listen(event_type, self.async_schedule_user_sync)
            )
        entry.async_on_unload(
            self.hass.bus.async_listen(
                EVENT_STATE_CHANGED,
                self.async_schedule_user_sync,
                event_filter=_person_link_changed,
            )
        )
        entry.async_on_unload(self._users_debouncer.async_shutdown)


@callback
def _person_link_changed(event_data: EventStateChangedData) -> bool:
    """A person entity was added, removed or linked to another user."""
    if not event_data["entity_id"].startswith("person."):
        return False
    old, new = event_data["old_state"], event_data["new_state"]
    if old is None or new is None:
        return True
    return old.attributes.get("user_id") != new.attributes.get("user_id")


def ticket_summary(ticket: dict[str, Any], data: TicketData) -> dict[str, Any]:
    """The parts of a ticket that automations typically need."""
    return {
        "id": ticket["id"],
        "type": ticket["type"],
        "title": ticket["title"],
        "status": ticket["status"],
        "urgency": ticket["urgency"],
        "due_date": ticket.get("due_date"),
        "approval_state": ticket.get("approval_state"),
        "series_id": ticket.get("series_id"),
        "requested_by_id": ticket["requested_by_id"],
        "requested_by_person": data.person_of(ticket["requested_by_id"]),
        "assignee_ids": ticket["assignee_ids"],
        "assignee_persons": [
            person
            for user_id in ticket["assignee_ids"]
            if (person := data.person_of(user_id))
        ],
    }
