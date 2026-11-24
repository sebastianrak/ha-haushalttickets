"""Who hears about what, and due reminders."""

from __future__ import annotations

from datetime import date

from homeassistant.core import Context, HomeAssistant
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_capture_events,
)

from custom_components.haushalttickets.api import Acting
from custom_components.haushalttickets.const import DOMAIN, EVENT_NOTIFICATION
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


def test_approval_goes_to_the_other_adults() -> None:
    """Approval: adults except the requester, with the price; decided in the ticket."""
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
    assert alex.ticket_id == 7
    assert "actions" not in alex.as_event()

    decided = _event(
        "approval_decided", "alex", {"decision": "declined"}, requested_by_id="robin"
    )
    robin = _by_person(notifications_for(decided, DATA, "en"))["person.robin"]
    assert robin.title == "Approval declined"


def test_status_changes_reach_the_requester() -> None:
    """Acceptance, done and dream-on go to the requester only."""
    acceptance = _event("status_changed", "kind", {"new": "acceptance"})
    alex = _by_person(notifications_for(acceptance, DATA, "de"))["person.alex"]
    assert alex.kind == "acceptance"
    assert alex.title == "Bitte abnehmen"
    assert alex.as_event()["ticket_id"] == 7

    in_progress = _event("status_changed", "kind", {"new": "in_progress"})
    assert notifications_for(in_progress, DATA, "de") == []
    own = _event("status_changed", "alex", {"new": "done"})
    assert notifications_for(own, DATA, "de") == []
    rejected = _event("status_changed", "robin", {"new": "rejected"})
    assert _by_person(notifications_for(rejected, DATA, "de"))["person.alex"].title == (
        "Träum weiter"
    )
    # Moves that follow an approval step: the approval message tells it already.
    after_decline = _event(
        "status_changed", "robin", {"new": "rejected", "reason": "approval_declined"}
    )
    assert notifications_for(after_decline, DATA, "de") == []


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


def test_recurring_round() -> None:
    """A new round goes to its assignees (or the requester) with the due date."""
    event = _event(
        "ticket_created", "automation", assignee_ids=["robin"], due_date="2026-12-01"
    )
    event["via"] = "recurrence"
    sent = _by_person(notifications_for(event, DATA, "de"))
    assert set(sent) == {"person.robin"}
    assert sent["person.robin"].kind == "recurring"
    assert sent["person.robin"].title == "Wiederkehrend"
    assert (
        sent["person.robin"].message == f"#7 Spülmaschine {DASH} fällig am 01.12.2026"
    )

    nobody = _event("ticket_created", "automation", due_date="2026-12-01")
    nobody["via"] = "recurrence"
    alex = _by_person(notifications_for(nobody, DATA, "en"))["person.alex"]
    assert alex.message == f"#7 Spülmaschine {DASH} due 2026-12-01"


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


async def test_approval_and_acceptance_events(
    hass: HomeAssistant, loaded: MockConfigEntry, household: Household
) -> None:
    """Robin's purchase asks Alex; Robin's finished work asks Alex to accept."""
    await _call(
        hass, "set_role", household.alex.id, person="person.robin", role="adult"
    )
    sent = async_capture_events(hass, EVENT_NOTIFICATION)
    purchase = await _call(
        hass,
        "create_ticket",
        household.robin.id,
        type="purchase",
        title="Waschmaschine",
        extra_fields={"price_estimated": "899.00"},
    )
    repair = await _call(
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
        ticket_id=repair["id"],
        status="acceptance",
    )
    await _settle(hass, loaded)
    got = {(e.data["person"], e.data["kind"], e.data["ticket_id"]) for e in sent}
    assert ("person.alex", "approval_requested", purchase["id"]) in got
    assert ("person.robin", "assigned", repair["id"]) in got
    assert ("person.alex", "acceptance", repair["id"]) in got
    assert all("actions" not in e.data for e in sent)


async def test_declined_purchase_is_told_once(
    hass: HomeAssistant, loaded: MockConfigEntry, household: Household
) -> None:
    """Alex declines Robin's purchase: it goes to dream on, Robin hears it once."""
    await _call(
        hass, "set_role", household.alex.id, person="person.robin", role="adult"
    )
    ticket = await _call(
        hass,
        "create_ticket",
        household.robin.id,
        type="purchase",
        title="Fernseher",
        extra_fields={"price_estimated": "1200.00"},
    )
    client = loaded.runtime_data.client
    assert (await client.ticket(ticket["id"]))["status"] == "discussing"
    await _settle(hass, loaded)

    sent = async_capture_events(hass, EVENT_NOTIFICATION)
    await client.request(
        "POST",
        f"/api/v1/tickets/{ticket['id']}/approval",
        acting=Acting(household.alex.id, "ui"),
        json={"decision": "decline"},
    )
    await _settle(hass, loaded)
    assert (await client.ticket(ticket["id"]))["status"] == "rejected"
    assert [(e.data["person"], e.data["kind"]) for e in sent] == [
        ("person.robin", "declined")
    ]


async def test_series_round_is_announced(
    hass: HomeAssistant, loaded: MockConfigEntry, household: Household
) -> None:
    """A series whose first round is due now: Robin hears about it."""
    await _call(
        hass, "set_role", household.alex.id, person="person.robin", role="adult"
    )
    await _settle(hass, loaded)
    sent = async_capture_events(hass, EVENT_NOTIFICATION)
    client = loaded.runtime_data.client
    series = await client.request(
        "POST",
        "/api/v1/series",
        acting=Acting(household.alex.id, "ui"),
        json={
            "type": "repair",
            "title": "Rauchmelder testen",
            "mode": "fixed_calendar",
            "interval_count": 1,
            "interval_unit": "years",
            "first_due": date.today().isoformat(),
            "assignee_ids": [household.robin.id],
        },
    )
    assert series["open_ticket_id"] is not None
    await _settle(hass, loaded)
    got = [(e.data["person"], e.data["kind"]) for e in sent]
    assert got == [("person.robin", "recurring")]


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
