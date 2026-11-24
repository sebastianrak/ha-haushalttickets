"""Recurring tickets: series and the rounds they create.

A series is a template plus a rule. ``after_completion`` schedules the next round
one interval after the current round is closed; ``fixed_calendar`` keeps rounds on
``anchor + n * interval``. A round appears ``lead_days`` before it is due, never
more than one round of a series is open, and missed calendar dates are skipped.
Closing a round as "Träum weiter" skips that round only; the series goes on.
"""

from __future__ import annotations

import calendar
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
import logging
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import permissions as perm, services
from .enums import (
    AUTOMATION_USER_ID,
    CLOSED_STATUSES,
    EventType,
    IntervalUnit,
    RecurrenceMode,
    Status,
    TicketType,
    Via,
)
from .models import ChecklistItem, Series, Ticket, User
from .permissions import Actor
from .schemas import (
    SERIES_TYPE_FIELDS,
    TYPE_FIELDS,
    SeriesCreate,
    SeriesPatch,
    SeriesTypeFields,
)

_LOGGER = logging.getLogger(__name__)

# Set from the settings when the app starts; rounds are planned in local days.
TIMEZONE = "Europe/Berlin"
_TEMPLATE = (
    "title",
    "description",
    "urgency",
    "importance",
    "ha_area_id",
    "ha_device_id",
)
_AMOUNTS = ("price_estimated", "cost_estimated")
_RULE = {"mode", "interval_count", "interval_unit", "lead_days"}


def today() -> date:
    """The current day in the household's time zone."""
    return datetime.now(ZoneInfo(TIMEZONE)).date()


def _local_day(moment: datetime | None) -> date:
    if moment is None:
        return today()
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(ZoneInfo(TIMEZONE)).date()


# Dates


def _add_months(day: date, months: int) -> date:
    index = day.month - 1 + months
    year, month = day.year + index // 12, index % 12 + 1
    return date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


def add_interval(day: date, count: int, unit: str, times: int = 1) -> date:
    """``day`` moved forward by ``times`` intervals."""
    unit = IntervalUnit(unit)
    if unit == IntervalUnit.DAYS:
        return day + timedelta(days=count * times)
    if unit == IntervalUnit.WEEKS:
        return day + timedelta(weeks=count * times)
    months = count * times * (12 if unit == IntervalUnit.YEARS else 1)
    return _add_months(day, months)


