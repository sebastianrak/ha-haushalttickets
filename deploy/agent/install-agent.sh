#!/usr/bin/env bash
# Install the nightly run (KI-Hausmeister) inside its own Debian 13 container. Run as
# root from a checkout of the repository (create-agent-lxc.sh does that for you).
# Safe to re-run; settings in /etc/haushalttickets-agent/agent.env are kept.
set -euo pipefail
export LANG=C.UTF-8 LC_ALL=C.UTF-8
unset LANGUAGE

SRC_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
AGENT_USER="hausmeister"
APP_DIR="/opt/haushalttickets-agent"
STATE_DIR="/var/lib/haushalttickets-agent"
CONF_DIR="/etc/haushalttickets-agent"
ENV_FILE="$CONF_DIR/agent.env"
HA_CA=""
SKIP_CLAUDE=0

usage() {
  cat <<EOF
Usage: $0 [--ha-ca FILE] [--skip-claude]

  --ha-ca FILE    CA certificate (PEM) that signed Home Assistant's certificate
  --skip-claude   do not install Claude Code (tests)
EOF
}

die() { echo "Error: $*" >&2; exit 1; }
have_systemd() { [[ -d /run/systemd/system ]]; }
as_agent() { runuser -u "$AGENT_USER" -- env HOME="$STATE_DIR" LANG=C.UTF-8 "$@"; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    --ha-ca) HA_CA="$2"; shift 2 ;;
    --skip-claude) SKIP_CLAUDE=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) usage >&2; die "unknown option $1" ;;
  esac
done

[[ $EUID -eq 0 ]] || die "run as root"
[[ -f $SRC_DIR/agent/pyproject.toml ]] || die "run from a repository checkout"
[[ -z $HA_CA || -f $HA_CA ]] || die "CA file $HA_CA not found"

echo "==> Packages"
apt-get update -qq
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends \
  python3 python3-venv ca-certificates curl git openssh-client >/dev/null
python3 -c 'import sys; sys.exit(sys.version_info < (3, 13))' || die "Python 3.13 or newer needed (Debian 13)"

echo "==> User and directories"
if ! id -u "$AGENT_USER" >/dev/null 2>&1; then
  useradd --system --home-dir "$STATE_DIR" --shell /usr/sbin/nologin "$AGENT_USER"
fi
install -d -o "$AGENT_USER" -g "$AGENT_USER" -m 0700 "$STATE_DIR" "$STATE_DIR/.ssh"
install -d -m 0755 "$APP_DIR"
install -d -o root -g "$AGENT_USER" -m 0750 "$CONF_DIR"

if [[ -n $HA_CA ]]; then
  echo "==> CA for Home Assistant"
  install -m 0644 "$HA_CA" /usr/local/share/ca-certificates/haushalttickets-ha.crt
  update-ca-certificates >/dev/null
fi

echo "==> Runner (Python)"
[[ -x $APP_DIR/venv/bin/python ]] || python3 -m venv "$APP_DIR/venv"
"$APP_DIR/venv/bin/pip" install --quiet --disable-pip-version-check --require-hashes \
  -r "$SRC_DIR/agent/requirements.txt"
"$APP_DIR/venv/bin/pip" install --quiet --disable-pip-version-check --no-deps --force-reinstall \
  "$SRC_DIR/agent"

echo "==> ha-mcp $(cat "$SRC_DIR/deploy/agent/ha-mcp.version")"
[[ -x $APP_DIR/ha-mcp/bin/python ]] || python3 -m venv "$APP_DIR/ha-mcp"
"$APP_DIR/ha-mcp/bin/pip" install --quiet --disable-pip-version-check --require-hashes \
  -r "$SRC_DIR/deploy/agent/ha-mcp-requirements.txt"

echo "==> Claude Code settings (managed, the AI cannot change them)"
install -d -m 0755 /etc/claude-code
install -m 0644 "$SRC_DIR/deploy/agent/managed-settings.json" /etc/claude-code/managed-settings.json

if [[ $SKIP_CLAUDE -eq 0 ]]; then
  echo "==> Claude Code (native installer, as $AGENT_USER)"
  if [[ ! -x $STATE_DIR/.local/bin/claude ]]; then
    as_agent bash -c 'curl -fsSL https://claude.ai/install.sh | bash' >/dev/null
  fi
  as_agent "$STATE_DIR/.local/bin/claude" --version
fi

echo "==> Configuration"
if [[ ! -f $ENV_FILE ]]; then
  install -o root -g "$AGENT_USER" -m 0640 "$SRC_DIR/deploy/agent/agent.env.example" "$ENV_FILE"
  echo "Created $ENV_FILE (fill in with: haushalttickets-agent-ctl setup)"
fi

echo "==> Deploy key for the private configuration repository"
if [[ ! -f $STATE_DIR/.ssh/ha-config ]]; then
  as_agent ssh-keygen -q -t ed25519 -N "" -C "ki-hausmeister@$(hostname)" -f "$STATE_DIR/.ssh/ha-config"
fi

echo "==> Tools"
for tool in haushalttickets-agent-ctl haushalttickets-agent-update; do
  install -m 0755 "$SRC_DIR/deploy/agent/$tool" "/usr/local/sbin/$tool"
done

echo "==> systemd"
install -m 0644 "$SRC_DIR/deploy/agent/systemd/haushalttickets-nightly.service" \
  "$SRC_DIR/deploy/agent/systemd/haushalttickets-nightly.timer" /etc/systemd/system/
if have_systemd; then
  systemctl daemon-reload
fi

cat <<EOF

Installed. Next steps:
  1. GitHub: create a PRIVATE repository for the configuration export and add this
     deploy key with write access:
       $(cat "$STATE_DIR/.ssh/ha-config.pub")
  2. haushalttickets-agent-ctl setup    # tokens, addresses, Claude login, test, timer
EOF
