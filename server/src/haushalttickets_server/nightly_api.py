"""REST endpoints of the nightly run (runner token) and its drafts/changes (all)."""

from __future__ import annotations

from fastapi import APIRouter, status

from . import nightly, services
from .api import ActorDep, SessionDep, _ticket_out, _tickets_out
from .schemas import (
    ChangeOut,
    CommentOut,
    DraftOut,
    NightlyResult,
    NightlyWork,
    NightlyWorkItem,
    RunEnd,
    RunOut,
)

router = APIRouter(prefix="/api/v1")


# Everyone: what the AI proposed and changed


@router.get("/tickets/{ticket_id}/drafts")
def list_drafts(session: SessionDep, actor: ActorDep, ticket_id: int) -> list[DraftOut]:
    """AI drafts of a ticket, newest first."""
    ticket = services.get_ticket(session, ticket_id)
    return [DraftOut.model_validate(d) for d in nightly.list_drafts(session, ticket)]


@router.get("/tickets/{ticket_id}/changes")
def list_changes(
    session: SessionDep, actor: ActorDep, ticket_id: int
) -> list[ChangeOut]:
    """Changes the nightly run made to Home Assistant for a ticket, newest first."""
    ticket = services.get_ticket(session, ticket_id)
    return [ChangeOut.model_validate(c) for c in nightly.list_changes(session, ticket)]


# Runner only


@router.get("/nightly/work")
def get_work(session: SessionDep, actor: ActorDep) -> NightlyWork:
    """Approved implementations first, then requested drafts."""
    nightly.require_runner(actor)
    items = nightly.work(session)
    tickets = _tickets_out(session, [ticket for _, ticket, _ in items])
    return NightlyWork(
        items=[
            NightlyWorkItem(
                kind=kind,
                ticket=out,
                comments=[
                    CommentOut.model_validate(c)
                    for c in nightly.comments(session, ticket)
                ],
                draft=DraftOut.model_validate(draft) if draft else None,
            )
            for (kind, ticket, draft), out in zip(items, tickets, strict=True)
        ]
    )


@router.post("/nightly/runs", status_code=status.HTTP_201_CREATED)
def start_run(session: SessionDep, actor: ActorDep) -> RunOut:
    """Log the start of a run."""
    run = nightly.start_run(session, actor)
    session.commit()
    return RunOut.model_validate(run)


@router.patch("/nightly/runs/{run_id}")
def end_run(session: SessionDep, actor: ActorDep, run_id: int, body: RunEnd) -> RunOut:
    """Log the end of a run; announces the results."""
    nightly.require_runner(actor)
    run = nightly.end_run(
        session,
        actor,
        nightly.get_run(session, run_id),
        body.outcome,
        body.notes,
        body.usage,
    )
    session.commit()
    return RunOut.model_validate(run)


@router.post("/nightly/tickets/{ticket_id}/result")
def store_result(
    session: SessionDep, actor: ActorDep, ticket_id: int, body: NightlyResult
) -> dict:
    """Store a draft, a change or a failure for one ticket."""
    nightly.require_runner(actor)
    ticket = services.get_ticket(session, ticket_id)
    result = nightly.store_result(session, actor, ticket, body)
    session.commit()
    return {"result": result, "ticket": _ticket_out(session, ticket)}
