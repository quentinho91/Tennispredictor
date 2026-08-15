"""
app.py — Interface web Flask pour le Tennis Match Predictor.

Lance avec :
    python src/app.py
Puis ouvre http://localhost:5000 dans ton navigateur.

Prerequis :
    pip install flask
    # + avoir deja lance 02_feature_engineering.py et 03_train_model.py
"""

from pathlib import Path
import pandas as pd
from flask import Flask, jsonify, request, render_template, Response
import importlib.util
import subprocess
import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
# --------------------------------------------------------------------------
# Import de compute_features / build_row / fuzzy_find / remove_overround
# depuis 05_predict_match.py (qui importe lui-meme depuis 02)
# --------------------------------------------------------------------------
_pred_path = Path(__file__).parent / "05_predict_match.py"
_spec = importlib.util.spec_from_file_location("pred", _pred_path)
pred = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pred)

# --------------------------------------------------------------------------
# Application Flask
# --------------------------------------------------------------------------
app = Flask(__name__, template_folder="templates")
app.config["TEMPLATES_AUTO_RELOAD"] = True  # sinon index.html reste en cache tant que le serveur n'est pas redémarré

# Ressources chargees une seule fois au demarrage
MODELS = {"atp": None, "wta": None}
_RESOURCES_LOADED = False

# Seuil d'inactivite : joueurs absents depuis plus de N mois sont caches
_ACTIVE_MONTHS = 18

# Un tournoi dure rarement plus de ~20 jours (Grand Chelem inclus).
_TOURNEY_RECENCY_DAYS = 20

def load_all():
    global MODELS, _RESOURCES_LOADED
    if _RESOURCES_LOADED:
        return
        
    if os.environ.get("RENDER"):
        from cloud_storage import download_data_from_github
        download_data_from_github()
        
    for circuit in ["atp", "wta"]:
        try:
            STATE, MODEL, FEATURE_COLS = pred.load_resources(circuit)
            KNOWN_PLAYERS = sorted(STATE["elo"].keys())
            last_day = int(STATE["last_day"])
            threshold = last_day - _ACTIVE_MONTHS * 30
            lpd = STATE["last_play_date"]
            ACTIVE_PLAYERS = sorted(
                p for p in KNOWN_PLAYERS
                if int(lpd.get(p, 0)) >= threshold
            )
            print(f"  Joueurs actifs ({circuit}, {_ACTIVE_MONTHS} mois) : {len(ACTIVE_PLAYERS)} / {len(KNOWN_PLAYERS)}")
            MODELS[circuit] = {
                "state": STATE,
                "model": MODEL,
                "feature_cols": FEATURE_COLS,
                "known_players": KNOWN_PLAYERS,
                "active_players": ACTIVE_PLAYERS
            }
        except Exception as e:
            print(f"Erreur chargement circuit {circuit}: {e}")
            MODELS[circuit] = None
    _RESOURCES_LOADED = True


def auto_tourney_context(player, circuit="atp"):
    if MODELS.get(circuit) is None:
        return {"mt": 0, "gw": 0, "gt": 0, "sw": 0, "st": 0, "source": "fresh_default"}
        
    STATE = MODELS[circuit]["state"]
    last_play = STATE["last_play_date"].get(player)
    current_day = STATE["last_day"]

    if last_play is None or (current_day - int(last_play)) > _TOURNEY_RECENCY_DAYS:
        return {"mt": 0, "gw": 0, "gt": 0, "sw": 0, "st": 0, "source": "fresh_default"}

    return {
        "mt": STATE["matches_this_tourney"].get(player, 0),
        "gw": STATE["tourney_games_won"].get(player, 0),
        "gt": STATE["tourney_games_total"].get(player, 0),
        "sw": STATE["tourney_sets_won"].get(player, 0),
        "st": STATE["tourney_sets_total"].get(player, 0),
        "source": "auto_detected",
    }


# --------------------------------------------------------------------------
# Routes
# --------------------------------------------------------------------------

@app.route("/")
def index():
    tourneys = []
    for circuit in ["atp", "wta"]:
        if MODELS.get(circuit):
            STATE = MODELS[circuit]["state"]
            if "tourney_champions" in STATE:
                for t in STATE["tourney_champions"].keys():
                    tourneys.append(f"{'[WTA] ' if circuit == 'wta' else ''}{t}")
    tourneys = sorted(list(set(tourneys)))
    return render_template("index.html", tourneys=tourneys)


