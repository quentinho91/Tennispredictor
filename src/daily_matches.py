import requests
import json
import datetime
import pandas as pd
import os
from typing import List, Dict

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
db_path = os.path.join(BASE_DIR, "data", "processed", "predictions_db.json")

# Clé de l'API the-odds-api.com : ne JAMAIS committer de clé en dur ici.
# En local : export ODDS_API_KEY=xxxx (ou fichier .env)
# En CI/Render : secret d'environnement ODDS_API_KEY
try:
    from dotenv import load_dotenv
    load_dotenv(override=True)
except ImportError:
    pass

API_KEY = os.environ.get("ODDS_API_KEY", "")
if not API_KEY:
    print("[AVERTISSEMENT] ODDS_API_KEY n'est pas définie — les cotes ne seront pas récupérées.")

def get_daily_matches_with_odds(models, pred):

    predictions_db = {}
    if os.path.exists(db_path):
        try:
            with open(db_path, "r", encoding="utf-8") as f:
                predictions_db = json.load(f)
        except Exception as e:
            print(f"Erreur de chargement DB: {e}")

    if not API_KEY:
        all_matches = list(predictions_db.values())
        all_matches.sort(key=lambda x: x['time'])
        return {"matches": all_matches, "warning": "ODDS_API_KEY non configurée côté serveur. Affichage des données en cache."}

    # 1. Fetch all tennis sports with caching
    cache_path = os.path.join(os.path.dirname(db_path), "odds_cache.json")
    cached_data = None
    if os.path.exists(cache_path):
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                c = json.load(f)
                if (datetime.datetime.now().timestamp() - c['timestamp']) < 21600: # 6 hours cache
                    cached_data = c['events']
        except:
            pass

    events = []
    if cached_data is not None:
        events = cached_data
    else:
        sports_url = f"https://api.the-odds-api.com/v4/sports/?apiKey={API_KEY}"
        r_sports = requests.get(sports_url)
        if not r_sports.ok:
            # Fallback to DB if API fails
            all_matches = list(predictions_db.values())
            all_matches.sort(key=lambda x: x['time'])
            return {"matches": all_matches, "warning": "Impossible de contacter The Odds API (sports), affichage des données en cache."}
            
        # Only keep main ATP and WTA tours (exclude challenger, ITF, Doubles)
        sports = [s['key'] for s in r_sports.json() if ('tennis_atp' in s['key'].lower() or 'tennis_wta' in s['key'].lower())
                  and 'challenger' not in s['key'].lower() 
                  and 'itf' not in s['key'].lower()
                  and 'doubles' not in s['key'].lower()]
        
        for sport in sports:
            odds_url = f"https://api.the-odds-api.com/v4/sports/{sport}/odds/?apiKey={API_KEY}&regions=eu,uk&markets=h2h"
            r_odds = requests.get(odds_url)
            if r_odds.ok:
                sport_events = r_odds.json()
                for e in sport_events:
                    e['circuit'] = 'wta' if 'wta' in sport.lower() else 'atp'
                events.extend(sport_events)
                
        # Save to cache
        try:
            with open(cache_path, "w", encoding="utf-8") as f:
                json.dump({"timestamp": datetime.datetime.now().timestamp(), "events": events}, f)
        except:
            pass

    all_matches = []
    
    # Track processed IDs so we can append the rest from DB later
    processed_ids = set()
    
    for event in events:
        processed_ids.add(event['id'])
        # Check date: must be upcoming or recent (today)
        is_live = False
        try:
            start_time = datetime.datetime.fromisoformat(event['commence_time'].replace("Z", "+00:00"))
            now = datetime.datetime.now(datetime.timezone.utc)
            if start_time < now - datetime.timedelta(hours=6):
                continue # Match too old
            if start_time > now + datetime.timedelta(days=3):
                continue # Match too far in future
            if start_time <= now:
                is_live = True
        except:
            pass
            
        # If match is in DB and has started, use stored pre-match odds
        if event['id'] in predictions_db:
            stored = predictions_db[event['id']]
            if 'time' in stored:
                try:
                    stored_time = datetime.datetime.fromisoformat(stored['time'].replace("Z", "+00:00"))
                    if stored_time <= now:
                        all_matches.append(stored)
                        continue
                except:
                    pass
            
        bookmakers = event.get('bookmakers', [])
        if not bookmakers:
            continue
            
        bet365 = next((b for b in bookmakers if b['key'] == 'bet365'), None)
        if not bet365:
            bet365 = bookmakers[0]
            
        markets = bet365.get('markets', [])
        if not markets:
            continue
            
        h2h = next((m for m in markets if m['key'] == 'h2h'), None)
        if not h2h:
            continue
            
        outcomes = h2h.get('outcomes', [])
        if len(outcomes) != 2:
            continue
            
        p1_name = outcomes[0]['name']
        p2_name = outcomes[1]['name']
        odds1 = outcomes[0]['price']
        odds2 = outcomes[1]['price']
        
        # Ignorer les cotes si le match a commencé (Live) pour ne pas fausser les value bets
        if is_live:
            odds1 = None
            odds2 = None
        
        circuit = event.get('circuit', 'atp')
        if circuit not in models or models[circuit] is None:
            continue
            
        STATE, MODEL, FEATURE_COLS = models[circuit]
        
        # Fuzzy find player names in our database
        p1_matches = pred.fuzzy_find(p1_name, STATE["elo"].keys(), n=1, cutoff=0.7)
        p2_matches = pred.fuzzy_find(p2_name, STATE["elo"].keys(), n=1, cutoff=0.7)
        
        if not p1_matches or not p2_matches:
            continue
            
        p1_real = p1_matches[0]
        p2_real = p2_matches[0]
        
        if p1_real == p2_real:
            continue
            
        # Deduce tournament name and level
        raw_t_name = event['sport_title'].replace('ATP ', '').replace('WTA ', '').replace(' Open', ' Masters')
        t_matches = pred.fuzzy_find(raw_t_name, list(STATE.get("tourney_champions", {}).keys()), n=1, cutoff=0.5)
        t_name = t_matches[0] if t_matches else raw_t_name
        
        # Deduce level
        t_level = "A"
        if "Grand Slam" in event['sport_title'] or "Wimbledon" in t_name or "Roland Garros" in t_name: 
            t_level = "G"
        elif "Masters" in t_name or "1000" in t_name: 
            t_level = "M"
            
        # Deduce surface from tournament name
        t_lower = t_name.toLowerCase() if hasattr(t_name, 'toLowerCase') else t_name.lower()
        clay_tourneys = ["roland garros", "monte carlo", "madrid", "rome", "barcelona", "estoril", "munich", "geneva", "lyon", "bastad", "gstaad", "umag", "kitzbuhel", "hamburg", "buenos aires", "cordoba", "rio", "santiago", "houston", "marrakech"]
        grass_tourneys = ["wimbledon", "halle", "queen", "stuttgart", "hertogenbosch", "mallorca", "eastbourne", "newport"]
        
        surf = "Hard"
        if any(c in t_lower for c in clay_tourneys):
            surf = "Clay"
        elif any(g in t_lower for g in grass_tourneys):
            surf = "Grass"
        
        try:
            now_dt = pd.Timestamp.now() if hasattr(pd, 'Timestamp') else datetime.datetime.now()
            
            # Auto tourney context (like in app.py)
            def auto_tourney_context(player):
                _TOURNEY_RECENCY_DAYS = 20
                last_play = STATE.get("last_play_date", {}).get(player)
                current_day = STATE.get("last_day", 0)
                if last_play is None or (current_day - int(last_play)) > _TOURNEY_RECENCY_DAYS:
                    return {"mt": 0, "gw": 0, "gt": 0, "sw": 0, "st": 0}
                return {
                    "mt": STATE.get("matches_this_tourney", {}).get(player, 0),
                    "gw": STATE.get("tourney_games_won", {}).get(player, 0),
                    "gt": STATE.get("tourney_games_total", {}).get(player, 0),
                    "sw": STATE.get("tourney_sets_won", {}).get(player, 0),
                    "st": STATE.get("tourney_sets_total", {}).get(player, 0),
                }
            
            ctx1 = auto_tourney_context(p1_real)
            ctx2 = auto_tourney_context(p2_real)
            
            # Deduce round dynamically based on matches played in the tournament
            mt_max = max(ctx1["mt"], ctx2["mt"])
            if t_level == "G":
                rounds_progression = ["R128", "R64", "R32", "R16", "QF", "SF", "F"]
            elif t_level == "M":
                rounds_progression = ["R64", "R32", "R16", "QF", "SF", "F"]
            else:
                rounds_progression = ["R32", "R16", "QF", "SF", "F"]
                
            round_idx = min(mt_max, len(rounds_progression) - 1)
            round_ = rounds_progression[round_idx]
            
            feat = pred.compute_features(
                p1=p1_real, p2=p2_real, 
                surf=surf, 
                t_level=t_level, 
                round_=round_, 
                best_of=5 if t_level == "G" else 3, 
                indoor=0,
                match_date=now_dt, 
                state=STATE, 
                tourney_name=t_name,
                matches_tourney1=ctx1["mt"], matches_tourney2=ctx2["mt"],
                games_won1=ctx1["gw"], games_total1=ctx1["gt"],
                games_won2=ctx2["gw"], games_total2=ctx2["gt"],
                sets_won1=ctx1["sw"], sets_total1=ctx1["st"],
                sets_won2=ctx2["sw"], sets_total2=ctx2["st"]
            )
            
            row = pred.build_row(feat, FEATURE_COLS)
            proba1 = float(MODEL.predict_proba(row)[0, 1])
            proba2 = 1.0 - proba1
            
            if odds1 and odds2:
                implied1 = 1.0 / odds1
                implied2 = 1.0 / odds2
                
                # Overround correction
                overround = implied1 + implied2
                if overround > 1.0:
                    implied1 /= overround
                    implied2 /= overround
                    
                edge1 = proba1 - implied1
                edge2 = proba2 - implied2
            else:
                implied1 = implied2 = edge1 = edge2 = 0.0
            
            conf = pred.calculate_confidence(
                p1_prob=proba1, p1=p1_real, p2=p2_real, 
                match_date=now_dt, 
                tourney_name=t_name, 
                t_level=t_level, 
                state=STATE, 
                fe=pred.fe, 
                hours1=24.0, 
                hours2=24.0
            )
            def _get_recent_form(state, player):
                rr = state.get("recent_results", {}).get(player, [])
                return [1 if r[1] else 0 for r in rr[-5:]]
                
            h2h_rec = STATE.get("h2h", {}).get(p1_real, {}).get(p2_real, [0, 0])
                
            match_data = {
                "id": event['id'],
                "time": event['commence_time'],
                "tourney": f"{'[WTA] ' if circuit == 'wta' else ''}{t_name}",
                "p1": p1_real,
                "p2": p2_real,
                "odds1": odds1,
                "odds2": odds2,
                "proba1": float(proba1),
                "proba2": float(proba2),
                "implied1": float(implied1),
                "implied2": float(implied2),
                "edge1": float(edge1),
                "edge2": float(edge2),
                "bookie": bet365['title'],
                "confidence": conf,
                "circuit": circuit,
                "p1_archetype": feat.get("_p1_archetype", "Polyvalent"),
                "p2_archetype": feat.get("_p2_archetype", "Polyvalent"),
                "p1_form": _get_recent_form(STATE, p1_real),
                "p2_form": _get_recent_form(STATE, p2_real),
                "serve_return_edge1": feat.get("serve_return_edge1", 0),
                "serve_return_edge2": feat.get("serve_return_edge2", 0),
                
                # Nouveaux champs pour UI détaillée
                "p1_elo_surf": feat.get("_p1_elo_surf", 1500),
                "p2_elo_surf": feat.get("_p2_elo_surf", 1500),
                "p1_hand": "Droitier" if feat.get("_p1_hand", "R") == "R" else "Gaucher",
                "p2_hand": "Droitier" if feat.get("_p2_hand", "R") == "R" else "Gaucher",
                "p1_wr_vs_L": float(feat.get("_p1_wr_vs_L", 0.5)),
                "p2_wr_vs_L": float(feat.get("_p2_wr_vs_L", 0.5)),
                "p1_wr_vs_R": float(feat.get("_p1_wr_vs_R", 0.5)),
                "p2_wr_vs_R": float(feat.get("_p2_wr_vs_R", 0.5)),
                
                "p1_service_idx": feat.get("_p1_service_idx", 50),
                "p2_service_idx": feat.get("_p2_service_idx", 50),
                "p1_return_idx": feat.get("_p1_return_idx", 50),
                "p2_return_idx": feat.get("_p2_return_idx", 50),
                "p1_clutch_idx": feat.get("_p1_clutch_idx", 50),
                "p2_clutch_idx": feat.get("_p2_clutch_idx", 50),
                "p1_global_idx": feat.get("_p1_global_idx", 50),
                "p2_global_idx": feat.get("_p2_global_idx", 50),
                
                "p1_fatigue_idx": feat.get("_p1_fatigue_idx", 0),
                "p2_fatigue_idx": feat.get("_p2_fatigue_idx", 0),
                "p1_rest_days": feat.get("_p1_rest_days", 3),
                "p2_rest_days": feat.get("_p2_rest_days", 3),
                
                "p1_h2h": h2h_rec[0],
                "p2_h2h": h2h_rec[1],
                
                "p1_wr_fav": float(feat.get("_p1_wr_fav", 50)),
                "p2_wr_fav": float(feat.get("_p2_wr_fav", 50)),
                "p1_wr_out": float(feat.get("_p1_wr_out", 30)),
                "p2_wr_out": float(feat.get("_p2_wr_out", 30)),
            }
            
            # Copy previous winner/result info if it exists
            if event['id'] in predictions_db:
                prev = predictions_db[event['id']]
                if 'winner' in prev:
                    match_data['winner'] = prev['winner']
            
            predictions_db[event['id']] = match_data
            all_matches.append(match_data)
            
        except Exception as e:
            print(f"Error predicting {p1_real} vs {p2_real}: {e}")
            
    # Add all historical matches from DB that were not in the API response
    for ev_id, match in predictions_db.items():
        if ev_id not in processed_ids:
            all_matches.append(match)
            
    # Sort by commence time
    all_matches.sort(key=lambda x: x['time'])
    
    # Save to DB
    try:
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        with open(db_path, "w", encoding="utf-8") as f:
            json.dump(predictions_db, f, indent=4)
    except Exception as e:
        print(f"Erreur de sauvegarde DB: {e}")
        
    return {"matches": all_matches}
