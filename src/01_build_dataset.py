"""
Concatène les CSV annuels bruts et transforme le format winner/loser
en format symétrique player_1 / player_2 avec une target binaire.

Pourquoi c'est important :
Les fichiers bruts ont toujours le vainqueur en premier ("winner_*").
Si on entraîne un modèle directement là-dessus, il peut apprendre des
raccourcis idiots (ex: "la colonne A gagne presque toujours" si on
n'y prend pas garde dans le feature engineering). On restructure donc
en (player_1, player_2, target) avec une assignation aléatoire du
"joueur 1", puis toutes les features seront construites en DIFFÉRENCE
(player_1 - player_2), ce qui rend le problème naturellement symétrique.
"""

import pandas as pd
import numpy as np
import glob
from pathlib import Path
import argparse

parser = argparse.ArgumentParser()
parser.add_argument("--circuit", default="atp", choices=["atp", "wta"], help="Circuit to process (atp or wta)")
args = parser.parse_args()

np.random.seed(42)

BASE_DIR = Path(__file__).resolve().parent.parent  # racine du projet
RAW_DIR = BASE_DIR / "data" / "raw" / args.circuit
PROCESSED_DIR = BASE_DIR / "data" / "processed"
PROCESSED_DIR.mkdir(parents=True, exist_ok=True)

MIN_YEAR = 2000

def load_raw(pattern=None):
    if pattern:
        files = sorted(glob.glob(pattern, recursive=True))
    else:
        files = sorted(str(p) for p in RAW_DIR.rglob("*.csv"))
        
    if not files:
        raise FileNotFoundError(
            f"Aucun CSV trouvé dans {RAW_DIR}. "
            "Lance d'abord: python 00_download_data.py"
        )
    dfs = []
    for f in files:
        try:
            d = pd.read_csv(f, low_memory=False)
        except Exception as e:
            print(f"Ignoré (illisible): {f} ({e})")
            continue
        if not {"winner_name", "loser_name", "tourney_date"}.issubset(d.columns):
            print(f"Ignoré (pas un fichier de matchs, colonnes attendues absentes): {f}")
            continue
        dfs.append(d)
    if not dfs:
        raise ValueError("Aucun fichier valide (schéma matchs) trouvé après filtrage.")
    df = pd.concat(dfs, ignore_index=True)

    # Garde-fou : ongoing_tourneys.csv (tournois en cours) peut en théorie
    # se recouvrir avec le fichier annuel correspondant une fois le tournoi
    # terminé et absorbé dans les archives (pas de garantie côté API que
    # l'un ou l'autre soit vidé au bon moment). Un match en double gonfle
    # artificiellement son poids dans l'entraînement -> on déduplique sur
    # la clé naturelle d'un match.
    n_before = len(df)
    df = df.drop_duplicates(subset=["tourney_id", "winner_name", "loser_name", "tourney_date", "round"])
    if len(df) < n_before:
        print(f"Doublons retirés (chevauchement ongoing_tourneys / archive annuelle): {n_before - len(df)}")

    return df


def basic_clean(df):
    # Dates
    df["tourney_date"] = pd.to_datetime(df["tourney_date"], format="%Y%m%d", errors="coerce")
    # Normalisation des noms (fautes de frappe dans les bases brutes ATP)
    name_aliases = {
        "Aleksander Shevchenko": "Alexander Shevchenko",
        "Aleksandr Shevchenko": "Alexander Shevchenko",
    }
    df["winner_name"] = df["winner_name"].replace(name_aliases)
    df["loser_name"] = df["loser_name"].replace(name_aliases)

    df = df.dropna(subset=["tourney_date", "winner_name", "loser_name"])

    # Normalisation de tourney_level : ongoing_tourneys.csv (tournois en
    # cours, pas encore absorbés dans l'archive annuelle) utilise parfois
    # '1000' au lieu de 'M' pour les Masters -- même événement, code
    # différent selon la source, ce qui fragmenterait artificiellement la
    # feature en 2 catégories distinctes. NB: '250'/'500'/'D'(Davis Cup)/
    # 'A'(United Cup, Laver Cup...) sont des catégories réellement
    # distinctes dans TOUTES les sources, on n'y touche pas.
    df["tourney_level"] = df["tourney_level"].replace({"1000": "M"})
    df["round"] = df["round"].replace({"Final": "F"})

    n_before = len(df)
    df = df[df["tourney_date"].dt.year >= MIN_YEAR]
    print(f"Filtre MIN_YEAR={MIN_YEAR}: {n_before} -> {len(df)} matchs")

    # On garde uniquement les matchs simples terminés normalement
    # (on retire les rétirements / walkovers du score si on veut être strict,
    # mais on garde une colonne pour pouvoir filtrer plus tard)
    df["retirement"] = df["score"].astype(str).str.contains("RET|W/O|DEF|ABN", case=False, na=False)

    # Tri chronologique : indispensable pour tout calcul "au fil de l'eau" (Elo, forme, etc.)
    df = df.sort_values(["tourney_date", "match_num"]).reset_index(drop=True)
    df["match_id"] = df.index
    return df


