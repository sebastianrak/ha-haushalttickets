# Installation

This guide takes you from nothing to a working ticket board in Home Assistant. Steps 1–6
are needed; everything under [Optional parts](#optional-parts) can be added later.

## What you need

- **Home Assistant 2026.11 or newer.** The integration is installed from the built-in
  Marketplace (the former HACS).
- **A small Linux system for the ticket server** that Home Assistant can reach in your
  network. The server keeps the tickets, photos and history in PostgreSQL. It does not run
  inside Home Assistant OS; there is no app (add-on) or Docker image yet. Two ways work:
  - **Proxmox VE** (recommended): one script creates a Debian container with everything.
    It suggests 2 CPU cores, 1 GB memory and 8 GB disk.
  - **Any machine or VM with Debian 13** and systemd: you run the installer yourself.
  Only x86-64 (amd64) is tested.
- **One Home Assistant user per family member, linked to a person**
  (*Settings → People*). Roles, per-person sensors and notifications work through persons.
- For notifications: the **Home Assistant Companion app** on the phones, or another
  notification service.

Plan a **fixed IP address** for the server (static address or DHCP reservation):
Home Assistant stores the address when you add the integration.

## Step 1 – Install the ticket server

### Option A: Proxmox VE

On the Proxmox host, as root:

```bash
curl -fsSLO https://raw.githubusercontent.com/sebastianrak/ha-haushalttickets/main/deploy/proxmox/create-lxc.sh
less create-lxc.sh          # read what it does
bash create-lxc.sh
```

The script asks every question and suggests an answer; press Enter to accept it.
The important ones:

| Question | Answer |
|---|---|
| IP address | a fixed address, e.g. `192.168.1.20/24`, plus the gateway |
| Addresses allowed to use the API | the IP address of Home Assistant, e.g. `192.168.1.10` |
| Database dump time | keep the suggestion (30 minutes before your backup job) |

Questions are in German if your shell language is German, otherwise in English.
All options: `bash create-lxc.sh --help`.

### Option B: Debian 13 machine or VM

As root on the Debian machine:

```bash
apt-get update && apt-get install -y git
git clone https://github.com/sebastianrak/ha-haushalttickets /opt/haushalttickets/src
bash /opt/haushalttickets/src/deploy/lxc/install.sh --allowed-clients 192.168.1.10
```

Replace `192.168.1.10` with the address of Home Assistant. All options:
`install.sh --help`.

### Keep the token

At the end, both ways print an **API token** once:

```
API token for the Home Assistant integration (shown only once, store it safely):

  4mJ2...
```

Copy it into your password manager. Lost it? `haushalttickets-ctl token create homeassistant2`
in the server creates a new one.

### Check

From any computer in your network:

```bash
curl http://192.168.1.20:8080/api/health
```

The answer contains `"status": "ok"` and the version. If not, see
[Troubleshooting](operations.md#troubleshooting).

## Step 2 – Install the integration

1. *Settings → Marketplace → Add from link*, enter
   `https://github.com/sebastianrak/ha-haushalttickets` and install **Haushalttickets**.
   If Home Assistant asks for a restart, do it (*Settings → System → Repairs*).
2. *Settings → Devices & services → Add integration → Haushalttickets*.
3. Enter the server address, e.g. `http://192.168.1.20:8080`, and the API token from
   step 1.

You now have a device **Haushalttickets** with sensors such as *Open tickets* and
*Burning tickets*.

Without the Marketplace: copy the folder `custom_components/haushalttickets` of this
repository into the `custom_components` folder of your Home Assistant configuration and
restart.

## Step 3 – Set the roles

The integration copies all Home Assistant users to the server. Home Assistant
administrators start as **admin**, everyone else as **child**, so nobody gets more
rights than intended. Give every adult the role **adult** – easiest after step 4: the
gear in the card's header opens the settings, where an admin sets each member's role
and hides accounts that should not appear on the board (wall tablets, the AI account).

Without the card, use the action *Developer tools → Actions →* `haushalttickets.set_role`:

```yaml
action: haushalttickets.set_role
data:
  person: person.alex
  role: adult
```

| Role | May |
|---|---|
| admin | everything, including deleting tickets and accepting for others |
| adult | create, comment, assign, approve purchases, approve AI work |
| child | create, comment, accept own tickets |
| ai_agent | for AI accounts only – see [Optional parts](#optional-parts) |

## Step 4 – Add the card

1. Create a dashboard, e.g. *Tickets* (*Settings → Dashboards → Add dashboard*).
2. Edit it, set the view type to **Panel**, add a card, choose *Manual* and enter:

   ```yaml
   type: custom:haushalttickets-card
   ```

3. Reload the browser or the app once.

The integration delivers the card itself; you do not add a dashboard resource. As an
admin, open the gear to set roles (step 3), the approval limit and categories such as
*Garden* or *Car*.
Options and features: [card.md](card.md). How to use the board:
[user-guide.md](user-guide.md).

## Step 5 – Notifications

The integration decides **who** has to know **what** and fires one event per person.
**How** it is delivered (which phone, quiet hours, Telegram …) is up to one automation
that you create once:

1. *Settings → Automations & scenes → Create automation → ⋮ → Edit in YAML*.
2. Paste the [delivery example](integration.md#delivery-example).
3. Adapt two things:
   - under `devices`: each person and the notify action of their phone, e.g.
     `person.alex: notify.mobile_app_alex_phone`;
   - in `url`: the path of your dashboard from step 4, e.g. `/dashboard-tickets/0`.
4. Save.

A tap on a notification opens the ticket. At 19:00 the automation sends each person a
summary of overdue tickets and tickets due today or tomorrow.

## Step 6 – Try it

1. Create a ticket on the board, assign it to another person and save.
2. That person gets the notification "Assigned to you"; a tap opens the ticket.
3. That person moves the ticket to **Review**. You, the requester, get "Please review"
   and set it to **Done**.

Nobody is notified about their own actions, so the test needs two people (or two
phones).

Works? Then you are set up. If not: [Troubleshooting](operations.md#troubleshooting).

## Optional parts

### AI assistant (Claude and others) over MCP

Lets an AI assistant read tickets, create new ones and comment – as the person who is
logged in, with the AI rules (it can never close, reject or delete). Needs an external
HTTPS address for Home Assistant (e.g. Nabu Casa) and an administrator account.
Setup: [integration.md → AI assistants](integration.md#ai-assistants-mcp).

### Emergency UI

A small HTTPS page on the ticket server for when Home Assistant is down. In the server,
as root: `haushalttickets-ctl emergency-setup`. Details:
[deploy/README.md → Emergency UI](../deploy/README.md#emergency-ui).

### Nightly AI run

An AI (Claude Code) that drafts solutions for Home Assistant tickets at night and – only
after an adult approves a draft – implements it. Needs a second container with about
4 GB memory, a Claude subscription, a private GitHub repository for the configuration
backup and a Home Assistant account for the AI. Setup:
[deploy/agent/README.md](../deploy/agent/README.md); what the AI may do:
[agent/README.md](../agent/README.md).

### Backups

The server writes a database dump every day (kept 14 days) in
`/var/backups/haushalttickets`. Include the server container or machine in your normal
backup, so photos and dumps are saved as well. Restoring:
[operations.md → Backups](operations.md#backups).

## Next

- Using the board: [user-guide.md](user-guide.md)
- Updates, backups, troubleshooting: [operations.md](operations.md)
- Questions: [faq.md](faq.md)
