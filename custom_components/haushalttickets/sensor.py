"""Ticket counters: household-wide and per person."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import AUTOMATION_USER_ID, DOMAIN, MAX_ATTRIBUTE_TICKETS
from .coordinator import HaushaltticketsConfigEntry, HaushaltticketsCoordinator

PARALLEL_UPDATES = 0

type Ticket = dict[str, Any]


@dataclass(frozen=True, kw_only=True)
class HouseholdSensorDescription(SensorEntityDescription):
    """Counts open tickets matching a filter."""

    matches: Callable[[Ticket], bool]


@dataclass(frozen=True, kw_only=True)
class PersonSensorDescription(SensorEntityDescription):
    """Counts open tickets matching a filter for one user."""

    matches: Callable[[Ticket, str], bool]


HOUSEHOLD_SENSORS: tuple[HouseholdSensorDescription, ...] = (
    HouseholdSensorDescription(
        key="open", translation_key="open", matches=lambda t: True
    ),
    HouseholdSensorDescription(
        key="burning",
        translation_key="burning",
        matches=lambda t: t["urgency"] == "burning",
    ),
    HouseholdSensorDescription(
        key="approval_pending",
        translation_key="approval_pending",
        matches=lambda t: t["type"] == "purchase" and t["approval_state"] == "pending",
    ),
    HouseholdSensorDescription(
        key="acceptance",
        translation_key="acceptance",
        matches=lambda t: t["status"] == "acceptance",
    ),
)

PERSON_SENSORS: tuple[PersonSensorDescription, ...] = (
    PersonSensorDescription(
        key="assigned",
        translation_key="assigned",
        matches=lambda t, user_id: user_id in t["assignee_ids"],
    ),
    PersonSensorDescription(
        key="to_accept",
        translation_key="to_accept",
        matches=lambda t, user_id: (
            t["status"] == "acceptance" and t["requested_by_id"] == user_id
        ),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HaushaltticketsConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Household sensors now, person sensors whenever a new user shows up."""
    coordinator = entry.runtime_data
    async_add_entities(
        HouseholdSensor(coordinator, description) for description in HOUSEHOLD_SENSORS
    )

    known: set[str] = set()

    @callback
    def _add_new_users() -> None:
        new = [
            user_id
            for user_id, user in coordinator.data.users.items()
            if user_id not in known
            and user_id != AUTOMATION_USER_ID
            and user["active"]
            # Only household members: service and tablet accounts have no person.
            and user["ha_person_entity_id"]
        ]
        known.update(new)
        async_add_entities(
            PersonSensor(coordinator, description, user_id)
            for user_id in new
            for description in PERSON_SENSORS
        )

    _add_new_users()
    entry.async_on_unload(coordinator.async_add_listener(_add_new_users))


class _TicketSensor(CoordinatorEntity[HaushaltticketsCoordinator], SensorEntity):
    _attr_has_entity_name = True
    _attr_state_class = SensorStateClass.MEASUREMENT
    _unrecorded_attributes = frozenset({"tickets"})

    def __init__(self, coordinator: HaushaltticketsCoordinator) -> None:
        super().__init__(coordinator)
        entry = coordinator.config_entry
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name="Haushalttickets",
            manufacturer="Haushalttickets",
            entry_type=DeviceEntryType.SERVICE,
            sw_version=coordinator.server_version,
        )

    def _matching(self) -> list[Ticket]:
        raise NotImplementedError

    @property
    def native_value(self) -> int:
        """Number of matching open tickets."""
        return len(self._matching())

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """The matching tickets, most urgent first (not recorded)."""
        order = {"burning": 0, "this_week": 1, "someday": 2}
        tickets = sorted(
            self._matching(), key=lambda t: (order.get(t["urgency"], 3), t["id"])
        )
        return {
            "tickets": [
                {
                    "id": t["id"],
                    "title": t["title"],
                    "type": t["type"],
                    "status": t["status"],
                    "urgency": t["urgency"],
                }
                for t in tickets[:MAX_ATTRIBUTE_TICKETS]
            ]
        }


class HouseholdSensor(_TicketSensor):
    """Open tickets of the whole household."""

    entity_description: HouseholdSensorDescription

    def __init__(
        self,
        coordinator: HaushaltticketsCoordinator,
        description: HouseholdSensorDescription,
    ) -> None:
        """Bind the description."""
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{coordinator.config_entry.entry_id}_{description.key}"

    def _matching(self) -> list[Ticket]:
        return [
            t
            for t in self.coordinator.data.tickets
            if self.entity_description.matches(t)
        ]


class PersonSensor(_TicketSensor):
    """Open tickets of one household member."""

    entity_description: PersonSensorDescription

    def __init__(
        self,
        coordinator: HaushaltticketsCoordinator,
        description: PersonSensorDescription,
        user_id: str,
    ) -> None:
        """Bind description and server user."""
        super().__init__(coordinator)
        self.entity_description = description
        self._user_id = user_id
        self._attr_unique_id = (
            f"{coordinator.config_entry.entry_id}_{user_id}_{description.key}"
        )
        self._attr_translation_placeholders = {
            "person": coordinator.data.users[user_id]["display_name"]
        }

    @property
    def available(self) -> bool:
        """Unavailable once the user is removed from Home Assistant."""
        user = self.coordinator.data.users.get(self._user_id)
        return super().available and user is not None and user["active"]

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Add the user and the linked person entity."""
        return {
            **super().extra_state_attributes,
            "user_id": self._user_id,
            "person": self.coordinator.data.person_of(self._user_id),
        }

    def _matching(self) -> list[Ticket]:
        return [
            t
            for t in self.coordinator.data.tickets
            if self.entity_description.matches(t, self._user_id)
        ]
