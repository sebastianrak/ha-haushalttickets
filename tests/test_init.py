"""Setup, user sync, sensors, actions and bus events against the real server."""

from __future__ import annotations

from datetime import timedelta
import logging

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import Context, HomeAssistant
from homeassistant.exceptions import (
    HomeAssistantError,
    ServiceValidationError,
    Unauthorized,
)
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_capture_events,
    async_fire_time_changed,
)

from custom_components.haushalttickets.const import DOMAIN, EVENT_TICKET
from custom_components.haushalttickets.coordinator import HaushaltticketsCoordinator
from custom_components.haushalttickets.diagnostics import (
    async_get_config_entry_diagnostics,
)

from .conftest import Household, Server


def _coordinator(entry: MockConfigEntry) -> HaushaltticketsCoordinator:
    return entry.runtime_data


def _sensor(hass: HomeAssistant, entry: MockConfigEntry, unique_suffix: str) -> str:
    entity_id = er.async_get(hass).async_get_entity_id(
        "sensor", DOMAIN, f"{entry.entry_id}_{unique_suffix}"
    )
    assert entity_id, unique_suffix
    return entity_id


async def _create(hass: HomeAssistant, user_id: str | None = None, **data) -> dict:
    data.setdefault("type", "repair")
    data.setdefault("title", "Wasserhahn Küche tropft")
    return await hass.services.async_call(
        DOMAIN,
        "create_ticket",
        data,
        blocking=True,
        return_response=True,
        context=Context(user_id=user_id),
    )


async def test_setup_syncs_users(
    hass: HomeAssistant, loaded: MockConfigEntry, household: Household
) -> None:
    """HA users reach the server; HA admins start as admin, others as child."""
    assert loaded.state is ConfigEntryState.LOADED
    users = _coordinator(loaded).data.users
    assert users[household.alex.id]["role"] == "admin"
    assert users[household.robin.id]["role"] == "child"
    assert users[household.robin.id]["ha_person_entity_id"] == "person.robin"
    assert users["automation"]["role"] == "adult"
    # System users (such as the Supervisor) are never synced.
    assert all(not user.system_generated or user.id not in users
               for user in await hass.auth.async_get_users())  # fmt: skip


async def test_sensors_and_events(
    hass: HomeAssistant, loaded: MockConfigEntry, household: Household
) -> None:
    """A ticket assigned to Robin shows up in the counters and on the event bus."""
    events = async_capture_events(hass, EVENT_TICKET)
    result = await _create(
        hass,
        household.alex.id,
        title="Spülmaschine pumpt nicht ab",
        urgency="burning",
        assignees=["person.robin"],
    )
    assert result["title"] == "Spülmaschine pumpt nicht ab"
    assert result["requested_by_id"] == household.alex.id
    await hass.async_block_till_done()

    assert hass.states.get(_sensor(hass, loaded, "open")).state == "1"
    assert hass.states.get(_sensor(hass, loaded, "burning")).state == "1"
    assigned = hass.states.get(_sensor(hass, loaded, f"{household.robin.id}_assigned"))
    assert assigned.state == "1"
    assert assigned.attributes["person"] == "person.robin"
    assert assigned.attributes["tickets"][0]["title"] == "Spülmaschine pumpt nicht ab"
    mine = _sensor(hass, loaded, f"{household.alex.id}_assigned")
    assert hass.states.get(mine).state == "0"
    # No sensors for the user Automation.
    assert not er.async_get(hass).async_get_entity_id(
        "sensor", DOMAIN, f"{loaded.entry_id}_automation_assigned"
    )

    created = [e for e in events if e.data["type"] == "ticket_created"]
    assert len(created) == 1
    data = created[0].data
    assert data["actor_id"] == household.alex.id
    assert data["actor_person"] == "person.alex"
    assert data["via"] == "ui"
    assert data["ticket"]["assignee_persons"] == ["person.robin"]


