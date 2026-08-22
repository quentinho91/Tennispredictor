"""
05_predict_match.py — Prédiction de probabilité de victoire pour un match de tennis à venir.

USAGE :
    python src/05_predict_match.py
    python src/05_predict_match.py --circuit wta

PRÉREQUIS :
    1. python src/02_feature_engineering.py --circuit atp  -> data/processed/player_state_atp.pkl
    2. python src/03_train_model.py --circuit atp          -> data/processed/xgb_model_atp.json
                                                              data/processed/calibrator_atp.pkl
                                                              data/processed/feature_cols_atp.pkl
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
from difflib import get_close_matches, SequenceMatcher
import datetime
import importlib.util

# Ajouter le répertoire 'src' au sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent))

from markov_tennis import (
    p_game,
    p_set,
    p_match,
    estimate_point_probabilities,
    price_game_handicap,
    price_total_games
)

# --------------------------------------------------------------------------
# Chemins
# --------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent.parent
PROC_DIR = BASE_DIR / "data" / "processed"


def get_paths(circuit="atp"):
    return (
        PROC_DIR / f"player_state_{circuit}.pkl",
        PROC_DIR / f"xgb_model_{circuit}.json",
        PROC_DIR / f"feature_cols_{circuit}.pkl",
        PROC_DIR / f"calibrator_{circuit}.pkl"
    )


# --------------------------------------------------------------------------
# Import des helpers de 02_feature_engineering.py via importlib
# --------------------------------------------------------------------------
_fe_path = Path(__file__).parent / "02_feature_engineering.py"
_spec    = importlib.util.spec_from_file_location("fe", _fe_path)
fe       = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fe)

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
# Utilitaires & Recherche Floue
# --------------------------------------------------------------------------

def fuzzy_find(name, known, n=5, cutoff=0.6):
    """Recherche floue rapide et efficace dans la liste des joueurs connus."""
    name_low = name.lower().strip()
    tokens = name_low.split()

    # 1. Correspondance exacte
    exact = [k for k in known if k.lower() == name_low]
    if exact:
        return exact

    # 2. Correspondance par partie du nom / préfixe
    scored = []
    for k in known:
        parts = k.lower().split()
        score = 0
        for t in tokens:
            if len(t) < 2:
                continue
            for part in parts:
                if part == t:
                    score = max(score, 4)
                elif part.startswith(t):
                    score = max(score, 3)
                elif t in part:
                    score = max(score, 2)
        if score > 0:
            scored.append((score, k))

    if scored:
        scored.sort(key=lambda x: (-x[0], x[1]))
        top_candidates = [k for _, k in scored[:max(n * 4, 20)]]

        def _ratio(k):
            parts = k.lower().split()
            return max(SequenceMatcher(None, name_low, part).ratio() for part in parts)

        top_candidates_scored = [(k, _ratio(k)) for k in top_candidates]
        top_candidates_scored.sort(key=lambda x: -x[1])
        best_ratio = top_candidates_scored[0][1] if top_candidates_scored else 0
        threshold = max(0.35, best_ratio * 0.5)
        filtered = [k for k, r in top_candidates_scored if r >= threshold]
        return filtered[:n] if filtered else [k for k, _ in top_candidates_scored[:n]]

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


def display_value_bet_analysis(p1, p2, p_p1, p_p2, odds1, odds2, edge_threshold=0.03):
    """Affiche une analyse détaillée de rentabilité et value bet avec seuils et gestion Kelly."""
    if not (odds1 and odds2 and odds1 > 1.0 and odds2 > 1.0):
        return

    pm1, pm2 = remove_overround(odds1, odds2)
    ev1 = p_p1 * odds1 - 1.0
    ev2 = p_p2 * odds2 - 1.0
    edge1 = p_p1 - pm1
    edge2 = p_p2 - pm2

    cote_seuil1 = 1.0 / p_p1 if p_p1 > 0 else 999.0
    cote_seuil2 = 1.0 / p_p2 if p_p2 > 0 else 999.0

    print("\n" + "=" * 75)
    print("  ANALYSE VALUE BET & RENTABILITE")
    print("=" * 75)
    print(f"  {'Joueur':<24} {'Cote':>6} {'Cote Min':>9} {'P(Modele)':>10} {'P(Marche)':>10} {'Edge':>8} {'EV':>8}")
    print("  " + "-" * 88)
    
    is_vb1 = (edge1 >= edge_threshold and ev1 >= edge_threshold)
    is_vb2 = (edge2 >= edge_threshold and ev2 >= edge_threshold)

    statut1 = "[VALUE BET]" if is_vb1 else ("[EV TROP FAIBLE]" if edge1 > 0 else "[PAS DE VALUE]")
    statut2 = "[VALUE BET]" if is_vb2 else ("[EV TROP FAIBLE]" if edge2 > 0 else "[PAS DE VALUE]")

    print(f"  {p1:<24} {odds1:>6.2f} {cote_seuil1:>9.2f} {p_p1:>10.1%} {pm1:>10.1%} {edge1:>+7.1%} {ev1:>+7.1%}  {statut1}")
    print(f"  {p2:<24} {odds2:>6.2f} {cote_seuil2:>9.2f} {p_p2:>10.1%} {pm2:>10.1%} {edge2:>+7.1%} {ev2:>+7.1%}  {statut2}")
    print("  " + "-" * 88)

    if is_vb1 or is_vb2:
        bet_p = p1 if ev1 > ev2 else p2
        bet_o = odds1 if ev1 > ev2 else odds2
        bet_prob = p_p1 if ev1 > ev2 else p_p2
        bet_ev = ev1 if ev1 > ev2 else ev2
        bet_edge = edge1 if ev1 > ev2 else edge2
        
        b = bet_o - 1.0
        kelly_full = (bet_prob * bet_o - 1.0) / b if b > 0 else 0.0
        kelly_quarter = max(0.0, min(kelly_full * 0.25, 0.05)) # borné à 5% max de bankroll

        print(f"\n  >>> DECISION : *** VALUE BET DETECTE SUR {bet_p.upper()} ***")
        print(f"      - Cote offerte : {bet_o:.2f} (Cote minimale requise pour rentabilite : {1.0/bet_prob:.2f})")
        print(f"      - Esperance mathematique nette (EV) : {bet_ev:+.1%}")
        print(f"      - Avantage net sur le marche (Edge) : {bet_edge:+.1%}")
        print(f"      - Mise recommandee (Quarter-Kelly) : {kelly_quarter*100:.1f}% de votre bankroll")
    elif max(edge1, edge2) > 0 or max(ev1, ev2) > 0:
        best_p = p1 if ev1 > ev2 else p2
        best_ev = max(ev1, ev2)
        best_edge = max(edge1, edge2)
        best_prob = p_p1 if ev1 > ev2 else p_p2
        min_cote = (1.0 + edge_threshold) / best_prob
        print(f"\n  >>> DECISION : [PAS DE VALUE BET] (Rendement net EV = {best_ev:+.1%} sur {best_p} insuffisant, seuil requis = +{edge_threshold*100:.0f}%)")
        print(f"      - Explication : Bien qu'il y ait un avantage de {best_edge:+.1%} sur le marche, la marge du bookmaker absorbe le gain net.")
        print(f"      - Conseil : Ne pas parier. Attendre une cote d'au moins {min_cote:.2f} sur {best_p}.")
    else:
        print(f"\n  >>> DECISION : [AUCUN VALUE BET] Les cotes proposees sont trop basses par rapport aux probabilites reelles.")


# --------------------------------------------------------------------------
# Modèle Stacking Ensemble & Explicabilité SHAP
# --------------------------------------------------------------------------

import shap

_shap_explainer_cache = {}


def get_shap_explainer(tree_model):
    """Initialise et met en cache un TreeExplainer SHAP pour un modèle d'arbres."""
    model_id = id(tree_model)
    if model_id not in _shap_explainer_cache:
        try:
            _shap_explainer_cache[model_id] = shap.TreeExplainer(tree_model)
        except Exception as e:
            # Fallback
            _shap_explainer_cache[model_id] = None
    return _shap_explainer_cache[model_id]


