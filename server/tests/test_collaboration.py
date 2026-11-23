"""AI flags, assignees, comments and checklists."""

from .conftest import Api, create, error_code, uid


def flags(api: Api, name: str, via: str = "ui", ticket_id: int = 1, **body):
    return api(name, via).post(f"/api/v1/tickets/{ticket_id}/ai-flags", json=body)


def test_ai_flags(api: Api) -> None:
    create(api, "sarah")
    create(api, "sarah", type="home_assistant", title="Automation Kamin")
    assert flags(api, "sarah", draft_requested=True).json()["ai_draft_requested"]
    response = flags(api, "sarah", implement_approved=True)
    assert error_code(response) == "implement_not_applicable"
    approved = flags(api, "sarah", ticket_id=2, implement_approved=True).json()
    assert approved["ai_implement_approved"] is True
    assert approved["ai_implement_approved_by_id"] == uid("sarah")
    revoked = flags(api, "sarah", ticket_id=2, implement_approved=False).json()
    assert revoked["ai_implement_approved_by_id"] is None
    for name, via in (("sebastian", "mcp"), ("kind", "ui"), ("ki", "ui")):
        response = flags(api, name, via, ticket_id=2, implement_approved=True)
        assert error_code(response) == "ai_flags_forbidden"


def test_assignees(api: Api) -> None:
    create(api, "sarah")
    url = "/api/v1/tickets/1/assignees"
    body = {"user_ids": [uid("sebastian")]}
    assert error_code(api("kind").put(url, json=body)) == "assign_forbidden"
    assert error_code(api("sarah", "mcp").put(url, json=body)) == "assign_forbidden"
    assert api("sarah").put(url, json=body).json()["assignee_ids"] == [uid("sebastian")]
    response = api("sarah").put(url, json={"user_ids": ["nobody"]})
    assert error_code(response) == "unknown_user"
    event = api().get("/api/v1/events").json()["events"][-1]
    assert event["type"] == "assignees_changed"
    assert event["payload"]["added"] == [uid("sebastian")]


def test_comments(api: Api) -> None:
    create(api, "sarah")
    url = "/api/v1/tickets/1/comments"
    assert api("kind").post(url, json={"body": "Tropft immer noch"}).status_code == 201
    response = api("sebastian", "mcp").post(url, json={"body": "Dichtung prüfen"})
    assert response.json()["via"] == "mcp"
    comments = api("sarah").get(url).json()
    assert [c["author_id"] for c in comments] == [uid("kind"), uid("sebastian")]
    assert api("sarah").post(url, json={"body": ""}).status_code == 422


def test_checklist(api: Api) -> None:
    create(api, "kind")
    url = "/api/v1/tickets/1/checklist"
    first = api("sarah").post(url, json={"text": "Dichtung kaufen"}).json()
    second = api("kind").post(url, json={"text": "Wasser abdrehen"}).json()
    assert (first["position"], second["position"]) == (0, 1)
    done = api("sarah").patch(f"{url}/{first['id']}", json={"done": True}).json()
    assert done["done"] is True
    assert done["done_by_id"] == uid("sarah")
    response = api("sebastian", "mcp").patch(
        f"{url}/{first['id']}", json={"done": False}
    )
    assert error_code(response) == "ai_checklist_forbidden"
    assert api("sarah").delete(f"{url}/{second['id']}").status_code == 204
    ticket = api("sarah").get("/api/v1/tickets/1").json()
    assert [item["text"] for item in ticket["checklist"]] == ["Dichtung kaufen"]
    assert api("sarah").patch(f"{url}/999", json={"done": True}).status_code == 404

    create(api, "sarah")
    response = api("kind").post("/api/v1/tickets/2/checklist", json={"text": "x"})
    assert error_code(response) == "checklist_forbidden"