@app.route("/api/players")
def api_players():
    """Autocomplete : renvoie les joueurs actifs correspondant a la requete q."""
    q = request.args.get("q", "").strip()
    circuit = request.args.get("circuit", "atp")
    if len(q) < 2 or MODELS.get(circuit) is None:
        return jsonify([])
        
    STATE = MODELS[circuit]["state"]
    ACTIVE_PLAYERS = MODELS[circuit]["active_players"]
    
    matches = pred.fuzzy_find(q, ACTIVE_PLAYERS, n=8, cutoff=0.4)
    return jsonify([
        {
            "name": name,
            "elo":  round(STATE["elo"].get(name, 1500)),
            "rank": STATE["last_rank"].get(name),
        }
        for name in matches
    ])


@app.route("/api/data_status")
def api_data_status():
    if MODELS.get("atp") is None:
        return jsonify({"error": "Modele non charge"}), 500
    STATE = MODELS["atp"]["state"]
    date_min = STATE["date_min"]
    last_day = int(STATE["last_day"])
    last_date = date_min + pd.Timedelta(days=last_day)
    age_days = (pd.Timestamp.today().normalize() - last_date).days
    return jsonify({
        "last_match_date": last_date.date().isoformat(),
        "age_days": age_days,
        "stale": age_days > 2,  # au-dela de 2 jours, un tournoi a pu se terminer sans etre capte
    })


