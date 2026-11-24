#!/usr/bin/env bash
# Create the container for the nightly run (KI-Hausmeister) on a Proxmox VE host and
# install it. Separate from the ticket server's container on purpose (decision 46).
#
#   curl -fsSLO https://raw.githubusercontent.com/sebastianrak/ha-haushalttickets/main/deploy/agent/create-agent-lxc.sh
#   less create-agent-lxc.sh      # read it first
#   bash create-agent-lxc.sh      # asks for every setting, suggests sensible values
#
# Options skip their question; --yes asks nothing (missing values use the suggestions).
set -euo pipefail

REPO_URL="https://github.com/sebastianrak/ha-haushalttickets"
REF="main"
SWAP=1024

CTID="" CT_HOSTNAME="" STORAGE="" TEMPLATE_STORAGE="" DISK="" CORES="" MEMORY=""
BRIDGE="" VLAN="" IP="" GATEWAY="" NAMESERVER="" HA_CA=""
declare -A GIVEN=()
ASSUME_YES=0

usage() {
  cat <<EOF
Usage: $0 [options]

  --ctid ID                container id
  --hostname NAME          container hostname (default ki-hausmeister)
  --storage NAME           storage for the root disk
  --template-storage NAME  storage for the Debian template
  --disk GB                root disk size (default 16)
  --cores N                CPU cores (default 2)
  --memory MB              memory (default 4096; Claude Code needs 4 GB)
  --bridge NAME            network bridge
  --vlan TAG               VLAN tag ("" for none)
  --ip CIDR|dhcp           e.g. 172.16.3.22/24 or dhcp
  --gateway IP             gateway for a static --ip
  --nameserver IP          DNS server ("" = settings of the host)
  --ha-ca FILE             CA certificate (PEM, on this host) of Home Assistant's certificate
  --ref REF                git branch or tag to install (default: $REF)
  -y, --yes                ask nothing
EOF
}

set_opt() { printf -v "$1" '%s' "$2"; GIVEN[$1]=1; }
while [[ $# -gt 0 ]]; do
  case "$1" in
    --ctid) set_opt CTID "$2"; shift 2 ;;
    --hostname) set_opt CT_HOSTNAME "$2"; shift 2 ;;
    --storage) set_opt STORAGE "$2"; shift 2 ;;
    --template-storage) set_opt TEMPLATE_STORAGE "$2"; shift 2 ;;
    --disk) set_opt DISK "$2"; shift 2 ;;
    --cores) set_opt CORES "$2"; shift 2 ;;
    --memory) set_opt MEMORY "$2"; shift 2 ;;
    --bridge) set_opt BRIDGE "$2"; shift 2 ;;
    --vlan) set_opt VLAN "$2"; shift 2 ;;
    --ip) set_opt IP "$2"; shift 2 ;;
    --gateway) set_opt GATEWAY "$2"; shift 2 ;;
    --nameserver) set_opt NAMESERVER "$2"; shift 2 ;;
    --ha-ca) set_opt HA_CA "$2"; shift 2 ;;
    --ref) REF="$2"; shift 2 ;;
    -y|--yes) ASSUME_YES=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) usage >&2; echo "Unknown option: $1" >&2; exit 2 ;;
  esac
done

say() { printf '%s\n' "$*"; }
step() { printf '\n==> %s\n' "$*"; }
die() { printf 'Error: %s\n' "$*" >&2; exit 1; }

INTERACTIVE=1
[[ $ASSUME_YES -eq 1 ]] && INTERACTIVE=0
[[ $INTERACTIVE -eq 0 || -t 0 ]] || die "no terminal: download the script and run 'bash create-agent-lxc.sh', or pass --yes"
command -v pct >/dev/null || die "run this on a Proxmox VE host"
[[ $EUID -eq 0 ]] || die "run as root"