class EnsemblePredictor:
    """
    Exécute le Stacking Multi-Modèles (XGBoost + LightGBM + CatBoost + Méta-Learner)
    avec support de la calibration et des prédictions individuelles.
    """
    def __init__(self, xgb_model, lgb_model=None, cat_model=None, meta_learner=None, calibrator=None, weights=None, calibrator_type=None):
        self.xgb_model = xgb_model
        self.lgb_model = lgb_model
        self.cat_model = cat_model
        self.meta_learner = meta_learner
        self.calibrator = calibrator
        self.calibrator_type = calibrator_type
        self.weights = weights or {"xgb": 0.5, "lgb": 0.25, "cat": 0.25}

    @property
    def is_ensemble(self):
        return self.lgb_model is not None and self.cat_model is not None and self.meta_learner is not None

    def _temperature_scale(self, p_raw, T):
        p_raw = np.clip(p_raw, 1e-6, 1.0 - 1e-6)
        logits = np.log(p_raw / (1.0 - p_raw)) / T
        return np.clip(1.0 / (1.0 + np.exp(-logits)), 0.001, 0.999)

    def predict_proba(self, X):
        p_xgb = self.xgb_model.predict_proba(X)[:, 1]

        if self.is_ensemble:
            p_lgb = self.lgb_model.predict_proba(X)[:, 1]
            p_cat = self.cat_model.predict_proba(X)[:, 1]
            M = np.column_stack([p_xgb, p_lgb, p_cat])
            p_raw = self.meta_learner.predict_proba(M)[:, 1]
        else:
            p_raw = p_xgb

        # Calibration
        if self.calibrator is None:
            p_final = p_raw
        elif isinstance(self.calibrator, dict):
            cal_type = self.calibrator.get("type", "")
            if cal_type == "temperature_scaling":
                T = self.calibrator.get("temperature", 1.0)
                p_final = self._temperature_scale(p_raw, T)
            elif cal_type == "bucket":
                cals = self.calibrator.get("calibrators", {})
                fallback = self.calibrator.get("fallback")
                p_final = np.copy(p_raw)
                for (lo, hi), cal in cals.items():
                    mask = (p_raw >= lo) & (p_raw < hi)
                    if cal is not None and np.any(mask):
                        p_final[mask] = cal.predict(p_raw[mask])
                    elif fallback is not None and np.any(mask):
                        p_final[mask] = fallback.predict(p_raw[mask])
                p_final = np.clip(p_final, 0.001, 0.999)
            else:
                p_final = p_raw
        else:
            p_final = np.clip(self.calibrator.predict(p_raw), 0.001, 0.999)

        p1_val = float(p_final[0]) if hasattr(p_final, "__len__") else float(p_final)
        p0_val = 1.0 - p1_val
        return np.array([[p0_val, p1_val]])

    def get_individual_probas(self, X):
        p_xgb = float(self.xgb_model.predict_proba(X)[0, 1])
        p_lgb = float(self.lgb_model.predict_proba(X)[0, 1]) if self.lgb_model else p_xgb
        p_cat = float(self.cat_model.predict_proba(X)[0, 1]) if self.cat_model else p_xgb
        p_ens = float(self.predict_proba(X)[0, 1])
        return {
            "xgb": round(p_xgb * 100, 1),
            "lgb": round(p_lgb * 100, 1),
            "cat": round(p_cat * 100, 1),
            "ensemble": round(p_ens * 100, 1),
        }

    @property
    def calibrator_name(self):
        if self.calibrator is None:
            return "Non"
        if isinstance(self.calibrator, dict):
            t = self.calibrator.get("type", "unknown")
            if t == "temperature_scaling":
                T = self.calibrator.get("temperature", 1.0)
                return f"TemperatureScaling (T={T:.2f})"
            return t.capitalize()
        return type(self.calibrator).__name__


