"""Attachments stored in the LXC."""

import hashlib
from pathlib import Path

from .conftest import Api, create, error_code

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 100


def upload(api: Api, name: str, ticket_id: int = 1, data: bytes = PNG, **kwargs):
    content_type = kwargs.pop("content_type", "image/png")
    return api(name).post(
        f"/api/v1/tickets/{ticket_id}/attachments",
        files={"file": ("../hahn.png", data, content_type)},
        data=kwargs,
    )


def test_upload_download_delete(api: Api, app) -> None:
    create(api, "sarah")
    response = upload(api, "sarah")
    assert response.status_code == 201, response.text
    attachment = response.json()
    assert attachment["filename"] == "hahn.png"
    assert attachment["sha256"] == hashlib.sha256(PNG).hexdigest()
    assert attachment["kind"] == "photo"

    download = api("kind").get(f"/api/v1/attachments/{attachment['id']}")
    assert download.content == PNG
    assert download.headers["content-type"] == "image/png"

    listed = api("kind").get("/api/v1/tickets/1/attachments").json()
    assert [a["id"] for a in listed] == [attachment["id"]]

    path = Path(app.state.settings.attachments_dir) / "1" / attachment["id"]
    assert path.exists()
    response = api("sebastian", "mcp").delete(f"/api/v1/attachments/{attachment['id']}")
    assert error_code(response) == "ai_delete_forbidden"
    assert (
        api("sarah").delete(f"/api/v1/attachments/{attachment['id']}").status_code
        == 204
    )
    assert not path.exists()


def test_invoice_kind_and_limits(api: Api) -> None:
    create(api, "sarah", type="purchase", title="Waschmaschine")
    invoice = upload(api, "sarah", kind="invoice", content_type="application/pdf")
    assert invoice.json()["kind"] == "invoice"
    assert upload(api, "sarah", data=b"0" * 2048).status_code == 413
    assert upload(api, "sarah", content_type="text/html").status_code == 415


def test_who_may_upload_and_delete(api: Api) -> None:
    create(api, "sarah")
    assert error_code(upload(api, "kind")) == "not_own_ticket"
    attachment = upload(api, "sarah").json()
    response = api("kind").delete(f"/api/v1/attachments/{attachment['id']}")
    assert error_code(response) == "forbidden"
    assert (
        api("sebastian").delete(f"/api/v1/attachments/{attachment['id']}").status_code
        == 204
    )


def test_ticket_delete_removes_files(api: Api, app) -> None:
    create(api, "sarah")
    attachment = upload(api, "sarah").json()
    path = Path(app.state.settings.attachments_dir) / "1" / attachment["id"]
    assert api("sebastian").delete("/api/v1/tickets/1").status_code == 204
    assert not path.exists()
