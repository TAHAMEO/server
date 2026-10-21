#!/usr/bin/env python3
"""Download the modpack, check it, install the server mods and build the players' pack.

    python3 scripts/install-mods.py            install / repair (uses modpack/mods.lock.json)
    python3 scripts/install-mods.py --update   look for newer versions of every mod

Steps:
  1. Finds the newest Forge 1.20.1 file of every mod in modpack/mods.json on
     CurseForge (or Modrinth), plus the mods those files require.
  2. Downloads them to modpack/cache/ and checks their hashes.
  3. Reads every jar's mods.toml and reports missing or wrong-version dependencies.
  4. Puts the server-side mods in server/mods/.
  5. Builds dist/<pack>-client.zip and dist/<pack>-curseforge.zip for the players.

The exact files end up in modpack/mods.lock.json, so later runs (and the players'
pack) get the very same versions.

CurseForge works best with a free API key from https://console.curseforge.com
(run: export CF_API_KEY='...'). Without one the script tries CurseForge's public
website API and then Modrinth. Anything it cannot download is listed with a link:
save those files into modpack/manual/ and run the script again.
"""

from __future__ import annotations

import argparse
import functools
import hashlib
import io
import json
import os
import re
import shutil
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from dataclasses import asdict, dataclass, field
from pathlib import Path

if sys.version_info < (3, 11):
    sys.exit("Python 3.11 or newer is needed (for tomllib).")
import tomllib  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
MODPACK = ROOT / "modpack"
MANIFEST = MODPACK / "mods.json"
LOCK = MODPACK / "mods.lock.json"
CACHE = MODPACK / "cache"
MANUAL = MODPACK / "manual"
MANUAL_CLIENT = MODPACK / "manual-client"
DIST = ROOT / "dist"
SERVER = ROOT / "server"
SERVER_MODS = SERVER / "mods"
INSTALLED = SERVER / ".mods-installed.json"

USER_AGENT = "TAHAMEO-server-modpack-installer/1.0 (github.com/TAHAMEO/server)"
CF_API = "https://api.curseforge.com"
CF_WEB = "https://www.curseforge.com/api/v1"
MR_API = "https://api.modrinth.com/v2"
CF_GAME_MINECRAFT = 432
CF_CLASS_MODS = 6
CF_LOADER_FORGE = 1
CF_REQUIRED_DEPENDENCY = 3
CF_RELEASE_ORDER = {1: 0, 2: 1, 3: 2}  # release, beta, alpha
MR_RELEASE_ORDER = {"release": 0, "beta": 1, "alpha": 2}
BUILTIN_MODS = {"minecraft", "forge", "javafml", "lowcodefml", "mclanguage"}


class ResolveError(Exception):
    pass


@dataclass
class ModFile:
    """One downloadable jar, as stored in mods.lock.json."""

    name: str
    side: str
    source: str  # curseforge, modrinth, local or manual
    project: str
    file_id: str
    file_name: str
    url: str | None
    page: str
    hashes: dict[str, str] = field(default_factory=dict)
    entry: str | None = None  # the mods.json entry this file is for
    spec: str | None = None  # fingerprint of that entry's settings; a changed entry is resolved again
    also: list[str] = field(default_factory=list)  # the same mod on the other site, e.g. "modrinth:SzzJttH8"
    required_by: list[str] = field(default_factory=list)
    path: str | None = None  # where the jar is on disk (not saved in the lock)

    def lock_dict(self) -> dict:
        data = asdict(self)
        data.pop("path")
        return data


# ---------------------------------------------------------------- networking


def http(url: str, headers: dict | None = None, attempts: int = 3) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **(headers or {})})
    for attempt in range(1, attempts + 1):
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                return response.read()
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504) and attempt < attempts:
                time.sleep(2 * attempt)
                continue
            raise ResolveError(f"HTTP {e.code} from {urllib.parse.urlsplit(url).netloc}") from None
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            if attempt < attempts:
                time.sleep(2 * attempt)
                continue
            reason = getattr(e, "reason", e)
            raise ResolveError(f"cannot reach {urllib.parse.urlsplit(url).netloc}: {reason}") from None
    raise AssertionError("unreachable")


