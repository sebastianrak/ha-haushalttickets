"""REST API v1.

Clients authenticate with a bearer token (``haushalttickets-server token create``).
The Home Assistant integration is trusted to name the acting household member in
``X-Acting-User`` (Home Assistant user id) and the channel in ``X-Acting-Via``
(``ui``, ``mcp``, ...). Requests without ``X-Acting-User`` act as the integration
itself and may only read and sync users.
"""

from __future__ import annotations

from collections.abc import Iterator
import contextlib
from pathlib import Path
from typing import Annotated

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    Header,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
    status,
)
from fastapi.responses import FileResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from . import services
from .config import Settings
from .enums import AttachmentKind, Via
from .models import User
from .permissions import Actor, check_admin
from .schemas import (
    AIFlags,
    ApprovalDecision,
    AssigneesSet,
    AttachmentOut,
    ChecklistCreate,
    ChecklistItemOut,
    ChecklistPatch,
    CommentCreate,
    CommentOut,
    EventList,
    EventOut,
    HistoryOut,
    SettingsOut,
    SettingsPatch,
    StatusChange,
    TicketCreate,
    TicketList,
    TicketOut,
    TicketPatch,
    UserOut,
    UserPatch,
    UserSync,
)

router = APIRouter(prefix="/api/v1")
bearer = HTTPBearer(auto_error=False)


def get_settings(request: Request) -> Settings:
    """Application settings."""
    return request.app.state.settings


def get_session(request: Request) -> Iterator[Session]:
    """Database session; endpoints commit explicitly."""
    session: Session = request.app.state.sessionmaker()
    try:
        yield session
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


SessionDep = Annotated[Session, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_settings)]


def get_actor(
    session: SessionDep,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    x_acting_user: Annotated[str | None, Header()] = None,
    x_acting_via: Annotated[str | None, Header()] = None,
) -> Actor:
    """Authenticate the client and resolve the acting user."""
    if (
        credentials is None
        or services.authenticate(session, credentials.credentials) is None
    ):
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            detail={"code": "invalid_token", "detail": "Missing or invalid token."},
            headers={"WWW-Authenticate": "Bearer"},
        )
    session.commit()  # last_used_at

    if x_acting_user is None:
        return Actor(user=None, via=Via.SYSTEM)

    try:
        via = Via(x_acting_via or Via.UI)
    except ValueError:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail={"code": "invalid_via", "detail": f"Unknown via {x_acting_via}."},
        ) from None
    if via == Via.SYSTEM:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail={"code": "invalid_via", "detail": "via=system needs no user."},
        )
    user = session.get(User, x_acting_user)
    if user is None or not user.active:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            detail={"code": "unknown_user", "detail": "Unknown or inactive user."},
        )
    return Actor(user=user, via=via)


ActorDep = Annotated[Actor, Depends(get_actor)]


def _ticket_out(ticket) -> TicketOut:
    return TicketOut.model_validate(ticket)


# Users


@router.get("/users")
def list_users(session: SessionDep, actor: ActorDep) -> list[UserOut]:
    """All users."""
    users = session.query(User).order_by(User.display_name).all()
    return [UserOut.model_validate(user) for user in users]


@router.post("/users/sync")
def sync_users(session: SessionDep, actor: ActorDep, body: UserSync) -> list[UserOut]:
    """Create or update users from Home Assistant (integration or admin)."""
    if not actor.is_system:
        check_admin(actor)
    users = services.sync_users(session, body.users)
    session.commit()
    return [UserOut.model_validate(user) for user in users]


@router.patch("/users/{user_id}")
def patch_user(
    session: SessionDep, actor: ActorDep, user_id: str, body: UserPatch
) -> UserOut:
    """Change the role of a user (admins)."""
    user = services.get_user(session, user_id)
    services.set_role(session, actor, user, body.role)
    session.commit()
    return UserOut.model_validate(user)


# Settings


@router.get("/settings")
def get_server_settings(session: SessionDep, actor: ActorDep) -> SettingsOut:
    """Runtime settings."""
    return SettingsOut(
        purchase_approval_threshold=services.get_approval_threshold(session)
    )


@router.patch("/settings")
def patch_server_settings(
    session: SessionDep, actor: ActorDep, body: SettingsPatch
) -> SettingsOut:
    """Change runtime settings (admins)."""
    if body.purchase_approval_threshold is not None:
        services.set_approval_threshold(
            session, actor, body.purchase_approval_threshold
        )
    session.commit()
    return get_server_settings(session, actor)


# Tickets


