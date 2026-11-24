"""Purchase approval by the second adult; the decision moves the ticket."""

from .conftest import Api, create, error_code


def purchase(api: Api, name: str = "robin", **body) -> dict:
    return create(api, name, type="purchase", title="Waschmaschine", **body)


def approve(api: Api, name: str, decision: str = "approve", via: str = "ui"):
    return api(name, via).post(
        "/api/v1/tickets/1/approval", json={"decision": decision}
    )


def move(api: Api, name: str, status: str):
    return api(name).post("/api/v1/tickets/1/status", json={"status": status})


def test_below_threshold_needs_no_approval(api: Api) -> None:
    ticket = purchase(api, price_estimated="249.99")
    assert ticket["approval_state"] == "not_required"
    assert move(api, "robin", "in_progress").status_code == 200


def status_moves(api: Api) -> list[tuple[str, str, str | None]]:
    events = api().get("/api/v1/events").json()["events"]
    return [
        (e["payload"]["old"], e["payload"]["new"], e["payload"].get("reason"))
        for e in events
        if e["type"] == "status_changed"
    ]


def test_threshold_requires_approval(api: Api) -> None:
    """Waiting for approval means being discussed."""
    ticket = purchase(api, price_estimated="250")
    assert ticket["approval_state"] == "pending"
    assert ticket["status"] == "discussing"
    types = [e["type"] for e in api().get("/api/v1/events").json()["events"]]
    assert types.index("approval_requested") < types.index("status_changed")
    assert status_moves(api) == [("new", "discussing", "approval_requested")]
    assert error_code(move(api, "robin", "in_progress")) == "approval_pending"


def test_who_approves(api: Api) -> None:
    purchase(api, price_estimated="800")
    assert error_code(approve(api, "robin")) == "approval_by_other_adult"
    assert error_code(approve(api, "kind")) == "approval_forbidden"
    response = approve(api, "alex", via="mcp")
    assert error_code(response) == "approval_forbidden"
    approved = approve(api, "alex").json()
    assert approved["approval_state"] == "approved"
    assert approved["approval_by_id"] == "u-alex"
    assert approved["status"] == "new"  # open: to do
    assert move(api, "robin", "in_progress").status_code == 200
    assert error_code(approve(api, "alex")) == "approval_not_pending"


def test_declined_goes_to_dream_on(api: Api) -> None:
    """Declined: dream on (closed); a later approval brings it back as open."""
    purchase(api, price_estimated="800")
    declined = approve(api, "alex", "decline").json()
    assert declined["approval_state"] == "declined"
    assert declined["status"] == "rejected"
    assert declined["closed_at"] is not None
    assert status_moves(api)[-1] == ("discussing", "rejected", "approval_declined")
    assert error_code(move(api, "robin", "in_progress")) == "approval_pending"

    approved = approve(api, "alex").json()  # changed her mind
    assert approved["approval_state"] == "approved"
    assert approved["status"] == "new"
    assert approved["closed_at"] is None
    assert status_moves(api)[-1] == ("rejected", "new", "approval_approved")


def test_declined_while_reopened(api: Api) -> None:
    """Reopened for another talk and declined again: dream on again."""
    purchase(api, price_estimated="800")
    approve(api, "alex", "decline")
    assert move(api, "robin", "discussing").status_code == 200
    assert approve(api, "alex", "decline").json()["status"] == "rejected"


def test_price_changes(api: Api) -> None:
    """A price over the threshold pulls a running purchase back into discussion."""
    purchase(api, price_estimated="100")
    move(api, "robin", "in_progress")
    response = api("robin").patch("/api/v1/tickets/1", json={"price_estimated": "400"})
    assert response.json()["approval_state"] == "pending"
    assert response.json()["status"] == "discussing"
    response = api("robin").patch("/api/v1/tickets/1", json={"price_estimated": "90"})
    assert response.json()["approval_state"] == "not_required"
    assert response.json()["status"] == "discussing"  # left as it is


def test_threshold_setting(api: Api) -> None:
    api("alex").patch("/api/v1/settings", json={"purchase_approval_threshold": "1000"})
    assert purchase(api, price_estimated="800")["approval_state"] == "not_required"


def test_not_a_purchase(api: Api) -> None:
    create(api, "robin")
    assert error_code(approve(api, "alex")) == "not_a_purchase"
