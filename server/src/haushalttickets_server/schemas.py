"""API request and response bodies."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl

from .enums import (
    ApprovalState,
    AttachmentKind,
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


class UserSyncItem(_In):
    ha_user_id: Annotated[str, Field(min_length=1, max_length=64)]
    display_name: Annotated[str, Field(min_length=1, max_length=200)]
    is_admin: bool = False
    is_active: bool = True
    person_entity_id: str | None = None


class UserSync(_In):
    users: list[UserSyncItem]


class UserPatch(_In):
    role: Role


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
    ha_area_id: str | None
    ha_device_id: str | None
    ha_targets: list[str]
    ai_draft_requested: bool
    ai_implement_approved: bool
    ai_implement_approved_by_id: str | None
    ai_implement_approved_at: datetime | None
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


class TicketList(BaseModel):
    count: int
    tickets: list[TicketOut]


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