@app.route("/api/predict", methods=["POST"])
def api_predict():
    """Calcule la probabilite de victoire et l'analyse value bet."""
    data = request.get_json(force=True)

    def _int(v, default=0):
        try:
            return int(float(v)) if v not in (None, "", "null", "undefined") else default
        except (ValueError, TypeError):
            return default

    def _float(v):
        try:
            return float(v) if v not in (None, "", "null", "undefined") else None
        except (ValueError, TypeError):
            return None

    try:
        circuit = data.get("circuit", "atp")
        if MODELS.get(circuit) is None:
            return jsonify({"error": f"Modèle {circuit} non disponible"}), 500
            
        STATE = MODELS[circuit]["state"]
        MODEL = MODELS[circuit]["model"]
        FEATURE_COLS = MODELS[circuit]["feature_cols"]
        
        p1 = (data.get("p1") or "").strip()
        p2 = (data.get("p2") or "").strip()
        if not p1 or not p2:
            return jsonify({"error": "Veuillez selectionner les deux joueurs."}), 400
        if p1 == p2:
            return jsonify({"error": "Les deux joueurs doivent etre differents."}), 400

        surf    = data.get("surface", "Hard")
        level   = data.get("level",   "M")
        round_  = data.get("round",   "QF")
        best_of = _int(data.get("best_of"), 3)
        indoor  = _int(data.get("indoor"),  0)
        match_date = pd.Timestamp.today()
        tourney_name = data.get("tourney_name", "Unknown").replace("[WTA] ", "")

        # Classement : automatique (compute_features retombe sur le dernier
        # classement connu en base si rank1/rank2 vaut None)
        rank1 = rank2 = None

        # Seed / statut d'entree : pas deductible du passe (info propre au
        # tirage du tournoi a venir, pas a l'historique du joueur) -> on
        # part sur l'hypothese par defaut (non tete de serie, entree directe).
        seed1 = seed2 = None
        entry1 = entry2 = None

        # Stats intra-tournoi : automatique, voir auto_tourney_context()
        ctx1 = auto_tourney_context(p1, circuit)
        ctx2 = auto_tourney_context(p2, circuit)
        mt1, gw1, gt1, sw1, st1 = ctx1["mt"], ctx1["gw"], ctx1["gt"], ctx1["sw"], ctx1["st"]
        mt2, gw2, gt2, sw2, st2 = ctx2["mt"], ctx2["gw"], ctx2["gt"], ctx2["sw"], ctx2["st"]

        feat = pred.compute_features(
            p1=p1, p2=p2, surf=surf, t_level=level,
            round_=round_, best_of=best_of, indoor=indoor,
            match_date=match_date, state=STATE,
            tourney_name=tourney_name,
            rank1=rank1, rank2=rank2,
            seed1=seed1, seed2=seed2,
            entry1=entry1, entry2=entry2,
            matches_tourney1=mt1, matches_tourney2=mt2,
            games_won1=gw1, games_total1=gt1,
            games_won2=gw2, games_total2=gt2,
            sets_won1=sw1, sets_total1=st1,
            sets_won2=sw2, sets_total2=st2,
        )

        X    = pred.build_row(feat, FEATURE_COLS)
        p_p1 = float(MODEL.predict_proba(X)[0, 1])
        p_p2 = 1.0 - p_p1

        h2h_rec = STATE["h2h"].get(p1, {}).get(p2, [0, 0])
        elo_p1  = round(STATE["elo"].get(p1, 1500))
        elo_p2  = round(STATE["elo"].get(p2, 1500))
        elo_sp1 = round(STATE["elo_surface"].get(surf, {}).get(p1, 1500))
        elo_sp2 = round(STATE["elo_surface"].get(surf, {}).get(p2, 1500))
        rk_p1   = STATE["last_rank"].get(p1)
        rk_p2   = STATE["last_rank"].get(p2)

        # Calcul de l'indice de confiance global
        conf = pred.calculate_confidence(
            p1_prob=p_p1, p1=p1, p2=p2, 
            match_date=match_date, 
            tourney_name=tourney_name, 
            t_level=level, 
            state=STATE, 
            fe=pred.fe, 
            hours1=24.0, 
            hours2=24.0
        )

        # Value bet
        odds1 = _float(data.get("odds1")) or 0.0
        odds2 = _float(data.get("odds2")) or 0.0
        value_bet = None
        if odds1 > 1 and odds2 > 1:
            pm1, pm2 = pred.remove_overround(odds1, odds2)
            edge1 = round(p_p1 - pm1, 4)
            edge2 = round(p_p2 - pm2, 4)
            rec = None
            
            if edge1 > 0.0 and edge1 > edge2:
                rec = {"player": p1, "odds": odds1, "edge": edge1, "conf_index": conf["confidence_index"], "bet_tier": conf["bet_tier"], "flags": conf["confidence_flags"]}
            elif edge2 > 0.0 and edge2 > edge1:
                rec = {"player": p2, "odds": odds2, "edge": edge2, "conf_index": conf["confidence_index"], "bet_tier": conf["bet_tier"], "flags": conf["confidence_flags"]}

            value_bet = {
                "pm1": round(pm1, 4), "pm2": round(pm2, 4),
                "edge1": edge1, "edge2": edge2,
                "recommended": rec,
            }

        # Historique des 5 derniers matchs
        rr1 = STATE["recent_results"].get(p1, [])[-5:]
        rr2 = STATE["recent_results"].get(p2, [])[-5:]
        
        def format_form(rr):
            res = []
            for r in rr:
                if len(r) >= 14:
                    res.append({"win": bool(r[1]), "surf": str(r[3]), "opp": str(r[11]), "tourney": str(r[12]), "score": str(r[13])})
                else:
                    res.append({"win": bool(r[1]), "surf": str(r[3]), "opp": "???", "tourney": "???", "score": "???"})
            return res

        form_p1 = format_form(rr1)
        form_p2 = format_form(rr2)

        # Pourcentages intra-tournoi pour affichage
        gp1 = round(gw1 / gt1, 3) if gt1 > 0 else None
        gp2 = round(gw2 / gt2, 3) if gt2 > 0 else None
        sp1 = round(sw1 / st1, 3) if st1 > 0 else None
        sp2 = round(sw2 / st2, 3) if st2 > 0 else None

        # Anciennete du dernier match connu par joueur -- explique
        # directement pourquoi un joueur peut sembler "manquer" ses matchs
        # les plus recents (trou entre l'archive annuelle et le tournoi en
        # cours, voir auto_tourney_context ci-dessus).
        current_day = STATE["last_day"]
        lpd1 = STATE["last_play_date"].get(p1)
        lpd2 = STATE["last_play_date"].get(p2)
        days_since_p1 = (current_day - int(lpd1)) if lpd1 is not None else None
        days_since_p2 = (current_day - int(lpd2)) if lpd2 is not None else None

        return jsonify({
            "p1": p1, "p2": p2,
            "p1_prob": round(p_p1, 4),
            "p2_prob": round(p_p2, 4),
            "elo_p1": elo_p1, "elo_p2": elo_p2,
            "elo_surf_p1": elo_sp1, "elo_surf_p2": elo_sp2,
            "rank_p1": rk_p1, "rank_p2": rk_p2,
            "surf": surf,
            "h2h": h2h_rec,
            "form_p1": form_p1, "form_p2": form_p2,
            "value_bet": value_bet,
            "confidence": conf,
            "tourney": {
                "mt1": mt1, "mt2": mt2,
                "gp1": gp1, "gp2": gp2,
                "sp1": sp1, "sp2": sp2,
                "source1": ctx1["source"], "source2": ctx2["source"],
            },
            "days_since_last_match": {"p1": days_since_p1, "p2": days_since_p2},
        })

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


