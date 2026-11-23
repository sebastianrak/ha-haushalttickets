#!/usr/bin/env bash
# Create the Haushalttickets container on a Proxmox VE host and install the server.
#
#   curl -fsSLO https://raw.githubusercontent.com/sebastianrak/ha-haushalttickets/main/deploy/proxmox/create-lxc.sh
#   less create-lxc.sh          # read it first
#   bash create-lxc.sh          # asks for every setting, suggests sensible values
#
# Every setting can also be passed as an option; options are not asked again.
# With --yes nothing is asked (missing values use the suggestions).
#
# Creates an unprivileged Debian 13 container, clones the repository into it and runs
# deploy/lxc/install.sh there, which prints the API token for Home Assistant.
set -euo pipefail

REPO_URL="https://github.com/sebastianrak/ha-haushalttickets"
REF="main"
SWAP=512

CTID="" CT_HOSTNAME="" STORAGE="" TEMPLATE_STORAGE="" DISK="" CORES="" MEMORY=""
BRIDGE="" VLAN="" IP="" GATEWAY="" NAMESERVER="" ALLOWED_CLIENTS="" BACKUP_TIME=""
declare -A GIVEN=()
ASSUME_YES=0
UI_LANG=""

usage() {
  cat <<EOF
Usage: $0 [options]

Without options the script asks for each setting.

  --ctid ID                container id
  --hostname NAME          container hostname
  --storage NAME           storage for the root disk
  --template-storage NAME  storage for the Debian template
  --disk GB                root disk size
  --cores N                CPU cores
  --memory MB              memory
  --bridge NAME            network bridge
  --vlan TAG               VLAN tag ("" for none)
  --ip CIDR|dhcp           e.g. 172.16.1.50/24 or dhcp
  --gateway IP             gateway for a static --ip
  --nameserver IP          DNS server ("" = settings of the host)
  --allowed-clients LIST   addresses/networks allowed to use the API (Home Assistant)
  --backup-time HH:MM      daily database dump, before the vzdump window
  --ref REF                git branch or tag to install (default: $REF)
  --lang de|en             language of the questions (default: from \$LANG)
  -y, --yes                ask nothing, use options and suggestions
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
    --allowed-clients) set_opt ALLOWED_CLIENTS "$2"; shift 2 ;;
    --backup-time) set_opt BACKUP_TIME "$2"; shift 2 ;;
    --ref) REF="$2"; shift 2 ;;
    --lang) UI_LANG="$2"; shift 2 ;;
    -y|--yes) ASSUME_YES=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) usage >&2; echo "Unknown option: $1" >&2; exit 2 ;;
  esac
done

if [[ -z $UI_LANG ]]; then
  case "${LC_ALL:-${LANG:-}}" in de*) UI_LANG="de" ;; *) UI_LANG="en" ;; esac
fi

# L "deutsch" "english"
L() { if [[ $UI_LANG == de ]]; then printf '%s' "$1"; else printf '%s' "$2"; fi; }
say() { printf '%s\n' "$*"; }
step() { printf '\n==> %s\n' "$*"; }
die() { printf '%s %s\n' "$(L "Fehler:" "Error:")" "$*" >&2; exit 1; }

INTERACTIVE=1
if [[ $ASSUME_YES -eq 1 ]]; then
  INTERACTIVE=0
elif [[ ! -t 0 ]]; then
  die "$(L "Kein Terminal. Skript herunterladen und mit 'bash create-lxc.sh' starten oder Optionen mit --yes übergeben." \
           "No terminal. Download the script and run 'bash create-lxc.sh', or pass options with --yes.")"
fi

command -v pct >/dev/null || die "$(L "Bitte auf einem Proxmox-VE-Host ausführen." "Run this on a Proxmox VE host.")"
[[ $EUID -eq 0 ]] || die "$(L "Bitte als root ausführen." "Run as root.")"

