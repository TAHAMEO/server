#!/usr/bin/env bash
# Firewall with ufw: block every incoming connection except Minecraft.
#
#   sudo scripts/firewall.sh          allow the Minecraft port from anywhere (internet play)
#   sudo scripts/firewall.sh --lan    allow it only from your home network (192.168.x.x, 10.x.x.x, ...)
#   sudo scripts/firewall.sh --off    remove the Minecraft rules again (ufw stays on)
#
# Outgoing traffic is not limited. If ssh is running, it stays allowed so you do not lock yourself out.
set -euo pipefail

server="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/../server" && pwd)"
lan_ranges=(10.0.0.0/8 172.16.0.0/12 192.168.0.0/16)

die() {
    echo "firewall: $*" >&2
    exit 1
}

[[ $EUID -eq 0 ]] || die "run it with sudo: sudo $0 $*"
command -v ufw >/dev/null || die "ufw is not installed. Install it with: sudo apt install ufw"

port="$(sed -n 's/^server-port=//p' "$server/server.properties")"
port="${port:-25565}"
[[ "$port" =~ ^[0-9]+$ ]] || die "server-port in server.properties is not a number."
mode="${1:-internet}"

remove_rules() {
    # ufw prints numbered rules; delete ours from the bottom up so the numbers stay valid.
    local numbers
    numbers="$(ufw status numbered | sed -n "s/^\[ *\([0-9]*\)\] ${port}\/tcp .*/\1/p" | sort -rn)"
    for n in $numbers; do
        ufw --force delete "$n" >/dev/null
    done
}

case "$mode" in
    --off)
        remove_rules
        echo "Removed the rules for port $port."
        ufw status verbose
        exit 0
        ;;
    internet | --lan) ;;
    *) die "unknown option $mode (use --lan or --off)" ;;
esac

echo "This will:"
echo "  - block all incoming connections (outgoing ones are not affected)"
if [[ "$mode" == --lan ]]; then
    echo "  - allow Minecraft (TCP $port) only from your local network"
else
    echo "  - allow Minecraft (TCP $port) from anywhere"
fi
ssh_open=false
if ss -ltnH 2>/dev/null | awk '{print $4}' | grep -qE '(^|:)22$'; then
    ssh_open=true
    echo "  - keep ssh (port 22) allowed, because ssh is running"
fi
read -r -p "Continue? [y/N] " answer
[[ "$answer" == [yY]* ]] || die "nothing changed."

ufw default deny incoming >/dev/null
ufw default allow outgoing >/dev/null
$ssh_open && ufw allow OpenSSH >/dev/null
remove_rules
if [[ "$mode" == --lan ]]; then
    for range in "${lan_ranges[@]}"; do
        ufw allow from "$range" to any port "$port" proto tcp comment "Minecraft (LAN)" >/dev/null
    done
else
    # Plain 'allow', not 'limit': every server-list refresh is a new connection, and friends
    # behind one router share an address. Password guessing is stopped by the login mod.
    ufw allow "$port/tcp" comment "Minecraft" >/dev/null
fi
ufw --force enable >/dev/null
ufw status verbose
