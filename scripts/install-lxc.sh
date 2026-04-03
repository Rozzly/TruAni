#!/usr/bin/env bash
# TruAni — Proxmox LXC Installer (Whiptail TUI)
# Usage: bash -c "$(curl -fsSL https://raw.githubusercontent.com/Rozzly/TruAni/main/scripts/install-lxc.sh)"
#
# Creates a Debian 13 LXC container on Proxmox VE and installs TruAni inside it.

set -euo pipefail

GITHUB_REPO="Rozzly/TruAni"
BACKTITLE="TruAni LXC Setup"

# --- Colors for post-TUI output ---
GN="\033[1;92m"
YW="\033[33m"
RD="\033[01;31m"
BL="\033[36m"
CL="\033[m"
BOLD="\033[1m"

msg_info() { echo -e "  \033[36m*\033[m  $1..."; }
msg_ok()   { echo -e "  \033[1;92m✓\033[m  $1"; }
msg_err()  { echo -e "  \033[01;31m✗\033[m  $1"; }

# --- Cleanup on failure ---
CT_CREATED=""
cleanup() {
    if [[ -n "$CT_CREATED" ]]; then
        pct stop "$CT_CREATED" &>/dev/null || true
        pct destroy "$CT_CREATED" &>/dev/null || true
    fi
}
trap cleanup ERR

# --- Preflight ---
if ! command -v pct &>/dev/null; then
    echo "Error: This script must be run on a Proxmox VE host." >&2
    exit 1
fi

if [[ $(id -u) -ne 0 ]]; then
    echo "Error: This script must be run as root." >&2
    exit 1
fi

if ! command -v whiptail &>/dev/null; then
    echo "Error: whiptail is required but not installed." >&2
    exit 1
fi

# --- Helpers ---
validate_ip_cidr() {
    echo "$1" | grep -qE '^([0-9]{1,3}\.){3}[0-9]{1,3}/[0-9]{1,2}$' || return 1
    local ip cidr
    ip="${1%/*}"
    cidr="${1#*/}"
    [[ "$cidr" -ge 1 && "$cidr" -le 32 ]] || return 1
    IFS='.' read -r a b c d <<< "$ip"
    [[ "$a" -le 255 && "$b" -le 255 && "$c" -le 255 && "$d" -le 255 ]] || return 1
}

validate_ip() {
    echo "$1" | grep -qE '^([0-9]{1,3}\.){3}[0-9]{1,3}$' || return 1
    IFS='.' read -r a b c d <<< "$1"
    [[ "$a" -le 255 && "$b" -le 255 && "$c" -le 255 && "$d" -le 255 ]] || return 1
}

get_bridges() {
    # List kernel bridges, excluding transient/system bridges
    # fwbr/fwpr/fwln = Proxmox firewall, docker0 = Docker, virbr = libvirt
    for dir in /sys/class/net/*/bridge; do
        [[ -d "$dir" ]] || continue
        name="${dir%/bridge}"
        name="${name##*/}"
        [[ -z "$name" ]] && continue
        [[ "$name" =~ ^(fwbr|fwpr|fwln|docker|virbr) ]] && continue
        echo "$name"
    done | sort -u
}

get_storages() {
    pvesm status -content rootdir 2>/dev/null | awk 'NR>1 && NF>=6 {
        name=$1; type=$2; avail=$6
        if (avail+0 > 0) printf "%s|%s|%.1f\n", name, type, avail/1048576
    }' | grep '|'
}

# ============================================================
# TUI PROMPTS
# ============================================================

# Enter alternate screen buffer — prevents shell flash between dialogs
tput smcup 2>/dev/null || true

tui_exit() {
    tput rmcup 2>/dev/null || true
    cleanup
}
trap tui_exit EXIT

# --- 1. Welcome ---
whiptail --title "TruAni" --backtitle "$BACKTITLE" --msgbox "\
    TruAni - Seasonal Anime Manager

 This will create a Debian LXC container
 and install TruAni with all dependencies.

    github.com/${GITHUB_REPO}" 13 48

