# Kanban card

The integration ships the card and loads it on every dashboard; no dashboard
resource is needed.

```yaml
type: custom:haushalttickets-card
titel: Hausmeisterei   # optional heading
archiv: 6              # optional: visible tickets in "Erledigt" and "Träum weiter"
```

Use it in a panel view or a full-width section.

## Board

- Six columns: Offen, Wird beredet, In Arbeit, Abnahme, Erledigt, Träum weiter.
  "Wartet auf Teile" is a marker on tickets in "In Arbeit". A purchase that needs
  approval moves to "Wird beredet" by itself and, once decided, to "Offen" or
  "Träum weiter".
- Filters: everyone / mine / per person, ticket type, text search.
- Tiles: type icon, number, title, assignees, urgency, waiting marker, purchase
  approval, due date, hearts, comment and attachment counts, the requester in
  "Abnahme", and the first photo as preview.
- Desktop: drag tiles between columns. Phones (under 600 px): one column at a time
  with a tab bar; status changes in the ticket dialog.

## Ticket dialog

Title, description, status, urgency, assignees (adults), due date, hearts, area,
type-specific details (prices, vendor, link, warranty; contractor, appointment,
costs, warranty case and related purchase; affected entities), approval buttons
for the second adult, AI approvals (adults), checklist, photos and files (camera
on phones, invoices for purchases), and a combined history with comments.

Photos are scaled to at most 2560 px before upload. Home Assistant accepts
uploads up to 16 MiB.

## Opening a ticket from a link

Add `?ticket=<number>` to the dashboard address, e.g. `/lovelace/tickets?ticket=7`,
and the card opens that ticket, also when the board is already open. The number is
then removed from the address, so reloading or going back does not open it again.
Notifications use this to lead straight to the ticket.

## Permissions

The card hides controls a person cannot use, but the server decides. A refused
change shows the reason as a notification.

## Origin

Based on the ticket board by Kily's World
([johnkily/ha-blueprints](https://github.com/johnkily/ha-blueprints), MIT) including
the local changes p1–p6 (tab bar on phones, dialog as full-screen portal with fixed
footer, two-step delete, notifications instead of alerts, touch without drag and
drop, renamed first column – "Offen" since 0.5.0).
