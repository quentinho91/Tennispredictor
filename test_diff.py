import sys
from pathlib import Path
import importlib.util
import pandas as pd
import datetime

def main():
    p = Path('src/05_predict_match.py')
    spec = importlib.util.spec_from_file_location('pred', p)
    pred = importlib.util.module_from_spec(spec)
    sys.modules['pred'] = pred
    spec.loader.exec_module(pred)
    
    state, model, fc = pred.load_resources()
    
    p1 = 'Grigor Dimitrov'
    p2 = 'Sebastian Baez'
    surf = 'Hard'
    level = 'M'
    indoor = 0
    t_name = 'Cincinnati Masters'
    
    # 1. Manual prediction context (from app.py)
    # app.py auto_tourney_context
    def auto_tourney_context(player):
        _TOURNEY_RECENCY_DAYS = 20
        last_play = state["last_play_date"].get(player)
        current_day = state["last_day"]
        if last_play is None or (current_day - int(last_play)) > _TOURNEY_RECENCY_DAYS:
            return {"mt": 0, "gw": 0, "gt": 0, "sw": 0, "st": 0}
        return {
            "mt": state["matches_this_tourney"].get(player, 0),
            "gw": state["tourney_games_won"].get(player, 0),
            "gt": state["tourney_games_total"].get(player, 0),
            "sw": state["tourney_sets_won"].get(player, 0),
            "st": state["tourney_sets_total"].get(player, 0),
        }
    
    ctx1 = auto_tourney_context(p1)
    ctx2 = auto_tourney_context(p2)
    
    match_date1 = pd.Timestamp.today()
    feat1 = pred.compute_features(
        p1=p1, p2=p2, surf=surf, t_level=level,
        round_="R128", best_of=3, indoor=indoor,
        match_date=match_date1, state=state,
        tourney_name=t_name,
        matches_tourney1=ctx1["mt"], matches_tourney2=ctx2["mt"],
        games_won1=ctx1["gw"], games_total1=ctx1["gt"],
        games_won2=ctx2["gw"], games_total2=ctx2["gt"],
        sets_won1=ctx1["sw"], sets_total1=ctx1["st"],
        sets_won2=ctx2["sw"], sets_total2=ctx2["st"]
    )
    
    # 2. Daily matches context (from daily_matches.py)
    last_surf1 = state.get("last_surface", {}).get(p1)
    last_surf2 = state.get("last_surface", {}).get(p2)
    if last_surf1 and last_surf1 == last_surf2:
        surf2 = last_surf1
    elif last_surf1:
        surf2 = last_surf1
    else:
        surf2 = "Hard"
    
    match_date2 = pd.Timestamp.now() if hasattr(pd, 'Timestamp') else datetime.datetime.now()
    feat2 = pred.compute_features(
        p1=p1, p2=p2, surf=surf2, t_level=level,
        round_="Q", best_of=3, indoor=indoor,
        match_date=match_date2, state=state,
        tourney_name=t_name,
        matches_tourney1=ctx1["mt"], matches_tourney2=ctx2["mt"],
        games_won1=ctx1["gw"], games_total1=ctx1["gt"],
        games_won2=ctx2["gw"], games_total2=ctx2["gt"],
        sets_won1=ctx1["sw"], sets_total1=ctx1["st"],
        sets_won2=ctx2["sw"], sets_total2=ctx2["st"]
    )
    
    print(f"Surface manual: {surf}, Surface daily: {surf2}")
    print("Differences in features:")
    for k in feat1.keys():
        v1 = feat1[k]
        v2 = feat2[k]
        if v1 != v2:
            print(f"{k}: manual={v1}, daily={v2}")
            
    # Probas
    row1 = pred.build_row(feat1, fc)
    row2 = pred.build_row(feat2, fc)
    p1_manual = float(model.predict_proba(row1)[0, 1])
    p1_daily = float(model.predict_proba(row2)[0, 1])
    print(f"\nProba Dimitrov manual: {p1_manual:.4f}")
    print(f"Proba Dimitrov daily:  {p1_daily:.4f}")

if __name__ == "__main__":
    main()
