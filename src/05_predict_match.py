"""
05_predict_match.py  —  Probabilite de victoire pour un match a venir.

USAGE :
    python 05_predict_match.py

PREREQUIS :
    1. python 02_feature_engineering.py   -> data/processed/player_state.pkl
    2. python 03_train_model.py           -> data/processed/xgb_model.json
                                            data/processed/feature_cols.pkl

Le script demande interactivement les infos du match et retourne :
    - Probabilite p_model pour chaque joueur
    - Si tu entres les cotes du bookmaker : edge calcule + recommandation
"""

import os
import sys
import pickle
import json
import numpy as np
import pandas as pd
import xgboost as xgb
import joblib
from pathlib import Path
from difflib import get_close_matches
import datetime
import requests
from rapidfuzz import process, fuzz

# --------------------------------------------------------------------------
# Chemins
# --------------------------------------------------------------------------
BASE_DIR    = Path(__file__).resolve().parent.parent
PROC_DIR    = BASE_DIR / "data" / "processed"

def get_paths(circuit="atp"):
    return (
        PROC_DIR / f"player_state_{circuit}.pkl",
        PROC_DIR / f"xgb_model_{circuit}.json",
        PROC_DIR / f"feature_cols_{circuit}.pkl"
    )

# --------------------------------------------------------------------------
# Import des helpers de 02_feature_engineering.py via importlib
# (le nom commence par un chiffre, on ne peut pas faire "import 02_...")
# --------------------------------------------------------------------------
import importlib.util
_fe_path = Path(__file__).parent / "02_feature_engineering.py"
_spec    = importlib.util.spec_from_file_location("fe", _fe_path)
fe       = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fe)

# Raccourcis vers les helpers
_wr_n          = fe._winrate_n
_wr_days       = fe._winrate_days
_cnt_recent    = fe._count_recent_tuples
_consistency   = fe._consistency
_avg_min       = fe._avg_minutes
_mean_field    = fe._mean_field
_tb_rate       = fe._tb_rate
_giant_killer  = fe._giant_killer_rate
_rank_ago      = fe._rank_n_days_ago
_rolling_stats = fe._rolling_stats
_wr_surf       = fe._winrate_surface
_wr_surf_days  = fe._winrate_surface_days
_get_arch      = fe._get_archetype
_surf_bias     = fe._surface_bias
_gk_n          = fe._giant_killer_rate_n
_upset_n       = fe._upset_rate_n
_wr_round      = fe._winrate_round_type
_sets_recent   = fe._sets_count_recent
elo_exp        = fe.elo_expected
SERVE_KEYS     = fe.SERVE_RETURN_KEYS
ELO_LAG        = fe.ELO_TREND_LAG
RANK_MOM_DAYS  = fe.RANK_MOMENTUM_DAYS
LATE_ROUNDS    = fe.LATE_ROUNDS
ELO_INIT       = fe.ELO_INIT


# --------------------------------------------------------------------------
# Utilitaires
# --------------------------------------------------------------------------

def fuzzy_find(name, known, n=5, cutoff=0.6):
    """Recherche floue d'un nom de joueur dans la liste connue.

    Priorité :
    1. Correspondance exacte sur le nom complet
    2. Correspondance exacte sur une partie du nom (prénom ou nom de famille)
    3. Correspondance par préfixe sur une partie du nom
    4. Sous-chaîne dans une partie du nom
    5. Correspondance floue (difflib) sur les candidats filtrés
    """
    name_low = name.lower().strip()
    tokens = name_low.split()

    # 1. Correspondance exacte nom complet
    exact = [k for k in known if k.lower() == name_low]
    if exact:
        return exact

    # 2 & 3 & 4. Classement par score de correspondance sur les parties du nom
    scored = []
    for k in known:
        parts = k.lower().split()
        score = 0
        for t in tokens:
            if len(t) < 2:
                continue
            for part in parts:
                if part == t:            # correspondance exacte sur une partie
                    score = max(score, 4)
                elif part.startswith(t): # préfixe exact
                    score = max(score, 3)
                elif t in part:          # sous-chaîne
                    score = max(score, 2)
        if score > 0:
            scored.append((score, k))

    if scored:
        # Trier par score décroissant, puis alphabétique
        scored.sort(key=lambda x: (-x[0], x[1]))
        top_candidates = [k for _, k in scored[:max(n * 4, 20)]]

        # Appliquer difflib sur les meilleurs candidats pour affiner
        from difflib import SequenceMatcher
        def _ratio(k):
            parts = k.lower().split()
            return max(SequenceMatcher(None, name_low, part).ratio() for part in parts)

        top_candidates_scored = [(k, _ratio(k)) for k in top_candidates]
        top_candidates_scored.sort(key=lambda x: -x[1])

        # Filtrer : garder seulement les candidats avec un ratio décent
        # (au moins 50% du meilleur ratio ou ratio >= 0.4)
        best_ratio = top_candidates_scored[0][1] if top_candidates_scored else 0
        threshold = max(0.35, best_ratio * 0.5)
        filtered = [k for k, r in top_candidates_scored if r >= threshold]

        return filtered[:n] if filtered else [k for k, _ in top_candidates_scored[:n]]

    # 5. Fallback : difflib global
    return get_close_matches(name, known, n=n, cutoff=cutoff)


def ask(prompt, default=None):
    suffix = f" [{default}]" if default is not None else ""
    val = input(f"{prompt}{suffix}: ").strip()
    return val if val else str(default) if default is not None else val


def ask_float(prompt, default=None):
    val = ask(prompt, default)
    if not val or val == "None":
        return None
    try:
        return float(val)
    except ValueError:
        return None


def remove_overround(odds1, odds2):
    total = 1 / odds1 + 1 / odds2
    return (1 / odds1) / total, (1 / odds2) / total


# --------------------------------------------------------------------------
# Chargement des ressources
# --------------------------------------------------------------------------

class EnsemblePredictor:
    def __init__(self, models):
        self.models = models
        
    def predict_proba(self, X):
        preds = []
        for model in self.models:
            preds.append(model.predict_proba(X))
        return np.mean(preds, axis=0)

