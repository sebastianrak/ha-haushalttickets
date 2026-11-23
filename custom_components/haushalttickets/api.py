"""Client for the Haushalttickets server."""

from __future__ import annotations

from dataclasses import dataclass
from json import loads as json_loads
from typing import Any

import aiohttp

from .const import AUTOMATION_USER_ID, REQUEST_TIMEOUT


class HaushaltticketsError(Exception):
    """Base error."""


class CannotConnect(HaushaltticketsError):
    """Server not reachable or not answering like a Haushalttickets server."""


class InvalidAuth(HaushaltticketsError):
    """Token missing, wrong or revoked."""


class ApiError(HaushaltticketsError):
    """The server refused a request."""

    def __init__(self, status: int, code: str, detail: str) -> None:
        """Keep status, error code and message of the server."""
        super().__init__(f"{status} {code}: {detail}")
        self.status = status
        self.code = code
        self.detail = detail


@dataclass(frozen=True, slots=True)
class Acting:
    """On whose behalf and through which channel a request is made."""

    user_id: str
    via: str = "ui"


AUTOMATION = Acting(AUTOMATION_USER_ID, "automation")

_PAGE = 500


class HaushaltticketsClient:
    """Minimal async client; the server enforces all permissions."""

    def __init__(self, session: aiohttp.ClientSession, url: str, token: str) -> None:
        """Store connection details."""
        self._session = session
        self._base = url.rstrip("/")
        self._token = token

    @property
    def url(self) -> str:
        """Base URL of the server."""
        return self._base

    async def request(
        self,
        method: str,
        path: str,
        *,
        acting: Acting | None = None,
        params: dict[str, Any] | None = None,
        json: Any = None,
        auth: bool = True,
    ) -> Any:
        """Send a request and return the decoded JSON body (or None)."""
        headers = {}
        if auth:
            headers["Authorization"] = f"Bearer {self._token}"
        if acting is not None:
            headers["X-Acting-User"] = acting.user_id
            headers["X-Acting-Via"] = acting.via
        try:
            async with self._session.request(
                method,
                f"{self._base}{path}",
                headers=headers,
                params=_query(params),
                json=json,
                timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT),
            ) as response:
                if response.status == 401:
                    raise InvalidAuth
                raw = await response.read()
                try:
                    body = json_loads(raw) if raw else None
                except ValueError as err:
                    raise CannotConnect(f"No JSON from {path}") from err
                if response.status >= 400:
                    raise _api_error(response.status, body)
                return body
        except (aiohttp.ClientError, TimeoutError) as err:
            raise CannotConnect(str(err) or type(err).__name__) from err

    async def download(
        self, acting: Acting, path: str
    ) -> tuple[bytes, str, str | None]:
        """A file: content, content type and Content-Disposition."""
        try:
            async with self._session.get(
                f"{self._base}{path}",
                headers=self._headers(acting),
                timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT * 3),
            ) as response:
                if response.status == 401:
                    raise InvalidAuth
                body = await response.read()
                if response.status >= 400:
                    raise _api_error(response.status, _json_or_none(body))
                return (
                    body,
                    response.headers.get("Content-Type", "application/octet-stream"),
                    response.headers.get("Content-Disposition"),
                )
        except (aiohttp.ClientError, TimeoutError) as err:
            raise CannotConnect(str(err) or type(err).__name__) from err

    async def upload(
        self,
        acting: Acting,
        ticket_id: int,
        *,
        filename: str,
        content_type: str,
        data: bytes,
        kind: str,
    ) -> dict[str, Any]:
        """Attach a file to a ticket."""
        form = aiohttp.FormData()
        form.add_field("kind", kind)
        form.add_field("file", data, filename=filename, content_type=content_type)
        try:
            async with self._session.post(
                f"{self._base}/api/v1/tickets/{ticket_id}/attachments",
                headers=self._headers(acting),
                data=form,
                timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT * 6),
            ) as response:
                if response.status == 401:
                    raise InvalidAuth
                body = _json_or_none(await response.read())
                if response.status >= 400:
                    raise _api_error(response.status, body)
                return body
        except (aiohttp.ClientError, TimeoutError) as err:
            raise CannotConnect(str(err) or type(err).__name__) from err

    def _headers(self, acting: Acting | None) -> dict[str, str]:
        headers = {"Authorization": f"Bearer {self._token}"}
        if acting is not None:
            headers["X-Acting-User"] = acting.user_id
            headers["X-Acting-Via"] = acting.via
        return headers

    async def health(self) -> dict[str, Any]:
        """Health check; needs no token."""
        body = await self.request("GET", "/api/health", auth=False)
        if not isinstance(body, dict) or body.get("status") != "ok":
            raise CannotConnect("Unexpected health response")
        return body

    async def users(self) -> list[dict[str, Any]]:
        """All users known to the server."""
        return await self.request("GET", "/api/v1/users")

    async def sync_users(self, users: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Create or update users from Home Assistant."""
        return await self.request("POST", "/api/v1/users/sync", json={"users": users})

    async def set_role(self, acting: Acting, user_id: str, role: str) -> dict[str, Any]:
        """Change the role of a user (admins)."""
        return await self.request(
            "PATCH", f"/api/v1/users/{user_id}", acting=acting, json={"role": role}
        )

    async def events(self, after: int, limit: int = 1000) -> dict[str, Any]:
        """Event feed after a sequence number."""
        return await self.request(
            "GET", "/api/v1/events", params={"after": after, "limit": limit}
        )

    async def open_tickets(self) -> list[dict[str, Any]]:
        """All open tickets."""
        tickets: list[dict[str, Any]] = []
        while True:
            page = await self.request(
                "GET",
                "/api/v1/tickets",
                params={
                    "include_closed": False,
                    "limit": _PAGE,
                    "offset": len(tickets),
                },
            )
            tickets.extend(page["tickets"])
            if not page["tickets"] or len(tickets) >= page["count"]:
                return tickets

    async def ticket(self, ticket_id: int) -> dict[str, Any]:
        """One ticket."""
        return await self.request("GET", f"/api/v1/tickets/{ticket_id}")

    async def create_ticket(
        self, acting: Acting, body: dict[str, Any]
    ) -> dict[str, Any]:
        """Create a ticket."""
        return await self.request("POST", "/api/v1/tickets", acting=acting, json=body)

    async def set_status(
        self, acting: Acting, ticket_id: int, status: str
    ) -> dict[str, Any]:
        """Change the status of a ticket."""
        return await self.request(
            "POST",
            f"/api/v1/tickets/{ticket_id}/status",
            acting=acting,
            json={"status": status},
        )

    async def add_comment(
        self, acting: Acting, ticket_id: int, body: str
    ) -> dict[str, Any]:
        """Comment on a ticket."""
        return await self.request(
            "POST",
            f"/api/v1/tickets/{ticket_id}/comments",
            acting=acting,
            json={"body": body},
        )

    async def set_assignees(
        self, acting: Acting, ticket_id: int, user_ids: list[str]
    ) -> dict[str, Any]:
        """Replace the assignees of a ticket."""
        return await self.request(
            "PUT",
            f"/api/v1/tickets/{ticket_id}/assignees",
            acting=acting,
            json={"user_ids": user_ids},
        )


def _query(params: dict[str, Any] | None) -> list[tuple[str, str]] | None:
    """Query pairs; lists repeat the key (``status=new&status=acceptance``)."""
    if params is None:
        return None
    pairs = []
    for key, value in params.items():
        for item in value if isinstance(value, (list, tuple)) else [value]:
            if item is None:
                continue
            pairs.append(
                (
                    key,
                    ("true" if item else "false")
                    if isinstance(item, bool)
                    else str(item),
                )
            )
    return pairs


def _json_or_none(raw: bytes) -> Any:
    try:
        return json_loads(raw) if raw else None
    except ValueError:
        return None


def _api_error(status: int, body: Any) -> ApiError:
    detail = body.get("detail") if isinstance(body, dict) else None
    if isinstance(detail, dict):
        return ApiError(status, str(detail.get("code")), str(detail.get("detail")))
    if isinstance(detail, list):  # request validation by FastAPI
        messages = "; ".join(
            f"{'.'.join(str(part) for part in item.get('loc', [])[1:])}: {item.get('msg')}"
            for item in detail
            if isinstance(item, dict)
        )
        return ApiError(status, "validation_error", messages)
    return ApiError(status, "error", str(detail or body))
