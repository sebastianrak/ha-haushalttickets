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
  (Home Assistant admins only).
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
not recorded in the history database. Per-person sensors appear automatically for new
users.

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

## Actions

| Action | Purpose |
|---|---|
| `haushalttickets.create_ticket` | type, title, description, urgency, due date, assignees (persons), area, device; `extra_fields` for type-specific fields such as `price_estimated` or `vendor`. Returns the ticket. |
| `haushalttickets.set_status` | Move a ticket; returns the ticket. |
| `haushalttickets.add_comment` | Comment on a ticket. |
| `haushalttickets.assign` | Replace the assignees (persons linked to users). |
| `haushalttickets.set_role` | Role of a person in the ticket system (HA admins only). |

The server rules apply unchanged; a refused call raises an error with the server's
error code.

## WebSocket API (for the card)

- `haushalttickets/request` with `method`, `path` (below `/api/v1/`), optional
  `params` and `body`: forwards the call as the logged-in user. `/api/v1/users/sync`
  is reserved for the integration.
- `haushalttickets/subscribe`: pushes every `haushalttickets_event`.

## Diagnostics

Contain counts, roles and the event cursor only – no URL, token, names or titles.
