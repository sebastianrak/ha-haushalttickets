"""Event feed for the Home Assistant integration."""

from .conftest import Api, create


def test_feed_pages_by_sequence(api: Api) -> None:
    create(api, "sarah")
    create(api, "sarah")
    api("sarah").post("/api/v1/tickets/1/comments", json={"body": "Hallo"})
    feed = api().get("/api/v1/events").json()
    assert feed["last_seq"] == 3
    assert [e["type"] for e in feed["events"]] == [
        "ticket_created",
        "ticket_created",
        "comment_added",
    ]
    later = api().get("/api/v1/events", params={"after": 2}).json()
    assert [e["seq"] for e in later["events"]] == [3]
    assert later["events"][0]["actor_id"] == "u-sarah"
    page = api().get("/api/v1/events", params={"limit": 1}).json()
    assert len(page["events"]) == 1