# --- 2. Container ID ---
NEXT_ID=$(pvesh get /cluster/nextid 2>/dev/null || echo 100)
while true; do
    CT_ID=$(whiptail --title "Container ID" --backtitle "$BACKTITLE" \
        --inputbox "Set the container ID:" 10 50 "$NEXT_ID" 3>&1 1>&2 2>&3) || exit 1
    if ! [[ "$CT_ID" =~ ^[0-9]+$ ]]; then
        whiptail --title "Error" --backtitle "$BACKTITLE" --msgbox "Container ID must be a number." 8 40
        continue
    fi
    if pct status "$CT_ID" &>/dev/null; then
        whiptail --title "Error" --backtitle "$BACKTITLE" --msgbox "Container ID $CT_ID is already in use." 8 45
        NEXT_ID=$((CT_ID + 1))
        continue
    fi
    break
done

# --- 3. Hostname ---
while true; do
    CT_HOSTNAME=$(whiptail --title "Hostname" --backtitle "$BACKTITLE" \
        --inputbox "Set the hostname:" 10 50 "truani" 3>&1 1>&2 2>&3) || exit 1
    CT_HOSTNAME=$(echo "$CT_HOSTNAME" | tr '[:upper:]' '[:lower:]')
    if [[ "$CT_HOSTNAME" =~ ^[a-z0-9]([a-z0-9-]*[a-z0-9])?$ ]]; then
        break
    fi
    whiptail --title "Error" --backtitle "$BACKTITLE" --msgbox \
        "Invalid hostname. Use lowercase letters, numbers, and hyphens." 8 55
done

# --- 4. Disk Size ---
while true; do
    CT_DISK=$(whiptail --title "Disk Size" --backtitle "$BACKTITLE" \
        --inputbox "Set disk size in GB:" 10 50 "2" 3>&1 1>&2 2>&3) || exit 1
    if [[ "$CT_DISK" =~ ^[1-9][0-9]*$ ]]; then break; fi
    whiptail --title "Error" --backtitle "$BACKTITLE" --msgbox "Disk size must be a positive number." 8 45
done

# --- 5. CPU Cores ---
while true; do
    CT_CPU=$(whiptail --title "CPU Cores" --backtitle "$BACKTITLE" \
        --inputbox "Allocate CPU cores:" 10 50 "1" 3>&1 1>&2 2>&3) || exit 1
    if [[ "$CT_CPU" =~ ^[1-9][0-9]*$ ]]; then break; fi
    whiptail --title "Error" --backtitle "$BACKTITLE" --msgbox "CPU cores must be a positive number." 8 45
done

# --- 6. RAM ---
while true; do
    CT_RAM=$(whiptail --title "RAM" --backtitle "$BACKTITLE" \
        --inputbox "Allocate RAM in MiB:" 10 50 "512" 3>&1 1>&2 2>&3) || exit 1
    if [[ "$CT_RAM" =~ ^[1-9][0-9]*$ ]]; then break; fi
    whiptail --title "Error" --backtitle "$BACKTITLE" --msgbox "RAM must be a positive number." 8 45
done

# --- 7. Storage ---
STORAGE_RAW=$(get_storages || true)
if [[ -z "$STORAGE_RAW" ]]; then
    STORAGE_COUNT=0
else
    STORAGE_COUNT=$(echo "$STORAGE_RAW" | wc -l)
fi

if [[ "$STORAGE_COUNT" -eq 0 ]]; then
    whiptail --title "Error" --backtitle "$BACKTITLE" --msgbox \
        "No storage pools found with rootdir content type.\nConfigure storage in Proxmox first." 10 50
    exit 1
elif [[ "$STORAGE_COUNT" -eq 1 ]]; then
    CT_STORAGE=$(echo "$STORAGE_RAW" | head -1 | cut -d'|' -f1)
    STORAGE_DESC=$(echo "$STORAGE_RAW" | head -1 | awk -F'|' '{printf "%s - %s GB free", $2, $3}')
    whiptail --title "Storage" --backtitle "$BACKTITLE" --msgbox \
        "Auto-selected: $CT_STORAGE ($STORAGE_DESC)" 8 55
