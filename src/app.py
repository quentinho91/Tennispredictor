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

# Cache des ressources en mémoire
CACHE: Dict[str, Any] = {}


def get_cached_resources(circuit: str):
    c = circuit.lower()
    if c not in CACHE:
        state, model, feature_cols = load_resources(c)
        CACHE[c] = {
            "state": state,
            "model": model,
            "feature_cols": feature_cols,
            "players": sorted(state["elo"].keys()),
        }
    return CACHE[c]


# Préchargement au démarrage
@app.on_event("startup")
def startup_event():
    print("Préchargement des modèles ATP & WTA...")
    try:
        get_cached_resources("atp")
        print("  • Modèle ATP chargé avec succès.")
    except Exception as e:
        print(f"  • Erreur ATP: {e}")
    try:
        get_cached_resources("wta")
        print("  • Modèle WTA chargé avec succès.")
    except Exception as e:
        print(f"  • Erreur WTA: {e}")


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
    odds1: Optional[float] = None
    odds2: Optional[float] = None


@app.get("/api/status")
def get_status():
    res_atp = get_cached_resources("atp")
    res_wta = get_cached_resources("wta")
    return {
        "atp": {
            "players_count": len(res_atp["players"]),
            "features_count": len(res_atp["feature_cols"]),
            "last_day": res_atp["state"].get("last_day", 0),
            "date_min": str(res_atp["state"].get("date_min", "2000-01-01")),
        },
        "wta": {
            "players_count": len(res_wta["players"]),
            "features_count": len(res_wta["feature_cols"]),
            "last_day": res_wta["state"].get("last_day", 0),
            "date_min": str(res_wta["state"].get("date_min", "2000-01-01")),
        }
    }


@app.get("/api/players")
def search_players(circuit: str = "atp", q: str = Query("", min_length=1), limit: int = 10):
    res = get_cached_resources(circuit)
    players = res["players"]
    state = res["state"]
    query = q.lower().strip()

    starts = []
    contains = []
    for p in players:
        p_lower = p.lower()
        if p_lower.startswith(query):
            starts.append(p)
        elif query in p_lower:
            contains.append(p)
        if len(starts) >= limit:
            break

    results = (starts + contains)[:limit]
    output = []
    for name in results:
        elo_val = state["elo"].get(name, 1500)
        rank_val = state.get("last_rank", {}).get(name)
        hand_val = state.get("last_hand", {}).get(name, "R")
        output.append({
            "name": name,
            "elo": round(elo_val),
            "rank": int(rank_val) if (rank_val is not None and not np.isnan(rank_val)) else None,
            "hand": hand_val,
        })
    return output


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

    vb_data = {"has_odds": False}
    if req.odds1 and req.odds2 and req.odds1 > 1.0 and req.odds2 > 1.0:
        pm1, pm2 = remove_overround(req.odds1, req.odds2)
        ev1 = p_p1 * req.odds1 - 1.0
        ev2 = p_p2 * req.odds2 - 1.0
        edge1 = p_p1 - pm1
        edge2 = p_p2 - pm2
        threshold = 0.03

        is_vb1 = (edge1 >= threshold and ev1 >= threshold)
        is_vb2 = (edge2 >= threshold and ev2 >= threshold)

        statut1 = "[VALUE BET]" if is_vb1 else ("[EV TROP FAIBLE]" if edge1 > 0 else "[PAS DE VALUE]")
        statut2 = "[VALUE BET]" if is_vb2 else ("[EV TROP FAIBLE]" if edge2 > 0 else "[PAS DE VALUE]")

        best_p = p1 if ev1 > ev2 else p2
        best_o = req.odds1 if ev1 > ev2 else req.odds2
        best_prob = p_p1 if ev1 > ev2 else p_p2
        best_ev = max(ev1, ev2)
        best_edge = max(edge1, edge2)

        b = best_o - 1.0
        kelly_full = (best_prob * best_o - 1.0) / b if b > 0 else 0.0
        kelly_quarter = max(0.0, min(kelly_full * 0.25, 0.05))

        vb_data = {
            "has_odds": True,
            "is_value_bet": bool(is_vb1 or is_vb2),
            "recommended_player": best_p if (is_vb1 or is_vb2) else None,
            "offered_odds": best_o if (is_vb1 or is_vb2) else None,
            "min_odds_required": round(1.0 / best_prob, 2),
            "ev_pct": round(best_ev * 100, 1),
            "edge_pct": round(best_edge * 100, 1),
            "kelly_pct": round(kelly_quarter * 100, 1) if (is_vb1 or is_vb2) else 0.0,
            "decision_badge": "VALUE_BET" if (is_vb1 or is_vb2) else ("LOW_EV" if (best_edge > 0 or best_ev > 0) else "NO_VALUE"),
            "details": [
                {
                    "player": p1,
                    "odds": req.odds1,
                    "min_odds": round(1.0 / p_p1, 2) if p_p1 > 0 else 999.0,
                    "proba": round(p_p1 * 100, 1),
                    "market_proba": round(pm1 * 100, 1),
                    "edge": round(edge1 * 100, 1),
                    "ev": round(ev1 * 100, 1),
                    "status": statut1,
                },
                {
                    "player": p2,
                    "odds": req.odds2,
                    "min_odds": round(1.0 / p_p2, 2) if p_p2 > 0 else 999.0,
                    "proba": round(p_p2 * 100, 1),
                    "market_proba": round(pm2 * 100, 1),
                    "edge": round(edge2 * 100, 1),
                    "ev": round(ev2 * 100, 1),
                    "status": statut2,
                }
            ]
        }

    h12 = state["h2h"].get(p1, {}).get(p2, [0, 0])

    return {
        "p1": p1,
        "p2": p2,
        "circuit": req.circuit.upper(),
        "proba_p1": round(p_p1, 4),
        "proba_p2": round(p_p2, 4),
        "fair_odds_p1": round(1.0 / p_p1, 2) if p_p1 > 0 else 999.0,
        "fair_odds_p2": round(1.0 / p_p2, 2) if p_p2 > 0 else 999.0,
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
            "expected_total_games": round(m_r.get("expected_total_games", 22.5), 1),
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
        "value_bet": vb_data,
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
