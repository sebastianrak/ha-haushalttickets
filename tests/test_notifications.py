"""Who hears about what, buttons in app notifications and due reminders."""

from __future__ import annotations

from datetime import date

from homeassistant.core import Context, HomeAssistant
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_capture_events,
)

from custom_components.haushalttickets.const import (
    DOMAIN,
    EVENT_NOTIFICATION,
    EVENT_NOTIFICATION_ACTION,
)
from custom_components.haushalttickets.coordinator import TicketData
from custom_components.haushalttickets.notifications import (
    due_notifications,
    notifications_for,
)

from .conftest import Household

DASH = "\N{EN DASH}"


def _user(user_id: str, role: str, person: str | None, **extra) -> dict:
    return {
        "ha_user_id": user_id,
        "display_name": user_id.capitalize(),
        "role": role,
        "ha_person_entity_id": person,
        "active": True,
        **extra,
    }


DATA = TicketData(
    users={
        "alex": _user("alex", "admin", "person.alex"),
        "robin": _user("robin", "adult", "person.robin"),
        "kind": _user("kind", "child", "person.kind"),
        "gast": _user("gast", "adult", "person.gast", active=False),
        "ki": _user("ki", "ai_agent", "person.ki"),
        "tablet": _user("tablet", "adult", None),
        "automation": _user("automation", "adult", None),
    }
)


def _event(kind: str, actor: str, payload: dict | None = None, **ticket) -> dict:
    ticket = {
        "id": 7,
        "title": "Spülmaschine",
        "urgency": "someday",
        "requested_by_id": "alex",
        "assignee_ids": [],
        **ticket,
    }
    return {
        "type": kind,
        "ticket_id": 7,
        "actor_id": actor,
        "via": "ui",
        "payload": payload or {},
        "ticket": ticket,
    }


def _by_person(notifications) -> dict:
    return {n.person: n for n in notifications}


def test_burning_ticket_reaches_all_adults_but_the_actor() -> None:
    """Burning: every active adult with a person, urgent; assignment is mentioned."""
    event = _event("ticket_created", "alex", urgency="burning", assignee_ids=["robin"])
    sent = _by_person(notifications_for(event, DATA, "de"))
    assert set(sent) == {"person.robin"}  # not Alex (actor), Kind, Gast, KI, Tablet
    robin = sent["person.robin"]
    assert robin.kind == "burning"
    assert robin.title == "🔥 Brennt"
    assert robin.urgent
    assert robin.message == f"#7 Spülmaschine {DASH} von Alex\nDir zugewiesen."


def test_assignment_and_own_actions() -> None:
    """New assignees hear about it, the person who assigned does not."""
    event = _event("ticket_created", "robin", assignee_ids=["robin", "kind"])
    sent = _by_person(notifications_for(event, DATA, "de"))
    assert set(sent) == {"person.kind"}
    assert sent["person.kind"].kind == "assigned"
    assert not sent["person.kind"].urgent

    event = _event(
        "assignees_changed", "alex", {"added": ["kind", "alex"]}, assignee_ids=["kind"]
    )
    assert set(_by_person(notifications_for(event, DATA, "de"))) == {"person.kind"}


def test_approval_goes_to_the_other_adults_with_buttons() -> None:
    """Approval: adults except the requester; approve/decline need an unlocked phone."""
    event = _event(
        "approval_requested",
        "robin",
        {"price_estimated": "899.00"},
        requested_by_id="robin",
    )
    sent = _by_person(notifications_for(event, DATA, "de"))
    assert set(sent) == {"person.alex"}
    alex = sent["person.alex"]
    assert alex.message == f"#7 Spülmaschine {DASH} von Robin\ngeschätzt 899,00 €"
    assert [a["action"] for a in alex.actions] == [
        "HAUSHALTTICKETS_APPROVE_7",
        "HAUSHALTTICKETS_DECLINE_7",
    ]
    assert all(a["authenticationRequired"] for a in alex.actions)
    assert alex.actions[1]["destructive"]

    decided = _event(
        "approval_decided", "alex", {"decision": "declined"}, requested_by_id="robin"
    )
    robin = _by_person(notifications_for(decided, DATA, "en"))["person.robin"]
    assert robin.title == "Approval declined"


