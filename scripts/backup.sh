#!/usr/bin/env bash
# Backs up the world, player data, login database and configs into server/backups/
# and keeps only the newest few (your disk is small).
#
#   scripts/backup.sh          safe while the server runs as a service (it pauses saving)
#   scripts/backup.sh --hot    copy while the server runs in a terminal (small risk of a damaged chunk)
#
# Settings: KEEP=3 (how many backups to keep), MIN_FREE_MB=2048 (space to leave free)
set -euo pipefail

repo="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/.." && pwd)"
server="$repo/server"
dest="${BACKUP_DIR:-$server/backups}"
keep="${KEEP:-3}"
min_free_mb="${MIN_FREE_MB:-2048}"
console="${MC_CONSOLE:-/run/minecraft.console}"
hot=false
[[ "${1:-}" == "--hot" ]] && hot=true

die() {
    echo "backup: $*" >&2
    exit 1
}

cd "$server"
level="$(sed -n 's/^level-name=//p' server.properties)"
level="${level:-world}"
[[ -d "$level" ]] || die "no world folder '$level' yet; start the server once first."

running=false
if [[ -e .start.lock ]] && ! flock -n .start.lock true; then
    running=true
fi

world_mb=$(du -sm "$level" | cut -f1)
mkdir -p "$dest"
chmod 700 "$dest"
free_mb=$(df -Pm "$dest" | awk 'NR == 2 {print $4}')
if (( free_mb - world_mb < min_free_mb )); then
    die "not enough disk space: the world is ${world_mb} MB and only ${free_mb} MB are free (keeping ${min_free_mb} MB free). Delete old backups or lower KEEP."
fi

# While the server runs, stop it from writing chunks during the copy.
paused=false
if $running; then
    if [[ -p "$console" && -w "$console" ]]; then
        saves_before=$(grep -c "Saved the game" logs/latest.log || true)
        echo "save-off" >"$console"
        paused=true
        trap 'echo "save-on" > "$console"' EXIT
        echo "save-all flush" >"$console"
        for _ in $(seq 1 120); do
            (( $(grep -c "Saved the game" logs/latest.log || true) > saves_before )) && break
            sleep 1
        done
    elif ! $hot; then
        die "the server is running in a terminal. Type 'save-off' and 'save-all flush' in its console, run this with --hot, then type 'save-on'. Or stop the server first."
    fi
fi

stamp="$(date +%Y-%m-%d_%H%M%S)"
items=("$level" server.properties user_jvm_args.txt)
for extra in config defaultconfigs serverauth whitelist.json ops.json banned-players.json banned-ips.json; do
    [[ -e "$extra" ]] && items+=("$extra")
done
if command -v zstd >/dev/null; then
    archive="$dest/$stamp.tar.zst"
    tar --exclude="$level/session.lock" -cf - "${items[@]}" | zstd -q -T2 -10 -o "$archive.part"
else
    archive="$dest/$stamp.tar.gz"
    tar --exclude="$level/session.lock" -czf "$archive.part" "${items[@]}"
fi
mv "$archive.part" "$archive"
chmod 600 "$archive"  # it contains the password hashes
if $paused; then
    echo "save-on" >"$console"
    trap - EXIT
fi

# Keep only the newest $keep backups.
mapfile -t old < <(find "$dest" -maxdepth 1 -name '*.tar.*' ! -name '*.part' -printf '%T@ %p\n' | sort -rn | tail -n +$((keep + 1)) | cut -d' ' -f2-)
for f in "${old[@]}"; do
    rm -f -- "$f"
done
echo "backup: saved $archive ($(du -h "$archive" | cut -f1)); keeping the newest $keep"
