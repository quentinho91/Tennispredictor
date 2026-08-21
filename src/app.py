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


@app.get("/api/tournaments")
def search_tournaments(q: str = Query("", min_length=0), limit: int = 12):
    """Recherche intelligente de tournois avec autocomplétion et métadonnées (surface, niveau, indoor)."""
    tourneys = get_tournaments()
    query = q.lower().strip()
    if not query:
        return tourneys[:limit]

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
    return [item[0] for item in scored[:limit]]


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


def evaluate_market_value(prob: float, odds: Optional[float], market_name: str, selection: str) -> Optional[Dict[str, Any]]:
    """Évalue un Value Bet sur n'importe quel marché (Vainqueur, Over/Under, Handicap, Set, etc.)."""
    if not odds or odds <= 1.0 or prob <= 0.0 or prob >= 1.0:
        return None
    fair_odds = round(1.0 / prob, 2)
    ev = (prob * odds) - 1.0
    implied_prob = 1.0 / odds
    edge = prob - implied_prob
    is_vb = (edge >= 0.025 and ev >= 0.025)

    b = odds - 1.0
    kelly_full = (prob * odds - 1.0) / b if b > 0 else 0.0
    kelly_quarter = max(0.0, min(kelly_full * 0.25, 0.05))

    return {
        "market": market_name,
        "selection": selection,
        "prob": round(prob * 100, 1),
        "fair_odds": fair_odds,
        "offered_odds": odds,
        "ev_pct": round(ev * 100, 1),
        "edge_pct": round(edge * 100, 1),
        "kelly_pct": round(kelly_quarter * 100, 1),
        "is_value_bet": is_vb,
        "badge": "VALUE_BET" if is_vb else ("LOW_EV" if (edge > 0 or ev > 0) else "NO_VALUE")
    }


