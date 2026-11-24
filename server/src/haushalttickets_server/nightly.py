"""Nightly AI run (decisions 46-56): access token, work queue, results, watchdog.

The runner in the agent container authenticates with its own token. That token acts
as exactly one household member - the AI account ("KI-Hausmeister", role
``ai_agent``) - over the channel ``nightly_run``, so the AI rules always apply. The
AI itself never talks to this server: the runner fetches the work, hands each ticket
to the model and stores what comes back.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import hmac
import secrets
from typing import Any

from sqlalchemy import case, func, nulls_last, select
from sqlalchemy.orm import Session

from . import permissions as perm
from .enums import CLOSED_STATUSES, EventType, Role, RunOutcome, Status, TicketType, Via
from .models import AIDraft, ChangeLogEntry, Comment, NightlyRun, Setting, Ticket, User
from .permissions import Actor
from .schemas import NightlyResult
from .services import (
    Invalid,
    NotFound,
    _change_status,
    add_comment,
    clear_implement_approval,
    emit,
    get_user,
    hash_token,
    latest_draft_version,
    record,
)

SETTING_ACCESS = "nightly_access"
SETTING_WATCHDOG = "nightly_watchdog_alert"
# Tell the admins once a day while approved work waits longer than this for a run.
WATCHDOG_HOURS = 26
_URGENCY_ORDER = {"burning": 0, "this_week": 1, "someday": 2}


def _now() -> datetime:
    return datetime.now(UTC)


# Access token


def create_access(session: Session, user_name: str) -> tuple[User, str]:
    """Bind a new runner token to the AI account; returns the token once."""
    user = find_user(session, user_name)
    if user.role != Role.AI_AGENT:
        raise Invalid(
            "not_ai_agent",
            f"{user.display_name} has the role {user.role}; set it to ai_agent first "
            "(Home Assistant action haushalttickets.set_role).",
        )
    token = secrets.token_urlsafe(32)
    setting = session.get(Setting, SETTING_ACCESS)
    value = {
        "token_hash": hash_token(token),
        "user_id": user.ha_user_id,
        "created_at": _now().isoformat(),
        "last_used_at": None,
    }
    if setting is None:
        session.add(Setting(key=SETTING_ACCESS, value=value))
    else:
        setting.value = value
    return user, token


def revoke_access(session: Session) -> None:
    """Remove the runner token."""
    setting = session.get(Setting, SETTING_ACCESS)
    if setting is None:
        raise NotFound("No nightly run token")
    session.delete(setting)


def access(session: Session) -> dict[str, Any] | None:
    """Stored token binding, if any."""
    setting = session.get(Setting, SETTING_ACCESS)
    return dict(setting.value) if setting else None


def find_user(session: Session, name: str) -> User:
    """User by Home Assistant id or display name."""
    user = session.get(User, name)
    if user is None:
        users = session.scalars(select(User).where(User.display_name == name)).all()
        if len(users) != 1:
            raise Invalid("unknown_user", f"No single user named {name}.")
        user = users[0]
    return user


class AccessDenied(Exception):
    """The runner token is valid but its account may not act."""

    def __init__(self, code: str, message: str) -> None:
        """Initialize."""
        super().__init__(message)
        self.code = code
        self.message = message


def authenticate(session: Session, token: str) -> Actor | None:
    """Actor for a valid runner token, None for an unknown token."""
    setting = session.get(Setting, SETTING_ACCESS)
    if setting is None:
        return None
    if not hmac.compare_digest(setting.value["token_hash"], hash_token(token)):
        return None
    user = session.get(User, setting.value["user_id"])
    if user is None or not user.active:
        raise AccessDenied("unknown_user", "The AI account is unknown or inactive.")
    if user.role != Role.AI_AGENT:
        raise AccessDenied(
            "nightly_user_not_ai", "The AI account needs the role ai_agent."
        )
    setting.value = {**setting.value, "last_used_at": _now().isoformat()}
    return Actor(user=user, via=Via.NIGHTLY_RUN)


def require_runner(actor: Actor) -> None:
    """Only the runner token may call this."""
    if actor.via != Via.NIGHTLY_RUN:
        raise perm.PermissionDenied("nightly_only", "Only the nightly run may do this.")


# Work queue


def _priority(*columns: Any) -> list[Any]:
    urgency = case(_URGENCY_ORDER, value=Ticket.urgency, else_=3)
    return [urgency, nulls_last(Ticket.importance.desc()), Ticket.created_at, *columns]


def _open() -> Any:
    return Ticket.status.not_in([str(s) for s in CLOSED_STATUSES])


def work(session: Session) -> list[tuple[str, Ticket, AIDraft | None]]:
    """Tickets to work on: approved implementations first, then drafts.

    A ticket with a pending request for a new draft is drafted, not implemented.
    """
    implement = session.scalars(
        select(Ticket)
        .where(
            _open(),
            Ticket.status != Status.ACCEPTANCE,
            Ticket.type == TicketType.HOME_ASSISTANT,
            Ticket.ai_implement_approved.is_(True),
            Ticket.ai_implement_draft_version.is_not(None),
            Ticket.ai_draft_requested.is_(False),
        )
        .order_by(*_priority())
    ).all()
    drafts = session.scalars(
        select(Ticket)
        .where(_open(), Ticket.ai_draft_requested.is_(True))
        .order_by(*_priority())
    ).all()
    items: list[tuple[str, Ticket, AIDraft | None]] = []
    for ticket in implement:
        draft = get_draft(session, ticket.id, ticket.ai_implement_draft_version)
        if draft is not None:
            items.append(("implement", ticket, draft))
    for ticket in drafts:
        version = latest_draft_version(session, ticket)
        draft = get_draft(session, ticket.id, version) if version else None
        items.append(("draft", ticket, draft))
    return items


def pending_count(session: Session) -> int:
    """Tickets waiting for the nightly run."""
    return len(work(session))


def comments(session: Session, ticket: Ticket) -> list[Comment]:
    """Comments of a ticket, oldest first."""
    return list(
        session.scalars(
            select(Comment)
            .where(Comment.ticket_id == ticket.id)
            .order_by(Comment.created_at, Comment.id)
        )
    )


# Drafts and changes


def get_draft(session: Session, ticket_id: int, version: int | None) -> AIDraft | None:
    """One draft version."""
    if version is None:
        return None
    return session.scalar(
        select(AIDraft).where(
            AIDraft.ticket_id == ticket_id, AIDraft.version == version
        )
    )


def list_drafts(session: Session, ticket: Ticket) -> list[AIDraft]:
    """All drafts of a ticket, newest first."""
    return list(
        session.scalars(
            select(AIDraft)
            .where(AIDraft.ticket_id == ticket.id)
            .order_by(AIDraft.version.desc())
        )
    )


def list_changes(session: Session, ticket: Ticket) -> list[ChangeLogEntry]:
    """Changes to Home Assistant made for a ticket, newest first."""
    return list(
        session.scalars(
            select(ChangeLogEntry)
            .where(ChangeLogEntry.ticket_id == ticket.id)
            .order_by(ChangeLogEntry.created_at.desc(), ChangeLogEntry.id.desc())
        )
    )


# Runs


def start_run(session: Session, actor: Actor) -> NightlyRun:
    """Log the start of a run."""
    require_runner(actor)
    run = NightlyRun(tickets=[], notes="", results=[], usage={})
    session.add(run)
    session.flush()
    return run


def get_run(session: Session, run_id: int) -> NightlyRun:
    """One run."""
    run = session.get(NightlyRun, run_id)
    if run is None:
        raise NotFound(f"Run {run_id} not found")
    return run


def _open_run(session: Session, run_id: int) -> NightlyRun:
    run = get_run(session, run_id)
    if run.ended_at is not None:
        raise Invalid("run_ended", f"Run {run_id} has ended.")
    return run


def end_run(
    session: Session,
    actor: Actor,
    run: NightlyRun,
    outcome: str,
    notes: str,
    usage: dict[str, Any],
) -> NightlyRun:
    """Log the end of a run and announce its results."""
    require_runner(actor)
    if run.ended_at is not None:
        raise Invalid("run_ended", f"Run {run.id} has ended.")
    run.ended_at = _now()
    run.outcome = RunOutcome(outcome)
    run.notes = notes
    run.usage = usage
    if run.outcome != RunOutcome.NO_WORK or run.results:
        emit(
            session,
            actor,
            EventType.NIGHTLY_RUN_FINISHED,
            None,
            {
                "run_id": run.id,
                "outcome": run.outcome,
                "notes": notes[:500],
                "results": run.results,
            },
        )
    return run


def store_result(
    session: Session, actor: Actor, ticket: Ticket, body: NightlyResult
) -> dict[str, Any]:
    """Store what the AI did for one ticket and report it in a comment."""
    require_runner(actor)
    run = _open_run(session, body.run_id)
    if ticket.status in CLOSED_STATUSES:
        raise Invalid("ticket_closed", "The ticket was closed meanwhile.")
    result: dict[str, Any] = {
        "ticket_id": ticket.id,
        "title": ticket.title,
        "kind": body.kind,
        "requested_by_id": ticket.requested_by_id,
        "assignee_ids": ticket.assignee_ids,
    }
    changed: dict[str, Any] = {}
    if body.kind == "draft":
        result["version"] = _store_draft(session, actor, ticket, run, body, changed)
    elif body.kind == "implemented":
        result.update(_store_change(session, actor, ticket, run, body, changed))
    else:
        result["phase"] = body.phase
        result["retry"] = body.retry
        if not body.retry:
            _clear_request(session, actor, ticket, body.phase, changed)
    if changed:
        emit(session, actor, EventType.AI_FLAGS_CHANGED, ticket.id, changed)
    add_comment(session, actor, ticket, body.comment)
    if body.kind == "implemented":
        perm.check_status(actor, ticket, Status.ACCEPTANCE)
        _change_status(session, actor, ticket, Status.ACCEPTANCE)
    run.results = [*run.results, result]
    run.tickets = sorted({*run.tickets, ticket.id})
    return result


def _store_draft(
    session: Session,
    actor: Actor,
    ticket: Ticket,
    run: NightlyRun,
    body: NightlyResult,
    changed: dict[str, Any],
) -> int:
    if not ticket.ai_draft_requested:
        raise Invalid("draft_not_requested", "No draft is requested for this ticket.")
    if not body.draft:
        raise Invalid("draft_missing", "The draft text is missing.")
    version = (latest_draft_version(session, ticket) or 0) + 1
    session.add(
        AIDraft(ticket_id=ticket.id, version=version, body=body.draft, run_id=run.id)
    )
    record(session, actor, ticket, "ai_draft", None, version)
    record(session, actor, ticket, "ai_draft_requested", True, False)
    ticket.ai_draft_requested = False
    changed["ai_draft_requested"] = False
    # An approval refers to an older draft and lapses (decision 50).
    clear_implement_approval(session, actor, ticket, changed)
    ticket.updated_at = _now()
    emit(
        session,
        actor,
        EventType.DRAFT_CREATED,
        ticket.id,
        {"version": version, "title": ticket.title},
    )
    return version


def _store_change(
    session: Session,
    actor: Actor,
    ticket: Ticket,
    run: NightlyRun,
    body: NightlyResult,
    changed: dict[str, Any],
) -> dict[str, Any]:
    if not ticket.ai_implement_approved or ticket.ai_implement_draft_version is None:
        raise Invalid("not_approved", "Implementing this ticket is not approved.")
    if body.change is None:
        raise Invalid("change_missing", "The change summary is missing.")
    version = ticket.ai_implement_draft_version
    entry = ChangeLogEntry(
        ticket_id=ticket.id,
        run_id=run.id,
        summary=body.change.summary,
        objects=body.change.objects,
        git_commit=body.change.git_commit,
        needs_restart=body.change.needs_restart,
    )
    session.add(entry)
    session.flush()
    record(session, actor, ticket, "ai_change", None, entry.id)
    clear_implement_approval(session, actor, ticket, changed)
    emit(
        session,
        actor,
        EventType.CHANGE_LOGGED,
        ticket.id,
        {"change_id": entry.id, "title": ticket.title, "version": version},
    )
    return {"version": version, "needs_restart": body.change.needs_restart}


def _clear_request(
    session: Session,
    actor: Actor,
    ticket: Ticket,
    phase: str | None,
    changed: dict[str, Any],
) -> None:
    if phase == "draft" and ticket.ai_draft_requested:
        record(session, actor, ticket, "ai_draft_requested", True, False)
        ticket.ai_draft_requested = False
        changed["ai_draft_requested"] = False
    elif phase == "implement":
        clear_implement_approval(session, actor, ticket, changed)


# Watchdog


def check_watchdog(session: Session, now: datetime | None = None) -> bool:
    """Announce once a day that approved work waits but no run started.

    Returns True when it emitted an event.
    """
    now = now or _now()
    binding = access(session)
    if binding is None:
        return False
    last_run = session.scalar(select(func.max(NightlyRun.started_at)))
    since = last_run or datetime.fromisoformat(binding["created_at"])
    if now - since < timedelta(hours=WATCHDOG_HOURS):
        return False
    alert = session.get(Setting, SETTING_WATCHDOG)
    if alert is not None and now - datetime.fromisoformat(alert.value) < timedelta(
        hours=24
    ):
        return False
    pending = pending_count(session)
    if not pending:
        return False
    emit(
        session,
        Actor(user=None, via=Via.SYSTEM),
        EventType.NIGHTLY_RUN_MISSING,
        None,
        {
            "last_run_at": last_run,
            "pending": pending,
            "hours": WATCHDOG_HOURS,
        },
    )
    if alert is None:
        session.add(Setting(key=SETTING_WATCHDOG, value=now.isoformat()))
    else:
        alert.value = now.isoformat()
    return True


def status(session: Session) -> dict[str, Any]:
    """Overview for the command line."""
    binding = access(session)
    last = session.scalar(select(NightlyRun).order_by(NightlyRun.started_at.desc()))
    user = None
    if binding:
        try:
            user = get_user(session, binding["user_id"])
        except NotFound:
            user = None
    return {
        "token": bool(binding),
        "user": user.display_name if user else None,
        "role": user.role if user else None,
        "token_created_at": binding["created_at"] if binding else None,
        "token_last_used_at": binding["last_used_at"] if binding else None,
        "last_run": (
            {
                "id": last.id,
                "started_at": last.started_at.isoformat(),
                "ended_at": last.ended_at.isoformat() if last.ended_at else None,
                "outcome": last.outcome,
            }
            if last
            else None
        ),
        "pending": pending_count(session),
    }
