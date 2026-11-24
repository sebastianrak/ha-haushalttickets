# Home Assistant integration

`custom_components/haushalttickets/` connects Home Assistant to the server. The server
stays the only place where tickets live and decides every permission; the integration
only says who is acting.

## Setup

1. Install the integration (HACS custom repository or copy the folder) and restart.
2. *Settings → Devices & services → Add integration → Haushalttickets*.
3. Enter the server URL (for example `http://192.168.1.20:8080`) and the API token
   the installer printed. The server must list the Home Assistant address in
   `HAUSHALTTICKETS_ALLOWED_CLIENTS`.

Changing the URL later: *Reconfigure* on the integration. A rejected token starts a
reauthentication.

## Users and roles

All Home Assistant users except system users are synced to the server at startup and
whenever users or person links change. Removed users become inactive.

- New Home Assistant admins start as ticket **admin**, everyone else as **child**
  (least privilege). Set the right role with the action `haushalttickets.set_role`
  (Home Assistant admins only); it takes a person or, for accounts without a
  person, the user's name or ID.
- A dedicated AI account (KI-Hausmeister) that is a Home Assistant admin starts as
  ticket admin. Set it to `ai_agent` immediately.
- Automations and scripts that run without a user act as the server user
  **Automation**. Started by a person (button, script from the UI), they act as
  that person.

## Entities

One service device *Haushalttickets* with:

| Entity | State |
|---|---|
| Open tickets | all open tickets |
| Burning tickets | open tickets with urgency *burning* |
| Approvals pending | purchases waiting for the second adult |
| Awaiting acceptance | tickets in status *acceptance* |
| *Person* assigned | open tickets assigned to that person (per user) |
| *Person* to accept | tickets the person requested that wait for acceptance |

The attribute `tickets` lists up to 25 matching tickets (most urgent first). It is
not recorded in the history database. Per-person sensors exist for users linked to a
person entity and appear automatically when a user is linked; accounts without a
person (wall tablets, service accounts) are synced but get no sensors.

## Event `haushalttickets_event`

Fired for every change on the server (polled every 10 seconds):

```yaml
seq: 42
type: status_changed        # ticket_created, comment_added, approval_requested, ...
ticket_id: 7
actor_id: <HA user id or "automation">
actor_person: person.robin
via: ui                     # ui, mcp, nightly_run, automation, recurrence, ...
payload: {old: in_progress, new: acceptance}
ticket:
  id: 7
  title: Rollladen Küche klemmt
  status: acceptance
  requested_by_person: person.alex
  assignee_persons: [person.robin]
  # type, urgency, due_date, approval_state, requested_by_id, assignee_ids
```

Example: notify the requester when a ticket waits for acceptance.

```yaml
triggers:
  - trigger: event
    event_type: haushalttickets_event
    event_data:
      type: status_changed
conditions:
  - condition: template
    value_template: "{{ trigger.event.data.payload.new == 'acceptance' }}"
actions:
  - action: notify.notify
    data:
      message: "Bitte abnehmen: #{{ trigger.event.data.ticket.id }} {{ trigger.event.data.ticket.title }}"
```

## Notifications

The integration decides who hears about what and fires one
`haushalttickets_notification` event per person. Delivering it (which device, which
channel, quiet hours) is up to an automation, so it stays editable in the UI.

| Server event | Who is notified |
|---|---|
| New ticket, urgency *burning* | all active adults (urgent) |
| New ticket with assignees, assignees added | the new assignees |
| Urgency changed to *burning* | all active adults (urgent) |
| Approval requested | the adults except the requester (with the estimated price) |
| Approval decided | the requester |
| Status → acceptance | the requester |
| Status → done / dream on | the requester (not when the move follows an approval decision; the approval message covers it) |
| Comment | requester and assignees |
| Everything else (fields, photos, checklist) | nobody |

Nobody is told about their own action. Only active members with a person entity are
notified, never AI accounts. Changes made through AI tools say so ("… via AI").

```yaml
user_id: <HA user id>
person: person.robin
kind: approval_requested    # burning, assigned, approved, declined, acceptance, done,
                            # rejected, comment, due
title: Approval needed
message: "#7 Washing machine – by Alex\nestimated €899.00"
ticket_id: 7                # null for the due summary
urgent: false               # true for burning tickets
```

Texts follow the Home Assistant language (German or English).

### Opening the ticket

Notifications have no buttons on purpose: approving a purchase or accepting work
needs a look at the ticket first. Link them to the dashboard with the card and add
`?ticket=<ticket_id>`; the card then opens that ticket (see [card](card.md)). The due
summary has no `ticket_id`; link it to the board.

### Delivery example

