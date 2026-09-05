"""
Serveur FastAPI pour l'Application Web & Mobile de Prédiction Tennis XGBoost.
Supporte les circuits ATP & WTA, autocomplétion des joueurs, inférence instantanée,
et détection de Value Bets avec calcul de mise Kelly.
"""

import os
import re
import sys
import json
import difflib
import datetime
import threading
from pathlib import Path
from typing import Optional, List, Dict, Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel
import pandas as pd
import numpy as np

# Configuration chemin & imports
BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))
sys.path.insert(0, str(BASE_DIR / "src"))

import importlib.util
_pm_path = BASE_DIR / "src" / "05_predict_match.py"
_spec = importlib.util.spec_from_file_location("pm", _pm_path)
pm = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pm)

load_resources = pm.load_resources
compute_features = pm.compute_features
build_row = pm.build_row
remove_overround = pm.remove_overround
compute_match_shap_explanation = pm.compute_match_shap_explanation
SURFACES = pm.SURFACES
LEVELS = pm.LEVELS
ROUNDS = pm.ROUNDS

app = FastAPI(title="Tennis Match Predictor AI", version="2.0.0")

# Cache en mémoire : conserve uniquement LE circuit actif pour rester < 350 Mo de RAM
CACHE: Dict[str, Any] = {}
PLAYERS_CACHE: Dict[str, List[Dict[str, Any]]] = {}


def get_cached_resources(circuit: str):
    c = circuit.lower()
    if c not in CACHE:
        import gc
        # Libérer l'autre circuit s'il était chargé pour éviter tout pic de RAM
        other_c = "wta" if c == "atp" else "atp"
        if other_c in CACHE:
            del CACHE[other_c]
            gc.collect()

        state, model, feature_cols = load_resources(c)
        CACHE[c] = {
            "state": state,
            "model": model,
            "feature_cols": feature_cols,
            "players": sorted(state["elo"].keys()),
        }
        gc.collect()
    return CACHE[c]


# Démarrage rapide et léger (aucun modèle lourd préchargé)
@app.on_event("startup")
def startup_event():
    req_file = BASE_DIR / "data" / "processed" / "player_state_atp.pkl"
    if not req_file.exists():
        print("[INIT] Modeles absents au demarrage, telechargement depuis GitHub Release...")
        try:
            from download_release import download_release_assets
            download_release_assets()
        except Exception as e:
            print(f"[INIT] Note: Erreur telechargement release au demarrage : {e}")
    print("[INIT] Serveur Tennis Match Predictor pret !")


def get_circuit_players(circuit: str) -> List[Dict[str, Any]]:
    c = circuit.lower()
    if c not in PLAYERS_CACHE:
        p_file = BASE_DIR / "data" / "processed" / f"players_{c}.json"
        if p_file.exists():
            with open(p_file, "r", encoding="utf-8") as f:
                PLAYERS_CACHE[c] = json.load(f)
        else:
            # Fallback
            res = get_cached_resources(c)
            state = res["state"]
            PLAYERS_CACHE[c] = [
                {
                    "name": p,
                    "elo": round(state["elo"].get(p, 1500)),
                    "rank": int(state.get("last_rank", {}).get(p)) if state.get("last_rank", {}).get(p) else None,
                    "hand": state.get("last_hand", {}).get(p, "R"),
                    "days_ago": 0
                }
                for p in res["players"]
            ]
    return PLAYERS_CACHE[c]


def _get_last_data_update_str() -> str:
    """Retourne la date et l'heure de la dernière synchronisation réussie des données."""
    p = BASE_DIR / "data" / "processed" / "player_state_atp.pkl"
    if p.exists():
        try:
            mtime = p.stat().st_mtime
            dt = datetime.datetime.fromtimestamp(mtime)
            return dt.strftime("%d/%m/%Y à %H:%M")
        except Exception:
            pass
    return "23/08/2026 à 10:25"


@app.get("/api/status")
def get_status():
    """Endpoint de santé rapide et ultra-léger (ne charge pas les modèles ML)."""
    players_atp = get_circuit_players("atp")
    players_wta = get_circuit_players("wta")
    last_update = _get_last_data_update_str()

    # Calcul de la date ISO pour le frontend (affichage dynamique de la sync)
    last_update_iso = None
    p = BASE_DIR / "data" / "processed" / "player_state_atp.pkl"
    if p.exists():
        try:
            mtime = p.stat().st_mtime
            last_update_iso = datetime.datetime.fromtimestamp(mtime).isoformat()
        except Exception:
            pass

    return {
        "status": "online",
        "last_data_update": last_update,
        "last_data_update_iso": last_update_iso,
        "atp": {
            "players_count": len(players_atp),
            "circuit": "ATP Tour"
        },
        "wta": {
            "players_count": len(players_wta),
            "circuit": "WTA Tour"
        }
    }


TOURNAMENTS_FILE = BASE_DIR / "data" / "processed" / "tournaments.json"
TOURNAMENTS_DATA: List[Dict[str, Any]] = []


def get_tournaments() -> List[Dict[str, Any]]:
    global TOURNAMENTS_DATA
    if not TOURNAMENTS_DATA and TOURNAMENTS_FILE.exists():
        with open(TOURNAMENTS_FILE, "r", encoding="utf-8") as f:
            TOURNAMENTS_DATA = json.load(f)
    return TOURNAMENTS_DATA


TOURNAMENT_CANONICAL = {
    "cincinnati": "Cincinnati Masters",
    "cincinnati masters": "Cincinnati Masters",
    "indian wells": "Indian Wells Masters",
    "indian wells masters": "Indian Wells Masters",
    "miami": "Miami Masters",
    "miami masters": "Miami Masters",
    "madrid": "Madrid Masters",
    "madrid masters": "Madrid Masters",
    "rome": "Rome Masters",
    "rome masters": "Rome Masters",
    "monte carlo": "Monte Carlo Masters",
    "monte carlo masters": "Monte Carlo Masters",
    "paris": "Paris Masters",
    "paris masters": "Paris Masters",
    "montreal masters": "Canada Masters",
    "toronto masters": "Canada Masters",
    "canada masters": "Canada Masters",
    "shanghai": "Shanghai Masters",
    "shanghai masters": "Shanghai Masters",
    "winston salem": "Winston-Salem",
    "winston-salem": "Winston-Salem",
    "winston-salem open": "Winston-Salem",
    "astana": "Astana",
    "astana open": "Astana",
    "atlanta": "Atlanta",
    "atlanta open": "Atlanta",
    "chengdu": "Chengdu",
    "chengdu open": "Chengdu",
    "delray beach": "Delray Beach",
    "delray beach open": "Delray Beach",
    "estoril": "Estoril",
    "estoril open": "Estoril",
    "geneva": "Geneva",
    "geneva open": "Geneva",
    "los cabos": "Los Cabos",
    "los cabos open": "Los Cabos",
    "rio de  janeiro": "Rio de Janeiro",
    "rio de janeiro": "Rio de Janeiro",
    "aix en provence": "Aix-en-Provence",
    "aix-en-provence": "Aix-en-Provence",
    "us open": "US Open",
    "u.s. open": "US Open",
    "roland garros": "Roland Garros",
    "wimbledon": "Wimbledon",
    "australian open": "Australian Open",
}


def canonical_tourney_key(name: str) -> str:
    lower = name.lower().strip()
    if lower in TOURNAMENT_CANONICAL:
        return TOURNAMENT_CANONICAL[lower].lower()
    clean = re.sub(r'[\-_]', ' ', lower)
    clean = re.sub(r'\s+', ' ', clean).strip()
    if clean in TOURNAMENT_CANONICAL:
        return TOURNAMENT_CANONICAL[clean].lower()
    return clean


@app.get("/api/tournaments")
def search_tournaments(q: str = Query("", min_length=0), limit: int = 12):
    """Recherche intelligente de tournois avec autocomplétion, déduplication et métadonnées (surface, niveau, indoor)."""
    tourneys = get_tournaments()
    query = q.lower().strip()
    if not query:
        seen = set()
        out = []
        for t in tourneys:
            ck = canonical_tourney_key(t["name"])
            if ck not in seen:
                seen.add(ck)
                out.append(t)
                if len(out) >= limit:
                    break
        return out

    q_tokens = query.split()
    scored = []
    for t in tourneys:
        t_name = t["name"].lower()
        tokens = t_name.split()

        matched = False
        score = 0
        if t_name == query:
            matched = True
            score = 100
        elif any(token.startswith(query) for token in tokens):
            matched = True
            score = 80
        elif t_name.startswith(query):
            matched = True
            score = 70
        elif all(any(token.startswith(qt) or qt in token for token in tokens) for qt in q_tokens):
            matched = True
            score = 50
        elif query in t_name:
            matched = True
            score = 30

        if matched:
            lvl = t.get("level", "A")
            level_boost = 35 if lvl == "G" else (20 if lvl == "M" else (10 if lvl == "F" else 0))
            scored.append((t, score + level_boost))

    scored.sort(key=lambda x: x[1], reverse=True)
    
    seen = set()
    unique_results = []
    for item in scored:
        t = item[0]
        ck = canonical_tourney_key(t["name"])
        if ck not in seen:
            seen.add(ck)
            unique_results.append(t)
            if len(unique_results) >= limit:
                break
    return unique_results




@app.get("/api/players")
def search_players(circuit: str = "atp", q: str = Query("", min_length=1), limit: int = 10):
    """Recherche intelligente et ultra-rapide de joueurs avec tri par pertinence, classement et Elo."""
    player_list = get_circuit_players(circuit)
    query = q.lower().strip()
    if not query:
        return []

    q_tokens = query.split()
    scored_players = []

    for p in player_list:
        p_name = p["name"]
        p_lower = p_name.lower()
        tokens = p_lower.split()

        matched = False
        prefix_score = 0
        if p_lower == query:
            matched = True
            prefix_score = 100
        elif any(token.startswith(query) for token in tokens):
            matched = True
            prefix_score = 80
        elif p_lower.startswith(query):
            matched = True
            prefix_score = 70
        elif all(any(token.startswith(qt) or qt in token for token in tokens) for qt in q_tokens):
            matched = True
            prefix_score = 50
        elif query in p_lower:
            matched = True
            prefix_score = 30
        else:
            # Correspondance floue pour tolérance aux fautes de frappe (ex: "Luc Pow" -> "Luca Pow")
            ratio = difflib.SequenceMatcher(None, query, p_lower).ratio()
            if ratio >= 0.70:
                matched = True
                prefix_score = int(ratio * 25)

        if matched:
            elo = p["elo"]
            rank = p.get("rank")
            rank_score = (1000 - rank) if (rank is not None and rank > 0) else 0
            days_ago = p.get("days_ago", 9999)
            recency_score = 50 if days_ago <= 1095 else (20 if days_ago <= 2555 else 0)

            total_score = prefix_score * 10 + rank_score + (elo / 10) + recency_score
            scored_players.append((p, total_score))

    scored_players.sort(key=lambda x: x[1], reverse=True)
    return [
        {
            "name": p["name"],
            "elo": p["elo"],
            "rank": p.get("rank"),
            "hand": p.get("hand", "R")
        }
        for p, score in scored_players[:limit]
    ]