@router.get("/tickets")
def list_tickets(
    session: SessionDep,
    actor: ActorDep,
    status_: Annotated[list[str] | None, Query(alias="status")] = None,
    type_: Annotated[str | None, Query(alias="type")] = None,
    assignee: str | None = None,
    requested_by: str | None = None,
    q: Annotated[str | None, Query(max_length=200)] = None,
    include_closed: bool = True,
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> TicketList:
    """Tickets, newest first."""
    total, tickets = services.list_tickets(
        session,
        status=status_,
        type_=type_,
        assignee_id=assignee,
        requested_by_id=requested_by,
        query=q,
        include_closed=include_closed,
        limit=limit,
        offset=offset,
    )
    return TicketList(count=total, tickets=[_ticket_out(t) for t in tickets])


@router.post("/tickets", status_code=status.HTTP_201_CREATED)
def create_ticket(
    session: SessionDep, actor: ActorDep, body: TicketCreate
) -> TicketOut:
    """Create a ticket."""
    ticket = services.create_ticket(session, actor, body)
    session.commit()
    return _ticket_out(ticket)


@router.get("/tickets/{ticket_id}")
def get_ticket(session: SessionDep, actor: ActorDep, ticket_id: int) -> TicketOut:
    """One ticket."""
    return _ticket_out(services.get_ticket(session, ticket_id))


@router.patch("/tickets/{ticket_id}")
def patch_ticket(
    session: SessionDep, actor: ActorDep, ticket_id: int, body: TicketPatch
) -> TicketOut:
    """Change fields."""
    ticket = services.update_ticket(
        session, actor, services.get_ticket(session, ticket_id), body
    )
    session.commit()
    return _ticket_out(ticket)


@router.delete("/tickets/{ticket_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_ticket(session: SessionDep, actor: ActorDep, ticket_id: int) -> Response:
    """Delete a ticket (admins)."""
    files = services.delete_ticket(
        session, actor, services.get_ticket(session, ticket_id)
    )
    session.commit()
    for path in files:
        path.unlink(missing_ok=True)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/tickets/{ticket_id}/status")
def change_status(
    session: SessionDep, actor: ActorDep, ticket_id: int, body: StatusChange
) -> TicketOut:
    """Move to another column."""
    ticket = services.set_status(
        session, actor, services.get_ticket(session, ticket_id), body.status
    )
    session.commit()
    return _ticket_out(ticket)


@router.put("/tickets/{ticket_id}/assignees")
def put_assignees(
    session: SessionDep, actor: ActorDep, ticket_id: int, body: AssigneesSet
) -> TicketOut:
    """Replace the assignees."""
    ticket = services.set_assignees(
        session, actor, services.get_ticket(session, ticket_id), body.user_ids
    )
    session.commit()
    return _ticket_out(ticket)


@router.post("/tickets/{ticket_id}/approval")
def decide_approval(
    session: SessionDep, actor: ActorDep, ticket_id: int, body: ApprovalDecision
) -> TicketOut:
    """Approve or decline a purchase."""
    ticket = services.decide_approval(
        session,
        actor,
        services.get_ticket(session, ticket_id),
        body.decision == "approve",
    )
    session.commit()
    return _ticket_out(ticket)


@router.post("/tickets/{ticket_id}/ai-flags")
def set_ai_flags(
    session: SessionDep, actor: ActorDep, ticket_id: int, body: AIFlags
) -> TicketOut:
    """Set "AI should draft" / "AI should implement"."""
    ticket = services.set_ai_flags(
        session,
        actor,
        services.get_ticket(session, ticket_id),
        body.draft_requested,
        body.implement_approved,
    )
    session.commit()
    return _ticket_out(ticket)


# Comments


@router.get("/tickets/{ticket_id}/comments")
def list_comments(
    session: SessionDep, actor: ActorDep, ticket_id: int
) -> list[CommentOut]:
    """Comments, oldest first."""
    ticket = services.get_ticket(session, ticket_id)
    return [
        CommentOut.model_validate(c) for c in services.list_comments(session, ticket)
    ]


@router.post("/tickets/{ticket_id}/comments", status_code=status.HTTP_201_CREATED)
def add_comment(
    session: SessionDep, actor: ActorDep, ticket_id: int, body: CommentCreate
) -> CommentOut:
    """Add a comment."""
    comment = services.add_comment(
        session, actor, services.get_ticket(session, ticket_id), body.body
    )
    session.commit()
    return CommentOut.model_validate(comment)


# Checklist


@router.post("/tickets/{ticket_id}/checklist", status_code=status.HTTP_201_CREATED)
def add_checklist_item(
    session: SessionDep, actor: ActorDep, ticket_id: int, body: ChecklistCreate
) -> ChecklistItemOut:
    """Add a checklist item."""
    item = services.add_checklist_item(
        session,
        actor,
        services.get_ticket(session, ticket_id),
        body.text,
        body.position,
    )
    session.commit()
    return ChecklistItemOut.model_validate(item)


@router.patch("/tickets/{ticket_id}/checklist/{item_id}")
def patch_checklist_item(
    session: SessionDep,
    actor: ActorDep,
    ticket_id: int,
    item_id: int,
    body: ChecklistPatch,
) -> ChecklistItemOut:
    """Change a checklist item."""
    item = services.update_checklist_item(
        session,
        actor,
        services.get_ticket(session, ticket_id),
        item_id,
        text=body.text,
        done=body.done,
        position=body.position,
    )
    session.commit()
    return ChecklistItemOut.model_validate(item)


@router.delete(
    "/tickets/{ticket_id}/checklist/{item_id}", status_code=status.HTTP_204_NO_CONTENT
)
def delete_checklist_item(
    session: SessionDep, actor: ActorDep, ticket_id: int, item_id: int
) -> Response:
    """Remove a checklist item."""
    services.delete_checklist_item(
        session, actor, services.get_ticket(session, ticket_id), item_id
    )
    session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# History


@router.get("/tickets/{ticket_id}/history")
def list_history(
    session: SessionDep, actor: ActorDep, ticket_id: int
) -> list[HistoryOut]:
    """Recorded changes, oldest first."""
    ticket = services.get_ticket(session, ticket_id)
    return [
        HistoryOut.model_validate(h) for h in services.list_history(session, ticket)
    ]


# Attachments


@router.get("/tickets/{ticket_id}/attachments")
def list_attachments(
    session: SessionDep, actor: ActorDep, ticket_id: int
) -> list[AttachmentOut]:
    """Attachments of a ticket."""
    ticket = services.get_ticket(session, ticket_id)
    return [
        AttachmentOut.model_validate(a)
        for a in services.list_attachments(session, ticket)
    ]


@router.post("/tickets/{ticket_id}/attachments", status_code=status.HTTP_201_CREATED)
def upload_attachment(
    session: SessionDep,
    settings: SettingsDep,
    actor: ActorDep,
    ticket_id: int,
    file: Annotated[UploadFile, File()],
    kind: Annotated[AttachmentKind, Form()] = AttachmentKind.PHOTO,
) -> AttachmentOut:
    """Upload a photo, invoice or document."""
    ticket = services.get_ticket(session, ticket_id)
    content_type = (file.content_type or "").split(";")[0].strip().lower()
    if content_type not in settings.allowed_attachment_types:
        raise HTTPException(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail={"code": "unsupported_type", "detail": content_type or "unknown"},
        )
    data = file.file.read(settings.max_attachment_bytes + 1)
    if len(data) > settings.max_attachment_bytes:
        raise HTTPException(
            413,
            detail={"code": "too_large", "detail": "File too large."},
        )
    attachment = services.add_attachment(
        session,
        actor,
        ticket,
        kind=kind,
        filename=file.filename or "file",
        content_type=content_type,
        data=data,
        attachments_dir=settings.attachments_dir,
    )
    try:
        session.commit()
    except Exception:
        with contextlib.suppress(OSError):
            Path(attachment.storage_path).unlink()
        raise
    return AttachmentOut.model_validate(attachment)


@router.get("/attachments/{attachment_id}", response_class=FileResponse)
def download_attachment(
    session: SessionDep, actor: ActorDep, attachment_id: str
) -> FileResponse:
    """Download a file."""
    attachment = services.get_attachment(session, attachment_id)
    return FileResponse(
        attachment.storage_path,
        media_type=attachment.content_type,
        filename=attachment.filename,
    )


@router.delete("/attachments/{attachment_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_attachment(
    session: SessionDep, actor: ActorDep, attachment_id: str
) -> Response:
    """Remove a file."""
    path = services.delete_attachment(
        session, actor, services.get_attachment(session, attachment_id)
    )
    session.commit()
    path.unlink(missing_ok=True)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# Events


@router.get("/events")
def list_events(
    session: SessionDep,
    actor: ActorDep,
    after: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=1000)] = 200,
) -> EventList:
    """Event feed for the integration, oldest first."""
    events = services.list_events(session, after, limit)
    return EventList(
        last_seq=services.last_event_seq(session),
        events=[EventOut.model_validate(e) for e in events],
    )
