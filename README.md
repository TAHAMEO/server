# Zombie Apocalypse server (Minecraft 1.20.1, Forge 47.4.27)

A modded survival server with guns, vehicles and zombies, tuned for an HP EliteBook 840 G2
(i5-5200U, 16 GB RAM) running Kali Linux, with a password login system and hardened settings.

> **Players need the mods too.** Guns, vehicles, armor and new blocks only work if the
> game on each player's PC has the same mods; Forge refuses to let players without
> them join. There is no way around that for these mods. To make it painless, the
> installer builds a **player pack** that friends import in one click (see
> [Adding friends](#adding-friends)). Only the login system and the performance
> mods are server-only.

## Contents

- [Quick start](#quick-start)
- [Adding friends](#adding-friends)
- [Playing over the internet](#playing-over-the-internet)
- [Security](#security)
- [Performance on this laptop](#performance-on-this-laptop)
- [Everyday commands](#everyday-commands)
- [The mod list](#the-mod-list)
- [Troubleshooting](#troubleshooting)
- [What is in this repository](#what-is-in-this-repository)

## Quick start

Everything happens in a terminal on the laptop.

1. **Get the files.** A fresh copy avoids clashes with the old test world:

   ```sh
   git clone -b claude/exciting-hawking-ojiv7c https://github.com/TAHAMEO/server.git ~/mcserver
   cd ~/mcserver
   ```

2. **Get Java 21.** Kali ships Java 25, which is newer than most 1.20.1 mods are tested
   with. This downloads Java 21 into `server/.java` (checksum-verified; your system Java is
   not touched):

   ```sh
   scripts/get-java.sh
   ```

3. **Download the mods.** This also builds the players' pack:

   ```sh
   python3 scripts/install-mods.py
   ```

   - Most of these mods are on CurseForge. Downloads are most reliable with a free
     CurseForge API key from <https://console.curseforge.com>:
     `export CF_API_KEY='your-key'` before running the script. Without a key the script
     tries CurseForge's public website, then Modrinth.
   - Lines starting with `PROBLEM:` say exactly what is missing. Usually you download a
     file in the browser from the link shown, save it in `modpack/manual/`, and run the
     script again.
   - It reads every mod's own dependency list, adds the missing libraries, and tells you
     when two mods need versions that do not fit together.

4. **Let yourself in and make yourself admin.** Use your exact in-game name:

   ```sh
   scripts/mc whitelist add YourName
   scripts/mc op YourName
   ```

5. **Start the server** and wait for `Done (...)! For help, type "help"`. The first start
   takes a few minutes:

   ```sh
   cd server && ./start.sh
   ```

   Type `stop` in that terminal to shut down (the world is saved). If the server crashes,
   `start.sh` restarts it, but gives up after 3 quick crashes in a row.

6. **Switch on The Lost Cities** for the normal world. Keerdm's Zombie Apocalypse
   Essentials puts its buildings and loot into Lost Cities' ruined cities, and the cities
   only appear in a world generated after this setting is on.
   1. After the first start and `stop`, Lost Cities has written its world settings to
      `server/world/serverconfig/` (`lostcities-server.toml`).
   2. Copy that file to `server/defaultconfigs/`. Set `selectedProfile` there to `"default"`
      (damaged cities) or `"wasteland"` (dead world). The comments in the file list all profiles.
   3. Move the test world away (`mv server/world server/world-old`) and start again.

   Every new world from then on has the cities.

7. **Join** with your game (same mods installed) at `localhost`. Type
   `/register <password> <password>` the first time and `/login <password>` after that.

8. **Pre-generate the world** before friends start exploring. Generating new land is the
   heaviest thing this CPU does; doing it ahead of time removes most lag. In the server
   console:

   ```
   chunky radius 1500
   chunky start
   ```

   This takes about an hour. The world has a border at ±4000 blocks (`max-world-size` in
   `server.properties`), which keeps the world size in check on a small disk.

## Adding friends

1. **Whitelist them** (the server only lets in listed players):

   ```sh
   scripts/mc whitelist add TheirName
   ```

   Use this instead of the in-game `/whitelist add`. In offline mode that command asks
   Mojang for the name and may store the wrong ID, so the friend gets
   "You are not white-listed".

2. **Send them the player pack** from the `dist/` folder:
   - `zombie-apocalypse-curseforge.zip`: in the **CurseForge app** choose
     *Create Custom Profile → Import*. **Prism Launcher**, **PollyMC** and **ATLauncher**
     can import it too. The launcher installs Forge and every mod by itself.
   - `zombie-apocalypse-client.zip`: the same mods as plain files. Its `README.txt` explains
     how to install Forge and copy the mods by hand.

   The pack also contains client-only FPS mods (Embeddium, Entity Culling,
   ImmediatelyFast). Players must **not** use OptiFine (Fracture Point and LesRaisins
   Armor break with it). Give the game about 6 GB of RAM.

3. **First join:** `/register <password> <password>`. **Every join after that:**
   `/login <password>`. Until then they are frozen at spawn, blind, invulnerable, and their
   inventory and location are hidden.

4. **Forgot their password?** Run `scripts/mc cmd auth unregister TheirName`, or type
   `auth unregister TheirName` in the server console. They then `/register` again.

Whenever you change the mod list, run `python3 scripts/install-mods.py` again and send
friends the new pack. Server and players must have the same mod versions.

## Playing over the internet

On the same Wi-Fi, friends join with the laptop's local IP (`ip -4 addr show wlan0`).
For friends elsewhere, from safest to least safe:

1. **A private VPN such as [Tailscale](https://tailscale.com) (recommended).** You and
   your friends install it and join your tailnet; they connect to the laptop's Tailscale
   IP. The server is never visible on the internet, so scanners and strangers cannot even
   find it.
2. **A tunnel such as [playit.gg](https://playit.gg).** Hides your home IP and needs no
   router changes. All players then arrive from one local address, so the per-IP login
   lockout cannot tell them apart. The whitelist and strong passwords matter more here.
3. **Port forwarding.** Forward TCP 25565 on your router to the laptop and turn on the
   firewall (below). Your home IP becomes public, and bots constantly scan for
   Minecraft servers.

## Security

What is already set up:

| Protection | How |
|---|---|
| **Password login** | `mods-src/serverauth`, a small server-side mod written for this server. Passwords are salted PBKDF2-HMAC-SHA512 hashes (210,000 rounds) in `server/serverauth/accounts.json` (mode 600). Hashing runs off the game thread, so login attempts cannot lag the server. Passwords never appear in logs. |
| **Nothing works before login** | No moving, chatting, commands (except `/login` and `/register`), breaking, placing, using items, opening containers, riding vehicles, picking up or dropping items. Players cannot be hurt or targeted by mobs. Their inventory and logout location stay hidden until they log in, so someone using a friend's name cannot use their guns or find their base. This survives crashes. |
| **Brute-force protection** | 3 wrong passwords and you are kicked. 6 wrong passwords from one IP address in 10 minutes lock that address out for 15 minutes. Players who do not log in within 60 seconds are kicked. Easy passwords (`123456`, your own name, ...) are refused. |
| **Name impersonation** | A registered name is case-exact: when `Bob` exists, nobody can join as `bob`. |
| **Whitelist** | `white-list=true` + `enforce-whitelist=true`; `scripts/mc whitelist` writes the correct offline-mode IDs. |
| **Less information for scanners** | `hide-online-players=true` (the server list does not reveal names to impersonate); LAN broadcast off; RCON and query off. |
| **Safe files** | `.gitignore` keeps the world, logs (they contain player IPs), the whitelist/op lists and the password file out of this **public** GitHub repository. The server creates new files private to you (`umask 007`). |
| **Verified downloads** | Mods are checked against the SHA-1/SHA-512 hash the mod site publishes, or pinned by SHA-256 in `modpack/mods.lock.json` on first download. Java 21 is checked against Adoptium's SHA-256. |

Recommended extra steps:

- **Run it as a sandboxed service** (strongly recommended once friends join over the internet):

  ```sh
  sudo scripts/install-service.sh            # add --autostart to also start at boot
  ```

  The server then runs as a separate `minecraft` user that cannot read your home
  folder. It runs inside a systemd sandbox: read-only system, no privilege escalation,
  and it may only listen on the Minecraft port. Some mods have had remote-code-execution
  bugs in the past; this keeps that kind of damage inside the server folder. It also
  stops cleanly with a world save, restarts after crashes, and backs up every day at 05:00
  (keeps 3). Run `systemd-analyze security minecraft.service` to see the score. Log out
  and back in once afterwards, so your user can use the server's files and console.
- **Firewall:** `sudo apt install ufw`, then `sudo scripts/firewall.sh` (or `--lan` for
  home-network only). It blocks every incoming connection except Minecraft, and keeps ssh
  open if it is running.
- **Keep mods updated:** `python3 scripts/install-mods.py --update`. Mod updates fix
  security holes too; TaCZ's latest hotfix is one.
- **If every friend owns Minecraft**, set `online-mode=true` and
  `enforce-secure-profile=true` in `server.properties`. Mojang then checks every account,
  which is stronger than any password plugin, and the login mod turns itself off.

## Performance on this laptop

Already tuned:

- **Memory:** 6 GB for the server (`server/user_jvm_args.txt`), so your own game client
  still fits in 16 GB. With nothing else running you can raise `-Xmx` to `8G`.
- **Garbage collector:** Aikar's G1 flags, which suit the 2-core / 4-thread i5-5200U.
- **`server.properties`:** `view-distance=8`, `simulation-distance=5`,
  `sync-chunk-writes=false`, `max-players=10`, AFK kick after 30 minutes, world border at
  4000. `allow-flight=true` stops pilots from being kicked for "flying".
- **Server-side performance mods:**
  - ModernFix and FerriteCore (memory)
  - Async Locator (structure searches off the main thread)
  - Chunky (pre-generation)
  - spark (`/spark profiler` and `/spark health` show what lags)
- **Crash protection:** an entity that crashes while ticking is removed instead of
  taking the server down (`defaultconfigs/forge-server.toml`).

Tips:

- **Power:** keep the charger in. On battery the CPU slows down to ~800 MHz.
- **Lid:** closing the lid suspends the laptop and stops the server. To prevent that, set
  `HandleLidSwitchExternalPower=ignore` in `/etc/systemd/logind.conf`.
- **Swap:** the laptop has none. `sudo apt install zram-tools` adds compressed swap in RAM,
  a safety net that needs no disk space.
- **Disk:** only about 8 GB is free on `/`.
  - The mods take about 1 GB and the two player zips about 1 GB more. Delete `dist/` after
    sharing the zips; the installer rebuilds them.
  - `sudo apt clean` and `sudo journalctl --vacuum-size=200M` free space.
  - `start.sh` refuses to start with less than 1 GB free, because a full disk can corrupt the world.
- **If it lags:** lower `view-distance` to 6 and `simulation-distance` to 4, then run
  `/spark profiler` to find the cause.

## Everyday commands

| Task | Command |
|---|---|
| Start in a terminal | `cd server && ./start.sh` (type `stop` to quit) |
| Start / stop / status (service) | `scripts/mc start`, `scripts/mc stop`, `scripts/mc status` |
| Follow the log | `scripts/mc logs` |
| Console command (service) | `scripts/mc cmd say Hello` |
| Whitelist a player | `scripts/mc whitelist add Name` / `remove Name` |
| Make someone admin | `scripts/mc op Name` |
| Reset a password | `scripts/mc cmd auth unregister Name` |
| Back up now | `scripts/mc backup` (in a terminal session: `scripts/backup.sh --hot` after `save-off` and `save-all flush`) |
| Update mods | `python3 scripts/install-mods.py --update` |
| Login-mod settings | `server/config/serverauth.toml` |

## The mod list

`modpack/mods.json` lists every mod, its CurseForge/Modrinth name, and where it runs
(`both`, `server` or `client`). To drop a mod, set `"enabled": false` and run the
installer again. To add one, add an entry the same way.

- **Requested:**
  - Guns: TaCZ and LesRaisins Tactical Equipements, Vic's Point Blank, Mo' Than Enough Guns.
  - Superb Warfare, with the Vintage Vehicle Pack, MCSP Military Vehicle and AshVehicle add-ons.
  - Immersive Vehicles with the VEB Automobilwerke Schwikau pack.
  - Survival and world: Survival Instinct, Fracture Point (formerly Warborn Resurgence),
    LesRaisins Armor, Keerdm's Zombie Apocalypse Essentials (TaCZ variant), Simply Traps,
    SecurityCraft, Doomsday Decoration, Chovy's Apocalypse, Explosion Overhaul.
  - EnhancedVisuals (players only).
- **Libraries they need:** GeckoLib, Curios, Kotlin for Forge, The Lost Cities, Immersive
  Weathering, Moonlight Lib, CreativeCore. The installer adds any other dependency a mod
  declares.
- **Performance:** ModernFix, FerriteCore, spark, Chunky, Async Locator.
  AI Improvements is listed but off.
- **Players only:** Embeddium, Entity Culling, ImmediatelyFast.
- **Server only:** Server Auth (the login system).

Known issues:

- Vic's Point Blank can crash when a Point Blank akimbo gun is in the off-hand while a
  TaCZ gun is in the main hand.
- Four gun systems in one pack (TaCZ, Point Blank, Just Enough Guns, Superb Warfare) are a
  lot. If the server crashes and the crash report names one of them, disabling it is the
  quickest fix.

## Troubleshooting

- **The server stops during start-up.**
  - Read the end of `server/logs/latest.log` and any file in `server/crash-reports/`.
  - "Mod X requires Y" means a dependency is missing or has the wrong version: run the
    installer again and fix its `PROBLEM:` lines.
- **It crashes on Java 25.** Run `scripts/get-java.sh`; `start.sh` prefers that Java 21.
- **"You are not white-listed".** Run `scripts/mc whitelist add Name` with the exact name
  and capitalisation the friend uses in their launcher.
- **"This name is registered as ...".** The friend changed the capitalisation of their
  name. They must use the original one.
- **The service does not start.** Check `journalctl -u minecraft -e` and
  `server/logs/latest.log`. If the sandbox blocks something a mod needs, loosen one line
  at a time with `sudo systemctl edit minecraft`, for example `SystemCallFilter=` (empty)
  under `[Service]`.
- **The Keerdm's buildings and loot are missing.** The Lost Cities is not switched on for
  this world; see step 6 of the [quick start](#quick-start).

## What is in this repository

| Path | What it is |
|---|---|
| `server/` | The Forge server. `start.sh` starts it; `server.properties` and `user_jvm_args.txt` hold the tuned settings. |
| `server/defaultconfigs/` | Settings copied into every new world. |
| `modpack/mods.json` | The mod list. |
| `modpack/mods.lock.json` | The exact files chosen (created by the installer; commit it to keep everyone on the same versions). |
| `modpack/local/` | `serverauth-1.0.0.jar`, the login mod built from `mods-src/serverauth`. |
| `scripts/install-mods.py` | Downloads, checks and installs the mods, builds the players' pack. |
| `scripts/mc` | Everyday commands (whitelist, op, console, logs, backup). |
| `scripts/get-java.sh` | Downloads Java 21 into `server/.java`. |
| `scripts/install-service.sh` | Installs the sandboxed systemd service and daily backups. |
| `scripts/backup.sh` | Backs up world, configs and accounts into `server/backups/`. |
| `scripts/firewall.sh` | ufw rules for the Minecraft port. |
| `mods-src/serverauth/` | Source of the login mod; `build.sh` rebuilds it offline, `test/run.sh` runs its end-to-end tests. |
