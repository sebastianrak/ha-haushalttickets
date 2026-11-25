"""Database models (see docs/data-model.md)."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Identity,
    Integer,
    MetaData,
    Numeric,
    SmallInteger,
    String,
    Table,
    Text,
    UniqueConstraint,
    func,
    true,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from .enums import (
    ApprovalState,
    AttachmentKind,
    IntervalUnit,
    RecurrenceMode,
    Role,
    RunOutcome,
    Status,
    TicketType,
    Urgency,
    Via,
)

JSONType = JSON().with_variant(JSONB(), "postgresql")
Money = Numeric(10, 2)

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


def _in(column: str, enum: type[StrEnum], name: str | None = None) -> CheckConstraint:
    """CHECK constraint limiting a string column to the keys of an enum."""
    values = ", ".join(f"'{member.value}'" for member in enum)
    return CheckConstraint(f"{column} IN ({values})", name=name or column)


class Base(DeclarativeBase):
    """Declarative base with stable constraint names for migrations."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def _now() -> Any:
    return mapped_column(DateTime(timezone=True), server_default=func.now())


ticket_assignees = Table(
    "ticket_assignees",
    Base.metadata,
    Column(
        "ticket_id",
        ForeignKey("tickets.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column("user_id", ForeignKey("users.ha_user_id"), primary_key=True),
)


class User(Base):
    """Household member, keyed by the Home Assistant user id."""

    __tablename__ = "users"
    __table_args__ = (_in("role", Role),)

    ha_user_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(200))
    role: Mapped[str] = mapped_column(String(20), default=Role.CHILD)
    ha_is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    ha_person_entity_id: Mapped[str | None] = mapped_column(String(255))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    # Shown as filter and for assignment on the board (set by admins).
    board_visible: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=true()
    )
    created_at: Mapped[datetime] = _now()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Category(Base):
    """Free category of the household (Garden, Car, …), one per ticket."""

    __tablename__ = "categories"

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    name: Mapped[str] = mapped_column(String(60), unique=True)
    color: Mapped[str] = mapped_column(String(7))
    created_at: Mapped[datetime] = _now()