class CalibratedPredictor:
    def __init__(self, model, calibrator=None):
        self.model = model
        self.calibrator = calibrator
    
    def predict_proba(self, X):
        p_raw = self.model.predict_proba(X)
        if self.calibrator is not None:
            p1_raw = p_raw[:, 1]
            p1_calib = self.calibrator.predict(p1_raw)
            p0_calib = 1.0 - p1_calib
            return np.column_stack((p0_calib, p1_calib))
        return p_raw


def load_resources(circuit="atp"):
    STATE_PATH, MODEL_PATH, FCOLS_PATH = get_paths(circuit)
    for path in (STATE_PATH, MODEL_PATH, FCOLS_PATH):
        if not path.exists():
            msg = f"[ERREUR] Fichier manquant : {path}\n"
            if "player_state" in str(path):
                msg += f"  -> Lance d'abord : python 02_feature_engineering.py --circuit {circuit}"
            elif "xgb_model" in str(path):
                msg += f"  -> Lance d'abord : python 03_train_model.py --circuit {circuit}"
            raise FileNotFoundError(msg)

    print(f"Chargement des modeles et de l'etat des joueurs ({circuit.upper()})...", end=" ", flush=True)
    with open(STATE_PATH, "rb") as f:
        state = pickle.load(f)
        
    xgb_model = xgb.XGBClassifier()
    xgb_model.load_model(str(MODEL_PATH))
    models = [xgb_model]
    
    lgb_path = PROC_DIR / f"lgb_model_{circuit}.txt"
    # Désactiver LightGBM et CatBoost sur Render pour éviter tout OOM
    if lgb_path.exists() and not os.environ.get("RENDER"):
        import lightgbm as lgbm
        lgb_model = lgbm.Booster(model_file=str(lgb_path))
        class LGBMWrapper:
            def __init__(self, booster):
                self.booster = booster
            def predict_proba(self, X):
                p1 = self.booster.predict(X)
                return np.column_stack((1.0 - p1, p1))
        models.append(LGBMWrapper(lgb_model))
        
    cat_path = PROC_DIR / f"cat_model_{circuit}.cbm"
    # Désactiver CatBoost sur Render pour éviter le Out-Of-Memory (OOM) sur le Free Tier (512MB)
    if cat_path.exists() and not os.environ.get("RENDER"):
        from catboost import CatBoostClassifier
        cat_model = CatBoostClassifier()
        cat_model.load_model(str(cat_path))
        models.append(cat_model)
        
    ensemble_model = EnsemblePredictor(models)
    feature_cols = joblib.load(FCOLS_PATH)
    
    calibrator_path = PROC_DIR / f"calibrator_{circuit}.pkl"
    calibrator = None
    if calibrator_path.exists():
        calibrator = joblib.load(calibrator_path)
        
    calibrated_model = CalibratedPredictor(ensemble_model, calibrator)
    
    print(f"OK  ({len(state['elo'])} joueurs, {len(feature_cols)} features, {len(models)} modeles, Calibrator={'Oui' if calibrator else 'Non'})")
    return state, calibrated_model, feature_cols