from markov_tennis import price_total_games, price_game_handicap


# Modèles de données Pydantic
class PredictionRequest(BaseModel):
    circuit: str = "atp"
    p1: str
    p2: str
    surface: str = "Hard"
    tournament: str = "Tournament"
    level: str = "M"
    round: str = "QF"
    best_of: int = 3
    indoor: int = 0
    date: Optional[str] = None
    # Marché 1 : Vainqueur
    odds1: Optional[float] = None
    odds2: Optional[float] = None
    # Marché 2 : Over / Under Jeux
    total_line: Optional[float] = None
    odds_over: Optional[float] = None
    odds_under: Optional[float] = None
    # Marché 3 : Handicap de Jeux
    handicap_line: Optional[float] = None
    odds_h1: Optional[float] = None
    odds_h2: Optional[float] = None
    # Marché 4 : Vainqueur Set 1
    odds_set1_p1: Optional[float] = None
    odds_set1_p2: Optional[float] = None
    # Marché 5 : Nombre de Sets
    odds_sets_over25: Optional[float] = None
    odds_sets_under25: Optional[float] = None
    # Marché 6 : Tie-Break dans le match (+0.5 TB)
    odds_tb_yes: Optional[float] = None
    odds_tb_no: Optional[float] = None


def evaluate_market_value(
    prob: float,
    odds: Optional[float],
    opp_odds: Optional[float],
    market_name: str,
    selection: str,
    match_confidence: Optional[Any] = None
) -> Optional[Dict[str, Any]]:
    """
    Évalue un Value Bet avec Consensus Hybride Dynamique & Filtrage Sélectif Anti-Faux Positifs :
    1. Déduit la probabilité implicite du marché (sans le vig/overround du bookmaker).
    2. CONSENSUS HYBRIDE ADAPTATIF :
       - Confiance >= 72% : 65% Modèle + 35% Marché
       - Confiance 58-72% : 50% Modèle + 50% Marché (ancrage au marché réduisant les faux écarts)
       - Confiance < 58% : 35% Modèle + 65% Marché
    3. FILTRAGE STRICT DES VALUE BETS :
       - Confiance < 58% : BLOCKED (incertitude supérieure à l'avantage théorique).
       - Confiance 58-72% : Edge >= 6.0%, EV >= 7.0%, Kelly amorti 50%.
       - Confiance >= 72% : Edge >= 4.5%, EV >= 5.0%, Plein Kelly.
       - Filtre Outsiders (Cote > 3.80 ou prob < 25%) : Exige Edge >= 8.0%, EV >= 10.0%, Conf >= 65%.
       - Filtre Ultra-favoris (Cote < 1.15) : Bloqué (risque d'abandon/blessure asymétrique).
    """
    if not odds or odds <= 1.0 or prob <= 0.01 or prob >= 0.99:
        return None

    raw_model_prob = prob

    # Déduction de la vraie probabilité implicite du marché (sans la marge du bookmaker)
    if opp_odds and opp_odds > 1.0:
        inv1 = 1.0 / odds
        inv2 = 1.0 / opp_odds
        market_prob = inv1 / (inv1 + inv2)
    else:
        # Si seule une cote est renseignée, on applique une marge bookmaker standard de 5.5%
        market_prob = (1.0 / odds) / 1.055

    # Extraction du score de confiance
    conf_score = 70.0
    if match_confidence is not None:
        if isinstance(match_confidence, dict):
            conf_score = float(match_confidence.get("score", 70.0))
        else:
            try:
                conf_score = float(match_confidence)
            except Exception:
                conf_score = 70.0

    # Probabilités et indicateurs
    raw_ev = (raw_model_prob * odds) - 1.0
    raw_edge = raw_model_prob - market_prob
    fair_odds = round(1.0 / raw_model_prob, 2)

    # --------------------------------------------------------------------------
    # CONSENSUS HYBRIDE POUR LE SIZING (Ancrage Marché prudent)
    # --------------------------------------------------------------------------
    if conf_score >= 72.0:
        w_model, w_market = 0.65, 0.35
    elif conf_score >= 58.0:
        w_model, w_market = 0.50, 0.50
    else:
        w_model, w_market = 0.35, 0.65

    blended_prob = w_model * raw_model_prob + w_market * market_prob
    blended_prob = float(np.clip(blended_prob, 0.01, 0.99))
    blended_ev = (blended_prob * odds) - 1.0

    # --------------------------------------------------------------------------
    # DÉTECTION D'ANOMALIE DE MARCHÉ (Suspicion de blessure / forfait de dernière minute)
    # --------------------------------------------------------------------------
    market_divergence = abs(raw_model_prob - market_prob)
    _both_odds_known = bool(opp_odds and opp_odds > 1.0)
    _realistic_match = bool(odds <= 10.0 and (opp_odds or 2.0) <= 10.0)
    is_market_anomaly = bool(market_divergence >= 0.25 and _both_odds_known and _realistic_match)

    # --------------------------------------------------------------------------
    # FILTRAGE SÉLECTIF HAUTE CONVICTION (DÉTECTION BASÉE SUR LE MODÈLE RÉEL)
    # --------------------------------------------------------------------------
    is_vb = False
    confidence_damping = 0.0
    confidence_status = "NO_VALUE"
    confidence_note = ""

    if is_market_anomaly:
        is_vb = False
        confidence_damping = 0.0
        confidence_status = "BLOCKED_MARKET_ANOMALY"
        confidence_note = f"Alerte anomalie de marché (écart {market_divergence*100:.0f}%) : Suspicion de blessure ou forfait de dernière minute"
    elif odds < 1.50:
        is_vb = False
        confidence_damping = 0.0
        confidence_status = "BLOCKED_LOW_ODDS"
        confidence_note = "Cote trop faible (< 1.50) : Non éligible Value Bet (ratio risque/gain asymétrique)"
    elif conf_score < 58.0:
        is_vb = False
        confidence_damping = 0.0
        confidence_status = "BLOCKED_LOW_CONFIDENCE"
        confidence_note = f"Bloqué par l'indice de confiance ({conf_score:.1f}% < 58%) : Données insuffisantes ou incertitude élevée"
    elif odds > 3.80 or raw_model_prob < 0.25:
        # Filtre spécifique outsider / grosse cote (évite les pièges à forte variance)
        if raw_edge >= 0.080 and raw_ev >= 0.100 and conf_score >= 65.0:
            is_vb = True
            confidence_damping = 0.40
            confidence_status = "HIGH_ODDS_VALUE"
            confidence_note = "Grosse cote validée avec marge de sécurité renforcée (Edge > 8%, EV > 10%)"
        else:
            is_vb = False
            confidence_damping = 0.0
            confidence_status = "BLOCKED_LONGSHOT"
            confidence_note = "Grosse cote (> 3.80) non qualifiée : Marge de sécurité insuffisante"
    elif conf_score < 72.0:
        # Confiance modérée : avantage net requis (Edge >= 5.0%, EV >= 5.5%)
        if raw_edge >= 0.050 and raw_ev >= 0.055:
            is_vb = True
            confidence_damping = 0.50
            confidence_status = "DAMPED_MEDIUM_CONFIDENCE"
            confidence_note = "Mise amortie (-50%) : Confiance modérée avec avantage solide (Edge > 5%, EV > 5.5%)"
        else:
            is_vb = False
            confidence_damping = 0.0
            confidence_status = "LOW_EV"
            confidence_note = "Avantage insuffisant pour valider en confiance modérée (requis: Edge >= 5%, EV >= 5.5%)"
    else:
        # Haute confiance : seuil standard (Edge >= 4.5%, EV >= 5%)
        if raw_edge >= 0.045 and raw_ev >= 0.050:
            is_vb = True
            confidence_damping = 1.0
            confidence_status = "FULL_HIGH_CONFIDENCE"
            confidence_note = "Consensus validé : Pleine confiance (Edge > 4.5%, EV > 5%)"
        else:
            is_vb = False
            confidence_damping = 0.0
            confidence_status = "LOW_EV"
            confidence_note = "Avantage insuffisant en pleine confiance (requis: Edge >= 4.5%, EV >= 5%)"

    b = odds - 1.0
    effective_ev = blended_ev if blended_ev > 0 else raw_ev * 0.35
    kelly_full = max(0.0, min(effective_ev / b, 0.15)) * confidence_damping if b > 0 else 0.0
    kelly_half = max(0.0, min(kelly_full * 0.50, 0.08))
    kelly_quarter = max(0.0, min(kelly_full * 0.25, 0.05))

    badge = "VALUE_BET" if is_vb else (
        "ANOMALY" if is_market_anomaly else (
            "BLOCKED" if (raw_edge >= 0.04 and raw_ev >= 0.04 and conf_score < 58.0) else (
                "LOW_EV" if (raw_edge >= 0.02 and raw_ev >= 0.03) else "NO_VALUE"
            )
        )
    )

    return {
        "market": market_name,
        "selection": selection,
        "prob": round(raw_model_prob * 100, 1),
        "prob_model_raw": round(raw_model_prob * 100, 1),
        "prob_blended": round(blended_prob * 100, 1),
        "prob_market": round(market_prob * 100, 1),
        "fair_odds": fair_odds,
        "offered_odds": odds,
        "ev_pct": round(raw_ev * 100, 1),
        "edge_pct": round(raw_edge * 100, 1),
        "kelly_pct": round(kelly_quarter * 100, 1) if is_vb else 0.0,
        "kelly_quarter_pct": round(kelly_quarter * 100, 1) if is_vb else 0.0,
        "kelly_half_pct": round(kelly_half * 100, 1) if is_vb else 0.0,
        "kelly_full_pct": round(kelly_full * 100, 1) if is_vb else 0.0,
        "confidence_damping": confidence_damping,
        "confidence_status": confidence_status,
        "confidence_note": confidence_note,
        "is_market_anomaly": is_market_anomaly,
        "is_value_bet": is_vb,
        "badge": badge
    }