# Définition des 8 Piliers d'Explicabilité SHAP
SHAP_PILLARS = {
    "serve": {
        "title": "Service & Puissance",
        "icon": "🎾",
        "keywords": ["serve_elo", "1stIn", "1stWon", "2ndWon", "svpt", "ace", "df_", "bpSaved", "hold_diff", "_pa_m", "serve_momentum"],
        "desc_pos": "Efficacité supérieure sur engagement (Aces / 1ères balles)",
        "desc_neg": "Solidité et points gratuits au service en retrait"
    },
    "return_game": {
        "title": "Retour & Pression",
        "icon": "🔄",
        "keywords": ["return_elo", "return_pts_won", "bp_converted", "bpFaced", "return_momentum"],
        "desc_pos": "Agressivité en retour et conversion des balles de break",
        "desc_neg": "Difficulté à neutraliser les jeux de service adverses"
    },
    "level_rank": {
        "title": "Niveau Global & Classement",
        "icon": "🏆",
        "keywords": ["elo_diff", "rank_diff", "peak_rank", "rank_points", "seed_number", "experience"],
        "desc_pos": "Supériorité hiérarchique au classement général et niveau ATP/WTA",
        "desc_neg": "Écart de calibre et d'expérience globale sur le circuit"
    },
    "form_momentum": {
        "title": "Forme & Dynamique Récente",
        "icon": "🔥",
        "keywords": ["form10", "form20", "form365d", "streak", "elo_trend", "game_dominance", "elo_momentum"],
        "desc_pos": "Dynamique victorieuse et momentum élevé sur les dernières semaines",
        "desc_neg": "Manque de rythme ou série de résultats mitigée"
    },
    "surface_speed": {
        "title": "Affinité Surface & Vitesse",
        "icon": "🟦",
        "keywords": ["surface_elo", "surface_winrate", "surface_experience", "surface_bias", "cpi", "speed", "indoor", "altitude"],
        "desc_pos": "Excellents repères et adéquation avec la surface et la vitesse de balle",
        "desc_neg": "Moindre efficacité sur ce type de surface spécifique"
    },
    "fatigue": {
        "title": "Fraîcheur & Physique",
        "icon": "⚡",
        "keywords": ["matches_last", "hours_played", "rest_days", "fatigue", "retirement", "sets_7d", "travel_strain"],
        "desc_pos": "Fraîcheur physique et temps de récupération optimal",
        "desc_neg": "Charge de temps de jeu élevée ou alerte physique récente"
    },
    "mental": {
        "title": "Mental & Moments Clés",
        "icon": "🧠",
        "keywords": ["tiebreak", "decided_set", "comeback", "giant_killer", "upset_rate", "late_round"],
        "desc_pos": "Efficacité redoutable dans le 'money time' (tie-breaks / sets décisifs)",
        "desc_neg": "Vulnérabilité dans la gestion des fins de sets sous haute pression"
    },
    "h2h": {
        "title": "Face-à-Face (H2H) Direct",
        "icon": "⚔️",
        "keywords": ["h2h", "last_h2h", "kryptonite"],
        "desc_pos": "Ascendant psychologique et avantage tactique dans les duels directs",
        "desc_neg": "Matchup tactique historiquement défavorable contre ce joueur"
    }
}


def compute_match_shap_explanation(X_row, ensemble_predictor, feature_cols, p1="Joueur 1", p2="Joueur 2"):
    """
    Calcule les contributions SHAP (TreeSHAP) pour le match et les agrège
    en 8 piliers tennistiques avec phrases explicatives et jauges en pourcentage.
    """
    explainer = get_shap_explainer(ensemble_predictor.xgb_model)
    raw_shap_dict = {}

    if explainer is not None:
        try:
            shap_vals = explainer.shap_values(X_row)
            # En classification binaire, TreeExplainer peut renvoyer un tableau 1D ou 2D
            if isinstance(shap_vals, list):
                # 2 sorties (classe 0 et classe 1) -> on prend la classe 1
                sv = shap_vals[1][0] if len(shap_vals) > 1 else shap_vals[0][0]
            elif len(shap_vals.shape) == 2:
                sv = shap_vals[0]
            else:
                sv = shap_vals

            for idx, col in enumerate(feature_cols):
                if idx < len(sv):
                    raw_shap_dict[col] = float(sv[idx])
        except Exception:
            raw_shap_dict = {}

    # Fallback si SHAP non disponible ou nul : heuristique basée sur les importances
    if not raw_shap_dict:
        for col in feature_cols:
            val = float(X_row[col].iloc[0]) if col in X_row.columns and not pd.isna(X_row[col].iloc[0]) else 0.0
            raw_shap_dict[col] = val * 0.005

    # Agrégation des valeurs SHAP par pilier thématique
    pillar_scores = {k: 0.0 for k in SHAP_PILLARS}
    pillar_contributors = {k: [] for k in SHAP_PILLARS}

    for col, shap_val in raw_shap_dict.items():
        assigned = False
        col_lower = col.lower()
        for p_key, p_meta in SHAP_PILLARS.items():
            if any(kw.lower() in col_lower for kw in p_meta["keywords"]):
                pillar_scores[p_key] += shap_val
                if abs(shap_val) > 0.005:
                    pillar_contributors[p_key].append((col, shap_val))
                assigned = True
                break
        if not assigned:
            # Rangement par défaut dans niveau/classement
            pillar_scores["level_rank"] += shap_val * 0.5

    # Normalisation des scores en points de pourcentage d'impact probabilité (ex: +6.4% pour p1)
    total_abs_shap = sum(abs(v) for v in pillar_scores.values()) + 1e-9
    scaling_factor = 25.0  # Plage d'impact réaliste (environ ±15-20% max par composante)

    pillars_output = []
    p1_drivers = []
    p2_drivers = []

    for p_key, p_meta in SHAP_PILLARS.items():
        raw_sum = pillar_scores[p_key]
        # Impact net en pourcentage
        impact_pct = round(float(np.clip(raw_sum * scaling_factor, -15.0, 15.0)), 1)
        favorable_to = "p1" if impact_pct >= 0 else "p2"
        favorable_player = p1 if favorable_to == "p1" else p2
        desc = p_meta["desc_pos"] if favorable_to == "p1" else p_meta["desc_neg"]

        entry = {
            "id": p_key,
            "title": p_meta["title"],
            "icon": p_meta["icon"],
            "impact_pct": impact_pct,
            "abs_impact": abs(impact_pct),
            "favorable_to": favorable_to,
            "favorable_player": favorable_player,
            "description": f"{favorable_player} : {desc} ({'+' if impact_pct > 0 else ''}{impact_pct}%)"
        }
        pillars_output.append(entry)

        if abs(impact_pct) >= 1.2:
            if favorable_to == "p1":
                p1_drivers.append((abs(impact_pct), f"{p_meta['icon']} {p_meta['title']} (+{impact_pct}%) : {p_meta['desc_pos']}"))
            else:
                p2_drivers.append((abs(impact_pct), f"{p_meta['icon']} {p_meta['title']} (+{abs(impact_pct)}%) : {p_meta['desc_pos']}"))

    # Tri par importance
    pillars_output.sort(key=lambda x: x["abs_impact"], reverse=True)
    p1_drivers.sort(key=lambda x: x[0], reverse=True)
    p2_drivers.sort(key=lambda x: x[0], reverse=True)

    top_p1_text = [d[1] for d in p1_drivers[:3]]
    top_p2_text = [d[1] for d in p2_drivers[:3]]

    # Synthèse textuelle en clair
    if pillars_output:
        top_driver = pillars_output[0]
        lead_player = p1 if top_driver["impact_pct"] > 0 else p2
        summary_text = f"Le facteur le plus déterminant selon l'IA est <b>{top_driver['title']}</b> (impact de <b>{abs(top_driver['impact_pct'])}%</b> en faveur de <b>{lead_player}</b>)."
    else:
        summary_text = "Les indicateurs de forme et de niveau sont très équilibrés."

    return {
        "pillars": pillars_output,
        "top_p1_factors": top_p1_text,
        "top_p2_factors": top_p2_text,
        "summary_text": summary_text,
        "raw_top_features": sorted([(k, round(v, 4)) for k, v in raw_shap_dict.items()], key=lambda x: abs(x[1]), reverse=True)[:8]
    }


