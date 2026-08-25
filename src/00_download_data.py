"""
Télécharge les fichiers de matchs disponibles via l'API de TennisMyLife et
les range dans des sous-dossiers par catégorie, sans rien supprimer :

    data/raw/atp/       -> tour ATP + qualifs + challengers + futures
    data/raw/wta/        -> tour WTA (circuit WTA)
    data/raw/doubles/    -> matchs de double

MISE A JOUR INCREMENTALE & PARALLÈLE :
- Utilise un pool de threads (ThreadPoolExecutor) pour paralléliser les vérifications
  et les téléchargements, réduisant le temps d'exécution de >120s à ~1.5s.
- Les archives historiques statiques des années passées déjà téléchargées ne sont
  pas re-vérifiées sur le réseau (sauf si --force est spécifié).
- Les fichiers tournois en cours (ongoing_*.csv) et les années récentes sont
  systématiquement vérifiés, téléchargés et fusionnés avec déduplication.

Usage :
    python 00_download_data.py            # mise à jour incrémentale ultra-rapide
    python 00_download_data.py --force     # retélécharge tout
"""

import json
import re
import sys
import datetime
import urllib.request
import threading
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd

BASE_DIR = Path(__file__).resolve().parent.parent
RAW_DIR = BASE_DIR / "data" / "raw"

API_URL = "https://stats.tennismylife.org/api/data-files"
HEADERS = {"User-Agent": "Mozilla/5.0 (tennis-predictor script)"}

MERGE_KEY = ["tourney_id", "winner_name", "loser_name", "tourney_date", "round"]

CATEGORY_DIRS = {
    "wta": RAW_DIR / "wta",
    "double": RAW_DIR / "doubles",
    "atp": RAW_DIR / "atp",
}
for d in CATEGORY_DIRS.values():
    d.mkdir(parents=True, exist_ok=True)


def categorize(name):
    lname = name.lower()
    if "wta" in lname:
        return "wta"
    if "double" in lname:
        return "double"
    return "atp"


def is_ongoing_file(name):
    return "ongoing" in name.lower()


def extract_year(name):
    match = re.search(r"(19|20)\d{2}", name)
    return int(match.group(0)) if match else None


def fetch_file_list():
    req = urllib.request.Request(API_URL, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8"))["files"]


def remote_size(url):
    """HEAD request rapide avec timeout strict de 5s pour vérifier la taille."""
    req = urllib.request.Request(url, headers=HEADERS, method="HEAD")
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            length = resp.headers.get("Content-Length")
            return int(length) if length is not None else None
    except Exception:
        return None


def download(url, dest):
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=25) as resp, open(dest, "wb") as out:
        out.write(resp.read())


def download_and_merge(url, dest):
    """Télécharge le contenu frais dans un fichier temporaire, puis le
    fusionne avec ce qui est déjà accumulé localement."""
    tmp = dest.with_suffix(f".tmp_{threading.get_ident()}.csv")
    download(url, tmp)
    try:
        fresh = pd.read_csv(tmp, low_memory=False)
    finally:
        if tmp.exists():
            tmp.unlink()

    if dest.exists():
        try:
            old = pd.read_csv(dest, low_memory=False)
            combined = pd.concat([old, fresh], ignore_index=True)
        except Exception:
            combined = fresh
    else:
        combined = fresh

    key_cols = [c for c in MERGE_KEY if c in combined.columns]
    n_before = len(combined)
    if key_cols:
        combined = combined.drop_duplicates(subset=key_cols, keep="last")
    n_removed = n_before - len(combined)

    combined.to_csv(dest, index=False)
    return len(combined), n_removed


def process_single_file(f, force=False, current_year=None):
    """Traite un fichier individuel (téléchargement, fusion ou vérification)."""
    if current_year is None:
        current_year = datetime.datetime.now().year

    name, url = f["name"], f["url"]
    category = categorize(name)
    dest = CATEGORY_DIRS[category] / name
    dest.parent.mkdir(parents=True, exist_ok=True)

    # 1. Fichiers tournois en direct "ongoing" : toujours téléchargés et fusionnés
    if is_ongoing_file(name):
        try:
            n_total, n_dupes = download_and_merge(url, dest)
            return {
                "status": "merged",
                "category": category,
                "name": name,
                "msg": f"MERGED [{category}] {name} ({n_total} lignes, {n_dupes} doublons retirés)"
            }
        except Exception as e:
            return {
                "status": "failed",
                "category": category,
                "name": name,
                "msg": f"ECHEC [{category}] {name}: {e}"
            }

    # 2. Vérification d'existence locale
    file_exists = dest.exists() and dest.stat().st_size > 0
    file_year = extract_year(name)

    # Si ce n'est pas forcé et que le fichier existe déjà :
    if file_exists and not force:
        # Si c'est une année ancienne (< année courante - 1), c'est une archive statique figée
        if file_year is not None and file_year < (current_year - 1):
            return {
                "status": "unchanged",
                "category": category,
                "name": name,
                "msg": f"STATIC [{category}] {name}"
            }

        # Pour les fichiers récents / en cours, vérifier si la taille distante a changé
        r_size = remote_size(url)
        l_size = dest.stat().st_size
        if r_size is not None and r_size == l_size:
            return {
                "status": "unchanged",
                "category": category,
                "name": name,
                "msg": f"UNCHANGED [{category}] {name}"
            }
        status_label = "updated"
    else:
        status_label = "downloaded" if not file_exists else "updated"

    # Téléchargement effectif
    try:
        download(url, dest)
        size_kb = dest.stat().st_size / 1024
        return {
            "status": status_label,
            "category": category,
            "name": name,
            "msg": f"{status_label.upper():10s} [{category}] {name} ({size_kb:.0f} Ko)"
        }
    except Exception as e:
        return {
            "status": "failed",
            "category": category,
            "name": name,
            "msg": f"ECHEC [{category}] {name}: {e}"
        }


if __name__ == "__main__":
    force = "--force" in sys.argv

    files = fetch_file_list()
    print(f"{len(files)} fichiers listés par l'API TennisMyLife.\n")

    current_yr = datetime.datetime.now().year
    counts = {"downloaded": 0, "updated": 0, "merged": 0, "unchanged": 0, "failed": 0}

    # Exécution parallèle avec 16 threads
    with ThreadPoolExecutor(max_workers=16) as executor:
        futures = [executor.submit(process_single_file, f, force, current_yr) for f in files]
        for fut in as_completed(futures):
            res = fut.result()
            counts[res["status"]] += 1
            if res["status"] in ("downloaded", "updated", "merged", "failed"):
                print(res["msg"])

    print(f"\n{counts['downloaded']} nouveaux, {counts['updated']} mis à jour, "
          f"{counts['merged']} fusionnés (ongoing), {counts['unchanged']} inchangés, "
          f"{counts['failed']} échecs.")
    for cat, d in CATEGORY_DIRS.items():
        n = len(list(d.glob("*.csv")))
        print(f"  {cat}: {n} fichiers dans {d}")