from src.daily_matches import get_daily_matches_with_odds

@app.route("/api/daily_matches")
def api_daily_matches():
    try:
        # Prépare le dict des modèles pour get_daily_matches_with_odds
        models_dict = {}
        for c in ["atp", "wta"]:
            if MODELS.get(c):
                models_dict[c] = (MODELS[c]["state"], MODELS[c]["model"], MODELS[c]["feature_cols"])
                
        data = get_daily_matches_with_odds(models_dict, pred)
        day_filter = request.args.get("day", "all")
        
        if day_filter != "all" and "matches" in data:
            import datetime
            filtered = []
            for m in data["matches"]:
                try:
                    m_time = datetime.datetime.fromisoformat(m["time"].replace("Z", "+00:00"))
                    m_time_local = m_time.astimezone(None)
                    if m_time_local.date().isoformat() == day_filter:
                        filtered.append(m)
                except:
                    pass
            data["matches"] = filtered
            
        return jsonify(data)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/roi_simulation", methods=["GET"])
def api_roi_simulation():
    import os, json
    BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    db_path = os.path.join(BASE_DIR, "data", "processed", "predictions_db.json")
    if not os.path.exists(db_path):
        return jsonify({"history": []})
        
    try:
        with open(db_path, "r", encoding="utf-8") as f:
            predictions_db = json.load(f)
            
        # Return only matches that have a winner
        history = [m for m in predictions_db.values() if "winner" in m]
        # Sort chronologically
        history.sort(key=lambda x: x.get("time", ""))
        return jsonify({"history": history})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/update", methods=["POST", "GET"])
def api_update():
    # Protection : cette route relance tout le pipeline (téléchargement +
    # réentraînement XGBoost), donc coûteuse en CPU/temps. On exige un
    # token secret pour éviter que n'importe quel visiteur puisse la
    # déclencher. Définis UPDATE_TOKEN dans les variables d'environnement
    # (Render : Settings > Environment) puis appelle avec
    # /api/update?token=... ou header "X-Update-Token: ...".
    expected_token = os.environ.get("UPDATE_TOKEN")
    if not expected_token:
        return jsonify({"error": "UPDATE_TOKEN non configuré côté serveur — route désactivée."}), 503
    provided_token = request.headers.get("X-Update-Token") or request.args.get("token")
    if provided_token != expected_token:
        return jsonify({"error": "Non autorisé."}), 401

    def generate():
        scripts = [
            "src/00_download_data.py",
            "src/01_build_dataset.py",
            "src/02_feature_engineering.py",
            "src/03_train_model.py",
            "src/short_term_rest_features.py"
        ]
        python_exe = sys.executable
        cwd_path = str(Path(__file__).resolve().parent.parent)
        
        try:
            for script in scripts:
                yield f"data: === Lancement de {script} ===\n\n"
                process = subprocess.Popen(
                    [python_exe, "-u", script],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                    cwd=cwd_path
                )
                
                for line in process.stdout:
                    yield f"data: {line.rstrip()}\n\n"
                
                process.wait()
                if process.returncode != 0:
                    yield f"data: ERREUR: {script} a renvoye le code {process.returncode}\n\n"
                    yield "data: [UPDATE_FAILED]\n\n"
                    return
            
            yield "data: === Scripts termines avec succes ===\n\n"
            yield "data: Recharge des donnees en memoire...\n\n"
            
            try:
                load_all()
                yield "data: Donnees rechargees.\n\n"
                yield "data: [UPDATE_SUCCESS]\n\n"
            except Exception as e:
                yield f"data: Erreur de rechargement: {e}\n\n"
                yield "data: [UPDATE_FAILED]\n\n"

        except Exception as e:
            yield f"data: ERREUR GLOBALE: {e}\n\n"
            yield "data: [UPDATE_FAILED]\n\n"

    response = Response(generate(), mimetype="text/event-stream")
    response.headers["Cache-Control"] = "no-cache"
    response.headers["X-Accel-Buffering"] = "no"
    return response


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------
# Gunicorn importe ce fichier, donc on appelle load_all() directement si c'est pas le main
if __name__ != "__main__":
    load_all()

if __name__ == "__main__":
    load_all()
    print("\n  Tennis Predictor Web — http://localhost:5000\n")
    app.run(debug=False, port=5000, use_reloader=False)
