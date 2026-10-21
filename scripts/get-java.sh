#!/usr/bin/env bash
# Downloads Java 21 (Eclipse Temurin JRE) into server/.java, which start.sh then uses.
# No root needed and your system Java stays as it is. Most 1.20.1 mods are made for
# Java 17-21; Kali's default Java 25 is newer than they are tested with.
set -euo pipefail

server="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/../server" && pwd)"
target="$server/.java"
case "$(uname -m)" in
    x86_64) arch=x64 ;;
    aarch64) arch=aarch64 ;;
    *) echo "get-java: unsupported CPU $(uname -m)" >&2; exit 1 ;;
esac

api="https://api.adoptium.net/v3/assets/latest/21/hotspot?architecture=$arch&image_type=jre&os=linux&vendor=eclipse"
echo "Asking Adoptium for the newest Java 21..."
read -r link checksum name < <(curl -fsSL "$api" | python3 -c '
import json, sys
release = json.load(sys.stdin)[0]
package = release["binary"]["package"]
print(package["link"], package["checksum"], release["release_name"])
')
[[ "$link" == https://* && "$checksum" =~ ^[0-9a-f]{64}$ ]] || { echo "get-java: unexpected answer from Adoptium" >&2; exit 1; }

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
echo "Downloading $name..."
curl -fL --progress-bar -o "$tmp/jre.tar.gz" "$link"
echo "$checksum  $tmp/jre.tar.gz" | sha256sum -c --quiet - || { echo "get-java: checksum mismatch, not installing" >&2; exit 1; }

mkdir "$tmp/jre"
tar -xzf "$tmp/jre.tar.gz" -C "$tmp/jre" --strip-components=1
"$tmp/jre/bin/java" -version
rm -rf "$target.old"
[[ -d "$target" ]] && mv "$target" "$target.old"
mv "$tmp/jre" "$target"
rm -rf "$target.old"
echo "Installed $name in $target. start.sh will use it from now on."
