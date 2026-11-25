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
- Filters: everyone / mine / per person, ticket type, category, text search. People
  hidden in the settings do not appear as filters or for assignment; tickets already
  assigned to them keep showing their avatar.
- Tiles: type icon, number, title, assignees, category in its colour, urgency, waiting marker, purchase
  approval, due date, hearts, comment and attachment counts, the requester in
  "Abnahme", and the first photo as preview.
- Desktop: drag tiles between columns. Phones (under 600 px): one column at a time
  with a tab bar; status changes in the ticket dialog.

## Ticket dialog

Title, description, category, status, urgency, assignees (adults), due date, hearts, area,
type-specific details (prices, vendor, link, warranty; contractor, appointment,
costs, warranty case and related purchase; affected entities), approval buttons
for the second adult, AI approvals (adults), checklist, photos and files (camera
on phones, invoices for purchases), and a combined history with comments.

Photos are scaled to at most 2560 px before upload. Home Assistant accepts
uploads up to 16 MiB.

## Recurring tickets

- **Wiederholen** in the ticket dialog: rhythm (every n days/weeks/months/years), mode
  ("ab Erledigung" or "fester Kalender") and lead time. An existing ticket becomes the
  first round. A new ticket whose due date lies beyond the lead time is only planned:
  it appears on the board when its lead time starts.
- **Wiederkehrend** in the header lists every series with its rhythm, the open round or
  the next due date, and lets you change, pause or end it.
- Tiles of rounds carry a repeat icon. Children manage their own series only.

A new ticket takes the category selected in the filter.

## Settings (admins)

The gear in the header opens the household settings. Changes apply right away and
reach every open board.

- **Approval from (€)** – purchases from this estimated price need a second adult
  (default 250 €).
- **Categories** – add, rename and recolour; the number shows how many tickets use one.
  Deleting asks a second time and clears the category on its tickets (recorded in their
  history) and series.
- **Members** – every Home Assistant user: role (admin, adult, child, AI) and whether
  they appear on the board. Users without a person never appear; your own role is fixed
  so the household always keeps an admin.

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