else
    STORAGE_OPTS=()
    FIRST=true
    while IFS='|' read -r name type free; do
        _st="OFF"
        if $FIRST; then _st="ON"; FIRST=false; fi
        STORAGE_OPTS+=("$name" "$type - ${free} GB free" "$_st")
    done <<< "$STORAGE_RAW"

    CT_STORAGE=$(whiptail --title "Storage" --backtitle "$BACKTITLE" \
        --radiolist "Select storage pool:" 16 60 "$STORAGE_COUNT" \
        "${STORAGE_OPTS[@]}" 3>&1 1>&2 2>&3) || exit 1
fi

# --- 8. Network Bridge ---
BRIDGES=$(get_bridges || true)
if [[ -z "$BRIDGES" ]]; then
    BRIDGE_COUNT=0
else
    BRIDGE_COUNT=$(echo "$BRIDGES" | wc -l)
fi

if [[ "$BRIDGE_COUNT" -eq 0 ]]; then
    whiptail --title "Error" --backtitle "$BACKTITLE" --msgbox \
        "No network bridges found.\nConfigure a bridge in Proxmox first." 10 50
    exit 1
elif [[ "$BRIDGE_COUNT" -eq 1 ]]; then
    CT_BRIDGE=$(echo "$BRIDGES" | head -1)
    whiptail --title "Network Bridge" --backtitle "$BACKTITLE" --msgbox \
        "Auto-selected bridge: $CT_BRIDGE" 8 45
else
    BRIDGE_OPTS=()
    FIRST=true
    while IFS= read -r br; do
        _st="OFF"
        if $FIRST; then _st="ON"; FIRST=false; fi
        BRIDGE_OPTS+=("$br" "" "$_st")
    done <<< "$BRIDGES"

    CT_BRIDGE=$(whiptail --title "Network Bridge" --backtitle "$BACKTITLE" \
        --radiolist "Select network bridge:" 14 50 "$BRIDGE_COUNT" \
        "${BRIDGE_OPTS[@]}" 3>&1 1>&2 2>&3) || exit 1
fi

# --- 9. IP Configuration ---
IP_MODE=$(whiptail --title "IP Address" --backtitle "$BACKTITLE" \
    --radiolist "Select IP configuration:" 12 50 2 \
    "dhcp" "Automatic (DHCP)" "ON" \
    "static" "Static IP address" "OFF" \
    3>&1 1>&2 2>&3) || exit 1

CT_IP="dhcp"
CT_GW=""

if [[ "$IP_MODE" == "static" ]]; then
    while true; do
        CT_IP=$(whiptail --title "Static IP" --backtitle "$BACKTITLE" \
            --inputbox "Enter IPv4 address with CIDR:\n(e.g. 192.168.1.100/24)" 12 50 3>&1 1>&2 2>&3) || exit 1
        if validate_ip_cidr "$CT_IP"; then break; fi
        whiptail --title "Error" --backtitle "$BACKTITLE" --msgbox \
            "Invalid IP/CIDR format. Example: 192.168.1.100/24" 8 50
    done

    while true; do
        CT_GW=$(whiptail --title "Gateway" --backtitle "$BACKTITLE" \
            --inputbox "Enter gateway IP address:\n(e.g. 192.168.1.1)" 12 50 3>&1 1>&2 2>&3) || exit 1
        if validate_ip "$CT_GW"; then break; fi
        whiptail --title "Error" --backtitle "$BACKTITLE" --msgbox \
            "Invalid gateway IP format." 8 40
    done
fi

# --- 10. VLAN Tag ---
CT_VLAN=""
VLAN_INPUT=$(whiptail --title "VLAN Tag" --backtitle "$BACKTITLE" \
    --inputbox "Enter VLAN tag (1-4094) or leave blank for none:" 10 55 "" 3>&1 1>&2 2>&3) || exit 1

