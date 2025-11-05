#!/usr/bin/env bash
# configure-bmc.sh — in-band IPMI config via KCS
# Usage: sudo ./configure-bmc.sh <server-number 1..99> [NETMASK]
# Example: sudo ./configure-bmc.sh 2           # -> IP 10.101.1.102, mask 255.255.255.0
#          sudo ./configure-bmc.sh 7 255.255.0.0

set -euo pipefail

if [[ $EUID -ne 0 ]]; then
  echo "Please run as root (sudo)." >&2
  exit 1
fi

if [[ $# -lt 1 || $# -gt 2 ]]; then
  echo "Usage: $0 <server-number 1..99> [NETMASK]" >&2
  exit 1
fi

srv="$1"
if ! [[ "$srv" =~ ^[0-9]{1,2}$ ]] || (( srv < 1 || srv > 99 )); then
  echo "Server number must be 1..99." >&2
  exit 1
fi

NETMASK="${2:-255.255.255.0}"
GATEWAY="10.101.1.1"
# IP format: 10.101.1.1XX where XX is server number (zero-padded)
printf -v XX "%02d" "$srv"
IP="10.101.1.1${XX}"

echo "Target BMC IPv4: $IP  Netmask: $NETMASK  Gateway: $GATEWAY"

# Ensure ipmitool + drivers
command -v ipmitool >/dev/null || { apt-get update -y && apt-get install -y ipmitool; }
modprobe ipmi_msghandler ipmi_devintf ipmi_si 2>/dev/null || true

# Detect an 802.3 LAN channel
chan=""
for c in {1..14}; do
  if ipmitool -I open channel info "$c" 2>/dev/null | grep -q '802\.3 LAN'; then
    chan="$c"; break
  fi
done
if [[ -z "$chan" ]]; then
  echo "No 802.3 LAN channel found via IPMI KCS." >&2
  exit 1
fi
echo "Using LAN channel: $chan"

# Find the user id for 'admin' (case-insensitive). Fallback to 2 if not found.
admin_uid="$(ipmitool -I open user list "$chan" 2>/dev/null \
  | awk 'BEGIN{IGNORECASE=1} $0 ~ /admin/ {print $1; exit}')"
if [[ -z "${admin_uid:-}" ]]; then
  echo "Could not identify 'admin' user from list; falling back to user ID 2."
  admin_uid=2
fi
echo "Admin user ID: $admin_uid"

# Make sure channel/user are enabled and privileged
ipmitool -I open lan set "$chan" access on
ipmitool -I open user enable "$admin_uid" || true
# 4 = ADMINISTRATOR privilege
ipmitool -I open channel setaccess "$chan" "$admin_uid" callin=on ipmi=on link=on privilege=4 || true
ipmitool -I open user priv "$admin_uid" 4 "$chan" || true

# Set admin password
ipmitool -I open user set password "$admin_uid" danucore

# Configure static IPv4
ipmitool -I open lan set "$chan" ipsrc static
ipmitool -I open lan set "$chan" ipaddr   "$IP"
ipmitool -I open lan set "$chan" netmask  "$NETMASK"
ipmitool -I open lan set "$chan" defgw ipaddr "$GATEWAY"

# (Optional) sane ARP defaults
ipmitool -I open lan set "$chan" arp respond off || true
ipmitool -I open lan set "$chan" arp generate on || true

echo
echo "BMC LAN config after changes:"
ipmitool -I open lan print "$chan" | egrep 'IP Address Source|IP Address|Subnet|Default Gateway|802\.1q VLAN|VLAN ID' || true

echo
echo "Admin user status:"
ipmitool -I open user list "$chan" | awk -v uid="$admin_uid" '$1==uid'

echo
echo "Note: Some BMCs apply LAN changes after a BMC reboot. If unreachable on the new IP, you can do:"
echo "      ipmitool -I open bmc reset cold   # (host stays up)"
ipmitool -I open bmc reset cold
