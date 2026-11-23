# Architecture and decisions

Decided on 2026-11-23. Changes to this list are made by pull request.

## Scope

- Ticket types: **repair**, **purchase** (acquisitions, not groceries), **home_assistant**.
- Users: the household's Home Assistant users. A child account joins later.
- Remote access: Home Assistant via Nabu Casa only. The LXC is not exposed.
- AI clients: claude.ai first, ChatGPT secondary.

## Components

### Server (`server/`)
- Python, FastAPI, PostgreSQL in the same unprivileged Debian LXC, run by systemd,
  installed with uv. An update script installs tagged GitHub releases.
- Attachments are stored inside the LXC next to the database.
- Backups: the existing Proxmox vzdump schedule, plus a nightly `pg_dump` that runs
  before the vzdump window so every backup contains a consistent dump.
- Emergency web UI for when Home Assistant is down: internal network only, a single
  local admin login.
- Nightly AI run (Claude Code) at 03:00 via systemd timer.

### Integration (`custom_components/haushalttickets/`)
- Config flow: server URL + API token.
- One to-do entity per ticket type, sensors (open, burning, overdue, waiting for
  acceptance), events, actions.
- Photo upload and display proxied through Home Assistant to the server.
- Registers an LLM API; Home Assistant's `mcp_server` exposes it at
  `/api/mcp/haushalttickets`. Home Assistant handles OAuth; the endpoint requires an
  administrator account. Tool calls carry the calling Home Assistant user, which is
  recorded in the ticket history as "via AI".
- Ships the Kanban card (based on johnkily/ha-blueprints, MIT – keep its copyright
  notice when code is reused).
- Notifications: Home Assistant companion app push and Telegram.

## Workflow

Statuses: `new`, `discussing`, `in_progress`, `waiting_for_parts`, `acceptance`,
`done`, `rejected`.

Fields include priority (someday / this week / burning), importance (hearts), due date,
assignees, requested by, comments, history, attachments, checklist, link to Home
Assistant area/device/entity, recurrence.

### Roles

| Role | Create | Comment | Assign | Accept own | AI approvals | Delete |
|---|---|---|---|---|---|---|
| Admin | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| Adult | ✓ | ✓ | ✓ | ✓ | ✓ | – |
| Child | ✓ | ✓ | – | ✓ | – | – |
| AI agent | ✓ | ✓ | – | – | – | – |

The AI agent may move a ticket at most to `acceptance`. It never sets `done`, never
rejects, never deletes.

### AI approvals and nightly run
- Per ticket: **draft** (propose only) and **implement** (build after explicit approval).
- 03:00 daily. A pre-check exits immediately unless approved tickets wait and the
  Claude quota is sufficient.
- Before any implementation the Home Assistant configuration is committed to a
  **private** Git repository (never this one, never `secrets.yaml`). Only targeted
  changes, never whole-file overwrites. Afterwards the ticket moves to `acceptance`.
- The run uses a dedicated Home Assistant account "KI-Hausmeister" (admin), so its
  changes are distinguishable and its tokens can be revoked.

## Known trade-offs

- The MCP endpoint requires an administrator login; the restriction to ticket tools
  applies at tool level, not to the Home Assistant token itself.
- If Home Assistant is down, MCP access is down as well. The emergency UI covers
  manual use only.