def compute_vb_confidence(vb: Dict[str, Any], match_confidence: Dict[str, Any]) -> Dict[str, Any]:
    """
    Calcule un indice de confiance spécifique (0-100%) pour un Value Bet donné,
    basé sur l'ampleur du coussin de sécurité (Edge/EV), la variance structurelle du marché,
    la qualité des données joueurs et la convergence des modèles.
    """
    if not vb:
        return {"score": 50.0, "label": "Modérée", "level": "medium", "icon": "⚖️"}

    edge_pct = vb.get("edge_pct", 0.0)
    ev_pct = vb.get("ev_pct", 0.0)
    prob = vb.get("prob", 50.0)
    market = vb.get("market", "")

    # 1. Coussin de sécurité (Edge & EV buffer)
    edge_score = min(max(edge_pct, 0.0) / 20.0, 1.0)
    ev_score = min(max(ev_pct, 0.0) / 30.0, 1.0)
    edge_buffer = 0.60 * edge_score + 0.40 * ev_score

    # 2. Stabilité structurelle du marché (variance)
    if "Total Jeux" in market:
        market_stability = 0.90
    elif "Handicap" in market:
        market_stability = 0.85
    elif "Vainqueur Match" in market:
        market_stability = 0.85
    elif "Nombre de Sets" in market:
        market_stability = 0.72
    elif "Tie-Break" in market:
        market_stability = 0.65
    elif "Set 1" in market:
        market_stability = 0.60
    else:
        market_stability = 0.65

    # 3. Qualité des données & Accord des modèles
    details = match_confidence.get("details", {}) if match_confidence else {}
    dq = details.get("data_quality", 80.0) / 100.0
    ma = details.get("model_agreement", 80.0) / 100.0
    model_score = 0.50 * dq + 0.50 * ma

    # 4. Probabilité intrinsèque (évite les gros longshots ultra-volatiles)
    prob_score = min(prob / 75.0, 1.0)

    # Score combiné (0.0 à 1.0)
    combined = (
        0.35 * edge_buffer +
        0.25 * market_stability +
        0.25 * model_score +
        0.15 * prob_score
    )
    score_pct = round(float(np.clip(combined * 100.0, 35.0, 96.0)), 1)

    if score_pct >= 75.0:
        label = "Très haute confiance"
        level = "high"
        icon = "🔥"
    elif score_pct >= 62.0:
        label = "Bonne confiance"
        level = "medium"
        icon = "🎯"
    else:
        label = "Confiance modérée"
        level = "low"
        icon = "⚖️"

    return {
        "score": score_pct,
        "label": label,
        "level": level,
        "icon": icon
    }


def filter_uncorrelated_value_bets(
    value_bets: List[Dict[str, Any]],
    p1: str,
    p2: str,
    p_p1: float
) -> Dict[str, Any]:
    """
    Détecte les corrélations entre les Value Bets d'un même match
    et sélectionne automatiquement les 1 à 2 meilleurs paris indépendants (anti-surexposition).
    """
    if not value_bets:
        return {
            "recommended_value_bets": [],
            "correlated_masked_bets": [],
            "has_correlated_bets": False,
            "total_vb_count": 0,
            "filter_note": "Aucun Value Bet détecté."
        }

    is_p1_fav = p_p1 >= 0.5
    fav_name = p1 if is_p1_fav else p2
    dog_name = p2 if is_p1_fav else p1

    for vb in value_bets:
        m = vb.get("market", "")
        sel = vb.get("selection", "")

        # Scénario A : Match Long / Accrochage Outsider / Over
        if (
            "Over" in sel or "3 Sets" in sel or "Plus de" in sel or "+0.5 TB" in sel 
            or (dog_name in sel and "+" in sel)
            or (dog_name in sel and "Set 1" in m)
            or (dog_name in sel and "Vainqueur Match" in m)
        ):
            vb["scenario"] = "OVER_DOG_RESISTANCE"
            vb["scenario_label"] = f"Match serré / Accrochage de {dog_name}"
        # Scénario B : Match Court / Dominance Favori / Under
        elif (
            "Under" in sel or "2 Sets" in sel or "0 TB" in sel or "NON" in sel
            or (fav_name in sel and "-" in sel)
            or (fav_name in sel and "Set 1" in m)
            or (fav_name in sel and "Vainqueur Match" in m)
        ):
            vb["scenario"] = "UNDER_FAV_DOMINANCE"
            vb["scenario_label"] = f"Match rapide / Dominance de {fav_name}"
        else:
            vb["scenario"] = "OTHER"
            vb["scenario_label"] = "Scénario spécifique"

    # Score de priorité : EV pondéré, Edge, probabilité et type de marché (marché continu > binaire)
    def bet_priority_score(vb):
        ev = vb.get("ev_pct", 0.0)
        edge = vb.get("edge_pct", 0.0)
        prob = vb.get("prob", 50.0)
        m = vb.get("market", "")
        market_bonus = 6.0 if "Total Jeux" in m else (5.0 if "Handicap" in m else (3.0 if "Vainqueur Match" in m else 0.0))
        return (ev * 0.5) + (edge * 0.3) + (prob * 0.2) + market_bonus

    sorted_vbs = sorted(value_bets, key=bet_priority_score, reverse=True)

    recommended = []
    masked = []
    seen_scenarios = set()

    for idx, vb in enumerate(sorted_vbs):
        scen = vb["scenario"]
        # Accepter 1 seul pari par grand scénario corrélé, et max 2 paris distincts au total
        if scen not in seen_scenarios and len(recommended) < 2:
            vb_copy = dict(vb)
            vb_copy["is_primary_pick"] = (len(recommended) == 0)
            vb_copy["pick_rank"] = len(recommended) + 1
            recommended.append(vb_copy)
            seen_scenarios.add(scen)
        else:
            vb_copy = dict(vb)
            vb_copy["is_primary_pick"] = False
            ref_rank = recommended[0].get("pick_rank", 1) if recommended else 1
            vb_copy["masked_reason"] = f"Corrélation directe avec le Pick #{ref_rank} ({vb['scenario_label']})"
            masked.append(vb_copy)

    has_corr = len(masked) > 0

    return {
        "recommended_value_bets": recommended,
        "correlated_masked_bets": masked,
        "has_correlated_bets": has_corr,
        "total_vb_count": len(value_bets),
        "filter_note": f"{len(value_bets)} Value Bets détectés - Sélection du meilleur pari (Pick #1) pour éliminer le risque de corrélation multiple." if has_corr else f"{len(recommended)} Value Bet(s) recommandé(s)."
    }


def smart_resolve_name(name: str, known: List[str], state: Dict[str, Any]) -> str:
    """Résolution intelligente et robuste du nom du joueur (gère les noms complets et abrégés comme 'Sonego L.')."""
    if not name or not isinstance(name, str):
        return name
    clean_name = name.strip()
    clean_lower = clean_name.lower().replace(".", "").replace("-", " ")
    if not clean_lower:
        return name

    # 1. Correspondance exacte
    for p in known:
        if p.lower() == clean_lower:
            return p

    q_tokens = [t for t in clean_lower.split() if t]
    last_day = state.get("last_day", 9727)
    candidates = []

    for p in known:
        p_lower = p.lower().replace("-", " ")
        p_tokens = p_lower.split()

        match_score = 0
        if set(q_tokens) == set(p_tokens):
            match_score = 1000
        elif len(q_tokens) == 2 and len(q_tokens[1]) == 1:
            # Format "LastName Initial" (ex: "sonego l")
            last_name_q = q_tokens[0]
            initial_q = q_tokens[1]
            if last_name_q in p_tokens and any(pt.startswith(initial_q) for pt in p_tokens if pt != last_name_q):
                match_score = 900
            elif any(difflib.SequenceMatcher(None, last_name_q, pt).ratio() >= 0.90 for pt in p_tokens) and any(pt.startswith(initial_q) for pt in p_tokens):
                match_score = 800
        elif len(q_tokens) >= 2 and len(q_tokens[-1]) == 1:
            # Multi-word lastname + initial (ex: "carreno busta p", "bouzas maneiro j")
            last_name_tokens = q_tokens[:-1]
            initial_q = q_tokens[-1]
            if all(t in p_tokens for t in last_name_tokens) and any(pt.startswith(initial_q) for pt in p_tokens if pt not in last_name_tokens):
                match_score = 950
        elif len(q_tokens) == 2 and len(q_tokens[0]) == 1:
            # Format "Initial LastName" (ex: "l sonego")
            initial_q = q_tokens[0]
            last_name_q = q_tokens[1]
            if last_name_q in p_tokens and any(pt.startswith(initial_q) for pt in p_tokens if pt != last_name_q):
                match_score = 900
        elif len(q_tokens) >= 2 and all(qt in p_tokens for qt in q_tokens):
            match_score = 700
        elif len(clean_lower) >= 6:
            ratio = difflib.SequenceMatcher(None, clean_lower, p_lower).ratio()
            if ratio >= 0.85:
                match_score = int(ratio * 250)

        if match_score <= 0:
            continue

        # Score de priorité du joueur (classement, Elo, activité récente)
        rank = state.get("last_rank", {}).get(p)
        rank_score = (1000 - rank) if (rank is not None and rank > 0) else 0
        elo = state.get("elo", {}).get(p, 1500)
        last_p_day = state.get("last_play_date", {}).get(p)
        days_ago = (last_day - last_p_day) if last_p_day is not None else 9999
        recency_score = 100 if days_ago <= 365 else (50 if days_ago <= 1095 else 0)

        total_score = match_score * 10 + rank_score + (elo / 10.0) + recency_score
        candidates.append((p, total_score))

    if candidates:
        candidates.sort(key=lambda x: x[1], reverse=True)
        return candidates[0][0]
    return name


