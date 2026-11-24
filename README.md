# Haushalttickets

A ticket system for a household – repairs, purchases and work on Home Assistant itself –
with a Home Assistant integration and AI access over MCP.

> **Status: pre-alpha.** Server, integration (sensors, actions, events, notifications
> that open the ticket), the Kanban card, recurring tickets, the emergency UI, the
> MCP tools for AI assistants and the nightly AI run (in its own container) work.

## Idea

Family members collect what needs doing in one Kanban board inside Home Assistant.
An AI assistant (Claude, optionally ChatGPT) can read tickets, create new ones and
comment through MCP. For Home Assistant tickets it may draft proposals and – only
after explicit approval per ticket – implement them during a nightly run. The person
who requested a ticket always does the final acceptance.

Inspired by the ticket board of [johnkily/ha-blueprints](https://github.com/johnkily/ha-blueprints)
and the blog post "Unser Hausmeister ist eine KI" on kilysworld.com.

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
| Integration | `custom_components/haushalttickets/` | Sensors, events, actions, Kanban card, file proxy, LLM API for MCP, see [docs/integration.md](docs/integration.md) and [docs/card.md](docs/card.md) |
| Nightly run | `agent/` | Drafts and approved implementations by Claude Code in its own LXC, see [agent/README.md](agent/README.md) and [deploy/agent/README.md](deploy/agent/README.md) |
| Docs | `docs/` | Architecture and decisions |

See [docs/architecture.md](docs/architecture.md), [docs/data-model.md](docs/data-model.md) and [deploy/README.md](deploy/README.md) for details.

## Key rules

- Home Assistant accounts are the ticket accounts. Roles: admin, adult, child, AI agent.
- The AI can never set a ticket to *done* and can never delete. Acceptance is done by
  the person who requested the ticket.
- Nightly implementation happens only for tickets explicitly approved for it, and
  only for the draft version that was approved; the Home Assistant configuration is
  exported to a private Git repository before and after every change.

## Security notes

- This repository is public. Never commit tokens, `secrets.yaml`, real ticket data,
  screenshots with names or Home Assistant URLs.
- The MCP endpoint is served by Home Assistant and requires an administrator account.

## Development

```bash
# server
cd server && uv run --extra test pytest

# nightly run (agent)
cd agent && uv run --extra test pytest

# integration (Home Assistant test harness; starts the server from server/.venv)
(cd server && uv sync) && uv venv .venv-ha --python 3.14 \
  && uv pip install --python .venv-ha -r requirements_test.txt && .venv-ha/bin/python -m pytest
```

## License

MIT – see [LICENSE](LICENSE).
