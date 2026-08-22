"""
Serveur FastAPI pour l'Application Web & Mobile de Prédiction Tennis XGBoost.
Supporte les circuits ATP & WTA, autocomplétion des joueurs, inférence instantanée,
et détection de Value Bets avec calcul de mise Kelly.
"""

import os
import sys
import datetime
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
    print("🎾 Serveur Tennis Match Predictor prêt !")


def get_circuit_players(circuit: str) -> List[Dict[str, Any]]:
    c = circuit.lower()
    if c not in PLAYERS_CACHE:
        p_file = BASE_DIR / "data" / "processed" / f"players_{c}.json"
        if p_file.exists():
            import json
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


@app.get("/api/status")
def get_status():
    """Endpoint de santé rapide et ultra-léger (ne charge pas les modèles ML)."""
    players_atp = get_circuit_players("atp")
    players_wta = get_circuit_players("wta")
    return {
        "status": "online",
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
        import json
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
    import re
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


import difflib


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
    selection: str
) -> Optional[Dict[str, Any]]:
    """
    Évalue un Value Bet de façon rigoureuse et sélective :
    1. Retire la marge (overround / vig) du bookmaker si les deux côtés du marché sont renseignés.
    2. Exige un Edge net d'au moins 4.5% ET une Espérance de gain (EV) d'au moins +5.0% pour éliminer le bruit.
    """
    if not odds or odds <= 1.0 or prob <= 0.01 or prob >= 0.99:
        return None

    fair_odds = round(1.0 / prob, 2)
    ev = (prob * odds) - 1.0

    # Déduction de la vraie probabilité implicite du marché (sans la marge du bookmaker)
    if opp_odds and opp_odds > 1.0:
        inv1 = 1.0 / odds
        inv2 = 1.0 / opp_odds
        market_prob = inv1 / (inv1 + inv2)
    else:
        # Si seule une cote est renseignée, on applique une marge bookmaker standard de 5.5%
        market_prob = (1.0 / odds) / 1.055

    edge = prob - market_prob

    # Seuil strict de sélection : Edge >= 4.5% et EV >= +5.0% (évite les faux positifs)
    is_vb = bool(edge >= 0.045 and ev >= 0.050)

    b = odds - 1.0
    kelly_full = max(0.0, min((prob * odds - 1.0) / b, 0.15)) if b > 0 else 0.0
    kelly_half = max(0.0, min(kelly_full * 0.50, 0.08))
    kelly_quarter = max(0.0, min(kelly_full * 0.25, 0.05))

    return {
        "market": market_name,
        "selection": selection,
        "prob": round(prob * 100, 1),
        "fair_odds": fair_odds,
        "offered_odds": odds,
        "ev_pct": round(ev * 100, 1),
        "edge_pct": round(edge * 100, 1),
        "kelly_pct": round(kelly_quarter * 100, 1) if is_vb else 0.0,
        "kelly_quarter_pct": round(kelly_quarter * 100, 1) if is_vb else 0.0,
        "kelly_half_pct": round(kelly_half * 100, 1) if is_vb else 0.0,
        "kelly_full_pct": round(kelly_full * 100, 1) if is_vb else 0.0,
        "is_value_bet": is_vb,
        "badge": "VALUE_BET" if is_vb else ("LOW_EV" if (edge >= 0.02 and ev >= 0.02) else "NO_VALUE")
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
    """Résolution intelligente et robuste du nom du joueur."""
    if not name or not isinstance(name, str):
        return name
    clean_name = name.strip()
    clean_lower = clean_name.lower()
    if not clean_lower:
        return name

    # 1. Correspondance exacte
    for p in known:
        if p.lower() == clean_lower:
            return p

    q_tokens = clean_lower.split()
    last_day = state.get("last_day", 9727)
    candidates = []

    for p in known:
        p_lower = p.lower()
        p_tokens = p_lower.split()

        # Inversion des noms / correspondance de jetons (ex. 'Cobolli Flavio', 'Pow Luca')
        if set(q_tokens) == set(p_tokens):
            match_score = 1000
        elif clean_lower in p_lower:
            match_score = 500
        elif all(any(pt.startswith(qt) or qt in pt for pt in p_tokens) for qt in q_tokens):
            match_score = 400
        elif any(pt.startswith(clean_lower) for pt in p_tokens) or p_lower.startswith(clean_lower):
            match_score = 300
        else:
            ratio = difflib.SequenceMatcher(None, clean_lower, p_lower).ratio()
            if ratio >= 0.70:
                match_score = int(ratio * 250)
            else:
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


@app.post("/api/update-data")
def update_data():
    """Lance la synchronisation complète : téléchargement, constitution du dataset et recalcul des stats."""
    import subprocess
    try:
        # 1. Télécharger les matchs récents
        subprocess.run([sys.executable, str(BASE_DIR / "src" / "00_download_data.py")], check=True, timeout=120)
        
        # 2. Reconstruire le dataset pour ATP et WTA
        subprocess.run([sys.executable, str(BASE_DIR / "src" / "01_build_dataset.py"), "--circuit", "atp"], check=True, timeout=120)
        subprocess.run([sys.executable, str(BASE_DIR / "src" / "01_build_dataset.py"), "--circuit", "wta"], check=True, timeout=120)
        
        # 3. Recalculer le feature engineering et l'état des joueurs
        subprocess.run([sys.executable, str(BASE_DIR / "src" / "02_feature_engineering.py"), "--circuit", "atp"], check=True, timeout=400)
        subprocess.run([sys.executable, str(BASE_DIR / "src" / "02_feature_engineering.py"), "--circuit", "wta"], check=True, timeout=300)

        # Réinitialisation des caches pour prise en compte immédiate
        CACHE.clear()
        PLAYERS_CACHE.clear()

        now_str = datetime.datetime.now().strftime("%d/%m/%Y à %H:%M")
        return {
            "success": True,
            "message": "Données, tournois et statistiques des joueurs synchronisés avec succès !",
            "timestamp": now_str,
            "output": ""
        }
    except Exception as e:
        return {
            "success": False,
            "message": f"Erreur lors de la synchronisation : {str(e)}",
            "timestamp": datetime.datetime.now().strftime("%d/%m/%Y à %H:%M")
        }


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

        # --- 1. Ecart Elo ---
        elo1 = state["elo"].get(p1, 1500)
        elo2 = state["elo"].get(p2, 1500)
        elo_gap = abs(elo1 - elo2)
        # 0-50 pts = très incertain, 50-150 = moyen, 150-300 = bon, 300+ = fort
        if elo_gap >= 300:
            scores["elo"] = 1.0
        elif elo_gap >= 150:
            scores["elo"] = 0.7 + (elo_gap - 150) / 500
        elif elo_gap >= 50:
            scores["elo"] = 0.3 + (elo_gap - 50) / 333
        else:
            scores["elo"] = elo_gap / 166.7

        # --- 2. Volume H2H ---
        h12 = state["h2h"].get(p1, {}).get(p2, [0, 0])
        h_total = h12[0] + h12[1]
        scores["h2h"] = min(h_total / 8.0, 1.0)  # 8+ matchs H2H = confiance max

        # --- 3. Volume de données joueur (matchs récents) ---
        rr = state.get("recent_results", {})
        rr1_len = len(rr.get(p1, []))
        rr2_len = len(rr.get(p2, []))
        # Pénalise si l'un des joueurs a très peu de données
        p1_known = min(rr1_len / 20.0, 1.0)
        p2_known = min(rr2_len / 20.0, 1.0)
        scores["data_quality"] = (p1_known + p2_known) / 2.0

        # --- 4. Accord XGBoost ↔ Markov ---
        p_markov = float(m_r.get("proba_a", 0.5))
        agreement = 1.0 - min(abs(p_xgb - p_markov) / 0.20, 1.0)  # ±20% = max désaccord
        scores["model_agreement"] = agreement

        # --- Score pondéré global ---
        weights = {"elo": 0.35, "h2h": 0.10, "data_quality": 0.30, "model_agreement": 0.25}
        global_score = sum(weights[k] * v for k, v in scores.items())
        global_score = float(np.clip(global_score, 0.0, 1.0))

        # --- Label ---
        if global_score >= 0.72:
            label = "Haute confiance"
            level = "high"
        elif global_score >= 0.45:
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

    # --------------------------------------------------------------------------
    # MARCHÉS ALTERNATIFS & VALUE BETS SÉLECTIFS (FILTRAGE ANTI-FAUX POSITIFS)
    # --------------------------------------------------------------------------
    detected_value_bets = []

    # 1. Marché Vainqueur du Match
    vb_p1 = evaluate_market_value(p_p1, req.odds1, req.odds2, "Vainqueur Match", p1)
    vb_p2 = evaluate_market_value(p_p2, req.odds2, req.odds1, "Vainqueur Match", p2)
    if vb_p1 and vb_p1["is_value_bet"]: detected_value_bets.append(vb_p1)
    if vb_p2 and vb_p2["is_value_bet"]: detected_value_bets.append(vb_p2)

    # 2. Marché Vainqueur Set 1
    p_set1_p1 = float(m_r.get("set_proba_a", p_p1))
    p_set1_p2 = float(m_r.get("set_proba_b", p_p2))
    vb_set1_p1 = evaluate_market_value(p_set1_p1, req.odds_set1_p1, req.odds_set1_p2, "Vainqueur Set 1", p1)
    vb_set1_p2 = evaluate_market_value(p_set1_p2, req.odds_set1_p2, req.odds_set1_p1, "Vainqueur Set 1", p2)
    if vb_set1_p1 and vb_set1_p1["is_value_bet"]: detected_value_bets.append(vb_set1_p1)
    if vb_set1_p2 and vb_set1_p2["is_value_bet"]: detected_value_bets.append(vb_set1_p2)

    # 3. Marché Over / Under Jeux (Calibré sur la convolution exacte des scores)
    exp_total_games = float(m_r.get("expected_total_games", 22.5))
    default_total_line = round(exp_total_games) - 0.5 if round(exp_total_games) - 0.5 > 15 else 22.5
    total_line = req.total_line if (req.total_line and req.total_line > 10) else default_total_line
    sigma_games = 3.8 if req.best_of == 3 else 6.8
    match_games_dist = m_r.get("match_games_distribution")
    p_over, p_under = price_total_games(exp_total_games, total_line, sigma=sigma_games, match_games_dist=match_games_dist)

    vb_over = evaluate_market_value(p_over, req.odds_over, req.odds_under, f"Total Jeux ({total_line})", f"Over {total_line} Jeux")
    vb_under = evaluate_market_value(p_under, req.odds_under, req.odds_over, f"Total Jeux ({total_line})", f"Under {total_line} Jeux")
    if vb_over and vb_over["is_value_bet"]: detected_value_bets.append(vb_over)
    if vb_under and vb_under["is_value_bet"]: detected_value_bets.append(vb_under)

    # 4. Marché Handicap de Jeux (Format bookmaker : J1 -X.5 vs J2 +X.5 - Distribution exacte)
    exp_game_diff = float(m_r.get("expected_game_diff", 0.0))
    raw_h = req.handicap_line if (req.handicap_line is not None and req.handicap_line != 0) else (round(abs(exp_game_diff)) + 0.5 if round(abs(exp_game_diff)) > 0 else 1.5)
    h_val = abs(raw_h)
    sigma_diff = 4.0 if req.best_of == 3 else 7.2
    p_h1, p_h2 = price_game_handicap(exp_game_diff, h_val, sigma=sigma_diff, match_games_dist=match_games_dist)

    label_h1 = f"{p1} (-{h_val:.1f})"
    label_h2 = f"{p2} (+{h_val:.1f})"
    vb_h1 = evaluate_market_value(p_h1, req.odds_h1, req.odds_h2, f"Handicap ({h_val:.1f})", label_h1)
    vb_h2 = evaluate_market_value(p_h2, req.odds_h2, req.odds_h1, f"Handicap ({h_val:.1f})", label_h2)
    if vb_h1 and vb_h1["is_value_bet"]: detected_value_bets.append(vb_h1)
    if vb_h2 and vb_h2["is_value_bet"]: detected_value_bets.append(vb_h2)

    # 5. Marché Nombre de Sets
    set_scores_dict = m_r.get("set_scores", {})
    if req.best_of == 3:
        p_sets_3 = float(set_scores_dict.get("2-1", 0.25) + set_scores_dict.get("1-2", 0.25))
        p_sets_2 = float(set_scores_dict.get("2-0", 0.25) + set_scores_dict.get("0-2", 0.25))
        vb_sets_over = evaluate_market_value(p_sets_3, req.odds_sets_over25, req.odds_sets_under25, "Nombre de Sets", "Plus de 2.5 Sets (3 Sets)")
        vb_sets_under = evaluate_market_value(p_sets_2, req.odds_sets_under25, req.odds_sets_over25, "Nombre de Sets", "Moins de 2.5 Sets (2-0 sec)")
    else:
        p_sets_2 = float(set_scores_dict.get("3-0", 0.2) + set_scores_dict.get("0-3", 0.2))
        p_sets_3 = 1.0 - p_sets_2
        vb_sets_over = evaluate_market_value(p_sets_3, req.odds_sets_over25, req.odds_sets_under25, "Nombre de Sets", "Plus de 3.5 Sets")
        vb_sets_under = evaluate_market_value(p_sets_2, req.odds_sets_under25, req.odds_sets_over25, "Nombre de Sets", "3 Sets (3-0 sec)")

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

    vb_tb_yes = evaluate_market_value(p_tb_yes, req.odds_tb_yes, req.odds_tb_no, "Tie-Break (+0.5 TB)", "Au moins 1 Tie-Break (+0.5 TB - OUI)")
    vb_tb_no = evaluate_market_value(p_tb_no, req.odds_tb_no, req.odds_tb_yes, "Tie-Break (0 TB)", "Aucun Tie-Break (0 TB - NON)")
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
        "fair_odds_p1": round(1.0 / p_p1, 2) if p_p1 > 0 else 999.0,
        "fair_odds_p2": round(1.0 / p_p2, 2) if p_p2 > 0 else 999.0,
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
        }
    }


# --------------------------------------------------------------------------
# Scanner Quotidien des Cotes (Bet365 / The Odds API)
# --------------------------------------------------------------------------
from src.odds_scanner import scan_daily_matches


@app.get("/api/scanner")
def get_daily_scanner(
    circuit: str = "atp",
    bookmaker: str = "bet365",
    api_key: Optional[str] = Query(None),
    refresh: bool = False
):
    """
    Scan quotidien des matchs avec cotes Bet365 / The Odds API,
    résolution automatique des contextes et détection instantanée des Value Bets.
    """
    c_lower = circuit.lower()
    res = get_cached_resources(c_lower)
    state = res["state"]
    known = res["players"]

    return scan_daily_matches(
        circuit=c_lower,
        bookmaker=bookmaker,
        api_key=api_key,
        force_refresh=refresh,
        predict_func=predict_match,
        known_players=known,
        player_state=state,
        smart_resolve_func=smart_resolve_name
    )


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