is_uint() { [[ $1 =~ ^[1-9][0-9]*$ ]]; }
is_ctid() { is_uint "$1" && (($1 >= 100)) && ! pct status "$1" >/dev/null 2>&1 && ! qm status "$1" >/dev/null 2>&1; }
is_hostname() { [[ $1 =~ ^[a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?$ ]]; }
is_ipv4() {
  [[ $1 =~ ^([0-9]{1,3})\.([0-9]{1,3})\.([0-9]{1,3})\.([0-9]{1,3})$ ]] || return 1
  local octet
  for octet in "${BASH_REMATCH[@]:1}"; do ((octet <= 255)) || return 1; done
}
is_ipv4_or_empty() { [[ -z $1 ]] || is_ipv4 "$1"; }
is_cidr() { [[ $1 == */* ]] && is_ipv4 "${1%/*}" && [[ ${1#*/} =~ ^[0-9]{1,2}$ ]] && ((${1#*/} <= 32)); }
is_ip_or_dhcp() { [[ $1 == dhcp ]] || is_cidr "$1"; }
is_vlan() { [[ -z $1 ]] || { is_uint "$1" && (($1 <= 4094)); }; }
is_file_or_empty() { [[ -z $1 || -f $1 ]]; }
is_in_list() { local needle=$1; shift; local item; for item in "$@"; do [[ $item == "$needle" ]] && return 0; done; return 1; }
is_rootdir_storage() { is_in_list "$1" "${ROOTDIR_STORAGES[@]}"; }
is_template_storage() { is_in_list "$1" "${TEMPLATE_STORAGES[@]}"; }
is_bridge() { is_in_list "$1" "${BRIDGES[@]}"; }
is_memory() { is_uint "$1" && (($1 >= 2048)); }

ask() {  # ask VAR "question" "suggestion" validator
  local var=$1 question=$2 suggestion=$3 check=$4 value
  if [[ -n ${GIVEN[$var]:-} ]]; then
    "$check" "${!var}" || die "invalid value for $var: '${!var}'"
    return 0
  fi
  if [[ $INTERACTIVE -eq 0 ]]; then
    "$check" "$suggestion" || die "no valid suggestion for $var, pass it as an option"
    printf -v "$var" '%s' "$suggestion"
    return 0
  fi
  while true; do
    read -rp "$question [${suggestion}]: " value
    if [[ $value == "-" ]]; then value=""; else value=${value:-$suggestion}; fi
    if "$check" "$value"; then printf -v "$var" '%s' "$value"; return 0; fi
    say "  invalid input, please try again" >&2
  done
}

mapfile -t ROOTDIR_STORAGES < <(pvesm status --content rootdir --enabled 1 2>/dev/null | awk 'NR > 1 {print $1}')
mapfile -t TEMPLATE_STORAGES < <(pvesm status --content vztmpl --enabled 1 2>/dev/null | awk 'NR > 1 {print $1}')
mapfile -t BRIDGES < <(ip -o link show type bridge 2>/dev/null | awk -F': ' '{print $2}' | cut -d@ -f1)
((${#ROOTDIR_STORAGES[@]})) || die "no storage for containers found"
((${#TEMPLATE_STORAGES[@]})) || die "no storage for templates found"
((${#BRIDGES[@]})) || die "no network bridge found"
pick() { local preferred=$1; shift; if is_in_list "$preferred" "$@"; then printf '%s' "$preferred"; else printf '%s' "$1"; fi; }
HOST_GATEWAY=$(ip route show default 2>/dev/null | awk '{print $3; exit}')

[[ $INTERACTIVE -eq 1 ]] && say "Nightly run (KI-Hausmeister) - create container. Enter accepts [suggestion], '-' leaves a field empty."
step "Container"
ask CTID "Container ID" "$(pvesh get /cluster/nextid)" is_ctid
ask CT_HOSTNAME "Hostname" "ki-hausmeister" is_hostname
ask STORAGE "Storage for the root disk (${ROOTDIR_STORAGES[*]})" "$(pick DATA01 "${ROOTDIR_STORAGES[@]}")" is_rootdir_storage
ask DISK "Root disk size in GB" "16" is_uint
if ((${#TEMPLATE_STORAGES[@]} > 1)) || [[ -n ${GIVEN[TEMPLATE_STORAGE]:-} ]]; then
  ask TEMPLATE_STORAGE "Storage for the Debian template (${TEMPLATE_STORAGES[*]})" "$(pick local "${TEMPLATE_STORAGES[@]}")" is_template_storage
else
  TEMPLATE_STORAGE=${TEMPLATE_STORAGES[0]}
fi
ask CORES "CPU cores" "2" is_uint
ask MEMORY "Memory in MB (at least 2048; Claude Code wants 4096)" "4096" is_memory
step "Network"
ask BRIDGE "Bridge (${BRIDGES[*]})" "$(pick vmbr0 "${BRIDGES[@]}")" is_bridge
ask VLAN "VLAN tag (empty = none)" "" is_vlan
ask IP "IP address with prefix (fixed: the ticket server allows only this address), or dhcp" "dhcp" is_ip_or_dhcp
if [[ $IP != dhcp ]]; then
  ask GATEWAY "Gateway" "$HOST_GATEWAY" is_ipv4
  ask NAMESERVER "DNS server (empty = same as the host)" "" is_ipv4_or_empty
fi
step "Home Assistant"
ask HA_CA "CA certificate (PEM file on this host) that signed Home Assistant's certificate, empty if none" "" is_file_or_empty

printf '\nSummary\n'
printf '  %-12s %s\n' "Container:" "$CTID ($CT_HOSTNAME), Debian 13, unprivileged, start on boot"
printf '  %-12s %s\n' "Root disk:" "$STORAGE, $DISK GB"
printf '  %-12s %s\n' "Resources:" "$CORES cores, $MEMORY MB"
printf '  %-12s %s\n' "Network:" "$BRIDGE${VLAN:+, VLAN $VLAN}, $IP${GATEWAY:+, gw $GATEWAY}${NAMESERVER:+, dns $NAMESERVER}"
printf '  %-12s %s\n' "HA CA:" "${HA_CA:-none}"
printf '  %-12s %s\n\n' "Version:" "$REF"
if [[ $INTERACTIVE -eq 1 ]]; then
  read -rp "Create the container now? [y/N]: " answer
  [[ $answer =~ ^[yYjJ] ]] || { say "Cancelled, nothing changed."; exit 0; }
fi

step "Debian 13 template"
pveam update >/dev/null
ARCH=$(dpkg --print-architecture)
TEMPLATE=$(pveam available --section system |
  awk -v arch="$ARCH" '$2 ~ ("^debian-13-standard_.*_" arch "\\.tar") {print $2}' | sort -V | tail -n1)
[[ -n $TEMPLATE ]] || die "no debian-13-standard template for $ARCH available"
pveam list "$TEMPLATE_STORAGE" | grep -q "$TEMPLATE" || pveam download "$TEMPLATE_STORAGE" "$TEMPLATE"

NET="name=eth0,bridge=$BRIDGE,ip=$IP"
[[ -n $GATEWAY ]] && NET+=",gw=$GATEWAY"
[[ -n $VLAN ]] && NET+=",tag=$VLAN"
CREATE_ARGS=(
  --hostname "$CT_HOSTNAME" --unprivileged 1 --features nesting=1
  --cores "$CORES" --memory "$MEMORY" --swap "$SWAP" --rootfs "$STORAGE:$DISK"
  --net0 "$NET" --onboot 1 --timezone host --tags "haushalttickets;ki"
  --description "Haushalttickets nightly run / KI-Hausmeister ($REPO_URL)"
)
[[ -n $NAMESERVER ]] && CREATE_ARGS+=(--nameserver "$NAMESERVER")
ct_exec() { pct exec "$CTID" -- env LANG=C.UTF-8 LC_ALL=C.UTF-8 "$@"; }

CREATED_CT=""
cleanup_hint() {
  local status=$?
  if [[ $status -ne 0 && -n $CREATED_CT ]]; then
    printf '\nAborted after creating the container. Remove it with:\n  pct destroy %s --force\n' "$CREATED_CT" >&2
  fi
}
trap cleanup_hint EXIT

step "Creating container $CTID"
pct create "$CTID" "$TEMPLATE_STORAGE:vztmpl/$TEMPLATE" "${CREATE_ARGS[@]}"
CREATED_CT=$CTID
pct start "$CTID"

step "Waiting for network"
online=0
for _ in $(seq 1 60); do
  if ct_exec getent hosts github.com >/dev/null 2>&1; then online=1; break; fi
  sleep 2
done
[[ $online -eq 1 ]] || die "container $CTID has no network (check bridge, VLAN, IP)"

step "Fetching Haushalttickets ($REF)"
ct_exec bash -c "apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends git ca-certificates >/dev/null"
ct_exec git clone --quiet --depth 1 --branch "$REF" "$REPO_URL" /opt/haushalttickets-agent/src

INSTALL_ARGS=()
if [[ -n $HA_CA ]]; then
  pct push "$CTID" "$HA_CA" /root/ha-ca.crt
  INSTALL_ARGS+=(--ha-ca /root/ha-ca.crt)
fi
step "Installing inside the container"
ct_exec bash /opt/haushalttickets-agent/src/deploy/agent/install-agent.sh "${INSTALL_ARGS[@]}"

ADDRESS=$(ct_exec hostname -I | awk '{print $1}')
cat <<EOF

Done. Container $CTID ($CT_HOSTNAME) runs at $ADDRESS.
Next:
  1. Ticket container:  haushalttickets-ctl nightly-setup   (address $ADDRESS, prints the runner token)
  2. This container:    pct enter $CTID, then haushalttickets-agent-ctl setup
  3. vzdump: add container $CTID to your backup job (it holds tokens and the export).
EOF
