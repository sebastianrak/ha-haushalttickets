# haushalttickets-server

FastAPI service holding tickets, attachments and history. Runs in an unprivileged
Debian LXC next to PostgreSQL.

## Commands

```bash
haushalttickets-server migrate               # apply database migrations
haushalttickets-server recur                 # create recurring tickets that are due now
haushalttickets-server token create NAME     # create an API token (printed once)
haushalttickets-server token revoke NAME
haushalttickets-server                       # run the API (same as "serve")
```

Settings come from environment variables with the prefix `HAUSHALTTICKETS_`:

| Variable | Default |
|---|---|
| `HAUSHALTTICKETS_DATABASE_URL` | `postgresql+psycopg:///haushalttickets` (local socket, peer auth) |
| `HAUSHALTTICKETS_HOST` / `_PORT` | `127.0.0.1` / `8080` |
| `HAUSHALTTICKETS_ATTACHMENTS_DIR` | `/var/lib/haushalttickets/attachments` |
| `HAUSHALTTICKETS_MAX_ATTACHMENT_BYTES` | 20 MiB |
| `HAUSHALTTICKETS_TIMEZONE` | `Europe/Berlin` (days of recurring tickets) |
| `HAUSHALTTICKETS_RECURRENCE_MINUTES` | `15` (how often the server creates due rounds; `0` = only on changes) |

## API

All endpoints except `/api/health` need `Authorization: Bearer <token>`.

The Home Assistant integration names the acting household member:

| Header | Meaning |
|---|---|
| `X-Acting-User` | Home Assistant user id. Without it the request acts as the integration itself (read and user sync only). |
| `X-Acting-Via` | `ui` (default), `mcp`, `nightly_run`, `emergency_ui`, `automation`. `mcp` and `nightly_run` always follow the AI rules. `recurrence` is reserved for the server itself. |

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/health` | Liveness |
| GET | `/api/v1/users` | Users |
| POST | `/api/v1/users/sync` | Create/update users from Home Assistant (new non-admins start as `child`) |
| PATCH | `/api/v1/users/{id}` | Change role (admins) |
| GET, PATCH | `/api/v1/settings` | Approval threshold (change: admins) |
| GET, POST | `/api/v1/tickets` | List (filters: `status`, `type`, `assignee`, `requested_by`, `q`, `include_closed`), create |
| GET, PATCH, DELETE | `/api/v1/tickets/{id}` | Read, edit fields, delete (admins) |
| POST | `/api/v1/tickets/{id}/status` | Move to another column |
| PUT | `/api/v1/tickets/{id}/assignees` | Replace assignees |
| POST | `/api/v1/tickets/{id}/approval` | Approve/decline a purchase |
| POST | `/api/v1/tickets/{id}/ai-flags` | "AI should draft" / "AI should implement" |
| GET, POST | `/api/v1/tickets/{id}/comments` | Comments |
| POST, PATCH, DELETE | `/api/v1/tickets/{id}/checklist[/{item}]` | Checklist |
| GET | `/api/v1/tickets/{id}/history` | Recorded changes |
| GET, POST | `/api/v1/tickets/{id}/attachments` | Files (multipart `file`, `kind`) |
| GET, DELETE | `/api/v1/attachments/{id}` | Download, remove |
| GET, POST | `/api/v1/series` | Recurring series: list, create (from a ticket or blank) |
| GET, PATCH, DELETE | `/api/v1/series/{id}` | Read, change/pause/resume, end (its tickets stay) |
| GET | `/api/v1/events?after=SEQ` | Event feed for notifications and refresh |

Errors return `{"detail": {"code": "...", "detail": "..."}}` with stable codes such as
`acceptance_by_requester`, `approval_pending` or `ai_status_forbidden`.

Interactive documentation: `/docs` on the running server.

## Tests

The tests need PostgreSQL; the configured database is wiped.

```bash
export HAUSHALTTICKETS_TEST_DATABASE_URL=postgresql+psycopg://user:pass@127.0.0.1/hht_test
uv run --extra test pytest
```
