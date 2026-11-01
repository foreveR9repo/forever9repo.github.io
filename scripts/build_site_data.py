#!/usr/bin/env python3
"""Genere data/packages.json (et icons/) pour le site, a partir des releases GitHub.

- Les depots a suivre sont listes dans data/sources.json.
- Toutes les releases publiees (hors brouillons et pre-releases) sont lues : chaque
  .deb attache devient une version dans l'historique du paquet.
- Les infos du paquet (nom, auteur, section, depends...) sont lues dans le .deb lui-meme.
- Les liens de telechargement pointent directement vers les .deb sur GitHub.
- Les .deb deja analyses sont repris du precedent packages.json (pas de re-telechargement).
"""
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOURCES = os.path.join(ROOT, "data", "sources.json")
OUT = os.path.join(ROOT, "data", "packages.json")
ICON_DIR = os.path.join(ROOT, "icons")
TOKEN = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")


def warn(msg):
    print("::warning::" + msg, file=sys.stderr)


def http(url):
    headers = {"User-Agent": "repo-site-sync"}
    if TOKEN and urllib.parse.urlparse(url).hostname == "api.github.com":
        headers["Authorization"] = "Bearer " + TOKEN
        headers["Accept"] = "application/vnd.github+json"
    return urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=90)


def get_releases(repo):
    out, page = [], 1
    while True:
        with http("https://api.github.com/repos/%s/releases?per_page=100&page=%d" % (repo, page)) as r:
            batch = json.load(r)
        out += batch
        if len(batch) < 100:
            return out
        page += 1


def download(url, dest):
    with http(url) as r, open(dest, "wb") as f:
        while True:
            chunk = r.read(1 << 16)
            if not chunk:
                return
            f.write(chunk)


def read_deb(path):
    """Champs de controle du .deb (dpkg-deb -f), descriptions multi-lignes aplaties."""
    text = subprocess.run(["dpkg-deb", "-f", path], capture_output=True, text=True, check=True).stdout
    fields, key = {}, None
    for line in text.splitlines():
        if line[:1] in (" ", "\t") and key:
            extra = line.strip()
            if extra and extra != ".":
                fields[key] += " " + extra
        elif ":" in line:
            key, val = line.split(":", 1)
            key = key.strip()
            fields[key] = val.strip()
    return fields


def digest(path):
    md5, sha = hashlib.md5(), hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            md5.update(chunk)
            sha.update(chunk)
    return md5.hexdigest(), sha.hexdigest()


def to_int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def analyse_asset(release, asset):
    """Retourne (infos_paquet, version) pour un .deb de release."""
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "pkg.deb")
        download(asset["browser_download_url"], path)
        c = read_deb(path)
        md5, sha = digest(path)
        size = os.path.getsize(path)
    pkg = {
        "id": c["Package"],
        "name": c.get("Name") or c["Package"],
        "author": c.get("Author") or c.get("Maintainer", ""),
        "section": c.get("Section", ""),
        "description": c.get("Description", ""),
    }
    ver = {
        "version": c.get("Version") or release["tag_name"].lstrip("v"),
        "date": release.get("published_at") or asset.get("updated_at"),
        "deb": asset["browser_download_url"],
        "arch": c.get("Architecture", ""),
        "size": size,
        "installedSize": to_int(c.get("Installed-Size")),
        "depends": c.get("Depends", ""),
        "priority": c.get("Priority", ""),
        "md5": md5,
        "sha256": sha,
    }
    return pkg, {k: v for k, v in ver.items() if v not in (None, "")}


def fetch_icon(repo, tag, paths, assets, dest):
    """Essaie : fichier de release icon.png, puis les chemins du depot. True si dest ecrit."""
    candidates = []
    for a in assets:
        if a["name"].lower() == "icon.png":
            candidates.append(a["browser_download_url"])
    for p in paths:
        candidates.append("https://raw.githubusercontent.com/%s/%s/%s" % (repo, urllib.parse.quote(tag), urllib.parse.quote(p)))
    for url in candidates:
        try:
            tmp = dest + ".tmp"
            download(url, tmp)
            os.replace(tmp, dest)
            return True
        except (urllib.error.URLError, OSError):
            continue
    return False