def test_status_changes_reach_the_requester() -> None:
    """Acceptance (with a button), done and dream-on go to the requester only."""
    acceptance = _event("status_changed", "kind", {"new": "acceptance"})
    alex = _by_person(notifications_for(acceptance, DATA, "de"))["person.alex"]
    assert alex.kind == "acceptance"
    assert alex.actions[0]["action"] == "HAUSHALTTICKETS_DONE_7"

    in_progress = _event("status_changed", "kind", {"new": "in_progress"})
    assert notifications_for(in_progress, DATA, "de") == []
    own = _event("status_changed", "alex", {"new": "done"})
    assert notifications_for(own, DATA, "de") == []
    rejected = _event("status_changed", "robin", {"new": "rejected"})
    assert _by_person(notifications_for(rejected, DATA, "de"))["person.alex"].title == (
        "Träum weiter"
    )


def test_comment_and_ai() -> None:
    """Comments go to requester and assignees, shortened; AI actions say so."""
    event = _event("comment_added", "robin", assignee_ids=["kind", "robin"])
    event["via"] = "mcp"
    sent = _by_person(
        notifications_for(event, DATA, "de", "Teil  ist\nbestellt. " * 30)
    )
    assert set(sent) == {"person.alex", "person.kind"}
    message = sent["person.alex"].message
    assert message.startswith("#7 Spülmaschine\nRobin über KI: Teil ist bestellt.")
    assert len(message.split(": ", 1)[1]) == 160
    assert message.endswith("…")

    # Field changes, photos and checklists stay quiet.
    for kind in ("ticket_updated", "attachment_added", "checklist_changed"):
        assert notifications_for(_event(kind, "robin"), DATA, "de") == []
    # ... unless the ticket now burns.
    burning = _event(
        "ticket_updated", "robin", {"fields": ["urgency"]}, urgency="burning"
    )
    assert set(_by_person(notifications_for(burning, DATA, "de"))) == {"person.alex"}


def test_due_reminders() -> None:
    """One message per person, sorted by due date; requester when nobody is assigned."""
    tickets = [
        {"id": 1, "title": "Filter", "status": "new", "due_date": "2026-11-25",
         "assignee_ids": ["robin"], "requested_by_id": "alex"},
        {"id": 2, "title": "Dübel", "status": "in_progress", "due_date": "2026-11-20",
         "assignee_ids": ["robin", "kind"], "requested_by_id": "alex"},
        {"id": 3, "title": "Lampe", "status": "new", "due_date": "2026-11-24",
         "assignee_ids": [], "requested_by_id": "alex"},
        {"id": 4, "title": "Später", "status": "new", "due_date": "2026-11-28",
         "assignee_ids": ["robin"], "requested_by_id": "alex"},
        {"id": 5, "title": "Ohne Datum", "status": "new", "due_date": None,
         "assignee_ids": ["robin"], "requested_by_id": "alex"},
    ]  # fmt: skip
    sent = _by_person(due_notifications(tickets, DATA, date(2026, 11, 24), "de"))
    assert set(sent) == {"person.robin", "person.kind", "person.alex"}
    assert sent["person.robin"].message == (
        "Überfällig: #2 Dübel (20.11.)\nMorgen: #1 Filter"
    )
    assert sent["person.alex"].message == "Heute: #3 Lampe"
    assert sent["person.kind"].title == "Fällige Tickets"


# --- against the real server -------------------------------------------------


async def _call(hass: HomeAssistant, service: str, user_id: str | None, **data):
    if service == "create_ticket":
        data.setdefault("type", "repair")
    return await hass.services.async_call(
        DOMAIN,
        service,
        data,
        blocking=True,
        return_response=service
        in ("create_ticket", "set_status", "send_due_reminders"),
        context=Context(user_id=user_id),
    )


