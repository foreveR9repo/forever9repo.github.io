#!/usr/bin/env python3
"""Ajoute a Packages l'icone + la depiction Sileo (avec banniere) de chaque paquet.

- Lit le fichier Packages (genere par dpkg-scanpackages) a la racine du depot.
- Cree depictions/<id>.json (depiction native Sileo/Zebra) avec la banniere assets/banner.png.
- Ajoute dans Packages : Icon (assets/logogs17.png) et SileoDepiction.
- Doit etre lance AVANT la creation de Packages.gz / Packages.bz2 / Release.
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SITE = os.environ.get("SITE_URL", "").rstrip("/")
ICON_FILE = "assets/logogs17.png"
BANNER_FILE = "assets/banner.png"
TINT = "#00c4a0"
DROP = {"icon", "depiction", "sileodepiction", "header"}  # champs remplaces par les notres


def warn(msg):
    print("::warning::" + msg, file=sys.stderr)


def parse_stanza(block):
    """Retourne (liste de lignes, dict champ -> valeur complete)."""
    lines = block.split("\n")
    fields, key = {}, None
    for line in lines:
        if line[:1] in (" ", "\t") and key:
            fields[key] += "\n" + line
        elif ":" in line:
            key, val = line.split(":", 1)
            key = key.strip()
            fields[key] = val.strip()
    return lines, fields


def drop_fields(lines):
    out, skipping = [], False
    for line in lines:
        if line[:1] in (" ", "\t"):
            if not skipping:
                out.append(line)
            continue
        key = line.split(":", 1)[0].strip().lower()
        skipping = key in DROP
        if not skipping:
            out.append(line)
    return out


def description_markdown(desc):
    parts = desc.split("\n")
    synopsis = parts[0].strip()
    rest = []
    for line in parts[1:]:
        t = line.strip()
        rest.append("" if t == "." else t)
    text = "\n".join(rest).strip()
    md = "**%s**" % synopsis if synopsis else ""
    if text:
        md += "\n\n" + text
    return md


def depiction(fields, has_banner):
    rows = [
        ("Version", fields.get("Version")),
        ("Author", fields.get("Author") or fields.get("Maintainer")),
        ("Section", fields.get("Section")),
        ("Requires", fields.get("Depends")),
        ("Architecture", fields.get("Architecture")),
    ]
    views = [{"class": "DepictionMarkdownView", "markdown": description_markdown(fields.get("Description", "")), "useSpacing": True},
             {"class": "DepictionSeparatorView"}]
    for title, text in rows:
        if text:
            views.append({"class": "DepictionTableTextView", "title": title, "text": text})
    d = {
        "minVersion": "0.1",
        "class": "DepictionTabView",
        "tintColor": TINT,
        "tabs": [{"tabname": "Details", "class": "DepictionStackView", "views": views}],
    }
    if has_banner:
        d["headerImage"] = SITE + "/" + BANNER_FILE
    return d


def main():
    if not SITE:
        warn("SITE_URL non defini : rien a faire")
        return
    packages_path = os.path.join(ROOT, "Packages")
    with open(packages_path, encoding="utf-8") as f:
        text = f.read()

    has_icon = os.path.exists(os.path.join(ROOT, ICON_FILE))
    has_banner = os.path.exists(os.path.join(ROOT, BANNER_FILE))
    if not has_icon:
        warn("%s introuvable : pas d'icone dans Sileo" % ICON_FILE)
    if not has_banner:
        warn("%s introuvable : pas de banniere dans Sileo" % BANNER_FILE)

    os.makedirs(os.path.join(ROOT, "depictions"), exist_ok=True)
    out_blocks = []
    for block in text.strip("\n").split("\n\n"):
        if not block.strip():
            continue
        lines, fields = parse_stanza(block)
        pid = fields.get("Package")
        if not pid:
            out_blocks.append(block)
            continue
        with open(os.path.join(ROOT, "depictions", pid + ".json"), "w", encoding="utf-8") as f:
            json.dump(depiction(fields, has_banner), f, ensure_ascii=False, indent=1)
            f.write("\n")
        lines = drop_fields(lines)
        while lines and not lines[-1].strip():
            lines.pop()
        if has_icon:
            lines.append("Icon: %s/%s" % (SITE, ICON_FILE))
        lines.append("SileoDepiction: %s/depictions/%s.json" % (SITE, pid))
        out_blocks.append("\n".join(lines))

    with open(packages_path, "w", encoding="utf-8") as f:
        f.write("\n\n".join(out_blocks) + "\n\n")
    print("Depictions : %d paquet(s)" % len(out_blocks))


if __name__ == "__main__":
    main()
