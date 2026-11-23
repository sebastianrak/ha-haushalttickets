"""Purchase approval by the second adult."""

from .conftest import Api, create, error_code


def purchase(api: Api, name: str = "sarah", **body) -> dict:
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
    assert move(api, "sarah", "in_progress").status_code == 200


def test_threshold_requires_approval(api: Api) -> None:
    ticket = purchase(api, price_estimated="250")
    assert ticket["approval_state"] == "pending"
    types = [e["type"] for e in api().get("/api/v1/events").json()["events"]]
    assert "approval_requested" in types
    assert move(api, "sarah", "discussing").status_code == 200
    assert error_code(move(api, "sarah", "in_progress")) == "approval_pending"


def test_who_approves(api: Api) -> None:
    purchase(api, price_estimated="800")
    assert error_code(approve(api, "sarah")) == "approval_by_other_adult"
    assert error_code(approve(api, "kind")) == "approval_forbidden"
    response = approve(api, "sebastian", via="mcp")
    assert error_code(response) == "approval_forbidden"
    approved = approve(api, "sebastian").json()
    assert approved["approval_state"] == "approved"
    assert approved["approval_by_id"] == "u-sebastian"
    assert move(api, "sarah", "in_progress").status_code == 200
    assert error_code(approve(api, "sebastian")) == "approval_not_pending"


def test_declined_blocks_until_approved(api: Api) -> None:
    purchase(api, price_estimated="800")
    assert approve(api, "sebastian", "decline").json()["approval_state"] == "declined"
    assert error_code(move(api, "sarah", "in_progress")) == "approval_pending"
    assert move(api, "sarah", "rejected").status_code == 200
    move(api, "sarah", "discussing")
    assert approve(api, "sebastian").json()["approval_state"] == "approved"


def test_price_changes(api: Api) -> None:
    purchase(api, price_estimated="100")
    response = api("sarah").patch("/api/v1/tickets/1", json={"price_estimated": "400"})
    assert response.json()["approval_state"] == "pending"
    response = api("sarah").patch("/api/v1/tickets/1", json={"price_estimated": "90"})
    assert response.json()["approval_state"] == "not_required"


def test_threshold_setting(api: Api) -> None:
    api("sebastian").patch(
        "/api/v1/settings", json={"purchase_approval_threshold": "1000"}
    )
    assert purchase(api, price_estimated="800")["approval_state"] == "not_required"


def test_not_a_purchase(api: Api) -> None:
    create(api, "sarah")
    assert error_code(approve(api, "sebastian")) == "not_a_purchase"
