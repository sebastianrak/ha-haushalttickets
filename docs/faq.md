# Questions and answers

## Why a separate server and not just Home Assistant?

Tickets, photos and their history should survive a broken Home Assistant and should not
bloat its database. The server keeps them in PostgreSQL with daily dumps, enforces the
rules (who may approve, close, delete) in one place, and still works through the
[emergency UI](../deploy/README.md#emergency-ui) when Home Assistant is down.

## Can the server run on Home Assistant OS, as an app or in Docker?

Not yet. Today it needs a Debian 13 system with systemd: a Proxmox container (one
script) or any Debian machine or VM. See [installation.md](installation.md#what-you-need).

## Does it run on a Raspberry Pi?

Not tested. The server and its dependencies are Python and PostgreSQL, which Debian 13
also has for ARM, but only x86-64 (amd64) is tested.

## Do I need an AI or a Claude subscription?

No. The board, approvals, recurring tickets and notifications work without any AI. The
MCP access and the nightly run are optional.

## Who sees which tickets?

Everyone in the household sees all tickets. Roles decide what someone may *do*:
[installation.md → roles](installation.md#step-3--set-the-roles).

## A new family member does not show up.

Every Home Assistant user is copied to the server automatically. To get notifications
and their own sensors, the user must be linked to a person (*Settings → People*). New
users start as *child*; give adults the role *adult* with `haushalttickets.set_role`.

## Can I change the 250 € approval limit?

Yes: as an admin, open the gear in the card's header and set *Approval from (€)*.
Purchases at or above the limit need a second adult.

## How do I hide the AI account or a wall tablet from the board?

In the same settings, untick *On the board* for that member. Hidden members do not appear
as filters or for assignment; their existing tickets stay as they are. Users without a
person (*Settings → People*) never appear anyway.

## Can I sort tickets into my own categories?

Yes. Admins create categories with a name and colour in the settings; every ticket can
have one. The board filters by category and shows it on the tiles.

## Can the AI break my Home Assistant?

The MCP tools only touch tickets. The nightly run can change Home Assistant, but only

- for tickets an adult approved, and only the draft version that was approved,
- creating and changing automations, scripts, scenes, helpers, dashboards and a limited
  set of YAML – never deleting, renaming entities, calling services or restarting,
- with the configuration committed to your private Git repository before and after
  every change.

Details: [agent/README.md](../agent/README.md).

## Where are the photos stored?

On the ticket server (`/var/lib/haushalttickets/attachments`), not in Home Assistant.
Home Assistant only passes them through for the logged-in user.

## Does it need internet access?

The server and the integration work in your network only. Installing and updating
fetch from GitHub and the Debian and Python package mirrors. An AI assistant needs
Home Assistant to be reachable from the internet (e.g. Nabu Casa); the nightly run
needs access to Claude and GitHub.

## Is there a German version?

The card, the notifications and the emergency UI speak German and English. The
documentation is in English.

## How do I report a bug?

Open an issue on GitHub with the version, the steps and the relevant log lines. The
repository is public: leave out tokens, names, addresses and screenshots with personal
data.
