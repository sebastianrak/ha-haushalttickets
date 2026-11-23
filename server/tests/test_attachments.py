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
    create(api, "robin")
    response = upload(api, "robin")
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
    response = api("alex", "mcp").delete(f"/api/v1/attachments/{attachment['id']}")
    assert error_code(response) == "ai_delete_forbidden"
    assert (
        api("robin").delete(f"/api/v1/attachments/{attachment['id']}").status_code
        == 204
    )
    assert not path.exists()


def test_invoice_kind_and_limits(api: Api) -> None:
    create(api, "robin", type="purchase", title="Waschmaschine")
    invoice = upload(api, "robin", kind="invoice", content_type="application/pdf")
    assert invoice.json()["kind"] == "invoice"
    assert upload(api, "robin", data=b"0" * 2048).status_code == 413
    assert upload(api, "robin", content_type="text/html").status_code == 415


def test_who_may_upload_and_delete(api: Api) -> None:
    create(api, "robin")
    assert error_code(upload(api, "kind")) == "not_own_ticket"
    attachment = upload(api, "robin").json()
    response = api("kind").delete(f"/api/v1/attachments/{attachment['id']}")
    assert error_code(response) == "forbidden"
    assert (
        api("alex").delete(f"/api/v1/attachments/{attachment['id']}").status_code == 204
    )


def test_ticket_delete_removes_files(api: Api, app) -> None:
    create(api, "robin")
    attachment = upload(api, "robin").json()
    path = Path(app.state.settings.attachments_dir) / "1" / attachment["id"]
    assert api("alex").delete("/api/v1/tickets/1").status_code == 204
    assert not path.exists()


def _jpeg(width: int = 1200, height: int = 800) -> bytes:
    from io import BytesIO

    from PIL import Image

    buffer = BytesIO()
    Image.new("RGB", (width, height), (200, 80, 40)).save(buffer, "JPEG")
    return buffer.getvalue()


def test_thumbnail_and_tile_counts(api: Api, app) -> None:
    app.state.settings.max_attachment_bytes = 5 * 1024 * 1024
    ticket = create(api, "robin")
    assert ticket["attachment_count"] == 0
    assert ticket["cover_attachment_id"] is None
    upload(
        api, "robin", data=b"%PDF-1.4", kind="document", content_type="application/pdf"
    )
    photo = upload(api, "robin", data=_jpeg(), content_type="image/jpeg").json()
    api("robin").post(f"/api/v1/tickets/{ticket['id']}/comments", json={"body": "Foto"})

    listed = api("alex").get("/api/v1/tickets").json()["tickets"][0]
    assert listed["attachment_count"] == 2
    assert listed["comment_count"] == 1
    assert listed["cover_attachment_id"] == photo["id"]

    response = api("alex").get(f"/api/v1/attachments/{photo['id']}/thumbnail")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/jpeg"
    from io import BytesIO

    from PIL import Image

    assert max(Image.open(BytesIO(response.content)).size) == 480


def test_no_thumbnail_for_documents_and_broken_images(api: Api, app) -> None:
    create(api, "robin")
    pdf = upload(
        api, "robin", data=b"%PDF-1.4", kind="document", content_type="application/pdf"
    ).json()
    broken = upload(api, "robin", data=b"not a jpeg", content_type="image/jpeg").json()
    for attachment in (pdf, broken):
        response = api("alex").get(f"/api/v1/attachments/{attachment['id']}/thumbnail")
        assert response.status_code == 404
        assert error_code(response) == "no_thumbnail"
