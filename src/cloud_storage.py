import os
import requests
from pathlib import Path

# Remplace par ton nom d'utilisateur et le nom de ton repo GitHub
GITHUB_OWNER = "quentinho91" 
GITHUB_REPO = "Tennispredictor"

# Liste des fichiers a telecharger depuis les Releases GitHub
FILES_TO_DOWNLOAD = [
    "player_state_atp.pkl",
    "xgb_model_atp.json",
    "lgb_model_atp.txt",
    "cat_model_atp.cbm",
    "calibrator_atp.pkl",
    "feature_cols_atp.pkl",
    "player_state_wta.pkl",
    "xgb_model_wta.json",
    "lgb_model_wta.txt",
    "cat_model_wta.cbm",
    "calibrator_wta.pkl",
    "feature_cols_wta.pkl",
    "predictions_db.json"
]

def download_data_from_github():
    """Telecharge les derniers modeles entraines depuis GitHub Releases."""
    print("Verification des donnees Cloud (GitHub Releases)...")
    base_dir = Path(__file__).resolve().parent.parent
    data_dir = base_dir / "data" / "processed"
    data_dir.mkdir(parents=True, exist_ok=True)
    
    # URL de la derniere release (tag "latest_model")
    for file_name in FILES_TO_DOWNLOAD:
        url = f"https://github.com/{GITHUB_OWNER}/{GITHUB_REPO}/releases/download/latest_model/{file_name}"
        file_path = data_dir / file_name
        
        # On essaie de telecharger seulement s'il n'existe pas ou en forcant
        print(f"  -> Telechargement de {file_name}...")
        try:
            response = requests.get(url, stream=True, timeout=30)
            if response.status_code == 200:
                with open(file_path, "wb") as f:
                    for chunk in response.iter_content(chunk_size=8192):
                        f.write(chunk)
                print(f"     OK ! Sauvegarde dans {file_path.name}")
            else:
                print(f"     Erreur {response.status_code}: Le fichier n'existe pas encore sur GitHub Releases.")
                print("     (C'est normal si c'est le premier lancement ou que tu es en local)")
        except Exception as e:
            print(f"     Erreur de connexion : {e}")