# Validators

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
is_time() { [[ $1 =~ ^([01][0-9]|2[0-3]):[0-5][0-9]$ ]]; }
is_clients() {
  [[ -z $1 ]] && return 0
  local item
  local -a items
  IFS=',' read -ra items <<<"$1"
  for item in "${items[@]}"; do
    is_ipv4 "$item" || is_cidr "$item" || [[ $item == *:* ]] || return 1
  done
}
is_in_list() { local needle=$1; shift; local item; for item in "$@"; do [[ $item == "$needle" ]] && return 0; done; return 1; }
is_rootdir_storage() { is_in_list "$1" "${ROOTDIR_STORAGES[@]}"; }
is_template_storage() { is_in_list "$1" "${TEMPLATE_STORAGES[@]}"; }
is_bridge() { is_in_list "$1" "${BRIDGES[@]}"; }

# ask VAR "question" "suggestion" validator
ask() {
  local var=$1 question=$2 suggestion=$3 check=$4 value
  if [[ -n ${GIVEN[$var]:-} ]]; then
    value=${!var}
    "$check" "$value" || die "$(L "Ungültiger Wert für" "Invalid value for") $var: '$value'"
    return 0
  fi
  if [[ $INTERACTIVE -eq 0 ]]; then
    "$check" "$suggestion" || die "$(L "Kein gültiger Vorschlag für" "No valid suggestion for") $var, $(L "bitte als Option angeben" "pass it as an option")"
    printf -v "$var" '%s' "$suggestion"
    return 0
  fi
  while true; do
    read -rp "$question [${suggestion}]: " value
    if [[ $value == "-" ]]; then
      value=""
    else
      value=${value:-$suggestion}
    fi
    if "$check" "$value"; then
      printf -v "$var" '%s' "$value"
      return 0
    fi
    say "  $(L "Ungültige Eingabe, bitte erneut." "Invalid input, please try again.")" >&2
  done
}

confirm() {
  local answer
  read -rp "$1 [$(L "j/N" "y/N")]: " answer
  [[ $answer =~ ^[jJyY] ]]
}

# Discovery

