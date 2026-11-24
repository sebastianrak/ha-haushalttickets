"""Recurring tickets: series, rounds, lead time and who may manage them."""

from __future__ import annotations

from datetime import date, timedelta
import time

from fastapi import FastAPI
from fastapi.testclient import TestClient

from haushalttickets_server import recurrence
from haushalttickets_server.models import Series

from .conftest import Api, create, error_code

TODAY = recurrence.today


def run(app: FastAPI, day: date) -> list[int]:
    with app.state.sessionmaker() as session:
        created = [ticket.id for ticket in recurrence.run(session, day)]
        session.commit()
    return created


def new_series(api: Api, name: str = "alex", **body) -> dict:
    body.setdefault("type", "repair")
    body.setdefault("title", "Rauchmelder testen")
    body.setdefault("mode", "after_completion")
    body.setdefault("interval_count", 1)
    body.setdefault("interval_unit", "months")
    response = api(name).post("/api/v1/series", json=body)
    assert response.status_code == 201, response.text
    return response.json()


def tickets(api: Api) -> list[dict]:
    return api("alex").get("/api/v1/tickets").json()["tickets"]


def done(api: Api, ticket_id: int, name: str = "alex") -> None:
    response = api(name).post(
        f"/api/v1/tickets/{ticket_id}/status", json={"status": "done"}
    )
    assert response.status_code == 200, response.text


def test_dates() -> None:
    """Months keep the anchor day where it exists; the grid never drifts."""
    add = recurrence.add_interval
    assert add(date(2027, 1, 31), 1, "months") == date(2027, 2, 28)
    assert add(date(2027, 1, 31), 1, "months", times=2) == date(2027, 3, 31)
    assert add(date(2028, 2, 29), 1, "years") == date(2029, 2, 28)
    assert add(date(2026, 12, 1), 2, "weeks") == date(2026, 12, 15)

    class S:
        anchor = date(2027, 1, 31)
        interval_count = 1
        interval_unit = "months"

    assert recurrence.grid_on_or_after(S, date(2027, 3, 1)) == date(2027, 3, 31)
    assert recurrence.grid_on_or_after(S, date(2027, 1, 1)) == date(2027, 1, 31)
    S.interval_unit, S.interval_count = "days", 10
    assert recurrence.grid_on_or_after(S, date(2027, 2, 2)) == date(2027, 2, 10)


def test_blank_series_appears_with_lead_time(api: Api, app: FastAPI) -> None:
    """The first round appears lead_days before it is due, with the template."""
    today = TODAY()
    series = new_series(
        api,
        first_due=str(today + timedelta(days=10)),
        lead_days=7,
        assignee_ids=["u-robin"],
        checklist=["Flur", "Küche"],
        fields={"contractor_name": "Elektro Müller"},
    )
    assert series["open_ticket_id"] is None
    assert series["appears_on"] == str(today + timedelta(days=3))
    assert tickets(api) == []

    assert run(app, today + timedelta(days=2)) == []
    [ticket_id] = run(app, today + timedelta(days=3))
    ticket = api("alex").get(f"/api/v1/tickets/{ticket_id}").json()
    assert ticket["series_id"] == series["id"]
    assert ticket["created_via"] == "recurrence"
    assert ticket["created_by_id"] == "automation"
    assert ticket["requested_by_id"] == "u-alex"
    assert ticket["assignee_ids"] == ["u-robin"]
    assert ticket["due_date"] == str(today + timedelta(days=10))
    assert [i["text"] for i in ticket["checklist"]] == ["Flur", "Küche"]
    assert ticket["contractor_name"] == "Elektro Müller"
    assert run(app, today + timedelta(days=4)) == []  # one open round at most

    events = api().get("/api/v1/events").json()["events"]
    created = [e for e in events if e["type"] == "ticket_created"][-1]
    assert created["via"] == "recurrence"
    assert created["payload"]["series_id"] == series["id"]
    assert "series_changed" in [e["type"] for e in events]


