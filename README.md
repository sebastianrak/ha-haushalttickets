# Haushalttickets

A ticket system for a household – repairs, purchases and work on Home Assistant itself –
that lives in Home Assistant, with AI access over MCP and an optional nightly AI run.

> **Status: alpha.** In use in one household. All parts below work; expect changes
> between versions. Releases are published from `main`, see [Releases](#releases).

## Idea

Family members collect what needs doing on one Kanban board inside Home Assistant.
An AI assistant (Claude, optionally ChatGPT) can read tickets, create new ones and
comment through MCP. For Home Assistant tickets it may draft proposals and – only
after explicit approval per ticket – implement them during a nightly run. The person
who requested a ticket always does the final acceptance.

Inspired by the ticket board of [johnkily/ha-blueprints](https://github.com/johnkily/ha-blueprints)
and the blog post "Unser Hausmeister ist eine KI" on kilysworld.com.

## Features

- **Kanban card** in Home Assistant, loaded by the integration (no dashboard resource
  needed): columns *Open, Discussing, In progress, Review, Done, Dream on*; ticket
  types repair, purchase and Home Assistant; urgency, due date, assignees, checklist,
  comments, history, photos, invoices and other files with thumbnails. German and
  English. See [docs/card.md](docs/card.md).
- **Approvals:** purchases from a configurable amount need the second adult's
  approval; approving or declining moves the ticket.
- **Settings in the card** (admins): approval limit, the household's own categories
  with colours, and per member the role and whether they appear on the board.
- **Recurring tickets:** every n days, weeks, months or years, counted from completion
  or on a fixed calendar, created ahead of the due date.
- **Notifications:** the integration decides who needs to know what and fires one
  event per person; your own automation delivers it (mobile app, Telegram, …) and the
  link opens the ticket. A daily reminder for due tickets is one action call.
- **Entities, events and actions** for your own automations: sensors for open, burning
  and pending tickets (also per person), `haushalttickets_event` for every change,
  actions to create, move, comment and assign. See [docs/integration.md](docs/integration.md).
- **AI over MCP:** Home Assistant's MCP server exposes the ticket tools; the AI acts
  as its own account with the role *AI agent*.
- **Nightly AI run** (optional, own container): Claude Code writes drafts for tickets
  marked *AI should draft* and implements only the draft version an adult approved.
  The Home Assistant configuration goes to a private Git repository before and after
  every change; each person gets one summary notification. See
  [agent/README.md](agent/README.md).
- **Emergency UI:** a small HTTPS page served by the ticket server for when Home
  Assistant is down – read, create, comment, change status.

## Architecture

```
claude.ai ──Nabu Casa──▶ Home Assistant ── mcp_server (/api/mcp/haushalttickets)
                              │                 ▲
                              │           LLM API of the integration
                              ▼                 │
                   custom_components/haushalttickets ──REST──▶ LXC: haushalttickets-server
                   (entities, card, events, actions)              FastAPI + PostgreSQL
                              ▲                                  attachments, emergency UI
                              │ ha-mcp (own token)                        ▲
                              │                                           │ runner token
                   LXC: haushalttickets-agent ────────────────────────────┘
                   nightly AI run (Claude Code, 03:00), git export of the HA config
```

| Part | Path | Purpose |
|---|---|---|
| Server | `server/` | FastAPI + PostgreSQL in a Debian LXC; source of truth for tickets, attachments, history |
| Integration | `custom_components/haushalttickets/` | Sensors, events, actions, notifications, Kanban card, file proxy, LLM API for MCP |
| Nightly run | `agent/` | Drafts and approved implementations by Claude Code in its own LXC |
| Deployment | `deploy/` | Scripts for the Proxmox containers and their admin tools |
| Docs | `docs/` | Architecture, data model, integration and card |

## Installation

You need Home Assistant 2026.11 or newer and a small Debian 13 system for the ticket
server – a Proxmox container made by one script, or any Debian machine or VM.

1. **Server:** install it with the script and keep the API token it prints.
2. **Integration:** *Settings → Marketplace → Add from link* with this repository's URL,
   then add *Haushalttickets* under *Devices & services*.
3. **Roles:** give adults the role *adult*.
4. **Card:** `type: custom:haushalttickets-card` on a panel view.
5. **Notifications:** one automation delivers them to the phones.

Step by step: **[docs/installation.md](docs/installation.md)**.

## Key rules

- Home Assistant accounts are the ticket accounts. Roles: admin, adult, child, AI agent.
- Purchases from the approval amount need a second adult; nobody approves their own.
- The AI can never set a ticket to *done* and can never delete. Acceptance is done by
  the person who requested the ticket.
- Nightly implementation happens only for tickets explicitly approved for it, and
  only for the draft version that was approved.

## Documentation

| Topic | Document |
|---|---|
| Installation, step by step | [docs/installation.md](docs/installation.md) |
| Using the board (for everyone in the household) | [docs/user-guide.md](docs/user-guide.md) |
| Updates, backups, troubleshooting, uninstalling | [docs/operations.md](docs/operations.md) |
| Questions and answers | [docs/faq.md](docs/faq.md) |
| Architecture, roles, decisions, trade-offs | [docs/architecture.md](docs/architecture.md) |
| Data model | [docs/data-model.md](docs/data-model.md) |
| Integration: setup, entities, events, notifications, actions, MCP | [docs/integration.md](docs/integration.md) |
| Kanban card | [docs/card.md](docs/card.md) |
| Server container: install, update, backup, emergency UI, releases | [deploy/README.md](deploy/README.md) |
| Nightly run: what the AI may do, settings, commands | [agent/README.md](agent/README.md) |
| Nightly run container | [deploy/agent/README.md](deploy/agent/README.md) |
| Server package | [server/README.md](server/README.md) |

## Security notes

- This repository is public. Never commit tokens, `secrets.yaml`, real ticket data,
  screenshots with names or Home Assistant URLs.
- The MCP endpoint is served by Home Assistant and requires an administrator account.
- The ticket server only answers the addresses on its allow list; the emergency UI
  has its own login.

## Development

```bash
# server (needs PostgreSQL, see server/README.md)
cd server && uv run --extra test pytest

# nightly run (agent)
cd agent && uv run --extra test pytest

# integration (Home Assistant test harness; starts the server from server/.venv)
(cd server && uv sync) && uv venv .venv-ha --python 3.14 \
  && uv pip install --python .venv-ha -r requirements_test.txt && .venv-ha/bin/python -m pytest

# card (smoke test in jsdom)
npm install --no-save --prefix tests/card jsdom@24
node tests/card/smoke.mjs custom_components/haushalttickets/frontend/haushalttickets-card.js
```

## Releases

A release is published by merging a pull request that raises the version and adds
`release-notes/v<version>.md`; CI checks both. See
[deploy/README.md](deploy/README.md#publishing-a-release).

## License

MIT – see [LICENSE](LICENSE).
