# haushalttickets-agent – the nightly run

Runs in its own Debian LXC (not the ticket server's). Every night at 03:00 a systemd
timer starts `haushalttickets-nightly`, which:

1. asks the ticket server what to do (`GET /api/v1/nightly/work`): approved
   implementations first, then requested drafts;
2. starts a run log and makes a tiny probe call to Claude (is the token valid, is
   there usage left?);
3. exports the Home Assistant configuration into a git working copy and commits it;
4. for each ticket starts Claude Code headless (`claude -p`, model `opus`):
   - **draft**: Home Assistant read-only through ha-mcp, plus web search; the answer
     is stored as a new draft version on the ticket;
   - **implement**: exactly the approved draft version, Home Assistant read and
     write (no web); the configuration is exported and committed before and after,
     the commit goes into the ticket's change log and the ticket moves to
     acceptance;
5. reports the run; the integration sends one summary push per person.

The AI never sees the ticket server or a token: the runner hands it the ticket as
text and stores what comes back. The runner token acts only as the AI account (role
`ai_agent`, channel `nightly_run`), so the AI rules apply on the server too.

## What the AI may do

| Phase | Home Assistant | Web | Shell, local files |
|---|---|---|---|
| draft | read | search and fetch | no |
| implement | read; create and change automations, scripts, scenes, helpers, dashboards; areas, labels (add only), categories, names, icons of entities; YAML in `packages/*.yaml` and `configuration.yaml` | no | no |

Never: deleting, renaming entity IDs, calling services, restarting or reloading,
integrations, apps, updates, `homeassistant:` in YAML, `!include`, voice assistant
exposure. Enforced twice:

- `--allowedTools` per phase (nobody can approve anything else at night), and
- the guard hook `haushalttickets-guard`, registered in
  `/etc/claude-code/managed-settings.json` (the AI cannot change it), which checks the
  arguments of every writing call and denies on any error.

ha-mcp itself writes YAML only for a short list of keys (`knx`, `command_line`,
`rest`, `notify`, …) and asks for a preview before every write.

If Home Assistant's configuration check fails after a YAML change, the runner puts
the old top-level keys back from the export and reports the ticket as failed. YAML
changes take effect only after someone reloads or restarts Home Assistant.

Everything the nightly run creates or changes gets the label `ki_hausmeister`.

## Settings (`/etc/haushalttickets-agent/agent.env`)

| Variable | Meaning |
|---|---|
| `HT_AGENT_TICKETS_URL` | ticket server, e.g. `http://172.16.3.21:8080` |
| `HT_AGENT_TICKETS_TOKEN` | runner token (`haushalttickets-ctl nightly-token` in the ticket LXC) |
| `HT_AGENT_HA_URL` | Home Assistant, e.g. `https://172.16.3.2:8123` |
| `HT_AGENT_HA_TOKEN` | long-lived token of the AI account ("KI-Hausmeister") |
| `HT_AGENT_HA_CA_FILE` | CA bundle for HTTPS to Home Assistant (own CA) |
| `HT_AGENT_GIT_REMOTE` | private repository, e.g. `git@github.com:owner/ha-config.git` (empty: local only) |
| `HT_AGENT_GIT_SSH_KEY` | deploy key with write access to that repository |
| `HT_AGENT_TIME_LIMIT_MINUTES` | time limit per night (default 120) |
| `HT_AGENT_MODEL` | Claude model alias (default `opus`) |
| `HT_AGENT_LANGUAGE` | language of comments (default `de`) |
| `HT_AGENT_HA_MCP` | command that starts ha-mcp (default: the pinned venv) |
| `CLAUDE_CODE_OAUTH_TOKEN` | from `claude setup-token` (subscription) |

## Commands

```
haushalttickets-nightly probe    # ticket server, Home Assistant, Claude
haushalttickets-nightly export   # export and commit the configuration now
haushalttickets-nightly check    # Home Assistant's configuration check
haushalttickets-nightly run      # what the timer starts
```

Setup and operation: see `deploy/agent/README.md`.
