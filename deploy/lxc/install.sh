#!/usr/bin/env bash
# Install Haushalttickets inside a Debian 13 container. Run as root from a checkout of
# the repository (deploy/proxmox/create-lxc.sh does that for you). Safe to re-run.
set -euo pipefail

# A locale that exists in every Debian container. Without it PostgreSQL falls back
# to "C" and creates databases with SQL_ASCII encoding.
export LANG=C.UTF-8 LC_ALL=C.UTF-8
unset LANGUAGE

SRC_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
APP_USER="haushalttickets"
DATA_DIR="/var/lib/haushalttickets"
BACKUP_DIR="/var/backups/haushalttickets"
CONF_DIR="/etc/haushalttickets"
ENV_FILE="$CONF_DIR/server.env"
PORT=8080
ALLOWED_CLIENTS=""
BACKUP_TIME="02:30"
START=1

usage() {
  cat <<EOF
Usage: $0 [--allowed-clients LIST] [--backup-time HH:MM] [--port N] [--no-start]

  --allowed-clients LIST  comma-separated addresses/networks allowed to use the API
  --backup-time HH:MM     daily database dump (default: $BACKUP_TIME)
  --port N                API port (default: $PORT)
  --no-start              install without enabling and starting systemd units
EOF
}

die() { echo "Error: $*" >&2; exit 1; }
have_systemd() { [[ -d /run/systemd/system ]]; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    --allowed-clients) ALLOWED_CLIENTS="$2"; shift 2 ;;
    --backup-time) BACKUP_TIME="$2"; shift 2 ;;
    --port) PORT="$2"; shift 2 ;;
    --no-start) START=0; shift ;;
    -h|--help) usage; exit 0 ;;
    *) usage >&2; die "unknown option $1" ;;
  esac
done

[[ $EUID -eq 0 ]] || die "run as root"
[[ $BACKUP_TIME =~ ^[0-2][0-9]:[0-5][0-9]$ ]] || die "--backup-time must be HH:MM"
[[ -f $SRC_DIR/server/pyproject.toml ]] || die "run from a repository checkout"

echo "==> Packages"
apt-get update -qq
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends \
  postgresql python3 python3-venv ca-certificates curl git >/dev/null

echo "==> User and directories"
if ! id -u "$APP_USER" >/dev/null 2>&1; then
  useradd --system --home-dir "$DATA_DIR" --shell /usr/sbin/nologin "$APP_USER"
fi
install -d -o "$APP_USER" -g "$APP_USER" -m 0750 "$DATA_DIR" "$DATA_DIR/attachments" "$BACKUP_DIR"
install -d -m 0755 "$CONF_DIR"

echo "==> PostgreSQL"
if have_systemd; then
  systemctl enable --now postgresql >/dev/null
fi
if ! runuser -u postgres -- psql -tAc "SELECT 1 FROM pg_roles WHERE rolname = '$APP_USER'" | grep -q 1; then
  runuser -u postgres -- createuser "$APP_USER"
fi
pg() { runuser -u postgres -- psql -X -v ON_ERROR_STOP=1 -tA "$@"; }
ENCODING=$(pg -c "SELECT pg_encoding_to_char(encoding) FROM pg_database WHERE datname = '$APP_USER'")
if [[ -n $ENCODING && $ENCODING != UTF8 ]]; then
  TABLES=$(pg -d "$APP_USER" -c "SELECT count(*) FROM information_schema.tables WHERE table_schema = 'public'")
  if [[ $TABLES -eq 0 ]]; then
    echo "Database $APP_USER uses $ENCODING and is empty, recreating it with UTF8."
    runuser -u postgres -- dropdb "$APP_USER"
    ENCODING=""
  else
    die "database $APP_USER uses $ENCODING and contains tables; convert it to UTF8 first (dump, recreate, restore)"
  fi
fi
if [[ -z $ENCODING ]]; then
  runuser -u postgres -- createdb --owner "$APP_USER" --encoding UTF8 --locale C.UTF-8 \
    --template template0 "$APP_USER"
fi
[[ $(pg -c "SELECT pg_encoding_to_char(encoding) FROM pg_database WHERE datname = '$APP_USER'") == UTF8 ]] ||
  die "database $APP_USER is not UTF8"

echo "==> Configuration"
if [[ ! -f $ENV_FILE ]]; then
  cat >"$ENV_FILE" <<EOF