if [[ -n "$VLAN_INPUT" ]]; then
    if [[ "$VLAN_INPUT" =~ ^[0-9]+$ ]] && [[ "$VLAN_INPUT" -ge 1 ]] && [[ "$VLAN_INPUT" -le 4094 ]]; then
        CT_VLAN="$VLAN_INPUT"
    else
        whiptail --title "Error" --backtitle "$BACKTITLE" --msgbox "Invalid VLAN tag. Must be 1-4094." 8 45
    fi
fi

# --- 11. DNS Server ---
CT_DNS=""
CT_DNS=$(whiptail --title "DNS Server" --backtitle "$BACKTITLE" \
    --inputbox "Enter DNS server IP or leave blank for host default:" 10 55 "" 3>&1 1>&2 2>&3) || exit 1

if [[ -n "$CT_DNS" ]] && ! validate_ip "$CT_DNS"; then
    whiptail --title "Error" --backtitle "$BACKTITLE" --msgbox "Invalid DNS IP. Using host default." 8 45
    CT_DNS=""
fi

# --- 12. Confirmation ---
IP_DISPLAY="$CT_IP"
[[ "$IP_MODE" == "static" && -n "$CT_GW" ]] && IP_DISPLAY="$CT_IP (gw: $CT_GW)"

SUMMARY="Container ID:  $CT_ID
Hostname:      $CT_HOSTNAME
Disk:          $CT_DISK GB
CPU:           $CT_CPU core(s)
RAM:           $CT_RAM MiB
Storage:       $CT_STORAGE
Bridge:        $CT_BRIDGE
IP:            $IP_DISPLAY
VLAN:          ${CT_VLAN:-(none)}
DNS:           ${CT_DNS:-(host default)}"

whiptail --title "Confirm" --backtitle "$BACKTITLE" --yesno \
"$SUMMARY

Create this container and install TruAni?" 20 55 || exit 0

# ============================================================
# CREATION & INSTALLATION
# ============================================================

# Leave alternate screen buffer — switch to normal terminal output
tput rmcup 2>/dev/null || true
trap 'cleanup' ERR
clear
echo ""
echo -e "  ${BOLD}TruAni LXC Setup${CL}"
echo ""

# --- Template ---
msg_info "Checking for Debian 13 template"

TEMPLATE_FILE=$(pveam list local 2>/dev/null | grep -oP 'debian-13-standard[^\s]+' | sort -V | tail -1 || true)

if [[ -z "$TEMPLATE_FILE" ]]; then
    msg_info "Downloading Debian 13 template"
    AVAILABLE=$(pveam available --section system 2>/dev/null | grep -oP 'debian-13-standard[^\s]+' | sort -V | tail -1 || true)
    if [[ -z "$AVAILABLE" ]]; then
        msg_err "No Debian 13 template found. Download a Debian template manually."
        exit 1
    fi
    pveam download local "$AVAILABLE" &>/dev/null
    TEMPLATE_FILE="$AVAILABLE"
    msg_ok "Downloaded $TEMPLATE_FILE"
else
    msg_ok "Template: $TEMPLATE_FILE"
fi

TEMPLATE_PATH="local:vztmpl/$TEMPLATE_FILE"

# --- Build net0 string ---
NET0="name=eth0,bridge=$CT_BRIDGE,ip=$CT_IP"
[[ -n "$CT_GW" ]] && NET0+=",gw=$CT_GW"
[[ -n "$CT_VLAN" ]] && NET0+=",tag=$CT_VLAN"

# --- Create container ---
msg_info "Creating container $CT_ID"

PCT_ARGS=(
    "$CT_ID" "$TEMPLATE_PATH"
    --hostname "$CT_HOSTNAME"
    --memory "$CT_RAM"
    --cores "$CT_CPU"
    --rootfs "$CT_STORAGE:$CT_DISK"
    --net0 "$NET0"
    --unprivileged 1
    --features nesting=1
    --onboot 1
)
[[ -n "$CT_DNS" ]] && PCT_ARGS+=(--nameserver "$CT_DNS")

