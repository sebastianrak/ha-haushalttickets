"""Status rules: acceptance by the requester, AI limits, children."""

from .conftest import Api, create, error_code


def move(api: Api, name: str, status: str, ticket_id: int = 1, via: str = "ui"):
    return api(name, via).post(
        f"/api/v1/tickets/{ticket_id}/status", json={"status": status}
    )


def test_adults_move_freely(api: Api) -> None:
    create(api, "robin")
    for status in ("discussing", "in_progress", "waiting_for_parts", "acceptance"):
        assert move(api, "alex", status).status_code == 200
    assert move(api, "robin", "new").json()["status"] == "new"


def test_only_requester_accepts(api: Api) -> None:
    create(api, "alex")
    response = move(api, "robin", "done")
    assert error_code(response) == "acceptance_by_requester"
    response = move(api, "alex", "done")
    assert response.json()["status"] == "done"
    assert response.json()["closed_at"] is not None


def test_admin_override_is_recorded(api: Api) -> None:
    create(api, "robin")
    response = move(api, "alex", "done")
    assert response.status_code == 200
    fields = [h["field"] for h in api("alex").get("/api/v1/tickets/1/history").json()]
    assert "admin_override" in fields
    event = api().get("/api/v1/events").json()["events"][-1]
    assert event["payload"]["admin_override"] is True


def test_reject_and_reopen(api: Api) -> None:
    create(api, "kind")
    assert error_code(move(api, "kind", "rejected")) == "child_status_forbidden"
    assert move(api, "robin", "rejected").json()["status"] == "rejected"
    reopened = move(api, "robin", "in_progress").json()
    assert reopened["status"] == "in_progress"
    assert reopened["closed_at"] is None


def test_ai_limits(api: Api) -> None:
    create(api, "robin")
    for name, via in (("alex", "mcp"), ("alex", "nightly_run"), ("ki", "ui")):
        assert move(api, name, "in_progress", via=via).status_code == 200
        assert move(api, name, "acceptance", via=via).status_code == 200
        assert error_code(move(api, name, "done", via=via)) == "ai_status_forbidden"
        assert error_code(move(api, name, "rejected", via=via)) == "ai_status_forbidden"
    move(api, "robin", "done")
    response = move(api, "alex", "in_progress", via="mcp")
    assert error_code(response) == "ai_reopen_forbidden"


def test_children_accept_only_their_own(api: Api) -> None:
    create(api, "kind")
    create(api, "robin")
    assert error_code(move(api, "kind", "in_progress")) == "child_status_forbidden"
    assert error_code(move(api, "kind", "done", 2)) == "child_status_forbidden"
    assert move(api, "kind", "done").json()["status"] == "done"


def test_status_event_payload(api: Api) -> None:
    create(api, "robin", assignee_ids=["u-alex"])
    move(api, "alex", "acceptance")
    event = api().get("/api/v1/events").json()["events"][-1]
    assert event["type"] == "status_changed"
    assert event["payload"]["old"] == "new"
    assert event["payload"]["new"] == "acceptance"
    assert event["payload"]["requested_by_id"] == "u-robin"
    assert event["payload"]["assignee_ids"] == ["u-alex"]