```yaml
alias: Haushalttickets notifications
mode: parallel
max: 20
triggers:
  - trigger: event
    event_type: haushalttickets_notification
    id: notification
  - trigger: time
    at: "19:00:00"
    id: due
variables:
  devices:
    person.alex: notify.mobile_app_alex_phone
    person.robin: notify.mobile_app_robin_phone
actions:
  - if:
      - condition: trigger
        id: due
    then:
      - action: haushalttickets.send_due_reminders
    else:
      - variables:
          n: "{{ trigger.event.data }}"
          quiet: "{{ now().strftime('%H:%M') >= '21:30' or now().strftime('%H:%M') < '07:00' }}"
      - condition: template
        value_template: "{{ n.person in devices }}"
      - action: "{{ devices[n.person] }}"
        data:
          title: "{{ n.title }}"
          message: "{{ n.message }}"
          data:
            url: "/lovelace/tickets{{ '?ticket=%s' % n.ticket_id if n.ticket_id else '' }}"
            group: haushalttickets
            push:
              interruption-level: >-
                {{ 'time-sensitive' if n.urgent else ('passive' if quiet else 'active') }}
```

## Actions

| Action | Purpose |
|---|---|
| `haushalttickets.create_ticket` | type, title, description, urgency, due date, assignees (persons), area, device; `extra_fields` for type-specific fields such as `price_estimated` or `vendor`. Returns the ticket. |
| `haushalttickets.set_status` | Move a ticket; returns the ticket. |
| `haushalttickets.add_comment` | Comment on a ticket. |
| `haushalttickets.assign` | Replace the assignees (persons linked to users). |
| `haushalttickets.set_role` | Role of a person, or of a user without person given by name or ID (HA admins only). |
| `haushalttickets.send_due_reminders` | One notification per person with their open tickets that are overdue, due today or due tomorrow. Returns how many were sent. |

The server rules apply unchanged; a refused call raises an error with the server's
error code.

## AI assistants (MCP)

The integration registers the LLM API **Haushalttickets** (id `haushalttickets`).
Home Assistant's MCP server integration serves it at `/api/mcp/haushalttickets`;
this endpoint accepts Home Assistant **admins only** (decision D7). If an MCP server
entry exposes "all LLM APIs, also those added later", the tools appear at `/api/mcp`
as well, with that entry's own admin setting.

| Tool | Does |
|---|---|
| `haushalttickets_household` | Calling person and role, members, allowed values, approval threshold |
| `haushalttickets_list_tickets` | Open tickets by default; filters: status, type, mine, text, closed |
| `haushalttickets_get_ticket` | All fields, checklist, comments, recent changes |
| `haushalttickets_create_ticket` | New ticket requested by the calling person |
| `haushalttickets_add_comment` | Comment, marked as written through the AI |
| `haushalttickets_set_status` | Status up to `acceptance`, optional reason as comment |

Every call acts for the logged-in Home Assistant user through the channel `mcp`,
so the server applies the AI rules even for admins: no done, no rejecting, no
deleting, no assigning, no approvals, no field edits after creation. Calls without
a user (voice satellites, automations) are refused. Ticket texts are handed to the
model as data; the API prompt tells it not to follow instructions inside them.

### Connecting claude.ai

1. claude.ai → Settings → Connectors → add a custom connector with
   `https://<your Nabu Casa URL>/api/mcp/haushalttickets`.
2. Sign in with a Home Assistant admin account. The tools act as that account.

### AI account

For the nightly run, create a Home Assistant user "KI-Hausmeister" (admin, no
person) and set its ticket role right away:

```yaml
action: haushalttickets.set_role
data:
  user: KI-Hausmeister
  role: ai_agent
```

Until then it is a ticket admin, because Home Assistant admins start as admins.

## Card

See [card.md](card.md). The integration serves it at
`/haushalttickets_static/haushalttickets-card.js` and registers it with the frontend.

## Files (for the card)

- `GET /api/haushalttickets/attachments/<id>` and `…/<id>/thumbnail` (JPEG, at most
  480 px) stream attachments from the server as the logged-in user.
- `POST /api/haushalttickets/tickets/<id>/attachments` (multipart `file`, `kind`:
  `photo`, `invoice` or `document`) uploads; Home Assistant accepts up to 16 MiB.

## WebSocket API (for the card)

- `haushalttickets/request` with `method`, `path` (below `/api/v1/`), optional
  `params` and `body`: forwards the call as the logged-in user. `/api/v1/users/sync`
  is reserved for the integration.
- `haushalttickets/subscribe`: pushes every `haushalttickets_event`.

## Diagnostics

Contain counts, roles and the event cursor only – no URL, token, names or titles.
