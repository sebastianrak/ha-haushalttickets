"""Who may do what (see docs/data-model.md, section Rules).

Effective permissions combine the role of the acting user with the channel the change
arrives through: anything coming in via MCP or the nightly run follows the AI rules,
whoever logged in the MCP client.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from .enums import (
    AI_VIAS,
    AUTOMATION_USER_ID,
    CLOSED_STATUSES,
    OPEN_STATUSES,
    ApprovalState,
    Role,
    Status,
    TicketType,
    Via,
)
from .models import Attachment, Ticket, User

AI_ALLOWED_STATUSES = frozenset(
    {
        Status.NEW,
        Status.DISCUSSING,
        Status.IN_PROGRESS,
        Status.WAITING_FOR_PARTS,
        Status.ACCEPTANCE,
    }
)
# A purchase awaiting approval cannot get past "discussing" (or be accepted).
BLOCKED_WITHOUT_APPROVAL = frozenset(
    {Status.IN_PROGRESS, Status.WAITING_FOR_PARTS, Status.ACCEPTANCE, Status.DONE}
)
CHILD_EDITABLE_FIELDS = frozenset(
    {
        "title",
        "description",
        "urgency",
        "importance",
        "due_date",
        "ha_area_id",
        "ha_device_id",
        "product_url",
        "vendor",
    }
)


class PermissionDenied(Exception):
    """The actor may not do this."""

    def __init__(self, code: str, message: str) -> None:
        """Initialize."""
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True, slots=True)
class Actor:
    """Who is acting and through which channel."""

    user: User | None
    via: Via

    @property
    def is_system(self) -> bool:
        """The integration itself, without an acting household member."""
        return self.user is None

    @property
    def is_ai(self) -> bool:
        """AI rules apply."""
        return self.via in AI_VIAS or (
            self.user is not None and self.user.role == Role.AI_AGENT
        )

    @property
    def is_automation(self) -> bool:
        """A Home Assistant automation or script."""
        return self.via == Via.AUTOMATION or self.user_id == AUTOMATION_USER_ID

    @property
    def role(self) -> Role | None:
        """Role of the acting user."""
        return Role(self.user.role) if self.user else None

    @property
    def user_id(self) -> str | None:
        """Id of the acting user."""
        return self.user.ha_user_id if self.user else None

    def is_human(self, *roles: Role) -> bool:
        """A household member (not AI) with one of the roles."""
        return not self.is_ai and self.role in roles

    def requested(self, ticket: Ticket) -> bool:
        """The actor requested the ticket."""
        return self.user is not None and ticket.requested_by_id == self.user_id


ADULTS = (Role.ADMIN, Role.ADULT)


def _deny(code: str, message: str) -> None:
    raise PermissionDenied(code, message)


def require_user(actor: Actor) -> None:
    """Mutations need an acting household member."""
    if actor.is_system:
        _deny("acting_user_required", "This change needs an acting user.")


def check_create(
    actor: Actor,
    requested_by_id: str | None,
    assignee_ids: Iterable[str],
    fields: Iterable[str] = (),
) -> None:
    """Create a ticket."""
    require_user(actor)
    if requested_by_id not in (None, actor.user_id) and not actor.is_human(*ADULTS):
        _deny("requester_forbidden", "Only adults may file tickets for others.")
    if list(assignee_ids) and not actor.is_human(*ADULTS):
        _deny("assign_forbidden", "Only adults may assign tickets.")
    extra = set(fields) - CHILD_EDITABLE_FIELDS
    if actor.role == Role.CHILD and not actor.is_ai and extra:
        _deny("field_forbidden", f"Not allowed: {', '.join(sorted(extra))}.")


def check_edit(actor: Actor, ticket: Ticket, fields: Iterable[str]) -> None:
    """Change ticket fields (not status, assignees, approvals or AI flags)."""
    require_user(actor)
    fields = set(fields)
    if actor.is_ai:
        _deny("ai_edit_forbidden", "The AI may comment but not edit ticket fields.")
    if actor.is_human(*ADULTS):
        return
    if actor.role == Role.CHILD:
        if not actor.requested(ticket):
            _deny("not_own_ticket", "Children may only edit their own tickets.")
        if ticket.status in CLOSED_STATUSES:
            _deny("ticket_closed", "Closed tickets cannot be edited.")
        if extra := fields - CHILD_EDITABLE_FIELDS:
            _deny("field_forbidden", f"Not allowed: {', '.join(sorted(extra))}.")
        return
    _deny("forbidden", "Not allowed.")


def check_status(actor: Actor, ticket: Ticket, new: Status) -> bool:
    """Change the status. Returns True when an admin overrides the acceptance rule."""
    require_user(actor)
    old = Status(ticket.status)
    if new == old:
        return False

    if (
        ticket.type == TicketType.PURCHASE
        and ticket.approval_state in (ApprovalState.PENDING, ApprovalState.DECLINED)
        and new in BLOCKED_WITHOUT_APPROVAL
    ):
        _deny("approval_pending", "This purchase needs the second adult's approval.")

    if actor.is_ai:
        if old in CLOSED_STATUSES:
            _deny("ai_reopen_forbidden", "The AI may not reopen closed tickets.")
        if new not in AI_ALLOWED_STATUSES:
            _deny("ai_status_forbidden", "The AI may move tickets up to acceptance.")
        return False

    if actor.role == Role.CHILD:
        if new == Status.DONE and actor.requested(ticket) and old in OPEN_STATUSES:
            return False
        _deny("child_status_forbidden", "Children may only accept their own tickets.")

    if not actor.is_human(*ADULTS):
        _deny("forbidden", "Not allowed.")

    # Adults and admins from here on: free moves, rejecting and reopening.
    if new == Status.DONE:
        if actor.requested(ticket):
            return False
        if actor.role == Role.ADMIN:
            return True
        _deny("acceptance_by_requester", "Only the requester accepts a ticket.")
    return False


def check_assign(actor: Actor) -> None:
    """Change assignees."""
    require_user(actor)
    if not actor.is_human(*ADULTS):
        _deny("assign_forbidden", "Only adults may assign tickets.")


def check_approval(actor: Actor, ticket: Ticket) -> None:
    """Approve or decline a purchase."""
    require_user(actor)
    if ticket.type != TicketType.PURCHASE:
        _deny("not_a_purchase", "Only purchases need approval.")
    if ticket.approval_state not in (ApprovalState.PENDING, ApprovalState.DECLINED):
        _deny("approval_not_pending", "This purchase does not await approval.")
    if not actor.is_human(*ADULTS) or actor.is_automation:
        _deny("approval_forbidden", "Only adults may approve purchases.")
    if actor.requested(ticket):
        _deny("approval_by_other_adult", "The other adult has to approve.")


def check_ai_flags(
    actor: Actor, ticket: Ticket, draft: bool | None, implement: bool | None
) -> None:
    """Set "AI should draft" / "AI should implement"."""
    require_user(actor)
    if not actor.is_human(*ADULTS) or actor.is_automation:
        _deny("ai_flags_forbidden", "Only adults may give the AI approvals.")
    if implement and ticket.type != TicketType.HOME_ASSISTANT:
        _deny("implement_not_applicable", "Only Home Assistant work is implemented.")


def check_comment(actor: Actor) -> None:
    """Add a comment. Everyone, including children and the AI."""
    require_user(actor)


def check_checklist(actor: Actor, ticket: Ticket) -> None:
    """Add, change or remove checklist items."""
    require_user(actor)
    if actor.is_ai:
        _deny("ai_checklist_forbidden", "The AI may comment but not edit checklists.")
    if actor.is_human(*ADULTS):
        return
    if actor.role == Role.CHILD and actor.requested(ticket):
        return
    _deny("checklist_forbidden", "Not allowed.")


def check_attachment_upload(actor: Actor, ticket: Ticket) -> None:
    """Attach a file."""
    require_user(actor)
    if actor.role == Role.CHILD and not actor.is_ai and not actor.requested(ticket):
        _deny("not_own_ticket", "Children may only add files to their own tickets.")


def check_attachment_delete(actor: Actor, attachment: Attachment) -> None:
    """Remove a file."""
    require_user(actor)
    if actor.is_ai:
        _deny("ai_delete_forbidden", "The AI never deletes.")
    if attachment.uploaded_by_id == actor.user_id or actor.role == Role.ADMIN:
        return
    _deny("forbidden", "Only the uploader or an admin may remove a file.")


def check_delete(actor: Actor) -> None:
    """Delete a ticket."""
    require_user(actor)
    if not actor.is_human(Role.ADMIN) or actor.is_automation:
        _deny("delete_forbidden", "Only admins delete tickets.")


def check_admin(actor: Actor) -> None:
    """Change roles or settings."""
    require_user(actor)
    if not actor.is_human(Role.ADMIN) or actor.is_automation:
        _deny("admin_required", "Admins only.")