def download(url: str, dest: Path, attempts: int = 3) -> None:
    """Streams a file to disk (some mods are over 100 MB)."""
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    for attempt in range(1, attempts + 1):
        try:
            with urllib.request.urlopen(request, timeout=120) as response, dest.open("wb") as out:
                shutil.copyfileobj(response, out, 1 << 20)
            return
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504) and attempt < attempts:
                time.sleep(2 * attempt)
                continue
            raise ResolveError(f"HTTP {e.code} from {urllib.parse.urlsplit(url).netloc}") from None
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            if attempt < attempts:
                time.sleep(2 * attempt)
                continue
            raise ResolveError(f"cannot reach {urllib.parse.urlsplit(url).netloc}: {getattr(e, 'reason', e)}") from None


def safe_name(name: str) -> str:
    """File names come from the download sites; never let one point outside our folders."""
    if not name or name != Path(name).name or name.startswith(".") or "\\" in name:
        raise ResolveError(f"refusing the file name {name!r}")
    return name


def http_json(url: str, headers: dict | None = None):
    try:
        return json.loads(http(url, headers))
    except json.JSONDecodeError:
        raise ResolveError(f"unexpected answer from {urllib.parse.urlsplit(url).netloc}") from None


def quote_url(url: str) -> str:
    """CurseForge download URLs can contain spaces and brackets."""
    parts = urllib.parse.urlsplit(url)
    return urllib.parse.urlunsplit(parts._replace(path=urllib.parse.quote(urllib.parse.unquote(parts.path))))


def file_hashes(path: Path) -> dict[str, str]:
    digests = {"sha1": hashlib.sha1(), "sha256": hashlib.sha256(), "sha512": hashlib.sha512()}
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            for d in digests.values():
                d.update(chunk)
    return {k: d.hexdigest() for k, d in digests.items()}


def hashes_match(expected: dict[str, str], actual: dict[str, str]) -> bool:
    common = [k for k in ("sha512", "sha256", "sha1") if k in expected]
    return bool(common) and all(expected[k].lower() == actual[k] for k in common)


# ---------------------------------------------------------------- Modrinth


class Modrinth:
    def __init__(self, minecraft: str):
        self.minecraft = minecraft
        self.projects: dict[str, dict] = {}

    def project(self, slug_or_id: str) -> dict:
        if slug_or_id not in self.projects:
            self.projects[slug_or_id] = http_json(f"{MR_API}/project/{urllib.parse.quote(slug_or_id)}")
        return self.projects[slug_or_id]

    def resolve(self, slug_or_id: str, name: str | None, side: str, match: str | None) -> tuple[ModFile, list[str]]:
        project = self.project(slug_or_id)
        query = urllib.parse.urlencode({"loaders": '["forge"]', "game_versions": json.dumps([self.minecraft])})
        versions = http_json(f"{MR_API}/project/{project['id']}/version?{query}")
        if match:
            versions = [v for v in versions if any(match.lower() in f["filename"].lower() for f in v["files"])
                        or match.lower() in v["version_number"].lower()]
        if not versions:
            raise ResolveError(f"no Forge {self.minecraft} file on Modrinth")
        versions.sort(key=lambda v: v["date_published"], reverse=True)
        versions.sort(key=lambda v: MR_RELEASE_ORDER.get(v["version_type"], 3))
        version = versions[0]
        files = version["files"]
        jar = next((f for f in files if f.get("primary")), files[0])
        deps = [d["project_id"] for d in version.get("dependencies", [])
                if d.get("dependency_type") == "required" and d.get("project_id")]
        mod = ModFile(
            name=name or project["title"], side=side, source="modrinth", project=project["id"],
            file_id=version["id"], file_name=safe_name(jar["filename"]), url=jar["url"],
            page=f"https://modrinth.com/mod/{project['slug']}/version/{version['id']}",
            hashes={k: v for k, v in jar["hashes"].items() if k in ("sha1", "sha512")})
        return mod, deps


# ---------------------------------------------------------------- CurseForge


@dataclass
class CfFile:
    id: int
    name: str
    display: str
    date: str
    release: int
    versions: list[str]
    url: str | None
    sha1: str | None
    deps: list[int]