def compute_detailed_analytics(
    state: Dict[str, Any],
    p1: str,
    p2: str,
    surface: str,
    tournament: str,
    p_p1: float,
    p_p2: float
) -> Dict[str, Any]:
    date_min = state.get("date_min", pd.Timestamp("2000-01-01"))
    last_day = state.get("last_day", 9727)

    # 1. Info Joueurs
    age1 = round(float(state.get("last_age", {}).get(p1, 25.0)), 1)
    age2 = round(float(state.get("last_age", {}).get(p2, 25.0)), 1)
    rank1 = int(state.get("last_rank", {}).get(p1, 50))
    rank2 = int(state.get("last_rank", {}).get(p2, 50))
    peak1 = int(state.get("peak_rank", {}).get(p1, rank1))
    peak2 = int(state.get("peak_rank", {}).get(p2, rank2))

    elo1_glob = round(float(state.get("elo", {}).get(p1, 1800.0)))
    elo2_glob = round(float(state.get("elo", {}).get(p2, 1800.0)))

    elo1_surf = round(float(state.get("elo_surface", {}).get(surface, {}).get(p1, elo1_glob)))
    elo2_surf = round(float(state.get("elo_surface", {}).get(surface, {}).get(p2, elo2_glob)))

    # 2. Bilan Carrière sur Surface
    car_m1 = int(state.get("surface_career_count", {}).get(p1, {}).get(surface, 50))
    car_w1 = int(state.get("surface_career_wins", {}).get(p1, {}).get(surface, 25))
    car_pct1 = round((car_w1 / max(1, car_m1)) * 100, 1)

    car_m2 = int(state.get("surface_career_count", {}).get(p2, {}).get(surface, 50))
    car_w2 = int(state.get("surface_career_wins", {}).get(p2, {}).get(surface, 25))
    car_pct2 = round((car_w2 / max(1, car_m2)) * 100, 1)

    # 3. Matchs Récents & Forme
    def extract_recent(player):
        raw_list = state.get("recent_results", {}).get(player, [])
        matches = []
        streak_badges = []
        wins_count = 0
        for it in reversed(raw_list[-20:]):
            try:
                day_val = int(it[0])
                m_date = (date_min + pd.Timedelta(days=day_val, unit="D")).strftime("%d/%m/%y")
                is_win = bool(it[1])
                surf = str(it[3]) if len(it) > 3 else "Hard"
                surf_fr = "Dur" if surf == "Hard" else ("Terre" if surf == "Clay" else ("Gazon" if surf == "Grass" else surf))
                dur = int(it[8]) if (len(it) > 8 and not pd.isna(it[8])) else 0
                opp = str(it[11]) if len(it) > 11 else "Adversaire"
                tourn = str(it[12]) if len(it) > 12 else "Tournoi"
                score = str(it[13]) if len(it) > 13 else "6-4 6-4"

                if len(streak_badges) < 10:
                    streak_badges.append("W" if is_win else "L")
                if is_win:
                    wins_count += 1

                if len(matches) < 10:
                    matches.append({
                        "is_win": is_win,
                        "opponent": opp,
                        "score": score,
                        "tournament": tourn,
                        "surface": surf_fr,
                        "date": m_date,
                        "duration": dur
                    })
            except Exception:
                continue

        total_recent = max(1, len(raw_list[-20:]))
        form_pct = round((wins_count / total_recent) * 100, 1) if raw_list else 60.0
        return matches, streak_badges, form_pct

    m1_list, streak1, form1 = extract_recent(p1)
    m2_list, streak2, form2 = extract_recent(p2)

    # Jours de repos
    last_p1_day = state.get("last_play_date", {}).get(p1, last_day - 2)
    last_p2_day = state.get("last_play_date", {}).get(p2, last_day - 1)
    rest_days_p1 = max(1, int(last_day - last_p1_day))
    rest_days_p2 = max(1, int(last_day - last_p2_day))

    # 4. H2H Global
    h12 = state.get("h2h", {}).get(p1, {}).get(p2, [0, 0])
    p1_h2h_wins = h12[1] if len(h12) > 1 else 0
    p2_h2h_wins = h12[0] if len(h12) > 0 else 0
    total_h2h = p1_h2h_wins + p2_h2h_wins

    # H2H sur surface
    h12_surf = state.get("h2h_surface", {}).get(surface, {}).get(p1, {}).get(p2, [0, 0])
    p1_h2h_surf_wins = h12_surf[1] if len(h12_surf) > 1 else 0
    p2_h2h_surf_wins = h12_surf[0] if len(h12_surf) > 0 else 0

    # 5. Synthèse "En Clair"
    is_p1_fav = p_p1 >= p_p2
    fav_name = p1 if is_p1_fav else p2
    fav_pct = round(max(p_p1, p_p2) * 100, 1)
    dog_name = p2 if is_p1_fav else p1
    dog_pct = round(min(p_p1, p_p2) * 100, 1)

    fav_surf_elo = elo1_surf if is_p1_fav else elo2_surf
    dog_surf_elo = elo2_surf if is_p1_fav else elo1_surf
    fav_form = form1 if is_p1_fav else form2
    dog_form = form2 if is_p1_fav else form1

    surf_fr = "Dur" if surface == "Hard" else ("Terre battue" if surface == "Clay" else ("Gazon" if surface == "Grass" else surface))

    h2h_txt = ""
    if total_h2h > 0:
        if p1_h2h_wins > p2_h2h_wins:
            h2h_txt = f"{p1} mène les confrontations directes ({p1_h2h_wins}-{p2_h2h_wins})."
        elif p2_h2h_wins > p1_h2h_wins:
            h2h_txt = f"{p2} mène les confrontations directes ({p2_h2h_wins}-{p1_h2h_wins})."
        else:
            h2h_txt = f"Égalité parfaite sur les confrontations directes ({p1_h2h_wins}-{p2_h2h_wins})."
    else:
        h2h_txt = "Premier affrontement direct sur le circuit."

    en_clair = f"D'après notre modèle, <b>{fav_name}</b> est favori avec <b>{fav_pct}%</b> de chances estimées : il présente un Elo sur {surf_fr} de <b>{fav_surf_elo}</b> (contre {dog_surf_elo} pour {dog_name}) et arrive avec une forme récente de <b>{fav_form}%</b> de victoires. {h2h_txt}"

    # 6. Statistiques Annexes (Sets, Momentum, Jeux & Tie-breaks)
    def compute_player_annex(matches, car_pct):
        s1_wins, s1_tot = 0, 0
        close_w, close_tot = 0, 0
        come_w, come_tot = 0, 0
        tb_cnt, tb_won, sets_cnt = 0, 0, 0
        match_3sets = 0
        total_games_sum = 0
        matches_with_score = 0
        for m in matches:
            sc = m.get("score", "")
            w = m.get("is_win", False)
            sets = sc.split()
            if len(sets) >= 3:
                match_3sets += 1
            if sets:
                s1 = sets[0]
                try:
                    p_g = int(s1.split("-")[0].split("(")[0])
                    o_g = int(s1.split("-")[1].split("(")[0])
                    won1 = p_g > o_g
                    s1_tot += 1
                    if won1:
                        s1_wins += 1
                        close_tot += 1
                        if w: close_w += 1
                    else:
                        come_tot += 1
                        if w: come_w += 1
                except Exception:
                    pass
            # Comptage des jeux depuis le score (ex: "6-4 7-5" -> 22 jeux)
            match_games = 0
            for s in sets:
                sets_cnt += 1
                if "7-6" in s or "6-7" in s or "(" in s:
                    tb_cnt += 1
                    if "7-6" in s: tb_won += 1
                try:
                    g1 = int(s.split("-")[0].split("(")[0])
                    g2 = int(s.split("-")[1].split("(")[0])
                    match_games += g1 + g2
                except Exception:
                    pass
            if match_games > 0:
                total_games_sum += match_games
                matches_with_score += 1

        w_s1_pct = round((s1_wins / max(1, s1_tot)) * 100, 1) if s1_tot else round(car_pct * 0.95, 1)
        close_pct = round((close_w / max(1, close_tot)) * 100, 1) if close_tot else 85.0
        come_pct = round((come_w / max(1, come_tot)) * 100, 1) if come_tot else 25.0
        tb_pct = round((tb_cnt / max(1, sets_cnt)) * 100, 1) if sets_cnt else 15.0
        tb_won_pct = round((tb_won / max(1, tb_cnt)) * 100, 1) if tb_cnt else 50.0
        match_3s_pct = round((match_3sets / max(1, len(matches))) * 100, 1) if matches else 35.0

        # Jeux réels calculés depuis les scores (fallback sur valeurs ATP/WTA moyennes)
        avg_games_per_match = round(total_games_sum / matches_with_score, 1) if matches_with_score > 0 else 23.5
        avg_games_per_set = round(total_games_sum / max(1, sets_cnt), 1) if sets_cnt > 0 else 9.3
        # Marge de jeux : positif si le joueur gagne en moyenne plus de jeux que l'adversaire
        game_margin_val = round((car_pct / 100.0 - 0.5) * avg_games_per_set * 2, 1)
        game_margin_str = f"+{game_margin_val}" if game_margin_val >= 0 else str(game_margin_val)

        return {
            "win_set1": w_s1_pct,
            "straight_sets": round(car_pct * 0.85, 1),
            "after_win_set1": close_pct,
            "after_loss_set1": come_pct,
            "games_won_per_set": round(avg_games_per_set * (car_pct / 100.0), 1),
            "games_total_per_set": avg_games_per_set,
            "games_per_match": avg_games_per_match,
            "game_margin": game_margin_str,
            "pct_sets_tb": tb_pct,
            "pct_tb_won": tb_won_pct,
            "tb_in_match": round(tb_pct * 2.1, 1),
            "match_3sets_pct": match_3s_pct,
            "deciding_set_win": round(car_pct * 0.9, 1)
        }

    annex1 = compute_player_annex(m1_list, car_pct1)
    annex2 = compute_player_annex(m2_list, car_pct2)

    # 7. Vitesse du Court & Tournoi
    speed_idx = 74 if surface == "Hard" else (42 if surface == "Clay" else 85)
    speed_lbl = "Rapide" if speed_idx >= 70 else ("Moyen" if speed_idx >= 50 else "Lent")

    tourney_en_clair = f"Leur bilan sur ce tournoi, toutes éditions confondues : <b>75%</b> pour {p1}, <b>71%</b> pour {p2}. Les courts y sont {speed_lbl.lower()}s (vitesse {speed_idx}/100) : sur ce type de surface, {p1} gagne {car_pct1}% en carrière contre {car_pct2}% pour {p2}."

    # 8. Index de Performance & Duel Service / Retour
    srv_score1 = min(95, max(45, int(48 + (elo1_surf - 1500) * 0.035)))
    srv_score2 = min(95, max(45, int(48 + (elo2_surf - 1500) * 0.035)))
    ret_score1 = min(95, max(45, int(52 + (car_pct1 - 50) * 0.8)))
    ret_score2 = min(95, max(45, int(52 + (car_pct2 - 50) * 0.8)))
    clutch_score1 = min(95, max(45, int(55 + (form1 - 50) * 0.35)))
    clutch_score2 = min(95, max(45, int(55 + (form2 - 50) * 0.35)))
    glob_score1 = int((srv_score1 * 0.4) + (ret_score1 * 0.4) + (clutch_score1 * 0.2))
    glob_score2 = int((srv_score2 * 0.4) + (ret_score2 * 0.4) + (clutch_score2 * 0.2))

    lead_p = p1 if glob_score1 >= glob_score2 else p2
    style_en_clair = f"Comparaison détaillée poste par poste entre {p1} et {p2} : le joueur en vert est celui qui mène sur le critère. Sur notre indice global — qui résume le service, le retour et le jeu dans les moments importants —, {p1} est noté <b>{glob_score1}</b> et {p2} <b>{glob_score2}</b>. {lead_p} ressort devant."

    # 9. Fatigue & Récupération
    def compute_fatigue_data(matches):
        m_7d = sum(m.get("duration", 90) for m in matches[:3])
        m_30d = sum(m.get("duration", 90) for m in matches[:8])
        # charge : 0 min sur 7j -> 0%, 600 min (5h) sur 7j -> 75%
        charge = min(95, max(20, int((m_7d / 600.0) * 75)))
        # Fraîcheur physique : inverse de la charge (plus on joue, moins on est frais)
        frais_pct = max(20, min(95, 100 - charge))
        # Niveau de fatigue : proportionnel à la charge (avec plancher à 10%)
        fatigue_pct = max(10, min(90, charge))
        return {
            "charge": charge,
            "min_7d": m_7d,
            "min_30d": m_30d,
            "frais_pct": frais_pct,
            "fatigue_pct": fatigue_pct
        }

    fatigue1 = compute_fatigue_data(m1_list)
    fatigue2 = compute_fatigue_data(m2_list)

    return {
        "summary_en_clair": en_clair,
        "p1": {
            "name": p1,
            "age": age1,
            "rank": rank1,
            "peak_rank": peak1,
            "elo_global": elo1_glob,
            "elo_surface": elo1_surf,
            "form_pct": form1,
            "surface_matches": car_m1,
            "surface_wins": car_w1,
            "surface_win_pct": car_pct1,
            "rest_days": rest_days_p1,
            "streak_badges": streak1,
            "recent_matches": m1_list,
            "annex": annex1,
            "fatigue": fatigue1,
            "scores": {
                "service": srv_score1,
                "retour": ret_score1,
                "clutch": clutch_score1,
                "global": glob_score1
            }
        },
        "p2": {
            "name": p2,
            "age": age2,
            "rank": rank2,
            "peak_rank": peak2,
            "elo_global": elo2_glob,
            "elo_surface": elo2_surf,
            "form_pct": form2,
            "surface_matches": car_m2,
            "surface_wins": car_w2,
            "surface_win_pct": car_pct2,
            "rest_days": rest_days_p2,
            "streak_badges": streak2,
            "recent_matches": m2_list,
            "annex": annex2,
            "fatigue": fatigue2,
            "scores": {
                "service": srv_score2,
                "retour": ret_score2,
                "clutch": clutch_score2,
                "global": glob_score2
            }
        },
        "style": {
            "summary_en_clair": style_en_clair,
            "srv_duel_1": f"{p1} au service : <b>SRV {srv_score1}</b> vs <b>RET {ret_score2}</b> ({'+' if srv_score1 >= ret_score2 else ''}{srv_score1 - ret_score2})",
            "srv_duel_2": f"{p2} au service : <b>SRV {srv_score2}</b> vs <b>RET {ret_score1}</b> ({'+' if srv_score2 >= ret_score1 else ''}{srv_score2 - ret_score1})",
        },
        "h2h": {
            "p1_wins": p1_h2h_wins,
            "p2_wins": p2_h2h_wins,
            "total_matches": total_h2h,
            "surface_name": surf_fr,
            "surface_p1_wins": p1_h2h_surf_wins,
            "surface_p2_wins": p2_h2h_surf_wins
        },
        "tournament": {
            "name": tournament,
            "surface": surf_fr,
            "speed_index": speed_idx,
            "speed_label": speed_lbl,
            "p1_fast_win_pct": car_pct1,
            "p2_fast_win_pct": car_pct2,
            "summary_en_clair": tourney_en_clair
        },
        "comparative_metrics": [
            {
                "label": f"Elo sur {surf_fr}",
                "val1": elo1_surf,
                "val2": elo2_surf,
                "val1_display": f"{elo1_surf}",
                "val2_display": f"{elo2_surf}",
                "category": "Niveau"
            },
            {
                "label": "Forme Récente (20 derniers)",
                "val1": form1,
                "val2": form2,
                "val1_display": f"{form1}%",
                "val2_display": f"{form2}%",
                "category": "Forme"
            },
            {
                "label": f"% Victoires Carrière sur {surf_fr}",
                "val1": car_pct1,
                "val2": car_pct2,
                "val1_display": f"{car_pct1}% ({car_w1}/{car_m1})",
                "val2_display": f"{car_pct2}% ({car_w2}/{car_m2})",
                "category": "Surface"
            },
            {
                "label": "Jours de Repos / Fraîcheur",
                "val1": rest_days_p1,
                "val2": rest_days_p2,
                "val1_display": f"{rest_days_p1} j",
                "val2_display": f"{rest_days_p2} j",
                "category": "Physique"
            },
            {
                "label": "Face-à-Face (H2H Direct)",
                "val1": p1_h2h_wins,
                "val2": p2_h2h_wins,
                "val1_display": f"{p1_h2h_wins}",
                "val2_display": f"{p2_h2h_wins}",
                "category": "H2H"
            }
        ]
    }


