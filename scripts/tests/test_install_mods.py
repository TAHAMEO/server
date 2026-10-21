"""Tests scripts/install-mods.py against a local fake of the Modrinth / CurseForge APIs.

Run: python3 scripts/tests/test_install_mods.py
"""
import hashlib
import importlib.util
import io
import json
import shutil
import tempfile
import threading
import unittest
import urllib.parse
import zipfile
from contextlib import redirect_stdout
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "install-mods.py"


def load_module():
    spec = importlib.util.spec_from_file_location("install_mods", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    import sys
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def make_jar(mod_id, version, deps=(), nested=None):
    toml = f'modLoader="javafml"\nloaderVersion="[47,)"\nlicense="x"\n[[mods]]\nmodId="{mod_id}"\nversion="{version}"\n'
    for dep_id, spec, side in deps:
        toml += f'[[dependencies.{mod_id}]]\nmodId="{dep_id}"\nmandatory=true\nversionRange="{spec}"\nside="{side}"\n'
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("META-INF/mods.toml", toml)
        if nested:
            z.writestr("META-INF/jarjar/metadata.json", json.dumps({"jars": [{"path": "META-INF/jarjar/inner.jar"}]}))
            z.writestr("META-INF/jarjar/inner.jar", nested)
    return buf.getvalue()


FILES = {
    "gunmod-2.0.jar": make_jar("gunmod", "2.0", [("geckolib", "[4.4,)", "BOTH"), ("forge", "[47,)", "BOTH")]),
    "gunmod-2.1-beta.jar": make_jar("gunmod", "2.1-beta"),
    "geckolib-4.8.4.jar": make_jar("geckolib", "4.8.4"),
    "geckolib-cf-4.7.0.jar": make_jar("geckolib", "4.7.0"),
    "warfare-0.8.9.jar": make_jar("superbwarfare", "0.8.9", [("curios", "[5.4.2,)", "BOTH")]),
    "warfare-neo.jar": make_jar("superbwarfare", "0.9.0"),
    "curios-5.14.1.jar": make_jar("curios", "5.14.1"),
    "addon-1.0.jar": make_jar("addon", "1.0", [("superbwarfare", "[0.9,)", "BOTH"), ("missingmod", "[1,)", "BOTH"),
                                               ("clientonlylib", "[1,)", "CLIENT")]),
    "Keerdm (TACZ) 1.42.jar": make_jar("keerdm", "1.42"),
    "Keerdm (Vics) 1.41.jar": make_jar("keerdm", "1.41"),
    "kff-4.11.jar": make_jar("kotlinforforge", "4.11", nested=make_jar("kfflang", "4.11")),
    "visuals-1.0.jar": make_jar("enhancedvisuals", "1.0"),
}


def sha(name, algo):
    return hashlib.new(algo, FILES[name]).hexdigest()


class Fake(BaseHTTPRequestHandler):
    base = ""
    hits = []

    def log_message(self, *a):
        pass

    def send(self, code, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        url = urllib.parse.urlsplit(self.path)
        q = dict(urllib.parse.parse_qsl(url.query))
        path = urllib.parse.unquote(url.path)
        Fake.hits.append(path)
        b = Fake.base
        if path.startswith("/files/"):
            name = path[len("/files/"):]
            return self.send(200, FILES[name], "application/java-archive") if name in FILES else self.send(404, {})
        # ---- Modrinth
        mr_projects = {"gunmod": "GUNID", "GUNID": "GUNID", "GECKOID": "GECKOID", "geckolib": "GECKOID",
                       "visuals": "VISID", "VISID": "VISID", "warfare-mr": "WARMR", "WARMR": "WARMR"}
        if path.startswith("/mr/project/"):
            rest = path[len("/mr/project/"):].split("/")
            pid = mr_projects.get(rest[0])
            if not pid:
                return self.send(404, {})
            if len(rest) == 1:
                return self.send(200, {"id": pid, "slug": rest[0].lower(), "title": pid.title()})
            assert json.loads(q["loaders"]) == ["forge"] and json.loads(q["game_versions"]) == ["1.20.1"], q

            def ver(vid, name, vtype, date, deps=()):
                return {"id": vid, "version_number": name.split("-")[1].removesuffix(".jar"), "version_type": vtype,
                        "date_published": date, "dependencies": [{"project_id": d, "dependency_type": "required"} for d in deps],
                        "files": [{"filename": name, "url": f"{b}/files/{name}", "primary": True,
                                   "hashes": {"sha1": sha(name, "sha1"), "sha512": sha(name, "sha512")}}]}
            versions = {
                "GUNID": [ver("V2B", "gunmod-2.1-beta.jar", "beta", "2026-09-01"),
                          ver("V2", "gunmod-2.0.jar", "release", "2026-08-01", ["GECKOID"])],
                "GECKOID": [ver("G1", "geckolib-4.8.4.jar", "release", "2026-07-01")],
                "VISID": [ver("VI", "visuals-1.0.jar", "release", "2026-01-01")],
                "WARMR": [ver("WM", "warfare-0.8.9.jar", "release", "2026-01-01")],
            }[pid]
            return self.send(200, versions)
        # ---- CurseForge API (needs the key)
        if path.startswith("/cf/"):
            if self.headers.get("x-api-key") != "testkey":
                return self.send(403, {})
            if path == "/cf/v1/mods/search":
                ids = {"warfare": 100, "keerdm": 300, "addon": 400, "geckolib-cf": 500, "kff": 600}
                slug = q["slug"]
                return self.send(200, {"data": [{"id": ids[slug], "slug": slug, "name": slug.title()}] if slug in ids else []})
            parts = path.split("/")  # /cf/v1/mods/<id>[/files]
            mid = int(parts[4])
            if len(parts) == 5:
                return self.send(200, {"data": {"id": mid, "slug": f"p{mid}", "name": f"Project {mid}"}})
            assert q["gameVersion"] == "1.20.1" and q["modLoaderType"] == "1", q

            def f(fid, name, date, versions=("1.20.1", "Forge"), release=1, deps=(), url=True):
                return {"id": fid, "fileName": name, "displayName": name, "fileDate": date, "releaseType": release,
                        "gameVersions": list(versions), "downloadUrl": f"{b}/files/{name}" if url else None,
                        "hashes": [{"value": sha(name, "sha1"), "algo": 1}],
                        "dependencies": [{"modId": d, "relationType": 3} for d in deps] + [{"modId": 999, "relationType": 2}]}
            files = {
                100: [f(1003, "warfare-neo.jar", "2026-10-01", ("1.20.1", "NeoForge")),
                      f(1002, "warfare-0.8.9.jar", "2026-09-01", deps=[200])],
                200: [f(2001, "curios-5.14.1.jar", "2026-08-01", url=False)],
                300: [f(3002, "Keerdm (Vics) 1.41.jar", "2026-06-01"), f(3001, "Keerdm (TACZ) 1.42.jar", "2026-05-01")],
                400: [f(4001, "addon-1.0.jar", "2026-04-01")],
                500: [f(5001, "geckolib-cf-4.7.0.jar", "2026-03-01")],
                600: [f(6001, "kff-4.11.jar", "2026-03-01")],
            }[mid]
            return self.send(200, {"data": files})
        # ---- CurseForge website: blocked like Cloudflare would
        if path.startswith("/web/"):
            return self.send(403, b"<html>Just a moment...</html>", "text/html")
        self.send(404, {})


MANIFEST = {
    "pack": {"name": "Test Pack", "version": "1.0.0", "minecraft": "1.20.1", "loader": "forge", "forge": "47.4.27"},
    "mods": [
        {"name": "Gun Mod", "modrinth": "gunmod", "side": "both"},
        {"name": "Warfare", "curseforge": "warfare", "modrinth": "warfare-mr", "side": "both"},
        {"name": "Keerdm", "curseforge": "keerdm", "side": "both", "match": "TACZ"},
        {"name": "Addon", "curseforge": "addon", "side": "both"},
        {"name": "GeckoLib (CF)", "curseforge": "geckolib-cf", "side": "both"},
        {"name": "Kotlin for Forge", "curseforge": "kff", "side": "both"},
        {"name": "Visuals", "modrinth": "visuals", "side": "client"},
        {"name": "Disabled", "curseforge": "nope", "side": "both", "enabled": False},
    ],
    "local": [{"name": "Server Auth", "file": "local/auth.jar", "side": "server"}],
}


class InstallModsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Fake)
        Fake.base = f"http://127.0.0.1:{cls.httpd.server_port}"
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        (self.tmp / "modpack" / "local").mkdir(parents=True)
        (self.tmp / "server").mkdir()
        (self.tmp / "modpack" / "mods.json").write_text(json.dumps(MANIFEST))
        (self.tmp / "modpack" / "local" / "auth.jar").write_bytes(make_jar("serverauth", "1.0.0"))
        self.m = load_module()
        m = self.m
        m.ROOT = self.tmp
        m.MODPACK = self.tmp / "modpack"
        m.MANIFEST = m.MODPACK / "mods.json"
        m.LOCK = m.MODPACK / "mods.lock.json"
        m.CACHE = m.MODPACK / "cache"
        m.MANUAL = m.MODPACK / "manual"
        m.MANUAL_CLIENT = m.MODPACK / "manual-client"
        m.DIST = self.tmp / "dist"
        m.SERVER = self.tmp / "server"
        m.SERVER_MODS = m.SERVER / "mods"
        m.INSTALLED = m.SERVER / ".mods-installed.json"
        m.MR_API = Fake.base + "/mr"
        m.CF_API = Fake.base + "/cf"
        m.CF_WEB = Fake.base + "/web"
        m.time.sleep = lambda s: None

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def run_script(self, *args, key="testkey"):
        import os
        import sys
        os.environ.pop("CF_API_KEY", None)
        if key:
            os.environ["CF_API_KEY"] = key
        sys.argv = ["install-mods.py", *args]
        out = io.StringIO()
        with redirect_stdout(out):
            code = self.m.main()
        return code, out.getvalue()

    def test_version_compare(self):
        m = self.m
        self.assertLess(m.compare_versions("1.0-beta", "1.0"), 0)
        self.assertLess(m.compare_versions("1.0", "1.0.1"), 0)
        self.assertEqual(m.compare_versions("1.0", "1.0.0"), 0)
        self.assertGreater(m.compare_versions("4.8.4", "4.4.6"), 0)
        self.assertGreater(m.compare_versions("1.1.8-hotfix2", "1.1.8"), 0)
        self.assertTrue(m.in_range("4.8.4", "[4.4.6,)"))
        self.assertFalse(m.in_range("4.4.5", "[4.4.6,)"))
        self.assertTrue(m.in_range("1.20.1", "[1.20.1,1.21)"))
        self.assertFalse(m.in_range("1.21", "[1.20.1,1.21)"))
        self.assertTrue(m.in_range("47.4.27", "[47,)"))
        self.assertTrue(m.in_range("2.0", "[1.0,2.0]"))
        self.assertFalse(m.in_range("2.0", "[1.0,2.0)"))
        self.assertTrue(m.in_range("1.5", "1.0"))
        self.assertTrue(m.in_range("3.1", "[1.0,2.0),[3.0,)"))
        self.assertTrue(m.in_range("1.0", "[1.0]"))
        self.assertFalse(m.in_range("1.1", "[1.0]"))
        self.assertTrue(m.in_range("anything", "*"))

    def test_safe_name(self):
        for bad in ("../evil.jar", "a/b.jar", ".hidden.jar", "", "x\\y.jar"):
            with self.assertRaises(self.m.ResolveError):
                self.m.safe_name(bad)
        self.assertEqual(self.m.safe_name("Keerdm (TACZ) 1.42.jar"), "Keerdm (TACZ) 1.42.jar")

    def test_full_run_with_api_key(self):
        code, out = self.run_script()
        lock = json.loads((self.tmp / "modpack" / "mods.lock.json").read_text())
        names = {f["file_name"]: f for f in lock["files"]}
        # newest release wins over a newer beta; Modrinth dependency pulled in
        self.assertIn("gunmod-2.0.jar", names)
        self.assertNotIn("gunmod-2.1-beta.jar", names)
        self.assertEqual(names["geckolib-4.8.4.jar"]["required_by"], ["Gun Mod"])
        # NeoForge-only file skipped; CurseForge dependency (curios) pulled in; optional deps ignored
        self.assertIn("warfare-0.8.9.jar", names)
        self.assertNotIn("warfare-neo.jar", names)
        self.assertEqual(names["curios-5.14.1.jar"]["required_by"], ["Warfare"])
        self.assertFalse(any("999" == f["project"] for f in lock["files"]))
        # "match" picks the TaCZ variant even though the Vics one is newer
        self.assertIn("Keerdm (TACZ) 1.42.jar", names)
        self.assertNotIn("Keerdm (Vics) 1.41.jar", names)
        # disabled entries are skipped
        self.assertFalse(any("nope" in f["name"].lower() for f in lock["files"]))
        # curios has no download URL (author disabled third-party downloads) -> manual step reported
        self.assertRegex(out, r"PROBLEM: .*curios-5\.14\.1\.jar by hand .*/files/2001")
        # duplicate geckolib (Modrinth + CurseForge) -> newest kept, listed entry preferred
        self.assertRegex(out, r"geckolib came twice")
        # version range problem and missing dependency are reported; CLIENT-only deps ignored on the server
        self.assertRegex(out, r"addon \(addon-1\.0\.jar\) needs superbwarfare \[0\.9,\), but the pack has 0\.8\.9")
        self.assertRegex(out, r"needs the mod 'missingmod'")
        self.assertNotRegex(out, r"\[server\].*clientonlylib")
        self.assertRegex(out, r"\[client\].*clientonlylib")
        # Jar-in-Jar mods count as provided and are not duplicates
        self.assertNotRegex(out, r"kfflang came twice")
        self.assertEqual(code, 1)

        mods = sorted(p.name for p in (self.tmp / "server" / "mods").iterdir())
        self.assertIn("auth.jar", mods)  # server-only local mod
        self.assertNotIn("visuals-1.0.jar", mods)  # client-only
        self.assertNotIn("curios-5.14.1.jar", mods)  # not downloaded yet
        cached = self.tmp / "modpack" / "cache" / "modrinth" / "GUNID" / "gunmod-2.0.jar"
        self.assertTrue((self.tmp / "server" / "mods" / "gunmod-2.0.jar").samefile(cached))  # hard link, no copy

        with zipfile.ZipFile(self.tmp / "dist" / "test-pack-client.zip") as z:
            entries = set(z.namelist())
        self.assertIn("mods/visuals-1.0.jar", entries)
        self.assertNotIn("mods/auth.jar", entries)  # players never get the server-only login mod
        self.assertIn("README.txt", entries)
        with zipfile.ZipFile(self.tmp / "dist" / "test-pack-curseforge.zip") as z:
            manifest = json.loads(z.read("manifest.json"))
            cf_entries = set(z.namelist())
        self.assertEqual(manifest["minecraft"]["modLoaders"][0]["id"], "forge-47.4.27")
        self.assertIn({"projectID": 100, "fileID": 1002, "required": True}, manifest["files"])
        self.assertIn("overrides/mods/gunmod-2.0.jar", cf_entries)  # non-CurseForge files ride along

        # Second run: the hand-downloaded curios jar is picked up and checked against its sha1;
        # nothing is resolved again (lock file), and nothing is downloaded again (cache).
        manual = self.tmp / "modpack" / "manual"
        manual.mkdir()
        (manual / "curios-5.14.1.jar").write_bytes(FILES["curios-5.14.1.jar"])
        Fake.hits.clear()
        code, out = self.run_script()
        self.assertFalse([h for h in Fake.hits if h.startswith(("/mr/", "/cf/", "/files/"))], Fake.hits)
        self.assertNotIn("by hand", out)
        self.assertIn("curios-5.14.1.jar", {p.name for p in (self.tmp / "server" / "mods").iterdir()})

        # A tampered manual file is refused.
        (manual / "curios-5.14.1.jar").write_bytes(b"PK\x05\x06" + b"\0" * 18)
        code, out = self.run_script()
        self.assertRegex(out, r"modpack/manual/curios-5\.14\.1\.jar does not match")

    def test_without_key_falls_back_to_modrinth(self):
        code, out = self.run_script(key=None)
        self.assertIn("CurseForge via website", out)
        lock = json.loads((self.tmp / "modpack" / "mods.lock.json").read_text())
        sources = {f["entry"]: f["source"] for f in lock["files"]}
        self.assertEqual(sources["Warfare"], "modrinth")  # CurseForge blocked -> Modrinth copy
        self.assertRegex(out, r"Keerdm: could not find it .*modpack/manual/")
        self.assertEqual(code, 1)

    def test_offline_needs_lock(self):
        code, out = self.run_script("--offline", key=None)
        self.assertRegex(out, r"Gun Mod: not in mods.lock.json and --offline was given")
        self.assertEqual(code, 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
