"""Photos and files through Home Assistant, and the bundled card."""

from __future__ import annotations

from io import BytesIO
from unittest.mock import patch

from aiohttp import FormData
from homeassistant.auth.models import User
from homeassistant.core import Context, HomeAssistant
from PIL import Image
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.haushalttickets.const import CARD_URL, DOMAIN

from .conftest import Household


def _jpeg() -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (1600, 900), (30, 120, 200)).save(buffer, "JPEG")
    return buffer.getvalue()


async def _http(hass: HomeAssistant, hass_client, user: User):
    refresh = await hass.auth.async_create_refresh_token(user, "https://example.test/")
    return await hass_client(hass.auth.async_create_access_token(refresh))


@pytest.fixture
async def ticket(hass: HomeAssistant, loaded: MockConfigEntry, household: Household):
    """A repair requested by Robin."""
    return await hass.services.async_call(
        DOMAIN,
        "create_ticket",
        {"type": "repair", "title": "Fliese gesprungen"},
        blocking=True,
        return_response=True,
        context=Context(user_id=household.robin.id),
    )


async def test_upload_preview_and_download(
    hass: HomeAssistant, ticket, household: Household, hass_client
) -> None:
    """Robin uploads a photo; Alex sees preview and original."""
    robin = await _http(hass, hass_client, household.robin)
    form = FormData()
    form.add_field("kind", "photo")
    form.add_field("file", _jpeg(), filename="fliese.jpg", content_type="image/jpeg")
    resp = await robin.post(
        f"/api/haushalttickets/tickets/{ticket['id']}/attachments", data=form
    )
    assert resp.status == 201, await resp.text()
    attachment = await resp.json()
    assert attachment["uploaded_by_id"] == household.robin.id

    alex = await _http(hass, hass_client, household.alex)
    thumb = await alex.get(
        f"/api/haushalttickets/attachments/{attachment['id']}/thumbnail"
    )
    assert thumb.status == 200
    assert thumb.content_type == "image/jpeg"
    assert max(Image.open(BytesIO(await thumb.read())).size) == 480

    original = await alex.get(f"/api/haushalttickets/attachments/{attachment['id']}")
    assert original.status == 200
    assert len(await original.read()) == len(_jpeg())
    assert original.headers["Content-Disposition"].startswith("inline")

    coordinator = hass.config_entries.async_entries(DOMAIN)[0].runtime_data
    await coordinator.async_refresh()
    tile = next(t for t in coordinator.data.tickets if t["id"] == ticket["id"])
    assert tile["cover_attachment_id"] == attachment["id"]


async def test_server_rules_and_errors(
    hass: HomeAssistant, ticket, household: Household, hass_client
) -> None:
    """Server refusals keep their code; bad ids and requests never reach the server."""
    kind = await _http(hass, hass_client, household.kind)
    form = FormData()
    form.add_field(
        "file", b"%PDF-1.4", filename="x.pdf", content_type="application/pdf"
    )
    form.add_field("kind", "document")
    resp = await kind.post(
        f"/api/haushalttickets/tickets/{ticket['id']}/attachments", data=form
    )
    assert resp.status == 403
    assert (await resp.json())["code"] == "not_own_ticket"

    alex = await _http(hass, hass_client, household.alex)
    resp = await alex.get("/api/haushalttickets/attachments/../../api/config")
    assert resp.status in (400, 404)
    resp = await alex.get("/api/haushalttickets/attachments/" + "0" * 36)
    assert resp.status == 404
    assert (await resp.json())["code"] == "not_found"
    resp = await alex.post(
        f"/api/haushalttickets/tickets/{ticket['id']}/attachments",
        data={"kind": "photo"},
    )
    assert resp.status == 400

    # Above Home Assistant's request limit: a JSON error the card can show
    form = FormData()
    form.add_field("kind", "document")
    form.add_field(
        "file",
        bytes(17 * 1024 * 1024),
        filename="big.pdf",
        content_type="application/pdf",
    )
    resp = await alex.post(
        f"/api/haushalttickets/tickets/{ticket['id']}/attachments", data=form
    )
    assert resp.status == 413
    assert (await resp.json())["code"] == "too_large"


async def test_requires_login(
    hass: HomeAssistant, loaded: MockConfigEntry, hass_client_no_auth
) -> None:
    """No anonymous access to files."""
    client = await hass_client_no_auth()
    resp = await client.get("/api/haushalttickets/attachments/" + "0" * 36)
    assert resp.status == 401


async def test_card_is_served(
    hass: HomeAssistant, entry: MockConfigEntry, household: Household, hass_client
) -> None:
    """The integration serves the card and registers it as extra module."""
    hass.config.components.add("frontend")
    with patch("custom_components.haushalttickets.add_extra_js_url") as add_js:
        entry.add_to_hass(hass)
        assert await hass.config_entries.async_setup(entry.entry_id)
    add_js.assert_called_once()
    assert add_js.call_args.args[1].startswith(CARD_URL + "?v=")
    client = await hass_client()
    resp = await client.get(CARD_URL)
    assert resp.status == 200
    assert "haushalttickets-card" in await resp.text()
    assert await hass.config_entries.async_unload(entry.entry_id)
