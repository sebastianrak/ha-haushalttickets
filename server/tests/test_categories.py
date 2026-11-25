"""Household settings: categories, board visibility, roles, approval threshold."""

from __future__ import annotations

from fastapi import FastAPI

from haushalttickets_server import recurrence

from .conftest import Api, create, error_code, uid


def new_category(api: Api, name: str = "Garten", color: str = "#4CAF50") -> dict:
    response = api("alex").post(
        "/api/v1/categories", json={"name": name, "color": color}
    )
    assert response.status_code == 201, response.text
    return response.json()


def settings_events(api: Api) -> list[dict]:
    feed = api().get("/api/v1/events").json()["events"]
    return [e for e in feed if e["type"] == "settings_changed"]


def history(api: Api, ticket_id: int) -> list[dict]:
    return api("alex").get(f"/api/v1/tickets/{ticket_id}/history").json()


def test_only_admins_manage_categories(api: Api) -> None:
    category = new_category(api)
    assert category["name"] == "Garten"
    assert category["color"] == "#4caf50"
    assert category["ticket_count"] == 0
    for name, via in (("robin", "ui"), ("kind", "ui"), ("ki", "mcp")):
        response = api(name, via).post(
            "/api/v1/categories", json={"name": "Auto", "color": "#2196f3"}
        )
        assert response.status_code == 403, name
        response = api(name, via).delete(f"/api/v1/categories/{category['id']}")
        assert response.status_code == 403, name
    assert [c["name"] for c in api("kind").get("/api/v1/categories").json()] == [
        "Garten"
    ]
    assert settings_events(api)[-1]["payload"] == {"what": "categories"}


def test_category_names_are_unique_and_colors_checked(api: Api) -> None:
    new_category(api, "Garten")
    response = api("alex").post(
        "/api/v1/categories", json={"name": "  garten ", "color": "#000000"}
    )
    assert response.status_code == 422
    assert error_code(response) == "category_exists"
    response = api("alex").post(
        "/api/v1/categories", json={"name": "Auto", "color": "blue"}
    )
    assert response.status_code == 422
    response = api("alex").post(
        "/api/v1/categories", json={"name": " ", "color": "#000000"}
    )
    assert error_code(response) == "name_required"


def test_ticket_with_category(api: Api) -> None:
    garten = new_category(api)
    ticket = create(api, "kind", title="Rasen mähen", category_id=garten["id"])
    assert ticket["category_id"] == garten["id"]
    assert ticket["category_name"] == "Garten"
    # The AI may set the category when it creates a ticket, like any field then.
    by_ai = create(api, "ki", via="mcp", title="Hecke", category_id=garten["id"])
    assert by_ai["category_name"] == "Garten"
    response = api("alex").post(
        "/api/v1/tickets", json={"type": "repair", "title": "X", "category_id": 999}
    )
    assert response.status_code == 422
    assert error_code(response) == "unknown_category"
    listed = api("alex").get("/api/v1/categories").json()
    assert listed[0]["ticket_count"] == 2


def test_change_category_records_names(api: Api) -> None:
    garten, auto = new_category(api), new_category(api, "Auto", "#2196f3")
    ticket = create(api, "kind", title="Rasen mähen", category_id=garten["id"])
    url = f"/api/v1/tickets/{ticket['id']}"
    changed = api("kind").patch(url, json={"category_id": auto["id"]})
    assert changed.status_code == 200, changed.text
    assert changed.json()["category_name"] == "Auto"
    cleared = api("kind").patch(url, json={"category_id": None}).json()
    assert cleared["category_id"] is None and cleared["category_name"] is None
    entries = [e for e in history(api, ticket["id"]) if e["field"] == "category"]
    assert [(e["old"], e["new"]) for e in entries] == [
        ("Garten", "Auto"),
        ("Auto", None),
    ]
    response = api("ki", "mcp").patch(url, json={"category_id": garten["id"]})
    assert error_code(response) == "ai_edit_forbidden"


