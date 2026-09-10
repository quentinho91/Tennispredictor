"""
00b_build_odds_lookup.py — Construction de la table de cotes historiques

Source : fichiers Excel tennis-data.co.uk dans data/raw/odds/ (2013–2026)
Format brut : Winner/Loser en "Federer R.", Date, Tournament, Round, B365W/B365L, PSW/PSL, MaxW/MaxL, AvgW/AvgL

Sortie : data/processed/odds_lookup_atp.parquet
Colonnes utiles pour le join :
  - winner_name_raw, loser_name_raw (noms normalisés du fichier odds)
  - tourney_date (datetime)
  - tournament_raw
  - round_raw
  - odds_winner_b365, odds_loser_b365
  - odds_winner_ps, odds_loser_ps
  - odds_winner_max, odds_loser_max
  - odds_winner_avg, odds_loser_avg
  - implied_prob_winner_avg (prob implicite sans vig, winner)
  - implied_prob_loser_avg (prob implicite sans vig, loser)

REGLE ANTI-LEAKAGE : Les cotes reflètent l'opinion du marché AVANT le match.
Elles sont disponibles avant le début du match => pas de leakage.
"""

import os
import sys
import glob
import re
import pandas as pd
import numpy as np
from pathlib import Path

# Fix Windows console encoding
try:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BASE_DIR = Path(__file__).resolve().parent.parent
ODDS_DIR = BASE_DIR / "data" / "raw" / "odds"
PROCESSED_DIR = BASE_DIR / "data" / "processed"
PROCESSED_DIR.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# 1. Mapping Round (tennis-data.co.uk -> standard ATP)
# ---------------------------------------------------------------------------
ROUND_MAP = {
    "1st round": "R64",
    "2nd round": "R32",
    "3rd round": "R16",
    "4th round": "R16",   # GC only (R16 = 4e tour)
    "quarterfinals": "QF",
    "quarterfinal": "QF",
    "semifinals": "SF",
    "semifinal": "SF",
    "the final": "F",
    "final": "F",
    "round robin": "RR",
    "round of 128": "R128",
    "round of 64": "R64",
    "round of 32": "R32",
    "round of 16": "R16",
}


def normalize_round(r):
    if not isinstance(r, str):
        return None
    rl = r.lower().strip()
    for k, v in ROUND_MAP.items():
        if k in rl:
            return v
    return r.strip()


# ---------------------------------------------------------------------------
# 2. Normalisation des noms de joueurs
# ---------------------------------------------------------------------------

def normalize_name_raw(name):
    """Uniformise le format 'Federer R.' -> 'federer r.' (lowercase, strip)."""
    if not isinstance(name, str):
        return None
    return name.strip().lower()


def last_name_initial(name):
    """
    Extrait 'Federer R.' -> ('federer', 'r') pour matching avec les noms ATP complets.
    Supporte aussi "De Minaur A." -> ('de minaur', 'a')
    """
    if not isinstance(name, str):
        return None, None
    name = name.strip()
    # Pattern : "Last Name(s) X." or "Last Name(s) X"
    m = re.match(r"^(.+?)\s+([a-z])\.?\s*$", name)
    if m:
        last = m.group(1).lower().strip()
        initial = m.group(2).lower()
        return last, initial
    return name.lower(), None


def build_name_index(full_name):
    """
    Construit (last_name_lower, first_initial_lower) depuis un nom complet ATP.
    Ex: "Rafael Nadal" -> ("nadal", "r")
        "Alex De Minaur" -> ("de minaur", "a")
    """
    if not isinstance(full_name, str):
        return None, None
    parts = full_name.strip().split()
    if len(parts) < 2:
        return full_name.lower(), None
    first_initial = parts[0][0].lower()
    last = " ".join(parts[1:]).lower()
    return last, first_initial


# ---------------------------------------------------------------------------
# 3. Suppression de la vig (overround) pour calculer les vraies probas
# ---------------------------------------------------------------------------

def remove_vig(odd_w, odd_l):
    """
    Suppression de la vig (methode multiplicative) :
    p_w_raw = 1/odd_w,  p_l_raw = 1/odd_l
    overround = p_w_raw + p_l_raw
    p_w_fair = p_w_raw / overround
    """
    try:
        if pd.isna(odd_w) or pd.isna(odd_l):
            return np.nan, np.nan
        if odd_w <= 1.0 or odd_l <= 1.0:
            return np.nan, np.nan
        p_w = 1.0 / odd_w
        p_l = 1.0 / odd_l
        total = p_w + p_l
        if total <= 0:
            return np.nan, np.nan
        return p_w / total, p_l / total
    except Exception:
        return np.nan, np.nan


# ---------------------------------------------------------------------------
# 4. Chargement et nettoyage des fichiers Excel
# ---------------------------------------------------------------------------

def load_odds_files():
    files = sorted(glob.glob(str(ODDS_DIR / "*.xlsx")))
    if not files:
        raise FileNotFoundError(
            f"Aucun fichier .xlsx trouve dans {ODDS_DIR}.\n"
            "Telecharge les fichiers depuis http://www.tennis-data.co.uk/alldata.php"
        )

    dfs = []
    for f in files:
        year = int(os.path.basename(f).replace(".xlsx", ""))
        try:
            df = pd.read_excel(f, engine="openpyxl")
        except Exception as e:
            print(f"  [WARN] Impossible de lire {f}: {e}")
            continue

        required = {"Winner", "Loser", "Date"}
        if not required.issubset(df.columns):
            print(f"  [WARN] {f}: colonnes manquantes {required - set(df.columns)}")
            continue

        df["_source_year"] = year
        dfs.append(df)
        print(f"  Charge {os.path.basename(f)}: {len(df)} matchs")

    if not dfs:
        raise ValueError("Aucun fichier Excel odds valide trouve.")

    return pd.concat(dfs, ignore_index=True)