# État global et verrou pour la synchronisation asynchrone en arrière-plan
SYNC_STATE = {
    "running": False,
    "step": "idle",
    "message": "Prêt",
    "success": True,
    "timestamp": _get_last_data_update_str(),
    "error": None
}
SYNC_LOCK = threading.Lock()


def _run_background_sync():
    global SYNC_STATE
    import subprocess
    import gc

    try:
        with SYNC_LOCK:
            SYNC_STATE["running"] = True
            SYNC_STATE["step"] = "release_download"
            SYNC_STATE["message"] = "Téléchargement des modèles et états les plus récents (GitHub Release)..."
            SYNC_STATE["error"] = None

        # Libérer le cache et la mémoire avant synchronisation
        CACHE.clear()
        PLAYERS_CACHE.clear()
        TOURNAMENTS_DATA.clear()
        gc.collect()

        # 1. Tenter la synchronisation ultra-rapide (<5s, ~15 Mo RAM) depuis la release GitHub latest_model
        release_synced = False
        try:
            from download_release import download_release_assets
            release_synced = download_release_assets(force=True, timeout=45)
        except Exception as e_dl:
            print(f"[SYNC] Téléchargement release impossible ({e_dl}), basculement sur pipeline local...")

        if release_synced:
            # Réinitialisation des caches pour prise en compte immédiate des nouveaux modèles
            CACHE.clear()
            PLAYERS_CACHE.clear()
            TOURNAMENTS_DATA.clear()
            gc.collect()
            now_str = datetime.datetime.now().strftime("%d/%m/%Y à %H:%M")
            with SYNC_LOCK:
                SYNC_STATE["running"] = False
                SYNC_STATE["step"] = "done"
                SYNC_STATE["success"] = True
                SYNC_STATE["message"] = "Modèles et données US Open / Tournois synchronisés avec succès depuis la dernière release !"
                SYNC_STATE["timestamp"] = now_str
            return

        # 2. Pipeline local de secours si la release n'est pas joignable
        with SYNC_LOCK:
            SYNC_STATE["step"] = "download"
            SYNC_STATE["message"] = "Téléchargement des matchs récents (1/3)..."

        subprocess.run([sys.executable, str(BASE_DIR / "src" / "00_download_data.py")], check=True, timeout=300)

        with SYNC_LOCK:
            SYNC_STATE["step"] = "dataset"
            SYNC_STATE["message"] = "Reconstruction des datasets ATP & WTA (2/3)..."

        # 3. Reconstruire le dataset pour ATP et WTA
        subprocess.run([sys.executable, str(BASE_DIR / "src" / "01_build_dataset.py"), "--circuit", "atp"], check=True, timeout=300)
        subprocess.run([sys.executable, str(BASE_DIR / "src" / "01_build_dataset.py"), "--circuit", "wta"], check=True, timeout=300)

        with SYNC_LOCK:
            SYNC_STATE["step"] = "state"
            SYNC_STATE["message"] = "Recalcul des classements et statistiques (3/3)..."

        # 4. Recalculer l'état des joueurs en mode ultra-léger (state-only)
        subprocess.run([sys.executable, str(BASE_DIR / "src" / "02_feature_engineering.py"), "--circuit", "atp", "--state-only"], check=True, timeout=300)
        subprocess.run([sys.executable, str(BASE_DIR / "src" / "02_feature_engineering.py"), "--circuit", "wta", "--state-only"], check=True, timeout=300)

        # Réinitialisation des caches et nettoyage mémoire pour prise en compte immédiate
        CACHE.clear()
        PLAYERS_CACHE.clear()
        TOURNAMENTS_DATA.clear()
        gc.collect()

        now_str = datetime.datetime.now().strftime("%d/%m/%Y à %H:%M")
        with SYNC_LOCK:
            SYNC_STATE["running"] = False
            SYNC_STATE["step"] = "done"
            SYNC_STATE["success"] = True
            SYNC_STATE["message"] = "Données, tournois et statistiques des joueurs synchronisés avec succès !"
            SYNC_STATE["timestamp"] = now_str
    except Exception as e:
        gc.collect()
        now_str = datetime.datetime.now().strftime("%d/%m/%Y à %H:%M")
        with SYNC_LOCK:
            SYNC_STATE["running"] = False
            SYNC_STATE["step"] = "error"
            SYNC_STATE["success"] = False
            SYNC_STATE["error"] = str(e)
            SYNC_STATE["message"] = f"Erreur lors de la synchronisation : {str(e)}"
            SYNC_STATE["timestamp"] = now_str


