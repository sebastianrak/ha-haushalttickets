# Feasibility spike: ticket tools over Home Assistant's MCP server

Throwaway integration `haushalttickets_spike`. It answers one question before the
real integration is built:

> Do tools registered by a custom integration as an LLM API reach claude.ai through
> Home Assistant's built-in MCP server (`mcp_server`) via Nabu Casa, with the calling
> Home Assistant user known inside the tool?

## What it does

- Registers the LLM API `haushalttickets_spike` with three tools:
  - `haushalttickets_spike_whoami` (read-only) – user, admin flag, platform, HA version
  - `haushalttickets_spike_list_tickets` (read-only) – demo tickets, optional status filter
  - `haushalttickets_spike_create_ticket` (write, non-destructive) – demo ticket, fires
    `haushalttickets_spike_ticket_created`
- Demo data lives in memory only and is lost on restart.
- Home Assistant serves the tools at `/api/mcp/haushalttickets_spike` (admin users only).
  If the `mcp_server` entry is set to "All LLM APIs", they also appear at `/api/mcp`.

## Tests

End-to-end through the MCP HTTP endpoint (initialize, tools/list, tools/call,
non-admin rejection):

```bash
cd spike
pip install pytest-homeassistant-custom-component
python -m pytest
```

Verified against Home Assistant 2026.10.4 (test harness) and import-checked against
2026.11.2.

## Removal

Delete the config entry, remove `custom_components/haushalttickets_spike/`, restart.
This folder is removed from the repository once the real integration exists.