def load_resources(circuit="atp"):
    c = circuit.lower()
    state_path, model_path, fcols_path, calib_path = get_paths(c)
    ensemble_path = PROC_DIR / f"ensemble_{c}.pkl"

    if not state_path.exists():
        raise FileNotFoundError(f"[ERREUR] Fichier manquant : {state_path}\n -> Lance d'abord : python src/02_feature_engineering.py --circuit {c}")

    with open(state_path, "rb") as f:
        state = pickle.load(f)
    state["circuit"] = c

    if not fcols_path.exists():
        raise FileNotFoundError(f"[ERREUR] Fichier manquant : {fcols_path}\n -> Lance d'abord : python src/03_train_model.py --circuit {c}")
    feature_cols = joblib.load(fcols_path)

    # 1. Tentative de chargement de l'ensemble Stacking complet
    if ensemble_path.exists():
        try:
            bundle = joblib.load(ensemble_path)
            predictor = EnsemblePredictor(
                xgb_model=bundle["xgb_model"],
                lgb_model=bundle.get("lgb_model"),
                cat_model=bundle.get("cat_model"),
                meta_learner=bundle.get("meta_learner"),
                calibrator=bundle.get("calibrator"),
                weights=bundle.get("weights"),
                calibrator_type=bundle.get("calibrator_type")
            )
            print(f"OK [Ensemble Stacking (XGB+LGB+CAT) {c.upper()}] ({len(state['elo'])} joueurs, {len(feature_cols)} features, Calibrator={predictor.calibrator_name})")
            return state, predictor, feature_cols
        except Exception as e:
            print(f"[Avertissement] Échec chargement ensemble: {e}, fallback sur XGBoost...")

    # 2. Fallback classique XGBoost pur
    if not model_path.exists():
        raise FileNotFoundError(f"[ERREUR] Modèle manquant : {model_path}\n -> Lance d'abord : python src/03_train_model.py --circuit {c}")

    xgb_model = xgb.XGBClassifier()
    xgb_model.load_model(str(model_path))
    calibrator = joblib.load(calib_path) if calib_path.exists() else None
    predictor = EnsemblePredictor(xgb_model=xgb_model, calibrator=calibrator)
    print(f"OK [XGBoost {c.upper()}] ({len(state['elo'])} joueurs, {len(feature_cols)} features, Calibrator={predictor.calibrator_name})")
    return state, predictor, feature_cols


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
    h2h_hist    = state.get("h2h_history", {})
    last_h2h_d  = state.get("last_h2h_day", {})
    last_h2h_r  = state.get("last_h2h_result", {})
    srv_hist    = state.get("serve_return_hist", {})

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
            if hist:
                return sum(hist) / len(hist)
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

    get_decayed_elo = getattr(fe, "get_decayed_elo", lambda b, d, ld: b)
    last_p1 = last_pd.get(p1)
    last_p2 = last_pd.get(p2)

    # Elo (avec dépréciation d'inactivité)
    e1  = get_decayed_elo(elo.get(p1, ELO_INIT), day, last_p1)
    e2  = get_decayed_elo(elo.get(p2, ELO_INIT), day, last_p2)
    es1 = get_decayed_elo(elo_surf.get(surf, {}).get(p1, ELO_INIT), day, last_p1)
    es2 = get_decayed_elo(elo_surf.get(surf, {}).get(p2, ELO_INIT), day, last_p2)
    esw1 = get_decayed_elo(elo_surf_w.get(surf, {}).get(p1, ELO_INIT), day, last_p1)
    esw2 = get_decayed_elo(elo_surf_w.get(surf, {}).get(p2, ELO_INIT), day, last_p2)
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
    hours1 = rest1_days * 24.0
    hours2 = rest2_days * 24.0

    feat["hours_rest_diff"] = hours1 - hours2
    feat["short_rest_p1"] = 1.0 if hours1 < 20 else 0.0
    feat["short_rest_p2"] = 1.0 if hours2 < 20 else 0.0
    feat["is_night_match"] = 0.0

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

    # Travel & Schedule Strain
    compute_strain = getattr(fe, "compute_travel_strain", None)
    if compute_strain:
        last_tc = state.get("last_tourney_country", {})
        last_tid = state.get("last_tourney_id", {})
        strain1, short_sc1 = compute_strain(p1, day, tourney_name, t_country, surf, last_tid, last_pd, last_tc, last_surf)
        strain2, short_sc2 = compute_strain(p2, day, tourney_name, t_country, surf, last_tid, last_pd, last_tc, last_surf)
        feat["travel_strain_diff"] = strain1 - strain2
        feat["short_rest_surface_change_diff"] = short_sc1 - short_sc2
    else:
        feat["travel_strain_diff"] = 0.0
        feat["short_rest_surface_change_diff"] = 0.0

    # Game Dominance EMA
    def _calc_ema(hist, n):
        if not hist:
            return 0.50
        k = min(len(hist), n)
        recent = hist[-k:]
        alpha = 2.0 / (n + 1.0)
        weights = [(1.0 - alpha)**(k - 1 - i) for i in range(k)]
        return float(np.average(recent, weights=weights))

    g_hist = state.get("game_dominance_hist", {})
    feat["game_dominance_ema5_diff"] = _calc_ema(g_hist.get(p1, []), 5) - _calc_ema(g_hist.get(p2, []), 5)
    feat["game_dominance_ema10_diff"] = _calc_ema(g_hist.get(p1, []), 10) - _calc_ema(g_hist.get(p2, []), 10)

    p1_h = state.get("last_hand", {}).get(p1, 'R')
    p2_h = state.get("last_hand", {}).get(p2, 'R')
    wr1_vs_L = _speed_wr(vs_lefty_results, p1) if p2_h == 'L' else 0.5
    wr2_vs_L = _speed_wr(vs_lefty_results, p2) if p1_h == 'L' else 0.5
    feat["kryptonite_diff"] = wr1_vs_L - wr2_vs_L

    is_def1 = 1 if tourney_champions.get(tourney_name) == p1 else 0
    is_def2 = 1 if tourney_champions.get(tourney_name) == p2 else 0
    feat["is_defending_champion_diff"] = is_def1 - is_def2

    # Stats service / retour
    sh1 = srv_hist.get(p1, [])
    sh2 = srv_hist.get(p2, [])
    r5_1, r20_1 = _rolling_stats(sh1)
    r5_2, r20_2 = _rolling_stats(sh2)
    for k_idx, k in enumerate(SERVE_KEYS):
        feat[f"{k}_20_diff"] = r20_1[k_idx] - r20_2[k_idx]

    sb1 = _surf_bias(rr1, day, surf)
    sb2 = _surf_bias(rr2, day, surf)
    feat["surface_bias_diff"] = sb1 - sb2

    arch1 = _get_arch(r20_1, sb1)
    arch2 = _get_arch(r20_2, sb2)
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

    # Sets & tournoi
    feat["sets_7d_diff"]              = _sets_recent(rr1, day, 7)  - _sets_recent(rr2, day, 7)
    feat["sets_14d_diff"]             = _sets_recent(rr1, day, 14) - _sets_recent(rr2, day, 14)
    feat["sets_tourney_diff"]         = sets_won1 - sets_won2
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

    # Features fatigue & momentum
    feat["matches_last_3d_diff"]      = _cnt_recent(rr1, day, 3) - _cnt_recent(rr2, day, 3)
    feat["matches_last_7d_diff"]      = _cnt_recent(rr1, day, 7) - _cnt_recent(rr2, day, 7)
    feat["hours_played_last_14d_diff"]= (_avg_min(rr1, 10) * _cnt_recent(rr1, day, 14) - _avg_min(rr2, 10) * _cnt_recent(rr2, day, 14)) / 60.0
    feat["form20_surface_diff"]       = _wr_surf(rr1, surf, 20) - _wr_surf(rr2, surf, 20)
    feat["serve_momentum_diff"]       = 0.0
    feat["return_momentum_diff"]      = 0.0
    feat["elo_momentum_diff"]         = 0.0

    # Serve & Return Elo + Markov Point-by-Point
    serve_elo = state.get("serve_elo", {})
    return_elo = state.get("return_elo", {})
    serve_elo_surf = state.get("serve_elo_surface", {})
    return_elo_surf = state.get("return_elo_surface", {})

    # Serve & Return Elo (avec dépréciation d'inactivité)
    se1  = get_decayed_elo(serve_elo.get(p1, ELO_INIT), day, last_p1)
    se2  = get_decayed_elo(serve_elo.get(p2, ELO_INIT), day, last_p2)
    re1  = get_decayed_elo(return_elo.get(p1, ELO_INIT), day, last_p1)
    re2  = get_decayed_elo(return_elo.get(p2, ELO_INIT), day, last_p2)
    ses1 = get_decayed_elo(serve_elo_surf.get(surf, {}).get(p1, se1), day, last_p1)
    ses2 = get_decayed_elo(serve_elo_surf.get(surf, {}).get(p2, se2), day, last_p2)
    res1 = get_decayed_elo(return_elo_surf.get(surf, {}).get(p1, re1), day, last_p1)
    res2 = get_decayed_elo(return_elo_surf.get(surf, {}).get(p2, re2), day, last_p2)

    feat["serve_elo_diff"]           = se1 - se2
    feat["return_elo_diff"]          = re1 - re2
    feat["serve_elo_surface_diff"]   = ses1 - ses2
    feat["return_elo_surface_diff"]  = res1 - res2

    # Point prob estimation & Markov match simulation
    pa_m, pb_m = estimate_point_probabilities(ses1, res2, ses2, res1, surface=surf, circuit=state.get("circuit", "atp"))
    bo_i = int(best_of) if best_of in (3, 5) else 3

    # -----------------------------------------------------------------------
    # FRESHNESS BOOST : Ajustement de forme récente sur les probas Markov
    # -----------------------------------------------------------------------
    # Concept : Les Elo et stats sont figés à l'entraînement. Si un joueur
    # est en grande forme ou revient de blessure, on ajuste pa_m / pb_m
    # en se basant sur ses 5 derniers matchs (résultats + dominance de jeux).
    #
    # Méthode :
    # 1. Form Score = moyenne pondérée des résultats W/L sur les 5 derniers matchs
    #    (poids décroissants : match le + récent = poids 5, le + vieux = poids 1)
    # 2. Game Dominance EMA = ratio de jeux gagnés sur les 5 derniers matchs
    # 3. Freshness Delta = combinaison des deux, normalisée à ±MAX_BOOST
    # 4. Appliqué sur pa_m et pb_m : pa_boosted = sigmoid(logit(pa_m) + delta)
    # Borne max : ±0.025 (soit ≈ ±2.5% de point win prob, ≈ ±4-5% de proba match)

    def _form_delta(recent_results_list, gd_hist, last_surf, surf, n=5, max_boost=0.022):
        """Calcule un delta de forme à appliquer sur la proba de point de service."""
        if not recent_results_list:
            return 0.0

        # -- Résultats W/L pondérés (5 derniers matchs, poids décroissants) --
        results = recent_results_list[-n:]
        n_avail = len(results)
        weights = list(range(1, n_avail + 1))  # [1, 2, 3, 4, 5]
        total_w = sum(weights)
        wr_score = sum(w * (1.0 if r[1] else 0.0) for w, r in zip(weights, results)) / total_w
        # wr_score ∈ [0, 1] ; neutre = 0.5
        form_raw = (wr_score - 0.5) * 2.0  # ∈ [-1, 1]

        # -- Dominance de jeux EMA (5 derniers matchs) --
        gd = gd_hist[-n:] if gd_hist else []
        if gd:
            gd_weights = list(range(1, len(gd) + 1))
            gd_total_w = sum(gd_weights)
            gd_score = sum(w * v for w, v in zip(gd_weights, gd)) / gd_total_w
            # gd_score ∈ [0, 1] ; neutre = ~0.55 (les gagnants font naturellement +55%)
            gd_raw = (gd_score - 0.55) * 2.0  # ∈ [-1, 1] centré sur le neutre réel
        else:
            gd_raw = 0.0

        # -- Bonus sur-surface : si le joueur est en forme ET joue sa surface préférée --
        surf_bonus = 0.0
        if last_surf and last_surf == surf:
            surf_bonus = 0.15 * form_raw  # amplification de 15% si continuité de surface

        # -- Combinaison pondérée --
        combined = 0.55 * form_raw + 0.35 * gd_raw + 0.10 * surf_bonus

        # -- Borne symétrique --
        return float(np.clip(combined * max_boost, -max_boost, max_boost))

    gd_hist_p1 = state.get("game_dominance_hist", {}).get(p1, [])
    gd_hist_p2 = state.get("game_dominance_hist", {}).get(p2, [])
    last_surf_p1 = state.get("last_surface", {}).get(p1)
    last_surf_p2 = state.get("last_surface", {}).get(p2)

    delta_p1 = _form_delta(rr1, gd_hist_p1, last_surf_p1, surf)
    delta_p2 = _form_delta(rr2, gd_hist_p2, last_surf_p2, surf)

    # Application via logit (préserve la cohérence probabiliste)
    def _apply_delta(p, delta):
        p = float(np.clip(p, 1e-4, 1 - 1e-4))
        logit = np.log(p / (1.0 - p))
        # Multiplicateur calibré pour produire ≈ ±2-3% de proba match max
        return float(np.clip(1.0 / (1.0 + np.exp(-(logit + delta * 2.2))), 1e-4, 1 - 1e-4))

    pa_boosted = _apply_delta(pa_m, delta_p1)
    pb_boosted = _apply_delta(pb_m, delta_p2)

    # Si les deux joueurs ont la même forme, les deltas s'annulent → neutre
    m_res = p_match(pa_boosted, pb_boosted, best_of=bo_i)

    # Sauvegarde des deltas pour affichage
    feat["_form_delta_p1"]  = round(delta_p1 * 100, 2)
    feat["_form_delta_p2"]  = round(delta_p2 * 100, 2)
    feat["_pa_m_raw"]       = pa_m
    feat["_pb_m_raw"]       = pb_m
    feat["_pa_m_boosted"]   = pa_boosted
    feat["_pb_m_boosted"]   = pb_boosted

    feat["markov_p_win"]          = m_res["proba_a"]
    feat["markov_hold_diff"]      = m_res["hold_proba_a"] - m_res["hold_proba_b"]
    feat["markov_expected_games"] = m_res["expected_total_games"]

    # Markov analytics pour l'affichage
    feat["_markov_res"]           = m_res
    feat["_pa_m"]                 = pa_boosted
    feat["_pb_m"]                 = pb_boosted
    feat["_p1_serve_elo_surf"]    = ses1
    feat["_p2_serve_elo_surf"]    = ses2
    feat["_p1_return_elo_surf"]   = res1
    feat["_p2_return_elo_surf"]   = res2

    # Catégoriel
    feat["surface"]       = surf
    feat["tourney_level"] = t_level
    feat["round"]         = round_
    feat["indoor"]        = indoor
    feat["best_of"]       = best_of

    return feat



