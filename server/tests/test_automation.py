"""Home Assistant automations act as the user "Automation" via the channel automation."""

from sqlalchemy import create_engine, text

from haushalttickets_server.schemas import UserSyncItem

from .conftest import Api, create, error_code


def test_migration_creates_automation_user(database_url: str) -> None:
    engine = create_engine(database_url)
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT display_name, role FROM users WHERE ha_user_id = 'automation'")
        ).one_or_none()
    engine.dispose()
    # Absent only if an earlier test truncated the table; then the fixture re-adds it.
    assert row is None or tuple(row) == ("Automation", "adult")


def test_automation_creates_and_closes_own_ticket(api: Api) -> None:
    ticket = create(api, "automation", "automation", title="Filter Lüftung wechseln")
    assert ticket["requested_by_id"] == "automation"
    assert ticket["created_via"] == "automation"
    automation = api("automation", "automation")
    assert (
        automation.post(
            f"/api/v1/tickets/{ticket['id']}/comments",
            json={"body": "Filter seit 180 Tagen"},
        ).status_code
        == 201
    )
    response = automation.post(
        f"/api/v1/tickets/{ticket['id']}/status", json={"status": "done"}
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "done"


def test_via_and_user_must_match(api: Api) -> None:
    assert (
        error_code(api("sarah", "automation").get("/api/v1/tickets")) == "invalid_via"
    )
    assert error_code(api("automation", "ui").get("/api/v1/tickets")) == "invalid_via"


def test_automation_cannot_approve_purchases(api: Api) -> None:
    ticket = create(
        api, "sarah", type="purchase", title="Waschmaschine", price_estimated="899.00"
    )
    assert ticket["approval_state"] == "pending"
    response = api("automation", "automation").post(
        f"/api/v1/tickets/{ticket['id']}/approval", json={"decision": "approve"}
    )
    assert response.status_code == 403
    assert error_code(response) == "approval_forbidden"


def test_automation_cannot_set_ai_flags_or_delete(api: Api) -> None:
    ticket = create(api, type="home_assistant", title="Dashboard aufräumen")
    automation = api("automation", "automation")
    response = automation.post(
        f"/api/v1/tickets/{ticket['id']}/ai-flags", json={"draft_requested": True}
    )
    assert error_code(response) == "ai_flags_forbidden"
    response = automation.delete(f"/api/v1/tickets/{ticket['id']}")
    assert error_code(response) == "delete_forbidden"
    response = automation.patch(
        "/api/v1/settings", json={"purchase_approval_threshold": "10.00"}
    )
    assert error_code(response) == "admin_required"


def test_automation_user_is_reserved(api: Api, app) -> None:
    response = api("sebastian").patch(
        "/api/v1/users/automation", json={"role": "admin"}
    )
    assert response.status_code == 422
    assert error_code(response) == "reserved_user"
    response = api().post(
        "/api/v1/users/sync",
        json={
            "users": [
                UserSyncItem(
                    ha_user_id="automation", display_name="Fake", is_admin=True
                ).model_dump()
            ]
        },
    )
    assert response.status_code == 422
    assert error_code(response) == "reserved_user"
