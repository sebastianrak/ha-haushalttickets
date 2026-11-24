# Deployment: Proxmox LXC

The server runs in an unprivileged Debian 13 container with PostgreSQL. Home Assistant
talks to it over the LAN (`http://<container>:8080`) with an API token.

## Create the container

On the Proxmox VE host, as root:

```bash
curl -fsSLO https://raw.githubusercontent.com/sebastianrak/ha-haushalttickets/main/deploy/proxmox/create-lxc.sh
less create-lxc.sh
bash create-lxc.sh
```

The script asks for every setting and suggests values: the next free container id,
storages and bridges of the host, the address of the Home Assistant VM (via the QEMU
guest agent) for the API allowlist, and a database dump time 30 minutes before the
first vzdump job. Enter accepts a suggestion, `-` leaves a field empty. Questions are
German when `$LANG` starts with `de`, otherwise English (`--lang de|en`).

Every setting can also be passed as an option (`--help`); with `--yes` nothing is asked.

The script

1. downloads the newest `debian-13-standard` template,
2. creates the container (unprivileged, `nesting=1`, start on boot, tag `haushalttickets`),
3. clones this repository to `/opt/haushalttickets/src` inside it,
4. runs `deploy/lxc/install.sh`, which installs PostgreSQL, the server, the systemd units
   and prints the **API token** once.

At the end the script checks the vzdump job: if it lists guests explicitly, it offers to
add the new container.

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

Without `--release` it installs GitHub's *latest* release, which never is a
pre-release: install a pre-release with `--release v<version>`.

From v0.3.1 on it also replaces the admin tools in `/usr/local/sbin`
(`haushalttickets-update`, `-backup`, `-ctl`) with the ones of the installed version;
in release mode only tools listed in the release's `SHA256SUMS`. Releases before
v0.3.1 carry no tools, so the tools stay as they are when you go back to one.

Updating from v0.3.0 the first time: the v0.3.0 update script does not know about
the tools yet. Fetch the new one once, check it, then update as usual:

```bash
cd /tmp
base=https://github.com/sebastianrak/ha-haushalttickets/releases/download/v0.3.1
curl -fsSLO "$base/SHA256SUMS" && curl -fsSLO "$base/haushalttickets-update"
grep ' haushalttickets-update$' SHA256SUMS | sha256sum --check
install -m 0755 haushalttickets-update /usr/local/sbin/haushalttickets-update
haushalttickets-update
```

## Publishing a release

Push a tag (`git tag v0.4.0 && git push origin v0.4.0`) or create the release in the
GitHub UI. The tag must match the server and integration version. The *Release*
workflow builds the wheel and attaches it with `requirements.txt` and `SHA256SUMS`;
`haushalttickets-update` refuses a release without these assets.

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