class CurseForge:
    """Official API when CF_API_KEY is set, otherwise CurseForge's public website API (best effort)."""

    def __init__(self, minecraft: str, api_key: str | None):
        self.minecraft = minecraft
        self.key = api_key
        self.disabled_reason: str | None = None
        self.search_blocked: str | None = None
        self.mods: dict[int, dict] = {}

    @property
    def mode(self) -> str:
        return "API" if self.key else "website"

    def _get(self, url: str, search: bool = False):
        if self.disabled_reason:
            raise ResolveError(self.disabled_reason)
        try:
            return http_json(url, {"x-api-key": self.key, "Accept": "application/json"} if self.key
                             else {"Accept": "application/json"})
        except ResolveError as e:
            if not self.key and "HTTP 404" not in str(e):
                # The website blocks its search for scripts, while file lists still work.
                # So a failed search only stops searching; any other failure stops CurseForge.
                if search:
                    self.search_blocked = str(e)
                else:
                    self.disabled_reason = f"CurseForge website not usable ({e})"
            raise

    def project_id(self, slug: str, known_id: int | None) -> int:
        if known_id:
            return int(known_id)
        if self.key:
            query = urllib.parse.urlencode({"gameId": CF_GAME_MINECRAFT, "classId": CF_CLASS_MODS, "slug": slug})
            hits = self._get(f"{CF_API}/v1/mods/search?{query}").get("data", [])
        else:
            if self.search_blocked:
                raise ResolveError(f"needs its CurseForge project ID (\"curseforgeId\" in mods.json); "
                                   f"CurseForge search is blocked ({self.search_blocked})")
            query = urllib.parse.urlencode({"gameId": CF_GAME_MINECRAFT, "classId": CF_CLASS_MODS, "filterText": slug,
                                            "pageSize": 20, "index": 0})
            hits = self._get(f"{CF_WEB}/mods/search?{query}", search=True).get("data", [])
        for hit in hits:
            if hit.get("slug") == slug:
                self.mods[int(hit["id"])] = hit
                return int(hit["id"])
        raise ResolveError(f"no CurseForge project with the slug '{slug}'")

    def mod_info(self, project: int) -> dict:
        if project not in self.mods:
            if self.key:
                self.mods[project] = self._get(f"{CF_API}/v1/mods/{project}").get("data", {})
            else:
                self.mods[project] = {}
        return self.mods[project]

    def files(self, project: int) -> list[CfFile]:
        out: list[CfFile] = []
        if self.key:
            query = urllib.parse.urlencode({"gameVersion": self.minecraft, "modLoaderType": CF_LOADER_FORGE,
                                            "pageSize": 50, "index": 0})
            for f in self._get(f"{CF_API}/v1/mods/{project}/files?{query}").get("data", []):
                if f.get("isServerPack") or f.get("isAvailable") is False:
                    continue
                sha1 = next((h["value"] for h in f.get("hashes", []) if h.get("algo") == 1), None)
                deps = [d["modId"] for d in f.get("dependencies", []) if d.get("relationType") == CF_REQUIRED_DEPENDENCY]
                out.append(CfFile(f["id"], f["fileName"], f.get("displayName", f["fileName"]), f.get("fileDate", ""),
                                  f.get("releaseType", 1), f.get("gameVersions", []), f.get("downloadUrl"), sha1, deps))
            return out
        for page in range(6):
            query = urllib.parse.urlencode({"pageIndex": page, "pageSize": 50, "sort": "dateCreated",
                                            "sortDescending": "true", "removeAlphas": "false"})
            data = self._get(f"{CF_WEB}/mods/{project}/files?{query}").get("data", [])
            for f in data:
                if f.get("isServerPack"):
                    continue
                url = f"{CF_WEB}/mods/{project}/files/{f['id']}/download"
                out.append(CfFile(f["id"], f["fileName"], f.get("displayName", f["fileName"]), f.get("dateCreated", ""),
                                  f.get("releaseType", 1), f.get("gameVersions", []), url, None, []))
            if len(data) < 50 or any(self._fits(f) for f in out):
                break
        return out

    def _fits(self, f: CfFile) -> bool:
        tags = {v.lower() for v in f.versions}
        if self.minecraft not in tags:
            return False
        loaders = tags & {"forge", "neoforge", "fabric", "quilt"}
        return not loaders or "forge" in loaders

    def resolve(self, slug: str, known_id: int | None, name: str | None, side: str,
                match: str | None) -> tuple[ModFile, list[int]]:
        project = self.project_id(slug, known_id)
        files = [f for f in self.files(project) if self._fits(f)]
        if match:
            files = [f for f in files if match.lower() in f.name.lower() or match.lower() in f.display.lower()]
        if not files:
            raise ResolveError(f"no Forge {self.minecraft} file on CurseForge")
        files.sort(key=lambda f: f.date, reverse=True)
        files.sort(key=lambda f: CF_RELEASE_ORDER.get(f.release, 3))
        f = files[0]
        info = self.mod_info(project)
        slug = info.get("slug", slug)
        mod = ModFile(
            name=name or info.get("name") or slug or f"CurseForge project {project}", side=side, source="curseforge",
            project=str(project), file_id=str(f.id), file_name=safe_name(f.name), url=quote_url(f.url) if f.url else None,
            page=f"https://www.curseforge.com/minecraft/mc-mods/{slug}/files/{f.id}",
            hashes={"sha1": f.sha1} if f.sha1 else {})
        return mod, f.deps