def build_row(feat, feature_cols):
    """Construit un DataFrame 1-ligne avec le même encodage que l'entraînement."""
    df = pd.DataFrame([feat])
    cat_cols = ["surface", "tourney_level", "round", "hand_matchup", "indoor"]
    df = pd.get_dummies(df, columns=[c for c in cat_cols if c in df.columns])
    cat_prefixes = tuple(c + "_" for c in cat_cols)
    for col in feature_cols:
        if col not in df.columns:
            if col.startswith(cat_prefixes):
                df[col] = 0.0
            else:
                df[col] = np.nan
    return df[feature_cols].astype(float)


# --------------------------------------------------------------------------
# Interface CLI interactive
# --------------------------------------------------------------------------

SURFACES = ["Hard", "Clay", "Grass", "Carpet"]
LEVELS   = {"G": "Grand Slam", "M": "Masters 1000", "A": "ATP 500 / WTA 500",
            "D": "ATP 250 / WTA 250", "C": "Challenger / WTA 125", "F": "Finals"}
ROUNDS   = ["Q", "R128", "R64", "R32", "R16", "QF", "SF", "F"]


def select_player(prompt, known_players):
    while True:
        name = input(f"\n{prompt}: ").strip()
        if not name:
            continue
        matches = fuzzy_find(name, known_players)
        if not matches:
            print(f"  Aucun joueur trouvé pour '{name}'.")
            continue
        if len(matches) == 1:
            confirm = input(f"  -> '{matches[0]}' ? (entrée=oui) ").strip().lower()
            if confirm in ("", "o", "y", "oui", "yes"):
                return matches[0]
            continue
        print("  Joueurs trouvés :")
        for i, m in enumerate(matches, 1):
            print(f"    {i}. {m}")
        choice = input("  Choix (numéro) : ").strip()
        try:
            idx = int(choice) - 1
            if 0 <= idx < len(matches):
                return matches[idx]
        except ValueError:
            pass


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Prédiction de match de tennis avec XGBoost pur et Markov.")
    parser.add_argument("--circuit", default="atp", choices=["atp", "wta"], help="Circuit (atp ou wta)")
    parser.add_argument("--p1", default=None, help="Nom du joueur 1 (ex: 'Djokovic N.')")
    parser.add_argument("--p2", default=None, help="Nom du joueur 2 (ex: 'Alcaraz C.')")
    parser.add_argument("--surface", default="Hard", help="Surface (Hard, Clay, Grass, Carpet)")
    parser.add_argument("--tournament", default="Tournament", help="Nom du tournoi")
    parser.add_argument("--level", default="M", choices=["G", "M", "A", "C", "F"], help="Niveau (G=Grand Chelem, M=Masters 1000, A=ATP 500/250, C=Challenger)")
    parser.add_argument("--round", default="QF", help="Tour (F, SF, QF, R16, R32, R64, R128, etc.)")
    parser.add_argument("--best-of", type=int, default=3, choices=[3, 5], help="Format (3 ou 5 sets)")
    parser.add_argument("--indoor", type=int, default=0, choices=[0, 1], help="Indoor (0=non, 1=oui)")
    parser.add_argument("--odds1", type=float, default=None, help="Cote joueur 1")
    parser.add_argument("--odds2", type=float, default=None, help="Cote joueur 2")
    args = parser.parse_args()

    state, model, feature_cols = load_resources(args.circuit)
    known_players = sorted(state["elo"].keys())

    # Mode direct (CLI)
    if args.p1 and args.p2:
        def find_best_match(name):
            exact = [p for p in known_players if p.lower() == name.lower()]
            if exact: return exact[0]
            contains = [p for p in known_players if name.lower() in p.lower()]
            if contains: return contains[0]
            return name

        p1 = find_best_match(args.p1)
        p2 = find_best_match(args.p2)
        surf = args.surface.capitalize()
        level = args.level
        round_ = args.round.upper()
        best_of = args.best_of
        indoor = args.indoor

        print("\n" + "=" * 60)
        print(f"  PRÉDICTION DE MATCH TENNIS ({args.circuit.upper()})")
        print("=" * 60)
        print(f"  Affiche  : {p1} vs {p2}")
        print(f"  Contexte : {args.tournament} | {surf} | {LEVELS.get(level, level)} | Tour: {round_} | Best-of {best_of} | Indoor: {'Oui' if indoor else 'Non'}")

        feat = compute_features(
            p1=p1, p2=p2, surf=surf, t_level=level, round_=round_,
            best_of=best_of, indoor=indoor, tourney_name=args.tournament,
            match_date=pd.Timestamp.today(), state=state,
        )
        row = build_row(feat, feature_cols)
        p_raw = model.predict_proba(row)[0]
        p_p1, p_p2 = float(p_raw[1]), float(p_raw[0])
        m_r = feat.get("_markov_result", {})

        print("\n" + "-" * 60)
        print(f"  PROBABILITÉ DE VICTOIRE :")
        print(f"    • {p1:<28} : {p_p1*100:>6.2f}%  (Cote juste : {1/p_p1:.2f})")
        print(f"    • {p2:<28} : {p_p2*100:>6.2f}%  (Cote juste : {1/p_p2:.2f})")
        print("-" * 60)

        print(f"\n  --- Détails et Dynamique des Joueurs ---")
        print(f"  Elo Global  : {p1} = {state['elo'].get(p1, 1500):.0f}  |  {p2} = {state['elo'].get(p2, 1500):.0f}")
        print(f"  Elo {surf:<7}: {p1} = {state['elo_surface'].get(surf,{}).get(p1,1500):.0f}  |  {p2} = {state['elo_surface'].get(surf,{}).get(p2,1500):.0f}")
        if m_r:
            print(f"  Point Service    : {p1} = {feat.get('_pa_m', 0.64):.1%}  |  {p2} = {feat.get('_pb_m', 0.64):.1%}")
            print(f"  Total Jeux Prévu : {m_r.get('expected_total_games', 22.5):.1f} jeux")
        h12 = state["h2h"].get(p1, {}).get(p2, [0, 0])
        print(f"  H2H Historique   : {p1} {h12[0]}-{h12[1]} {p2}")

        if args.odds1 and args.odds2 and args.odds1 > 1 and args.odds2 > 1:
            display_value_bet_analysis(p1, p2, p_p1, p_p2, args.odds1, args.odds2)
        return

    # Mode interactif
    print("\n" + "=" * 60)
    print(f"  PRÉDICTION DE MATCH TENNIS (XGBOOST {args.circuit.upper()})")
    print("=" * 60)
    print(f"  {len(known_players)} joueurs connus")
    print(f"  Dernier match enregistré : {state['date_min'].date()} + {state['last_day']} jours")
    print("=" * 60)

    while True:
        p1 = select_player("Joueur 1 (ex: Djokovic N.)", known_players)
        p2 = select_player("Joueur 2 (ex: Alcaraz C.)", known_players)
        if p1 == p2:
            print("  Les deux joueurs doivent être différents.")
            continue

        print(f"\n  Surfaces : {', '.join(SURFACES)}")
        surf_in = ask("Surface", "Hard")
        surf = next((s for s in SURFACES if s.lower().startswith(surf_in.lower())), "Hard")

        tourney_in = ask("Tournoi (ex: Cincinnati, US Open, Madrid, Rome, Roland Garros)", "Cincinnati")
        tourney_name = tourney_in if tourney_in else "Tournament"

        print(f"\n  Niveaux : " + "  ".join(f"{k}={v}" for k, v in LEVELS.items()))
        level = ask("Niveau", "M").upper()
        if level not in LEVELS:
            level = "M"

        print(f"\n  Tours : {', '.join(ROUNDS)}")
        round_ = ask("Tour", "QF").upper()
        if round_ not in ROUNDS:
            round_ = "QF"

        bo_in = ask("Best-of (3 ou 5)", "3")
        best_of = 5 if bo_in.strip() == "5" else 3

        ind_in = ask("Indoor ? (0=non, 1=oui)", "0")
        indoor = 1 if ind_in.strip() == "1" else 0

        date_str = ask("Date du match (AAAA-MM-JJ)", datetime.date.today().isoformat())
        try:
            match_date = pd.Timestamp(date_str)
        except Exception:
            match_date = pd.Timestamp.today()

        r1_in = state["last_rank"].get(p1)
        r2_in = state["last_rank"].get(p2)

        feat = compute_features(
            p1=p1, p2=p2, surf=surf, t_level=level,
            round_=round_, best_of=best_of, indoor=indoor,
            tourney_name=tourney_name, match_date=match_date,
            state=state, rank1=r1_in, rank2=r2_in,
        )
        X = build_row(feat, feature_cols)
        p_p1 = float(model.predict_proba(X)[0, 1])
        p_p2 = 1.0 - p_p1

        print("\n" + "=" * 65)
        print(f"  RÉSULTAT DE PRÉDICTION ({args.circuit.upper()} - XGBOOST)")
        print("=" * 65)
        t_cpi_val = feat.get("tourney_cpi", 8.5)
        t_alt_val = feat.get("tourney_altitude", 0)
        cpi_str = f"Vitesse Ace Rate : {t_cpi_val:.1f}%" if t_cpi_val else "Vitesse Standard"
        alt_str = f" | Altitude : {t_alt_val:.0f}m" if t_alt_val > 0 else ""
        print(f"  Contexte : {tourney_name} | {surf} ({cpi_str}{alt_str}) | {LEVELS.get(level, level)}")
        print("=" * 65)
        m_r = feat.get("_markov_res", {})
        p_markov1 = m_r.get("proba_a", 0.5)
        p_markov2 = m_r.get("proba_b", 0.5)
        print(f"  {p1:<30}  XGBoost: {p_p1:>6.1%}  |  Markov: {p_markov1:>6.1%}")
        print(f"  {p2:<30}  XGBoost: {p_p2:>6.1%}  |  Markov: {p_markov2:>6.1%}")

        print(f"\n  --- Ratings Elo Décomposés ---")
        print(f"  Elo Global  : {p1} = {state['elo'].get(p1, 1500):.0f}  |  {p2} = {state['elo'].get(p2, 1500):.0f}")
        print(f"  Elo {surf:<7}: {p1} = {state['elo_surface'].get(surf,{}).get(p1,1500):.0f}  |  {p2} = {state['elo_surface'].get(surf,{}).get(p2,1500):.0f}")
        print(f"  Serve Elo   : {p1} = {feat.get('_p1_serve_elo_surf', 1500):.0f}  |  {p2} = {feat.get('_p2_serve_elo_surf', 1500):.0f}")
        print(f"  Return Elo  : {p1} = {feat.get('_p1_return_elo_surf', 1500):.0f}  |  {p2} = {feat.get('_p2_return_elo_surf', 1500):.0f}")

        if m_r:
            print(f"\n  --- Modèle Markovien Point-par-Point (Barnett & Clarke) ---")
            print(f"  P(Point Service) : {p1} = {feat.get('_pa_m', 0.64):.1%}  |  {p2} = {feat.get('_pb_m', 0.64):.1%}")
            print(f"  P(Hold Service)  : {p1} = {m_r.get('hold_proba_a', 0.8):.1%}  |  {p2} = {m_r.get('hold_proba_b', 0.8):.1%}")
            print(f"  Total Jeux Prévu : {m_r.get('expected_total_games', 22.5):.1f} jeux")

        h12 = state["h2h"].get(p1, {}).get(p2, [0, 0])
        print(f"\n  H2H : {p1} {h12[0]}-{h12[1]} {p2}")

        print(f"\n  --- Modèles IA Stacking & Probabilités ---")
        if hasattr(model, "get_individual_probas"):
            probas_indiv = model.get_individual_probas(X)
            print(f"  XGBoost   : {p1} {probas_indiv['xgb']}% vs {100-probas_indiv['xgb']:.1f}%")
            print(f"  LightGBM  : {p1} {probas_indiv['lgb']}% vs {100-probas_indiv['lgb']:.1f}%")
            print(f"  CatBoost  : {p1} {probas_indiv['cat']}% vs {100-probas_indiv['cat']:.1f}%")
            print(f"  ⭐ ENSEMBLE : {p1} {probas_indiv['ensemble']}% vs {100-probas_indiv['ensemble']:.1f}%")

        # Explicabilité SHAP
        shap_exp = compute_match_shap_explanation(X, model, feature_cols, p1=p1, p2=p2)
        print(f"\n  --- Explicabilité IA (Facteurs Déterminants SHAP) ---")
        if shap_exp["top_p1_factors"]:
            print(f"  Points forts {p1} :")
            for f in shap_exp["top_p1_factors"]:
                print(f"    • {f}")
        if shap_exp["top_p2_factors"]:
            print(f"  Points forts {p2} :")
            for f in shap_exp["top_p2_factors"]:
                print(f"    • {f}")

        print("\n  [Optionnel] Cotes du bookmaker (entrée pour sauter)")
        odds1_in = ask_float(f"  Cote {p1}")
        odds2_in = ask_float(f"  Cote {p2}")

        if odds1_in and odds2_in and odds1_in > 1 and odds2_in > 1:
            display_value_bet_analysis(p1, p2, p_p1, p_p2, odds1_in, odds2_in)

        again = input("\n  Nouveau match ? (entrée=oui / n=non) ").strip().lower()
        if again in ("n", "non", "no", "q", "quit"):
            break

    print("\nAu revoir !")


if __name__ == "__main__":
    main()

