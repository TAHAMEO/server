#!/usr/bin/env bash
# Starts the Forge server with the settings in user_jvm_args.txt.
#
#   ./start.sh            run in this terminal; restarts by itself after a crash
#   ./start.sh --service  used by the systemd service, which does the restarting
#
# Java: $JAVA if set, else server/.java (scripts/get-java.sh), else an installed
# Java 21 or 17, else whatever `java` is on the PATH.
set -euo pipefail

cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")"
# New files (logs with player IPs, the login database, the world) stay private to you
# and the minecraft group.
umask 0007

FORGE_ARGS="libraries/net/minecraftforge/forge/1.20.1-47.4.27/unix_args.txt"
MIN_FREE_MB=1024   # below this, refuse to start: a full disk can corrupt the world
WARN_FREE_MB=4096

service=false
if [[ "${1:-}" == "--service" ]]; then
    service=true
    shift
fi

die() {
    echo "start.sh: $*" >&2
    exit 1
}

warn() {
    echo "start.sh: WARNING: $*" >&2
}

find_java() {
    local candidate dir
    local candidates=("${JAVA:-}" ".java/bin/java")
    for dir in /usr/lib/jvm/*-21-* /usr/lib/jvm/*-17-*; do
        candidates+=("$dir/bin/java")
    done
    candidates+=("$(command -v java || true)")
    for candidate in "${candidates[@]}"; do
        if [[ -n "$candidate" && -x "$candidate" ]]; then
            echo "$candidate"
            return 0
        fi
    done
    return 1
}

java="$(find_java)" || die "Java not found. Run ../scripts/get-java.sh or install Java 21 (sudo apt install openjdk-21-jre-headless)."
java_version="$("$java" -version 2>&1 | awk -F'"' '/version/ {print $2; exit}')"
java_major="${java_version%%.*}"
echo "start.sh: using Java $java_version ($java)"
if (( java_major < 17 )); then
    die "Minecraft 1.20.1 needs Java 17 or newer."
elif (( java_major > 21 )); then
    warn "Java $java_major is newer than most 1.20.1 mods are tested with. If the server crashes, run ../scripts/get-java.sh to get Java 21."
fi

[[ -f "$FORGE_ARGS" ]] || die "Forge is not installed here ($FORGE_ARGS is missing)."
grep -qi '^eula=true' eula.txt 2>/dev/null || die "Accept the Minecraft EULA first: put eula=true in eula.txt."
if ! compgen -G "mods/*.jar" >/dev/null; then
    warn "mods/ is empty. Run: python3 ../scripts/install-mods.py"
fi

free_mb="$(df -Pm . | awk 'NR == 2 {print $4}')"
if (( free_mb < MIN_FREE_MB )); then
    die "Only ${free_mb} MB of disk space left. Free some space first; a full disk can corrupt the world."
elif (( free_mb < WARN_FREE_MB )); then
    warn "Only ${free_mb} MB of disk space left."
fi

heap="$(sed -n 's/^-Xmx\([0-9]*\)\([GgMm]\)[[:space:]]*$/\1\2/p' user_jvm_args.txt | tail -n 1)"
if [[ -n "$heap" && -r /proc/meminfo ]]; then
    heap_mb="${heap%?}"
    [[ "${heap: -1}" == [Gg] ]] && heap_mb=$((heap_mb * 1024))
    available_mb=$(( $(awk '/^MemAvailable:/ {print $2}' /proc/meminfo) / 1024 ))
    if (( heap_mb + 1024 > available_mb )); then
        warn "The server may use ${heap} plus about 1.5 GB, but only ${available_mb} MB of RAM is free. Close other programs or lower -Xmx in user_jvm_args.txt."
    fi
fi

# One server per folder: the lock is held for as long as this script (or Java) runs.
exec 9>.start.lock
flock -n 9 || die "The server in this folder is already running."

java_cmd=("$java" @user_jvm_args.txt "@$FORGE_ARGS" nogui "$@")
if $service; then
    exec "${java_cmd[@]}"
fi

quick_crashes=0
while true; do
    started=$(date +%s)
    set +e
    "${java_cmd[@]}"
    code=$?
    set -e
    case $code in
        0 | 130 | 143)
            echo "start.sh: server stopped."
            exit 0
            ;;
    esac
    if (( $(date +%s) - started < 300 )); then
        quick_crashes=$((quick_crashes + 1))
    else
        quick_crashes=1
    fi
    if (( quick_crashes >= 3 )); then
        die "The server crashed 3 times in a row within 5 minutes; not restarting. Check logs/latest.log and crash-reports/."
    fi
    echo "start.sh: the server exited with code $code; restarting in 10 seconds (Ctrl+C to cancel)..." >&2
    sleep 10
done
