"""Creating, editing, listing and deleting tickets."""

from .conftest import Api, create, error_code, uid


def test_create_minimal(api: Api) -> None:
    ticket = create(api, "sarah")
    assert ticket["id"] == 1
    assert ticket["status"] == "new"
    assert ticket["urgency"] == "someday"
    assert ticket["requested_by_id"] == uid("sarah")
    assert ticket["created_via"] == "ui"
    history = api("sarah").get("/api/v1/tickets/1/history").json()
    assert history[0]["field"] == "created"
    events = api().get("/api/v1/events").json()["events"]
    assert [e["type"] for e in events] == ["ticket_created"]


def test_numbers_are_global(api: Api) -> None:
    assert create(api, type="repair")["id"] == 1
    assert create(api, type="purchase", title="Akkuschrauber")["id"] == 2
    assert create(api, type="home_assistant", title="Kamin-Automation")["id"] == 3


def test_type_specific_fields(api: Api) -> None:
    response = api("sebastian").post(
        "/api/v1/tickets",
        json={"type": "repair", "title": "x", "price_estimated": "10"},
    )
    assert response.status_code == 422
    assert error_code(response) == "field_not_for_type"
    ticket = create(
        api,
        type="home_assistant",
        title="Licht Flur",
        ha_targets=["light.flur_eg"],
        ha_area_id="flur_eg",
    )
    assert ticket["ha_targets"] == ["light.flur_eg"]
    repair = create(
        api,
        contractor_name="Sanitär Müller",
        contractor_phone="030 123",
        cost_estimated="80.50",
    )
    assert repair["cost_estimated"] == "80.50"


def test_related_purchase_must_be_purchase(api: Api) -> None:
    other = create(api)
    response = api("sebastian").post(
        "/api/v1/tickets",
        json={"type": "repair", "title": "x", "related_purchase_id": other["id"]},
    )
    assert error_code(response) == "not_a_purchase"
    purchase = create(api, type="purchase", title="Spülmaschine")
    ticket = create(api, related_purchase_id=purchase["id"], warranty_case=True)
    assert ticket["related_purchase_id"] == purchase["id"]


def test_filing_for_others_and_assigning(api: Api) -> None:
    ticket = create(
        api,
        "sarah",
        requested_by_id=uid("kind"),
        assignee_ids=[uid("sebastian"), uid("sarah")],
    )
    assert ticket["requested_by_id"] == uid("kind")
    assert sorted(ticket["assignee_ids"]) == sorted([uid("sebastian"), uid("sarah")])
    for name, via in (("kind", "ui"), ("sebastian", "mcp")):
        response = api(name, via).post(
            "/api/v1/tickets",
            json={"type": "repair", "title": "x", "assignee_ids": [uid("sarah")]},
        )
        assert error_code(response) == "assign_forbidden"
    response = api("kind").post(
        "/api/v1/tickets",
        json={"type": "repair", "title": "x", "requested_by_id": uid("sarah")},
    )
    assert error_code(response) == "requester_forbidden"
    response = api("kind").post(
        "/api/v1/tickets",
        json={"type": "repair", "title": "x", "contractor_name": "Firma"},
    )
    assert error_code(response) == "field_forbidden"
    assert create(api, "kind", description="im Bad")["description"] == "im Bad"


def test_ai_creates_for_the_logged_in_person(api: Api) -> None:
    ticket = create(api, "sebastian", via="mcp")
    assert ticket["requested_by_id"] == uid("sebastian")
    assert ticket["created_via"] == "mcp"


def test_edit_records_history(api: Api) -> None:
    create(api, "sarah")
    response = api("sarah").patch(
        "/api/v1/tickets/1",
        json={
            "title": "Wasserhahn tropft stark",
            "urgency": "burning",
            "importance": 4,
        },
    )
    assert response.status_code == 200
    assert response.json()["urgency"] == "burning"
    fields = [h["field"] for h in api("sarah").get("/api/v1/tickets/1/history").json()]
    assert {"title", "urgency", "importance"} <= set(fields)
    events = api().get("/api/v1/events").json()["events"]
    assert events[-1]["type"] == "ticket_updated"
    assert events[-1]["payload"]["fields"] == ["importance", "title", "urgency"]


def test_edit_rules(api: Api) -> None:
    create(api, "kind")
    create(api, "sarah")
    # The AI comments but does not edit.
    response = api("sebastian", via="mcp").patch(
        "/api/v1/tickets/1", json={"title": "x"}
    )
    assert error_code(response) == "ai_edit_forbidden"
    # Children edit basic fields of their own tickets only.
    ok = api("kind").patch("/api/v1/tickets/1", json={"description": "im Bad"})
    assert ok.status_code == 200
    response = api("kind").patch("/api/v1/tickets/2", json={"description": "x"})
    assert error_code(response) == "not_own_ticket"
    response = api("kind").patch("/api/v1/tickets/1", json={"contractor_name": "Firma"})
    assert error_code(response) == "field_forbidden"
    response = api("sarah").patch("/api/v1/tickets/1", json={"title": None})
    assert error_code(response) == "title_required"


def test_null_clears_fields(api: Api) -> None:
    create(api, importance=3, due_date="2026-12-01")
    response = api("sebastian").patch(
        "/api/v1/tickets/1", json={"importance": None, "due_date": None}
    )
    assert response.json()["importance"] is None
    assert response.json()["due_date"] is None


def test_list_filters(api: Api) -> None:
    create(api, "sarah", title="Wasserhahn", assignee_ids=[uid("sebastian")])
    create(api, "sarah", type="purchase", title="Bohrmaschine")
    create(api, "sebastian", title="Rollladen klemmt")
    api("sebastian").post("/api/v1/tickets/3/status", json={"status": "rejected"})

    def ids(**params) -> list[int]:
        body = api("kind").get("/api/v1/tickets", params=params).json()
        return [t["id"] for t in body["tickets"]]

    assert ids() == [3, 2, 1]
    assert ids(include_closed="false") == [2, 1]
    assert ids(type="purchase") == [2]
    assert ids(assignee=uid("sebastian")) == [1]
    assert ids(requested_by=uid("sarah")) == [2, 1]
    assert ids(q="bohr") == [2]
    assert ids(status=["new", "rejected"]) == [3, 2, 1]


def test_delete(api: Api) -> None:
    create(api)
    assert error_code(api("sarah").delete("/api/v1/tickets/1")) == "delete_forbidden"
    response = api("sebastian", via="mcp").delete("/api/v1/tickets/1")
    assert error_code(response) == "delete_forbidden"
    assert api("sebastian").delete("/api/v1/tickets/1").status_code == 204
    assert api("sebastian").get("/api/v1/tickets/1").status_code == 404
    events = api().get("/api/v1/events").json()["events"]
    assert events[-1]["type"] == "ticket_deleted"