pct create "${PCT_ARGS[@]}" &>/dev/null
CT_CREATED="$CT_ID"
msg_ok "Container $CT_ID created"

# --- Start ---
msg_info "Starting container"
pct start "$CT_ID"
msg_ok "Container started"

# --- Wait for network ---
msg_info "Waiting for network"
for i in $(seq 1 30); do
    if pct exec "$CT_ID" -- ping -c 1 -W 1 8.8.8.8 &>/dev/null; then
        break
    fi
    sleep 1
done

if ! pct exec "$CT_ID" -- ping -c 1 -W 2 8.8.8.8 &>/dev/null; then
    msg_err "Container has no network. Check bridge ($CT_BRIDGE) and DHCP settings."
    exit 1
fi
msg_ok "Network ready"

# --- Install TruAni (silent with spinner) ---
msg_info "Preparing container"
pct exec "$CT_ID" -- bash -c "
    export DEBIAN_FRONTEND=noninteractive
    export LC_ALL=C
    apt-get update -qq && apt-get install -y -qq curl locales >/dev/null 2>&1
    sed -i 's/# en_US.UTF-8/en_US.UTF-8/' /etc/locale.gen
    locale-gen >/dev/null 2>&1
" &>/dev/null
msg_ok "Container ready"

INSTALL_URL="https://raw.githubusercontent.com/${GITHUB_REPO}/main/scripts/install.sh"

# Run installer silently with a spinner
INSTALL_LOG=$(mktemp)
pct exec "$CT_ID" -- bash -c "export DEBIAN_FRONTEND=noninteractive LC_ALL=C; curl -fsSL '${INSTALL_URL}' | bash" >"$INSTALL_LOG" 2>&1 &
INSTALL_PID=$!

# Spinner
SPINNER='⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏'
i=0
printf "  \033[36m*\033[m  Installing TruAni..."
while kill -0 "$INSTALL_PID" 2>/dev/null; do
    printf "\r  ${SPINNER:i++%${#SPINNER}:1}  Installing TruAni..."
    sleep 0.1
done
wait "$INSTALL_PID"
INSTALL_EXIT=$?
printf "\r"

if [[ "$INSTALL_EXIT" -ne 0 ]]; then
    msg_err "Installation failed"
    echo ""
    echo -e "  ${YW}Install log:${CL}"
    cat "$INSTALL_LOG" | tail -20 | while IFS= read -r line; do
        echo -e "  ${BL}│${CL} $line"
    done
    rm -f "$INSTALL_LOG"
    exit 1
fi
rm -f "$INSTALL_LOG"
msg_ok "TruAni installed"

if pct exec "$CT_ID" -- systemctl is-active truani &>/dev/null; then
    msg_ok "TruAni is running"
else
    msg_err "TruAni service failed to start"
    echo -e "  ${YW}Check logs: pct exec $CT_ID -- journalctl -u truani${CL}"
    exit 1
fi

# Success — clear cleanup trap
CT_CREATED=""

# --- Get IP ---
CT_ACTUAL_IP=$(pct exec "$CT_ID" -- hostname -I 2>/dev/null | awk '{print $1}')

# --- Done ---
echo ""
echo ""
echo -e "  ${GN}${BOLD}TruAni has been successfully installed!${CL}"
echo ""
echo -e "  Access TruAni at:"
echo -e "     ${GN}http://${CT_ACTUAL_IP}:5656${CL}"
echo ""
echo -e "  Default login: ${YW}truani${CL} / ${YW}truani${CL}"
echo -e "  You will be prompted to change these on first login."
echo ""
echo -e "  Container: ${YW}$CT_ID${CL} ($CT_HOSTNAME)"
echo -e "  Update:    ${YW}pct exec $CT_ID -- update${CL}"
echo ""
