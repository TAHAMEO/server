#!/usr/bin/env bash
# Builds the Server Auth mod into modpack/local/ without Gradle or internet access.
# It compiles against the Forge and Minecraft jars in server/libraries, using the
# same renaming tools the Forge installer uses. Needs a JDK 17+ (javac).
set -euo pipefail

here="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"
repo="$(cd "$here/../.." && pwd)"
lib="$repo/server/libraries"
build="$here/build"
version="$(sed -n 's/^version="\(.*\)"/\1/p' "$here/src/main/resources/META-INF/mods.toml")"
out="$repo/modpack/local/serverauth-$version.jar"

mc_srg="$lib/net/minecraft/server/1.20.1-20230612.114412/server-1.20.1-20230612.114412-srg.jar"
mojang_map="$lib/net/minecraft/server/1.20.1-20230612.114412/server-1.20.1-20230612.114412-mappings.txt"
mcp_map="$lib/de/oceanlabs/mcp/mcp_config/1.20.1-20230612.114412/mcp_config-1.20.1-20230612.114412-mappings-merged.txt"
forge_dir="$lib/net/minecraftforge/forge/1.20.1-47.4.27"
srgutils="$lib/net/minecraftforge/srgutils/0.5.6/srgutils-0.5.6.jar"
renamer="$lib/net/minecraftforge/ForgeAutoRenamingTool/0.1.22/ForgeAutoRenamingTool-0.1.22-all.jar"

command -v javac >/dev/null || { echo "javac not found: install a JDK, e.g. sudo apt install openjdk-21-jdk-headless" >&2; exit 1; }
for f in "$mc_srg" "$mojang_map" "$mcp_map" "$srgutils" "$renamer"; do
    [[ -f "$f" ]] || { echo "Missing $f (run the Forge installer first)" >&2; exit 1; }
done
mkdir -p "$build"

# 1. Readable-name versions of Minecraft and Forge's patched classes to compile against (cached).
if [[ ! -f "$build/named2srg.tsrg" ]]; then
    java -cp "$srgutils" "$here/tools/Combine.java" "$mojang_map" "$mcp_map" "$build/named2srg.tsrg"
fi
if [[ ! -f "$build/mc-named.jar" ]]; then
    java -jar "$renamer" --input "$mc_srg" --output "$build/mc-named.jar" \
        --map "$build/named2srg.tsrg" --reverse --log "$build/rename-mc.log" >/dev/null
fi
if [[ ! -f "$build/forge-named.jar" ]]; then
    java -jar "$renamer" --input "$forge_dir/forge-1.20.1-47.4.27-server.jar" --output "$build/forge-named.jar" \
        --map "$build/named2srg.tsrg" --reverse --lib "$build/mc-named.jar" --log "$build/rename-forge.log" >/dev/null
fi

# 2. Compile. Forge's patched classes go first so they win over plain Minecraft ones.
classpath="$build/forge-named.jar:$build/mc-named.jar:$forge_dir/forge-1.20.1-47.4.27-universal.jar"
while IFS= read -r jar; do
    classpath+=":$jar"
done < <(find "$lib" -name '*.jar' ! -path '*/net/minecraft/server/*' ! -path '*/net/minecraftforge/forge/*' | sort)
rm -rf "$build/classes"
mapfile -t sources < <(find "$here/src/main/java" -name '*.java' | sort)
javac --release 17 -encoding UTF-8 -proc:none -Xlint:all -Werror -cp "$classpath" -d "$build/classes" "${sources[@]}"
cp -r "$here/src/main/resources/." "$build/classes/"
(cd "$build/classes" && rm -f "$build/serverauth-named.jar" && zip -qrX "$build/serverauth-named.jar" .)

# 3. Rename to the SRG names Forge uses at runtime.
mkdir -p "$(dirname "$out")"
java -jar "$renamer" --input "$build/serverauth-named.jar" --output "$out" --map "$build/named2srg.tsrg" \
    --lib "$build/forge-named.jar" --lib "$build/mc-named.jar" --lib "$forge_dir/forge-1.20.1-47.4.27-universal.jar" \
    --log "$build/rename-mod.log" >/dev/null
echo "Built $out"