# ---------------------------------------------------------------- resolving


def load_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default


def entry_spec(entry: dict) -> str:
    return hashlib.sha1(json.dumps(entry, sort_keys=True).encode("utf-8")).hexdigest()[:12]


def resolve_pack(manifest: dict, lock: dict | None, cf: CurseForge, mr: Modrinth,
                 offline: bool) -> tuple[list[ModFile], list[str]]:
    """Returns the files to use and a list of problems."""
    problems: list[str] = []
    files: list[ModFile] = []
    locked = {}
    for item in (lock or {}).get("files", []):
        mod = ModFile(**{k: v for k, v in item.items() if k in ModFile.__dataclass_fields__})
        locked.setdefault(mod.entry or f"dep:{mod.source}:{mod.project}", mod)
    seen_projects: set[tuple[str, str]] = set()
    entries = [e for e in manifest["mods"] if e.get("enabled", True)]
    entry_names = {e["name"] for e in entries}

    def add(mod: ModFile) -> bool:
        key = (mod.source, mod.project)
        if key in seen_projects:
            return False
        seen_projects.add(key)
        files.append(mod)
        return True

    # Projects the manifest already lists, so dependencies are not added twice.
    listed_cf = {str(e["curseforgeId"]) for e in entries if e.get("curseforgeId")}
    queue: list[tuple[str, str, str, list[str]]] = []  # (source, project, side, required_by)

    for e in entries:
        spec = entry_spec(e)
        mod = locked.get(e["name"])
        if mod and mod.spec == spec:
            add(mod)
            continue
        if offline:
            problems.append(f"{e['name']}: not in mods.lock.json and --offline was given")
            continue
        errors = []
        # Modrinth first: it publishes hashes and dependency lists. "prefer": "curseforge"
        # is for mods whose newest files are only on CurseForge.
        order = ("curseforge", "modrinth") if e.get("prefer") == "curseforge" else ("modrinth", "curseforge")
        for source in order:
            try:
                if source == "curseforge" and e.get("curseforge"):
                    if cf.disabled_reason:
                        errors.append(f"curseforge: skipped ({cf.disabled_reason})")
                        continue
                    mod, deps = cf.resolve(e["curseforge"], e.get("curseforgeId"), e["name"], e["side"], e.get("match"))
                    queue += [("curseforge", str(d), e["side"], [e["name"]]) for d in deps]
                elif source == "modrinth" and e.get("modrinth"):
                    mod, deps = mr.resolve(e["modrinth"], e["name"], e["side"], e.get("match"))
                    queue += [("modrinth", d, e["side"], [e["name"]]) for d in deps]
                else:
                    continue
                mod.entry = e["name"]
                mod.spec = spec
                # Remember the mod's ID on the other site, so a dependency found there is not added again.
                if mod.source == "curseforge" and e.get("modrinth"):
                    try:
                        mod.also.append("modrinth:" + mr.project(e["modrinth"])["id"])
                    except ResolveError:
                        pass
                elif mod.source == "modrinth" and e.get("curseforgeId"):
                    mod.also.append(f"curseforge:{e['curseforgeId']}")
                add(mod)
                print(f"  found  {e['name']}: {mod.file_name} ({mod.source})")
                break
            except ResolveError as err:
                errors.append(f"{source}: {err}")
        else:
            links = [f"https://www.curseforge.com/minecraft/mc-mods/{e['curseforge']}/files" if e.get("curseforge") else None,
                     f"https://modrinth.com/mod/{e['modrinth']}/versions" if e.get("modrinth") else None]
            problems.append(f"{e['name']}: could not find it ({'; '.join(errors) or 'no source configured'}). "
                            f"Download the Forge {manifest['pack']['minecraft']} file from "
                            f"{' or '.join(l for l in links if l)} into modpack/manual/")

    # A dependency that is also a listed mod (found on the other site) must not be added twice.
    listed_ids = {f"curseforge:{p}" for p in listed_cf}
    for mod in files:
        listed_ids.add(f"{mod.source}:{mod.project}")
        listed_ids.update(mod.also)

    def listed(source: str, project: str) -> bool:
        return f"{source}:{project}" in listed_ids

    pending_deps = [mod for key, mod in locked.items() if key.startswith("dep:")]

    # Dependencies from the lock file, as long as something in the pack still needs them.
    needed = set(entry_names)
    while True:
        ready = [mod for mod in pending_deps if any(r in needed for r in mod.required_by)]
        if not ready:
            break
        for mod in ready:
            pending_deps.remove(mod)
            needed.add(mod.name)
            if not listed(mod.source, mod.project):
                add(mod)

    # Required dependencies the mod pages declare (only the CurseForge API and Modrinth report them).
    while queue and not offline:
        source, project, side, required_by = queue.pop(0)
        if (source, project) in seen_projects or listed(source, project):
            continue
        try:
            if source == "curseforge":
                mod, deps = cf.resolve("", int(project), None, side, None)
                queue += [("curseforge", str(d), side, [mod.name]) for d in deps]
            else:
                mod, deps = mr.resolve(project, None, side, None)
                queue += [("modrinth", d, side, [mod.name]) for d in deps]
        except ResolveError as err:
            problems.append(f"dependency {source} project {project} (needed by {', '.join(required_by)}): {err}")
            continue
        mod.required_by = required_by
        mod.entry = None
        if add(mod):
            print(f"  found  {mod.name}: {mod.file_name} (needed by {', '.join(required_by)})")
    return files, problems


