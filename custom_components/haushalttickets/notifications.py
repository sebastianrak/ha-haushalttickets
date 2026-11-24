"""Who hears about what: notifications for household members.

The integration decides recipients and text and fires one
``haushalttickets_notification`` event per person. A Home Assistant automation
delivers them (device per person, Telegram, quiet hours) and links them to the
ticket. There are no buttons: approving a purchase or accepting work needs a
look at the ticket, so that happens in the card.

Nobody is told about their own action.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, timedelta
from typing import TYPE_CHECKING, Any

from homeassistant.core import HomeAssistant

from .const import AUTOMATION_USER_ID, EVENT_NOTIFICATION

if TYPE_CHECKING:
    from .coordinator import TicketData

ADULT_ROLES = ("admin", "adult")
_AI_VIAS = ("mcp", "nightly_run")
_COMMENT_PREVIEW = 160
_DASH = "\N{EN DASH}"

TEXTS: dict[str, dict[str, str]] = {
    "de": {
        "burning": "🔥 Brennt",
        "assigned": "Dir zugewiesen",
        "approval_requested": "Freigabe nötig",
        "approved": "Freigegeben",
        "declined": "Freigabe abgelehnt",
        "acceptance": "Bitte abnehmen",
        "done": "Erledigt",
        "rejected": "Träum weiter",
        "comment": "Neuer Kommentar",
        "due": "Fällige Tickets",
        "by": "von {name}",
        "via_ai": "{name} über KI",
        "also_assigned": "Dir zugewiesen.",
        "estimated": "geschätzt {price} €",
        "overdue": "Überfällig",
        "today": "Heute",
        "tomorrow": "Morgen",
        "automation": "Automation",
    },
    "en": {
        "burning": "🔥 Urgent",
        "assigned": "Assigned to you",
        "approval_requested": "Approval needed",
        "approved": "Approved",
        "declined": "Approval declined",
        "acceptance": "Please review",
        "done": "Done",
        "rejected": "Dream on",
        "comment": "New comment",
        "due": "Tickets due",
        "by": "by {name}",
        "via_ai": "{name} via AI",
        "also_assigned": "Assigned to you.",
        "estimated": "estimated €{price}",
        "overdue": "Overdue",
        "today": "Today",
        "tomorrow": "Tomorrow",
        "automation": "Automation",
    },
}


def _texts(language: str) -> dict[str, str]:
    return TEXTS["de" if language.startswith("de") else "en"]


@dataclass(frozen=True)
class Notification:
    """One message for one household member."""

    user_id: str
    person: str
    kind: str
    title: str
    message: str
    ticket_id: int | None = None
    urgent: bool = False

    def as_event(self) -> dict[str, Any]:
        """Data of the ``haushalttickets_notification`` event."""
        return {
            "user_id": self.user_id,
            "person": self.person,
            "kind": self.kind,
            "title": self.title,
            "message": self.message,
            "ticket_id": self.ticket_id,
            "urgent": self.urgent,
        }


class _Builder:
    """Collects at most one notification per person for one event."""

    def __init__(self, data: TicketData, texts: dict[str, str], actor_id: str | None):
        self.data = data
        self.texts = texts
        self.actor_id = actor_id
        self.out: dict[str, Notification] = {}

    def recipient(self, user_id: str | None) -> str | None:
        """The person of an active member who did not cause the event."""
        if not user_id or user_id == self.actor_id:
            return None
        user = self.data.users.get(user_id)
        if not user or not user.get("active", True) or user.get("role") == "ai_agent":
            return None
        return user.get("ha_person_entity_id")

    def adults(self) -> list[str]:
        return [
            user_id
            for user_id, user in self.data.users.items()
            if user.get("role") in ADULT_ROLES
        ]

    def add(
        self,
        user_ids: Iterable[str | None],
        kind: str,
        message: str,
        ticket_id: int | None,
        *,
        urgent: bool = False,
    ) -> None:
        for user_id in user_ids:
            person = self.recipient(user_id)
            if person is None or user_id in self.out:
                continue
            assert user_id is not None
            self.out[user_id] = Notification(
                user_id=user_id,
                person=person,
                kind=kind,
                title=self.texts[kind],
                message=message,
                ticket_id=ticket_id,
                urgent=urgent,
            )


def _name(data: TicketData, texts: dict[str, str], user_id: str | None) -> str:
    if user_id == AUTOMATION_USER_ID:
        return texts["automation"]
    user = data.users.get(user_id or "")
    return (user or {}).get("display_name") or "?"


def notifications_for(
    event: dict[str, Any],
    data: TicketData,
    language: str,
    comment: str | None = None,
) -> list[Notification]:
    """Notifications for one ``haushalttickets_event``."""
    texts = _texts(language)
    ticket = event.get("ticket") or {}
    payload = event.get("payload") or {}
    ticket_id = event.get("ticket_id")
    actor_id = event.get("actor_id")
    title = ticket.get("title") or payload.get("title") or ""
    requester = ticket.get("requested_by_id") or payload.get("requested_by_id")
    assignees = ticket.get("assignee_ids") or payload.get("assignee_ids") or []
    burning = (ticket.get("urgency") or payload.get("urgency")) == "burning"

    actor = _name(data, texts, actor_id)
    if event.get("via") in _AI_VIAS:
        actor = texts["via_ai"].format(name=actor)
    line = f"#{ticket_id} {title} {_DASH} {texts['by'].format(name=actor)}"

    build = _Builder(data, texts, actor_id)
    kind = event.get("type")
    if kind == "ticket_created":
        if burning:
            for user_id in build.adults():
                assigned = user_id in assignees
                build.add(
                    [user_id],
                    "burning",
                    f"{line}\n{texts['also_assigned']}" if assigned else line,
                    ticket_id,
                    urgent=True,
                )
        build.add(assignees, "assigned", line, ticket_id, urgent=burning)
    elif kind == "assignees_changed":
        build.add(
            payload.get("added") or [], "assigned", line, ticket_id, urgent=burning
        )
    elif kind == "ticket_updated":
        if burning and "urgency" in (payload.get("fields") or []):
            build.add(build.adults(), "burning", line, ticket_id, urgent=True)
    elif kind == "approval_requested":
        price = payload.get("price_estimated")
        message = line
        if price is not None:
            shown = (
                str(price).replace(".", ",")
                if language.startswith("de")
                else str(price)
            )
            message = f"{line}\n{texts['estimated'].format(price=shown)}"
        build.add(
            [user_id for user_id in build.adults() if user_id != requester],
            "approval_requested",
            message,
            ticket_id,
        )
    elif kind == "approval_decided":
        decision = payload.get("decision")
        if decision in ("approved", "declined"):
            build.add([requester], decision, line, ticket_id)
    elif kind == "status_changed":
        new = payload.get("new")
        # Moves that follow an approval step are told by the approval message.
        if new in ("acceptance", "done", "rejected") and not payload.get("reason"):
            build.add([requester], new, line, ticket_id)
    elif kind == "comment_added":
        message = f"#{ticket_id} {title}\n{actor}"
        if comment:
            short = " ".join(comment.split())
            if len(short) > _COMMENT_PREVIEW:
                short = short[: _COMMENT_PREVIEW - 1] + "…"
            message = f"{message}: {short}"
        build.add([requester, *assignees], "comment", message, ticket_id)
    return list(build.out.values())


def due_notifications(
    tickets: list[dict[str, Any]], data: TicketData, today: date, language: str
) -> list[Notification]:
    """One summary per person: overdue, due today and due tomorrow."""
    texts = _texts(language)
    tomorrow = today + timedelta(days=1)
    lines: dict[str, list[tuple[date, str]]] = {}
    for ticket in tickets:
        if ticket.get("status") in ("done", "rejected") or not ticket.get("due_date"):
            continue
        due = date.fromisoformat(ticket["due_date"])
        if due > tomorrow:
            continue
        label = (
            texts["overdue"]
            if due < today
            else texts["today"]
            if due == today
            else texts["tomorrow"]
        )
        entry = f"{label}: #{ticket['id']} {ticket['title']}"
        if due < today:
            entry += f" ({due.strftime('%d.%m.') if language.startswith('de') else due.isoformat()})"
        for user_id in ticket.get("assignee_ids") or [ticket.get("requested_by_id")]:
            lines.setdefault(user_id, []).append((due, entry))

    build = _Builder(data, texts, actor_id=None)
    for user_id, items in lines.items():
        items.sort()
        build.add([user_id], "due", "\n".join(entry for _, entry in items), None)
    return list(build.out.values())


def fire(hass: HomeAssistant, notifications: Iterable[Notification]) -> None:
    """Hand the notifications to the delivering automation."""
    for notification in notifications:
        hass.bus.async_fire(EVENT_NOTIFICATION, notification.as_event())
