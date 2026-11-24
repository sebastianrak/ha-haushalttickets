"""Nightly run: runner token, work queue, drafts, changes, watchdog."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from haushalttickets_server import nightly
from haushalttickets_server.__main__ import main
from haushalttickets_server.models import NightlyRun, Setting, User

from .conftest import Api, create, error_code, uid


@pytest.fixture
def runner_token(app: FastAPI, household: None) -> str:
    with app.state.sessionmaker() as session:
        _, token = nightly.create_access(session, "KI-Hausmeister")
        session.commit()
    return token


@pytest.fixture
def runner(app: FastAPI, runner_token: str) -> TestClient:
    client = TestClient(app)
    client.headers["Authorization"] = f"Bearer {runner_token}"
    return client


def flags(api: Api, name: str, ticket_id: int, **body):
    return api(name).post(f"/api/v1/tickets/{ticket_id}/ai-flags", json=body)


def start(runner: TestClient) -> int:
    response = runner.post("/api/v1/nightly/runs")
    assert response.status_code == 201, response.text
    return response.json()["id"]


def result(runner: TestClient, ticket_id: int, **body):
    return runner.post(f"/api/v1/nightly/tickets/{ticket_id}/result", json=body)


def draft(runner: TestClient, run_id: int, ticket_id: int, text: str = "Plan") -> dict:
    response = result(
        runner,
        ticket_id,
        run_id=run_id,
        kind="draft",
        draft=text,
        comment="Entwurf bereit",
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_token_needs_ai_role(app: FastAPI, household: None) -> None:
    with app.state.sessionmaker() as session:
        session.get(User, uid("ki")).role = "admin"
        with pytest.raises(nightly.Invalid) as err:
            nightly.create_access(session, "KI-Hausmeister")
        assert err.value.code == "not_ai_agent"


def test_runner_is_ai_and_read_only_outside_nightly(
    app: FastAPI, api: Api, runner: TestClient
) -> None:
    create(api, "robin")
    # Reading works, acting as the AI account.
    assert runner.get("/api/v1/tickets").json()["count"] == 1
    # Writing outside /nightly/ is refused, whatever headers it sends.
    response = runner.post(
        "/api/v1/tickets/1/comments",
        json={"body": "x"},
        headers={"X-Acting-User": uid("alex"), "X-Acting-Via": "ui"},
    )
    assert error_code(response) == "nightly_only"
    # The integration token may not pretend to be the runner.
    response = api("ki", "nightly_run").get("/api/v1/tickets")
    assert error_code(response) == "invalid_via"
    assert error_code(api("alex").get("/api/v1/nightly/work")) == "nightly_only"
    # Role changed back -> token stops working.
    with app.state.sessionmaker() as session:
        session.get(User, uid("ki")).role = "admin"
        session.commit()
    assert error_code(runner.get("/api/v1/tickets")) == "nightly_user_not_ai"


def test_draft_then_approval_then_change(api: Api, runner: TestClient) -> None:
    create(api, "robin", type="home_assistant", title="Licht Flur")
    assert flags(api, "robin", 1, draft_requested=True).status_code == 200
    work = runner.get("/api/v1/nightly/work").json()["items"]
    assert [(i["kind"], i["ticket"]["id"], i["draft"]) for i in work] == [
        ("draft", 1, None)
    ]
    run_id = start(runner)
    stored = draft(runner, run_id, 1, "Automation: Bewegung -> Licht")
    assert stored["result"]["version"] == 1
    ticket = stored["ticket"]
    assert ticket["ai_draft_requested"] is False
    assert ticket["latest_draft_version"] == 1
    assert runner.get("/api/v1/nightly/work").json()["items"] == []

    # Approval binds to draft 1.
    approved = flags(api, "robin", 1, implement_approved=True).json()
    assert approved["ai_implement_draft_version"] == 1
    item = runner.get("/api/v1/nightly/work").json()["items"][0]
    assert item["kind"] == "implement"
    assert item["draft"]["version"] == 1

    response = result(
        runner,
        1,
        run_id=run_id,
        kind="implemented",
        comment="Umgesetzt",
        change={
            "summary": "Automation angelegt",
            "objects": ["automation.licht_flur"],
            "git_commit": "abc123",
            "needs_restart": False,
        },
    )
    assert response.status_code == 200, response.text
    ticket = response.json()["ticket"]
    assert ticket["status"] == "acceptance"
    assert ticket["ai_implement_approved"] is False
    changes = api("robin").get("/api/v1/tickets/1/changes").json()
    assert changes[0]["git_commit"] == "abc123"
    assert changes[0]["objects"] == ["automation.licht_flur"]
    comments = api("robin").get("/api/v1/tickets/1/comments").json()
    assert [(c["author_id"], c["via"]) for c in comments] == [
        (uid("ki"), "nightly_run"),
        (uid("ki"), "nightly_run"),
    ]

    ended = runner.patch(
        f"/api/v1/nightly/runs/{run_id}",
        json={"outcome": "done", "usage": {"cost_usd": 0.5}},
    ).json()
    assert ended["tickets"] == [1]
    assert [r["kind"] for r in ended["results"]] == ["draft", "implemented"]
    event = api().get("/api/v1/events").json()["events"][-1]
    assert event["type"] == "nightly_run_finished"
    assert event["via"] == "nightly_run"
    assert event["payload"]["results"][0]["requested_by_id"] == uid("robin")
    # A run ends once.
    response = runner.patch(f"/api/v1/nightly/runs/{run_id}", json={"outcome": "done"})
    assert error_code(response) == "run_ended"


def test_new_draft_withdraws_approval(api: Api, runner: TestClient) -> None:
    create(api, "robin", type="home_assistant")
    flags(api, "robin", 1, draft_requested=True)
    run_id = start(runner)
    draft(runner, run_id, 1)
    flags(api, "robin", 1, implement_approved=True)
    # Asking for a revision withdraws the approval right away.
    revised = flags(api, "robin", 1, draft_requested=True).json()
    assert revised["ai_implement_approved"] is False
    assert revised["ai_implement_draft_version"] is None
    response = flags(api, "robin", 1, implement_approved=True)
    assert error_code(response) == "draft_pending"
    item = runner.get("/api/v1/nightly/work").json()["items"][0]
    assert (item["kind"], item["draft"]["version"]) == ("draft", 1)
    assert draft(runner, run_id, 1, "v2")["result"]["version"] == 2
    drafts = api("robin").get("/api/v1/tickets/1/drafts").json()
    assert [d["version"] for d in drafts] == [2, 1]
    approved = flags(api, "robin", 1, implement_approved=True).json()
    assert approved["ai_implement_draft_version"] == 2


def test_rules_for_results(api: Api, runner: TestClient) -> None:
    create(api, "robin", type="repair")
    run_id = start(runner)
    # No draft requested.
    response = result(runner, 1, run_id=run_id, kind="draft", draft="x", comment="x")
    assert error_code(response) == "draft_not_requested"
    # Not approved.
    response = result(
        runner,
        1,
        run_id=run_id,
        kind="implemented",
        comment="x",
        change={"summary": "x"},
    )
    assert error_code(response) == "not_approved"
    # Repairs are never implemented.
    flags(api, "robin", 1, draft_requested=True)
    draft(runner, run_id, 1)
    response = flags(api, "robin", 1, implement_approved=True)
    assert error_code(response) == "implement_not_applicable"


def test_failure_keeps_or_clears_request(api: Api, runner: TestClient) -> None:
    create(api, "robin")
    create(api, "robin")
    flags(api, "robin", 1, draft_requested=True)
    flags(api, "robin", 2, draft_requested=True)
    run_id = start(runner)
    kept = result(
        runner,
        1,
        run_id=run_id,
        kind="failed",
        phase="draft",
        retry=True,
        comment="Limit erreicht, nächste Nacht erneut",
    ).json()["ticket"]
    assert kept["ai_draft_requested"] is True
    cleared = result(
        runner,
        2,
        run_id=run_id,
        kind="failed",
        phase="draft",
        comment="Nicht machbar",
    ).json()["ticket"]
    assert cleared["ai_draft_requested"] is False


def test_work_order(api: Api, runner: TestClient) -> None:
    create(api, "robin", title="irgendwann")
    create(api, "robin", title="brennt", urgency="burning")
    create(api, "robin", title="woche wichtig", urgency="this_week", importance=5)
    create(api, "robin", title="woche", urgency="this_week", importance=2)
    for ticket_id in (1, 2, 3, 4):
        flags(api, "robin", ticket_id, draft_requested=True)
    titles = [
        i["ticket"]["title"] for i in runner.get("/api/v1/nightly/work").json()["items"]
    ]
    assert titles == ["brennt", "woche wichtig", "woche", "irgendwann"]


def test_watchdog(app: FastAPI, api: Api, runner: TestClient) -> None:
    create(api, "robin")
    now = datetime.now(UTC)
    with app.state.sessionmaker() as session:
        # Nothing waits -> no alarm, even long after the token was made.
        assert not nightly.check_watchdog(session, now + timedelta(days=3))
    flags(api, "robin", 1, draft_requested=True)
    with app.state.sessionmaker() as session:
        assert not nightly.check_watchdog(session, now + timedelta(hours=2))
        assert nightly.check_watchdog(session, now + timedelta(hours=27))
        # Once a day only.
        assert not nightly.check_watchdog(session, now + timedelta(hours=30))
        session.commit()
    event = api().get("/api/v1/events").json()["events"][-1]
    assert event["type"] == "nightly_run_missing"
    assert event["payload"]["pending"] == 1
    with app.state.sessionmaker() as session:
        session.add(NightlyRun(started_at=now + timedelta(hours=40)))
        session.flush()
        assert not nightly.check_watchdog(session, now + timedelta(hours=60))
        assert session.get(Setting, nightly.SETTING_WATCHDOG) is not None


def test_cli(app: FastAPI, household: None, capsys, monkeypatch) -> None:
    monkeypatch.setenv("HAUSHALTTICKETS_DATABASE_URL", app.state.settings.database_url)
    assert main(["nightly", "token", "--user", "KI-Hausmeister"]) == 0
    token = capsys.readouterr().out.strip()
    client = TestClient(app)
    response = client.get(
        "/api/v1/nightly/work", headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 200
    assert main(["nightly", "token", "--user", "Alex"]) == 1
    assert main(["nightly", "status"]) == 0
    assert '"user": "KI-Hausmeister"' in capsys.readouterr().out
    assert main(["nightly", "revoke"]) == 0
    response = client.get(
        "/api/v1/nightly/work", headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 401