async def test_automation_acts_as_automation(
    hass: HomeAssistant, loaded: MockConfigEntry
) -> None:
    """Without a user in the context the ticket is filed by the user Automation."""
    events = async_capture_events(hass, EVENT_TICKET)
    result = await _create(hass, None, title="Filter Lüftung wechseln")
    assert result["requested_by_id"] == "automation"
    await hass.services.async_call(
        DOMAIN,
        "add_comment",
        {"ticket_id": result["id"], "comment": "Laufzeit 180 Tage"},
        blocking=True,
    )
    done = await hass.services.async_call(
        DOMAIN,
        "set_status",
        {"ticket_id": result["id"], "status": "done"},
        blocking=True,
        return_response=True,
    )
    assert done["status"] == "done"
    await hass.async_block_till_done()
    assert {e.data["via"] for e in events} == {"automation"}


async def test_purchase_extra_fields_and_approval(
    hass: HomeAssistant, loaded: MockConfigEntry, household: Household
) -> None:
    """Type-specific fields go through extra_fields; big purchases need approval."""
    result = await _create(
        hass,
        household.alex.id,
        type="purchase",
        title="Waschmaschine",
        extra_fields={"price_estimated": "899.00", "vendor": "Fachhandel"},
    )
    assert result["approval_state"] == "pending"
    await hass.async_block_till_done()
    assert hass.states.get(_sensor(hass, loaded, "approval_pending")).state == "1"

    with pytest.raises(ServiceValidationError) as err:
        await _create(hass, household.alex.id, extra_fields={"title": "x"})
    assert err.value.translation_key == "reserved_fields"
    with pytest.raises(ServiceValidationError) as err:
        # A vendor makes no sense on a repair.
        await _create(hass, household.alex.id, extra_fields={"vendor": "Baumarkt"})
    assert err.value.translation_key == "rejected"


async def test_server_permissions_apply(
    hass: HomeAssistant, loaded: MockConfigEntry, household: Household
) -> None:
    """The child may not close Alex's ticket; the server says no."""
    ticket = await _create(hass, household.alex.id)
    with pytest.raises(HomeAssistantError) as err:
        await hass.services.async_call(
            DOMAIN,
            "set_status",
            {"ticket_id": ticket["id"], "status": "done"},
            blocking=True,
            context=Context(user_id=household.kind.id),
        )
    assert err.value.translation_key == "forbidden"
    assert err.value.translation_placeholders["code"] == "child_status_forbidden"

    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(
            DOMAIN,
            "set_status",
            {"ticket_id": 999, "status": "done"},
            blocking=True,
            context=Context(user_id=household.alex.id),
        )
    assert err.value.translation_key == "not_found"


async def test_assign_needs_linked_persons(
    hass: HomeAssistant, loaded: MockConfigEntry, household: Household
) -> None:
    """Persons without a user cannot be assigned."""
    hass.states.async_set("person.gast", "home", {})
    ticket = await _create(hass, household.alex.id)
    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(
            DOMAIN,
            "assign",
            {"ticket_id": ticket["id"], "assignees": ["person.gast"]},
            blocking=True,
            context=Context(user_id=household.alex.id),
        )
    assert err.value.translation_key == "person_without_user"
    result = await hass.services.async_call(
        DOMAIN,
        "assign",
        {"ticket_id": ticket["id"], "assignees": ["person.robin", "person.kind"]},
        blocking=True,
        return_response=True,
        context=Context(user_id=household.alex.id),
    )
    assert sorted(result["assignee_persons"]) == ["person.kind", "person.robin"]


async def test_removed_user_becomes_inactive(
    hass: HomeAssistant, loaded: MockConfigEntry, household: Household
) -> None:
    """Deleting an HA user deactivates it on the server; its sensors go unavailable."""
    sensor = _sensor(hass, loaded, f"{household.kind.id}_assigned")
    await hass.auth.async_remove_user(household.kind)
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=3))
    await hass.async_block_till_done()
    assert _coordinator(loaded).data.users[household.kind.id]["active"] is False
    assert hass.states.get(sensor).state == "unavailable"