async def _settle(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()


async def _tap(hass: HomeAssistant, user_id: str, action: str) -> None:
    hass.bus.async_fire(
        EVENT_NOTIFICATION_ACTION, {"action": action}, context=Context(user_id=user_id)
    )
    await hass.async_block_till_done()


async def test_approve_from_the_notification(
    hass: HomeAssistant, loaded: MockConfigEntry, household: Household
) -> None:
    """Robin's purchase asks Alex; Alex approves with the button, Robin hears back."""
    await _call(
        hass, "set_role", household.alex.id, person="person.robin", role="adult"
    )
    sent = async_capture_events(hass, EVENT_NOTIFICATION)
    ticket = await _call(
        hass,
        "create_ticket",
        household.robin.id,
        type="purchase",
        title="Waschmaschine",
        extra_fields={"price_estimated": "899.00"},
    )
    await _settle(hass, loaded)
    ask = [e.data for e in sent if e.data["kind"] == "approval_requested"]
    assert len(ask) == 1
    assert ask[0]["person"] == "person.alex"
    assert ask[0]["actions"][0]["action"] == f"HAUSHALTTICKETS_APPROVE_{ticket['id']}"

    # Robin cannot approve his own purchase: he is told why, nothing changes.
    sent.clear()
    await _tap(hass, household.robin.id, f"HAUSHALTTICKETS_APPROVE_{ticket['id']}")
    failed = [e.data for e in sent if e.data["kind"] == "action_failed"]
    assert len(failed) == 1
    assert failed[0]["person"] == "person.robin"
    assert "other adult" in failed[0]["message"]

    sent.clear()
    await _tap(hass, household.alex.id, f"HAUSHALTTICKETS_APPROVE_{ticket['id']}")
    await _settle(hass, loaded)
    client = loaded.runtime_data.client
    assert (await client.ticket(ticket["id"]))["approval_state"] == "approved"
    back = [e.data for e in sent if e.data["kind"] == "approved"]
    assert [n["person"] for n in back] == ["person.robin"]

    # Unknown actions and taps without a user are ignored.
    sent.clear()
    await _tap(hass, household.alex.id, "SOMETHING_ELSE")
    hass.bus.async_fire(
        EVENT_NOTIFICATION_ACTION, {"action": f"HAUSHALTTICKETS_DECLINE_{ticket['id']}"}
    )
    await hass.async_block_till_done()
    assert sent == []


async def test_accept_from_the_notification(
    hass: HomeAssistant, loaded: MockConfigEntry, household: Household
) -> None:
    """Robin reports done, Alex accepts with the button."""
    await _call(
        hass, "set_role", household.alex.id, person="person.robin", role="adult"
    )
    sent = async_capture_events(hass, EVENT_NOTIFICATION)
    ticket = await _call(
        hass,
        "create_ticket",
        household.alex.id,
        title="Rollladen",
        assignees=["person.robin"],
    )
    await _call(
        hass,
        "set_status",
        household.robin.id,
        ticket_id=ticket["id"],
        status="acceptance",
    )
    await _settle(hass, loaded)
    kinds = {(e.data["person"], e.data["kind"]) for e in sent}
    assert ("person.robin", "assigned") in kinds
    assert ("person.alex", "acceptance") in kinds

    await _tap(hass, household.alex.id, f"HAUSHALTTICKETS_DONE_{ticket['id']}")
    await _settle(hass, loaded)
    assert (await loaded.runtime_data.client.ticket(ticket["id"]))["status"] == "done"


async def test_send_due_reminders(
    hass: HomeAssistant, loaded: MockConfigEntry, household: Household
) -> None:
    """The action sends one summary per person and reports how many."""
    sent = async_capture_events(hass, EVENT_NOTIFICATION)
    ticket = await _call(
        hass,
        "create_ticket",
        household.alex.id,
        title="Heizungsfilter",
        due_date="2020-01-01",
        assignees=["person.robin"],
    )
    result = await _call(hass, "send_due_reminders", None)
    assert result == {"notifications": 1}
    due = [e.data for e in sent if e.data["kind"] == "due"]
    assert due[0]["person"] == "person.robin"
    assert f"#{ticket['id']} Heizungsfilter (01.01.)" in due[0]["message"] or (
        f"#{ticket['id']} Heizungsfilter (2020-01-01)" in due[0]["message"]
    )
