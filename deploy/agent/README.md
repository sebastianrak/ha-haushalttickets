# Deployment: nightly run (KI-Hausmeister)

The nightly AI run gets its own Debian 13 container, separate from the ticket server.
What it does and what the AI may do: [agent/README.md](../../agent/README.md).

## Before you start

1. **Home Assistant account.** Settings → People → Users → Add user
   "KI-Hausmeister": administrator, local access only, no person needed. Right after
   that, set its ticket role (otherwise it is a ticket admin):
   Developer tools → Actions → `haushalttickets.set_role`, user "KI-Hausmeister",
   role `ai_agent`. Then log in once as KI-Hausmeister and create a long-lived token
   (Profile → Security). Keep the token for the setup below; do not paste it anywhere else.
2. **Private GitHub repository** for the configuration export, e.g. `ha-config`
   (private!). The deploy key comes from the container (step 3 below).
3. **ha-mcp's File & YAML tools** must be set up in Home Assistant (the HA-MCP
   custom component's "File & YAML Tools" entry, `ha_mcp_tools`). The export reads
   `configuration.yaml` and the packages through them.

## Create the container (Proxmox host)

```bash
curl -fsSLO https://raw.githubusercontent.com/sebastianrak/ha-haushalttickets/main/deploy/agent/create-agent-lxc.sh
less create-agent-lxc.sh
bash create-agent-lxc.sh            # hostname ki-hausmeister, 2 cores, 4096 MB, 16 GB
```

Give it a fixed address: the ticket server only answers addresses on its allow list.
If Home Assistant uses a certificate from your own CA, pass the CA file (`--ha-ca`)
or answer the question; it ends up in the container's trust store.

The script installs, as root inside the container (`install-agent.sh`):

| What | Where |
|---|---|
| Runner (Python, hashed dependencies) | `/opt/haushalttickets-agent/venv` |
| ha-mcp, pinned version (`ha-mcp.version`) with hashed dependencies | `/opt/haushalttickets-agent/ha-mcp` |
| Claude Code (native installer) | `/var/lib/haushalttickets-agent/.local/bin/claude` |
| Claude Code managed settings (deny list, guard hook) | `/etc/claude-code/managed-settings.json` |
| Settings with the tokens (root:hausmeister 0640) | `/etc/haushalttickets-agent/agent.env` |
| Export working copy, run files, deploy key | `/var/lib/haushalttickets-agent` (user `hausmeister`) |
| Timer 03:00 + service | `haushalttickets-nightly.timer` / `.service` |

## Connect

1. **Ticket container:** `haushalttickets-ctl nightly-setup` → enter the agent
   container's address; it is added to the allow list (the server restarts) and the
   runner token is printed once.
2. **GitHub:** in the agent container `haushalttickets-agent-ctl pubkey`, add the key
   to the private repository as a deploy key **with write access**.
3. **Agent container:** `haushalttickets-agent-ctl setup` asks for the ticket server
   URL and runner token, the Home Assistant URL and token, the git remote, and runs
   `claude setup-token` (open the link, log in, paste the code; then paste the token
   it prints). It tests everything (`probe`) and only then turns the timer on.

## Day to day

```bash
haushalttickets-agent-ctl status       # timer and the last run
haushalttickets-agent-ctl probe        # ticket server, Home Assistant, Claude
haushalttickets-agent-ctl export       # export and push the configuration now
haushalttickets-agent-ctl run-now      # run now (uses your Claude usage)
haushalttickets-agent-ctl claude-token # new Claude token (valid one year)
haushalttickets-agent-update           # latest release (verified with AGENT-SHA256SUMS)
haushalttickets-agent-update --claude  # ... and update Claude Code
journalctl -u haushalttickets-nightly  # logs
```

In the ticket container: `haushalttickets-ctl nightly status` (token, last run,
waiting tickets), `haushalttickets-ctl nightly revoke` (switch the runner off).

Turning it off: `systemctl disable --now haushalttickets-nightly.timer`. Revoking
access: the runner token (ticket container), the long-lived token of KI-Hausmeister
(Home Assistant profile), the Claude token (claude.ai settings), the deploy key
(GitHub).

## Security notes

- The container holds three secrets in `agent.env`: the runner token (acts only as
  the AI account, AI rules apply), the Home Assistant token of KI-Hausmeister
  (**administrator**), and the Claude token. Include the container in vzdump, keep
  the backups as protected as Home Assistant's own.
- The AI never sees these tokens: it has no shell and no file access, and the
  runner starts Claude with a minimal environment. ha-mcp holds the Home Assistant
  token in its own process.
- The managed settings (deny list, guard hook) are owned by root; the AI runs as
  `hausmeister` and cannot change them. The guard denies on any error.
- Residual risk: while drafting, the AI reads web pages and Home Assistant at the
  same time; a malicious page could make it put Home Assistant details into a web
  request. Drafts cannot change anything, and tokens are out of its reach.
- The export repository must stay private: dashboards and automations contain
  entity names, chat IDs and addresses. `secrets.yaml` and `.storage` are never read.
