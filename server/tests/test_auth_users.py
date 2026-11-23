"""Authentication, acting users and user management."""

from fastapi.testclient import TestClient

from haushalttickets_server import services

from .conftest import Api, error_code, uid


def test_token_required(app, household) -> None:
    client = TestClient(app)
    assert client.get("/api/v1/tickets").status_code == 401
    response = client.get("/api/v1/tickets", headers={"Authorization": "Bearer wrong"})
    assert response.status_code == 401
    assert error_code(response) == "invalid_token"


def test_revoked_token(app, token, household) -> None:
    with app.state.sessionmaker() as session:
        services.revoke_token(session, "test")
        session.commit()
    response = TestClient(app).get(
        "/api/v1/tickets", headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 401


def test_unknown_acting_user(api: Api) -> None:
    response = api.client.get(
        "/api/v1/tickets",
        headers={"Authorization": f"Bearer {api.token}", "X-Acting-User": "nobody"},
    )
    assert response.status_code == 403
    assert error_code(response) == "unknown_user"


def test_invalid_via(api: Api) -> None:
    response = api("robin", via="telepathy").get("/api/v1/tickets")
    assert response.status_code == 400
    response = api("robin", via="system").get("/api/v1/tickets")
    assert response.status_code == 400


def test_system_reads_but_does_not_write(api: Api) -> None:
    assert api().get("/api/v1/tickets").status_code == 200
    response = api().post("/api/v1/tickets", json={"type": "repair", "title": "x"})
    assert response.status_code == 403
    assert error_code(response) == "acting_user_required"


def test_sync_defaults_and_keeps_roles(api: Api) -> None:
    response = api().post(
        "/api/v1/users/sync",
        json={
            "users": [
                {"ha_user_id": "u-new-admin", "display_name": "Neu", "is_admin": True},
                {"ha_user_id": "u-new", "display_name": "Gast"},
                {"ha_user_id": uid("robin"), "display_name": "Robin S."},
            ]
        },
    )
    assert response.status_code == 200
    roles = {u["ha_user_id"]: u for u in response.json()}
    assert roles["u-new-admin"]["role"] == "admin"
    assert roles["u-new"]["role"] == "child"
    assert roles[uid("robin")]["role"] == "adult"
    assert roles[uid("robin")]["display_name"] == "Robin S."


def test_sync_by_non_admin_is_refused(api: Api) -> None:
    response = api("robin").post("/api/v1/users/sync", json={"users": []})
    assert response.status_code == 403


def test_role_changes(api: Api) -> None:
    url = f"/api/v1/users/{uid('kind')}"
    assert api("robin").patch(url, json={"role": "adult"}).status_code == 403
    # Through MCP even an admin follows the AI rules.
    response = api("alex", via="mcp").patch(url, json={"role": "adult"})
    assert error_code(response) == "admin_required"
    response = api("alex").patch(url, json={"role": "adult"})
    assert response.status_code == 200
    assert response.json()["role"] == "adult"
    response = api("alex").patch(f"/api/v1/users/{uid('alex')}", json={"role": "adult"})
    assert error_code(response) == "own_admin_role"


def test_settings(api: Api) -> None:
    assert api("kind").get("/api/v1/settings").json() == {
        "purchase_approval_threshold": "250.00"
    }
    body = {"purchase_approval_threshold": "300"}
    assert api("robin").patch("/api/v1/settings", json=body).status_code == 403
    response = api("alex").patch("/api/v1/settings", json=body)
    assert response.json()["purchase_approval_threshold"] == "300"