def grid_on_or_after(series: Series, day: date) -> date:
    """First calendar date of a fixed series on or after ``day``."""
    anchor = series.anchor or day
    if day <= anchor:
        return anchor
    unit = IntervalUnit(series.interval_unit)
    if unit in (IntervalUnit.DAYS, IntervalUnit.WEEKS):
        step = series.interval_count * (7 if unit == IntervalUnit.WEEKS else 1)
        times = -(-(day - anchor).days // step)
        return anchor + timedelta(days=step * times)
    months = series.interval_count * (12 if unit == IntervalUnit.YEARS else 1)
    times = max(0, ((day.year - anchor.year) * 12 + day.month - anchor.month) // months)
    candidate = add_interval(anchor, series.interval_count, unit, times)
    while candidate < day:
        times += 1
        candidate = add_interval(anchor, series.interval_count, unit, times)
    return candidate


# Reading


def list_series(session: Session) -> list[Series]:
    """All series, the next due first."""
    return list(
        session.scalars(
            select(Series).order_by(
                Series.next_due.is_(None), Series.next_due, Series.id
            )
        )
    )


def get_series(session: Session, series_id: int) -> Series:
    series = session.get(Series, series_id)
    if series is None:
        raise services.NotFound(f"Series {series_id} not found.")
    return series


def open_round(session: Session, series: Series) -> Ticket | None:
    """The open ticket of the series, if any."""
    return session.scalars(
        select(Ticket)
        .where(Ticket.series_id == series.id, Ticket.status.notin_(CLOSED_STATUSES))
        .order_by(Ticket.id.desc())
        .limit(1)
    ).first()


def series_info(session: Session, series: Series) -> dict[str, Any]:
    """Values the API adds to a series."""
    ticket = open_round(session, series)
    count = session.scalar(
        select(func.count()).select_from(Ticket).where(Ticket.series_id == series.id)
    )
    appears = None
    if series.active and ticket is None and series.next_due is not None:
        appears = max(today(), series.next_due - timedelta(days=series.lead_days))
    return {
        "open_ticket_id": ticket.id if ticket else None,
        "appears_on": appears,
        "round_count": count or 0,
    }


# Changing


def _system_actor(session: Session) -> Actor:
    return Actor(user=session.get(User, AUTOMATION_USER_ID), via=Via.RECURRENCE)


def _sent_fields(body: SeriesCreate | SeriesPatch) -> set[str]:
    sent = set(body.model_fields_set)
    if "fields" in sent and body.fields is not None:
        sent.discard("fields")
        sent |= {f"fields.{name}" for name in body.fields.model_fields_set}
    return sent


def _type_fields(ticket_type: str, fields: SeriesTypeFields | None) -> dict[str, Any]:
    if fields is None:
        return {}
    values = fields.model_dump(mode="json", exclude_unset=True)
    wrong = [
        name for name in values if TicketType(ticket_type) not in TYPE_FIELDS[name]
    ]
    if wrong:
        raise services.Invalid(
            "field_not_for_type",
            f"Not for {ticket_type}: {', '.join(sorted(wrong))}.",
        )
    return {
        name: str(Decimal(value).quantize(Decimal("0.01")))
        if name in _AMOUNTS
        else value
        for name, value in values.items()
        if value is not None
    }


def _apply_template(
    session: Session, series: Series, body: SeriesCreate | SeriesPatch
) -> None:
    sent = body.model_fields_set
    for name in _TEMPLATE:
        if name in sent:
            value = getattr(body, name)
            if name == "title" and value is None:
                raise services.Invalid("title_required", "A series needs a title.")
            if name == "urgency" and value is None:
                raise services.Invalid("urgency_required", "Urgency cannot be empty.")
            setattr(
                series, name, "" if name == "description" and value is None else value
            )
    if "assignee_ids" in sent:
        series.assignee_ids = [
            u.ha_user_id for u in services._users(session, body.assignee_ids or [])
        ]
    if "checklist" in sent:
        series.checklist = list(body.checklist or [])
    if "fields" in sent:
        series.fields = {**series.fields, **_type_fields(series.type, body.fields)}
        series.fields = {k: v for k, v in series.fields.items() if v is not None}


def _announce(
    session: Session,
    actor: Actor,
    series: Series,
    action: str,
    ticket_id: int | None = None,
) -> None:
    services.emit(
        session,
        actor,
        EventType.SERIES_CHANGED,
        ticket_id,
        {"series_id": series.id, "action": action, "title": series.title},
    )


def create_series(session: Session, actor: Actor, body: SeriesCreate) -> Series:
    """Create a series from a ticket (its first round) or from a blank template."""
    sent = _sent_fields(body)
    ticket = (
        services.get_ticket(session, body.from_ticket_id)
        if body.from_ticket_id
        else None
    )
    if ticket is not None:
        perm.check_series(
            actor,
            ticket.requested_by_id,
            ticket.assignee_ids
            if "assignee_ids" not in sent
            else body.assignee_ids or [],
            sent - _RULE - {"from_ticket_id", "first_due", "type", "requested_by_id"},
        )
        if ticket.series_id is not None:
            raise services.Invalid("already_in_series", "This ticket already repeats.")
        series = Series(
            type=ticket.type,
            title=ticket.title,
            description=ticket.description or "",
            urgency=ticket.urgency,
            importance=ticket.importance,
            requested_by_id=ticket.requested_by_id,
            assignee_ids=ticket.assignee_ids,
            ha_area_id=ticket.ha_area_id,
            ha_device_id=ticket.ha_device_id,
            checklist=[
                item.text for item in sorted(ticket.checklist, key=lambda i: i.position)
            ],
            fields={
                name: services._json(getattr(ticket, name))
                for name in SERIES_TYPE_FIELDS
                if TicketType(ticket.type) in TYPE_FIELDS[name]
                and getattr(ticket, name) not in (None, [])
            },
        )
    else:
        if body.type is None or body.title is None:
            raise services.Invalid(
                "type_and_title_required", "A new series needs a type and a title."
            )
        if body.first_due is None:
            raise services.Invalid(
                "first_due_required", "A new series needs the first due date."
            )
        owner = services.get_user_or_invalid(
            session, body.requested_by_id or actor.user_id
        )
        perm.check_series(
            actor,
            body.requested_by_id,
            body.assignee_ids or [],
            sent - _RULE - {"type", "requested_by_id", "first_due"},
        )
        series = Series(
            type=body.type,
            title=body.title,
            description="",
            requested_by_id=owner.ha_user_id,
            assignee_ids=[],
            checklist=[],
            fields={},
        )
    series.mode = body.mode
    series.interval_count = body.interval_count
    series.interval_unit = body.interval_unit
    series.lead_days = 7 if body.lead_days is None else body.lead_days
    series.active = True
    _apply_template(session, series, body)
    session.add(series)
    session.flush()

    now = today()
    if ticket is not None:
        services.record(session, actor, ticket, "series_id", None, series.id)
        ticket.series_id = series.id
        closed = ticket.status in CLOSED_STATUSES
        if series.mode == RecurrenceMode.FIXED_CALENDAR:
            series.anchor = body.first_due or ticket.due_date or now
            after = series.anchor + timedelta(days=1)
            series.next_due = grid_on_or_after(
                series, max(after, now) if closed else after
            )
        else:
            series.next_due = (
                add_interval(
                    _local_day(ticket.closed_at),
                    series.interval_count,
                    series.interval_unit,
                )
                if closed
                else None
            )
    else:
        series.next_due = body.first_due
        if series.mode == RecurrenceMode.FIXED_CALENDAR:
            series.anchor = body.first_due
    _announce(session, actor, series, "created", ticket.id if ticket else None)
    run_series(session, series, now)
    return series


def update_series(
    session: Session, actor: Actor, series: Series, body: SeriesPatch
) -> Series:
    """Change template, rule, next date or pause/resume; future rounds only."""
    sent = _sent_fields(body)
    perm.check_series(
        actor,
        None,
        body.assignee_ids or [] if "assignee_ids" in sent else [],
        sent - _RULE - {"next_due", "active"},
        series_owner_id=series.requested_by_id,
    )
    _apply_template(session, series, body)
    for name in ("mode", "interval_count", "interval_unit", "lead_days"):
        if name in sent:
            value = getattr(body, name)
            if value is None:
                raise services.Invalid(f"{name}_required", f"{name} cannot be empty.")
            setattr(series, name, value)
    now = today()
    current = open_round(session, series)
    if "next_due" in sent and body.next_due is not None:
        series.next_due = body.next_due
        if series.mode == RecurrenceMode.FIXED_CALENDAR:
            series.anchor = body.next_due
    elif sent & {"mode", "interval_count", "interval_unit"}:
        if series.mode == RecurrenceMode.FIXED_CALENDAR:
            base = (current.due_date if current else None) or series.next_due or now
            series.anchor = series.anchor or base
            series.next_due = grid_on_or_after(
                series,
                max(now, base + timedelta(days=1)) if current else max(now, base),
            )
        else:
            series.anchor = None
            if current is not None:
                series.next_due = None
    if "active" in sent and body.active is not None:
        series.active = body.active
    action = "updated"
    if "active" in sent:
        action = "resumed" if series.active else "paused"
    _announce(session, actor, series, action)
    run_series(session, series, now)
    return series


def delete_series(session: Session, actor: Actor, series: Series) -> None:
    """End a series. Its tickets stay and lose the link."""
    perm.check_series(actor, None, [], (), series_owner_id=series.requested_by_id)
    _announce(session, actor, series, "deleted")
    for ticket in session.scalars(select(Ticket).where(Ticket.series_id == series.id)):
        ticket.series_id = None
    session.delete(series)


# Rounds


def round_closed(session: Session, ticket: Ticket) -> None:
    """A round was closed (done, dream on) or deleted: plan the next one."""
    if ticket.series_id is None:
        return
    series = session.get(Series, ticket.series_id)
    if series is None:
        return
    if series.mode == RecurrenceMode.AFTER_COMPLETION:
        series.next_due = add_interval(
            today(), series.interval_count, series.interval_unit
        )
    # The next round may already be within its lead time.
    run_series(session, series, today())


def _create_round(session: Session, series: Series, due: date) -> Ticket:
    actor = _system_actor(session)
    ticket = Ticket(
        type=series.type,
        title=series.title,
        description=series.description,
        urgency=series.urgency,
        importance=series.importance,
        due_date=due,
        requested_by_id=series.requested_by_id,
        created_by_id=actor.user_id,
        created_via=Via.RECURRENCE,
        status=Status.NEW,
        series_id=series.id,
        ha_area_id=series.ha_area_id,
        ha_device_id=series.ha_device_id,
        ha_targets=[],
    )
    for name, value in series.fields.items():
        if name in SERIES_TYPE_FIELDS and TicketType(series.type) in TYPE_FIELDS[name]:
            setattr(ticket, name, Decimal(value) if name in _AMOUNTS else value)
    ticket.assignees = [
        user
        for user_id in series.assignee_ids
        if (user := session.get(User, user_id)) is not None and user.active
    ]
    ticket.checklist = [
        ChecklistItem(text=text, position=index)
        for index, text in enumerate(series.checklist)
    ]
    session.add(ticket)
    session.flush()
    services.record(
        session,
        actor,
        ticket,
        "created",
        None,
        {"type": ticket.type, "series_id": series.id},
    )
    services.emit(
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
            "series_id": series.id,
            "due_date": due,
        },
    )
    services._update_approval(session, actor, ticket)
    return ticket


def run_series(session: Session, series: Series, day: date) -> Ticket | None:
    """Create the next round of one series if it is time."""
    if not series.active or series.next_due is None:
        return None
    if open_round(session, series) is not None:
        return None
    due = series.next_due
    if due < day:
        # Missed while paused or while the last round was open: no overdue rounds.
        due = (
            grid_on_or_after(series, day)
            if series.mode == RecurrenceMode.FIXED_CALENDAR
            else day
        )
        series.next_due = due
    if day < due - timedelta(days=series.lead_days):
        return None
    ticket = _create_round(session, series, due)
    if series.mode == RecurrenceMode.FIXED_CALENDAR:
        series.next_due = grid_on_or_after(series, due + timedelta(days=1))
    else:
        series.next_due = None
    return ticket


def run(session: Session, day: date | None = None) -> list[Ticket]:
    """Create every round that is due to appear. Each series is locked meanwhile."""
    day = day or today()
    created = []
    ids = session.scalars(
        select(Series.id).where(Series.active.is_(True), Series.next_due.is_not(None))
    ).all()
    for series_id in ids:
        series = session.get(Series, series_id, with_for_update=True)
        if series is None:
            continue
        ticket = run_series(session, series, day)
        if ticket is not None:
            created.append(ticket)
    return created