def test_rename_and_delete_category(api: Api, app: FastAPI) -> None:
    garten = new_category(api)
    ticket = create(api, "robin", title="Rasen mähen", category_id=garten["id"])
    series = (
        api("robin")
        .post(
            "/api/v1/series",
            json={
                "from_ticket_id": ticket["id"],
                "mode": "after_completion",
                "interval_count": 1,
                "interval_unit": "weeks",
            },
        )
        .json()
    )
    assert series["category_id"] == garten["id"]

    renamed = api("alex").patch(
        f"/api/v1/categories/{garten['id']}", json={"name": "Garten & Hof"}
    )
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["ticket_count"] == 1
    url = f"/api/v1/tickets/{ticket['id']}"
    assert api("robin").get(url).json()["category_name"] == "Garten & Hof"

    response = api("alex").delete(f"/api/v1/categories/{garten['id']}")
    assert response.status_code == 204
    assert api("robin").get(url).json()["category_id"] is None
    assert api("alex").get("/api/v1/categories").json() == []
    [entry] = [e for e in history(api, ticket["id"]) if e["field"] == "category"]
    assert (entry["old"], entry["new"]) == ("Garten & Hof", None)
    [series] = api("robin").get("/api/v1/series").json()
    assert series["category_id"] is None


def test_rounds_inherit_the_category(api: Api) -> None:
    garten = new_category(api)
    today = recurrence.today()
    api("alex").post(
        "/api/v1/series",
        json={
            "type": "repair",
            "title": "Dachrinne",
            "first_due": str(today),
            "mode": "after_completion",
            "interval_count": 1,
            "interval_unit": "days",
            "category_id": garten["id"],
        },
    )
    [first] = api("alex").get("/api/v1/tickets").json()["tickets"]
    assert first["category_name"] == "Garten"
    api("alex").post(f"/api/v1/tickets/{first['id']}/status", json={"status": "done"})
    # The next round is within the lead time, so it follows right away.
    [second] = [
        t
        for t in api("alex").get("/api/v1/tickets").json()["tickets"]
        if t["status"] == "new"
    ]
    assert second["id"] != first["id"]
    assert second["category_name"] == "Garten"
    response = api("alex").post(
        "/api/v1/series",
        json={
            "type": "repair",
            "title": "X",
            "first_due": str(today),
            "mode": "after_completion",
            "interval_count": 1,
            "interval_unit": "days",
            "category_id": 999,
        },
    )
    assert error_code(response) == "unknown_category"


def test_board_visibility_and_roles(api: Api) -> None:
    url = f"/api/v1/users/{uid('ki')}"
    response = api("robin").patch(url, json={"board_visible": False})
    assert response.status_code == 403
    hidden = api("alex").patch(url, json={"board_visible": False})
    assert hidden.status_code == 200, hidden.text
    assert hidden.json()["board_visible"] is False
    users = {u["ha_user_id"]: u for u in api("robin").get("/api/v1/users").json()}
    assert users[uid("ki")]["board_visible"] is False
    assert users[uid("robin")]["board_visible"] is True

    promoted = api("alex").patch(
        f"/api/v1/users/{uid('kind')}", json={"role": "adult", "board_visible": True}
    )
    assert promoted.json()["role"] == "adult"
    assert [e["payload"] for e in settings_events(api)] == [
        {"what": "users"},
        {"what": "users"},
    ]
    response = api("alex").patch(
        "/api/v1/users/automation", json={"board_visible": True}
    )
    assert error_code(response) == "reserved_user"
    response = api("alex").patch(f"/api/v1/users/{uid('alex')}", json={"role": "adult"})
    assert error_code(response) == "own_admin_role"


def test_threshold_change_is_announced_once(api: Api) -> None:
    body = {"purchase_approval_threshold": "500.00"}
    assert api("robin").patch("/api/v1/settings", json=body).status_code == 403
    assert api("alex").patch("/api/v1/settings", json=body).status_code == 200
    assert api("alex").patch("/api/v1/settings", json=body).status_code == 200
    assert api("kind").get("/api/v1/settings").json() == {
        "purchase_approval_threshold": "500.00"
    }
    assert [e["payload"] for e in settings_events(api)] == [{"what": "approval"}]
