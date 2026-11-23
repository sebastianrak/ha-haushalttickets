# Deployment: Proxmox LXC

The server runs in an unprivileged Debian 13 container with PostgreSQL. Home Assistant
talks to it over the LAN (`http://<container>:8080`) with an API token.

## Create the container

On the Proxmox VE host, as root:

```bash
curl -fsSLO https://raw.githubusercontent.com/sebastianrak/ha-haushalttickets/main/deploy/proxmox/create-lxc.sh
less create-lxc.sh
bash create-lxc.sh --allowed-clients <home-assistant-ip>
```

Useful options (see `--help`): `--ctid`, `--storage` (default `DATA01`), `--ip 172.16.1.50/24
--gateway 172.16.1.1`, `--vlan`, `--backup-time HH:MM`, `--ref <branch>`.

The script

1. downloads the newest `debian-13-standard` template,
2. creates the container (unprivileged, `nesting=1`, start on boot, tag `haushalttickets`),
3. clones this repository to `/opt/haushalttickets/src` inside it,
4. runs `deploy/lxc/install.sh`, which installs PostgreSQL, the server, the systemd units
   and prints the **API token** once.

Add the container to your vzdump backup job if the job does not back up all guests.

## Layout inside the container

| Path | Content |
|---|---|
| `/opt/haushalttickets/venv` | Server (Python venv, dependencies pinned by hash) |
| `/etc/haushalttickets/server.env` | Settings (`HAUSHALTTICKETS_*`), owned `root:haushalttickets`, mode 0640 |
| `/var/lib/haushalttickets/attachments` | Uploaded files |
| `/var/backups/haushalttickets` | Database dumps (14 days) |
| `haushalttickets.service` | API on port 8080, runs as user `haushalttickets` |
| `haushalttickets-backup.timer` | Daily `pg_dump` at the configured time |

PostgreSQL uses peer authentication over the local socket; there is no database password
and PostgreSQL does not listen on the network.

## Day to day

```bash
haushalttickets-update                  # newest GitHub release
haushalttickets-update --release v0.2.0 # specific release / rollback
haushalttickets-update --ref main       # development branch
haushalttickets-ctl token create NAME   # more tokens
haushalttickets-ctl token revoke NAME
haushalttickets-backup manual           # extra dump
journalctl -u haushalttickets -f        # logs
```

`haushalttickets-update` dumps the database, installs the new version (release assets
verified against `SHA256SUMS`, dependencies against the hashes in `requirements.txt`),
runs the migrations, restarts the service and checks `/api/health`.

## Restore a dump

```bash
systemctl stop haushalttickets
runuser -u postgres -- dropdb haushalttickets
runuser -u postgres -- createdb --owner haushalttickets haushalttickets
runuser -u postgres -- pg_restore --no-owner --role=haushalttickets -d haushalttickets \
  < /var/backups/haushalttickets/<file>.dump
haushalttickets-update --release v<version-of-the-dump>   # when rolling back code too
systemctl start haushalttickets
```

The dump file is passed on stdin because the `postgres` user cannot read the backup
directory.

## Security notes

- The API speaks plain HTTP inside the LAN. Limit it to Home Assistant with
  `HAUSHALTTICKETS_ALLOWED_CLIENTS` (set by `--allowed-clients`); the token is required in
  any case.
- `/api/health` is reachable from everywhere and only reveals the version.
- The systemd unit uses sandboxing (`ProtectSystem=strict` and friends). If the service
  fails with status `226/NAMESPACE`, check that `nesting=1` is set on the container.