def fetch(mod: ModFile, offline: bool) -> str | None:
    """Puts the jar in the cache (or finds it in modpack/manual/); returns a problem or None."""
    if mod.source == "local":
        return None
    dest = CACHE / mod.source / mod.project / mod.file_name
    manual = MANUAL / mod.file_name
    for candidate in (dest, manual):
        if candidate.is_file():
            actual = file_hashes(candidate)
            if not mod.hashes or hashes_match(mod.hashes, actual):
                mod.hashes = mod.hashes or {"sha256": actual["sha256"]}
                mod.path = str(candidate)
                return None
            if candidate == manual:
                return f"{mod.name}: modpack/manual/{mod.file_name} does not match the expected file; download it again from {mod.page}"
    if offline or not mod.url:
        why = "the author only allows downloads from the CurseForge website" if not mod.url else "--offline"
        return f"{mod.name}: download {mod.file_name} by hand ({why}) from {mod.page} and save it in modpack/manual/"
    tmp = dest.with_name(dest.name + ".part")
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        download(mod.url, tmp)
    except ResolveError as err:
        tmp.unlink(missing_ok=True)
        return f"{mod.name}: download failed ({err}); get it from {mod.page} and save it in modpack/manual/"
    actual = file_hashes(tmp)
    if mod.hashes and not hashes_match(mod.hashes, actual):
        tmp.unlink()
        return f"{mod.name}: the downloaded file does not match its published hash; not using it"
    if not zipfile.is_zipfile(tmp):
        tmp.unlink()
        return f"{mod.name}: the download is not a jar file; get it from {mod.page} and save it in modpack/manual/"
    tmp.replace(dest)
    # Without a published hash, remember this one: later runs must get the identical file.
    mod.hashes = mod.hashes or {"sha256": actual["sha256"]}
    mod.path = str(dest)
    print(f"  saved  {mod.file_name}")
    return None


def manual_extras(files: list[ModFile]) -> list[ModFile]:
    """Extra jars dropped in by hand: modpack/manual/ (server and players), modpack/manual-client/ (players only)."""
    known = {m.file_name for m in files}
    extras = []
    for folder, side in ((MANUAL, "both"), (MANUAL_CLIENT, "client")):
        for jar in sorted(folder.glob("*.jar")):
            if jar.name not in known:
                extras.append(ModFile(name=jar.stem, side=side, source="manual", project=jar.name, file_id="",
                                      file_name=jar.name, url=None, page="",
                                      hashes={"sha256": file_hashes(jar)["sha256"]}, path=str(jar)))
    return extras


# ---------------------------------------------------------------- checking jars


@dataclass
class ModInfo:
    mod_id: str
    version: str
    deps: list[tuple[str, bool, str, str]]  # (mod id, mandatory, version range, side)
    nested: bool  # embedded with Jar-in-Jar (Forge picks the newest copy, so duplicates are fine)


