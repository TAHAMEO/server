# Server Auth

A small server-side Forge 1.20.1 mod that adds `/register`, `/login` and `/changepassword`
for offline-mode servers. Players do not install it. It switches itself off when
`online-mode=true`, because Mojang then checks accounts already.

**How it works**

- Passwords are stored as salted PBKDF2-HMAC-SHA512 hashes (210,000 iterations by default)
  in `server/serverauth/accounts.json`. The folder has mode 700 and the file 600, and the
  file is written atomically. Hashing runs on a worker thread, never on the game thread.
  Older hashes are upgraded at login when the iteration count is raised.
- Until a player logs in they cannot:
  - move (they are held in place)
  - chat, or run commands other than `/login`, `/l` and `/register`
  - break or place blocks, interact, attack, mount, pick up or drop items
  - take damage, or be targeted by mobs
- While they wait, their inventory and logout location are stored in their player data.
  They are moved to world spawn and made blind. Everything comes back when they log in,
  even after a server crash.
- A wrong password costs one of 3 tries per connection. Repeated failures from one IP
  lock that address out for a while (localhost is exempt by default). Players who do not
  log in in time are kicked. Easy passwords are refused. Registered names are case-exact.
- If the accounts file cannot be read, nobody can join (fail closed).
- `/auth unregister <name>` (op level 3) resets a password.

Settings: `server/config/serverauth.toml`.

**Building and testing**

- `./build.sh` compiles the mod with a JDK 17+, using only the jars in `server/libraries`
  (no Gradle, no internet). It writes `modpack/local/serverauth-<version>.jar`.
- `test/run.sh` builds the mod and starts a throwaway server with it. Bots
  ([mineflayer](https://github.com/PrismarineJS/mineflayer), Node.js 18+) then try
  everything above, including stopping the server while a player is still logging in.