def test_after_completion(api: Api, app: FastAPI) -> None:
    """The next round is one interval after the last one was closed."""
    today = TODAY()
    new_series(api, first_due=str(today), interval_unit="weeks", interval_count=2)
    [first] = [t["id"] for t in tickets(api)]
    done(api, first)
    [series] = api("alex").get("/api/v1/series").json()
    assert series["next_due"] == str(today + timedelta(weeks=2))
    assert series["open_ticket_id"] is None
    [second] = run(app, today + timedelta(weeks=2) - timedelta(days=7))
    assert second != first


def test_dream_on_skips_one_round(api: Api, app: FastAPI) -> None:
    """Closing a round as dream on keeps the series going."""
    today = TODAY()
    new_series(api, first_due=str(today), interval_unit="days", interval_count=3)
    [first] = [t["id"] for t in tickets(api)]
    api("alex").post(f"/api/v1/tickets/{first}/status", json={"status": "rejected"})
    # Within the lead time already, so the next round follows right away.
    open_ids = [t["id"] for t in tickets(api) if t["status"] == "new"]
    assert len(open_ids) == 1 and open_ids[0] != first


def test_fixed_calendar_from_ticket(api: Api, app: FastAPI) -> None:
    """A ticket becomes the first round; the calendar continues from its due date."""
    today = TODAY()
    due = today + timedelta(days=2)
    ticket = create(api, "robin", title="Heizung entlüften", due_date=str(due))
    response = api("robin").post(
        "/api/v1/series",
        json={
            "from_ticket_id": ticket["id"],
            "mode": "fixed_calendar",
            "interval_count": 1,
            "interval_unit": "months",
        },
    )
    assert response.status_code == 201, response.text
    series = response.json()
    assert series["title"] == "Heizung entlüften"
    assert series["requested_by_id"] == "u-robin"
    assert series["anchor"] == str(due)
    assert series["open_ticket_id"] == ticket["id"]
    next_due = recurrence.add_interval(due, 1, "months")
    assert series["next_due"] == str(next_due)
    assert (
        api("alex").get(f"/api/v1/tickets/{ticket['id']}").json()["series_id"]
        == (series["id"])
    )

    again = api("robin").post(
        "/api/v1/series",
        json={"from_ticket_id": ticket["id"], "mode": "fixed_calendar",
              "interval_count": 1, "interval_unit": "months"},
    )  # fmt: skip
    assert error_code(again) == "already_in_series"

    # Still open when the next date comes into view: nothing new.
    assert run(app, next_due - timedelta(days=7)) == []
    done(api, ticket["id"], "robin")
    [second] = run(app, next_due - timedelta(days=7))
    assert api("alex").get(f"/api/v1/tickets/{second}").json()["due_date"] == str(
        next_due
    )


def test_missed_calendar_dates_are_skipped(api: Api, app: FastAPI) -> None:
    """No overdue rounds: a past start moves to the next calendar date."""
    today = TODAY()
    series = new_series(
        api,
        mode="fixed_calendar",
        interval_unit="weeks",
        interval_count=1,
        first_due=str(today - timedelta(days=20)),
    )
    [ticket] = tickets(api)
    assert date.fromisoformat(ticket["due_date"]) >= today
    assert (
        date.fromisoformat(ticket["due_date"]) - date.fromisoformat(series["anchor"])
    ).days % 7 == 0


def test_pause_resume_and_end(api: Api, app: FastAPI) -> None:
    today = TODAY()
    series = new_series(api, first_due=str(today + timedelta(days=30)))
    sid = series["id"]
    paused = api("alex").patch(f"/api/v1/series/{sid}", json={"active": False}).json()
    assert paused["active"] is False
    assert paused["appears_on"] is None
    assert run(app, today + timedelta(days=30)) == []

    moved = (
        api("alex")
        .patch(f"/api/v1/series/{sid}", json={"active": True, "next_due": str(today)})
        .json()
    )
    assert moved["open_ticket_id"] is not None  # due now: created right away
    ticket_id = moved["open_ticket_id"]

    assert api("alex").delete(f"/api/v1/series/{sid}").status_code == 204
    assert api("alex").get("/api/v1/series").json() == []
    assert api("alex").get(f"/api/v1/tickets/{ticket_id}").json()["series_id"] is None