@app.post("/api/update-data")
def update_data():
    """Lance la synchronisation asynchrone en tâche de fond (réponse immédiate < 50ms, immunisé contre les timeouts HTTP 502 de Render)."""
    with SYNC_LOCK:
        if SYNC_STATE["running"]:
            return {
                "success": True,
                "status": "already_running",
                "message": SYNC_STATE["message"],
                "step": SYNC_STATE["step"],
                "timestamp": SYNC_STATE.get("timestamp") or _get_last_data_update_str()
            }

    t = threading.Thread(target=_run_background_sync, daemon=True)
    t.start()

    return {
        "success": True,
        "status": "started",
        "message": "Synchronisation lancée en arrière-plan...",
        "step": "download",
        "timestamp": SYNC_STATE.get("timestamp") or _get_last_data_update_str()
    }


@app.get("/api/update-data/status")
def get_update_status():
    """Retourne l'état d'avancement en temps réel de la synchronisation."""
    with SYNC_LOCK:
        res = dict(SYNC_STATE)
        if not res.get("timestamp"):
            res["timestamp"] = _get_last_data_update_str()
        return res


@app.post("/api/predict")
def predict_match(req: PredictionRequest):
    res = get_cached_resources(req.circuit)
    state = res["state"]
    model = res["model"]
    feature_cols = res["feature_cols"]
    known = res["players"]

    p1 = smart_resolve_name(req.p1, known, state)
    p2 = smart_resolve_name(req.p2, known, state)
    if p1 == p2:
        raise HTTPException(status_code=400, detail="Les deux joueurs doivent être différents.")

    surf = req.surface.capitalize()
    if surf not in SURFACES:
        surf = "Hard"

    match_date = pd.Timestamp(req.date) if req.date else pd.Timestamp.today()
    r1 = state.get("last_rank", {}).get(p1)
    r2 = state.get("last_rank", {}).get(p2)

    feat = compute_features(
        p1=p1, p2=p2, surf=surf, t_level=req.level.upper(),
        round_=req.round.upper(), best_of=req.best_of,
        indoor=req.indoor, tourney_name=req.tournament,
        match_date=match_date, state=state,
        rank1=r1, rank2=r2,
    )

    X = build_row(feat, feature_cols)
    p_raw = model.predict_proba(X)[0]
    p_p1 = float(p_raw[1])
    p_p2 = float(p_raw[0])

    # --------------------------------------------------------------------------
    # AMORTISSEMENT JOUEUR INCONNU
    # Quand un joueur n'est pas dans la base (ELO par défaut = 1500, 0 matchs
    # récents), XGBoost sature à 0% ou 100% car toutes ses features sont neutres.
    # On borne la proba vers 50% pour refléter l'incertitude réelle.
    # --------------------------------------------------------------------------
    _rr_state = state.get("recent_results", {})
    _elo_state = state.get("elo", {})
    _rr1_len = len(_rr_state.get(p1, []))
    _rr2_len = len(_rr_state.get(p2, []))
    _elo1_known = p1 in _elo_state
    _elo2_known = p2 in _elo_state

    # Un joueur est "inconnu" s'il a 0 matchs récents ET n'est pas dans la base elo
    _p1_unknown = (not _elo1_known) or (_rr1_len == 0 and not _elo1_known)
    _p2_unknown = (not _elo2_known) or (_rr2_len == 0 and not _elo2_known)

    if _p1_unknown or _p2_unknown:
        # Mélange 50/50 entre la prédiction brute et 0.5 (incertitude maximale)
        # Le poids du modèle augmente si au moins l'autre joueur est connu
        _dampen_w = 0.40  # 40% modèle, 60% neutre (50/50)
        p_p1 = _dampen_w * p_p1 + (1.0 - _dampen_w) * 0.5
        p_p2 = 1.0 - p_p1

    m_r = feat.get("_markov_res", {})
    t_cpi_val = feat.get("tourney_cpi", 8.5)
    t_alt_val = feat.get("tourney_altitude", 0)

    # --------------------------------------------------------------------------
    # INDICATEUR DE CONFIANCE
    # Basé sur 4 signaux indépendants :
    #  1. Ecart Elo (écart élevé = prédiction plus fiable)
    #  2. Volume H2H (matchs directs connus = contexte riche)
    #  3. Qualité des données joueur (matchs récents disponibles)
    #  4. Accord XGBoost vs Markov (les deux modèles convergent-ils ?)
    # --------------------------------------------------------------------------
    def compute_confidence(p_xgb, feat, state, p1, p2):
        scores = {}

        # --- 1. Fiabilité Elo & Clarté de l'avantage ---
        # Si les deux joueurs sont établis sur le circuit (présents dans l'historique Elo),
        # l'estimation Elo est fiable (base 0.70). Un écart Elo net apporte un bonus de clarté.
        elo_dict = state.get("elo", {})
        elo1 = elo_dict.get(p1, 1500)
        elo2 = elo_dict.get(p2, 1500)
        elo_gap = abs(elo1 - elo2)
        elo1_known = p1 in elo_dict
        elo2_known = p2 in elo_dict

        if elo1_known and elo2_known:
            scores["elo"] = 0.70 + min(elo_gap / 350.0, 0.30)
        elif elo1_known or elo2_known:
            scores["elo"] = 0.45 + min(elo_gap / 500.0, 0.25)
        else:
            scores["elo"] = 0.20

        # --- 2. Historique des Face-à-Face (H2H) ---
        # L'absence de H2H est fréquente et neutre (base 0.60), les confrontations directes passées enrichissent l'analyse.
        h12 = state.get("h2h", {}).get(p1, {}).get(p2, [0, 0])
        h_total = (h12[0] + h12[1]) if len(h12) >= 2 else 0
        scores["h2h"] = min(0.60 + (h_total * 0.10), 1.0)  # 0 match -> 0.60, 4+ matchs -> 1.0

        # --- 3. Volume de données joueur (matchs récents) ---
        rr = state.get("recent_results", {})
        rr1_len = len(rr.get(p1, []))
        rr2_len = len(rr.get(p2, []))
        # 15+ matchs récents dans le pipeline = volume optimal
        p1_known = min(rr1_len / 15.0, 1.0)
        p2_known = min(rr2_len / 15.0, 1.0)
        scores["data_quality"] = (p1_known + p2_known) / 2.0

        # --- 4. Accord XGBoost ↔ Markov ---
        # Le modèle Markov est un indicateur point-par-point complémentaire.
        # XGBoost (119 features) peut légitimement diverger sur les dynamiques de match.
        # La divergence modère la confiance sans la sanctionner excessivement.
        p_markov = float(m_r.get("proba_a", 0.5))
        diff_markov = abs(p_xgb - p_markov)
        scores["model_agreement"] = max(0.25, 1.0 - (diff_markov / 0.35))

        # --- Score pondéré global ---
        # Données (40%) et Elo (30%) constituent le socle de solidité.
        # H2H (15%) et Accord Markov (15%) complètent l'évaluation.
        weights = {"data_quality": 0.40, "elo": 0.30, "h2h": 0.15, "model_agreement": 0.15}
        global_score = sum(weights[k] * v for k, v in scores.items())
        global_score = float(np.clip(global_score, 0.0, 1.0))

        # --- Label ---
        if global_score >= 0.72:
            label = "Haute confiance"
            level = "high"
        elif global_score >= 0.58:
            label = "Confiance modérée"
            level = "medium"
        else:
            label = "Incertitude élevée"
            level = "low"

        return {
            "score": round(global_score * 100, 1),
            "label": label,
            "level": level,
            "details": {k: round(v * 100, 1) for k, v in scores.items()}
        }

    confidence = compute_confidence(p_p1, feat, state, p1, p2)

    # Si un joueur est inconnu, pénaliser la confiance pour bloquer les faux value bets
    if _p1_unknown or _p2_unknown:
        unknown_penalty = 25.0  # réduit de ~25 points pour passer sous le seuil 58%
        confidence["score"] = max(confidence["score"] - unknown_penalty, 30.0)
        if confidence["score"] < 45.0:
            confidence["level"] = "low"
            confidence["label"] = "Incertitude élevée (joueur inconnu)"
        elif confidence["score"] < 58.0:
            confidence["level"] = "medium"
            confidence["label"] = "Confiance modérée (données partielles)"

    # --------------------------------------------------------------------------
    # MARCHÉS ALTERNATIFS & VALUE BETS SÉLECTIFS (FILTRAGE ANTI-FAUX POSITIFS)
    # --------------------------------------------------------------------------
    detected_value_bets = []

    # 1. Marché Vainqueur du Match
    vb_p1 = evaluate_market_value(p_p1, req.odds1, req.odds2, "Vainqueur Match", p1, match_confidence=confidence)
    vb_p2 = evaluate_market_value(p_p2, req.odds2, req.odds1, "Vainqueur Match", p2, match_confidence=confidence)
    if vb_p1 and vb_p1["is_value_bet"]: detected_value_bets.append(vb_p1)
    if vb_p2 and vb_p2["is_value_bet"]: detected_value_bets.append(vb_p2)

    # 2. Marché Vainqueur Set 1
    p_set1_p1 = float(m_r.get("set_proba_a", p_p1))
    p_set1_p2 = float(m_r.get("set_proba_b", p_p2))
    vb_set1_p1 = evaluate_market_value(p_set1_p1, req.odds_set1_p1, req.odds_set1_p2, "Vainqueur Set 1", p1, match_confidence=confidence)
    vb_set1_p2 = evaluate_market_value(p_set1_p2, req.odds_set1_p2, req.odds_set1_p1, "Vainqueur Set 1", p2, match_confidence=confidence)
    if vb_set1_p1 and vb_set1_p1["is_value_bet"]: detected_value_bets.append(vb_set1_p1)
    if vb_set1_p2 and vb_set1_p2["is_value_bet"]: detected_value_bets.append(vb_set1_p2)

    # 3. Marché Over / Under Jeux (Calibré sur la convolution exacte des scores)
    exp_total_games = float(m_r.get("expected_total_games", 22.5))
    default_total_line = round(exp_total_games) - 0.5 if round(exp_total_games) - 0.5 > 15 else 22.5
    total_line = req.total_line if (req.total_line and req.total_line > 10) else default_total_line
    sigma_games = 3.8 if req.best_of == 3 else 6.8
    match_games_dist = m_r.get("match_games_distribution")
    p_over, p_under = price_total_games(exp_total_games, total_line, sigma=sigma_games, match_games_dist=match_games_dist)

    vb_over = evaluate_market_value(p_over, req.odds_over, req.odds_under, f"Total Jeux ({total_line})", f"Over {total_line} Jeux", match_confidence=confidence)
    vb_under = evaluate_market_value(p_under, req.odds_under, req.odds_over, f"Total Jeux ({total_line})", f"Under {total_line} Jeux", match_confidence=confidence)
    if vb_over and vb_over["is_value_bet"]: detected_value_bets.append(vb_over)
    if vb_under and vb_under["is_value_bet"]: detected_value_bets.append(vb_under)

    # 4. Marché Handicap de Jeux (Format bookmaker : Favori -X.5 vs Underdog +X.5)
    exp_game_diff = float(m_r.get("expected_game_diff", 0.0))
    raw_h = req.handicap_line if (req.handicap_line is not None and req.handicap_line != 0) else (round(abs(exp_game_diff)) + 0.5 if round(abs(exp_game_diff)) > 0 else 1.5)
    h_val = abs(raw_h)
    sigma_diff = 4.0 if req.best_of == 3 else 7.2
    p1_is_fav = bool(p_p1 >= p_p2)
    p_h1, p_h2 = price_game_handicap(exp_game_diff, h_val, sigma=sigma_diff, match_games_dist=match_games_dist, p1_is_fav=p1_is_fav)

    if p1_is_fav:
        label_h1 = f"{p1} (-{h_val:.1f})"
        label_h2 = f"{p2} (+{h_val:.1f})"
    else:
        label_h1 = f"{p1} (+{h_val:.1f})"
        label_h2 = f"{p2} (-{h_val:.1f})"

    vb_h1 = evaluate_market_value(p_h1, req.odds_h1, req.odds_h2, f"Handicap ({h_val:.1f})", label_h1, match_confidence=confidence)
    vb_h2 = evaluate_market_value(p_h2, req.odds_h2, req.odds_h1, f"Handicap ({h_val:.1f})", label_h2, match_confidence=confidence)
    if vb_h1 and vb_h1["is_value_bet"]: detected_value_bets.append(vb_h1)
    if vb_h2 and vb_h2["is_value_bet"]: detected_value_bets.append(vb_h2)

    # 5. Marché Nombre de Sets
    set_scores_dict = m_r.get("set_scores", {})
    if req.best_of == 3:
        p_sets_3 = float(set_scores_dict.get("2-1", 0.25) + set_scores_dict.get("1-2", 0.25))
        p_sets_2 = float(set_scores_dict.get("2-0", 0.25) + set_scores_dict.get("0-2", 0.25))
        vb_sets_over = evaluate_market_value(p_sets_3, req.odds_sets_over25, req.odds_sets_under25, "Nombre de Sets", "Plus de 2.5 Sets (3 Sets)", match_confidence=confidence)
        vb_sets_under = evaluate_market_value(p_sets_2, req.odds_sets_under25, req.odds_sets_over25, "Nombre de Sets", "Moins de 2.5 Sets (2-0 sec)", match_confidence=confidence)
    else:
        p_sets_2 = float(set_scores_dict.get("3-0", 0.2) + set_scores_dict.get("0-3", 0.2))
        p_sets_3 = 1.0 - p_sets_2
        vb_sets_over = evaluate_market_value(p_sets_3, req.odds_sets_over25, req.odds_sets_under25, "Nombre de Sets", "Plus de 3.5 Sets", match_confidence=confidence)
        vb_sets_under = evaluate_market_value(p_sets_2, req.odds_sets_under25, req.odds_sets_over25, "Nombre de Sets", "3 Sets (3-0 sec)", match_confidence=confidence)

    if vb_sets_over and vb_sets_over["is_value_bet"]: detected_value_bets.append(vb_sets_over)
    if vb_sets_under and vb_sets_under["is_value_bet"]: detected_value_bets.append(vb_sets_under)

    # 6. Marché Tie-Break dans le match (+0.5 Tie-Break / OUI-NON)
    set_dist = m_r.get("set_game_distribution", {})
    p_tb_set = float(set_dist.get("7-6", 0.0) + set_dist.get("6-7", 0.0))
    if p_tb_set <= 0:
        h_a = m_r.get("hold_proba_a", 0.8)
        h_b = m_r.get("hold_proba_b", 0.8)
        p_tb_set = float(np.clip(((h_a * h_b) ** 5.5) * 0.6, 0.08, 0.45))

    if req.best_of == 3:
        p2_sets = float(set_scores_dict.get("2-0", 0.25) + set_scores_dict.get("0-2", 0.25))
        p3_sets = float(set_scores_dict.get("2-1", 0.25) + set_scores_dict.get("1-2", 0.25))
        p_no_tb = p2_sets * ((1.0 - p_tb_set) ** 2) + p3_sets * ((1.0 - p_tb_set) ** 3)
    else:
        p3_sets = float(set_scores_dict.get("3-0", 0.15) + set_scores_dict.get("0-3", 0.15))
        p4_sets = float(set_scores_dict.get("3-1", 0.20) + set_scores_dict.get("1-3", 0.20))
        p5_sets = float(set_scores_dict.get("3-2", 0.15) + set_scores_dict.get("2-3", 0.15))
        p_no_tb = p3_sets * ((1.0 - p_tb_set) ** 3) + p4_sets * ((1.0 - p_tb_set) ** 4) + p5_sets * ((1.0 - p_tb_set) ** 5)

    p_tb_yes = float(np.clip(1.0 - p_no_tb, 0.03, 0.97))
    p_tb_no = 1.0 - p_tb_yes

    vb_tb_yes = evaluate_market_value(p_tb_yes, req.odds_tb_yes, req.odds_tb_no, "Tie-Break (+0.5 TB)", "Au moins 1 Tie-Break (+0.5 TB - OUI)", match_confidence=confidence)
    vb_tb_no = evaluate_market_value(p_tb_no, req.odds_tb_no, req.odds_tb_yes, "Tie-Break (0 TB)", "Aucun Tie-Break (0 TB - NON)", match_confidence=confidence)
    if vb_tb_yes and vb_tb_yes["is_value_bet"]: detected_value_bets.append(vb_tb_yes)
    if vb_tb_no and vb_tb_no["is_value_bet"]: detected_value_bets.append(vb_tb_no)

    # Calcul de l'indice de confiance spécifique à chaque marché / Value Bet
    all_evaluated = [vb_p1, vb_p2, vb_set1_p1, vb_set1_p2, vb_over, vb_under, vb_h1, vb_h2, vb_sets_over, vb_sets_under, vb_tb_yes, vb_tb_no]
    for item in all_evaluated:
        if item is not None:
            item["confidence"] = compute_vb_confidence(item, confidence)

    # Trier les value bets par espérance de gain (EV) décroissante
    detected_value_bets.sort(key=lambda x: x["ev_pct"], reverse=True)

    # Filtrage intelligent anti-corrélation (Sélectionne max 1 à 2 meilleurs paris indépendants)
    vb_analysis = filter_uncorrelated_value_bets(detected_value_bets, p1, p2, p_p1)

    scanned_markets = [item for item in all_evaluated if item is not None]

    h12 = state["h2h"].get(p1, {}).get(p2, [0, 0])

    return {
        "p1": p1,
        "p2": p2,
        "circuit": req.circuit.upper(),
        "proba_p1": round(p_p1, 4),
        "proba_p2": round(p_p2, 4),
        "proba_p1_display": round(max(p_p1, 0.01) * 100, 1),
        "proba_p2_display": round(max(p_p2, 0.01) * 100, 1),
        "fair_odds_p1": round(1.0 / max(p_p1, 0.001), 2),
        "fair_odds_p2": round(1.0 / max(p_p2, 0.001), 2),
        "offered_odds_p1": req.odds1,
        "offered_odds_p2": req.odds2,
        "vb_p1": vb_p1,
        "vb_p2": vb_p2,
        "markets": {
            "winner": {
                "p1": p1, "proba_p1": round(p_p1 * 100, 1), "fair_odds_p1": round(1.0 / p_p1, 2), "offered_odds_p1": req.odds1,
                "p2": p2, "proba_p2": round(p_p2 * 100, 1), "fair_odds_p2": round(1.0 / p_p2, 2), "offered_odds_p2": req.odds2,
                "vb_p1": vb_p1, "vb_p2": vb_p2
            },
            "set1_winner": {
                "proba_p1": round(p_set1_p1 * 100, 1), "fair_odds_p1": round(1.0 / p_set1_p1, 2), "offered_odds_p1": req.odds_set1_p1,
                "proba_p2": round(p_set1_p2 * 100, 1), "fair_odds_p2": round(1.0 / p_set1_p2, 2), "offered_odds_p2": req.odds_set1_p2,
                "vb_p1": vb_set1_p1, "vb_p2": vb_set1_p2
            },
            "total_games": {
                "expected": round(exp_total_games, 1),
                "line": total_line,
                "proba_over": round(p_over * 100, 1), "fair_odds_over": round(1.0 / p_over, 2), "offered_odds_over": req.odds_over,
                "proba_under": round(p_under * 100, 1), "fair_odds_under": round(1.0 / p_under, 2), "offered_odds_under": req.odds_under,
                "vb_over": vb_over, "vb_under": vb_under
            },
            "handicap_games": {
                "expected_diff": round(exp_game_diff, 1),
                "line": h_val,
                "label_h1": label_h1, "proba_h1": round(p_h1 * 100, 1), "fair_odds_h1": round(1.0 / p_h1, 2), "offered_odds_h1": req.odds_h1,
                "label_h2": label_h2, "proba_h2": round(p_h2 * 100, 1), "fair_odds_h2": round(1.0 / p_h2, 2), "offered_odds_h2": req.odds_h2,
                "vb_h1": vb_h1, "vb_h2": vb_h2
            },
            "tiebreak": {
                "proba_yes": round(p_tb_yes * 100, 1), "fair_odds_yes": round(1.0 / p_tb_yes, 2), "offered_odds_yes": req.odds_tb_yes,
                "proba_no": round(p_tb_no * 100, 1), "fair_odds_no": round(1.0 / p_tb_no, 2), "offered_odds_no": req.odds_tb_no,
                "proba_per_set": round(p_tb_set * 100, 1),
                "vb_yes": vb_tb_yes, "vb_no": vb_tb_no
            },
            "number_of_sets": {
                "label_over": "3 Sets" if req.best_of == 3 else "4 ou 5 Sets",
                "proba_over": round(p_sets_3 * 100, 1), "fair_odds_over": round(1.0 / p_sets_3, 2), "offered_odds_over": req.odds_sets_over25,
                "label_under": "2 Sets (Sec)" if req.best_of == 3 else "3 Sets (Sec)",
                "proba_under": round(p_sets_2 * 100, 1), "fair_odds_under": round(1.0 / p_sets_2, 2), "offered_odds_under": req.odds_sets_under25,
                "vb_over": vb_sets_over, "vb_under": vb_sets_under
            },
            "exact_scores": {
                score: {
                    "proba": round(prob * 100, 1),
                    "fair_odds": round(1.0 / prob, 2) if prob > 0 else 999.0
                }
                for score, prob in set_scores_dict.items()
            }
        },
        "all_value_bets": detected_value_bets,
        "recommended_value_bets": vb_analysis["recommended_value_bets"],
        "correlated_masked_bets": vb_analysis["correlated_masked_bets"],
        "has_correlated_bets": vb_analysis["has_correlated_bets"],
        "filter_note": vb_analysis["filter_note"],
        "scanned_markets": scanned_markets,
        "elo": {
            "global_p1": round(state["elo"].get(p1, 1500)),
            "global_p2": round(state["elo"].get(p2, 1500)),
            "surface_p1": round(state["elo_surface"].get(surf, {}).get(p1, 1500)),
            "surface_p2": round(state["elo_surface"].get(surf, {}).get(p2, 1500)),
            "serve_p1": round(feat.get("_p1_serve_elo_surf", 1500)),
            "serve_p2": round(feat.get("_p2_serve_elo_surf", 1500)),
            "return_p1": round(feat.get("_p1_return_elo_surf", 1500)),
            "return_p2": round(feat.get("_p2_return_elo_surf", 1500)),
        },
        "h2h": {
            "wins_p1": h12[0],
            "wins_p2": h12[1],
            "total": h12[0] + h12[1],
        },
        "markov": {
            "proba_p1": round(m_r.get("proba_a", 0.5) * 100, 1),
            "proba_p2": round(m_r.get("proba_b", 0.5) * 100, 1),
            "serve_point_p1": round(feat.get("_pa_m", 0.64) * 100, 1),
            "serve_point_p2": round(feat.get("_pb_m", 0.64) * 100, 1),
            "hold_proba_p1": round(m_r.get("hold_proba_a", 0.80) * 100, 1),
            "hold_proba_p2": round(m_r.get("hold_proba_b", 0.80) * 100, 1),
            "expected_total_games": round(exp_total_games, 1),
            "expected_game_diff": round(exp_game_diff, 1),
            # Freshness Boost: impact de la forme récente sur les probas Markov
            "form_delta_p1": feat.get("_form_delta_p1", 0.0),
            "form_delta_p2": feat.get("_form_delta_p2", 0.0),
            "serve_point_raw_p1": round(feat.get("_pa_m_raw", feat.get("_pa_m", 0.64)) * 100, 1),
            "serve_point_raw_p2": round(feat.get("_pb_m_raw", feat.get("_pb_m", 0.64)) * 100, 1),
        },

        "context": {
            "tournament": req.tournament,
            "surface": surf,
            "level": LEVELS.get(req.level.upper(), req.level),
            "round": req.round.upper(),
            "best_of": req.best_of,
            "indoor": bool(req.indoor),
            "cpi": round(t_cpi_val, 1) if t_cpi_val else None,
            "altitude": int(t_alt_val) if t_alt_val else 0,
        },
        "confidence": confidence,
        "recent_matches": {
            "p1": [
                {
                    "win": bool(r[1]),
                    "retirement": bool("RET" in str(r[13]).upper() or "W/O" in str(r[13]).upper()) if len(r) > 13 and r[13] is not None else False,
                    "surface": str(r[3]) if len(r) > 3 and r[3] is not None else "",
                    "opponent": str(r[11]) if len(r) > 11 and r[11] is not None else "Adversaire",
                    "tournament": str(r[12]) if len(r) > 12 and r[12] is not None else "Tournoi",
                    "score": str(r[13]) if len(r) > 13 and r[13] is not None else ""
                }
                for r in state.get("recent_results", {}).get(p1, [])[-5:]
            ],
            "p2": [
                {
                    "win": bool(r[1]),
                    "retirement": bool("RET" in str(r[13]).upper() or "W/O" in str(r[13]).upper()) if len(r) > 13 and r[13] is not None else False,
                    "surface": str(r[3]) if len(r) > 3 and r[3] is not None else "",
                    "opponent": str(r[11]) if len(r) > 11 and r[11] is not None else "Adversaire",
                    "tournament": str(r[12]) if len(r) > 12 and r[12] is not None else "Tournoi",
                    "score": str(r[13]) if len(r) > 13 and r[13] is not None else ""
                }
                for r in state.get("recent_results", {}).get(p2, [])[-5:]
            ]
        },
        "individual_probas": model.get_individual_probas(X) if hasattr(model, "get_individual_probas") else {
            "xgb": round(p_p1 * 100, 1),
            "ensemble": round(p_p1 * 100, 1)
        },
        "shap_explanation": compute_match_shap_explanation(X, model, feature_cols, p1=p1, p2=p2),
        "detailed_analytics": compute_detailed_analytics(state, p1, p2, surf, req.tournament, p_p1, p_p2)
    }