def read_mods(data: bytes, nested: bool = False) -> list[ModInfo]:
    """Mods declared in a jar's META-INF/mods.toml, including jars embedded with Jar-in-Jar."""
    mods: list[ModInfo] = []
    with zipfile.ZipFile(io.BytesIO(data)) as jar:
        names = set(jar.namelist())
        if "META-INF/mods.toml" in names:
            try:
                toml = tomllib.loads(jar.read("META-INF/mods.toml").decode("utf-8", "replace"))
            except tomllib.TOMLDecodeError:
                toml = {}
            jar_version = ""
            if "META-INF/MANIFEST.MF" in names:
                m = re.search(r"^Implementation-Version:\s*(\S+)", jar.read("META-INF/MANIFEST.MF").decode("utf-8", "replace"), re.M)
                jar_version = m.group(1) if m else ""
            all_deps = toml.get("dependencies", {})
            for mod in toml.get("mods", []):
                mod_id = str(mod.get("modId", "")).strip()
                if not mod_id:
                    continue
                version = str(mod.get("version", ""))
                if "${" in version:
                    version = jar_version
                deps = []
                for d in all_deps.get(mod_id, []) if isinstance(all_deps, dict) else []:
                    mandatory = d.get("mandatory", d.get("type", "required") == "required")
                    deps.append((str(d.get("modId", "")), bool(mandatory), str(d.get("versionRange", "")),
                                 str(d.get("side", "BOTH")).upper()))
                mods.append(ModInfo(mod_id, version, deps, nested))
        if "META-INF/jarjar/metadata.json" in names:
            try:
                meta = json.loads(jar.read("META-INF/jarjar/metadata.json"))
            except json.JSONDecodeError:
                meta = {}
            for embedded in meta.get("jars", []):
                path = embedded.get("path")
                if path in names:
                    try:
                        mods += read_mods(jar.read(path), nested=True)
                    except zipfile.BadZipFile:
                        pass
    return mods


_QUALIFIERS = {"alpha": 0, "a": 0, "beta": 1, "b": 1, "milestone": 2, "m": 2, "rc": 3, "cr": 3, "pre": 3,
               "snapshot": 4, "": 5, "final": 5, "release": 5, "ga": 5, "sp": 6}


def _version_items(version: str) -> list[tuple[int, int, str]]:
    items = []
    for part in re.findall(r"\d+|[a-zA-Z]+", version.lower()):
        if part.isdigit():
            items.append((2, int(part), ""))
        else:
            rank = _QUALIFIERS.get(part)
            items.append((1, rank, "") if rank is not None else (1, 7, part))
    return items


def compare_versions(a: str, b: str) -> int:
    """Close to Maven's ComparableVersion, which Forge uses: 1.0-beta < 1.0 < 1.0.1."""
    x, y = _version_items(a), _version_items(b)
    for i in range(max(len(x), len(y))):
        p = x[i] if i < len(x) else None
        q = y[i] if i < len(y) else None
        if p is None:
            p = (2, 0, "") if q[0] == 2 else (1, 5, "")
        if q is None:
            q = (2, 0, "") if p[0] == 2 else (1, 5, "")
        if p != q:
            return -1 if p < q else 1
    return 0


version_key = functools.cmp_to_key(compare_versions)


def in_range(version: str, spec: str) -> bool | None:
    """True/False for a Maven version range like [1.2,2.0); None when it cannot tell."""
    spec = spec.strip()
    if not spec or spec == "*":
        return True
    if not version:
        return None
    if spec[0] not in "[(":
        return True  # a bare version is only a recommendation
    groups = re.findall(r"([\[(])([^\])]*)([\])])", spec)
    if not groups:
        return None
    for opening, inner, closing in groups:
        if "," not in inner:
            if compare_versions(version, inner.strip()) == 0:
                return True
            continue
        low, high = (part.strip() for part in inner.split(",", 1))
        ok = True
        if low:
            c = compare_versions(version, low)
            ok = c >= 0 if opening == "[" else c > 0
        if ok and high:
            c = compare_versions(version, high)
            ok = c <= 0 if closing == "]" else c < 0
        if ok:
            return True
    return False