def build_odds_lookup():
    print("\n" + "=" * 60)
    print("  CONSTRUCTION DE LA TABLE ODDS HISTORIQUES (tennis-data.co.uk)")
    print("=" * 60)

    raw = load_odds_files()
    print(f"\nTotal brut : {len(raw):,} matchs ({raw['_source_year'].min()}-{raw['_source_year'].max()})")

    # --- Dates ---
    raw["tourney_date"] = pd.to_datetime(raw["Date"], errors="coerce")
    raw = raw.dropna(subset=["tourney_date"])

    # --- Noms normalises ---
    raw["winner_name_raw"] = raw["Winner"].apply(normalize_name_raw)
    raw["loser_name_raw"]  = raw["Loser"].apply(normalize_name_raw)
    raw = raw.dropna(subset=["winner_name_raw", "loser_name_raw"])

    # Extraction last_name + initiale pour le matching
    winner_idx = raw["winner_name_raw"].apply(lambda x: pd.Series(last_name_initial(x)))
    raw["winner_last"] = winner_idx.iloc[:, 0]
    raw["winner_init"] = winner_idx.iloc[:, 1]

    loser_idx = raw["loser_name_raw"].apply(lambda x: pd.Series(last_name_initial(x)))
    raw["loser_last"] = loser_idx.iloc[:, 0]
    raw["loser_init"] = loser_idx.iloc[:, 1]

    # --- Tournoi / Round ---
    raw["tournament_raw"] = raw["Tournament"].astype(str).str.strip().str.lower() if "Tournament" in raw.columns else ""
    raw["round_raw"] = raw["Round"].apply(normalize_round) if "Round" in raw.columns else None

    # --- Cotes (float) ---
    for col in ["B365W", "B365L", "PSW", "PSL", "MaxW", "MaxL", "AvgW", "AvgL"]:
        if col in raw.columns:
            raw[col] = pd.to_numeric(raw[col], errors="coerce")
        else:
            raw[col] = np.nan

    # --- Probabilites implicites sans vig ---
    # Reference principale : cote moyenne du marche (AvgW/AvgL)
    results_avg = [remove_vig(r["AvgW"], r["AvgL"]) for _, r in raw.iterrows()]
    raw["implied_prob_winner_avg"] = [x[0] for x in results_avg]
    raw["implied_prob_loser_avg"]  = [x[1] for x in results_avg]

    # Pinnacle = bookmaker "sharp" (sans marge ou presque)
    results_ps = [remove_vig(r["PSW"], r["PSL"]) for _, r in raw.iterrows()]
    raw["implied_prob_winner_ps"] = [x[0] for x in results_ps]
    raw["implied_prob_loser_ps"]  = [x[1] for x in results_ps]

    # --- Overround (marge totale du bookmaker) ---
    def calc_overround(o_w, o_l):
        try:
            if pd.isna(o_w) or pd.isna(o_l) or o_w <= 0 or o_l <= 0:
                return np.nan
            return (1.0 / o_w + 1.0 / o_l - 1.0) * 100.0
        except Exception:
            return np.nan

    raw["overround_b365_pct"] = raw.apply(lambda r: calc_overround(r["B365W"], r["B365L"]), axis=1)
    raw["overround_avg_pct"]  = raw.apply(lambda r: calc_overround(r["AvgW"],  r["AvgL"]),  axis=1)

    # --- Selection et export ---
    keep = [
        "tourney_date", "tournament_raw", "round_raw", "_source_year",
        "winner_name_raw", "loser_name_raw",
        "winner_last", "winner_init", "loser_last", "loser_init",
        "B365W", "B365L", "PSW", "PSL", "MaxW", "MaxL", "AvgW", "AvgL",
        "implied_prob_winner_avg", "implied_prob_loser_avg",
        "implied_prob_winner_ps",  "implied_prob_loser_ps",
        "overround_b365_pct", "overround_avg_pct",
    ]
    keep = [c for c in keep if c in raw.columns]
    result = raw[keep].copy()

    # Renommage pour clarte
    result = result.rename(columns={
        "B365W": "odds_winner_b365", "B365L": "odds_loser_b365",
        "PSW":   "odds_winner_ps",   "PSL":   "odds_loser_ps",
        "MaxW":  "odds_winner_max",  "MaxL":  "odds_loser_max",
        "AvgW":  "odds_winner_avg",  "AvgL":  "odds_loser_avg",
    })

    out_path = PROCESSED_DIR / "odds_lookup_atp.parquet"
    result.to_parquet(out_path, index=False)

    print(f"\n[OK] {len(result):,} matchs avec odds -> {out_path}")
    print(f"  Couverture : {result['_source_year'].min()} - {result['_source_year'].max()}")
    print(f"  NaN implied_prob_winner_avg : {result['implied_prob_winner_avg'].isna().mean():.1%}")
    print(f"  NaN odds_winner_ps : {result['odds_winner_ps'].isna().mean():.1%}")

    print("\nApercu :")
    print(result[["tourney_date", "winner_name_raw", "loser_name_raw",
                  "odds_winner_avg", "odds_loser_avg",
                  "implied_prob_winner_avg"]].head(5).to_string(index=False))

    return result


if __name__ == "__main__":
    build_odds_lookup()
