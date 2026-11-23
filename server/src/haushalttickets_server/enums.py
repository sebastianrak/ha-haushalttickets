"""Stable keys used in the database and the API. The UI translates them."""

from enum import StrEnum


class TicketType(StrEnum):
    """Kind of ticket."""

    REPAIR = "repair"
    PURCHASE = "purchase"
    HOME_ASSISTANT = "home_assistant"


class Status(StrEnum):
    """Kanban column."""

    NEW = "new"
    DISCUSSING = "discussing"
    IN_PROGRESS = "in_progress"
    WAITING_FOR_PARTS = "waiting_for_parts"
    ACCEPTANCE = "acceptance"
    DONE = "done"
    REJECTED = "rejected"


OPEN_STATUSES = frozenset(
    {
        Status.NEW,
        Status.DISCUSSING,
        Status.IN_PROGRESS,
        Status.WAITING_FOR_PARTS,
        Status.ACCEPTANCE,
    }
)
CLOSED_STATUSES = frozenset({Status.DONE, Status.REJECTED})


class Urgency(StrEnum):
    """How soon something has to happen."""

    SOMEDAY = "someday"
    THIS_WEEK = "this_week"
    BURNING = "burning"


class Role(StrEnum):
    """Role of a household member."""

    ADMIN = "admin"
    ADULT = "adult"
    CHILD = "child"
    AI_AGENT = "ai_agent"


class Via(StrEnum):
    """Channel a change arrives through."""

    UI = "ui"
    MCP = "mcp"
    NIGHTLY_RUN = "nightly_run"
    RECURRENCE = "recurrence"
    EMERGENCY_UI = "emergency_ui"
    SYSTEM = "system"


# Changes through these channels follow the AI rules, whoever is logged in.
AI_VIAS = frozenset({Via.MCP, Via.NIGHTLY_RUN})


class ApprovalState(StrEnum):
    """Approval of a purchase by the second adult."""

    NOT_REQUIRED = "not_required"
    PENDING = "pending"
    APPROVED = "approved"
    DECLINED = "declined"


class AttachmentKind(StrEnum):
    """Kind of attached file."""

    PHOTO = "photo"
    INVOICE = "invoice"
    DOCUMENT = "document"


class RecurrenceMode(StrEnum):
    """How the next due date of a series is computed."""

    AFTER_COMPLETION = "after_completion"
    FIXED_CALENDAR = "fixed_calendar"


class IntervalUnit(StrEnum):
    """Unit of a recurrence interval."""

    DAYS = "days"
    WEEKS = "weeks"
    MONTHS = "months"
    YEARS = "years"


class RunOutcome(StrEnum):
    """Result of a nightly AI run."""

    NO_WORK = "no_work"
    QUOTA_LOW = "quota_low"
    DONE = "done"
    FAILED = "failed"


class EventType(StrEnum):
    """Entries of the event feed the Home Assistant integration consumes."""

    TICKET_CREATED = "ticket_created"
    TICKET_UPDATED = "ticket_updated"
    TICKET_DELETED = "ticket_deleted"
    STATUS_CHANGED = "status_changed"
    ASSIGNEES_CHANGED = "assignees_changed"
    APPROVAL_REQUESTED = "approval_requested"
    APPROVAL_DECIDED = "approval_decided"
    AI_FLAGS_CHANGED = "ai_flags_changed"
    COMMENT_ADDED = "comment_added"
    ATTACHMENT_ADDED = "attachment_added"
    CHECKLIST_CHANGED = "checklist_changed"
