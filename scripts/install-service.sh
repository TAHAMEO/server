#!/usr/bin/env bash
# Runs the server as a locked-down background service. Recommended as soon as
# people join over the internet:
#   - its own 'minecraft' system user, so the server cannot touch your files
#   - a systemd sandbox: read-only system, no access to your home folder, no new privileges,
#     and it may only listen on the Minecraft port
#   - restarts after a crash, saves the world on stop, backs up daily at 05:00
#
#   sudo scripts/install-service.sh              install and start it now
#   sudo scripts/install-service.sh --autostart  also start it whenever the laptop boots
#   sudo scripts/install-service.sh --uninstall  remove the service (the world is kept)
set -euo pipefail

repo="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/.." && pwd)"
server="$repo/server"
units=(minecraft.socket minecraft.service minecraft-backup.service minecraft-backup.timer)
unit_dir=/etc/systemd/system

die() {
    echo "install-service: $*" >&2
    exit 1
}

[[ $EUID -eq 0 ]] || die "run it with sudo: sudo $0 $*"
command -v systemctl >/dev/null || die "systemd is required."

autostart=false
case "${1:-}" in
    "") ;;
    --autostart) autostart=true ;;
    --uninstall)
        systemctl disable --now minecraft.service minecraft-backup.timer minecraft.socket 2>/dev/null || true
        for unit in "${units[@]}"; do
            rm -f "$unit_dir/$unit"
        done
        systemctl daemon-reload
        echo "Service removed. Your server folder and the 'minecraft' user were kept (sudo userdel minecraft removes the user)."
        exit 0
        ;;
    *) die "unknown option $1 (use --autostart or --uninstall)" ;;
esac

[[ "$repo" != *[[:space:]]* ]] || die "the folder path contains spaces ($repo); move it somewhere without spaces."
[[ -x "$server/start.sh" ]] || die "$server/start.sh not found."
if [[ -e "$server/.start.lock" ]] && ! flock -n "$server/.start.lock" true; then
    die "the server is running in a terminal; stop it first (type 'stop' in its console)."
fi

# 1. A system user that owns nothing else on this machine and cannot log in.
if ! id minecraft >/dev/null 2>&1; then
    useradd --system --user-group --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin minecraft
    echo "Created the system user 'minecraft'."
fi
admin="${SUDO_USER:-}"
if [[ -n "$admin" && "$admin" != root ]] && ! id -nG "$admin" | grep -qw minecraft; then
    usermod -aG minecraft "$admin"
    echo "Added $admin to the 'minecraft' group (log out and back in for it to take effect)."
fi

# 2. The server folder is shared by you and the minecraft group, and closed to everyone else.
chgrp -R minecraft "$server"
chmod -R g+rwX,o-rwx "$server"
find "$server" -type d -exec chmod g+s {} +
mkdir -p "$server/backups"
chown minecraft:minecraft "$server/backups"
chmod 2770 "$server/backups"

# 3. Install the units with this folder's paths and port filled in.
port="$(sed -n 's/^server-port=//p' "$server/server.properties")"
port="${port:-25565}"
[[ "$port" =~ ^[0-9]+$ ]] || die "server-port in server.properties is not a number."
for unit in "${units[@]}"; do
    sed -e "s|@SERVER_DIR@|$server|g" -e "s|@REPO_DIR@|$repo|g" -e "s|@PORT@|$port|g" -e "s|@PROTECT_HOME@|tmpfs|g" \
        "$repo/scripts/systemd/$unit" >"$unit_dir/$unit"
    chmod 644 "$unit_dir/$unit"
done
systemctl daemon-reload
if command -v systemd-analyze >/dev/null; then
    (cd "$unit_dir" && systemd-analyze verify "${units[@]}") || echo "install-service: systemd-analyze reported the warnings above." >&2
fi

# 4. Start it.
systemctl enable --now minecraft-backup.timer >/dev/null
if $autostart; then
    systemctl enable minecraft.service >/dev/null 2>&1
    echo "The server will also start when the laptop boots (undo: sudo systemctl disable minecraft)."
fi
systemctl start minecraft.service
sleep 5
if systemctl is-active --quiet minecraft.service; then
    echo
    echo "The server is starting as a service (the first start takes a few minutes)."
    echo "  follow the log:      scripts/mc logs"
    echo "  console command:     scripts/mc cmd say hello"
    echo "  stop / start:        scripts/mc stop | scripts/mc start"
    echo "  how sandboxed it is: systemd-analyze security minecraft.service"
else
    journalctl -u minecraft.service -n 30 --no-pager || true
    die "the service did not start; see the messages above."
fi