def to_symmetric(df):
    """Transforme chaque ligne winner/loser en player_1/player_2 + target,
    avec assignation aléatoire de qui est 'player_1' pour éviter tout biais
    d'ordre dans les colonnes."""
    swap = np.random.rand(len(df)) < 0.5

    p1_is_winner = ~swap  # si pas de swap, player_1 = winner

    out = pd.DataFrame({
        "match_id": df["match_id"],
        "tourney_date": df["tourney_date"],
        "tourney_id": df["tourney_id"],
        "tourney_name": df["tourney_name"],
        "surface": df["surface"],
        "tourney_level": df["tourney_level"],
        "best_of": pd.to_numeric(df["best_of"], errors="coerce"),
        "round": df["round"],
        "retirement": df["retirement"],
        "score": df["score"],  # gardé brut, toujours écrit du point de vue du VAINQUEUR
                                 # (donc du point de vue p1 seulement si p1_is_winner==True,
                                 # sinon il faut inverser chaque set — fait dans 02_feature_engineering.py)
        "minutes": pd.to_numeric(df["minutes"], errors="coerce"),
        "indoor": df["indoor"],  # 'I' / 'O' / NaN
    })

    cols_map = [
        ("id", "winner_id", "loser_id"),
        ("name", "winner_name", "loser_name"),
        ("hand", "winner_hand", "loser_hand"),
        ("ht", "winner_ht", "loser_ht"),
        ("age", "winner_age", "loser_age"),
        ("ioc", "winner_ioc", "loser_ioc"),
        ("rank", "winner_rank", "loser_rank"),
        ("rank_points", "winner_rank_points", "loser_rank_points"),
        ("ace", "w_ace", "l_ace"),
        ("df", "w_df", "l_df"),
        ("svpt", "w_svpt", "l_svpt"),
        ("1stIn", "w_1stIn", "l_1stIn"),
        ("1stWon", "w_1stWon", "l_1stWon"),
        ("2ndWon", "w_2ndWon", "l_2ndWon"),
        ("SvGms", "w_SvGms", "l_SvGms"),
        ("bpSaved", "w_bpSaved", "l_bpSaved"),
        ("bpFaced", "w_bpFaced", "l_bpFaced"),
        ("seed", "winner_seed", "loser_seed"),
        ("entry", "winner_entry", "loser_entry"),
    ]

    # Colonnes qui doivent être numériques. Selon les fichiers sources (tour
    # principal / qualifs / challengers / futures), certaines valeurs sont
    # parfois stockées en texte -> ça casse l'écriture en parquet si on ne
    # force pas le type explicitement (colonne "object" avec un mélange de
    # str et de float en interne).
    numeric_bases = {"ht", "age", "rank", "rank_points", "ace", "df", "svpt",
                      "1stIn", "1stWon", "2ndWon", "SvGms", "bpSaved", "bpFaced", "seed"}

    for base, wcol, lcol in cols_map:
        if base in numeric_bases:
            w_series = pd.to_numeric(df[wcol], errors="coerce")
            l_series = pd.to_numeric(df[lcol], errors="coerce")
        else:
            w_series, l_series = df[wcol], df[lcol]
        out[f"p1_{base}"] = np.where(p1_is_winner, w_series, l_series)
        out[f"p2_{base}"] = np.where(p1_is_winner, l_series, w_series)
        if base in numeric_bases:
            out[f"p1_{base}"] = pd.to_numeric(out[f"p1_{base}"], errors="coerce")
            out[f"p2_{base}"] = pd.to_numeric(out[f"p2_{base}"], errors="coerce")

    out["target"] = p1_is_winner.astype(int)  # 1 si player_1 gagne
    return out


if __name__ == "__main__":
    raw = load_raw()
    raw = basic_clean(raw)
    sym = to_symmetric(raw)
    output_path = PROCESSED_DIR / f"matches_symmetric_{args.circuit}.parquet"
    sym.to_parquet(output_path, index=False)
    print(f"{len(sym)} matchs traités -> {output_path}")
    print(sym["target"].value_counts(normalize=True))

    # --- UPDATE PREDICTIONS DB RESULTS ---
    import json
    db_path = PROCESSED_DIR / "predictions_db.json"
    if db_path.exists():
        try:
            with open(db_path, "r", encoding="utf-8") as f:
                db = json.load(f)
            
            updated = 0
            # Convert tourney_date to datetime for recent matches filtering
            # tourney_date is like "20240115"
            sym_recent = sym.copy()
            sym_recent['date_dt'] = pd.to_datetime(sym_recent['tourney_date'], format='%Y%m%d', errors='coerce')
            
            for ev_id, match in db.items():
                if match.get("circuit", "atp") != args.circuit:
                    continue
                if "winner" not in match:
                    p1, p2 = match['p1'], match['p2']
                    m_time = pd.to_datetime(match['time']).tz_localize(None)
                    
                    # Search for this match in sym
                    # Since tourney_date is usually the monday of the week, we allow a +/- 15 days window
                    mask_date = (sym_recent['date_dt'] - m_time).dt.days.abs() <= 15
                    
                    # Direct order
                    mask_direct = mask_date & (sym_recent['p1_name'] == p1) & (sym_recent['p2_name'] == p2)
                    if mask_direct.any():
                        target = sym_recent.loc[mask_direct, 'target'].iloc[-1]
                        match['winner'] = "p1" if target == 1 else "p2"
                        updated += 1
                        continue
                        
                    # Reversed order
                    mask_rev = mask_date & (sym_recent['p1_name'] == p2) & (sym_recent['p2_name'] == p1)
                    if mask_rev.any():
                        target = sym_recent.loc[mask_rev, 'target'].iloc[-1]
                        match['winner'] = "p2" if target == 1 else "p1"
                        updated += 1
            
            if updated > 0:
                with open(db_path, "w", encoding="utf-8") as f:
                    json.dump(db, f, indent=4)
                print(f"[{updated}] résultats de matchs mis à jour dans predictions_db.json")
        except Exception as e:
            print(f"Erreur lors de la MAJ de predictions_db: {e}")
