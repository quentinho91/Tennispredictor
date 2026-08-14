"""
Télécharge les fichiers de matchs disponibles via l'API de TennisMyLife et
les range dans des sous-dossiers par catégorie, sans rien supprimer :

    data/raw/atp/       -> tour ATP + qualifs + challengers + futures
    data/raw/wta/        -> tour WTA (pas utilisé par le pipeline actuel,
                             mais dispo si tu veux entraîner un modèle WTA plus tard)
    data/raw/doubles/    -> matchs de double (schéma de colonnes différent,
                             pas géré par ce pipeline, juste archivé)

Le pipeline (01_build_dataset.py etc.) ne lit QUE data/raw/atp/. Le filtre
"avant 2000" n'est PAS appliqué ici au téléchargement (on garde tout
l'historique dispo, ça ne coûte presque rien en espace disque) mais dans
01_build_dataset.py via MIN_YEAR, pour pouvoir changer d'avis plus tard
sans tout retélécharger.

MISE A JOUR INCREMENTALE : relancer ce script plus tard ne retélécharge
que les fichiers dont la taille a changé sur le serveur (typiquement,
juste l'année en cours qui se met à jour au fil des tournois). Utilise
--force pour tout retélécharger quoi qu'il arrive.

FUSION DES FICHIERS "ONGOING" (tournois en cours) : contrairement aux
archives annuelles, ongoing_tourneys.csv / challenger_ongoing_tourneys.csv
/ wta_ongoing_tourneys.csv ne contiennent QUE le(s) tournoi(s) en direct
au moment du téléchargement -- pas une fenêtre glissante. Dès qu'un
tournoi se termine et est remplacé par le suivant dans l'API, ses matchs
disparaissent de ce fichier. S'il n'a pas encore été absorbé dans
l'archive annuelle à ce moment-là (l'archive se met à jour par lots, pas
en continu), ses résultats sont perdus pour de bon entre deux
téléchargements. On fusionne donc ces fichiers avec l'historique déjà
accumulé localement au lieu de les écraser, avec déduplication sur la clé
naturelle d'un match (le nettoyage définitif des vrais doublons -- une
fois le tournoi absorbé dans l'archive -- se fait déjà dans
01_build_dataset.py).

Usage :
    python 00_download_data.py            # mise à jour incrémentale
    python 00_download_data.py --force     # retélécharge tout
"""

import json
import re
import sys
import urllib.request
from pathlib import Path

import pandas as pd

BASE_DIR = Path(__file__).resolve().parent.parent
RAW_DIR = BASE_DIR / "data" / "raw"

API_URL = "https://stats.tennismylife.org/api/data-files"
HEADERS = {"User-Agent": "Mozilla/5.0 (tennis-predictor script)"}

MERGE_KEY = ["tourney_id", "winner_name", "loser_name", "tourney_date", "round"]

CATEGORY_DIRS = {
    "wta": RAW_DIR / "wta",
    "double": RAW_DIR / "doubles",
    "atp": RAW_DIR / "atp",  # catégorie par défaut (tour, qualifs, challengers, futures ATP)
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
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read().decode("utf-8"))["files"]


def remote_size(url):
    """HEAD request pour comparer la taille distante à la taille locale,
    sans télécharger si rien n'a changé (mise à jour incrémentale)."""
    req = urllib.request.Request(url, headers=HEADERS, method="HEAD")
    try:
        with urllib.request.urlopen(req) as resp:
            length = resp.headers.get("Content-Length")
            return int(length) if length is not None else None
    except Exception:
        return None  # si le HEAD échoue, on retéléchargera par sécurité


def download(url, dest):
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req) as resp, open(dest, "wb") as out:
        out.write(resp.read())


def download_and_merge(url, dest):
    """Télécharge le contenu frais dans un fichier temporaire, puis le
    fusionne avec ce qui est déjà accumulé localement (voir docstring en
    tête de fichier). Retourne (n_lignes_total, n_doublons_retires)."""
    tmp = dest.with_suffix(".tmp.csv")
    download(url, tmp)
    fresh = pd.read_csv(tmp, low_memory=False)
    tmp.unlink()

    if dest.exists():
        try:
            old = pd.read_csv(dest, low_memory=False)
            combined = pd.concat([old, fresh], ignore_index=True)
        except Exception:
            combined = fresh  # fichier local corrompu/vide -> on repart du frais
    else:
        combined = fresh

    key_cols = [c for c in MERGE_KEY if c in combined.columns]
    n_before = len(combined)
    if key_cols:
        combined = combined.drop_duplicates(subset=key_cols, keep="last")
    n_removed = n_before - len(combined)

    combined.to_csv(dest, index=False)
    return len(combined), n_removed


if __name__ == "__main__":
    force = "--force" in sys.argv

    files = fetch_file_list()
    print(f"{len(files)} fichiers listés par l'API.\n")

    counts = {"downloaded": 0, "updated": 0, "merged": 0, "unchanged": 0, "failed": 0}

    for f in files:
        name, url = f["name"], f["url"]
        category = categorize(name)
        dest = CATEGORY_DIRS[category] / name
        dest.parent.mkdir(parents=True, exist_ok=True)

        # Fichiers "ongoing" : toujours re-vérifiés et FUSIONNÉS (jamais
        # écrasés), voir docstring en tête de fichier. Ce sont de petits
        # fichiers, le coût de toujours les re-télécharger est négligeable.
        if is_ongoing_file(name):
            try:
                n_total, n_dupes = download_and_merge(url, dest)
                print(f"MERGED     [{category}] {name} ({n_total} lignes accumulées, {n_dupes} doublons retirés)")
                counts["merged"] += 1
            except Exception as e:
                print(f"ECHEC [{category}] {name}: {e}")
                counts["failed"] += 1
            continue

        needs_download = force or not dest.exists()

        if not needs_download:
            r_size = remote_size(url)
            l_size = dest.stat().st_size
            if r_size is not None and r_size != l_size:
                needs_download = True
                status = "updated"
            else:
                counts["unchanged"] += 1
                continue
        else:
            status = "downloaded" if not dest.exists() else "updated"

        try:
            download(url, dest)
            size_kb = dest.stat().st_size / 1024
            print(f"{status.upper():10s} [{category}] {name} ({size_kb:.0f} Ko)")
            counts[status] += 1
        except Exception as e:
            print(f"ECHEC [{category}] {name}: {e}")
            counts["failed"] += 1

    print(f"\n{counts['downloaded']} nouveaux, {counts['updated']} mis à jour, "
          f"{counts['merged']} fusionnés (ongoing), {counts['unchanged']} inchangés, "
          f"{counts['failed']} échecs.")
    for cat, d in CATEGORY_DIRS.items():
        n = len(list(d.glob("*.csv")))
        print(f"  {cat}: {n} fichiers dans {d}")