@app.post("/api/update-data")
def update_data():
    """Lance la synchronisation des données de matchs récents et tournois en direct."""
    import subprocess
    try:
        cmd = [sys.executable, str(BASE_DIR / "src" / "00_download_data.py")]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=90)

        # Réinitialisation des caches pour prise en compte immédiate
        CACHE.clear()
        PLAYERS_CACHE.clear()

        now_str = datetime.datetime.now().strftime("%d/%m/%Y à %H:%M")
        return {
            "success": True,
            "message": "Données et statistiques actualisées avec succès !",
            "timestamp": now_str,
            "output": result.stdout[:200] if result.stdout else ""
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

    def resolve_name(name):
        exact = [p for p in known if p.lower() == name.lower()]
        if exact: return exact[0]
        contains = [p for p in known if name.lower() in p.lower()]
        if contains: return contains[0]
        return name

    p1 = resolve_name(req.p1)
    p2 = resolve_name(req.p2)
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
    # MARCHÉS ALTERNATIFS & VALUE BETS
    # --------------------------------------------------------------------------
    detected_value_bets = []

    # 1. Marché Vainqueur du Match
    vb_p1 = evaluate_market_value(p_p1, req.odds1, "Vainqueur Match", p1)
    vb_p2 = evaluate_market_value(p_p2, req.odds2, "Vainqueur Match", p2)
    if vb_p1 and vb_p1["is_value_bet"]: detected_value_bets.append(vb_p1)
    if vb_p2 and vb_p2["is_value_bet"]: detected_value_bets.append(vb_p2)

    # 2. Marché Vainqueur Set 1
    p_set1_p1 = float(m_r.get("set_proba_a", p_p1))
    p_set1_p2 = float(m_r.get("set_proba_b", p_p2))
    vb_set1_p1 = evaluate_market_value(p_set1_p1, req.odds_set1_p1, "Vainqueur Set 1", p1)
    vb_set1_p2 = evaluate_market_value(p_set1_p2, req.odds_set1_p2, "Vainqueur Set 1", p2)
    if vb_set1_p1 and vb_set1_p1["is_value_bet"]: detected_value_bets.append(vb_set1_p1)
    if vb_set1_p2 and vb_set1_p2["is_value_bet"]: detected_value_bets.append(vb_set1_p2)

    # 3. Marché Over / Under Jeux
    exp_total_games = float(m_r.get("expected_total_games", 22.5))
    default_total_line = round(exp_total_games) - 0.5 if round(exp_total_games) - 0.5 > 15 else 22.5
    total_line = req.total_line if (req.total_line and req.total_line > 10) else default_total_line
    p_over, p_under = price_total_games(exp_total_games, total_line)

    vb_over = evaluate_market_value(p_over, req.odds_over, f"Total Jeux ({total_line})", f"Over {total_line} Jeux")
    vb_under = evaluate_market_value(p_under, req.odds_under, f"Total Jeux ({total_line})", f"Under {total_line} Jeux")
    if vb_over and vb_over["is_value_bet"]: detected_value_bets.append(vb_over)
    if vb_under and vb_under["is_value_bet"]: detected_value_bets.append(vb_under)

    # 4. Marché Handicap de Jeux
    exp_game_diff = float(m_r.get("expected_game_diff", 0.0))
    default_h_line = round(exp_game_diff) - 0.5
    h_line = req.handicap_line if req.handicap_line is not None else default_h_line
    p_h1, p_h2 = price_game_handicap(exp_game_diff, h_line)

    h1_sign = f"{h_line:+.1f}"
    h2_sign = f"{-h_line:+.1f}"
    vb_h1 = evaluate_market_value(p_h1, req.odds_h1, f"Handicap Jeux ({h1_sign})", f"{p1} ({h1_sign})")
    vb_h2 = evaluate_market_value(p_h2, req.odds_h2, f"Handicap Jeux ({h2_sign})", f"{p2} ({h2_sign})")
    if vb_h1 and vb_h1["is_value_bet"]: detected_value_bets.append(vb_h1)
    if vb_h2 and vb_h2["is_value_bet"]: detected_value_bets.append(vb_h2)

    # 5. Marché Nombre de Sets (Over / Under 2.5 sets en Best of 3)
    set_scores_dict = m_r.get("set_scores", {})
    if req.best_of == 3:
        p_sets_3 = float(set_scores_dict.get("2-1", 0.25) + set_scores_dict.get("1-2", 0.25))
        p_sets_2 = float(set_scores_dict.get("2-0", 0.25) + set_scores_dict.get("0-2", 0.25))
        vb_sets_over = evaluate_market_value(p_sets_3, req.odds_sets_over25, "Nombre de Sets", "Plus de 2.5 Sets (3 Sets)")
        vb_sets_under = evaluate_market_value(p_sets_2, req.odds_sets_under25, "Nombre de Sets", "Moins de 2.5 Sets (2-0 sec)")
    else:
        # En Grand Chelem (Best of 5) : 3 sets vs 4-5 sets
        p_sets_2 = float(set_scores_dict.get("3-0", 0.2) + set_scores_dict.get("0-3", 0.2))
        p_sets_3 = 1.0 - p_sets_2
        vb_sets_over = evaluate_market_value(p_sets_3, req.odds_sets_over25, "Nombre de Sets", "Plus de 3.5 Sets")
        vb_sets_under = evaluate_market_value(p_sets_2, req.odds_sets_under25, "Nombre de Sets", "3 Sets (3-0 sec)")

    if vb_sets_over and vb_sets_over["is_value_bet"]: detected_value_bets.append(vb_sets_over)
    if vb_sets_under and vb_sets_under["is_value_bet"]: detected_value_bets.append(vb_sets_under)

    # Trier les value bets par espérance de gain (EV) décroissante
    detected_value_bets.sort(key=lambda x: x["ev_pct"], reverse=True)

    h12 = state["h2h"].get(p1, {}).get(p2, [0, 0])

    return {
        "p1": p1,
        "p2": p2,
        "circuit": req.circuit.upper(),
        "proba_p1": round(p_p1, 4),
        "proba_p2": round(p_p2, 4),
        "fair_odds_p1": round(1.0 / p_p1, 2) if p_p1 > 0 else 999.0,
        "fair_odds_p2": round(1.0 / p_p2, 2) if p_p2 > 0 else 999.0,
        "markets": {
            "winner": {
                "p1": p1, "proba_p1": round(p_p1 * 100, 1), "fair_odds_p1": round(1.0 / p_p1, 2),
                "p2": p2, "proba_p2": round(p_p2 * 100, 1), "fair_odds_p2": round(1.0 / p_p2, 2),
                "vb_p1": vb_p1, "vb_p2": vb_p2
            },
            "set1_winner": {
                "proba_p1": round(p_set1_p1 * 100, 1), "fair_odds_p1": round(1.0 / p_set1_p1, 2),
                "proba_p2": round(p_set1_p2 * 100, 1), "fair_odds_p2": round(1.0 / p_set1_p2, 2),
                "vb_p1": vb_set1_p1, "vb_p2": vb_set1_p2
            },
            "total_games": {
                "expected": round(exp_total_games, 1),
                "line": total_line,
                "proba_over": round(p_over * 100, 1), "fair_odds_over": round(1.0 / p_over, 2),
                "proba_under": round(p_under * 100, 1), "fair_odds_under": round(1.0 / p_under, 2),
                "vb_over": vb_over, "vb_under": vb_under
            },
            "handicap_games": {
                "expected_diff": round(exp_game_diff, 1),
                "line": h_line,
                "label_h1": f"{p1} ({h1_sign})", "proba_h1": round(p_h1 * 100, 1), "fair_odds_h1": round(1.0 / p_h1, 2),
                "label_h2": f"{p2} ({h2_sign})", "proba_h2": round(p_h2 * 100, 1), "fair_odds_h2": round(1.0 / p_h2, 2),
                "vb_h1": vb_h1, "vb_h2": vb_h2
            },
            "number_of_sets": {
                "label_over": "3 Sets" if req.best_of == 3 else "4 ou 5 Sets",
                "proba_over": round(p_sets_3 * 100, 1), "fair_odds_over": round(1.0 / p_sets_3, 2),
                "label_under": "2 Sets (Sec)" if req.best_of == 3 else "3 Sets (Sec)",
                "proba_under": round(p_sets_2 * 100, 1), "fair_odds_under": round(1.0 / p_sets_2, 2),
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
        }
    }


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