def check_pack(files: list[ModFile], side: str, builtin: dict[str, str]) -> tuple[list[str], list[str], set[int]]:
    """Checks one side ('server' or 'client').

    Returns problems, notes, and the id() of every file to leave out because another
    file is the same mod (Forge refuses to start with a mod installed twice).
    """
    present = [m for m in files if m.path and m.side in ("both", side)]
    provided: dict[str, list[tuple[str, ModFile, bool]]] = {}
    infos: dict[int, list[ModInfo]] = {}
    for mod in present:
        try:
            infos[id(mod)] = read_mods(Path(mod.path).read_bytes())
        except (zipfile.BadZipFile, OSError) as e:
            infos[id(mod)] = []
            print(f"  warning: cannot read {mod.file_name}: {e}")
        for info in infos[id(mod)]:
            provided.setdefault(info.mod_id, []).append((info.version, mod, not info.nested))

    problems: list[str] = []
    notes: list[str] = []
    drop: set[int] = set()
    for mod_id, providers in provided.items():
        top_level = [p for p in providers if p[2] and id(p[1]) not in drop]
        if len(top_level) > 1:
            # Keep a file listed in mods.json over a dependency, then the newest version.
            top_level.sort(key=lambda p: version_key(p[0]), reverse=True)
            top_level.sort(key=lambda p: p[1].entry is not None, reverse=True)
            for _, mod, _ in top_level[1:]:
                drop.add(id(mod))
            kept = top_level[0][1]
            others = sorted({p[1].file_name for p in top_level[1:]} - {kept.file_name})
            if others:
                notes.append(f"{mod_id} was found twice; using {kept.file_name}, not {', '.join(others)}")
    versions = {mod_id: max((p[0] for p in ps if id(p[1]) not in drop), key=version_key, default="")
                for mod_id, ps in provided.items()}
    versions.update(builtin)
    for mod in present:
        if id(mod) in drop:
            continue
        for info in infos[id(mod)]:
            for dep_id, mandatory, spec, dep_side in info.deps:
                if not mandatory or dep_side not in ("BOTH", side.upper()) or not dep_id:
                    continue
                if dep_id not in versions:
                    if dep_id not in BUILTIN_MODS:
                        problems.append(f"{info.mod_id} ({mod.file_name}) needs the mod '{dep_id}' {spec}, which is not in the pack")
                    continue
                if in_range(versions[dep_id], spec) is False:
                    problems.append(f"{info.mod_id} ({mod.file_name}) needs {dep_id} {spec}, but the pack has {versions[dep_id]}")
    return problems, notes, drop


# ---------------------------------------------------------------- installing


def link_or_copy(src: Path, dest: Path) -> None:
    if dest.exists():
        if dest.samefile(src):
            return
        dest.unlink()
    try:
        os.link(src, dest)
    except OSError:
        shutil.copy2(src, dest)


def install_server(files: list[ModFile]) -> None:
    SERVER_MODS.mkdir(exist_ok=True)
    previous = set(load_json(INSTALLED, []))
    wanted = {m.file_name: Path(m.path) for m in files}
    for name in previous - wanted.keys():
        (SERVER_MODS / name).unlink(missing_ok=True)
        print(f"  removed  server/mods/{name}")
    for name, src in sorted(wanted.items()):
        link_or_copy(src, SERVER_MODS / name)
    INSTALLED.write_text(json.dumps(sorted(wanted), indent=2) + "\n", encoding="utf-8")
    others = sorted(p.name for p in SERVER_MODS.glob("*.jar") if p.name not in wanted)
    if others:
        print(f"  note: server/mods also has jars this script did not install: {', '.join(others)}")
    print(f"  server/mods: {len(wanted)} mods")


def client_readme(pack: dict, files: list[ModFile], cf_zip: str) -> str:
    lines = [
        f"{pack['name']} - player pack",
        f"Minecraft {pack['minecraft']} with Forge {pack['forge']} (any newer 47.x Forge works too)",
        "",
        "EASIEST: CurseForge app -> Create Custom Profile -> Import -> pick " + cf_zip,
        "         (Prism Launcher and ATLauncher can import that zip as well.)",
        "",
        "BY HAND:",
        f" 1. Install Forge {pack['minecraft']}-{pack['forge']} from https://files.minecraftforge.net",
        " 2. Copy everything in the 'mods' folder of this zip into .minecraft/mods",
        "    (empty that folder first if you used other mods before).",
        " 3. Give the game 6 GB of RAM in your launcher. Do NOT install OptiFine.",
        "",
        "FIRST TIME ON THE SERVER:  /register <password> <password>",
        "EVERY TIME AFTER THAT:     /login <password>",
        "Use a password you do not use anywhere else.",
        "",
        "Mods in this pack:",
    ]
    lines += [f"  - {m.name} ({m.file_name})" for m in sorted(files, key=lambda m: m.name.lower())]
    return "\n".join(lines) + "\n"