class Series(Base):
    """Template for recurring tickets."""

    __tablename__ = "series"
    __table_args__ = (
        _in("type", TicketType),
        _in("urgency", Urgency),
        _in("mode", RecurrenceMode),
        _in("interval_unit", IntervalUnit),
        CheckConstraint("lead_days >= 0", name="lead_days"),
        CheckConstraint(
            "interval_count IS NULL OR interval_count > 0", name="interval"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    type: Mapped[str] = mapped_column(String(20))
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    urgency: Mapped[str] = mapped_column(String(20), default=Urgency.SOMEDAY)
    importance: Mapped[int | None] = mapped_column(SmallInteger)
    requested_by_id: Mapped[str] = mapped_column(ForeignKey("users.ha_user_id"))
    assignee_ids: Mapped[list[str]] = mapped_column(JSONType, default=list)
    ha_area_id: Mapped[str | None] = mapped_column(String(255))
    ha_device_id: Mapped[str | None] = mapped_column(String(255))
    category_id: Mapped[int | None] = mapped_column(
        ForeignKey("categories.id", ondelete="SET NULL")
    )
    mode: Mapped[str] = mapped_column(String(20))
    interval_count: Mapped[int | None] = mapped_column(Integer)
    interval_unit: Mapped[str] = mapped_column(String(10), default=IntervalUnit.MONTHS)
    # Unused; fixed_calendar steps from ``anchor`` by the interval instead.
    rrule: Mapped[str | None] = mapped_column(String(255))
    lead_days: Mapped[int] = mapped_column(Integer, default=7)
    # Due date of the next round to create; None while a round after completion
    # is still open.
    next_due: Mapped[date | None] = mapped_column(Date)
    # fixed_calendar: rounds fall on anchor + n * interval.
    anchor: Mapped[date | None] = mapped_column(Date)
    # Copied into every round: checklist texts and type-specific fields.
    checklist: Mapped[list[str]] = mapped_column(JSONType, default=list)
    fields: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = _now()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Ticket(Base):
    """A ticket. Type-specific columns are NULL for other types."""

    __tablename__ = "tickets"
    __table_args__ = (
        _in("type", TicketType),
        _in("status", Status),
        _in("urgency", Urgency),
        _in("created_via", Via),
        _in("approval_state", ApprovalState),
        CheckConstraint(
            "importance IS NULL OR importance BETWEEN 1 AND 5", name="importance"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    type: Mapped[str] = mapped_column(String(20))
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default=Status.NEW, index=True)
    urgency: Mapped[str] = mapped_column(String(20), default=Urgency.SOMEDAY)
    importance: Mapped[int | None] = mapped_column(SmallInteger)
    due_date: Mapped[date | None] = mapped_column(Date)
    requested_by_id: Mapped[str] = mapped_column(ForeignKey("users.ha_user_id"))
    created_by_id: Mapped[str | None] = mapped_column(ForeignKey("users.ha_user_id"))
    created_via: Mapped[str] = mapped_column(String(20), default=Via.UI)
    series_id: Mapped[int | None] = mapped_column(
        ForeignKey("series.id", ondelete="SET NULL")
    )
    category_id: Mapped[int | None] = mapped_column(
        ForeignKey("categories.id", ondelete="SET NULL"), index=True
    )

    # repair + home_assistant
    ha_area_id: Mapped[str | None] = mapped_column(String(255))
    ha_device_id: Mapped[str | None] = mapped_column(String(255))
    # home_assistant
    ha_targets: Mapped[list[str]] = mapped_column(JSONType, default=list)

    # AI approvals
    ai_draft_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    ai_implement_approved: Mapped[bool] = mapped_column(Boolean, default=False)
    ai_implement_approved_by_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.ha_user_id")
    )
    ai_implement_approved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    # Draft version the approval to implement refers to (decision 50).
    ai_implement_draft_version: Mapped[int | None] = mapped_column(Integer)

    # purchase
    price_estimated: Mapped[Decimal | None] = mapped_column(Money)
    price_actual: Mapped[Decimal | None] = mapped_column(Money)
    product_url: Mapped[str | None] = mapped_column(String(2000))
    vendor: Mapped[str | None] = mapped_column(String(200))
    warranty_until: Mapped[date | None] = mapped_column(Date)
    approval_state: Mapped[str] = mapped_column(
        String(20), default=ApprovalState.NOT_REQUIRED
    )
    approval_by_id: Mapped[str | None] = mapped_column(ForeignKey("users.ha_user_id"))
    approval_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # repair
    contractor_name: Mapped[str | None] = mapped_column(String(200))
    contractor_phone: Mapped[str | None] = mapped_column(String(50))
    contractor_appointment: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    cost_estimated: Mapped[Decimal | None] = mapped_column(Money)
    cost_actual: Mapped[Decimal | None] = mapped_column(Money)
    warranty_case: Mapped[bool] = mapped_column(Boolean, default=False)
    related_purchase_id: Mapped[int | None] = mapped_column(
        ForeignKey("tickets.id", ondelete="SET NULL")
    )

    created_at: Mapped[datetime] = _now()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    assignees: Mapped[list[User]] = relationship(
        secondary=ticket_assignees, lazy="selectin", order_by=User.display_name
    )
    category: Mapped[Category | None] = relationship(lazy="selectin")
    checklist: Mapped[list[ChecklistItem]] = relationship(
        back_populates="ticket",
        cascade="all, delete-orphan",
        order_by="ChecklistItem.position",
        lazy="selectin",
    )

    @property
    def assignee_ids(self) -> list[str]:
        """Ids of the assigned users."""
        return [user.ha_user_id for user in self.assignees]

    @property
    def category_name(self) -> str | None:
        """Name of the category, for clients that do not load the list."""
        return self.category.name if self.category else None


class Comment(Base):
    """Chat-like comment on a ticket."""

    __tablename__ = "comments"
    __table_args__ = (_in("via", Via),)

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    ticket_id: Mapped[int] = mapped_column(
        ForeignKey("tickets.id", ondelete="CASCADE"), index=True
    )
    author_id: Mapped[str] = mapped_column(ForeignKey("users.ha_user_id"))
    body: Mapped[str] = mapped_column(Text)
    via: Mapped[str] = mapped_column(String(20))
    created_at: Mapped[datetime] = _now()


class HistoryEntry(Base):
    """One recorded change of a ticket."""

    __tablename__ = "history"
    __table_args__ = (_in("via", Via),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    ticket_id: Mapped[int] = mapped_column(
        ForeignKey("tickets.id", ondelete="CASCADE"), index=True
    )
    actor_id: Mapped[str | None] = mapped_column(ForeignKey("users.ha_user_id"))
    via: Mapped[str] = mapped_column(String(20))
    field: Mapped[str] = mapped_column(String(50))
    old: Mapped[Any] = mapped_column(JSONType, nullable=True)
    new: Mapped[Any] = mapped_column(JSONType, nullable=True)
    at: Mapped[datetime] = _now()


class ChecklistItem(Base):
    """Optional sub-step of a ticket."""

    __tablename__ = "checklist_items"

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    ticket_id: Mapped[int] = mapped_column(
        ForeignKey("tickets.id", ondelete="CASCADE"), index=True
    )
    position: Mapped[int] = mapped_column(Integer, default=0)
    text: Mapped[str] = mapped_column(String(500))
    done: Mapped[bool] = mapped_column(Boolean, default=False)
    done_by_id: Mapped[str | None] = mapped_column(ForeignKey("users.ha_user_id"))
    done_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    ticket: Mapped[Ticket] = relationship(back_populates="checklist")


class Attachment(Base):
    """File stored in the attachments directory of the LXC."""

    __tablename__ = "attachments"
    __table_args__ = (_in("kind", AttachmentKind), _in("uploaded_via", Via))

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    ticket_id: Mapped[int] = mapped_column(
        ForeignKey("tickets.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(20))
    filename: Mapped[str] = mapped_column(String(255))
    content_type: Mapped[str] = mapped_column(String(100))
    size: Mapped[int] = mapped_column(BigInteger)
    sha256: Mapped[str] = mapped_column(String(64))
    storage_path: Mapped[str] = mapped_column(String(500))
    uploaded_by_id: Mapped[str] = mapped_column(ForeignKey("users.ha_user_id"))
    uploaded_via: Mapped[str] = mapped_column(String(20))
    created_at: Mapped[datetime] = _now()


class NightlyRun(Base):
    """Log of one nightly AI run."""

    __tablename__ = "nightly_runs"
    __table_args__ = (_in("outcome", RunOutcome),)

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    started_at: Mapped[datetime] = _now()
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    outcome: Mapped[str | None] = mapped_column(String(20))
    tickets: Mapped[list[int]] = mapped_column(JSONType, default=list)
    notes: Mapped[str] = mapped_column(Text, default="")
    # Per ticket: {"ticket_id", "kind", "version", "needs_restart"}.
    results: Mapped[list[dict[str, Any]]] = mapped_column(JSONType, default=list)
    # Token and cost figures the runner reports (free-form).
    usage: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)


class AIDraft(Base):
    """Versioned AI proposal for a ticket."""

    __tablename__ = "ai_drafts"
    __table_args__ = (UniqueConstraint("ticket_id", "version"),)

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    ticket_id: Mapped[int] = mapped_column(
        ForeignKey("tickets.id", ondelete="CASCADE"), index=True
    )
    version: Mapped[int] = mapped_column(Integer)
    body: Mapped[str] = mapped_column(Text)
    run_id: Mapped[int | None] = mapped_column(
        ForeignKey("nightly_runs.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = _now()


class ChangeLogEntry(Base):
    """Home Assistant change made by the nightly run."""

    __tablename__ = "change_log"

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    ticket_id: Mapped[int] = mapped_column(
        ForeignKey("tickets.id", ondelete="CASCADE"), index=True
    )
    run_id: Mapped[int | None] = mapped_column(
        ForeignKey("nightly_runs.id", ondelete="SET NULL")
    )
    summary: Mapped[str] = mapped_column(Text)
    objects: Mapped[list[str]] = mapped_column(JSONType, default=list)
    git_commit: Mapped[str | None] = mapped_column(String(64))
    # The change only takes effect after a reload or restart (YAML files).
    needs_restart: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = _now()


class Event(Base):
    """Outbox feed for the Home Assistant integration (notifications, refresh)."""

    __tablename__ = "events"
    __table_args__ = (_in("via", Via),)

    seq: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    type: Mapped[str] = mapped_column(String(40))
    # No foreign key: events of deleted tickets stay in the feed.
    ticket_id: Mapped[int | None] = mapped_column(Integer, index=True)
    actor_id: Mapped[str | None] = mapped_column(String(64))
    via: Mapped[str] = mapped_column(String(20))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    created_at: Mapped[datetime] = _now()


class Setting(Base):
    """Key/value settings changed at runtime (e.g. the approval threshold)."""

    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[Any] = mapped_column(JSONType)


class ApiToken(Base):
    """Bearer token of an API client (the Home Assistant integration)."""

    __tablename__ = "api_tokens"

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = _now()
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
