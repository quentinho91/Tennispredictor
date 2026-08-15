import pickle
from pathlib import Path
import os
import argparse

BASE_DIR = Path(__file__).resolve().parent.parent
PROCESSED_DIR = BASE_DIR / "data" / "processed"
ACTIVE_MONTHS = 18

def lighten_state(circuit):
    STATE_PATH = PROCESSED_DIR / f"player_state_{circuit}.pkl"
    if not STATE_PATH.exists():
        print(f"{STATE_PATH} introuvable.")
        return

    print(f"Chargement de l'état complet ({circuit})...")
    with open(STATE_PATH, "rb") as f:
        state = pickle.load(f)

    last_day = int(state["last_day"])
    threshold = last_day - ACTIVE_MONTHS * 30
    lpd = state.get("last_play_date", {})
    
    active_players = {p for p, d in lpd.items() if d >= threshold}
    print(f"Joueurs actifs ({ACTIVE_MONTHS} mois) : {len(active_players)} sur {len(lpd)}")

    light_state = {}
    
    # Clés simples à conserver telles quelles
    meta_keys = ["date_min", "last_day", "tourney_champions", "tourney_cpi_yearly", "tourney_countries"]
    for k in meta_keys:
        if k in state:
            light_state[k] = state[k]

    # Dictionnaires simples {joueur: valeur}
    dict_1d = [
        "elo", "elo_history", "rank_history", "peak_rank", "career_matches",
        "career_retirements", "first_match_day", "last_surface", "last_tourney_id",
        "matches_this_tourney", "tourney_games_won", "tourney_games_total",
        "tourney_sets_won", "tourney_sets_total", "recent_results", "streak",
        "last_play_date", "last_retirement", "serve_return_hist",
        "vs_lefty_results", "high_altitude_results", "player_ioc_dict",
        "fast_results", "medium_results", "slow_results",
        "last_rank", "last_points", "last_ht", "last_hand", "last_age", "last_age_day"
    ]
    for k in dict_1d:
        if k in state:
            light_state[k] = {p: v for p, v in state[k].items() if p in active_players}

    # Dictionnaires {surface: {joueur: valeur}}
    dict_surf_1d = ["elo_surface", "elo_surface_w"]
    for k in dict_surf_1d:
        if k in state:
            light_state[k] = {}
            for surf, d in state[k].items():
                light_state[k][surf] = {p: v for p, v in d.items() if p in active_players}
                
    # Dictionnaires {joueur: {surface: valeur}}
    dict_1d_surf = ["surface_career_count", "surface_career_wins"]
    for k in dict_1d_surf:
        if k in state:
            light_state[k] = {p: v for p, v in state[k].items() if p in active_players}

    # Dictionnaires H2H {joueur1: {joueur2: valeur}}
    dict_2d = ["h2h", "h2h_history", "last_h2h_day", "last_h2h_result"]
    for k in dict_2d:
        if k in state:
            light_state[k] = {}
            for p1, d in state[k].items():
                if p1 in active_players:
                    filtered_d = {p2: v for p2, v in d.items() if p2 in active_players}
                    if filtered_d:
                        light_state[k][p1] = filtered_d

    # Dictionnaire H2H Surface {joueur1: {joueur2: {surface: valeur}}}
    if "h2h_surface" in state:
        light_state["h2h_surface"] = {}
        for p1, d in state["h2h_surface"].items():
            if p1 in active_players:
                filtered_d = {}
                for p2, surf_dict in d.items():
                    if p2 in active_players:
                        filtered_d[p2] = surf_dict
                if filtered_d:
                    light_state["h2h_surface"][p1] = filtered_d

    # Sauvegarde
    print(f"Sauvegarde de l'état allégé ({circuit})...")
    with open(STATE_PATH, "wb") as f:
        pickle.dump(light_state, f)
        
    old_size = os.path.getsize(STATE_PATH) / (1024 * 1024)
    print(f"Opération terminée. Taille du fichier : {old_size:.1f} Mo")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--circuit", default="atp", choices=["atp", "wta"], help="Circuit to process (atp or wta)")
    args = parser.parse_args()
    lighten_state(args.circuit)
