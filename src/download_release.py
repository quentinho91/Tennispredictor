"""
Script de synchronisation automatique des modèles et états depuis les GitHub Releases.
Télécharge les 13 fichiers précalculés par le pipeline GitHub Actions (Daily Model Update).
Permet à Render et aux environnements de déploiement de toujours disposer des modèles
les plus récents sans dépendre des commits git ni consommer la RAM de build/training.
"""

import os
import sys
import json
import time
import urllib.request
import urllib.error
from pathlib import Path
from typing import List, Optional

RELEASE_TAG = "latest_model"
REPO = "quentinho91/Tennispredictor"
BASE_URL = f"https://github.com/{REPO}/releases/download/{RELEASE_TAG}"
API_URL = f"https://api.github.com/repos/{REPO}/releases/tags/{RELEASE_TAG}"

DEFAULT_ASSETS = [
    "player_state_atp.pkl",
    "xgb_model_atp.json",
    "ensemble_atp.pkl",
    "calibrator_atp.pkl",
    "feature_cols_atp.pkl",
    "player_state_wta.pkl",
    "xgb_model_wta.json",
    "ensemble_wta.pkl",
    "calibrator_wta.pkl",
    "feature_cols_wta.pkl",
    "players_atp.json",
    "players_wta.json",
    "tournaments.json",
]


def download_release_assets(target_dir: Optional[str] = None, force: bool = False, timeout: int = 30) -> bool:
    """
    Télécharge tous les assets de la release 'latest_model' vers target_dir (par défaut data/processed).
    Vérifie les tailles de fichiers pour éviter les téléchargements inutiles.
    Retourne True si tous les assets requis sont présents et valides.
    """
    if target_dir is None:
        base_dir = Path(__file__).resolve().parent.parent
        target_path = base_dir / "data" / "processed"
    else:
        target_path = Path(target_dir)

    target_path.mkdir(parents=True, exist_ok=True)

    assets_to_dl = []
    # 1. Tenter d'obtenir la liste et les tailles réelles via l'API GitHub
    try:
        req = urllib.request.Request(API_URL, headers={"User-Agent": "TennisPredictor/1.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
            for a in data.get("assets", []):
                assets_to_dl.append({
                    "name": a["name"],
                    "url": a["browser_download_url"],
                    "size": a.get("size", 0)
                })
        print(f"[RELEASE] {len(assets_to_dl)} assets listés depuis l'API GitHub.")
    except Exception as e:
        print(f"[RELEASE] Note: API GitHub inaccessible ({e}), utilisation de la liste statique d'assets.")
        assets_to_dl = [
            {
                "name": name,
                "url": f"{BASE_URL}/{name}",
                "size": 0
            }
            for name in DEFAULT_ASSETS
        ]

    success_count = 0
    t0 = time.time()

    for item in assets_to_dl:
        name = item["name"]
        url = item["url"]
        expected_size = item["size"]
        dest = target_path / name

        # Si le fichier existe déjà localement et qu'on ne force pas
        if not force and dest.exists() and dest.stat().st_size > 0:
            if expected_size > 0 and dest.stat().st_size == expected_size:
                print(f"  [OK-CACHE] {name} ({dest.stat().st_size / 1048576:.2f} Mo)")
                success_count += 1
                continue
            elif expected_size == 0:
                # Vérifier la taille distante par HTTP HEAD
                try:
                    head_req = urllib.request.Request(url, headers={"User-Agent": "TennisPredictor/1.0"}, method="HEAD")
                    with urllib.request.urlopen(head_req, timeout=10) as head_resp:
                        remote_size = int(head_resp.headers.get("Content-Length", 0))
                        if remote_size > 0 and dest.stat().st_size == remote_size:
                            print(f"  [OK-CACHE] {name} ({dest.stat().st_size / 1048576:.2f} Mo)")
                            success_count += 1
                            continue
                except Exception:
                    pass

        # Téléchargement
        print(f"  [TELECHARGEMENT] {name} depuis {url}...")
        try:
            req_dl = urllib.request.Request(url, headers={"User-Agent": "TennisPredictor/1.0"})
            with urllib.request.urlopen(req_dl, timeout=timeout) as resp, open(dest, "wb") as out_file:
                # Lecture par bloc pour économiser la RAM
                while True:
                    chunk = resp.read(1024 * 64)
                    if not chunk:
                        break
                    out_file.write(chunk)
            print(f"  [SUCCES] {name} ({dest.stat().st_size / 1048576:.2f} Mo)")
            success_count += 1
        except Exception as err:
            print(f"  [ERREUR] Impossible de télécharger {name} : {err}")

    elapsed = round(time.time() - t0, 2)
    total_needed = len(assets_to_dl)
    print(f"[RELEASE] Terminé en {elapsed}s : {success_count}/{total_needed} assets prêts dans {target_path}.")
    return success_count == total_needed


if __name__ == "__main__":
    force_download = "--force" in sys.argv
    res = download_release_assets(force=force_download)
    if not res:
        sys.exit(1)