async def test_new_user_gets_sensors(
    hass: HomeAssistant, loaded: MockConfigEntry
) -> None:
    """A user added later (the daughter, say) gets person sensors without a reload."""
    user = await hass.auth.async_create_user("Tochter", group_ids=["system-users"])
    hass.states.async_set("person.tochter", "home", {"user_id": user.id})
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=3))
    await hass.async_block_till_done()
    state = hass.states.get(_sensor(hass, loaded, f"{user.id}_assigned"))
    assert state.state == "0"
    assert state.attributes["person"] == "person.tochter"


async def test_feed_reset_reloads_without_replay(
    hass: HomeAssistant,
    loaded: MockConfigEntry,
    household: Household,
    server: Server,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A restored server database (feed goes back) is detected and not replayed."""
    await _create(hass, household.alex.id)
    await _create(hass, household.alex.id)
    coordinator = _coordinator(loaded)
    await coordinator.async_refresh()  # the second refresh request was debounced
    assert coordinator.data.last_seq >= 2

    server.reset()  # sequence numbers start again at 1
    events = async_capture_events(hass, EVENT_TICKET)
    with caplog.at_level(logging.WARNING):
        await coordinator.async_refresh()
    assert "went back" in caplog.text
    assert coordinator.data.last_seq == 0
    assert coordinator.data.tickets == []
    assert events == []


async def test_server_down_marks_unavailable(
    hass: HomeAssistant, loaded: MockConfigEntry
) -> None:
    """Connection errors make the entities unavailable instead of raising."""
    coordinator = _coordinator(loaded)
    coordinator.client._base = "http://127.0.0.1:9"  # nothing listens there
    await coordinator.async_refresh()
    assert not coordinator.last_update_success
    assert hass.states.get(_sensor(hass, loaded, "open")).state == "unavailable"


async def test_setup_retries_when_server_down(
    hass: HomeAssistant, household: Household
) -> None:
    """Unreachable server at startup: retry later."""
    entry = MockConfigEntry(
        domain=DOMAIN, data={"url": "http://127.0.0.1:9", "api_token": "x"}
    )
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    assert entry.state is ConfigEntryState.SETUP_RETRY


async def test_setup_wrong_token_starts_reauth(
    hass: HomeAssistant, server: Server, household: Household
) -> None:
    """A rejected token starts the reauth flow."""
    entry = MockConfigEntry(
        domain=DOMAIN, data={"url": server.url, "api_token": "wrong"}
    )
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    assert entry.state is ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert [flow["context"]["source"] for flow in flows] == ["reauth"]


async def test_diagnostics_hide_private_data(
    hass: HomeAssistant, loaded: MockConfigEntry, household: Household
) -> None:
    """No token, URL, names or titles in diagnostics."""
    await _create(hass, household.alex.id, title="Geheimer Titel")
    diagnostics = await async_get_config_entry_diagnostics(hass, loaded)
    text = str(diagnostics)
    assert loaded.data["api_token"] not in text
    assert loaded.data["url"] not in text
    assert "Geheimer Titel" not in text
    assert "Alex" not in text
    assert diagnostics["open_tickets"] == 1


async def test_set_role(
    hass: HomeAssistant, loaded: MockConfigEntry, household: Household
) -> None:
    """HA admins set roles; others and automations cannot."""
    await hass.services.async_call(
        DOMAIN,
        "set_role",
        {"person": "person.robin", "role": "adult"},
        blocking=True,
        context=Context(user_id=household.alex.id),
    )
    await hass.async_block_till_done()
    assert _coordinator(loaded).data.users[household.robin.id]["role"] == "adult"

    with pytest.raises(Unauthorized):
        await hass.services.async_call(
            DOMAIN,
            "set_role",
            {"person": "person.kind", "role": "admin"},
            blocking=True,
            context=Context(user_id=household.robin.id),
        )
    with pytest.raises(HomeAssistantError) as err:
        await hass.services.async_call(
            DOMAIN,
            "set_role",
            {"person": "person.kind", "role": "admin"},
            blocking=True,
        )
    assert err.value.translation_placeholders["code"] == "admin_required"
