"""Client of the ticket server's nightly API (runner token, standard library only)."""

from __future__ import annotations

import json
from typing import Any
import urllib.error
import urllib.request


class TicketsError(Exception):
    """The ticket server refused a request or could not be reached."""

    def __init__(self, status: int, code: str, detail: str) -> None:
        """Initialize."""
        super().__init__(f"{status} {code}: {detail}")
        self.status = status
        self.code = code
        self.detail = detail


class Tickets:
    """Calls ``/api/v1`` with the runner token."""

    def __init__(self, url: str, token: str, timeout: float = 30) -> None:
        """Initialize."""
        self.url = url.rstrip("/")
        self.token = token
        self.timeout = timeout

    def _request(self, method: str, path: str, body: Any = None) -> Any:
        data = None if body is None else json.dumps(body).encode()
        request = urllib.request.Request(
            self.url + path,
            data=data,
            method=method,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read()
        except urllib.error.HTTPError as err:
            try:
                detail = json.loads(err.read() or b"{}").get("detail") or {}
            except ValueError:
                detail = {}
            if isinstance(detail, dict):
                raise TicketsError(
                    err.code, detail.get("code", "error"), detail.get("detail", "")
                ) from None
            raise TicketsError(err.code, "error", str(detail)) from None
        except (urllib.error.URLError, TimeoutError, OSError) as err:
            raise TicketsError(0, "unreachable", str(err)) from None
        return json.loads(raw) if raw else None

    def work(self) -> list[dict[str, Any]]:
        """Approved implementations first, then requested drafts."""
        return self._request("GET", "/api/v1/nightly/work")["items"]

    def users(self) -> dict[str, str]:
        """Household member id -> display name."""
        return {
            user["ha_user_id"]: user["display_name"]
            for user in self._request("GET", "/api/v1/users")
        }

    def start_run(self) -> int:
        """Log the start of a run."""
        return self._request("POST", "/api/v1/nightly/runs")["id"]

    def end_run(
        self, run_id: int, outcome: str, notes: str, usage: dict[str, Any]
    ) -> None:
        """Log the end of a run; the server announces the results."""
        self._request(
            "PATCH",
            f"/api/v1/nightly/runs/{run_id}",
            {"outcome": outcome, "notes": notes[:20_000], "usage": usage},
        )

    def result(self, ticket_id: int, body: dict[str, Any]) -> dict[str, Any]:
        """Store a draft, a change or a failure for one ticket."""
        return self._request(
            "POST", f"/api/v1/nightly/tickets/{ticket_id}/result", body
        )
