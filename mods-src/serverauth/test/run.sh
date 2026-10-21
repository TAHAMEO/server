#!/usr/bin/env bash
# End-to-end tests for Server Auth. Builds the mod, starts a throwaway Forge server
# (it reuses server/libraries, nothing in server/ is changed) and lets bots try
# everything an impostor would: chatting, commands, walking off, wrong passwords,
# getting hurt, reading the password file, crashing the server mid-login...
#
#   mods-src/serverauth/test/run.sh        needs a JDK 17+ and Node.js 18+
set -euo pipefail

here="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"
repo="$(cd "$here/../../.." && pwd)"
port="${PORT:-25599}"
work="$(mktemp -d)"
server_pid=""
keep_work=false

# shellcheck disable=SC2329  # called by the EXIT trap
cleanup() {
    if [[ -n "$server_pid" ]] && kill -0 "$server_pid" 2>/dev/null; then
        echo stop >>"$work/console.fifo"
        wait "$server_pid" || true
    fi
    $keep_work || rm -rf "$work"
}
trap cleanup EXIT

start_server() {
    # A fresh log per start, so "ready" below is this start's line (the tests read server.out).
    [[ -f "$work/server.out" ]] && mv "$work/server.out" "$work/server-$(date +%s%N).out"
    (cd "$work" && exec ./start.sh <>console.fifo >server.out 2>&1) &
    server_pid=$!
    for _ in $(seq 1 300); do
        if grep -q "Server Auth is on" "$work/server.out" 2>/dev/null; then
            return 0
        fi
        kill -0 "$server_pid" 2>/dev/null || break
        sleep 1
    done
    tail -n 30 "$work/server.out" >&2
    echo "run.sh: the test server did not start" >&2
    exit 1
}

"$here/../build.sh"
[[ -d "$here/node_modules" ]] || (cd "$here" && npm install --no-audit --no-fund --silent)

mkdir -p "$work/mods" "$work/config"
ln -s "$repo/server/libraries" "$work/libraries"
cp "$repo/server/start.sh" "$repo/server/user_jvm_args.txt" "$repo/server/eula.txt" "$repo/server/server.properties" "$work/"
cp "$repo/server/config/fml.toml" "$work/config/"
cp -r "$repo/server/defaultconfigs" "$work/"
cp "$repo"/modpack/local/serverauth-*.jar "$work/mods/"
sed -i -e "s/^server-port=.*/server-port=$port/" -e 's/^level-name=.*/level-name=testworld/' \
    -e 's/^white-list=.*/white-list=false/' -e 's/^enforce-whitelist=.*/enforce-whitelist=false/' \
    -e 's/^view-distance=.*/view-distance=4/' -e 's/^simulation-distance=.*/simulation-distance=4/' "$work/server.properties"
# Short timeouts so the test does not take all day; localhost is not exempt so the lockout can be tested.
cat >"$work/config/serverauth.toml" <<'EOF'
[login]
	timeoutSeconds = 20
	maxAttempts = 3
	minPasswordLength = 6
[protection]
	hideInventory = true
	hideLocation = true
[ipLockout]
	maxFailures = 4
	windowMinutes = 10
	lockMinutes = 1
	exemptLocalhost = false
[hashing]
	iterations = 210000
EOF
mkfifo "$work/console.fifo"
export SERVER_DIR="$work" PORT="$port"

status=0
start_server
node "$here/test-restart-a.js" || status=1   # ends by stopping the server mid-login
wait "$server_pid" || true
start_server
node "$here/test-restart-b.js" || status=1
node "$here/test-respawn.js" || status=1
node "$here/test-auth.js" || status=1
if (( status == 0 )); then
    echo "Server Auth: all end-to-end tests passed"
else
    keep_work=true
    echo "Server Auth: some tests FAILED; the test server is kept in $work (see server.out)" >&2
fi
exit "$status"