def main():
    with open(SOURCES, encoding="utf-8") as f:
        sources = json.load(f)
    try:
        with open(OUT, encoding="utf-8") as f:
            previous = json.load(f)
    except (OSError, ValueError):
        previous = []

    # cache : url du .deb -> (infos paquet, version)
    cache = {}
    for p in previous:
        for v in p.get("versions", []):
            meta = v.get("_pkg")
            if v.get("deb") and meta:
                cache[v["deb"]] = (meta, v)

    packages = {}  # id -> paquet
    os.makedirs(ICON_DIR, exist_ok=True)

    for src in sources:
        repo = src["repo"]
        try:
            releases = get_releases(repo)
        except (urllib.error.URLError, OSError, ValueError) as e:
            warn("Releases illisibles pour %s (%s) : on garde les donnees precedentes" % (repo, e))
            for p in previous:
                if p.get("_src") == repo:
                    packages[p["id"]] = p
            continue

        releases = [r for r in releases if not r.get("draft") and not r.get("prerelease")]
        releases.sort(key=lambda r: r.get("published_at") or "", reverse=True)
        if not releases:
            warn("Aucune release publiee pour %s" % repo)
            continue

        found = []  # (pkg, version) de ce depot
        for rel in releases:
            for asset in rel.get("assets", []):
                if not asset["name"].lower().endswith(".deb"):
                    continue
                url = asset["browser_download_url"]
                try:
                    if url in cache:
                        pkg, ver = cache[url]
                        ver = dict(ver)
                    else:
                        pkg, ver = analyse_asset(rel, asset)
                except (urllib.error.URLError, OSError, subprocess.CalledProcessError, KeyError) as e:
                    warn("Impossible d'analyser %s (%s)" % (url, e))
                    continue
                ver["_pkg"] = pkg
                found.append((pkg, ver))

        if not found:
            warn("Aucun .deb attache aux releases de %s" % repo)
            continue

        for pkg, ver in found:
            entry = packages.setdefault(pkg["id"], {"_src": repo, "versions": []})
            entry["versions"].append(ver)

        latest_tag = releases[0]["tag_name"]
        icon_paths = [src["icon"]] if src.get("icon") else []
        icon_paths.append("icon.png")
        ids = {pkg["id"] for pkg, _ in found}
        for pid in ids:
            dest = os.path.join(ICON_DIR, pid + ".png")
            if not fetch_icon(repo, latest_tag, icon_paths, releases[0].get("assets", []), dest) and not os.path.exists(dest):
                warn("Pas d'icone pour %s (ajoute \"icon\" dans data/sources.json)" % pid)
                continue
            packages[pid]["icon"] = "icons/%s.png" % pid

    result = []
    for pid, entry in packages.items():
        vers = sorted(entry["versions"], key=lambda v: v.get("date") or "", reverse=True)
        newest_pkg = vers[0].get("_pkg", {})
        item = {
            "id": pid,
            "name": newest_pkg.get("name", pid),
            "author": newest_pkg.get("author", ""),
            "section": newest_pkg.get("section", ""),
            "description": newest_pkg.get("description", ""),
            "added": vers[-1].get("date"),
            "free": True,
            "_src": entry.get("_src"),
            "versions": vers,
        }
        if entry.get("icon"):
            item["icon"] = entry["icon"]
        result.append(item)

    result.sort(key=lambda p: p["versions"][0].get("date") or "", reverse=True)
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=1)
        f.write("\n")
    print("packages.json : %d paquet(s), %d version(s)" % (len(result), sum(len(p["versions"]) for p in result)))


if __name__ == "__main__":
    main()
