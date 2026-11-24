# Architecture and decisions

Decided on 2026-11-23. Changes to this list are made by pull request.

## Scope

- Ticket types: **repair**, **purchase** (acquisitions, not groceries), **home_assistant**.
- Users: the household's Home Assistant users. A child account joins later.
- Remote access: Home Assistant via Nabu Casa only. The LXC is not exposed.
- AI clients: claude.ai first, ChatGPT secondary.

## Components

### Server (`server/`)
- Python, FastAPI, PostgreSQL in the same unprivileged Debian LXC, run by systemd.
  Dependencies are locked with uv (`uv.lock`) and exported with hashes to
  `server/requirements.txt`; the container installs them with pip into a venv, so uv
  itself is not needed there. An update script installs tagged GitHub releases
  (see [deploy/README.md](../deploy/README.md)).
- Only the configured clients (normally Home Assistant) may call the API
  (`HAUSHALTTICKETS_ALLOWED_CLIENTS`), in addition to the bearer token.
- Attachments are stored inside the LXC next to the database.
- Backups: the existing Proxmox vzdump schedule, plus a nightly `pg_dump` that runs
  before the vzdump window so every backup contains a consistent dump.
- Emergency web UI for when Home Assistant is down: HTTPS on its own port (8443),
  only for the listed networks (home network, NetBird), a single local admin login.
  Self-signed certificate or an own one uploaded in the UI; a ticket reminds 14 days
  before an own certificate expires. See [server/README.md](../server/README.md).
- Nightly AI run (Claude Code) at 03:00 via systemd timer.

### Integration (`custom_components/haushalttickets/`)
- Config flow: server URL + API token (reauth and reconfigure supported).
- No to-do entities: the server is the only ticket store. The integration provides
  sensors (household-wide and per person), the bus event `haushalttickets_event`,
  actions and WebSocket commands for the card, see [integration.md](integration.md).
- Home Assistant users are synced to the server; automations and scripts without a
  user act as the server user **Automation** (channel `automation`), which may file,
  comment and close its own tickets but never approve, set AI flags, delete or
  administrate.
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
- Per ticket: **draft** (propose only, all types) and **implement** (Home Assistant
  work only). Implementing needs a draft; the approval refers to exactly that draft
  version. Asking for a new draft, or a new draft arriving, withdraws it.
- Runs in its **own container** (`agent/`, `deploy/agent/`), not next to the ticket
  database: Claude Code needs ~4 GB RAM, and an AI with a shell next to the database
  could bypass the rules. A systemd timer starts it at 03:00; a missed night is not
  made up during the day. Time limit 2 h; no limit on the number of tickets.
- A plain runner, not the AI, talks to the ticket server with a **runner token**
  bound to the AI account (role `ai_agent`, channel `nightly_run`): it reads the work
  and stores drafts, change log entries, comments and status. The AI gets the ticket
  as text and returns a structured answer.
- Draft phase: Home Assistant read-only (ha-mcp) plus web. Implementation phase:
  Home Assistant writes, no web (prompt injection from web pages cannot reach the
  writing phase). No shell, no local files in either phase. Allowed tools per phase
  plus a guard hook in Claude Code's managed settings that checks every writing call.
- Implementation may create and change automations, scripts, scenes, helpers and
  dashboards, assign areas/labels/categories, and write whole top-level YAML keys in
  `packages/*.yaml` and `configuration.yaml` (not `homeassistant:`, no `!include`).
  Never delete, rename entity IDs, call services, reload or restart.
- Before and after every change the runner exports the configuration (YAML files,
  dashboards, helpers, labels) to a **private** Git repository (never this one, never
  `secrets.yaml`); the commit goes into the ticket's change log. If Home Assistant's
  configuration check fails after a YAML change, the old keys are put back.
- The run uses the Home Assistant account "KI-Hausmeister" (admin, its own token), so
  its changes are distinguishable and its token can be revoked. Home Assistant admins
  start as ticket admins, so this account must be set to the role `ai_agent`
  (action `haushalttickets.set_role`) right after it is created; the runner token
  refuses to work otherwise.
- Reporting: no single pushes for the run's actions; one summary per person when it
  ends, problems to the admins; a watchdog event when approved work waits 26 h
  without a run.

## Known trade-offs

- The MCP endpoint requires an administrator login; the restriction to ticket tools
  applies at tool level, not to the Home Assistant token itself.
- If Home Assistant is down, MCP access is down as well. The emergency UI covers
  manual use only.
- The integration acts for the Home Assistant user in the call context. An AI that
  controls Home Assistant with a person's token (for example through ha-mcp) therefore
  acts as that person through the channel `ui`; only the KI-Hausmeister account and
  the MCP tools of the integration are bound to the AI rules.
- The integration polls the event feed every 10 seconds. Events that happened while
  Home Assistant was down are not replayed after a restart.