def build_client_packs(pack: dict, files: list[ModFile]) -> list[Path]:
    DIST.mkdir(exist_ok=True)
    files = list({m.file_name: m for m in files}.values())
    slug = re.sub(r"[^a-z0-9]+", "-", pack["name"].lower()).strip("-")
    plain = DIST / f"{slug}-client.zip"
    cf_zip = DIST / f"{slug}-curseforge.zip"
    readme = client_readme(pack, files, cf_zip.name)

    tmp = plain.with_suffix(".zip.part")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("README.txt", readme)
        for m in files:
            z.write(m.path, f"mods/{m.file_name}", compress_type=zipfile.ZIP_STORED)
    tmp.replace(plain)

    manifest = {
        "minecraft": {"version": pack["minecraft"],
                      "modLoaders": [{"id": f"{pack['loader']}-{pack['forge']}", "primary": True}]},
        "manifestType": "minecraftModpack",
        "manifestVersion": 1,
        "name": pack["name"],
        "version": pack.get("version", "1.0.0"),
        "author": "",
        "files": [{"projectID": int(m.project), "fileID": int(m.file_id), "required": True}
                  for m in files if m.source == "curseforge"],
        "overrides": "overrides",
    }
    tmp = cf_zip.with_suffix(".zip.part")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("manifest.json", json.dumps(manifest, indent=2))
        z.writestr("modlist.txt", readme)
        for m in files:
            if m.source != "curseforge":
                z.write(m.path, f"overrides/mods/{m.file_name}", compress_type=zipfile.ZIP_STORED)
    tmp.replace(cf_zip)
    return [plain, cf_zip]


# ---------------------------------------------------------------- main


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--update", action="store_true", help="look for newer versions instead of using mods.lock.json")
    parser.add_argument("--offline", action="store_true", help="no internet: use only mods.lock.json and downloaded files")
    parser.add_argument("--no-server", action="store_true", help="do not touch server/mods")
    parser.add_argument("--no-client-pack", action="store_true", help="do not build the players' zip files")
    args = parser.parse_args()

    manifest = load_json(MANIFEST, None)
    if manifest is None:
        print(f"{MANIFEST} is missing", file=sys.stderr)
        return 2
    pack = manifest["pack"]
    lock = None if args.update else load_json(LOCK, None)
    cf = CurseForge(pack["minecraft"], os.environ.get("CF_API_KEY") or None)
    mr = Modrinth(pack["minecraft"])

    print(f"Resolving {pack['name']} for Minecraft {pack['minecraft']} / Forge "
          f"(CurseForge via {cf.mode}{'' if cf.key else '; set CF_API_KEY for the official API'})")
    files, problems = resolve_pack(manifest, lock, cf, mr, args.offline)
    for item in manifest.get("local", []):
        path = MODPACK / item["file"]
        if path.is_file():
            files.append(ModFile(name=item["name"], side=item["side"], source="local", project=item["file"], file_id="",
                                 file_name=path.name, url=None, page="", entry=item["name"], path=str(path),
                                 hashes={"sha256": file_hashes(path)["sha256"]}))
        else:
            problems.append(f"{item['name']}: {path} is missing (build it with mods-src/serverauth/build.sh)")

    print("Downloading")
    for mod in files:
        problem = fetch(mod, args.offline)
        if problem:
            problems.append(problem)
    files += manual_extras(files)

    print("Checking dependencies")
    builtin = {"minecraft": pack["minecraft"], "forge": pack["forge"]}
    check_problems: list[str] = []
    notes: list[str] = []
    dropped: set[int] = set()
    for side in ("server", "client"):
        side_problems, side_notes, drop = check_pack(files, side, builtin)
        dropped |= drop
        check_problems += [f"[{side}] {p}" for p in side_problems]
        notes += [n for n in side_notes if n not in notes]
    usable = [m for m in files if m.path and id(m) not in dropped]

    lock_files = [m.lock_dict() for m in files if m.source in ("curseforge", "modrinth")]
    LOCK.write_text(json.dumps({"minecraft": pack["minecraft"], "loader": pack["loader"],
                                "files": lock_files}, indent=2) + "\n", encoding="utf-8")

    server_files = [m for m in usable if m.side in ("both", "server")]
    client_files = [m for m in usable if m.side in ("both", "client")]
    if not args.no_server:
        print("Installing server mods")
        install_server(server_files)
    built = [] if args.no_client_pack else build_client_packs(pack, client_files)

    print()
    for path in built:
        print(f"Players' pack: {path.relative_to(ROOT)} ({path.stat().st_size // (1024 * 1024)} MB)")
    for n in notes:
        print(f"note: {n}")
    for p in problems + check_problems:
        print(f"PROBLEM: {p}")
    if problems or check_problems:
        print(f"\n{len(problems) + len(check_problems)} problem(s). Fix them and run the script again.")
        return 1
    print(f"All {len(server_files)} server mods and {len(client_files)} player mods are in place.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
