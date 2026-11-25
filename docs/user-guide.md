# Using the board

For everyone in the household. The card shows German or English, following the
language of your Home Assistant profile; this guide uses the English labels.

## The columns

| Column | Means |
|---|---|
| **Open** | Collected, nobody is working on it yet. |
| **Discussing** | Needs talking about. Purchases that need approval land here by themselves. |
| **In progress** | Someone is on it. *Waiting for parts* marks a ticket that is stuck. |
| **Review** | The work is done; the person who asked for it checks it. |
| **Done** | Finished and checked. |
| **Dream on** | Decided not to do it (also: declined purchases). |

*Done* and *Dream on* show the newest tickets only; *Show all* opens the rest.

On a computer you drag tiles between columns. On a phone the board shows one column at a
time with a tab bar; change the status inside the ticket.

Filters at the top: **All**, **Mine** or one person, the kind of ticket, the category
and a search.

## Create a ticket

Tap **New ticket** and fill in what you know – only the title is required:

- **What needs doing?** – a short title.
- **Kind** – *Repair*, *Purchase* or *Home Assistant* (work on the smart home itself).
- **Category** – if your household uses categories (e.g. *Garden*, *Car*); shown in
  colour on the tile.
- **How urgent?** – *Someday*, *This week* or *Urgent*. *Urgent* notifies all adults
  right away.
- **How important?** – hearts.
- **Due** – a date, or one of the quick choices.
- **Who takes care?** – adults can assign people; everyone assigned is notified.
- **Area** and **Details** – e.g. price, vendor and link for a purchase; contractor,
  appointment and costs for a repair; affected entities for Home Assistant work.
- **Photos** – on a phone the camera opens directly.

You are the **requester** of every ticket you create. That matters at the end: you
decide whether it is done.

## Work on a ticket

Open a tile to see everything: description, checklist, photos and files, and the
**history**, which lists every change and comment with who made it. Changes made
through an AI say *via AI*.

- **Comment** at the bottom; the requester and the assignees are notified.
- **Checklist** for steps; tick them off as you go.
- **Photos and files** – before/after photos, an invoice for a purchase, a manual.
  Files up to 16 MB.

## Purchases and approval

A purchase with an estimated price from **250 €** (the default; an admin can change
it) needs a **second adult**:

1. Create the purchase with its estimated price. It moves to *Discussing* and the other
   adults get "Approval needed".
2. Another adult opens it and taps **Approve** or **Decline**. Nobody approves their own
   purchase.
3. Approved → *Open*. Declined → *Dream on*. The requester is told either way.

If the price is raised above the limit later, the approval is asked again.

## Review and done

1. Whoever finishes the work moves the ticket to **Review**.
2. The requester gets "Please review", checks the result and sets it to **Done** – or
   back to *In progress* with a comment.

Only the requester closes a ticket (an admin can accept for someone else). An AI never
sets a ticket to *Done*.

## Recurring tickets

For things that come back: filters, smoke detector batteries, the car inspection.

1. In a ticket, tick **Repeat**.
2. Choose the **rhythm** (every n days, weeks, months or years), the **mode** and the
   **lead time** (how many days before the due date the ticket appears):
   - *after completion* – the next round counts from the day the last one was done;
   - *fixed calendar* – always on the same dates, whenever the last one was done.
3. Save. The ticket is the first round; the next one appears by itself.

The **Recurring** button in the header lists every series: change it, pause, resume or
end it. Ending a series keeps its tickets. Tiles of recurring tickets carry a repeat
icon.

## Notifications

Depending on how your household set it up, your phone tells you when

- a ticket is assigned to you,
- something urgent comes in (adults),
- a purchase needs your approval (adults), or yours was decided,
- a ticket you asked for is ready for review, done or set to *Dream on*,
- someone comments on a ticket you asked for or work on,
- a new round of a recurring ticket appears,
- and once a day which of your tickets are overdue or due today or tomorrow.

A tap opens the ticket. You are never notified about your own actions.

## AI helpers

If your household connected an AI assistant (for example Claude), it can read tickets,
create new ones and comment – always as the person who uses it, and marked *via AI*.
It cannot close, decline or delete tickets, assign people or approve anything.

If the **nightly AI run** is set up, adults can ask it for help in a ticket:

- **AI should draft** – at night the AI writes a proposal (any kind of ticket). It
  appears under **AI drafts** in the ticket, and you get a notification after the run.
- **AI should implement draft vN** (Home Assistant tickets only) – approves exactly
  that draft version. The next night the AI makes the change in Home Assistant, moves
  the ticket to *Review* and lists what it changed under **Changes the AI made in Home
  Assistant**. Check it and accept or reopen.

Asking for a new draft withdraws an approval, so the AI never implements something
nobody has read.

## Settings (admins)

Admins see a gear in the header. It opens the household settings: from which price a
purchase needs a second adult, the list of categories, and for every member the role
and whether they appear on the board. Details: [card.md](card.md#settings-admins).

## Children

Children can create tickets, comment, close their own tickets after review and manage
their own recurring tickets. They cannot assign people, approve purchases or set AI
options.

## When Home Assistant is down

If your household set up the emergency page, you can still read tickets, create new
ones, comment and change the status there. Ask whoever runs your Home Assistant for the
address.
