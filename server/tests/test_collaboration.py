"""AI flags, assignees, comments and checklists."""

from .conftest import Api, create, error_code, uid


def flags(api: Api, name: str, via: str = "ui", ticket_id: int = 1, **body):
    return api(name, via).post(f"/api/v1/tickets/{ticket_id}/ai-flags", json=body)


def test_ai_flags(api: Api) -> None:
    create(api, "robin")
    create(api, "robin", type="home_assistant", title="Automation Kamin")
    assert flags(api, "robin", draft_requested=True).json()["ai_draft_requested"]
    response = flags(api, "robin", implement_approved=True)
    assert error_code(response) == "implement_not_applicable"
    # Approving needs a draft to approve (decision 50; see test_nightly.py).
    response = flags(api, "robin", ticket_id=2, implement_approved=True)
    assert error_code(response) == "no_draft"
    for name, via in (("alex", "mcp"), ("kind", "ui"), ("ki", "ui")):
        response = flags(api, name, via, ticket_id=2, implement_approved=True)
        assert error_code(response) == "ai_flags_forbidden"


def test_assignees(api: Api) -> None:
    create(api, "robin")
    url = "/api/v1/tickets/1/assignees"
    body = {"user_ids": [uid("alex")]}
    assert error_code(api("kind").put(url, json=body)) == "assign_forbidden"
    assert error_code(api("robin", "mcp").put(url, json=body)) == "assign_forbidden"
    assert api("robin").put(url, json=body).json()["assignee_ids"] == [uid("alex")]
    response = api("robin").put(url, json={"user_ids": ["nobody"]})
    assert error_code(response) == "unknown_user"
    event = api().get("/api/v1/events").json()["events"][-1]
    assert event["type"] == "assignees_changed"
    assert event["payload"]["added"] == [uid("alex")]


def test_comments(api: Api) -> None:
    create(api, "robin")
    url = "/api/v1/tickets/1/comments"
    assert api("kind").post(url, json={"body": "Tropft immer noch"}).status_code == 201
    response = api("alex", "mcp").post(url, json={"body": "Dichtung prüfen"})
    assert response.json()["via"] == "mcp"
    comments = api("robin").get(url).json()
    assert [c["author_id"] for c in comments] == [uid("kind"), uid("alex")]
    assert api("robin").post(url, json={"body": ""}).status_code == 422


def test_checklist(api: Api) -> None:
    create(api, "kind")
    url = "/api/v1/tickets/1/checklist"
    first = api("robin").post(url, json={"text": "Dichtung kaufen"}).json()
    second = api("kind").post(url, json={"text": "Wasser abdrehen"}).json()
    assert (first["position"], second["position"]) == (0, 1)
    done = api("robin").patch(f"{url}/{first['id']}", json={"done": True}).json()
    assert done["done"] is True
    assert done["done_by_id"] == uid("robin")
    response = api("alex", "mcp").patch(f"{url}/{first['id']}", json={"done": False})
    assert error_code(response) == "ai_checklist_forbidden"
    assert api("robin").delete(f"{url}/{second['id']}").status_code == 204
    ticket = api("robin").get("/api/v1/tickets/1").json()
    assert [item["text"] for item in ticket["checklist"]] == ["Dichtung kaufen"]
    assert api("robin").patch(f"{url}/999", json={"done": True}).status_code == 404

    create(api, "robin")
    response = api("kind").post("/api/v1/tickets/2/checklist", json={"text": "x"})
    assert error_code(response) == "checklist_forbidden"
