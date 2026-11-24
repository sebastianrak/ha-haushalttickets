# haushalttickets-server

FastAPI service holding tickets, attachments and history. Runs in an unprivileged
Debian LXC next to PostgreSQL.

## Commands

```bash
haushalttickets-server migrate               # apply database migrations
haushalttickets-server recur                 # create recurring tickets that are due now
haushalttickets-server token create NAME     # create an API token (printed once)
haushalttickets-server token revoke NAME
haushalttickets-server emergency password [--user NAME]  # emergency UI login
haushalttickets-server emergency cert [--renew] [--remove-custom]
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
| `HAUSHALTTICKETS_EMERGENCY_CLIENTS` | empty = emergency UI off; otherwise the addresses or networks that may open it |
| `HAUSHALTTICKETS_EMERGENCY_PORT` | `8443` (HTTPS) |
| `HAUSHALTTICKETS_TLS_DIR` | `/var/lib/haushalttickets/tls` (certificates of the emergency UI) |
| `HAUSHALTTICKETS_LANGUAGE` | `de` (tickets the server files itself, e.g. "renew the certificate") |

## Emergency UI

A small HTTPS site for when Home Assistant is down, on its own port next to the API
(same process). It is started only when `HAUSHALTTICKETS_EMERGENCY_CLIENTS` is set and
answers only those networks. One login, bound to an admin
(`emergency password`); every change runs as that admin through the channel
`emergency_ui`, so the normal rules and the history apply. Pages: ticket list with
search, ticket details (fields, checklist, photos, comments, history), new ticket,
comment, status change, certificate. No approvals, assignments, uploads or series.

- Password: scrypt hash in the database; five wrong passwords lock the login for 15
  minutes; sessions last 12 hours (signed cookie, `Secure`, `HttpOnly`,
  `SameSite=Strict`); a new password ends all sessions. Forms carry a token and must
  come from the same origin; no JavaScript, strict Content Security Policy, no HSTS
  (it would stop browsers from letting you past a self-signed certificate).
- Certificate: self-signed (EC P-256, 820 days, names: host name, `localhost`,
  `127.0.0.1` and the container address), renewed automatically 60 days before it
  expires. An own certificate (for example from your own CA in xca) can be uploaded on
  the page "Certificate", as PKCS#12 or as PEM certificate (chain) plus key, also
  encrypted; it is used from the next connection on and never changed by the server.
  The page warns about certificates iPhone/macOS refuse: valid longer than 825 days,
  no subjectAltName, extended key usage without serverAuth.
- Expiry: 14 days before an own certificate expires the server files a ticket
  "renew the certificate" (as **Automation**, requested by and assigned to the admin
  of the login, due on the expiry date) once per certificate. Uploading the next
  certificate comments on that ticket and moves it to review. Checked at start and
  every 6 hours.

## API

All endpoints except `/api/health` need `Authorization: Bearer <token>`.

The Home Assistant integration names the acting household member:

| Header | Meaning |
|---|---|
| `X-Acting-User` | Home Assistant user id. Without it the request acts as the integration itself (read and user sync only). |
| `X-Acting-Via` | `ui` (default), `mcp`, `nightly_run`, `automation`. `mcp` and `nightly_run` always follow the AI rules. `recurrence` and `emergency_ui` are reserved for the server itself. |

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