mapfile -t ROOTDIR_STORAGES < <(pvesm status --content rootdir --enabled 1 2>/dev/null | awk 'NR > 1 {print $1}')
mapfile -t TEMPLATE_STORAGES < <(pvesm status --content vztmpl --enabled 1 2>/dev/null | awk 'NR > 1 {print $1}')
mapfile -t BRIDGES < <(ip -o link show type bridge 2>/dev/null | awk -F': ' '{print $2}' | cut -d@ -f1)
((${#ROOTDIR_STORAGES[@]})) || die "$(L "Kein Storage für Container gefunden." "No storage for containers found.")"
((${#TEMPLATE_STORAGES[@]})) || die "$(L "Kein Storage für Vorlagen gefunden." "No storage for templates found.")"
((${#BRIDGES[@]})) || die "$(L "Keine Netzwerk-Bridge gefunden." "No network bridge found.")"

pick() { local preferred=$1; shift; if is_in_list "$preferred" "$@"; then printf '%s' "$preferred"; else printf '%s' "$1"; fi; }

detect_ha_ip() {
  command -v python3 >/dev/null || return 1
  local vmid
  for vmid in $(qm list 2>/dev/null | awk 'NR > 1 && tolower($2) ~ /haos|hass|home.?assistant/ {print $1}'); do
    if qm guest cmd "$vmid" network-get-interfaces 2>/dev/null | python3 -c '
import json, sys
for iface in json.load(sys.stdin):
    name = iface.get("name", "")
    if name == "lo" or name.startswith(("docker", "hassio", "veth")):
        continue
    for addr in iface.get("ip-addresses", []):
        ip = addr.get("ip-address", "")
        if addr.get("ip-address-type") == "ipv4" and not ip.startswith(("127.", "169.254.", "172.30.")):
            print(ip)
            sys.exit(0)
sys.exit(1)
'; then
      return 0
    fi
  done
  return 1
}

# Prints: id time suggested_dump all vmids pool  (of the first enabled job with a time)
detect_backup_job() {
  command -v python3 >/dev/null || return 1
  pvesh get /cluster/backup --output-format json 2>/dev/null | python3 -c '
import json, re, sys
for job in json.load(sys.stdin):
    if str(job.get("enabled", 1)) in ("0", "False"):
        continue
    match = re.search(r"(\d{1,2}):(\d{2})", str(job.get("schedule") or job.get("starttime") or ""))
    if not match:
        continue
    hour, minute = int(match.group(1)), int(match.group(2))
    dump = (hour * 60 + minute - 30) % 1440
    print(job.get("id", "-"), f"{hour:02d}:{minute:02d}", f"{dump // 60:02d}:{dump % 60:02d}",
          1 if str(job.get("all", 0)) in ("1", "True") else 0,
          job.get("vmid") or "-", job.get("pool") or "-")
    sys.exit(0)
sys.exit(1)
'
}

HA_IP=$(detect_ha_ip || true)
read -r JOB_ID JOB_TIME JOB_DUMP JOB_ALL JOB_VMIDS JOB_POOL < <(detect_backup_job || echo "- - - - - -")
HOST_GATEWAY=$(ip route show default 2>/dev/null | awk '{print $3; exit}')

# Questions

if [[ $INTERACTIVE -eq 1 ]]; then
  say "$(L "Haushalttickets – Container anlegen. Enter übernimmt den Vorschlag in [Klammern], '-' lässt ein Feld leer." \
           "Haushalttickets – create container. Enter accepts the suggestion in [brackets], '-' leaves a field empty.")"
fi

step "$(L "Container" "Container")"
ask CTID "$(L "Container-ID" "Container ID")" "$(pvesh get /cluster/nextid)" is_ctid
ask CT_HOSTNAME "$(L "Hostname" "Hostname")" "haushalttickets" is_hostname

step "$(L "Speicher" "Storage")"
[[ $INTERACTIVE -eq 1 && -z ${GIVEN[STORAGE]:-} ]] && pvesm status --content rootdir --enabled 1 | sed 's/^/  /'
ask STORAGE "$(L "Storage für die Root-Disk" "Storage for the root disk")" "$(pick DATA01 "${ROOTDIR_STORAGES[@]}")" is_rootdir_storage
ask DISK "$(L "Größe der Root-Disk in GB" "Root disk size in GB")" "8" is_uint
if ((${#TEMPLATE_STORAGES[@]} > 1)) || [[ -n ${GIVEN[TEMPLATE_STORAGE]:-} ]]; then
  ask TEMPLATE_STORAGE "$(L "Storage für die Debian-Vorlage" "Storage for the Debian template") (${TEMPLATE_STORAGES[*]})" \
    "$(pick local "${TEMPLATE_STORAGES[@]}")" is_template_storage
else
  TEMPLATE_STORAGE=${TEMPLATE_STORAGES[0]}
fi

step "$(L "Leistung" "Resources")"
ask CORES "$(L "CPU-Kerne" "CPU cores")" "2" is_uint
ask MEMORY "$(L "Arbeitsspeicher in MB" "Memory in MB")" "1024" is_uint

step "$(L "Netzwerk" "Network")"
ask BRIDGE "$(L "Bridge" "Bridge") (${BRIDGES[*]})" "$(pick vmbr0 "${BRIDGES[@]}")" is_bridge
ask VLAN "$(L "VLAN-Tag (leer = keiner)" "VLAN tag (empty = none)")" "" is_vlan
ask IP "$(L "IP-Adresse mit Präfix, z. B. 172.16.1.50/24, oder dhcp" "IP address with prefix, e.g. 172.16.1.50/24, or dhcp")" "dhcp" is_ip_or_dhcp
if [[ $IP != dhcp ]]; then
  ask GATEWAY "$(L "Gateway" "Gateway")" "$HOST_GATEWAY" is_ipv4
  ask NAMESERVER "$(L "DNS-Server (leer = wie der Host)" "DNS server (empty = same as the host)")" "" is_ipv4_or_empty
elif [[ $INTERACTIVE -eq 1 ]]; then
  say "  $(L "Tipp: Mit DHCP bitte eine feste Zuweisung im Router anlegen, die HA-Integration nutzt die Adresse dauerhaft." \
             "Tip: with DHCP, reserve the address in your router; the Home Assistant integration keeps using it.")"
fi

step "$(L "Zugriff" "Access")"
if [[ -n $HA_IP && $INTERACTIVE -eq 1 ]]; then
  say "  $(L "Home Assistant gefunden unter" "Found Home Assistant at") $HA_IP"
fi
ask ALLOWED_CLIENTS "$(L "Adressen, die die API nutzen dürfen (Home Assistant), durch Komma getrennt, leer = alle" \
                         "Addresses allowed to use the API (Home Assistant), comma-separated, empty = any")" "$HA_IP" is_clients
if [[ -z $ALLOWED_CLIENTS ]]; then
  say "  $(L "Hinweis: Jede Adresse im Netz kann die API erreichen; das Token ist trotzdem nötig." \
             "Note: any address on the network can reach the API; the token is still required.")"
fi

step "$(L "Sicherung" "Backup")"
if [[ $JOB_ID != "-" && $INTERACTIVE -eq 1 ]]; then
  say "  $(L "vzdump-Job" "vzdump job") $JOB_ID $(L "startet um" "starts at") $JOB_TIME"
fi
DUMP_SUGGESTION="02:30"
[[ $JOB_DUMP != "-" ]] && DUMP_SUGGESTION=$JOB_DUMP
ask BACKUP_TIME "$(L "Uhrzeit des täglichen Datenbank-Dumps (vor vzdump)" "Time of the daily database dump (before vzdump)")" "$DUMP_SUGGESTION" is_time

# Summary

row() { printf '  %-14s %s\n' "$1:" "$2"; }
printf '\n%s\n' "$(L "Zusammenfassung" "Summary")"
row "$(L "Container" "Container")" "$CTID ($CT_HOSTNAME), Debian 13, $(L "unprivilegiert" "unprivileged"), nesting, $(L "Autostart" "start on boot")"
row "$(L "Root-Disk" "Root disk")" "$STORAGE, $DISK GB ($(L "Vorlage" "template"): $TEMPLATE_STORAGE)"
row "$(L "Leistung" "Resources")" "$CORES $(L "Kerne" "cores"), $MEMORY MB"
row "$(L "Netzwerk" "Network")" "$BRIDGE${VLAN:+, VLAN $VLAN}, $IP${GATEWAY:+, gw $GATEWAY}${NAMESERVER:+, dns $NAMESERVER}"
row "$(L "API-Zugriff" "API access")" "${ALLOWED_CLIENTS:-$(L "alle Adressen" "any address")}"
row "$(L "DB-Dump" "DB dump")" "$(L "täglich" "daily") $BACKUP_TIME"
row "$(L "Version" "Version")" "$REF"
echo

if [[ $INTERACTIVE -eq 1 ]]; then
  confirm "$(L "Container jetzt anlegen?" "Create the container now?")" || { say "$(L "Abgebrochen, nichts geändert." "Cancelled, nothing changed.")"; exit 0; }
fi

# Create

step "$(L "Debian-13-Vorlage" "Debian 13 template")"
pveam update >/dev/null
# Proxmox offers the template for several architectures; pick the host's (amd64, arm64).
ARCH=$(dpkg --print-architecture)
TEMPLATE=$(pveam available --section system |
  awk -v arch="$ARCH" '$2 ~ ("^debian-13-standard_.*_" arch "\\.tar") {print $2}' | sort -V | tail -n1)
[[ -n $TEMPLATE ]] || die "$(L "Keine debian-13-standard-Vorlage für $ARCH verfügbar." "No debian-13-standard template for $ARCH available.")"
say "$TEMPLATE"
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

CREATED_CT=""
cleanup_hint() {
  local status=$?
  if [[ $status -ne 0 && -n $CREATED_CT ]]; then
    printf '\n%s\n  pct destroy %s --force\n' \
      "$(L "Abbruch nach dem Anlegen. Den Container entfernst du mit:" "Aborted after creating the container. Remove it with:")" \
      "$CREATED_CT" >&2
  fi
}
trap cleanup_hint EXIT

step "$(L "Container $CTID anlegen" "Creating container $CTID")"
pct create "$CTID" "$TEMPLATE_STORAGE:vztmpl/$TEMPLATE" "${CREATE_ARGS[@]}"
CREATED_CT=$CTID
pct start "$CTID"

step "$(L "Warte auf Netzwerk" "Waiting for network")"
online=0
for _ in $(seq 1 60); do
  if pct exec "$CTID" -- getent hosts github.com >/dev/null 2>&1; then
    online=1
    break
  fi
  sleep 2
done
[[ $online -eq 1 ]] || die "$(L "Container $CTID hat kein Netzwerk (Bridge, VLAN, IP prüfen)." \
                               "Container $CTID has no network (check bridge, VLAN, IP).")"

step "$(L "Haushalttickets holen" "Fetching Haushalttickets") ($REF)"
pct exec "$CTID" -- bash -c \
  "apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends git ca-certificates >/dev/null"
pct exec "$CTID" -- git clone --quiet --depth 1 --branch "$REF" "$REPO_URL" /opt/haushalttickets/src

INSTALL_ARGS=(--backup-time "$BACKUP_TIME")
[[ -n $ALLOWED_CLIENTS ]] && INSTALL_ARGS+=(--allowed-clients "$ALLOWED_CLIENTS")

step "$(L "Installation im Container" "Installing inside the container")"
pct exec "$CTID" -- bash /opt/haushalttickets/src/deploy/lxc/install.sh "${INSTALL_ARGS[@]}"

# Backup job

step "$(L "vzdump-Sicherung" "vzdump backup")"
if [[ $JOB_ID == "-" ]]; then
  say "$(L "Kein aktiver vzdump-Job mit Uhrzeit gefunden. Bitte Container $CTID in eine Sicherung aufnehmen." \
           "No active vzdump job with a start time found. Please add container $CTID to a backup.")"
elif [[ $JOB_ALL == 1 ]]; then
  say "$(L "Job $JOB_ID sichert alle Gäste, Container $CTID ist enthalten." "Job $JOB_ID backs up all guests, container $CTID is included.")"
elif [[ $JOB_POOL != "-" ]]; then
  say "$(L "Job $JOB_ID sichert den Pool $JOB_POOL. Aufnehmen mit:" "Job $JOB_ID backs up pool $JOB_POOL. Add the container with:") pvesh set /pools/$JOB_POOL --vms $CTID"
elif [[ $JOB_VMIDS != "-" ]]; then
  if [[ $INTERACTIVE -eq 1 ]] && confirm "$(L "Container $CTID in vzdump-Job $JOB_ID aufnehmen (bisher: $JOB_VMIDS)?" \
                                                "Add container $CTID to vzdump job $JOB_ID (currently: $JOB_VMIDS)?")"; then
    pvesh set "/cluster/backup/$JOB_ID" --vmid "$JOB_VMIDS,$CTID"
    say "$(L "Aufgenommen." "Added.")"
  else
    say "$(L "Nicht geändert. Später:" "Unchanged. Later:") pvesh set /cluster/backup/$JOB_ID --vmid $JOB_VMIDS,$CTID"
  fi
fi

ADDRESS=$(pct exec "$CTID" -- hostname -I | awk '{print $1}')
cat <<EOF

$(L "Fertig. Container $CTID ($CT_HOSTNAME) läuft." "Done. Container $CTID ($CT_HOSTNAME) is running.")
  API:     http://$ADDRESS:8080
  Health:  http://$ADDRESS:8080/api/health
$(L "Das oben angezeigte Token trägst du in der Home-Assistant-Integration ein." "Enter the token shown above in the Home Assistant integration.")
EOF