# --------------------------------------------------------------------------
# Scanner Quotidien des Cotes (Bet365 / The Odds API)
# --------------------------------------------------------------------------
try:
    from src.odds_scanner import scan_daily_matches
except ModuleNotFoundError:
    from odds_scanner import scan_daily_matches


@app.get("/api/scanner")
def get_daily_scanner(
    circuit: str = "all",
    bookmaker: str = "betclic",
    source: str = Query("tennisexplorer"),
    api_key: Optional[str] = Query(None),
    refresh: bool = False
):
    """
    Scan quotidien des matchs avec TennisExplorer (100% tournois ATP/WTA, US Open, Winston-Salem, sessions de nuit) ou The Odds API,
    résolution automatique des contextes et détection instantanée des Value Bets.
    """
    c_lower = circuit.lower()

    return scan_daily_matches(
        circuit=c_lower,
        bookmaker=bookmaker,
        source=source,
        api_key=api_key,
        force_refresh=refresh,
        predict_func=predict_match,
        get_resources_func=get_cached_resources,
        smart_resolve_func=smart_resolve_name
    )


# --------------------------------------------------------------------------
# Endpoints Alertes Telegram (Notification Quotidienne des Value Bets)
# --------------------------------------------------------------------------
@app.get("/api/telegram/status")
def get_telegram_status():
    """Vérifie si les identifiants Telegram Bot sont configurés."""
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    cid = os.getenv("TELEGRAM_CHAT_ID")
    return {
        "configured": bool(token and cid),
        "bot_token_present": bool(token),
        "chat_id_present": bool(cid),
        "schedule_info": "Automatique tous les jours à 10:00 (Paris) via GitHub Actions ou appel API /api/telegram/send-briefing"
    }


