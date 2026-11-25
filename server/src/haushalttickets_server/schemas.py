"""API request and response bodies."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl

from .enums import (
    ApprovalState,
    AttachmentKind,
    IntervalUnit,
    RecurrenceMode,
    Role,
    Status,
    TicketType,
    Urgency,
)

Title = Annotated[str, Field(min_length=1, max_length=200)]
Importance = Annotated[int, Field(ge=1, le=5)]
Amount = Annotated[Decimal, Field(ge=0, max_digits=10, decimal_places=2)]


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _Out(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# Users


class UserOut(_Out):
    ha_user_id: str
    display_name: str
    role: Role
    ha_is_admin: bool
    ha_person_entity_id: str | None
    active: bool
    board_visible: bool = True


class UserSyncItem(_In):
    ha_user_id: Annotated[str, Field(min_length=1, max_length=64)]
    display_name: Annotated[str, Field(min_length=1, max_length=200)]
    is_admin: bool = False
    is_active: bool = True
    person_entity_id: str | None = None


class UserSync(_In):
    users: list[UserSyncItem]


class UserPatch(_In):
    role: Role | None = None
    board_visible: bool | None = None


# Categories

CategoryName = Annotated[str, Field(min_length=1, max_length=60)]
Color = Annotated[str, Field(pattern=r"^#[0-9a-fA-F]{6}$")]


class CategoryCreate(_In):
    name: CategoryName
    color: Color


class CategoryPatch(_In):
    name: CategoryName | None = None
    color: Color | None = None


class CategoryOut(_Out):
    id: int
    name: str
    color: str
    ticket_count: int = 0


# Tickets

# Fields that only apply to some ticket types.
TYPE_FIELDS: dict[str, frozenset[TicketType]] = {
    "ha_area_id": frozenset({TicketType.REPAIR, TicketType.HOME_ASSISTANT}),
    "ha_device_id": frozenset({TicketType.REPAIR, TicketType.HOME_ASSISTANT}),
    "ha_targets": frozenset({TicketType.HOME_ASSISTANT}),
    "price_estimated": frozenset({TicketType.PURCHASE}),
    "price_actual": frozenset({TicketType.PURCHASE}),
    "product_url": frozenset({TicketType.PURCHASE}),
    "vendor": frozenset({TicketType.PURCHASE}),
    "warranty_until": frozenset({TicketType.PURCHASE}),
    "contractor_name": frozenset({TicketType.REPAIR}),
    "contractor_phone": frozenset({TicketType.REPAIR}),
    "contractor_appointment": frozenset({TicketType.REPAIR}),
    "cost_estimated": frozenset({TicketType.REPAIR}),
    "cost_actual": frozenset({TicketType.REPAIR}),
    "warranty_case": frozenset({TicketType.REPAIR}),
    "related_purchase_id": frozenset({TicketType.REPAIR}),
}


class TicketFields(_In):
    """Editable fields shared by create and patch."""

    title: Title | None = None
    description: Annotated[str, Field(max_length=20000)] | None = None
    urgency: Urgency | None = None
    importance: Importance | None = None
    due_date: date | None = None
    category_id: int | None = None
    ha_area_id: Annotated[str, Field(max_length=255)] | None = None
    ha_device_id: Annotated[str, Field(max_length=255)] | None = None
    ha_targets: list[Annotated[str, Field(max_length=255)]] | None = None
    price_estimated: Amount | None = None
    price_actual: Amount | None = None
    product_url: HttpUrl | None = None
    vendor: Annotated[str, Field(max_length=200)] | None = None
    warranty_until: date | None = None
    contractor_name: Annotated[str, Field(max_length=200)] | None = None
    contractor_phone: Annotated[str, Field(max_length=50)] | None = None
    contractor_appointment: datetime | None = None
    cost_estimated: Amount | None = None
    cost_actual: Amount | None = None
    warranty_case: bool | None = None
    related_purchase_id: int | None = None


class TicketCreate(TicketFields):
    type: TicketType
    title: Title
    requested_by_id: str | None = None
    assignee_ids: list[str] = Field(default_factory=list)


class TicketPatch(TicketFields):
    """Only the fields that are sent are changed; null clears a field."""


class StatusChange(_In):
    status: Status


class AssigneesSet(_In):
    user_ids: list[str]


class ApprovalDecision(_In):
    decision: Literal["approve", "decline"]


class AIFlags(_In):
    draft_requested: bool | None = None
    implement_approved: bool | None = None


class ChecklistItemOut(_Out):
    id: int
    position: int
    text: str
    done: bool
    done_by_id: str | None
    done_at: datetime | None


class TicketOut(_Out):
    id: int
    type: TicketType
    title: str
    description: str
    status: Status
    urgency: Urgency
    importance: int | None
    due_date: date | None
    requested_by_id: str
    created_by_id: str | None
    created_via: str
    series_id: int | None
    assignee_ids: list[str]
    category_id: int | None = None
    category_name: str | None = None
    ha_area_id: str | None
    ha_device_id: str | None
    ha_targets: list[str]
    ai_draft_requested: bool
    ai_implement_approved: bool
    ai_implement_approved_by_id: str | None
    ai_implement_approved_at: datetime | None
    ai_implement_draft_version: int | None = None
    price_estimated: Decimal | None
    price_actual: Decimal | None
    product_url: str | None
    vendor: str | None
    warranty_until: date | None
    approval_state: ApprovalState
    approval_by_id: str | None
    approval_at: datetime | None
    contractor_name: str | None
    contractor_phone: str | None
    contractor_appointment: datetime | None
    cost_estimated: Decimal | None
    cost_actual: Decimal | None
    warranty_case: bool
    related_purchase_id: int | None
    checklist: list[ChecklistItemOut]
    created_at: datetime
    updated_at: datetime
    closed_at: datetime | None
    # Shown on the ticket tile.
    comment_count: int = 0
    attachment_count: int = 0
    cover_attachment_id: str | None = None
    # Newest AI draft (None: no draft yet).
    latest_draft_version: int | None = None


class TicketList(BaseModel):
    count: int
    tickets: list[TicketOut]


# Recurring series

# Type-specific fields a series copies into every round (planned values only).
SERIES_TYPE_FIELDS = (
    "ha_targets",
    "price_estimated",
    "product_url",
    "vendor",
    "contractor_name",
    "contractor_phone",
    "cost_estimated",
)
ChecklistText = Annotated[str, Field(min_length=1, max_length=500)]


class SeriesTypeFields(_In):
    ha_targets: list[Annotated[str, Field(max_length=255)]] | None = None
    price_estimated: Amount | None = None
    product_url: HttpUrl | None = None
    vendor: Annotated[str, Field(max_length=200)] | None = None
    contractor_name: Annotated[str, Field(max_length=200)] | None = None
    contractor_phone: Annotated[str, Field(max_length=50)] | None = None
    cost_estimated: Amount | None = None


class SeriesTemplate(_In):
    """What every round gets; only the fields that are sent are changed."""

    title: Title | None = None
    description: Annotated[str, Field(max_length=20000)] | None = None
    urgency: Urgency | None = None
    importance: Importance | None = None
    assignee_ids: list[str] | None = None
    category_id: int | None = None
    ha_area_id: Annotated[str, Field(max_length=255)] | None = None
    ha_device_id: Annotated[str, Field(max_length=255)] | None = None
    checklist: Annotated[list[ChecklistText], Field(max_length=50)] | None = None
    fields: SeriesTypeFields | None = None


class SeriesRule(_In):
    mode: RecurrenceMode | None = None
    interval_count: Annotated[int, Field(ge=1, le=999)] | None = None
    interval_unit: IntervalUnit | None = None
    lead_days: Annotated[int, Field(ge=0, le=365)] | None = None


class SeriesCreate(SeriesTemplate, SeriesRule):
    """A new series, either from an existing ticket (its first round) or blank."""

    from_ticket_id: int | None = None
    type: TicketType | None = None
    requested_by_id: str | None = None
    # Due date of the first round (blank series) or calendar start (fixed_calendar).
    first_due: date | None = None
    mode: RecurrenceMode
    interval_count: Annotated[int, Field(ge=1, le=999)]
    interval_unit: IntervalUnit


class SeriesPatch(SeriesTemplate, SeriesRule):
    """Change a series; affects future rounds only."""

    next_due: date | None = None
    active: bool | None = None


class SeriesOut(_Out):
    id: int
    type: TicketType
    title: str
    description: str
    urgency: Urgency
    importance: int | None
    requested_by_id: str
    assignee_ids: list[str]
    category_id: int | None = None
    ha_area_id: str | None
    ha_device_id: str | None
    checklist: list[str]
    fields: dict[str, Any]
    mode: RecurrenceMode
    interval_count: int
    interval_unit: IntervalUnit
    lead_days: int
    next_due: date | None
    anchor: date | None
    active: bool
    created_at: datetime
    updated_at: datetime
    # Filled in by the API.
    open_ticket_id: int | None = None
    appears_on: date | None = None
    round_count: int = 0


# Comments, checklist, history, attachments


class CommentCreate(_In):
    body: Annotated[str, Field(min_length=1, max_length=20000)]


class CommentOut(_Out):
    id: int
    ticket_id: int
    author_id: str
    body: str
    via: str
    created_at: datetime


class ChecklistCreate(_In):
    text: Annotated[str, Field(min_length=1, max_length=500)]
    position: int | None = None


class ChecklistPatch(_In):
    text: Annotated[str, Field(min_length=1, max_length=500)] | None = None
    done: bool | None = None
    position: int | None = None


class HistoryOut(_Out):
    id: int
    ticket_id: int
    actor_id: str | None
    via: str
    field: str
    old: Any
    new: Any
    at: datetime


class AttachmentOut(_Out):
    id: str
    ticket_id: int
    kind: AttachmentKind
    filename: str
    content_type: str
    size: int
    sha256: str
    uploaded_by_id: str
    uploaded_via: str
    created_at: datetime


# Events and settings


class EventOut(_Out):
    seq: int
    type: str
    ticket_id: int | None
    actor_id: str | None
    via: str
    payload: dict[str, Any]
    created_at: datetime


class EventList(BaseModel):
    last_seq: int
    events: list[EventOut]


class SettingsOut(BaseModel):
    purchase_approval_threshold: Decimal


class SettingsPatch(_In):
    purchase_approval_threshold: Amount | None = None


# Nightly run (AI drafts and changes)

DraftText = Annotated[str, Field(min_length=1, max_length=100_000)]
NightlyComment = Annotated[str, Field(min_length=1, max_length=20_000)]


class DraftOut(_Out):
    id: int
    ticket_id: int
    version: int
    body: str
    run_id: int | None
    created_at: datetime


class ChangeIn(_In):
    summary: Annotated[str, Field(min_length=1, max_length=5000)]
    objects: list[Annotated[str, Field(max_length=255)]] = Field(
        default_factory=list, max_length=200
    )
    git_commit: Annotated[str | None, Field(max_length=64)] = None
    needs_restart: bool = False


class ChangeOut(_Out):
    id: int
    ticket_id: int
    run_id: int | None
    summary: str
    objects: list[str]
    git_commit: str | None
    needs_restart: bool
    created_at: datetime


class NightlyWorkItem(BaseModel):
    kind: Literal["implement", "draft"]
    ticket: TicketOut
    comments: list[CommentOut]
    # implement: the approved draft; draft: the newest draft to revise (if any).
    draft: DraftOut | None


class NightlyWork(BaseModel):
    items: list[NightlyWorkItem]


class RunOut(_Out):
    id: int
    started_at: datetime
    ended_at: datetime | None
    outcome: str | None
    tickets: list[int]
    notes: str
    results: list[dict[str, Any]]
    usage: dict[str, Any]


class RunEnd(_In):
    outcome: Literal["no_work", "quota_low", "done", "failed", "aborted"]
    notes: Annotated[str, Field(max_length=20_000)] = ""
    usage: dict[str, Any] = Field(default_factory=dict)


class NightlyResult(_In):
    run_id: int
    kind: Literal["draft", "implemented", "failed"]
    comment: NightlyComment
    draft: DraftText | None = None
    change: ChangeIn | None = None
    # failed: which step failed, and whether to try again next night (e.g. when the
    # usage limit cut the run short) instead of clearing the request.
    phase: Literal["draft", "implement"] | None = None
    retry: bool = False
