#!/usr/bin/env bash
# Create the Haushalttickets container on a Proxmox VE host and install the server.
#
#   curl -fsSLO https://raw.githubusercontent.com/sebastianrak/ha-haushalttickets/main/deploy/proxmox/create-lxc.sh
#   less create-lxc.sh                      # read it first
#   bash create-lxc.sh --allowed-clients 172.16.1.10
#
# Creates an unprivileged Debian 13 container, clones the repository into it and runs
# deploy/lxc/install.sh there. Prints the API token for the Home Assistant integration.
set -euo pipefail

REPO_URL="https://github.com/sebastianrak/ha-haushalttickets"
REF="main"
CTID=""
CT_HOSTNAME="haushalttickets"
STORAGE="DATA01"
TEMPLATE_STORAGE="local"
BRIDGE="vmbr0"
VLAN=""
IP="dhcp"
GATEWAY=""
NAMESERVER=""
CORES=2
MEMORY=1024
SWAP=512
DISK=8
ALLOWED_CLIENTS=""
BACKUP_TIME="02:30"

usage() {
  cat <<EOF
Usage: $0 [options]

  --ctid ID                container id (default: next free id)
  --hostname NAME          (default: $CT_HOSTNAME)
  --storage NAME           storage for the root disk (default: $STORAGE)
  --template-storage NAME  storage for the Debian template (default: $TEMPLATE_STORAGE)
  --disk GB                root disk size (default: $DISK)
  --cores N                (default: $CORES)
  --memory MB              (default: $MEMORY)
  --bridge NAME            (default: $BRIDGE)
  --vlan TAG               VLAN tag (default: none)
  --ip CIDR|dhcp           e.g. 172.16.1.50/24 (default: $IP)
  --gateway IP             required with a static --ip
  --nameserver IP          (default: host settings)
  --allowed-clients LIST   comma-separated addresses/networks allowed to use the API,
                           normally the Home Assistant address (default: any client)
  --backup-time HH:MM      daily database dump, before your vzdump window (default: $BACKUP_TIME)
  --ref REF                git branch or tag to install (default: $REF)
EOF
}

die() { echo "Error: $*" >&2; exit 1; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    --ctid) CTID="$2"; shift 2 ;;
    --hostname) CT_HOSTNAME="$2"; shift 2 ;;
    --storage) STORAGE="$2"; shift 2 ;;
    --template-storage) TEMPLATE_STORAGE="$2"; shift 2 ;;
    --disk) DISK="$2"; shift 2 ;;
    --cores) CORES="$2"; shift 2 ;;
    --memory) MEMORY="$2"; shift 2 ;;
    --bridge) BRIDGE="$2"; shift 2 ;;
    --vlan) VLAN="$2"; shift 2 ;;
    --ip) IP="$2"; shift 2 ;;
    --gateway) GATEWAY="$2"; shift 2 ;;
    --nameserver) NAMESERVER="$2"; shift 2 ;;
    --allowed-clients) ALLOWED_CLIENTS="$2"; shift 2 ;;
    --backup-time) BACKUP_TIME="$2"; shift 2 ;;
    --ref) REF="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) usage >&2; die "unknown option $1" ;;
  esac
done

command -v pct >/dev/null || die "run this on a Proxmox VE host"
[[ $EUID -eq 0 ]] || die "run as root"
[[ $IP == dhcp || -n $GATEWAY ]] || die "--gateway is required with a static --ip"
[[ $BACKUP_TIME =~ ^[0-2][0-9]:[0-5][0-9]$ ]] || die "--backup-time must be HH:MM"

if [[ -z $CTID ]]; then
  CTID=$(pvesh get /cluster/nextid)
fi
if pct status "$CTID" >/dev/null 2>&1; then
  die "container $CTID exists already"
fi

echo "==> Debian 13 template"
pveam update >/dev/null
TEMPLATE=$(pveam available --section system | awk '$2 ~ /^debian-13-standard_/ {print $2}' | sort -V | tail -n1)
[[ -n $TEMPLATE ]] || die "no debian-13-standard template available"
if ! pveam list "$TEMPLATE_STORAGE" | grep -q "$TEMPLATE"; then
  pveam download "$TEMPLATE_STORAGE" "$TEMPLATE"
fi

NET="name=eth0,bridge=$BRIDGE,ip=$IP"
[[ -n $GATEWAY ]] && NET+=",gw=$GATEWAY"
[[ -n $VLAN ]] && NET+=",tag=$VLAN"

CREATE_ARGS=(
  --hostname "$CT_HOSTNAME"
  --unprivileged 1
  --features nesting=1
  --cores "$CORES"
  --memory "$MEMORY"
  --swap "$SWAP"
  --rootfs "$STORAGE:$DISK"
  --net0 "$NET"
  --onboot 1
  --timezone host
  --tags haushalttickets
  --description "Haushalttickets server ($REPO_URL)"
)
[[ -n $NAMESERVER ]] && CREATE_ARGS+=(--nameserver "$NAMESERVER")

echo "==> Creating container $CTID"
pct create "$CTID" "$TEMPLATE_STORAGE:vztmpl/$TEMPLATE" "${CREATE_ARGS[@]}"
pct start "$CTID"

echo "==> Waiting for network"
online=0
for _ in $(seq 1 60); do
  if pct exec "$CTID" -- getent hosts github.com >/dev/null 2>&1; then
    online=1
    break
  fi
  sleep 2
done
[[ $online -eq 1 ]] || die "container $CTID has no network (check bridge, VLAN, IP)"

echo "==> Fetching Haushalttickets ($REF)"
pct exec "$CTID" -- bash -c \
  "apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends git ca-certificates >/dev/null"
pct exec "$CTID" -- git clone --quiet --depth 1 --branch "$REF" "$REPO_URL" /opt/haushalttickets/src

INSTALL_ARGS=(--backup-time "$BACKUP_TIME")
[[ -n $ALLOWED_CLIENTS ]] && INSTALL_ARGS+=(--allowed-clients "$ALLOWED_CLIENTS")

echo "==> Installing"
pct exec "$CTID" -- bash /opt/haushalttickets/src/deploy/lxc/install.sh "${INSTALL_ARGS[@]}"

ADDRESS=$(pct exec "$CTID" -- hostname -I | awk '{print $1}')
cat <<EOF

Container $CTID ($CT_HOSTNAME) is running.
  API:     http://$ADDRESS:8080
  Health:  http://$ADDRESS:8080/api/health
Use the token printed above in the Home Assistant integration.
EOF