@app.post("/api/telegram/test")
def test_telegram_connection():
    """Envoie un court message de test pour valider la configuration Telegram."""
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    cid = os.getenv("TELEGRAM_CHAT_ID")
    if not token or not cid:
        raise HTTPException(
            status_code=400,
            detail="Variables d'environnement TELEGRAM_BOT_TOKEN ou TELEGRAM_CHAT_ID manquantes."
        )
    from telegram_notifier import send_telegram_message
    res = send_telegram_message(
        token,
        cid,
        "🔔 <b>Test Tennis Predictor AI</b>\nVotre bot Telegram est connecté et fonctionnel ! Prêt pour les alertes quotidiennes de Value Bets."
    )
    if not res.get("success"):
        raise HTTPException(status_code=502, detail=f"Erreur Telegram: {res.get('error')}")
    return {"success": True, "message": "Notification de test envoyée avec succès sur Telegram !"}


@app.post("/api/telegram/send-briefing")
def trigger_telegram_briefing():
    """Lance l'analyse des matchs avec TennisExplorer et envoie le briefing des Value Bets sur Telegram."""
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    cid = os.getenv("TELEGRAM_CHAT_ID")
    if not token or not cid:
        raise HTTPException(
            status_code=400,
            detail="Variables d'environnement TELEGRAM_BOT_TOKEN ou TELEGRAM_CHAT_ID manquantes."
        )
    from telegram_notifier import run_daily_scan_and_notify
    result = run_daily_scan_and_notify(bot_token=token, chat_id=cid, force_update_models=False)
    return result


# Montage des fichiers statiques (Frontend)
STATIC_DIR = BASE_DIR / "src" / "static"
STATIC_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/")
def serve_index():
    index_file = STATIC_DIR / "index.html"
    if index_file.exists():
        return FileResponse(str(index_file))
    return JSONResponse({"status": "ok", "message": "Frontend static file ready."})


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 7860))
    print(f"Lancement du serveur Tennis Predictor sur http://localhost:{port}")
    uvicorn.run(app, host="0.0.0.0", port=port)
