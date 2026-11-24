"""HTTP views: the card's photos and files go through Home Assistant.

The LXC is only reachable from Home Assistant, so the card fetches attachments here.
Every request acts for the logged-in Home Assistant user; the server decides what
that user may see or upload.
"""

from __future__ import annotations

from http import HTTPStatus
import re

from aiohttp import web
from homeassistant.components.http import KEY_HASS, HomeAssistantView
from homeassistant.exceptions import ServiceValidationError

from .api import Acting, ApiError, CannotConnect, InvalidAuth
from .services import get_coordinator

# Attachment ids are UUIDs.
_ID = re.compile(r"^[0-9a-fA-F-]{36}$")
# Home Assistant itself accepts requests up to 16 MiB; the server allows 20 MiB.
MAX_UPLOAD = 16 * 1024 * 1024
_KINDS = frozenset({"photo", "invoice", "document"})


def _error(status: int, code: str, message: str) -> web.Response:
    return web.json_response({"code": code, "message": message}, status=status)


def _acting(request: web.Request) -> Acting | None:
    user = request.get("hass_user")
    if user is None or user.system_generated:
        return None
    return Acting(user.id, "ui")


class AttachmentView(HomeAssistantView):
    """Original file or JPEG preview of an attachment."""

    url = "/api/haushalttickets/attachments/{attachment_id}"
    extra_urls = ["/api/haushalttickets/attachments/{attachment_id}/thumbnail"]  # noqa: RUF012
    name = "api:haushalttickets:attachment"

    async def get(self, request: web.Request, attachment_id: str) -> web.StreamResponse:
        """Stream the file from the server."""
        if not _ID.match(attachment_id):
            return _error(HTTPStatus.NOT_FOUND, "not_found", "Unknown attachment.")
        acting = _acting(request)
        if acting is None:
            return _error(HTTPStatus.FORBIDDEN, "unauthorized", "No household member.")
        try:
            coordinator = get_coordinator(request.app[KEY_HASS])
        except ServiceValidationError:
            return _error(HTTPStatus.SERVICE_UNAVAILABLE, "not_loaded", "Not loaded.")
        thumbnail = request.path.endswith("/thumbnail")
        path = f"/api/v1/attachments/{attachment_id}" + (
            "/thumbnail" if thumbnail else ""
        )
        try:
            body, content_type, disposition = await coordinator.client.download(
                acting, path
            )
        except ApiError as err:
            return _error(err.status, err.code, err.detail)
        except (CannotConnect, InvalidAuth) as err:
            return _error(HTTPStatus.BAD_GATEWAY, "cannot_connect", str(err))
        headers = {
            # Attachments never change; previews are regenerated only when missing.
            "Cache-Control": "private, max-age=604800, immutable",
            "X-Content-Type-Options": "nosniff",
        }
        if disposition and not thumbnail:
            headers["Content-Disposition"] = disposition.replace(
                "attachment;", "inline;", 1
            )
        return web.Response(
            body=body, content_type=content_type.split(";")[0], headers=headers
        )


class UploadView(HomeAssistantView):
    """Attach a photo, invoice or document to a ticket."""

    url = "/api/haushalttickets/tickets/{ticket_id}/attachments"
    name = "api:haushalttickets:upload"

    async def post(self, request: web.Request, ticket_id: str) -> web.Response:
        """Forward one multipart file to the server."""
        if not ticket_id.isdigit():
            return _error(HTTPStatus.NOT_FOUND, "not_found", "Unknown ticket.")
        acting = _acting(request)
        if acting is None:
            return _error(HTTPStatus.FORBIDDEN, "unauthorized", "No household member.")
        try:
            coordinator = get_coordinator(request.app[KEY_HASS])
        except ServiceValidationError:
            return _error(HTTPStatus.SERVICE_UNAVAILABLE, "not_loaded", "Not loaded.")
        try:
            form = await request.post()
        except (ValueError, web.HTTPRequestEntityTooLarge):
            return _error(
                HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "too_large", "Too large."
            )
        upload = form.get("file")
        kind = str(form.get("kind") or "photo")
        if not isinstance(upload, web.FileField) or kind not in _KINDS:
            return _error(
                HTTPStatus.BAD_REQUEST, "invalid", "Send one file and a kind."
            )
        data = upload.file.read(MAX_UPLOAD + 1)
        if len(data) > MAX_UPLOAD:
            return _error(
                HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "too_large", "Too large."
            )
        try:
            attachment = await coordinator.client.upload(
                acting,
                int(ticket_id),
                filename=upload.filename or "file",
                content_type=upload.content_type or "application/octet-stream",
                data=data,
                kind=kind,
            )
        except ApiError as err:
            return _error(err.status, err.code, err.detail)
        except (CannotConnect, InvalidAuth) as err:
            return _error(HTTPStatus.BAD_GATEWAY, "cannot_connect", str(err))
        await coordinator.async_request_refresh()
        return self.json(attachment, status_code=HTTPStatus.CREATED)