def _get_exact_rest_hours(player_name, last_play_day_offset, date_min, match_date, default_days):
    """
    Scrape l'heure exacte du dernier match de player_name joué le jour `last_play_day_offset`.
    """
    if default_days > 60:
        return default_days * 24.0, 0.0

    target_date = date_min + pd.Timedelta(days=last_play_day_offset)
    url = f"https://api.sofascore.com/api/v1/sport/tennis/scheduled-events/{target_date.strftime('%Y-%m-%d')}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        "Accept": "*/*",
        "Origin": "https://www.sofascore.com",
        "Referer": "https://www.sofascore.com/"
    }
    
    try:
        resp = requests.get(url, headers=headers, timeout=5)
        if resp.status_code == 200:
            data = resp.json()
            events_dict = {}
            for ev in data.get('events', []):
                p1_n = ev.get('homeTeam', {}).get('name')
                p2_n = ev.get('awayTeam', {}).get('name')
                ts = ev.get('startTimestamp')
                if p1_n and ts: events_dict[p1_n] = ts
                if p2_n and ts: events_dict[p2_n] = ts
                    
            if events_dict:
                best_match, score, _ = process.extractOne(player_name, list(events_dict.keys()), scorer=fuzz.token_sort_ratio)
                if score >= 80:
                    ts = events_dict[best_match]
                    match_dt = datetime.datetime.fromtimestamp(ts)
                    # On estime le match à prédire à 14h00
                    pred_dt = match_date + pd.Timedelta(hours=14)
                    hours_diff = (pred_dt - match_dt).total_seconds() / 3600.0
                    return max(0.0, hours_diff), (1.0 if match_dt.hour >= 20 else 0.0)
    except Exception:
        pass
        
    return default_days * 24.0, 0.0


# --------------------------------------------------------------------------
# Calcul des features pour UN match
# --------------------------------------------------------------------------

def compute_features(p1, p2, surf, t_level, round_, best_of, indoor,
                     match_date, state, tourney_name=None, rank1=None, rank2=None,
                     points1=None, points2=None,
                     seed1=None, seed2=None, entry1=None, entry2=None,
                     matches_tourney1=0, matches_tourney2=0,
                     games_won1=0, games_total1=0,
                     games_won2=0, games_total2=0,
                     sets_won1=0, sets_total1=0,
                     sets_won2=0, sets_total2=0):
    """Calcule toutes les features pour le match p1 vs p2."""
    date_min = state["date_min"]
    day = int((match_date - date_min).days)

    elo         = state["elo"]
    elo_surf    = state["elo_surface"]
    elo_surf_w  = state["elo_surface_w"]
    elo_hist    = state["elo_history"]
    rk_hist     = state["rank_history"]
    peak_rank   = state["peak_rank"]
    career_m    = state["career_matches"]
    career_ret  = state["career_retirements"]
    first_md    = state["first_match_day"]
    sc_count    = state["surface_career_count"]
    sc_wins     = state["surface_career_wins"]
    last_surf   = state["last_surface"]
    rr          = state["recent_results"]
    streak      = state["streak"]
    last_pd     = state["last_play_date"]
    last_ret    = state["last_retirement"]
    h2h         = state["h2h"]
    h2h_surf    = state["h2h_surface"]
    h2h_hist    = state["h2h_history"]
    last_h2h_d  = state["last_h2h_day"]
    last_h2h_r  = state["last_h2h_result"]
    srv_hist    = state["serve_return_hist"]

    tourney_cpi_yearly = state.get("tourney_cpi_yearly", {})
    fast_results = state.get("fast_results", {})
    medium_results = state.get("medium_results", {})
    slow_results = state.get("slow_results", {})
    high_altitude_results = state.get("high_altitude_results", {})
    vs_lefty_results = state.get("vs_lefty_results", {})
    tourney_champions = state.get("tourney_champions", {})
    tourney_countries = state.get("tourney_countries", {})
    player_ioc_dict = state.get("player_ioc_dict", {})
    
    def get_cpi(t_name, t_year, t_surf):
        if t_name in tourney_cpi_yearly:
            hist = [tourney_cpi_yearly[t_name][y] for y in range(t_year-3, t_year) if y in tourney_cpi_yearly[t_name]]
            if hist: return sum(hist) / len(hist)
        if t_surf == 'Hard': return 8.5
        elif t_surf == 'Clay': return 5.5
        elif t_surf == 'Grass': return 10.5
        return 8.0

    def _speed_wr(results_dict, p):
        lst = results_dict.get(p, [])[-30:]
        if not lst: return 0.5
        return sum(w for d, w in lst) / len(lst)

    def get_altitude(t_name):
        alt_map = {'Gstaad': 1050, 'Kitzbuhel': 762, 'Bogota': 2640, 'Quito': 2850, 'Madrid': 667, 'Denver': 1609}
        return alt_map.get(t_name, 0)
        
    def get_tourney_country(t_name):
        return tourney_countries.get(t_name, "UNKNOWN")

    rr1 = rr.get(p1, [])
    rr2 = rr.get(p2, [])

    r1   = rank1   if rank1   is not None else state["last_rank"].get(p1, np.nan)
    r2   = rank2   if rank2   is not None else state["last_rank"].get(p2, np.nan)
    pts1 = points1 if points1 is not None else state["last_points"].get(p1, np.nan)
    pts2 = points2 if points2 is not None else state["last_points"].get(p2, np.nan)
    has_rank = (r1 == r1 and r2 == r2)

    ht1  = state["last_ht"].get(p1, np.nan)
    ht2  = state["last_ht"].get(p2, np.nan)
    hand1 = state["last_hand"].get(p1, "R")
    hand2 = state["last_hand"].get(p2, "R")

    def _est_age(player):
        last_a = state["last_age"].get(player)
        last_d = state["last_age_day"].get(player)
        if last_a is None:
            return np.nan
        return last_a + (day - last_d) / 365.25

    age1 = _est_age(p1)
    age2 = _est_age(p2)

    feat = {}

    # Elo
    e1  = elo.get(p1, ELO_INIT)
    e2  = elo.get(p2, ELO_INIT)
    es1 = elo_surf.get(surf, {}).get(p1, ELO_INIT)
    es2 = elo_surf.get(surf, {}).get(p2, ELO_INIT)
    esw1 = elo_surf_w.get(surf, {}).get(p1, ELO_INIT)
    esw2 = elo_surf_w.get(surf, {}).get(p2, ELO_INIT)
    feat["elo_diff"]           = e1 - e2
    feat["elo_surface_diff"]   = es1 - es2
    feat["elo_surface_w_diff"] = esw1 - esw2
    feat["elo_p1"]             = e1
    feat["elo_p2"]             = e2
    eh1 = elo_hist.get(p1, [])
    eh2 = elo_hist.get(p2, [])
    t1  = (e1 - eh1[-ELO_LAG]) if len(eh1) >= ELO_LAG else np.nan
    t2  = (e2 - eh2[-ELO_LAG]) if len(eh2) >= ELO_LAG else np.nan
    feat["elo_trend_diff"] = (t1 - t2) if (t1 == t1 and t2 == t2) else np.nan

    # Classement
    feat["points_diff"] = ((np.log1p(pts1) - np.log1p(pts2))
                           if (pts1 == pts1 and pts2 == pts2) else np.nan)
    pr1 = peak_rank.get(p1, float("inf"))
    pr2 = peak_rank.get(p2, float("inf"))
    feat["peak_rank_diff"] = (pr2 - pr1) if (pr1 < float("inf") and pr2 < float("inf")) else np.nan
    rh1 = rk_hist.get(p1, [])
    rh2 = rk_hist.get(p2, [])
    rm1 = _rank_ago(rh1, day, RANK_MOM_DAYS)
    rm2 = _rank_ago(rh2, day, RANK_MOM_DAYS)
    m1  = (rm1 - r1) if (rm1 == rm1 and r1 == r1) else np.nan
    m2  = (rm2 - r2) if (rm2 == rm2 and r2 == r2) else np.nan
    feat["rank_momentum_diff"] = (m1 - m2) if (m1 == m1 and m2 == m2) else np.nan

    # Forme
    feat["form10_diff"]   = _wr_n(rr1, 10)   - _wr_n(rr2, 10)
    feat["form365d_diff"] = _wr_days(rr1, day, 365) - _wr_days(rr2, day, 365)
    feat["streak_diff"]      = streak.get(p1, 0) - streak.get(p2, 0)
    feat["consistency_diff"] = _consistency(rr2, 20) - _consistency(rr1, 20)

    # H2H
    h12   = h2h.get(p1, {}).get(p2, [0, 0])
    h_tot = h12[0] + h12[1]
    feat["h2h_total"]       = h_tot
    feat["h2h_diff"]        = (h12[0] - h12[1]) / h_tot if h_tot > 0 else 0.0
    hs12  = h2h_surf.get(p1, {}).get(p2, {}).get(surf, [0, 0])
    hs_tot = hs12[0] + hs12[1]
    feat["h2h_surface_diff"] = (hs12[0] - hs12[1]) / hs_tot if hs_tot > 0 else 0.0
    lhd   = last_h2h_d.get(p1, {}).get(p2)
    feat["days_since_h2h"]    = (day - lhd) if lhd is not None else -1
    lhr   = last_h2h_r.get(p1, {}).get(p2)
    feat["last_h2h_result_diff"] = (1 if lhr == 1 else (-1 if lhr == 0 else 0))

    # Repos / fatigue
    rest1_days = (day - last_pd[p1]) if p1 in last_pd else 365
    rest2_days = (day - last_pd[p2]) if p2 in last_pd else 365
    # Scraping en temps réel de la vraie fatigue
    hours1, night1 = _get_exact_rest_hours(p1, last_pd.get(p1, -999), date_min, match_date, rest1_days) if p1 in last_pd else (365*24.0, 0.0)
    hours2, night2 = _get_exact_rest_hours(p2, last_pd.get(p2, -999), date_min, match_date, rest2_days) if p2 in last_pd else (365*24.0, 0.0)
    
    feat["hours_rest_diff"] = hours1 - hours2
    feat["short_rest_p1"] = 1.0 if hours1 < 20 else 0.0
    feat["short_rest_p2"] = 1.0 if hours2 < 20 else 0.0
    feat["is_night_match"] = 1.0 if (night1 or night2) else 0.0

    feat["matches_this_tourney_diff"]    = matches_tourney1 - matches_tourney2
    feat["avg_minutes_recent_diff"]      = _avg_min(rr1, 5) - _avg_min(rr2, 5)
    feat["last_retirement_diff"]         = int(last_ret.get(p1, False)) - int(last_ret.get(p2, False))
    cm1 = career_m.get(p1, 0)
    cm2 = career_m.get(p2, 0)
    cr1 = career_ret.get(p1, 0) / cm1 if cm1 > 0 else 0.0
    
    cr2 = career_ret.get(p2, 0) / cm2 if cm2 > 0 else 0.0
    feat["career_retirement_rate_diff"]  = cr1 - cr2
    
    t_cpi = get_cpi(tourney_name, match_date.year, surf)
    
    feat["tourney_cpi"] = t_cpi
    feat["fast_court_winrate_diff"] = _speed_wr(fast_results, p1) - _speed_wr(fast_results, p2)
    feat["medium_court_winrate_diff"] = _speed_wr(medium_results, p1) - _speed_wr(medium_results, p2)
    feat["slow_court_winrate_diff"] = _speed_wr(slow_results, p1) - _speed_wr(slow_results, p2)
    
    t_alt = get_altitude(tourney_name)
    
    feat["tourney_altitude"] = t_alt
    feat["high_altitude_winrate_diff"] = _speed_wr(high_altitude_results, p1) - _speed_wr(high_altitude_results, p2)
    
    t_country = get_tourney_country(tourney_name)
    p1_ioc = player_ioc_dict.get(p1, "UNKNOWN")
    p2_ioc = player_ioc_dict.get(p2, "UNKNOWN")
    p1_home = 1 if p1_ioc == t_country else 0
    p2_home = 1 if p2_ioc == t_country else 0
    feat["home_advantage_diff"] = p1_home - p2_home

    p1_h = state.get("last_hand", {}).get(p1, 'R')
    p2_h = state.get("last_hand", {}).get(p2, 'R')
    wr1_vs_L = _speed_wr(vs_lefty_results, p1) if p2_h == 'L' else 0.5
    wr2_vs_L = _speed_wr(vs_lefty_results, p2) if p1_h == 'L' else 0.5
    feat["kryptonite_diff"] = wr1_vs_L - wr2_vs_L

    is_def1 = 1 if tourney_champions.get(tourney_name) == p1 else 0
    is_def2 = 1 if tourney_champions.get(tourney_name) == p2 else 0
    feat["is_defending_champion_diff"] = is_def1 - is_def2

    # Stats service/retour





    sh1 = srv_hist.get(p1, [])
    sh2 = srv_hist.get(p2, [])
    r5_1, r20_1 = _rolling_stats(sh1)
    r5_2, r20_2 = _rolling_stats(sh2)
    for k_idx, k in enumerate(SERVE_KEYS):
        feat[f"{k}_20_diff"] = r20_1[k_idx] - r20_2[k_idx]

    # --- Nouvelles features Archétypes ---
    sb1 = _surf_bias(rr1, day, surf)
    sb2 = _surf_bias(rr2, day, surf)
    feat["surface_bias_diff"] = sb1 - sb2

    arch1 = _get_arch(r20_1, sb1)
    arch2 = _get_arch(r20_2, sb2)
    # Exporter les archétypes pour le JSON du site web
    feat["_p1_archetype"] = arch1
    feat["_p2_archetype"] = arch2

    grind1 = r20_1[8] if len(r20_1) > 8 else np.nan
    grind2 = r20_2[8] if len(r20_2) > 8 else np.nan
    feat["grind_mismatch"] = grind1 - grind2 if (grind1 == grind1 and grind2 == grind2) else 0.0

    serve_win1 = (r20_1[2] * r20_1[3] + (1 - r20_1[2]) * r20_1[4]) if len(r20_1) > 4 else np.nan
    serve_win2 = (r20_2[2] * r20_2[3] + (1 - r20_2[2]) * r20_2[4]) if len(r20_2) > 4 else np.nan
    return_won1 = r20_1[6] if len(r20_1) > 6 else np.nan
    return_won2 = r20_2[6] if len(r20_2) > 6 else np.nan
    
    feat["serve_return_edge1"] = serve_win1 - return_won2 if (serve_win1 == serve_win1 and return_won2 == return_won2) else 0.0
    feat["serve_return_edge2"] = serve_win2 - return_won1 if (serve_win2 == serve_win2 and return_won1 == return_won1) else 0.0

    wins_vs = state.get("wins_vs_arch", {})
    matches_vs = state.get("matches_vs_arch", {})
    
    p1_w_vs = wins_vs.get(p1, {}).get(arch2, 0)
    p1_m_vs = matches_vs.get(p1, {}).get(arch2, 0)
    wr_vs_arch1 = p1_w_vs / p1_m_vs if p1_m_vs > 0 else 0.5
    
    p2_w_vs = wins_vs.get(p2, {}).get(arch1, 0)
    p2_m_vs = matches_vs.get(p2, {}).get(arch1, 0)
    wr_vs_arch2 = p2_w_vs / p2_m_vs if p2_m_vs > 0 else 0.5
    
    feat["winrate_vs_arch_diff"] = wr_vs_arch1 - wr_vs_arch2
    feat["_p1_wr_vs_arch"] = wr_vs_arch1
    feat["_p2_wr_vs_arch"] = wr_vs_arch2

    # Statique
    feat["age_diff"]  = (age1 - age2) if (age1 == age1 and age2 == age2) else np.nan
    feat["ht_diff"]   = (ht1  - ht2)  if (ht1  == ht1  and ht2  == ht2)  else np.nan
    _h1 = hand1 if hand1 in ("R", "L") else "R"
    _h2 = hand2 if hand2 in ("R", "L") else "R"
    feat["hand_matchup"]     = f"{_h1}_{_h2}"
    feat["hand_missing_diff"] = int(hand1 not in ("R","L")) - int(hand2 not in ("R","L"))
    feat["experience_diff"]  = cm1 - cm2
    fmd1 = first_md.get(p1)
    fmd2 = first_md.get(p2)
    ((day - fmd2) / 365.25 if fmd2 else 0.0)

    # Surface
    sc1 = sc_count.get(p1, {}).get(surf, 0)
    sc2 = sc_count.get(p2, {}).get(surf, 0)
    feat["surface_experience_diff"] = sc1 - sc2
    sw1 = sc_wins.get(p1, {}).get(surf, 0) / sc1 if sc1 > 0 else 0.5
    sw2 = sc_wins.get(p2, {}).get(surf, 0) / sc2 if sc2 > 0 else 0.5
    feat["surface_winrate_diff"]    = sw1 - sw2
    trans1 = int(last_surf.get(p1) is not None and last_surf.get(p1) != surf)
    trans2 = int(last_surf.get(p2) is not None and last_surf.get(p2) != surf)
    feat["surface_transition_diff"] = trans1 - trans2

    # Contexte
    sd1_f = seed1 if seed1 is not None else 999
    sd2_f = seed2 if seed2 is not None else 999
    feat["seed_number_diff"]  = sd2_f - sd1_f
    feat["is_wildcard_diff"]  = int(entry1 == "WC") - int(entry2 == "WC")
    feat["is_qualifier_diff"] = int(entry1 == "Q")  - int(entry2 == "Q")

    # Mental / clutch
    feat["decided_set_winrate_diff"] = _mean_field(rr1, 4) - _mean_field(rr2, 4)
    feat["comeback_rate_diff"]       = _mean_field(rr1, 5) - _mean_field(rr2, 5)
    feat["tiebreak_winrate_diff"]    = _tb_rate(rr1) - _tb_rate(rr2)
    feat["giant_killer_rate_diff"]   = _giant_killer(rr1) - _giant_killer(rr2)

    # Nouvelles features
    feat["sets_7d_diff"]              = _sets_recent(rr1, day, 7)  - _sets_recent(rr2, day, 7)
    feat["sets_14d_diff"]             = _sets_recent(rr1, day, 14) - _sets_recent(rr2, day, 14)
    feat["sets_tourney_diff"]         = sets_total1 - sets_total2
    feat["tourney_game_winpct_diff"]  = (
        (games_won1 / games_total1 if games_total1 > 0 else 0.5)
        - (games_won2 / games_total2 if games_total2 > 0 else 0.5)
    )
    feat["form10_surface_diff"]       = _wr_surf(rr1, surf, 10) - _wr_surf(rr2, surf, 10)
    feat["form365d_surface_diff"]     = (_wr_surf_days(rr1, surf, day, 365)
                                         - _wr_surf_days(rr2, surf, day, 365))
    feat["upset_rate_10_diff"]        = _upset_n(rr1, 10) - _upset_n(rr2, 10)
    feat["late_round_winrate_diff"]   = (_wr_round(rr1, day, 730, late=True)
                                         - _wr_round(rr2, day, 730, late=True))
    feat["early_round_winrate_diff"]  = (_wr_round(rr1, day, 730, late=False)
                                         - _wr_round(rr2, day, 730, late=False))

    # Categoriel (pour one-hot)
    feat["surface"]       = surf
    feat["tourney_level"] = t_level
    feat["round"]         = round_
    feat["indoor"]        = indoor
    feat["best_of"]       = best_of

    # === UI Detailed Stats (Ignorées par XGBoost grâce au préfixe _) ===
    feat["_p1_elo_surf"] = int(elo_surf.get(surf, {}).get(p1, ELO_INIT))
    feat["_p2_elo_surf"] = int(elo_surf.get(surf, {}).get(p2, ELO_INIT))
    
    feat["_p1_hand"] = state.get("last_hand", {}).get(p1, "Droitier")
    feat["_p2_hand"] = state.get("last_hand", {}).get(p2, "Droitier")
    
    # Winrates vs Left/Right
    w1_L = _speed_wr(vs_lefty_results, p1)
    w2_L = _speed_wr(vs_lefty_results, p2)
    # Approximation wr vs R
    wr1_global = _wr_surf(rr1, "All", 20)
    wr2_global = _wr_surf(rr2, "All", 20)
    feat["_p1_wr_vs_L"] = w1_L
    feat["_p2_wr_vs_L"] = w2_L
    feat["_p1_wr_vs_R"] = wr1_global + (wr1_global - w1_L) * 0.15 # Approx
    feat["_p2_wr_vs_R"] = wr2_global + (wr2_global - w2_L) * 0.15
    
    # Indices sur 100
    # Service (r20[2]=first_in, r20[3]=first_won, r20[4]=second_won)
    s1_pct = r20_1[2] * r20_1[3] + (1 - r20_1[2]) * r20_1[4]
    s2_pct = r20_2[2] * r20_2[3] + (1 - r20_2[2]) * r20_2[4]
    feat["_p1_service_idx"] = min(100, int(s1_pct * 100))
    feat["_p2_service_idx"] = min(100, int(s2_pct * 100))
    
    # Retour (r20[6]=return_pts_won_pct) -> x 2 pour avoir une note sur ~100 (40% de retour = 80/100)
    feat["_p1_return_idx"] = min(100, int(r20_1[6] * 200))
    feat["_p2_return_idx"] = min(100, int(r20_2[6] * 200))
    
    # Clutch (bp_saved + bp_conv) / 2 * 150 pour l'échelle
    feat["_p1_clutch_idx"] = min(100, int(((r20_1[5] + r20_1[7]) / 2.0) * 150))
    feat["_p2_clutch_idx"] = min(100, int(((r20_2[5] + r20_2[7]) / 2.0) * 150))
    
    feat["_p1_global_idx"] = int((feat["_p1_service_idx"] + feat["_p1_return_idx"] + feat["_p1_clutch_idx"]) / 3)
    feat["_p2_global_idx"] = int((feat["_p2_service_idx"] + feat["_p2_return_idx"] + feat["_p2_clutch_idx"]) / 3)
    
    # Fatigue (0 à 100)
    feat["_p1_fatigue_idx"] = min(100, int((_sets_recent(rr1, day, 14) / 15.0) * 100))
    feat["_p2_fatigue_idx"] = min(100, int((_sets_recent(rr2, day, 14) / 15.0) * 100))
    feat["_p1_rest_days"] = min(30, int(hours1 / 24))
    feat["_p2_rest_days"] = min(30, int(hours2 / 24))
    
    # Stats en tant que favori / outsider (approx via upset_rate et giant_killer_rate)
    feat["_p1_wr_fav"] = min(100, int((wr1_global + _upset_n(rr1, 20)) * 100))
    feat["_p2_wr_fav"] = min(100, int((wr2_global + _upset_n(rr2, 20)) * 100))
    feat["_p1_wr_out"] = min(100, int(_giant_killer(rr1) * 100))
    feat["_p2_wr_out"] = min(100, int(_giant_killer(rr2) * 100))

    return feat


def build_row(feat, feature_cols):
    """Construit un DataFrame 1-ligne avec le meme encodage que l'entrainement."""
    df = pd.DataFrame([feat])
    cat_cols = ["surface", "tourney_level", "round", "hand_matchup", "indoor"]
    df = pd.get_dummies(df, columns=[c for c in cat_cols if c in df.columns])
    cat_prefixes = tuple(c + "_" for c in cat_cols)
    for col in feature_cols:
        if col not in df.columns:
            if col.startswith(cat_prefixes):
                df[col] = 0.0  # Colonnes One-Hot (catégories non rencontrées)
            else:
                df[col] = np.nan  # Features numériques manquantes
    return df[feature_cols].astype(float)


import math

def calculate_confidence(p1_prob, p1, p2, match_date, tourney_name, t_level, state, fe, hours1=24.0, hours2=24.0):
    """Calcule un indice de confiance global de 0 a 100 basé sur les signaux hors-terrain."""
    # 1. Base Score (recalibré avec racine carrée pour une progression plus dynamique)
    fav_prob = max(p1_prob, 1.0 - p1_prob)
    # L'écart brut est entre 0.0 et 0.5. On le divise par 0.5 pour l'avoir entre 0 et 1.
    gap = (fav_prob - 0.50) / 0.50
    # Nouvelle formule : On part d'une base de 40 (car le modèle ML a déjà une fiabilité de base)
    # et on ajoute le signal d'écart. Cela permet aux matchs "serrés" d'atteindre 55-60 de conf.
    base_score = 40.0 + (math.sqrt(gap) * 60.0)
    
    multiplier = 1.0
    flags = []
    
    current_day = int(state.get("last_day", 0))
    
    # 2. Inactivité
    last_p1 = state.get("last_play_date", {}).get(p1)
    last_p2 = state.get("last_play_date", {}).get(p2)
    days1 = (current_day - int(last_p1)) if last_p1 is not None else 365
    days2 = (current_day - int(last_p2)) if last_p2 is not None else 365
    
    if days1 > 45 or days2 > 45:
        multiplier *= 0.7
        flags.append("⚠️ Inactivité > 45j")
        
    # 3. Débutant
    cm1 = state.get("career_matches", {}).get(p1, 0)
    cm2 = state.get("career_matches", {}).get(p2, 0)
    if cm1 < 20 or cm2 < 20:
        multiplier *= 0.85
        flags.append("⚠️ Débutant < 20 matchs")
        
    # 4. Épuisement
    rr1 = state.get("recent_results", {}).get(p1, [])
    rr2 = state.get("recent_results", {}).get(p2, [])
    min1 = rr1[-1][8] if rr1 and len(rr1[-1]) > 8 else 0
    min2 = rr2[-1][8] if rr2 and len(rr2[-1]) > 8 else 0
    if (hours1 < 18 and min1 > 150) or (hours2 < 18 and min2 > 150):
        multiplier *= 0.8
        flags.append("⚠️ Épuisement extrême (<18h repos)")
        
    # 5. Volatilité
    cons1 = fe._consistency(rr1, 20)
    cons2 = fe._consistency(rr2, 20)
    # L'écart-type d'une distribution de Bernoulli (victoire/défaite) est max à 0.5 (50% de winrate).
    # La somme des deux est au maximum de 1.0. Un seuil de 0.35 pénalisait tout le monde.
    # On met le seuil à 0.90 pour viser le top 80e percentile des joueurs les plus imprévisibles.
    if (cons1 + cons2) > 0.90:
        multiplier *= 0.85
        flags.append("⚠️ Forte volatilité (Irréguliers)")
        
    # 6. Pre-GS Tanking
    t_lower = (tourney_name or "").lower()
    pre_gs_tourneys = ["winston-salem", "geneva", "lyon", "eastbourne", "mallorca", "adelaide", "auckland"]
    if t_level == "D" and any(x in t_lower for x in pre_gs_tourneys):
        rk1 = state.get("last_rank", {}).get(p1)
        rk2 = state.get("last_rank", {}).get(p2)
        if (rk1 and rk1 <= 30) or (rk2 and rk2 <= 30):
            multiplier *= 0.75
            flags.append("⚠️ Pre-GS Tanking potentiel")
            
    # 7. Bonus Motivation / Domicile
    def get_country(t):
        tl = t.lower()
        if any(x in tl for x in ["us open", "miami", "cincinnati", "indian wells", "washington", "dallas", "delray"]): return "USA"
        if any(x in tl for x in ["roland garros", "paris", "marseille", "montpellier", "lyon", "metz"]): return "FRA"
        if any(x in tl for x in ["madrid", "barcelona", "mallorca"]): return "ESP"
        if any(x in tl for x in ["australian open", "brisbane", "sydney"]): return "AUS"
        if any(x in tl for x in ["wimbledon", "queen", "eastbourne"]): return "GBR"
        if any(x in tl for x in ["rome", "turin", "naples"]): return "ITA"
        if any(x in tl for x in ["munich", "halle", "hamburg", "stuttgart"]): return "GER"
        return "UNKNOWN"
        
    t_country = get_country(t_lower)
    ioc1 = state.get("player_ioc_dict", {}).get(p1, "UNKNOWN")
    ioc2 = state.get("player_ioc_dict", {}).get(p2, "UNKNOWN")
    def1 = state.get("tourney_champions", {}).get(tourney_name) == p1
    def2 = state.get("tourney_champions", {}).get(tourney_name) == p2
    
    if ioc1 == t_country or ioc2 == t_country or def1 or def2:
        multiplier *= 1.10
        flags.append("✅ Bonus Domicile/Tenant du titre")
        
    final_score = min(100.0, base_score * multiplier)
    
    tier = "NO BET"
    if final_score >= 75: tier = "HIGH STAKE"
    elif final_score >= 55: tier = "MEDIUM STAKE"
    elif final_score >= 35: tier = "LOW STAKE"
    
    return {
        "confidence_index": round(final_score),
        "bet_tier": tier,
        "confidence_flags": flags
    }


# --------------------------------------------------------------------------
# Interface interactive
# --------------------------------------------------------------------------

SURFACES = ["Hard", "Clay", "Grass", "Carpet"]
LEVELS   = {"G": "Grand Slam", "M": "Masters 1000", "A": "ATP 500",
            "D": "ATP 250", "C": "Challenger", "F": "Finals"}
ROUNDS   = ["Q", "R128", "R64", "R32", "R16", "QF", "SF", "F"]


def select_player(prompt, known_players):
    while True:
        name = input(f"\n{prompt}: ").strip()
        if not name:
            continue
        matches = fuzzy_find(name, known_players)
        if not matches:
            print(f"  Aucun joueur trouve pour '{name}'.")
            continue
        if len(matches) == 1:
            confirm = input(f"  -> '{matches[0]}' ? (entree=oui) ").strip().lower()
            if confirm in ("", "o", "y", "oui", "yes"):
                return matches[0]
            continue
        print("  Joueurs trouves :")
        for i, m in enumerate(matches, 1):
            print(f"    {i}. {m}")
        choice = input("  Choix (numero) : ").strip()
        try:
            idx = int(choice) - 1
            if 0 <= idx < len(matches):
                return matches[idx]
        except ValueError:
            pass


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--circuit", default="atp", choices=["atp", "wta"], help="Circuit to predict (atp or wta)")
    args = parser.parse_args()
    
    state, model, feature_cols = load_resources(args.circuit)
    known_players = sorted(state["elo"].keys())

    print("\n" + "=" * 60)
    print("  PREDICTION DE MATCH TENNIS")
    print("=" * 60)
    print(f"  {len(known_players)} joueurs connus")
    print(f"  Dernier match enregistre : {state['date_min'].date()} + {state['last_day']} jours")
    print("=" * 60)

    while True:
        # Joueurs
        p1 = select_player("Joueur 1 (ex: Djokovic N.)", known_players)
        p2 = select_player("Joueur 2 (ex: Alcaraz C.)", known_players)
        if p1 == p2:
            print("  Les deux joueurs doivent etre differents.")
            continue

        # Surface
        print(f"\n  Surfaces : {', '.join(SURFACES)}")
        surf_in = ask("Surface", "Hard")
        surf = next((s for s in SURFACES if s.lower().startswith(surf_in.lower())), "Hard")

        # Niveau
        print(f"\n  Niveaux : " + "  ".join(f"{k}={v}" for k, v in LEVELS.items()))
        level = ask("Niveau", "M").upper()
        if level not in LEVELS:
            level = "M"

        # Tour
        print(f"\n  Tours : {', '.join(ROUNDS)}")
        round_ = ask("Tour", "QF").upper()
        if round_ not in ROUNDS:
            round_ = "QF"

        # Best-of
        bo_in = ask("Best-of (3 ou 5)", "3")
        best_of = 5 if bo_in.strip() == "5" else 3

        # Indoor
        ind_in = ask("Indoor ? (0=non, 1=oui)", "0")
        indoor = 1 if ind_in.strip() == "1" else 0

        # Date
        date_str = ask("Date du match (AAAA-MM-JJ)", datetime.date.today().isoformat())
        try:
            match_date = pd.Timestamp(date_str)
        except Exception:
            match_date = pd.Timestamp.today()

        # Classements optionnels
        print("\n  [Optionnel] Classements actuels (entree = dernier connu)")
        known_r1 = state["last_rank"].get(p1)
        known_r2 = state["last_rank"].get(p2)
        r1_in = ask_float(f"  Rank {p1}", known_r1)
        r2_in = ask_float(f"  Rank {p2}", known_r2)

        # Seeds
        seed1_in = ask("  Seed joueur 1 (entree si non tete de serie)", "")
        seed2_in = ask("  Seed joueur 2 (entree si non tete de serie)", "")
        seed1 = int(seed1_in) if seed1_in.isdigit() else None
        seed2 = int(seed2_in) if seed2_in.isdigit() else None

        # Statut d'entree (wildcard / qualifie)
        print("\n  [Optionnel] Statut d'entree (WC=wildcard, Q=qualifie, entree=aucun)")
        entry1_in = ask(f"  Statut {p1}", "").upper().strip()
        entry2_in = ask(f"  Statut {p2}", "").upper().strip()
        entry1 = entry1_in if entry1_in in ("WC", "Q") else None
        entry2 = entry2_in if entry2_in in ("WC", "Q") else None

        # Stats intra-tournoi (pertinentes a partir des 8es de finale)
        EARLY_ROUNDS = {"R128", "R64", "R32"}
        mt1 = mt2 = 0
        gw1 = gt1 = gw2 = gt2 = 0
        sw1 = st1 = sw2 = st2 = 0
        if round_ not in EARLY_ROUNDS:
            print(f"\n  [Optionnel] Stats dans le tournoi en cours (entree=0/inconnu)")
            print(f"  Exemple pour un quart de finale : {p1} a joue 3 matchs -> 3")
            mt1_in = ask_float(f"  Matchs joues par {p1} dans ce tournoi", 0)
            mt2_in = ask_float(f"  Matchs joues par {p2} dans ce tournoi", 0)
            mt1 = int(mt1_in) if mt1_in is not None else 0
            mt2 = int(mt2_in) if mt2_in is not None else 0
            if mt1 > 0 or mt2 > 0:
                print("  Jeux (ex: 'jeux gagnes / jeux total' — laisser vide si inconnu)")
                gw1_in = ask_float(f"  Jeux gagnes {p1}", 0)
                gt1_in = ask_float(f"  Jeux totaux {p1}", 0)
                gw2_in = ask_float(f"  Jeux gagnes {p2}", 0)
                gt2_in = ask_float(f"  Jeux totaux {p2}", 0)
                gw1 = int(gw1_in) if gw1_in else 0
                gt1 = int(gt1_in) if gt1_in else 0
                gw2 = int(gw2_in) if gw2_in else 0
                gt2 = int(gt2_in) if gt2_in else 0
                print("  Sets (ex: 'sets gagnes / sets total')")
                sw1_in = ask_float(f"  Sets gagnes {p1}", 0)
                st1_in = ask_float(f"  Sets totaux {p1}", 0)
                sw2_in = ask_float(f"  Sets gagnes {p2}", 0)
                st2_in = ask_float(f"  Sets totaux {p2}", 0)
                sw1 = int(sw1_in) if sw1_in else 0
                st1 = int(st1_in) if st1_in else 0
                sw2 = int(sw2_in) if sw2_in else 0
                st2 = int(st2_in) if st2_in else 0

        # Calcul
        feat = compute_features(
            p1=p1, p2=p2, surf=surf, t_level=level,
            round_=round_, best_of=best_of, indoor=indoor,
            match_date=match_date, state=state,
            rank1=r1_in, rank2=r2_in,
            seed1=seed1, seed2=seed2,
            entry1=entry1, entry2=entry2,
            matches_tourney1=mt1, matches_tourney2=mt2,
            games_won1=gw1, games_total1=gt1,
            games_won2=gw2, games_total2=gt2,
            sets_won1=sw1, sets_total1=st1,
            sets_won2=sw2, sets_total2=st2,
        )
        X   = build_row(feat, feature_cols)
        p_p1 = float(model.predict_proba(X)[0, 1])
        p_p2 = 1.0 - p_p1

        # Affichage
        print("\n" + "=" * 60)
        print("  RESULTAT")
        print("=" * 60)
        print(f"  {p1:<35}  {p_p1:.1%}")
        print(f"  {p2:<35}  {p_p2:.1%}")
        print(f"\n  Elo : {p1} = {state['elo'].get(p1, 1500):.0f}  |  "
              f"{p2} = {state['elo'].get(p2, 1500):.0f}")
        print(f"  Elo {surf} : {p1} = {state['elo_surface'].get(surf,{}).get(p1,1500):.0f}  |  "
              f"{p2} = {state['elo_surface'].get(surf,{}).get(p2,1500):.0f}")
        h12 = state["h2h"].get(p1, {}).get(p2, [0, 0])
        print(f"  H2H : {p1} {h12[0]}-{h12[1]} {p2}")

        # Cotes (optionnel)
        print("\n  [Optionnel] Cotes du bookmaker (entree pour sauter)")
        odds1_in = ask_float(f"  Cote {p1}")
        odds2_in = ask_float(f"  Cote {p2}")

        if odds1_in and odds2_in and odds1_in > 1 and odds2_in > 1:
            pm1, pm2 = remove_overround(odds1_in, odds2_in)
            edge1 = p_p1 - pm1
            edge2 = p_p2 - pm2
            print("\n  --- Analyse value bet (seuil edge > 3%) ---")
            print(f"  {'Joueur':<35}  {'p_modele':>8}  {'p_marche':>9}  {'Edge':>8}")
            print(f"  {p1:<35}  {p_p1:>8.1%}  {pm1:>9.1%}  {edge1:>+8.1%}"
                  + ("  <= VALUE BET!" if edge1 > 0.03 else ""))
            print(f"  {p2:<35}  {p_p2:>8.1%}  {pm2:>9.1%}  {edge2:>+8.1%}"
                  + ("  <= VALUE BET!" if edge2 > 0.03 else ""))
            best_e = max(edge1, edge2)
            if best_e > 0.03:
                bet_p   = p1 if edge1 > edge2 else p2
                bet_o   = odds1_in if edge1 > edge2 else odds2_in
                bet_e   = best_e
                print(f"\n  => PARIER SUR : {bet_p}  @ {bet_o:.2f}  (edge {bet_e:+.1%})")
            else:
                print("\n  => Pas de value bet (edge < 3%).")

        # Continuer ?
        again = input("\n  Nouveau match ? (entree=oui / n=non) ").strip().lower()
        if again in ("n", "non", "no", "q", "quit"):
            break

    print("\nAu revoir !")


if __name__ == "__main__":
    main()
