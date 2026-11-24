"""Who hears about what: notifications for household members.

The integration decides recipients, text and buttons and fires one
``haushalttickets_notification`` event per person. A Home Assistant automation
delivers them (device per person, Telegram, quiet hours). Buttons in the mobile
app come back as ``mobile_app_notification_action`` events; they act for the
person who tapped, so the server rules apply to that person.

Nobody is told about their own action.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, timedelta
import logging
import re
from typing import TYPE_CHECKING, Any

from homeassistant.core import Event, HomeAssistant

from .api import Acting, ApiError, CannotConnect, InvalidAuth
from .const import ACTION_PREFIX, AUTOMATION_USER_ID, EVENT_NOTIFICATION

if TYPE_CHECKING:
    from .coordinator import HaushaltticketsCoordinator, TicketData

_LOGGER = logging.getLogger(__name__)

ADULT_ROLES = ("admin", "adult")
_AI_VIAS = ("mcp", "nightly_run")
_ACTION = re.compile(rf"^{ACTION_PREFIX}(APPROVE|DECLINE|DONE)_(\d+)$")
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
        "action_failed": "Nicht ausgeführt",
        "by": "von {name}",
        "via_ai": "{name} über KI",
        "also_assigned": "Dir zugewiesen.",
        "estimated": "geschätzt {price} €",
        "overdue": "Überfällig",
        "today": "Heute",
        "tomorrow": "Morgen",
        "button_approve": "Freigeben",
        "button_decline": "Ablehnen",
        "button_done": "Abnehmen",
        "unreachable": "Haushalttickets ist nicht erreichbar.",
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
        "action_failed": "Not done",
        "by": "by {name}",
        "via_ai": "{name} via AI",
        "also_assigned": "Assigned to you.",
        "estimated": "estimated €{price}",
        "overdue": "Overdue",
        "today": "Today",
        "tomorrow": "Tomorrow",
        "button_approve": "Approve",
        "button_decline": "Decline",
        "button_done": "Accept",
        "unreachable": "Haushalttickets cannot be reached.",
        "automation": "Automation",
    },
}

# Server error codes a button can run into, in words.
ERRORS: dict[str, dict[str, str]] = {
    "de": {
        "approval_by_other_adult": "Freigeben muss die andere erwachsene Person.",
        "approval_forbidden": "Freigeben dürfen nur Erwachsene.",
        "approval_not_pending": "Die Freigabe ist schon entschieden.",
        "acceptance_by_requester": "Abnehmen darf nur, wer das Ticket gewünscht hat.",
        "ticket_closed": "Das Ticket ist schon geschlossen.",
        "not_found": "Das Ticket gibt es nicht mehr.",
    },
    "en": {},
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
    actions: tuple[dict[str, Any], ...] = field(default=())

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
            "actions": [dict(action) for action in self.actions],
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
        actions: tuple[dict[str, Any], ...] = (),
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
                actions=actions,
            )


def _name(data: TicketData, texts: dict[str, str], user_id: str | None) -> str:
    if user_id == AUTOMATION_USER_ID:
        return texts["automation"]
    user = data.users.get(user_id or "")
    return (user or {}).get("display_name") or "?"


def _button(
    texts: dict[str, str], verb: str, ticket_id: int, **extra: Any
) -> dict[str, Any]:
    return {
        "action": f"{ACTION_PREFIX}{verb}_{ticket_id}",
        "title": texts[f"button_{verb.lower()}"],
        # The iPhone must be unlocked before the server is asked.
        "authenticationRequired": True,
        **extra,
    }


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
            actions=(
                _button(texts, "APPROVE", ticket_id),
                _button(texts, "DECLINE", ticket_id, destructive=True),
            ),
        )
    elif kind == "approval_decided":
        decision = payload.get("decision")
        if decision in ("approved", "declined"):
            build.add([requester], decision, line, ticket_id)
    elif kind == "status_changed":
        new = payload.get("new")
        if new == "acceptance":
            build.add(
                [requester],
                "acceptance",
                line,
                ticket_id,
                actions=(_button(texts, "DONE", ticket_id),),
            )
        elif new in ("done", "rejected"):
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


async def async_handle_action(
    hass: HomeAssistant, coordinator: HaushaltticketsCoordinator, event: Event
) -> None:
    """A button in a mobile app notification: act for the person who tapped it."""
    match = _ACTION.match(str(event.data.get("action", "")))
    if match is None:
        return
    verb, ticket_id = match.group(1), int(match.group(2))
    user_id = event.context.user_id
    user = await hass.auth.async_get_user(user_id) if user_id else None
    if user is None or user.system_generated:
        _LOGGER.debug("Ignoring %s without a person", event.data.get("action"))
        return

    language = hass.config.language
    acting = Acting(user.id, "ui")
    client = coordinator.client
    try:
        if verb == "DONE":
            await client.set_status(acting, ticket_id, "done")
        else:
            await client.decide_approval(
                acting, ticket_id, "approve" if verb == "APPROVE" else "decline"
            )
    except ApiError as err:
        problem = ERRORS["de" if language.startswith("de") else "en"].get(
            err.code, err.detail
        )
        _failed(hass, coordinator, user.id, ticket_id, problem, language)
        return
    except (CannotConnect, InvalidAuth):
        _failed(
            hass,
            coordinator,
            user.id,
            ticket_id,
            _texts(language)["unreachable"],
            language,
        )
        return
    await coordinator.async_request_refresh()


def _failed(
    hass: HomeAssistant,
    coordinator: HaushaltticketsCoordinator,
    user_id: str,
    ticket_id: int,
    problem: str,
    language: str,
) -> None:
    person = coordinator.data.person_of(user_id) if coordinator.data else None
    if person is None:
        _LOGGER.warning("Button for ticket %s failed: %s", ticket_id, problem)
        return
    texts = _texts(language)
    fire(
        hass,
        [
            Notification(
                user_id=user_id,
                person=person,
                kind="action_failed",
                title=texts["action_failed"],
                message=f"#{ticket_id}: {problem}",
                ticket_id=ticket_id,
            )
        ],
    )
