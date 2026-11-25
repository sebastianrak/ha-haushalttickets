"""Business logic. Every change is checked, recorded in the history and emitted as
an event in the same transaction."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
import hashlib
import logging
from pathlib import Path
import secrets
from typing import Any
import uuid

from PIL import Image, ImageOps
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import permissions as perm
from .enums import (
    AUTOMATION_USER_ID,
    CLOSED_STATUSES,
    ApprovalState,
    AttachmentKind,
    EventType,
    Role,
    Status,
    TicketType,
)
from .models import (
    AIDraft,
    ApiToken,
    Attachment,
    Category,
    ChecklistItem,
    Comment,
    Event,
    HistoryEntry,
    Series,
    Setting,
    Ticket,
    User,
)
from .permissions import Actor
from .schemas import (
    TYPE_FIELDS,
    CategoryCreate,
    CategoryPatch,
    TicketCreate,
    TicketFields,
    UserPatch,
    UserSyncItem,
)

_LOGGER = logging.getLogger(__name__)

DEFAULT_APPROVAL_THRESHOLD = Decimal("250.00")
SETTING_APPROVAL_THRESHOLD = "purchase_approval_threshold"


class NotFound(Exception):
    """Object does not exist."""


class Invalid(Exception):
    """Request is well-formed but not acceptable."""

    def __init__(self, code: str, message: str) -> None:
        """Initialize."""
        super().__init__(message)
        self.code = code
        self.message = message


def _now() -> datetime:
    return datetime.now(UTC)


def _json(value: Any) -> Any:
    """Make a value JSON-serializable for history and events."""
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, list):
        return [_json(item) for item in value]
    return value


def emit(
    session: Session,
    actor: Actor,
    event_type: EventType,
    ticket_id: int | None,
    payload: dict[str, Any] | None = None,
) -> None:
    """Append to the event feed."""
    session.add(
        Event(
            type=event_type,
            ticket_id=ticket_id,
            actor_id=actor.user_id,
            via=actor.via,
            payload={key: _json(value) for key, value in (payload or {}).items()},
        )
    )


def record(
    session: Session, actor: Actor, ticket: Ticket, field: str, old: Any, new: Any
) -> None:
    """Append to the ticket history."""
    session.add(
        HistoryEntry(
            ticket_id=ticket.id,
            actor_id=actor.user_id,
            via=actor.via,
            field=field,
            old=_json(old),
            new=_json(new),
        )
    )


# Settings


def get_approval_threshold(session: Session) -> Decimal:
    """Purchases at or above this estimated price need approval."""
    setting = session.get(Setting, SETTING_APPROVAL_THRESHOLD)
    return Decimal(setting.value) if setting else DEFAULT_APPROVAL_THRESHOLD


def set_approval_threshold(session: Session, actor: Actor, value: Decimal) -> None:
    """Change the approval threshold."""
    perm.check_admin(actor)
    setting = session.get(Setting, SETTING_APPROVAL_THRESHOLD)
    if setting is None:
        session.add(Setting(key=SETTING_APPROVAL_THRESHOLD, value=str(value)))
    elif Decimal(setting.value) == value:
        return
    else:
        setting.value = str(value)
    emit(session, actor, EventType.SETTINGS_CHANGED, None, {"what": "approval"})


# Users


def get_user(session: Session, user_id: str) -> User:
    """User by Home Assistant id."""
    user = session.get(User, user_id)
    if user is None:
        raise NotFound(f"User {user_id} not found")
    return user


def sync_users(session: Session, items: list[UserSyncItem]) -> list[User]:
    """Create or update users from Home Assistant.

    New users start as ``admin`` when they are Home Assistant administrators and as
    ``child`` (least privilege) otherwise; an admin assigns the right role.
    """
    users = []
    for item in items:
        if item.ha_user_id == AUTOMATION_USER_ID:
            raise Invalid("reserved_user", f"{AUTOMATION_USER_ID!r} is reserved.")
        user = session.get(User, item.ha_user_id)
        if user is None:
            user = User(
                ha_user_id=item.ha_user_id,
                role=Role.ADMIN if item.is_admin else Role.CHILD,
            )
            session.add(user)
        user.display_name = item.display_name
        user.ha_is_admin = item.is_admin
        user.active = item.is_active
        user.ha_person_entity_id = item.person_entity_id
        users.append(user)
    session.flush()
    return users


def set_role(session: Session, actor: Actor, user: User, role: Role) -> None:
    """Change the role of a user."""
    perm.check_admin(actor)
    if user.ha_user_id == AUTOMATION_USER_ID:
        raise Invalid("reserved_user", "The role of the user Automation is fixed.")
    if user.ha_user_id == actor.user_id and role != Role.ADMIN:
        raise Invalid("own_admin_role", "Admins cannot remove their own admin role.")
    if user.role != role:
        user.role = role
        emit(session, actor, EventType.SETTINGS_CHANGED, None, {"what": "users"})


def update_user(session: Session, actor: Actor, user: User, patch: UserPatch) -> None:
    """Role and board visibility of a user (admins)."""
    perm.check_admin(actor)
    sent = patch.model_fields_set
    if "role" in sent and patch.role is not None:
        set_role(session, actor, user, patch.role)
    if "board_visible" in sent and patch.board_visible is not None:
        if user.ha_user_id == AUTOMATION_USER_ID:
            raise Invalid("reserved_user", "The user Automation is never shown.")
        if user.board_visible != patch.board_visible:
            user.board_visible = patch.board_visible
            emit(session, actor, EventType.SETTINGS_CHANGED, None, {"what": "users"})


# Categories


def list_categories(session: Session) -> list[tuple[Category, int]]:
    """All categories by name, each with the number of tickets that use it."""
    counts = dict(
        session.execute(
            select(Ticket.category_id, func.count())
            .where(Ticket.category_id.is_not(None))
            .group_by(Ticket.category_id)
        ).all()
    )
    categories = session.scalars(select(Category).order_by(func.lower(Category.name)))
    return [(category, counts.get(category.id, 0)) for category in categories]


def get_category(session: Session, category_id: int) -> Category:
    """Category by id."""
    category = session.get(Category, category_id)
    if category is None:
        raise NotFound(f"Category {category_id} not found")
    return category


def check_category(session: Session, category_id: int | None) -> None:
    """A category a ticket or series may refer to (None clears it)."""
    if category_id is not None and session.get(Category, category_id) is None:
        raise Invalid("unknown_category", f"Unknown category {category_id}.")


def _category_name(session: Session, name: str, own_id: int | None = None) -> str:
    name = " ".join(name.split())
    if not name:
        raise Invalid("name_required", "A category needs a name.")
    taken = session.scalar(
        select(Category.id).where(
            func.lower(Category.name) == name.lower(), Category.id != (own_id or -1)
        )
    )
    if taken is not None:
        raise Invalid("category_exists", f"There is already a category {name!r}.")
    return name


def create_category(session: Session, actor: Actor, data: CategoryCreate) -> Category:
    """New category (admins)."""
    perm.check_admin(actor)
    category = Category(
        name=_category_name(session, data.name), color=data.color.lower()
    )
    session.add(category)
    session.flush()
    emit(session, actor, EventType.SETTINGS_CHANGED, None, {"what": "categories"})
    return category


def update_category(
    session: Session, actor: Actor, category: Category, patch: CategoryPatch
) -> Category:
    """Rename or recolor a category (admins); tickets keep it."""
    perm.check_admin(actor)
    sent = patch.model_fields_set
    if "name" in sent and patch.name is not None:
        category.name = _category_name(session, patch.name, category.id)
    if "color" in sent and patch.color is not None:
        category.color = patch.color.lower()
    emit(session, actor, EventType.SETTINGS_CHANGED, None, {"what": "categories"})
    return category


def delete_category(session: Session, actor: Actor, category: Category) -> int:
    """Remove a category (admins). Its tickets lose it; returns how many."""
    perm.check_admin(actor)
    tickets = session.scalars(
        select(Ticket).where(Ticket.category_id == category.id)
    ).all()
    for ticket in tickets:
        record(session, actor, ticket, "category", category.name, None)
        ticket.category = None
        ticket.updated_at = _now()
        emit(
            session,
            actor,
            EventType.TICKET_UPDATED,
            ticket.id,
            {"fields": ["category_id"]},
        )
    for series in session.scalars(
        select(Series).where(Series.category_id == category.id)
    ):
        series.category_id = None
    session.delete(category)
    emit(session, actor, EventType.SETTINGS_CHANGED, None, {"what": "categories"})
    return len(tickets)


# Tickets


def get_ticket(session: Session, ticket_id: int) -> Ticket:
    """Ticket by number."""
    ticket = session.get(Ticket, ticket_id)
    if ticket is None:
        raise NotFound(f"Ticket {ticket_id} not found")
    return ticket


def list_tickets(
    session: Session,
    *,
    status: list[str] | None = None,
    type_: str | None = None,
    assignee_id: str | None = None,
    requested_by_id: str | None = None,
    query: str | None = None,
    include_closed: bool = True,
    limit: int = 200,
    offset: int = 0,
) -> tuple[int, list[Ticket]]:
    """Filtered tickets, newest first."""
    stmt = select(Ticket)
    if status:
        stmt = stmt.where(Ticket.status.in_(status))
    elif not include_closed:
        stmt = stmt.where(Ticket.status.not_in(list(CLOSED_STATUSES)))
    if type_:
        stmt = stmt.where(Ticket.type == type_)
    if requested_by_id:
        stmt = stmt.where(Ticket.requested_by_id == requested_by_id)
    if assignee_id:
        stmt = stmt.where(Ticket.assignees.any(User.ha_user_id == assignee_id))
    if query:
        pattern = f"%{query}%"
        stmt = stmt.where(
            Ticket.title.ilike(pattern) | Ticket.description.ilike(pattern)
        )
    total = session.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = session.scalars(
        stmt.order_by(Ticket.id.desc()).limit(limit).offset(offset)
    ).all()
    return total, list(rows)


def _check_type_fields(ticket_type: TicketType, fields: set[str]) -> None:
    for field in fields:
        allowed = TYPE_FIELDS.get(field)
        if allowed is not None and ticket_type not in allowed:
            raise Invalid(
                "field_not_for_type", f"{field} does not apply to {ticket_type}."
            )


def _clean(field: str, value: Any) -> Any:
    if field == "product_url" and value is not None:
        return str(value)
    if field == "ha_targets" and value is None:
        return []
    if field == "warranty_case" and value is None:
        return False
    return value


def _users(session: Session, user_ids: list[str]) -> list[User]:
    users = []
    for user_id in dict.fromkeys(user_ids):
        user = session.get(User, user_id)
        if user is None or not user.active:
            raise Invalid("unknown_user", f"Unknown user {user_id}.")
        users.append(user)
    return users


def _check_related_purchase(session: Session, ticket_id: int | None) -> None:
    if ticket_id is None:
        return
    related = session.get(Ticket, ticket_id)
    if related is None or related.type != TicketType.PURCHASE:
        raise Invalid("not_a_purchase", "related_purchase_id must be a purchase.")


def _update_approval(session: Session, actor: Actor, ticket: Ticket) -> None:
    """Require approval when the estimated price reaches the threshold."""
    if ticket.type != TicketType.PURCHASE or ticket.approval_state in (
        ApprovalState.APPROVED,
        ApprovalState.DECLINED,
    ):
        return
    needed = (
        ticket.price_estimated is not None
        and ticket.price_estimated >= get_approval_threshold(session)
    )
    new_state = ApprovalState.PENDING if needed else ApprovalState.NOT_REQUIRED
    if new_state != ticket.approval_state:
        record(
            session, actor, ticket, "approval_state", ticket.approval_state, new_state
        )
        ticket.approval_state = new_state
        if new_state == ApprovalState.PENDING:
            emit(
                session,
                actor,
                EventType.APPROVAL_REQUESTED,
                ticket.id,
                {"price_estimated": ticket.price_estimated},
            )
            # A decision is due: the ticket is being discussed until then.
            if ticket.status not in CLOSED_STATUSES:
                _change_status(
                    session, actor, ticket, Status.DISCUSSING, "approval_requested"
                )


def create_ticket(session: Session, actor: Actor, data: TicketCreate) -> Ticket:
    """Create a ticket."""
    sent = data.model_fields_set - {"type", "requested_by_id", "assignee_ids"}
    perm.check_create(actor, data.requested_by_id, data.assignee_ids, sent)
    _check_type_fields(data.type, sent)
    requester = get_user_or_invalid(session, data.requested_by_id or actor.user_id)
    if "related_purchase_id" in sent:
        _check_related_purchase(session, data.related_purchase_id)
    if "category_id" in sent:
        check_category(session, data.category_id)

    ticket = Ticket(
        type=data.type,
        title=data.title,
        requested_by_id=requester.ha_user_id,
        created_by_id=actor.user_id,
        created_via=actor.via,
        status=Status.NEW,
        ha_targets=[],
    )
    for field in sent - {"title"}:
        value = _clean(field, getattr(data, field))
        if value is not None:
            setattr(ticket, field, value)
    ticket.assignees = _users(session, data.assignee_ids)
    session.add(ticket)
    session.flush()

    record(session, actor, ticket, "created", None, {"type": data.type})
    emit(
        session,
        actor,
        EventType.TICKET_CREATED,
        ticket.id,
        {
            "title": ticket.title,
            "type": ticket.type,
            "requested_by_id": ticket.requested_by_id,
            "assignee_ids": ticket.assignee_ids,
            "urgency": ticket.urgency,
        },
    )
    _update_approval(session, actor, ticket)
    return ticket


def get_user_or_invalid(session: Session, user_id: str | None) -> User:
    """Active user or a validation error."""
    user = session.get(User, user_id) if user_id else None
    if user is None or not user.active:
        raise Invalid("unknown_user", f"Unknown user {user_id}.")
    return user


def update_ticket(
    session: Session, actor: Actor, ticket: Ticket, patch: TicketFields
) -> Ticket:
    """Change fields of a ticket."""
    sent = set(patch.model_fields_set)
    if not sent:
        return ticket
    perm.check_edit(actor, ticket, sent)
    _check_type_fields(TicketType(ticket.type), sent)
    if "title" in sent and patch.title is None:
        raise Invalid("title_required", "A ticket needs a title.")
    if "related_purchase_id" in sent:
        _check_related_purchase(session, patch.related_purchase_id)
    if "category_id" in sent:
        check_category(session, patch.category_id)
    if "description" in sent and patch.description is None:
        patch.description = ""
    if "urgency" in sent and patch.urgency is None:
        raise Invalid("urgency_required", "Urgency cannot be empty.")

    changed = {}
    for field in sorted(sent):
        new = _clean(field, getattr(patch, field))
        old = getattr(ticket, field)
        if old != new:
            if field == "category_id":
                # Names, so the history stays readable after a rename or delete.
                category = session.get(Category, new) if new is not None else None
                record(
                    session,
                    actor,
                    ticket,
                    "category",
                    ticket.category_name,
                    category.name if category else None,
                )
                ticket.category = category
            else:
                record(session, actor, ticket, field, old, new)
                setattr(ticket, field, new)
            changed[field] = new
    if changed:
        ticket.updated_at = _now()
        emit(
            session,
            actor,
            EventType.TICKET_UPDATED,
            ticket.id,
            {"fields": sorted(changed)},
        )
        if "price_estimated" in changed:
            _update_approval(session, actor, ticket)
    return ticket


def set_status(
    session: Session, actor: Actor, ticket: Ticket, status: Status
) -> Ticket:
    """Move a ticket to another column."""
    override = perm.check_status(actor, ticket, status)
    _change_status(session, actor, ticket, status, override=override)
    return ticket


def _change_status(
    session: Session,
    actor: Actor,
    ticket: Ticket,
    status: Status,
    reason: str | None = None,
    *,
    override: bool = False,
) -> None:
    """Record and announce a status change.

    ``reason`` marks a move that follows from an approval step rather than from
    someone moving the ticket; it is not checked against the actor's rights.
    """
    old = ticket.status
    if old == status:
        return
    record(session, actor, ticket, "status", old, status)
    if override:
        record(session, actor, ticket, "admin_override", None, "accepted_for_requester")
    ticket.status = status
    ticket.closed_at = _now() if status in CLOSED_STATUSES else None
    ticket.updated_at = _now()
    payload = {
        "old": old,
        "new": status,
        "title": ticket.title,
        "requested_by_id": ticket.requested_by_id,
        "assignee_ids": ticket.assignee_ids,
        "admin_override": override,
    }
    if reason:
        payload["reason"] = reason
    emit(session, actor, EventType.STATUS_CHANGED, ticket.id, payload)
    if status in CLOSED_STATUSES and old not in CLOSED_STATUSES and ticket.series_id:
        from . import recurrence  # recurrence builds on this module

        recurrence.round_closed(session, ticket)


def set_assignees(
    session: Session, actor: Actor, ticket: Ticket, user_ids: list[str]
) -> Ticket:
    """Replace the assignees."""
    perm.check_assign(actor)
    old = ticket.assignee_ids
    ticket.assignees = _users(session, user_ids)
    new = ticket.assignee_ids
    if old != new:
        record(session, actor, ticket, "assignees", old, new)
        emit(
            session,
            actor,
            EventType.ASSIGNEES_CHANGED,
            ticket.id,
            {
                "old": old,
                "new": new,
                "added": [u for u in new if u not in old],
                "title": ticket.title,
            },
        )
    return ticket


def decide_approval(
    session: Session, actor: Actor, ticket: Ticket, approve: bool
) -> Ticket:
    """Approve or decline a purchase."""
    perm.check_approval(actor, ticket)
    new = ApprovalState.APPROVED if approve else ApprovalState.DECLINED
    record(session, actor, ticket, "approval_state", ticket.approval_state, new)
    ticket.approval_state = new
    ticket.approval_by_id = actor.user_id
    ticket.approval_at = _now()
    emit(
        session,
        actor,
        EventType.APPROVAL_DECIDED,
        ticket.id,
        {
            "decision": new,
            "title": ticket.title,
            "requested_by_id": ticket.requested_by_id,
        },
    )
    # The decision moves the ticket: approved → open (to do), declined → dream on.
    if approve:
        if ticket.status in (Status.DISCUSSING, Status.REJECTED):
            _change_status(session, actor, ticket, Status.NEW, "approval_approved")
    elif ticket.status not in CLOSED_STATUSES:
        _change_status(session, actor, ticket, Status.REJECTED, "approval_declined")
    return ticket


def latest_draft_version(session: Session, ticket: Ticket) -> int | None:
    """Newest AI draft version of a ticket."""
    return session.scalar(
        select(func.max(AIDraft.version)).where(AIDraft.ticket_id == ticket.id)
    )


def clear_implement_approval(
    session: Session, actor: Actor, ticket: Ticket, changed: dict[str, Any]
) -> None:
    """Withdraw the approval to implement (recorded)."""
    if not ticket.ai_implement_approved:
        return
    record(session, actor, ticket, "ai_implement_approved", True, False)
    ticket.ai_implement_approved = False
    ticket.ai_implement_approved_by_id = None
    ticket.ai_implement_approved_at = None
    ticket.ai_implement_draft_version = None
    changed["ai_implement_approved"] = False


def set_ai_flags(
    session: Session,
    actor: Actor,
    ticket: Ticket,
    draft: bool | None,
    implement: bool | None,
) -> Ticket:
    """Set "AI should draft" and "AI should implement".

    The approval to implement refers to the newest draft (decision 50): it needs a
    draft and no pending request for a new one, and asking for a new draft withdraws
    it.
    """
    perm.check_ai_flags(actor, ticket, draft, implement)
    changed: dict[str, Any] = {}
    draft_after = ticket.ai_draft_requested if draft is None else draft
    if implement and not ticket.ai_implement_approved:
        if draft_after:
            raise Invalid(
                "draft_pending", "A new draft is requested; approve it once it is in."
            )
        version = latest_draft_version(session, ticket)
        if version is None:
            raise Invalid("no_draft", "There is no draft to implement yet.")
    if draft is not None and draft != ticket.ai_draft_requested:
        record(
            session,
            actor,
            ticket,
            "ai_draft_requested",
            ticket.ai_draft_requested,
            draft,
        )
        ticket.ai_draft_requested = draft
        changed["ai_draft_requested"] = draft
        if draft:
            clear_implement_approval(session, actor, ticket, changed)
    if implement is not None and implement != ticket.ai_implement_approved:
        if implement:
            record(session, actor, ticket, "ai_implement_approved", False, True)
            ticket.ai_implement_approved = True
            ticket.ai_implement_approved_by_id = actor.user_id
            ticket.ai_implement_approved_at = _now()
            ticket.ai_implement_draft_version = version
            changed["ai_implement_approved"] = True
            changed["ai_implement_draft_version"] = version
        else:
            clear_implement_approval(session, actor, ticket, changed)
    if changed:
        emit(session, actor, EventType.AI_FLAGS_CHANGED, ticket.id, changed)
    return ticket


def delete_ticket(session: Session, actor: Actor, ticket: Ticket) -> list[Path]:
    """Delete a ticket. Returns the attachment files to remove after commit."""
    perm.check_delete(actor)
    files = [
        Path(path)
        for path in session.scalars(
            select(Attachment.storage_path).where(Attachment.ticket_id == ticket.id)
        )
    ]
    emit(session, actor, EventType.TICKET_DELETED, ticket.id, {"title": ticket.title})
    if ticket.series_id and ticket.status not in CLOSED_STATUSES:
        from . import recurrence  # recurrence builds on this module

        recurrence.round_closed(session, ticket)
    session.delete(ticket)
    return files


# Comments


def add_comment(session: Session, actor: Actor, ticket: Ticket, body: str) -> Comment:
    """Add a comment."""
    perm.check_comment(actor)
    assert actor.user_id is not None
    comment = Comment(
        ticket_id=ticket.id, author_id=actor.user_id, body=body, via=actor.via
    )
    session.add(comment)
    session.flush()
    emit(
        session,
        actor,
        EventType.COMMENT_ADDED,
        ticket.id,
        {
            "comment_id": comment.id,
            "title": ticket.title,
            "requested_by_id": ticket.requested_by_id,
            "assignee_ids": ticket.assignee_ids,
        },
    )
    return comment


def list_comments(session: Session, ticket: Ticket) -> list[Comment]:
    """Comments, oldest first."""
    return list(
        session.scalars(
            select(Comment).where(Comment.ticket_id == ticket.id).order_by(Comment.id)
        )
    )


# Checklist


def _checklist_item(ticket: Ticket, item_id: int) -> ChecklistItem:
    for item in ticket.checklist:
        if item.id == item_id:
            return item
    raise NotFound(f"Checklist item {item_id} not found")


def add_checklist_item(
    session: Session, actor: Actor, ticket: Ticket, text: str, position: int | None
) -> ChecklistItem:
    """Add a checklist item."""
    perm.check_checklist(actor, ticket)
    if position is None:
        position = max((item.position for item in ticket.checklist), default=-1) + 1
    item = ChecklistItem(text=text, position=position)
    ticket.checklist.append(item)
    session.flush()
    record(session, actor, ticket, "checklist_added", None, text)
    emit(session, actor, EventType.CHECKLIST_CHANGED, ticket.id, {"item_id": item.id})
    return item


def update_checklist_item(
    session: Session,
    actor: Actor,
    ticket: Ticket,
    item_id: int,
    *,
    text: str | None,
    done: bool | None,
    position: int | None,
) -> ChecklistItem:
    """Change a checklist item."""
    perm.check_checklist(actor, ticket)
    item = _checklist_item(ticket, item_id)
    if text is not None and text != item.text:
        record(session, actor, ticket, "checklist_text", item.text, text)
        item.text = text
    if done is not None and done != item.done:
        record(
            session,
            actor,
            ticket,
            "checklist_done",
            {"item": item.text, "done": item.done},
            {"item": item.text, "done": done},
        )
        item.done = done
        item.done_by_id = actor.user_id if done else None
        item.done_at = _now() if done else None
    if position is not None:
        item.position = position
    emit(session, actor, EventType.CHECKLIST_CHANGED, ticket.id, {"item_id": item.id})
    return item


def delete_checklist_item(
    session: Session, actor: Actor, ticket: Ticket, item_id: int
) -> None:
    """Remove a checklist item."""
    perm.check_checklist(actor, ticket)
    item = _checklist_item(ticket, item_id)
    record(session, actor, ticket, "checklist_removed", item.text, None)
    ticket.checklist.remove(item)
    emit(session, actor, EventType.CHECKLIST_CHANGED, ticket.id, {"item_id": item_id})


# History


def list_history(session: Session, ticket: Ticket) -> list[HistoryEntry]:
    """History, oldest first."""
    return list(
        session.scalars(
            select(HistoryEntry)
            .where(HistoryEntry.ticket_id == ticket.id)
            .order_by(HistoryEntry.id)
        )
    )


# Attachments


THUMBNAIL_SIZE = (480, 480)


def thumbnail_path(attachment: Attachment) -> Path:
    """Where the preview of an image attachment lives."""
    return Path(attachment.storage_path + ".thumb.jpg")


def make_thumbnail(attachment: Attachment) -> Path | None:
    """Create the JPEG preview of an image; None if it is no readable image."""
    if not attachment.content_type.startswith("image/"):
        return None
    target = thumbnail_path(attachment)
    if target.exists():
        return target
    try:
        with Image.open(attachment.storage_path) as image:
            image = ImageOps.exif_transpose(image)
            image.thumbnail(THUMBNAIL_SIZE)
            image.convert("RGB").save(target, "JPEG", quality=80, optimize=True)
    except (OSError, ValueError, Image.DecompressionBombError):
        _LOGGER.warning("No thumbnail for attachment %s", attachment.id)
        target.unlink(missing_ok=True)
        return None
    return target


@dataclass(frozen=True)
class TicketStats:
    """Counts shown on a ticket tile."""

    comment_count: int = 0
    attachment_count: int = 0
    cover_attachment_id: str | None = None
    latest_draft_version: int | None = None


def ticket_stats(session: Session, ticket_ids: list[int]) -> dict[int, TicketStats]:
    """Comment and attachment counts and the first photo per ticket."""
    if not ticket_ids:
        return {}
    comments = dict(
        session.execute(
            select(Comment.ticket_id, func.count())
            .where(Comment.ticket_id.in_(ticket_ids))
            .group_by(Comment.ticket_id)
        ).all()
    )
    attachments = dict(
        session.execute(
            select(Attachment.ticket_id, func.count())
            .where(Attachment.ticket_id.in_(ticket_ids))
            .group_by(Attachment.ticket_id)
        ).all()
    )
    drafts = dict(
        session.execute(
            select(AIDraft.ticket_id, func.max(AIDraft.version))
            .where(AIDraft.ticket_id.in_(ticket_ids))
            .group_by(AIDraft.ticket_id)
        ).all()
    )
    covers: dict[int, str] = {}
    for ticket_id, attachment_id in session.execute(
        select(Attachment.ticket_id, Attachment.id)
        .where(
            Attachment.ticket_id.in_(ticket_ids),
            Attachment.kind == AttachmentKind.PHOTO,
            Attachment.content_type.like("image/%"),
        )
        .order_by(Attachment.ticket_id, Attachment.created_at)
    ):
        covers.setdefault(ticket_id, attachment_id)
    return {
        ticket_id: TicketStats(
            comment_count=comments.get(ticket_id, 0),
            attachment_count=attachments.get(ticket_id, 0),
            cover_attachment_id=covers.get(ticket_id),
            latest_draft_version=drafts.get(ticket_id),
        )
        for ticket_id in ticket_ids
    }


def add_attachment(
    session: Session,
    actor: Actor,
    ticket: Ticket,
    *,
    kind: AttachmentKind,
    filename: str,
    content_type: str,
    data: bytes,
    attachments_dir: Path,
) -> Attachment:
    """Store a file and register it. The file is written before the row is added."""
    perm.check_attachment_upload(actor, ticket)
    assert actor.user_id is not None
    attachment_id = str(uuid.uuid4())
    directory = attachments_dir / str(ticket.id)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / attachment_id
    path.write_bytes(data)
    attachment = Attachment(
        id=attachment_id,
        ticket_id=ticket.id,
        kind=kind,
        filename=Path(filename).name[:255] or "file",
        content_type=content_type,
        size=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
        storage_path=str(path),
        uploaded_by_id=actor.user_id,
        uploaded_via=actor.via,
    )
    session.add(attachment)
    session.flush()
    record(session, actor, ticket, "attachment_added", None, attachment.filename)
    emit(
        session,
        actor,
        EventType.ATTACHMENT_ADDED,
        ticket.id,
        {"attachment_id": attachment.id, "kind": kind, "filename": attachment.filename},
    )
    return attachment


def get_attachment(session: Session, attachment_id: str) -> Attachment:
    """Attachment by id."""
    attachment = session.get(Attachment, attachment_id)
    if attachment is None:
        raise NotFound(f"Attachment {attachment_id} not found")
    return attachment


def list_attachments(session: Session, ticket: Ticket) -> list[Attachment]:
    """Attachments of a ticket, oldest first."""
    return list(
        session.scalars(
            select(Attachment)
            .where(Attachment.ticket_id == ticket.id)
            .order_by(Attachment.created_at)
        )
    )


def delete_attachment(session: Session, actor: Actor, attachment: Attachment) -> Path:
    """Remove an attachment. Returns the file to delete after commit."""
    perm.check_attachment_delete(actor, attachment)
    ticket = get_ticket(session, attachment.ticket_id)
    record(session, actor, ticket, "attachment_removed", attachment.filename, None)
    session.delete(attachment)
    thumbnail_path(attachment).unlink(missing_ok=True)
    return Path(attachment.storage_path)


# Events


def list_events(session: Session, after: int, limit: int) -> list[Event]:
    """Events after a sequence number."""
    return list(
        session.scalars(
            select(Event).where(Event.seq > after).order_by(Event.seq).limit(limit)
        )
    )


def last_event_seq(session: Session) -> int:
    """Highest sequence number."""
    return session.scalar(select(func.max(Event.seq))) or 0


# API tokens


def hash_token(token: str) -> str:
    """Tokens are stored as SHA-256 hashes."""
    return hashlib.sha256(token.encode()).hexdigest()


def create_token(session: Session, name: str) -> str:
    """Create a token and return it once in plain text."""
    if session.scalar(select(ApiToken).where(ApiToken.name == name)):
        raise Invalid("token_exists", f"A token named {name} exists.")
    token = secrets.token_urlsafe(32)
    session.add(ApiToken(name=name, token_hash=hash_token(token)))
    return token


def revoke_token(session: Session, name: str) -> None:
    """Revoke a token."""
    api_token = session.scalar(select(ApiToken).where(ApiToken.name == name))
    if api_token is None:
        raise NotFound(f"Token {name} not found")
    api_token.revoked_at = _now()


def authenticate(session: Session, token: str) -> ApiToken | None:
    """Valid token or None."""
    api_token = session.scalar(
        select(ApiToken).where(
            ApiToken.token_hash == hash_token(token), ApiToken.revoked_at.is_(None)
        )
    )
    if api_token is not None:
        api_token.last_used_at = _now()
    return api_token