# Haushalttickets server settings. Restart after changes:
#   systemctl restart haushalttickets
HAUSHALTTICKETS_HOST=0.0.0.0
HAUSHALTTICKETS_PORT=$PORT
HAUSHALTTICKETS_DATABASE_URL=postgresql+psycopg:///$APP_USER
HAUSHALTTICKETS_ATTACHMENTS_DIR=$DATA_DIR/attachments
# Comma-separated addresses or networks allowed to use the API, no spaces.
# Empty = any client (the API token is still required).
HAUSHALTTICKETS_ALLOWED_CLIENTS=$ALLOWED_CLIENTS
EOF
elif [[ -n $ALLOWED_CLIENTS ]]; then
  sed -i "s|^HAUSHALTTICKETS_ALLOWED_CLIENTS=.*|HAUSHALTTICKETS_ALLOWED_CLIENTS=$ALLOWED_CLIENTS|" "$ENV_FILE"
fi
chown root:"$APP_USER" "$ENV_FILE"
chmod 0640 "$ENV_FILE"

echo "==> Tools"
install -m 0755 "$SRC_DIR/deploy/lxc/haushalttickets-update" /usr/local/sbin/haushalttickets-update
install -m 0755 "$SRC_DIR/deploy/lxc/haushalttickets-backup" /usr/local/sbin/haushalttickets-backup
install -m 0755 "$SRC_DIR/deploy/lxc/haushalttickets-ctl" /usr/local/sbin/haushalttickets-ctl
# `pct enter` starts a non-login shell with PATH=/sbin:/bin:/usr/sbin:/usr/bin, so the
# tools above would only work with their full path.
if ! grep -q '# haushalttickets: PATH' /root/.bashrc 2>/dev/null; then
  cat >>/root/.bashrc <<'BASHRC'

# haushalttickets: PATH for the admin tools in /usr/local/sbin
case ":$PATH:" in *:/usr/local/sbin:*) ;; *) PATH="/usr/local/sbin:/usr/local/bin:$PATH" ;; esac
BASHRC
fi
# `pct enter` also passes the host's locale, which the container does not have (Perl
# tools such as pg_dump then warn "Setting locale failed").
if ! grep -q "# haushalttickets: the Proxmox host's locale" /root/.bashrc 2>/dev/null; then
  cat >>/root/.bashrc <<'BASHRC'
# haushalttickets: the Proxmox host's locale (e.g. en_US.UTF-8) does not exist here
export LANG=C.UTF-8
BASHRC
fi

echo "==> Server"
/usr/local/sbin/haushalttickets-update --source "$SRC_DIR" --no-restart

echo "==> API token"
if TOKEN=$(/usr/local/sbin/haushalttickets-ctl token create homeassistant 2>/dev/null); then
  TOKEN_NEW=1
else
  TOKEN_NEW=0
fi

echo "==> systemd"
install -m 0644 "$SRC_DIR/deploy/lxc/systemd/haushalttickets.service" /etc/systemd/system/
install -m 0644 "$SRC_DIR/deploy/lxc/systemd/haushalttickets-backup.service" /etc/systemd/system/
sed "s|@BACKUP_TIME@|$BACKUP_TIME|" "$SRC_DIR/deploy/lxc/systemd/haushalttickets-backup.timer" \
  >/etc/systemd/system/haushalttickets-backup.timer
chmod 0644 /etc/systemd/system/haushalttickets-backup.timer

if [[ $START -eq 1 ]] && have_systemd; then
  systemctl daemon-reload
  systemctl enable --now haushalttickets.service haushalttickets-backup.timer >/dev/null
  systemctl restart haushalttickets.service
  for _ in $(seq 1 30); do
    if curl -fsS "http://127.0.0.1:$PORT/api/health" >/dev/null 2>&1; then
      break
    fi
    sleep 1
  done
  curl -fsS "http://127.0.0.1:$PORT/api/health" >/dev/null || die "server did not start, see: journalctl -u haushalttickets"
  echo "Server running: $(curl -fsS "http://127.0.0.1:$PORT/api/health")"
else
  echo "systemd units installed but not started (--no-start or no systemd)."
fi

if [[ $TOKEN_NEW -eq 1 ]]; then
  cat <<EOF

API token for the Home Assistant integration (shown only once, store it safely):

  $TOKEN

EOF
else
  echo "API token 'homeassistant' exists already. New one: haushalttickets-ctl token revoke homeassistant && haushalttickets-ctl token create homeassistant"
fi