def test_template_changes_affect_future_rounds(api: Api, app: FastAPI) -> None:
    today = TODAY()
    series = new_series(
        api, first_due=str(today), interval_unit="days", interval_count=1
    )
    response = api("alex").patch(
        f"/api/v1/series/{series['id']}",
        json={"title": "Rauchmelder prüfen", "fields": {"cost_estimated": "20"}},
    )
    assert response.json()["fields"] == {"cost_estimated": "20.00"}
    [first] = tickets(api)
    assert first["title"] == "Rauchmelder testen"  # the open round stays
    done(api, first["id"])
    second = next(t for t in tickets(api) if t["status"] == "new")
    assert second["title"] == "Rauchmelder prüfen"
    assert second["cost_estimated"] == "20.00"

    wrong = api("alex").patch(
        f"/api/v1/series/{series['id']}", json={"fields": {"vendor": "Baumarkt"}}
    )
    assert error_code(wrong) == "field_not_for_type"


def test_purchase_rounds_need_approval(api: Api, app: FastAPI) -> None:
    """Every round of an expensive purchase asks for approval again."""
    today = TODAY()
    new_series(
        api,
        "robin",
        type="purchase",
        title="Wasserfilter",
        first_due=str(today),
        fields={"price_estimated": "300"},
    )
    [ticket] = tickets(api)
    assert ticket["approval_state"] == "pending"
    assert ticket["status"] == "discussing"


def test_who_manages_series(api: Api, app: FastAPI) -> None:
    today = TODAY()
    first_due = str(today + timedelta(days=60))
    # Children: own series without assignees.
    own = new_series(api, "kind", title="Zimmer aufräumen", first_due=first_due)
    assert own["requested_by_id"] == "u-kind"
    response = api("kind").post(
        "/api/v1/series",
        json={"type": "repair", "title": "X", "mode": "after_completion",
              "interval_count": 1, "interval_unit": "weeks", "first_due": first_due,
              "assignee_ids": ["u-robin"]},
    )  # fmt: skip
    assert error_code(response) == "assign_forbidden"
    adults = new_series(api, "robin", first_due=first_due)
    response = api("kind").patch(
        f"/api/v1/series/{adults['id']}", json={"active": False}
    )
    assert error_code(response) == "series_forbidden"
    assert error_code(api("kind").delete(f"/api/v1/series/{adults['id']}")) == (
        "series_forbidden"
    )
    paused = api("kind").patch(f"/api/v1/series/{own['id']}", json={"active": False})
    assert paused.status_code == 200
    response = api("kind").patch(
        f"/api/v1/series/{own['id']}", json={"fields": {"cost_estimated": "5"}}
    )
    assert error_code(response) == "field_forbidden"
    # Adults manage every series.
    assert (
        api("robin")
        .patch(f"/api/v1/series/{own['id']}", json={"lead_days": 3})
        .json()["lead_days"]
        == 3
    )

    # AI and automations do not.
    body = {"type": "repair", "title": "X", "mode": "after_completion",
            "interval_count": 1, "interval_unit": "weeks", "first_due": first_due}  # fmt: skip
    assert error_code(api("alex", "mcp").post("/api/v1/series", json=body)) == (
        "ai_series_forbidden"
    )
    assert error_code(
        api("automation", "automation").post("/api/v1/series", json=body)
    ) == ("forbidden")
    # Only the server itself creates rounds.
    response = api("alex", "recurrence").get("/api/v1/series")
    assert error_code(response) == "invalid_via"


def test_server_creates_rounds_on_its_own(api: Api, app: FastAPI) -> None:
    """The background loop creates due rounds when the server runs."""
    series = new_series(api, first_due=str(TODAY() + timedelta(days=30)))
    with app.state.sessionmaker() as session:
        session.get(Series, series["id"]).next_due = TODAY()
        session.commit()
    with TestClient(app):
        for _ in range(50):
            if tickets(api):
                break
            time.sleep(0.1)
    assert [t["series_id"] for t in tickets(api)] == [series["id"]]
