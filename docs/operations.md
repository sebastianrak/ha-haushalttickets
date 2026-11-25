# Operations

Updating, backups, keeping an eye on things, and what to do when something does not
work. Commands marked *server* run as root in the ticket server (Proxmox:
`pct enter <id>`); *agent* means the container of the nightly run.

## Updating

Every release on GitHub lists what changed. Update in this order:

1. **Server** (*server*):

   ```bash
   haushalttickets-update
   ```

   It dumps the database first, installs the newest release (files checked against
   `SHA256SUMS`, dependencies against their hashes), runs the database migrations,
   restarts the service and checks that it answers. Back to an older version:
   `haushalttickets-update --release v0.8.1`.

2. **Integration and card:** *Settings → Marketplace* shows the update; install it and
   restart Home Assistant if it asks. Then reload the browser and the app once, so the
   new card is loaded.

3. **Nightly run** (*agent*), if you use it:

   ```bash
   haushalttickets-agent-update
   haushalttickets-agent-ctl probe
   ```

   `probe` checks the ticket server, Home Assistant and Claude. Claude Code itself:
   `haushalttickets-agent-update --claude`.

Updates are never installed automatically.

## Backups

| What | Where | How |
|---|---|---|
| Tickets, history, users | `/var/backups/haushalttickets` (*server*) | daily dump at the time chosen during installation, kept 14 days; extra dump: `haushalttickets-backup manual` |
| Photos and files | `/var/lib/haushalttickets/attachments` (*server*) | not in the dump – back up the whole container or machine |
| Settings and tokens | `/etc/haushalttickets/server.env` (*server*) | in the container backup |
| Home Assistant configuration | your private Git repository | the nightly run commits before and after every change it makes |

On Proxmox, include the server container in a vzdump job; the installer offers that at
the end. Restoring a dump: [deploy/README.md → Restore a dump](../deploy/README.md#restore-a-dump).

## Keeping an eye on it

- **Sensors:** if the integration's sensors become *unavailable*, Home Assistant cannot
  reach the server. An automation on that state is a cheap alarm.
- **Logs:** `journalctl -u haushalttickets -f` (*server*),
  `journalctl -u haushalttickets-nightly` (*agent*).
- **Nightly run:** `haushalttickets-agent-ctl status` (*agent*) shows the timer and the
  last run; `haushalttickets-ctl nightly status` (*server*) shows the last run and how
  many tickets wait. If approved work waits more than 26 hours without a run, admins
  get a notification.

## Tokens and access

| Task | Command |
|---|---|
| New API token for Home Assistant | `haushalttickets-ctl token create NAME` (*server*); then *Reconfigure* the integration |
| Revoke a token | `haushalttickets-ctl token revoke NAME` (*server*) |
| Change who may use the API | `HAUSHALTTICKETS_ALLOWED_CLIENTS` in `/etc/haushalttickets/server.env`, then `systemctl restart haushalttickets` (*server*) |
| Change the server address in Home Assistant | *Settings → Devices & services → Haushalttickets → Reconfigure* |
| New emergency UI password | `haushalttickets-ctl emergency password` (*server*) |
| Switch the nightly run off | `systemctl disable --now haushalttickets-nightly.timer` (*agent*), or revoke its token: `haushalttickets-ctl nightly revoke` (*server*) |

## Troubleshooting

| What you see | Likely cause | What to do |
|---|---|---|
| Adding the integration: "The server cannot be reached …" | wrong address or port, server not running, firewall | `curl http://<server>:8080/api/health` from another computer; *server*: `systemctl status haushalttickets` |
| "The server does not accept connections from this Home Assistant" | the address of Home Assistant is not on the allow list | add it to `HAUSHALTTICKETS_ALLOWED_CLIENTS` and restart (see above) |
| "The server rejected the API token" or a re-authentication request | token wrong or revoked | create a new token and enter it |
| Card: "Haushalttickets cannot be reached." | integration not loaded or server down | *Settings → Devices & services → Haushalttickets*; server status |
| Card: "Custom element doesn't exist" | page loaded before the integration | reload the browser or app; check the integration is loaded |
| Card looks old after an update | browser cache | reload once (the card's address carries the version) |
| No notifications | delivery automation missing a person, person not linked to a user, or it was your own action | check the automation's traces; check *Settings → People* |
| Upload fails, file too large | more than 16 MB | photos are shrunk automatically; split or compress other files |
| `haushalttickets-ctl: command not found` | shell without `/usr/local/sbin` in `PATH` (e.g. `pct enter`) | open a new shell, or call `/usr/local/sbin/haushalttickets-ctl` |
| Update: "release is missing assets" | the release was published seconds ago and is still being built | wait a minute and run the update again |
| Update: Perl "Setting locale failed" | locale of the Proxmox host passed into the container | harmless; the dump is written anyway |
| Service fails with `226/NAMESPACE` | container without `nesting=1` | Proxmox: *Options → Features → Nesting*, then restart the container |
| Nightly run `probe`: ticket server 401 | runner token wrong (often pasted twice) | `haushalttickets-agent-ctl setup` again; it checks the token's form |
| Nightly run `probe`: Claude 401 | Claude token expired or wrong | `haushalttickets-agent-ctl claude-token` |
| Nightly run: `PermissionError: '.env'` | agent older than 0.8.1 started outside its directory | `haushalttickets-agent-update` |

Still stuck? Open an issue on GitHub with the version, what you did and the relevant
log lines – without tokens, names or addresses (the repository is public).

## Uninstalling

1. Home Assistant: delete the card and the delivery automation, then remove the
   integration (*Settings → Devices & services*) and uninstall it in the Marketplace.
2. Nightly run: delete its container; revoke the Home Assistant token of the AI account,
   its Claude token and the deploy key of the configuration repository.
3. Server: delete the container (Proxmox: `pct stop <id> && pct destroy <id>`) – after
   copying a last dump if you want to keep the tickets.
