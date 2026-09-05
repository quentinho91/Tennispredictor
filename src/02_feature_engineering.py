"""
Feature engineering "au fil de l'eau" (walk-forward) — V2, liste étendue.

REGLE D'OR ANTI-LEAKAGE : pour le match n, toute feature ne doit utiliser
que des informations disponibles STRICTEMENT AVANT ce match. On parcourt
les matchs triés chronologiquement et on met à jour des états par joueur
au fur et à mesure, en calculant la feature AVANT de mettre à jour l'état
avec le résultat du match courant.

ORGANISATION DE L'ETAT (pour comprendre le code) :
- Scalaires non bornés par joueur (coût O(1), peu importe la longueur de
  carrière) : Elo, meilleur classement jamais atteint, nb de matchs/
  abandons en carrière, date du 1er match, compteurs par surface, série
  victoires/défaites en cours, dernier tournoi joué...
- Listes bornées (MAX_HISTORY entrées) par joueur pour les stats
  glissantes (forme, retour, mental/clutch...) : au-delà de ~150 matchs
  récents, aucune fenêtre de ce script (max 365 jours) n'en a besoin, donc
  scanner plus loin ne ferait que ralentir sans rien changer au résultat.

FAMILLES DE FEATURES (V1 + ajouts V2 listés à la fin de chaque bloc) :

1. Classement : diff rang, diff points (log)
   V2 : meilleur classement jamais atteint (peak rank), momentum du
   classement (évolution sur ~6 mois)
2. Elo général + par surface
   V2 : tendance Elo (pente sur les ~10 derniers matchs), proba Elo brute
   injectée en feature absolue (utile pour du stacking)
3. Forme récente (10 derniers matchs, 90 jours)
   V2 : fenêtres 5/20 matchs, 180/365 jours, série en cours, consistance
   (variance des résultats récents)
4. H2H global
   V2 : H2H par surface, H2H sur les 2 dernières années, résultat de la
   dernière confrontation + ancienneté
5. Repos / fatigue
   V2 : matchs déjà joués dans le tournoi en cours, durée moyenne des
   matchs récents, sortie d'abandon récent, taux d'abandon en carrière
6. Stats de service glissantes (5/20 derniers matchs)
   V2 : stats de RETOUR symétriques (% points gagnés au retour, % BP
   convertis), dérivées des stats de service adverses du même match
7. Statique : âge, taille, main
   V2 : années de carrière pro
8. Contexte : surface, niveau tournoi, best of, tour
   V2 : indoor/outdoor, tête de série + numéro de seed, wildcard/qualifié,
   expérience + winrate sur la surface, transition de surface
9. NOUVEAU — Mental / clutch (nécessite de parser le score) : taux de
   victoire en set décisif, taux de comeback (perdu le 1er set puis
   gagné), taux de victoire en tie-break, "giant-killer factor" (bat des
   joueurs mieux classés que lui)
10. NOUVEAU — Saisonnalité : position dans l'année (encodée en sin/cos)
"""

import pandas as pd
import numpy as np
import re
import time
from collections import defaultdict
import sys
from pathlib import Path

# Ajouter le répertoire 'src' au sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent))

from markov_tennis import (
    p_game,
    p_set,
    p_match,
    estimate_point_probabilities
)

BASE_DIR = Path(__file__).resolve().parent.parent
PROCESSED_DIR = BASE_DIR / "data" / "processed"
PROCESSED_DIR.mkdir(parents=True, exist_ok=True)

K_ELO = 32
K_SERVE_ELO = 24.0
K_RETURN_ELO = 24.0
ELO_INIT = 1500
MAX_HISTORY = 150       # borne des listes glissantes par joueur (forme, retour, clutch...)
ELO_TREND_LAG = 10      # "pente" Elo = elo actuel - elo il y a N matchs
RANK_MOMENTUM_DAYS = 180

# K adaptatif pour l'Elo surface pondéré : plus fort en GC/Masters (signal plus fiable)
K_ELO_BY_LEVEL = {"G": 48, "M": 40, "A": 36, "F": 44}
# Tours considérés comme "sous pression" (QF / SF / F / match pour la 3e place)
LATE_ROUNDS = {"QF", "SF", "F", "BR"}

SERVE_RETURN_KEYS = ("ace_rate", "df_rate", "first_in_pct", "first_won_pct",
                      "second_won_pct", "bp_saved_pct",
                      "return_pts_won_pct", "bp_converted_pct", "grind_index")  # 6 service + 2 retour + 1 rythme

# ---------------------------------------------------------------------------
# Fonctions partagées entre 02_feature_engineering.py et 05_predict_match.py
# (extraites au niveau module pour éviter les duplications et les divergences)
# ---------------------------------------------------------------------------

ALTITUDES = {
    "Bogota": 2640, "Quito": 2850, "Johannesburg": 1750, "Gstaad": 1050,
    "Kitzbuhel": 762, "Sao Paulo": 760, "Madrid": 667, "Sofia": 590,
    "Santiago": 570, "Munich": 520, "Denver": 1609
}


def get_altitude(t_name):
    """Return altitude in meters for known high-altitude tournament cities."""
    if not isinstance(t_name, str):
        return 0
    for city, alt in ALTITUDES.items():
        if city.lower() in t_name.lower():
            return alt
    return 0


def get_tourney_country(t_name):
    """Map tournament name to country code."""
    if not isinstance(t_name, str): return "UNKNOWN"
    t = t_name.lower()
    if any(x in t for x in ["us open", "miami", "cincinnati", "indian wells", "washington", "dallas", "delray", "houston", "atlanta", "winston-salem", "newport"]): return "USA"
    if any(x in t for x in ["roland garros", "paris", "marseille", "montpellier", "lyon", "metz"]): return "FRA"
    if any(x in t for x in ["madrid", "barcelona", "mallorca"]): return "ESP"
    if any(x in t for x in ["australian open", "brisbane", "sydney", "adelaide", "perth"]): return "AUS"
    if any(x in t for x in ["wimbledon", "queen", "eastbourne", "london"]): return "GBR"
    if any(x in t for x in ["rome", "turin", "milan", "naples"]): return "ITA"
    if any(x in t for x in ["munich", "halle", "hamburg", "stuttgart"]): return "GER"
    if any(x in t for x in ["geneva", "basel", "gstaad"]): return "SUI"
    if any(x in t for x in ["buenos aires", "cordoba"]): return "ARG"
    if any(x in t for x in ["shanghai", "beijing", "chengdu", "zhuhai", "hangzhou"]): return "CHN"
    if "tokyo" in t: return "JPN"
    if any(x in t for x in ["toronto", "montreal"]): return "CAN"
    if "vienna" in t: return "AUT"
    if "dubai" in t: return "UAE"
    if "doha" in t: return "QAT"
    if "stockholm" in t or "bastad" in t: return "SWE"
    if "s-hertogenbosch" in t or "rotterdam" in t: return "NED"
    return "UNKNOWN"


def get_cpi(t_name, t_year, t_surf, tourney_cpi_yearly):
    """Court Pace Index from historical ace-rate data (uses only past years)."""
    if t_name in tourney_cpi_yearly:
        hist = [tourney_cpi_yearly[t_name][y] for y in range(t_year-3, t_year) if y in tourney_cpi_yearly[t_name]]
        if hist:
            return sum(hist) / len(hist)
    if t_surf == 'Hard': return 8.5
    elif t_surf == 'Clay': return 5.5
    elif t_surf == 'Grass': return 10.5
    return 8.0


def speed_wr(results_dict, p):
    """Win-rate on fast/medium/slow/altitude courts (last 30 results)."""
    lst = results_dict.get(p, [])[-30:]
    if not lst: return 0.5
    return sum(w for d, w in lst) / len(lst)


COUNTRY_CONTINENT = {
    "USA": "NA", "CAN": "NA", "MEX": "NA",
    "ARG": "SA", "BRA": "SA", "CHI": "SA", "COL": "SA", "ECU": "SA",
    "FRA": "EU", "ESP": "EU", "GBR": "EU", "ITA": "EU", "GER": "EU", "SUI": "EU",
    "AUT": "EU", "SWE": "EU", "NED": "EU", "BEL": "EU", "POR": "EU", "CRO": "EU",
    "SRB": "EU", "GRE": "EU", "CZE": "EU", "POL": "EU", "ROU": "EU", "BUL": "EU",
    "MON": "EU", "RUS": "EU",
    "CHN": "AS", "JPN": "AS", "UAE": "AS", "QAT": "AS", "KOR": "AS", "IND": "AS", "KAZ": "AS", "SAU": "AS", "SGP": "AS",
    "AUS": "OC", "NZL": "OC",
    "RSA": "AF", "MAR": "AF", "TUN": "AF", "EGY": "AF"
}


def compute_travel_strain(player, day, t1_id, t_country, surf, last_tourney_id_dict, last_play_date_dict, last_tourney_country_dict, last_surface_dict):
    """Calcule la fatigue de déplacement et le changement de surface rapide."""
    prev_t_id = last_tourney_id_dict.get(player)
    if prev_t_id == t1_id or player not in last_play_date_dict:
        return 0.0, 0.0

    days_rest = day - last_play_date_dict[player]
    if days_rest > 7:
        return 0.0, 0.0

    prev_country = last_tourney_country_dict.get(player, "UNKNOWN")
    prev_cont = COUNTRY_CONTINENT.get(prev_country)
    curr_cont = COUNTRY_CONTINENT.get(t_country)

    diff_continent = int(prev_cont is not None and curr_cont is not None and prev_cont != curr_cont)
    diff_country = int(prev_country not in ("UNKNOWN", t_country) and t_country != "UNKNOWN")
    diff_surface = int(last_surface_dict.get(player) is not None and last_surface_dict[player] != surf)

    strain = 0.0
    if days_rest <= 4:
        strain += 1.5
    elif days_rest <= 7:
        strain += 0.5

    if diff_continent:
        strain += 2.0
    elif diff_country:
        strain += 0.75

    if diff_surface:
        strain += 1.25

    short_sc = 1.0 if (days_rest <= 4 and diff_surface) else 0.0
    return strain, short_sc


def elo_expected(ra, rb):
    return 1.0 / (1.0 + 10 ** ((rb - ra) / 400.0))


def get_decayed_elo(base_elo, day, last_day, grace_days=60, half_life_days=365.0, init_elo=ELO_INIT):
    """
    Applique une dépréciation d'inactivité (Inactivity Decay) sur l'Elo :
    - Si inactif <= grace_days (ex: 60 jours) : aucun impact.
    - Au-delà, l'Elo régresse exponentiellement vers la moyenne (init_elo = 1500).
    - Rétention bornée à min 50% pour préserver le socle historique du joueur.
    """
    if last_day is None or day is None:
        return base_elo
    days_inactive = day - last_day
    if days_inactive <= grace_days:
        return base_elo
    excess_days = days_inactive - grace_days
    retention = np.exp(-np.log(2) * (excess_days / half_life_days))
    retention = max(0.50, retention)
    return init_elo + (base_elo - init_elo) * retention


def get_dynamic_k(base_k, matches_count, k_boost=32.0, halflife_matches=25.0):
    """
    Facteur K adaptatif à l'incertitude (Dynamic Uncertainty K-Factor) :
    - Débutant / espoir montant (0-5 matchs) : K = base_k + 32 (ex: 32+32=64) -> convergence ultra-rapide.
    - 25 matchs : K = base_k + 16 (ex: 48).
    - Vétéran / joueur établi (50+ matchs) : K converge vers base_k (ex: 32) -> grande stabilité.
    """
    unc_factor = np.exp(-matches_count / halflife_matches)
    return base_k + k_boost * unc_factor


def compute_rust_factor(day, last_play_day, matches_since_break, break_threshold_days=75, max_rusty_matches=3):
    """
    Détecte si un joueur reprend la compétition après une longue absence (> 75j)
    avec moins de 3 matchs dans les jambes.
    Retourne (is_rusty, matches_since_break).
    """
    if last_play_day is None or day is None:
        return 0.0, 999

    days_inactive = day - last_play_day
    # Si le match actuel survient après une pause de plus de 75 jours
    if days_inactive > break_threshold_days:
        return 1.0, 0

    # Si le joueur a connu une longue pause récemment et a joué moins de 3 matchs
    if matches_since_break is not None and matches_since_break < max_rusty_matches:
        return 1.0, matches_since_break

    return 0.0, matches_since_break if matches_since_break is not None else 999


def compute_slump_indicator(recent_results_list, current_streak):
    """
    Détecte si un joueur est dans une spirale négative ("slump") :
    - 3+ défaites consécutives (current_streak <= -3) OU 4+ défaites sur les 5 derniers matchs
    - ET au moins 1 défaite face à un adversaire moins bien classé (contre-performance).
    Retourne (is_in_slump, slump_severity).
    """
    if not recent_results_list:
        return 0.0, 0.0

    last5 = recent_results_list[-5:]
    losses_last5 = sum(1 for r in last5 if not r[1])
    # r = (day, win, opp_better_ranked, ...) -> not opp_better_ranked = joueur moins bien classé
    bad_losses = sum(1 for r in last5 if (not r[1] and (r[2] is False)))
    consecutive_losses = max(0, -current_streak) if current_streak is not None else 0

    is_in_slump = 1.0 if ((consecutive_losses >= 3 or losses_last5 >= 4) and bad_losses >= 1) else 0.0
    slump_severity = float(np.clip((consecutive_losses * 0.35) + (bad_losses * 0.45), 0.0, 3.0))
    return is_in_slump, slump_severity


def compute_bo5_stats(bo5_matches_count, bo5_wins_count, match_best_of):
    """
    Calcule le winrate lissé et l'expérience en Best-of-5 (Grand Chelem).
    Si le match n'est pas en Best-of-5 (ex: ATP 250/500/1000), retourne (0.50, 0.0) pour neutraliser.
    """
    if match_best_of != 5:
        return 0.50, 0.0
    m = bo5_matches_count if bo5_matches_count is not None else 0
    w = bo5_wins_count if bo5_wins_count is not None else 0
    smoothed_wr = (w + 1.0) / (m + 2.0)
    log_exp = float(np.log1p(m))
    return smoothed_wr, log_exp


# ---------------------------------------------------------------------------
# Parsing du score : nécessaire pour les features "mental/clutch" (set
# décisif, comeback, tie-breaks). Le score brut est toujours écrit du point
# de vue du VAINQUEUR (ex: "6-4 3-6 7-6(4)"), donc il faut le retourner si
# player_1 a perdu, pour obtenir la séquence du point de vue de p1.
# ---------------------------------------------------------------------------
_SCORE_BAD = ("RET", "W/O", "DEF", "ABN")


def parse_score_p1_perspective(score, p1_won):
    if not isinstance(score, str):
        return None
    su = score.upper()
    if any(x in su for x in _SCORE_BAD):
        return None
    sets = score.strip().split()
    parsed = []
    for s in sets:
        tb = "(" in s
        s_clean = re.sub(r"\([^)]*\)", "", s)
        parts = s_clean.split("-")
        if len(parts) != 2:
            continue
        try:
            g_w, g_l = int(parts[0]), int(parts[1])
        except ValueError:
            continue
        p1_g, p2_g = (g_w, g_l) if p1_won else (g_l, g_w)
        parsed.append((p1_g, p2_g, tb))
    return parsed if parsed else None


def derive_match_outcome_stats(score, p1_won, best_of):
    """A partir du score, dérive pour CE match (pas une moyenne glissante) :
    decided_win (1/0/nan), comeback (1/0/nan), tb_played, tb_won -- du point
    de vue de p1. Retourne aussi les versions p2 (symétriques). Ces valeurs
    sont ensuite ajoutées à l'historique glissant de chaque joueur pour
    servir de features aux matchs FUTURS (même logique que les stats de
    service : on résume la performance du match une fois qu'il est connu,
    et ça devient une donnée passée pour la suite)."""
    sets = parse_score_p1_perspective(score, p1_won)
    if not sets:
        return (np.nan, np.nan, 0, 0, np.nan, np.nan, 0, 0)

    n_sets = len(sets)
    decided = (best_of == 3 and n_sets == 3) or (best_of == 5 and n_sets == 5)
    p1_decided_win = (1.0 if p1_won else 0.0) if decided else np.nan
    p2_decided_win = (0.0 if p1_won else 1.0) if decided else np.nan

    p1_won_first_set = sets[0][0] > sets[0][1]
    if not p1_won_first_set:
        p1_comeback = 1.0 if p1_won else 0.0
        p2_comeback = np.nan
    else:
        p1_comeback = np.nan
        p2_comeback = 1.0 if not p1_won else 0.0

    tb_played = sum(1 for (a, b, tb) in sets if tb)
    tb_won_p1 = sum(1 for (a, b, tb) in sets if tb and a > b)
    tb_won_p2 = tb_played - tb_won_p1

    return (p1_decided_win, p2_decided_win, tb_played, tb_won_p1,
            p1_comeback, p2_comeback, tb_played, tb_won_p2)


def build_features(df, circuit="atp", state_only=False):
    df = df.sort_values(["tourney_date", "match_id"]).reset_index(drop=True)
    n = len(df)

    # ---- Extraction en tableaux numpy (une seule fois, avant la boucle) ----
    p1_name = df["p1_name"].to_numpy()
    p2_name = df["p2_name"].to_numpy()
    surface = df["surface"].to_numpy()
    date_min = df["tourney_date"].min()
    date_days = (df["tourney_date"] - date_min).dt.days.to_numpy()
    tourney_date_out = df["tourney_date"].to_numpy() if not state_only else None
    day_of_year = df["tourney_date"].dt.dayofyear.to_numpy() if not state_only else None

    p1_rank = df["p1_rank"].to_numpy(dtype=float)
    p2_rank = df["p2_rank"].to_numpy(dtype=float)
    p1_ioc = df["p1_ioc"].to_numpy(dtype=str)
    p2_ioc = df["p2_ioc"].to_numpy(dtype=str)

    p1_points = df["p1_rank_points"].to_numpy(dtype=float)
    p2_points = df["p2_rank_points"].to_numpy(dtype=float)
    p1_age = df["p1_age"].to_numpy(dtype=float)
    p2_age = df["p2_age"].to_numpy(dtype=float)
    p1_ht = df["p1_ht"].to_numpy(dtype=float)
    p2_ht = df["p2_ht"].to_numpy(dtype=float)
    p1_hand = df["p1_hand"].to_numpy()
    p2_hand = df["p2_hand"].to_numpy()
    p1_seed = df["p1_seed"].to_numpy(dtype=float)
    p2_seed = df["p2_seed"].to_numpy(dtype=float)
    p1_entry = df["p1_entry"].to_numpy()
    p2_entry = df["p2_entry"].to_numpy()
    target = df["target"].to_numpy(dtype=int)

    match_id = df["match_id"].to_numpy()
    tourney_id = df["tourney_id"].to_numpy()
    tourney_name_arr = df["tourney_name"].to_numpy()
    tourney_level = df["tourney_level"].to_numpy()
    best_of = df["best_of"].to_numpy()
    round_ = df["round"].to_numpy()
    retirement = df["retirement"].to_numpy()
    score_arr = df["score"].to_numpy()
    minutes_arr = df["minutes"].to_numpy(dtype=float)
    indoor_arr = df["indoor"].to_numpy()

    def stat_arrays(who):
        return {
            "svpt": df[f"{who}_svpt"].to_numpy(dtype=float),
            "ace": df[f"{who}_ace"].to_numpy(dtype=float),
            "df_": df[f"{who}_df"].to_numpy(dtype=float),
            "1stIn": df[f"{who}_1stIn"].to_numpy(dtype=float),
            "1stWon": df[f"{who}_1stWon"].to_numpy(dtype=float),
            "2ndWon": df[f"{who}_2ndWon"].to_numpy(dtype=float),
            "bpSaved": df[f"{who}_bpSaved"].to_numpy(dtype=float),
            "bpFaced": df[f"{who}_bpFaced"].to_numpy(dtype=float),
            "SvGms": df[f"{who}_SvGms"].to_numpy(dtype=float),
        }

    s1 = stat_arrays("p1")
    s2 = stat_arrays("p2")

    # ================= ETAT NON BORNE (scalaires, O(1) par joueur) =================
    elo = defaultdict(lambda: ELO_INIT)
    elo_surface = defaultdict(lambda: defaultdict(lambda: ELO_INIT))
    serve_elo = defaultdict(lambda: ELO_INIT)
    return_elo = defaultdict(lambda: ELO_INIT)
    serve_elo_surface = defaultdict(lambda: defaultdict(lambda: ELO_INIT))
    return_elo_surface = defaultdict(lambda: defaultdict(lambda: ELO_INIT))
    career_matches = defaultdict(int)
    career_retirements = defaultdict(int)
    peak_rank = defaultdict(lambda: np.inf)          # plus petit = meilleur
    first_match_day = {}
    surface_career_count = defaultdict(lambda: defaultdict(int))
    surface_career_wins = defaultdict(lambda: defaultdict(int))
    last_surface = {}
    last_tourney_id = {}
    last_tourney_country = {}
    matches_this_tourney = defaultdict(int)
    last_retirement = defaultdict(bool)
    streak = defaultdict(int)                         # signé : +N série de victoires, -N série de défaites
    last_play_date = {}
    matches_since_long_break = defaultdict(lambda: 999) # matchs joués depuis une pause > 75j
    bo5_matches = defaultdict(int)                    # matchs en 5 sets en carrière
    bo5_wins = defaultdict(int)                       # victoires en 5 sets en carrière
    h2h = defaultdict(lambda: defaultdict(lambda: [0, 0]))
    h2h_surface = defaultdict(lambda: defaultdict(lambda: defaultdict(lambda: [0, 0])))
    last_h2h_day = defaultdict(dict)
    last_h2h_result = defaultdict(dict)                # 1 si le joueur (clé) avait gagné la dernière confrontation

    # --- Nouveaux états ---
    elo_surface_w = defaultdict(lambda: defaultdict(lambda: ELO_INIT))  # Elo surface avec K adaptatif
    tourney_games_won   = defaultdict(int)   # jeux gagnés dans le tournoi en cours
    tourney_games_total = defaultdict(int)   # jeux totaux dans le tournoi en cours
    tourney_sets_won    = defaultdict(int)   # sets gagnés dans le tournoi en cours
    tourney_sets_total  = defaultdict(int)   # sets totaux dans le tournoi en cours
    
    wins_vs_arch = defaultdict(lambda: defaultdict(int))
    matches_vs_arch = defaultdict(lambda: defaultdict(int))

    # --- Infos statiques par joueur (utilisées par 05_predict_match.py) ---
    last_rank    = {}   # dernier classement ATP connu
    last_points  = {}   # derniers points ATP connus
    last_ht      = {}   # taille (cm)
    last_hand    = {}   # main dominante
    last_age     = {}   # âge au dernier match connu
    last_age_day = {}   # jour relatif du dernier match connu (pour recalculer l'âge actuel)

    # ================= ETAT BORNE (listes glissantes, MAX_HISTORY) =================
    # recent_results[p] : tuples (day, win, opp_better_ranked, surface,
    #                              decided_win, comeback, tb_played, tb_won, minutes)
    recent_results = defaultdict(list)
    elo_history = defaultdict(list)          # valeurs d'Elo successives, pour la tendance
    rank_history = defaultdict(list)         # (day, rank), pour le momentum de classement
    h2h_history = defaultdict(lambda: defaultdict(list))  # (day, win), borné, pour "H2H sur 2 ans"
    serve_return_hist = defaultdict(list)     # tuples à 8 valeurs (service + retour)

    
    # ================= Calcul du CPI des tournois =================
    df["year"] = df["tourney_date"].dt.year
    cpi_df = df.groupby(["tourney_name", "year"]).agg({
        "p1_ace": "sum", "p2_ace": "sum",
        "p1_svpt": "sum", "p2_svpt": "sum"
    }).reset_index()
    cpi_df["aces"] = cpi_df["p1_ace"] + cpi_df["p2_ace"]
    cpi_df["svpt"] = cpi_df["p1_svpt"] + cpi_df["p2_svpt"]
    cpi_df["ace_rate"] = cpi_df["aces"] / cpi_df["svpt"] * 100.0
    cpi_df["ace_rate"] = cpi_df["ace_rate"].fillna(8.5)
    # CPI glissant
    tourney_cpi_yearly = defaultdict(dict)
    for _, row in cpi_df.iterrows():
        tourney_cpi_yearly[row["tourney_name"]][row["year"]] = row["ace_rate"]
        
    tourney_name_arr = df["tourney_name"].to_numpy()
    year_arr = df["year"].to_numpy()
    round_arr = df["round"].to_numpy(dtype=str)
    
    
    fast_results = defaultdict(list)
    medium_results = defaultdict(list)
    slow_results = defaultdict(list)
    
    high_altitude_results = defaultdict(list)
    vs_lefty_results = defaultdict(list)
    tourney_champions = {}
    player_ioc_dict = {}
    game_dominance_hist = defaultdict(list)

    def _game_dominance_ema(hist, n):
        if not hist:
            return 0.50
        k = min(len(hist), n)
        recent = hist[-k:]
        alpha = 2.0 / (n + 1.0)
        weights = [(1.0 - alpha)**(k - 1 - i) for i in range(k)]
        return float(np.average(recent, weights=weights))

    # get_tourney_country is now a module-level function (see top of file)

    
    # COUNTRY_CONTINENT and compute_travel_strain are now module-level (see top of file)
    # ALTITUDES and get_altitude are now module-level (see top of file)

    # ================= Tableaux de sortie pré-alloués =================


    if not state_only:
        out = {name: np.empty(n) for name in [
            "elo_diff", "elo_surface_diff", "elo_p1", "elo_p2", "elo_trend_diff", "points_diff", "peak_rank_diff", "rank_momentum_diff",
            "form10_diff", "form365d_diff",
            "streak_diff", "consistency_diff",
            "h2h_diff", "h2h_total", "h2h_surface_diff", "days_since_h2h", "last_h2h_result_diff",
            "matches_this_tourney_diff", "avg_minutes_recent_diff",
            "last_retirement_diff", "career_retirement_rate_diff",
            "age_diff", "ht_diff", "experience_diff", "hand_missing_diff",
            "surface_experience_diff", "surface_winrate_diff", "surface_transition_diff",
            "seed_number_diff", "is_wildcard_diff", "is_qualifier_diff",
            "decided_set_winrate_diff", "comeback_rate_diff", "tiebreak_winrate_diff",
            "giant_killer_rate_diff",
            # --- Nouvelles features ---
            "sets_7d_diff", "sets_14d_diff", "sets_tourney_diff",
            "tourney_game_winpct_diff",
            "form10_surface_diff", "form365d_surface_diff",
            "elo_surface_w_diff",
            "upset_rate_10_diff",
            "late_round_winrate_diff", "early_round_winrate_diff",
            
            "hours_rest_diff", "short_rest_p1", "short_rest_p2", "is_night_match",
            "travel_strain_diff", "short_rest_surface_change_diff",
            "game_dominance_ema5_diff", "game_dominance_ema10_diff",
            
            "tourney_cpi", "fast_court_winrate_diff", "medium_court_winrate_diff", "slow_court_winrate_diff",
            "tourney_altitude", "high_altitude_winrate_diff", "home_advantage_diff",
            "kryptonite_diff", "is_defending_champion_diff",
            
            # --- Nouvelles features Archétypes ---
            "surface_bias_diff", "grind_mismatch", 
            "serve_return_edge1", "serve_return_edge2",
            "winrate_vs_arch_diff",
            
            # --- Phase B Nouvelles Features ---
            "matches_last_3d_diff", "matches_last_7d_diff", "hours_played_last_14d_diff",
            "form20_surface_diff",
            "serve_momentum_diff", "return_momentum_diff", "elo_momentum_diff",

            # --- Serve & Return Elo + Markov Point-by-Point ---
            "serve_elo_diff", "return_elo_diff",
            "serve_elo_surface_diff", "return_elo_surface_diff",
            "markov_p_win", "markov_hold_diff", "markov_expected_games",

            # --- Nouvelles features Modélisation (Rust, Bo5, Slump) ---
            "returning_from_break_diff", "is_returning_from_break_p1", "is_returning_from_break_p2",
            "bo5_winrate_diff", "bo5_experience_diff",
            "slump_diff", "is_in_slump_p1", "is_in_slump_p2"
        ]}
        hand_matchup_arr = np.empty(n, dtype=object)
        serve_diff_20 = {k: np.empty(n) for k in SERVE_RETURN_KEYS}
    else:
        out = None
        hand_matchup_arr = None
        serve_diff_20 = None

    
    # get_cpi and _speed_wr are now module-level functions (see top of file)
    _speed_wr = speed_wr  # local alias for backward compatibility within this function

    t0 = time.time()
    for i in range(n):

        
        p1, p2 = p1_name[i], p2_name[i]
        day = date_days[i]
        surf = surface[i]
        t_name = tourney_name_arr[i]
        t_year = year_arr[i]
        r1, r2 = p1_rank[i], p2_rank[i]


        last_p1 = last_play_date.get(p1)
        last_p2 = last_play_date.get(p2)

        # ---- Elo (avec dépréciation d'inactivité) ----
        e1 = get_decayed_elo(elo[p1], day, last_p1)
        e2 = get_decayed_elo(elo[p2], day, last_p2)
        es1 = get_decayed_elo(elo_surface[surf][p1], day, last_p1)
        es2 = get_decayed_elo(elo_surface[surf][p2], day, last_p2)
        eh1, eh2 = elo_history[p1], elo_history[p2]
        rh1, rh2 = rank_history[p1], rank_history[p2]
        rr1, rr2 = recent_results[p1], recent_results[p2]
        sh1, sh2 = serve_return_hist[p1], serve_return_hist[p2]
        has_rank = (r1 == r1 and r2 == r2)

        if not state_only:
            out["elo_diff"][i] = e1 - e2
            out["elo_surface_diff"][i] = es1 - es2
            out["elo_p1"][i] = e1
            out["elo_p2"][i] = e2
            trend1 = (e1 - eh1[-ELO_TREND_LAG]) if len(eh1) >= ELO_TREND_LAG else np.nan
            trend2 = (e2 - eh2[-ELO_TREND_LAG]) if len(eh2) >= ELO_TREND_LAG else np.nan
            out["elo_trend_diff"][i] = trend1 - trend2 if (trend1 == trend1 and trend2 == trend2) else np.nan

            # ---- Classement ----
            pts1, pts2 = p1_points[i], p2_points[i]
            out["points_diff"][i] = (np.log1p(pts1) - np.log1p(pts2)) if (pts1 == pts1 and pts2 == pts2) else np.nan

            pr1, pr2 = peak_rank[p1], peak_rank[p2]
            out["peak_rank_diff"][i] = (pr2 - pr1) if (pr1 < np.inf and pr2 < np.inf) else np.nan

            rh1, rh2 = rank_history[p1], rank_history[p2]
            rm1 = _rank_n_days_ago(rh1, day, RANK_MOMENTUM_DAYS)
            rm2 = _rank_n_days_ago(rh2, day, RANK_MOMENTUM_DAYS)
            mom1 = (rm1 - r1) if (rm1 == rm1 and r1 == r1) else np.nan   # positif = amélioration
            mom2 = (rm2 - r2) if (rm2 == rm2 and r2 == r2) else np.nan
            out["rank_momentum_diff"][i] = mom1 - mom2 if (mom1 == mom1 and mom2 == mom2) else np.nan

            # ---- Forme récente ----
            rr1, rr2 = recent_results[p1], recent_results[p2]
            out["form10_diff"][i] = _winrate_n(rr1, 10) - _winrate_n(rr2, 10)
            out["form365d_diff"][i] = _winrate_days(rr1, day, 365) - _winrate_days(rr2, day, 365)
            out["streak_diff"][i] = streak[p1] - streak[p2]
            out["consistency_diff"][i] = _consistency(rr2, 20) - _consistency(rr1, 20)  # std plus bas = plus régulier

            # CPI Features
            t_cpi = get_cpi(t_name, t_year, surf, tourney_cpi_yearly)
            out["tourney_cpi"][i] = t_cpi
            out["fast_court_winrate_diff"][i] = _speed_wr(fast_results, p1) - _speed_wr(fast_results, p2)
            out["medium_court_winrate_diff"][i] = _speed_wr(medium_results, p1) - _speed_wr(medium_results, p2)
            out["slow_court_winrate_diff"][i] = _speed_wr(slow_results, p1) - _speed_wr(slow_results, p2)
            
            # Altitude Features
            t_alt = get_altitude(t_name)
            out["tourney_altitude"][i] = t_alt
            out["high_altitude_winrate_diff"][i] = _speed_wr(high_altitude_results, p1) - _speed_wr(high_altitude_results, p2)

            # Home Advantage Features
            t_country = get_tourney_country(t_name)
            p1_home = 1 if p1_ioc[i] == t_country else 0
            p2_home = 1 if p2_ioc[i] == t_country else 0
            out["home_advantage_diff"][i] = p1_home - p2_home

            # Kryptonite
            p1_h = p1_hand[i]
            p2_h = p2_hand[i]
            wr1_vs_L = _speed_wr(vs_lefty_results, p1) if p2_h == 'L' else 0.5
            wr2_vs_L = _speed_wr(vs_lefty_results, p2) if p1_h == 'L' else 0.5
            out["kryptonite_diff"][i] = wr1_vs_L - wr2_vs_L

            # Title Defender
            is_def1 = 1 if tourney_champions.get(t_name) == p1 else 0
            is_def2 = 1 if tourney_champions.get(t_name) == p2 else 0
            out["is_defending_champion_diff"][i] = is_def1 - is_def2

            # ---- H2H ----
            h2h_w1, h2h_w2 = h2h[p1][p2]
            h2h_tot = h2h_w1 + h2h_w2
            out["h2h_total"][i] = h2h_tot
            out["h2h_diff"][i] = (h2h_w1 - h2h_w2) / h2h_tot if h2h_tot > 0 else 0.0

            hs_w1, hs_w2 = h2h_surface[p1][p2][surf]
            hs_tot = hs_w1 + hs_w2
            out["h2h_surface_diff"][i] = (hs_w1 - hs_w2) / hs_tot if hs_tot > 0 else 0.0

            last_day = last_h2h_day[p1].get(p2)
            out["days_since_h2h"][i] = (day - last_day) if last_day is not None else -1  # -1 = jamais rencontrés
            last_res = last_h2h_result[p1].get(p2)
            out["last_h2h_result_diff"][i] = (1 if last_res == 1 else (-1 if last_res == 0 else 0))

            # ---- Repos / fatigue ----
            rest1 = (day - last_play_date[p1]) if p1 in last_play_date else 365
            rest2 = (day - last_play_date[p2]) if p2 in last_play_date else 365
            
            # Approximation historique pour les features de fatigue (heures)
            out["hours_rest_diff"][i] = (rest1 - rest2) * 24.0
            out["short_rest_p1"][i] = 1.0 if rest1 < 1 else 0.0
            out["short_rest_p2"][i] = 1.0 if rest2 < 1 else 0.0
            out["is_night_match"][i] = 0.0  # Par défaut, non nocturne en historique
            t1_id = tourney_id[i]
            m1 = matches_this_tourney[p1] if last_tourney_id.get(p1) == t1_id else 0
            m2 = matches_this_tourney[p2] if last_tourney_id.get(p2) == t1_id else 0
            out["matches_this_tourney_diff"][i] = m1 - m2

            # Travel & Schedule Strain
            strain1, short_sc1 = compute_travel_strain(p1, day, t1_id, t_country, surf, last_tourney_id, last_play_date, last_tourney_country, last_surface)
            strain2, short_sc2 = compute_travel_strain(p2, day, t1_id, t_country, surf, last_tourney_id, last_play_date, last_tourney_country, last_surface)
            out["travel_strain_diff"][i] = strain1 - strain2
            out["short_rest_surface_change_diff"][i] = short_sc1 - short_sc2

            # Game Dominance EMA
            out["game_dominance_ema5_diff"][i] = _game_dominance_ema(game_dominance_hist[p1], 5) - _game_dominance_ema(game_dominance_hist[p2], 5)
            out["game_dominance_ema10_diff"][i] = _game_dominance_ema(game_dominance_hist[p1], 10) - _game_dominance_ema(game_dominance_hist[p2], 10)

            out["avg_minutes_recent_diff"][i] = _avg_minutes(rr1, 5) - _avg_minutes(rr2, 5)
            out["last_retirement_diff"][i] = int(last_retirement[p1]) - int(last_retirement[p2])
            cr1 = career_retirements[p1] / career_matches[p1] if career_matches[p1] > 0 else 0.0
            cr2 = career_retirements[p2] / career_matches[p2] if career_matches[p2] > 0 else 0.0
            out["career_retirement_rate_diff"][i] = cr1 - cr2

            # ---- Stats de service/retour glissantes & Archétypes ----
            sh1, sh2 = serve_return_hist[p1], serve_return_hist[p2]
            r5_1, r20_1 = _rolling_stats(sh1)
            r5_2, r20_2 = _rolling_stats(sh2)
            for k_idx, k in enumerate(SERVE_RETURN_KEYS):
                serve_diff_20[k][i] = r20_1[k_idx] - r20_2[k_idx]

            sb1 = _surface_bias(rr1, day, surf)
            sb2 = _surface_bias(rr2, day, surf)
            out["surface_bias_diff"][i] = sb1 - sb2

            arch1 = _get_archetype(r20_1, sb1)
            arch2 = _get_archetype(r20_2, sb2)

            grind1 = r20_1[8] if len(r20_1) > 8 else np.nan
            grind2 = r20_2[8] if len(r20_2) > 8 else np.nan
            out["grind_mismatch"][i] = grind1 - grind2 if (grind1 == grind1 and grind2 == grind2) else 0.0

            serve_win1 = (r20_1[2] * r20_1[3] + (1 - r20_1[2]) * r20_1[4]) if len(r20_1) > 4 else np.nan
            serve_win2 = (r20_2[2] * r20_2[3] + (1 - r20_2[2]) * r20_2[4]) if len(r20_2) > 4 else np.nan
            return_won1 = r20_1[6] if len(r20_1) > 6 else np.nan
            return_won2 = r20_2[6] if len(r20_2) > 6 else np.nan
            
            out["serve_return_edge1"][i] = serve_win1 - return_won2 if (serve_win1 == serve_win1 and return_won2 == return_won2) else 0.0
            out["serve_return_edge2"][i] = serve_win2 - return_won1 if (serve_win2 == serve_win2 and return_won1 == return_won1) else 0.0

            wr_vs_arch1 = wins_vs_arch[p1][arch2] / matches_vs_arch[p1][arch2] if matches_vs_arch[p1][arch2] > 0 else 0.5
            wr_vs_arch2 = wins_vs_arch[p2][arch1] / matches_vs_arch[p2][arch1] if matches_vs_arch[p2][arch1] > 0 else 0.5
            out["winrate_vs_arch_diff"][i] = wr_vs_arch1 - wr_vs_arch2

            # ---- Statique ----
            a1, a2 = p1_age[i], p2_age[i]
            out["age_diff"][i] = (a1 - a2) if (a1 == a1 and a2 == a2) else np.nan
            h1, h2 = p1_ht[i], p2_ht[i]
            out["ht_diff"][i] = (h1 - h2) if (h1 == h1 and h2 == h2) else np.nan
            hand_matchup_arr[i] = f"{_impute_hand(p1_hand[i])}_{_impute_hand(p2_hand[i])}"
            out["hand_missing_diff"][i] = int(p1_hand[i] not in ("R", "L")) - int(p2_hand[i] not in ("R", "L"))
            out["experience_diff"][i] = career_matches[p1] - career_matches[p2]

            # ---- Surface ----
            sc1, sc2 = surface_career_count[p1][surf], surface_career_count[p2][surf]
            out["surface_experience_diff"][i] = sc1 - sc2
            sw1 = surface_career_wins[p1][surf] / sc1 if sc1 > 0 else 0.5
            sw2 = surface_career_wins[p2][surf] / sc2 if sc2 > 0 else 0.5
            out["surface_winrate_diff"][i] = sw1 - sw2
            trans1 = int(last_surface.get(p1) is not None and last_surface[p1] != surf)
            trans2 = int(last_surface.get(p2) is not None and last_surface[p2] != surf)
            out["surface_transition_diff"][i] = trans1 - trans2

            # ---- Contexte (seed / entry) ----
            sd1, sd2 = p1_seed[i], p2_seed[i]
            sd1_f = sd1 if sd1 == sd1 else 999
            sd2_f = sd2 if sd2 == sd2 else 999
            out["seed_number_diff"][i] = sd2_f - sd1_f  # positif = p1 mieux classé (seed plus basse)
            out["is_wildcard_diff"][i] = int(p1_entry[i] == "WC") - int(p2_entry[i] == "WC")
            out["is_qualifier_diff"][i] = int(p1_entry[i] == "Q") - int(p2_entry[i] == "Q")

            # ---- Mental / clutch ----
            out["decided_set_winrate_diff"][i] = _mean_field(rr1, 4) - _mean_field(rr2, 4)
            out["comeback_rate_diff"][i] = _mean_field(rr1, 5) - _mean_field(rr2, 5)
            out["tiebreak_winrate_diff"][i] = _tb_rate(rr1) - _tb_rate(rr2)

            ob1 = _giant_killer_rate(rr1)
            ob2 = _giant_killer_rate(rr2)
            out["giant_killer_rate_diff"][i] = ob1 - ob2

            # ---- Sets joués (fatigue granulaire) ----
            out["sets_7d_diff"][i]  = _sets_count_recent(rr1, day, 7)  - _sets_count_recent(rr2, day, 7)
            out["sets_14d_diff"][i] = _sets_count_recent(rr1, day, 14) - _sets_count_recent(rr2, day, 14)
            st1 = tourney_sets_total[p1] if last_tourney_id.get(p1) == t1_id else 0
            st2 = tourney_sets_total[p2] if last_tourney_id.get(p2) == t1_id else 0
            out["sets_tourney_diff"][i] = st1 - st2

            # ---- Momentum intra-tournoi (domination en jeux et en sets) ----
            tgw1 = tourney_games_won[p1]   if last_tourney_id.get(p1) == t1_id else 0
            tgt1 = tourney_games_total[p1] if last_tourney_id.get(p1) == t1_id else 0
            tgw2 = tourney_games_won[p2]   if last_tourney_id.get(p2) == t1_id else 0
            tgt2 = tourney_games_total[p2] if last_tourney_id.get(p2) == t1_id else 0
            out["tourney_game_winpct_diff"][i] = (tgw1/tgt1 if tgt1 > 0 else 0.5) - (tgw2/tgt2 if tgt2 > 0 else 0.5)
            
            # ---- Phase B: Fatigue ----
            out["matches_last_3d_diff"][i] = _count_recent_tuples(rr1, day, 3) - _count_recent_tuples(rr2, day, 3)
            out["matches_last_7d_diff"][i] = _count_recent_tuples(rr1, day, 7) - _count_recent_tuples(rr2, day, 7)
            out["hours_played_last_14d_diff"][i] = (_avg_minutes(rr1, 14) * _count_recent_tuples(rr1, day, 14) / 60.0 if _avg_minutes(rr1, 14) == _avg_minutes(rr1, 14) else 0) - (_avg_minutes(rr2, 14) * _count_recent_tuples(rr2, day, 14) / 60.0 if _avg_minutes(rr2, 14) == _avg_minutes(rr2, 14) else 0)

            # ---- Forme sur la surface courante (récente) ----
            out["form10_surface_diff"][i]  = _winrate_surface(rr1, surf, 10) - _winrate_surface(rr2, surf, 10)
            out["form20_surface_diff"][i]  = _winrate_surface(rr1, surf, 20) - _winrate_surface(rr2, surf, 20)
            out["form365d_surface_diff"][i] = (_winrate_surface_days(rr1, surf, day, 365)
                                               - _winrate_surface_days(rr2, surf, day, 365))
            
            # ---- Phase B: Momentum Serve/Return & Elo ----
            eh1 = elo_history[p1]
            eh2 = elo_history[p2]
            out["elo_momentum_diff"][i] = (e1 - eh1[-30] if len(eh1) >= 30 else 0) - (e2 - eh2[-30] if len(eh2) >= 30 else 0)
            
            # serve momentum = recent (last 5) first won % - long term (last 20) first won %
            serve_mom1 = r5_1[3] - r20_1[3] if (r5_1[3] == r5_1[3] and r20_1[3] == r20_1[3]) else 0
            serve_mom2 = r5_2[3] - r20_2[3] if (r5_2[3] == r5_2[3] and r20_2[3] == r20_2[3]) else 0
            out["serve_momentum_diff"][i] = serve_mom1 - serve_mom2
            
            # return momentum = recent return points won % - long term return points won %
            return_mom1 = r5_1[6] - r20_1[6] if (r5_1[6] == r5_1[6] and r20_1[6] == r20_1[6]) else 0
            return_mom2 = r5_2[6] - r20_2[6] if (r5_2[6] == r5_2[6] and r20_2[6] == r20_2[6]) else 0
            out["return_momentum_diff"][i] = return_mom1 - return_mom2

            # ---- Elo surface avec K adaptatif par niveau de tournoi (avec dépréciation d'inactivité) ----
            esw1 = get_decayed_elo(elo_surface_w[surf][p1], day, last_p1)
            esw2 = get_decayed_elo(elo_surface_w[surf][p2], day, last_p2)
            out["elo_surface_w_diff"][i] = esw1 - esw2

            # ---- Giant killer / upset sur les 10 derniers matchs seulement ----
            out["upset_rate_10_diff"][i]   = _upset_rate_n(rr1, 10) - _upset_rate_n(rr2, 10)

            # ---- Winrate par phase de tournoi (QF/SF/F vs premiers tours, 2 ans) ----
            out["late_round_winrate_diff"][i]  = (_winrate_round_type(rr1, day, 730, late=True)
                                                   - _winrate_round_type(rr2, day, 730, late=True))
            out["early_round_winrate_diff"][i] = (_winrate_round_type(rr1, day, 730, late=False)
                                                   - _winrate_round_type(rr2, day, 730, late=False))

            # ---- Serve & Return Elo + Markov Point-by-Point (avec dépréciation d'inactivité) ----
            se1 = get_decayed_elo(serve_elo[p1], day, last_p1)
            se2 = get_decayed_elo(serve_elo[p2], day, last_p2)
            re1 = get_decayed_elo(return_elo[p1], day, last_p1)
            re2 = get_decayed_elo(return_elo[p2], day, last_p2)
            ses1 = get_decayed_elo(serve_elo_surface[surf][p1], day, last_p1)
            ses2 = get_decayed_elo(serve_elo_surface[surf][p2], day, last_p2)
            res1 = get_decayed_elo(return_elo_surface[surf][p1], day, last_p1)
            res2 = get_decayed_elo(return_elo_surface[surf][p2], day, last_p2)

            out["serve_elo_diff"][i] = se1 - se2
            out["return_elo_diff"][i] = re1 - re2
            out["serve_elo_surface_diff"][i] = ses1 - ses2
            out["return_elo_surface_diff"][i] = res1 - res2

            pa_m, pb_m = estimate_point_probabilities(ses1, res2, ses2, res1, surface=surf, circuit=circuit)
            bo_i = int(best_of[i]) if (best_of[i] == best_of[i] and best_of[i] in (3, 5)) else 3
            m_res = p_match(pa_m, pb_m, best_of=bo_i)

            out["markov_p_win"][i] = m_res["proba_a"]
            out["markov_hold_diff"][i] = m_res["hold_proba_a"] - m_res["hold_proba_b"]
            out["markov_expected_games"][i] = m_res["expected_total_games"]

            # --- Nouvelles features Modélisation (Rust, Bo5, Slump) ---
            # 1. Rust Factor (Reprise de blessure)
            rust1, _ = compute_rust_factor(day, last_p1, matches_since_long_break[p1])
            rust2, _ = compute_rust_factor(day, last_p2, matches_since_long_break[p2])
            out["returning_from_break_diff"][i] = rust1 - rust2
            out["is_returning_from_break_p1"][i] = rust1
            out["is_returning_from_break_p2"][i] = rust2

            # 2. Best-of-5 (Grand Chelem)
            bo5_wr1, bo5_exp1 = compute_bo5_stats(bo5_matches[p1], bo5_wins[p1], bo_i)
            bo5_wr2, bo5_exp2 = compute_bo5_stats(bo5_matches[p2], bo5_wins[p2], bo_i)
            out["bo5_winrate_diff"][i] = bo5_wr1 - bo5_wr2
            out["bo5_experience_diff"][i] = bo5_exp1 - bo5_exp2

            # 3. Slump (Spirale négative)
            slump1, slump_sev1 = compute_slump_indicator(rr1, streak[p1])
            slump2, slump_sev2 = compute_slump_indicator(rr2, streak[p2])
            out["slump_diff"][i] = slump_sev1 - slump_sev2
            out["is_in_slump_p1"][i] = slump1
            out["is_in_slump_p2"][i] = slump2
        else:
            t_country = get_tourney_country(t_name)
            t1_id = tourney_id[i]
            sh1, sh2 = serve_return_hist[p1], serve_return_hist[p2]
            r5_1, r20_1 = _rolling_stats(sh1)
            r5_2, r20_2 = _rolling_stats(sh2)
            sb1 = _surface_bias(recent_results[p1], day, surf)
            sb2 = _surface_bias(recent_results[p2], day, surf)
            arch1 = _get_archetype(r20_1, sb1)
            arch2 = _get_archetype(r20_2, sb2)
            esw1 = get_decayed_elo(elo_surface_w[surf][p1], day, last_p1)
            esw2 = get_decayed_elo(elo_surface_w[surf][p2], day, last_p2)
            se1 = get_decayed_elo(serve_elo[p1], day, last_p1)
            se2 = get_decayed_elo(serve_elo[p2], day, last_p2)
            re1 = get_decayed_elo(return_elo[p1], day, last_p1)
            re2 = get_decayed_elo(return_elo[p2], day, last_p2)
            ses1 = get_decayed_elo(serve_elo_surface[surf][p1], day, last_p1)
            ses2 = get_decayed_elo(serve_elo_surface[surf][p2], day, last_p2)
            res1 = get_decayed_elo(return_elo_surface[surf][p1], day, last_p1)
            res2 = get_decayed_elo(return_elo_surface[surf][p2], day, last_p2)
            pa_m, pb_m = estimate_point_probabilities(ses1, res2, ses2, res1, surface=surf, circuit=circuit)

        # ================= MISE A JOUR DE L'ETAT (après calcul des features) =================
        p1_won = bool(target[i])
        exp1 = elo_expected(e1, e2)
        outcome = derive_match_outcome_stats(score_arr[i], p1_won, best_of[i])
        p1_dec, p2_dec, p1_tbp, p1_tbw, p1_cb, p2_cb, p2_tbp, p2_tbw = outcome

        # Parsing score pour n_sets, is_late_round et stats intra-tournoi
        parsed_sets_i = parse_score_p1_perspective(score_arr[i], p1_won) or []
        n_sets_i  = len(parsed_sets_i)
        is_late_i = str(round_[i]) in LATE_ROUNDS
        if parsed_sets_i:
            gw1_m    = sum(a for a, b, _ in parsed_sets_i)           # jeux gagnés p1
            gw2_m    = sum(b for a, b, _ in parsed_sets_i)           # jeux gagnés p2
            g_total_m = gw1_m + gw2_m
            sw1_m    = sum(1 for a, b, _ in parsed_sets_i if a > b)  # sets gagnés p1
            sw2_m    = n_sets_i - sw1_m
            
            # Calcul du Margin of Victory (MoV) pour pondérer l'Elo
            game_diff = abs(gw1_m - gw2_m)
            mov_mult = ((game_diff / max(1, n_sets_i)) + 1) / 3.0
            mov_mult = max(0.5, min(mov_mult, 3.0)) # Borner entre x0.5 (très serré) et x3.0 (boucherie)

            if g_total_m > 0:
                game_dominance_hist[p1].append(gw1_m / g_total_m); _trim(game_dominance_hist[p1], 30)
                game_dominance_hist[p2].append(gw2_m / g_total_m); _trim(game_dominance_hist[p2], 30)
        else:
            gw1_m = gw2_m = g_total_m = sw1_m = sw2_m = 0
            mov_mult = 1.0

        # Mise à jour Elo (pondérée par MoV et K adaptatif à l'incertitude / expérience)
        k1 = get_dynamic_k(K_ELO, career_matches[p1]) * mov_mult
        k2 = get_dynamic_k(K_ELO, career_matches[p2]) * mov_mult
        new_e1 = e1 + k1 * ((1.0 if p1_won else 0.0) - exp1)
        new_e2 = e2 + k2 * ((0.0 if p1_won else 1.0) - (1.0 - exp1))
        elo[p1], elo[p2] = new_e1, new_e2
        eh1.append(new_e1); _trim(eh1, 60)
        eh2.append(new_e2); _trim(eh2, 60)

        exps1 = elo_expected(es1, es2)
        ks1 = get_dynamic_k(K_ELO, surface_career_count[p1][surf]) * mov_mult
        ks2 = get_dynamic_k(K_ELO, surface_career_count[p2][surf]) * mov_mult
        elo_surface[surf][p1] = es1 + ks1 * ((1.0 if p1_won else 0.0) - exps1)
        elo_surface[surf][p2] = es2 + ks2 * ((0.0 if p1_won else 1.0) - (1.0 - exps1))

        # Elo surface avec K adaptatif (GC>M1000>ATP500) pondéré par MoV & incertitude
        base_lvl = K_ELO_BY_LEVEL.get(str(tourney_level[i]), K_ELO)
        k_lvl_1 = get_dynamic_k(base_lvl, surface_career_count[p1][surf]) * mov_mult
        k_lvl_2 = get_dynamic_k(base_lvl, surface_career_count[p2][surf]) * mov_mult
        expsw = elo_expected(esw1, esw2)
        elo_surface_w[surf][p1] = esw1 + k_lvl_1 * ((1.0 if p1_won else 0.0) - expsw)
        elo_surface_w[surf][p2] = esw2 + k_lvl_2 * ((0.0 if p1_won else 1.0) - (1.0 - expsw))

        if r1 == r1:
            peak_rank[p1] = min(peak_rank[p1], r1)
            rh1.append((day, r1)); _trim(rh1, 60)
        if r2 == r2:
            peak_rank[p2] = min(peak_rank[p2], r2)
            rh2.append((day, r2)); _trim(rh2, 60)

        opp_better_1 = bool(r2 < r1) if has_rank else None
        opp_better_2 = bool(r1 < r2) if has_rank else None

        min_i = minutes_arr[i]
        rr1.append((day, p1_won, opp_better_1, surf, p1_dec, p1_cb, p1_tbp, p1_tbw, min_i, n_sets_i, is_late_i, p2, tourney_name_arr[i], score_arr[i])); _trim(rr1, MAX_HISTORY)
        rr2.append((day, not p1_won, opp_better_2, surf, p2_dec, p2_cb, p2_tbp, p2_tbw, min_i, n_sets_i, is_late_i, p1, tourney_name_arr[i], score_arr[i])); _trim(rr2, MAX_HISTORY)

        last_play_date[p1] = day
        last_play_date[p2] = day
        career_matches[p1] += 1
        career_matches[p2] += 1
        if retirement[i]:
            career_retirements[p1] += 1
            career_retirements[p2] += 1
        last_retirement[p1] = bool(retirement[i])
        last_retirement[p2] = bool(retirement[i])

        # Infos statiques (taille, main, âge, classement)
        if r1 == r1: last_rank[p1] = r1
        if r2 == r2: last_rank[p2] = r2
        if p1_points[i] == p1_points[i]: last_points[p1] = p1_points[i]
        if p2_points[i] == p2_points[i]: last_points[p2] = p2_points[i]
        if p1_ht[i] == p1_ht[i]:  last_ht[p1] = p1_ht[i]
        if p2_ht[i] == p2_ht[i]:  last_ht[p2] = p2_ht[i]
        if p1_hand[i] in ("R", "L"): last_hand[p1] = p1_hand[i]
        if p2_hand[i] in ("R", "L"): last_hand[p2] = p2_hand[i]
        if p1_age[i] == p1_age[i]: last_age[p1] = p1_age[i]; last_age_day[p1] = day
        if p2_age[i] == p2_age[i]: last_age[p2] = p2_age[i]; last_age_day[p2] = day

        if p1 not in first_match_day:
            first_match_day[p1] = day
        if p2 not in first_match_day:
            first_match_day[p2] = day

        surface_career_count[p1][surf] += 1
        surface_career_count[p2][surf] += 1
        if p1_won:
            surface_career_wins[p1][surf] += 1
        else:
            surface_career_wins[p2][surf] += 1
        last_surface[p1] = surf
        last_surface[p2] = surf
        last_tourney_country[p1] = t_country
        last_tourney_country[p2] = t_country

        if last_tourney_id.get(p1) == t1_id:
            matches_this_tourney[p1] += 1
            tourney_games_won[p1]   += gw1_m;    tourney_games_total[p1] += g_total_m
            tourney_sets_won[p1]    += sw1_m;    tourney_sets_total[p1]  += n_sets_i
        else:
            last_tourney_id[p1] = t1_id
            matches_this_tourney[p1] = 1
            tourney_games_won[p1]   = gw1_m;     tourney_games_total[p1] = g_total_m
            tourney_sets_won[p1]    = sw1_m;     tourney_sets_total[p1]  = n_sets_i
        if last_tourney_id.get(p2) == t1_id:
            matches_this_tourney[p2] += 1
            tourney_games_won[p2]   += gw2_m;    tourney_games_total[p2] += g_total_m
            tourney_sets_won[p2]    += sw2_m;    tourney_sets_total[p2]  += n_sets_i
        else:
            last_tourney_id[p2] = t1_id
            matches_this_tourney[p2] = 1
            tourney_games_won[p2]   = gw2_m;     tourney_games_total[p2] = g_total_m
            tourney_sets_won[p2]    = sw2_m;     tourney_sets_total[p2]  = n_sets_i

        # Suivi Rust Factor (reprise de compétition après absence > 75 jours)
        if last_p1 is not None and (day - last_p1) > 75:
            matches_since_long_break[p1] = 1
        else:
            if matches_since_long_break[p1] < 10:
                matches_since_long_break[p1] += 1

        if last_p2 is not None and (day - last_p2) > 75:
            matches_since_long_break[p2] = 1
        else:
            if matches_since_long_break[p2] < 10:
                matches_since_long_break[p2] += 1

        # Suivi Best-of-5 (Grand Chelem)
        if best_of[i] == 5:
            bo5_matches[p1] += 1
            bo5_matches[p2] += 1
            if p1_won:
                bo5_wins[p1] += 1
            else:
                bo5_wins[p2] += 1

        streak[p1] = _update_streak(streak[p1], p1_won)
        streak[p2] = _update_streak(streak[p2], not p1_won)

        if p1_won:
            h2h[p1][p2][0] += 1; h2h[p2][p1][0] += 1
            h2h_surface[p1][p2][surf][0] += 1; h2h_surface[p2][p1][surf][0] += 1
        else:
            h2h[p1][p2][1] += 1; h2h[p2][p1][1] += 1
            h2h_surface[p1][p2][surf][1] += 1; h2h_surface[p2][p1][surf][1] += 1

        h2h_history[p1][p2].append((day, p1_won)); _trim(h2h_history[p1][p2], 30)
        h2h_history[p2][p1].append((day, not p1_won)); _trim(h2h_history[p2][p1], 30)
        last_h2h_day[p1][p2] = day
        last_h2h_day[p2][p1] = day
        last_h2h_result[p1][p2] = 1 if p1_won else 0
        last_h2h_result[p2][p1] = 0 if p1_won else 1

        matches_vs_arch[p1][arch2] += 1
        matches_vs_arch[p2][arch1] += 1
        if p1_won:
            wins_vs_arch[p1][arch2] += 1
        else:
            wins_vs_arch[p2][arch1] += 1

        sh1.append(_extract_serve_return_stats(s1, s2, i, min_i)); _trim(sh1, MAX_HISTORY)
        sh2.append(_extract_serve_return_stats(s2, s1, i, min_i)); _trim(sh2, MAX_HISTORY)

        # ---- Mise à jour Serve Elo & Return Elo (général et par surface) ----
        svpt1_val = s1["svpt"][i]
        w1_won_val = s1["1stWon"][i] + s1["2ndWon"][i]
        spw1_actual = (w1_won_val / svpt1_val) if (svpt1_val > 0 and w1_won_val == w1_won_val) else None

        svpt2_val = s2["svpt"][i]
        w2_won_val = s2["1stWon"][i] + s2["2ndWon"][i]
        spw2_actual = (w2_won_val / svpt2_val) if (svpt2_val > 0 and w2_won_val == w2_won_val) else None

        pa_gen, pb_gen = estimate_point_probabilities(se1, re2, se2, re1, surface="Default", circuit=circuit)
        k_srv1 = get_dynamic_k(K_SERVE_ELO, career_matches[p1])
        k_ret1 = get_dynamic_k(K_RETURN_ELO, career_matches[p1])
        k_srv2 = get_dynamic_k(K_SERVE_ELO, career_matches[p2])
        k_ret2 = get_dynamic_k(K_RETURN_ELO, career_matches[p2])

        if spw1_actual is not None:
            delta1_g = spw1_actual - pa_gen
            serve_elo[p1] += k_srv1 * delta1_g
            return_elo[p2] -= k_ret2 * delta1_g

            delta1_s = spw1_actual - pa_m
            serve_elo_surface[surf][p1] += k_srv1 * delta1_s
            return_elo_surface[surf][p2] -= k_ret2 * delta1_s

        if spw2_actual is not None:
            delta2_g = spw2_actual - pb_gen
            serve_elo[p2] += k_srv2 * delta2_g
            return_elo[p1] -= k_ret1 * delta2_g

            delta2_s = spw2_actual - pb_m
            serve_elo_surface[surf][p2] += k_srv2 * delta2_s
            return_elo_surface[surf][p1] -= k_ret1 * delta2_s

        # Update court speeds results
        t_cpi_val = get_cpi(t_name, t_year, surf, tourney_cpi_yearly)
        if t_cpi_val >= 9.5:
            fast_results[p1].append((day, 1 if p1_won else 0)); _trim(fast_results[p1], 30)
            fast_results[p2].append((day, 0 if p1_won else 1)); _trim(fast_results[p2], 30)
        elif t_cpi_val <= 6.5:
            slow_results[p1].append((day, 1 if p1_won else 0)); _trim(slow_results[p1], 30)
            slow_results[p2].append((day, 0 if p1_won else 1)); _trim(slow_results[p2], 30)
        else:
            medium_results[p1].append((day, 1 if p1_won else 0)); _trim(medium_results[p1], 30)
            medium_results[p2].append((day, 0 if p1_won else 1)); _trim(medium_results[p2], 30)

        t_alt_val = get_altitude(t_name)
        if t_alt_val >= 500:
            high_altitude_results[p1].append((day, 1 if p1_won else 0)); _trim(high_altitude_results[p1], 30)
            high_altitude_results[p2].append((day, 0 if p1_won else 1)); _trim(high_altitude_results[p2], 30)

        if p2_hand[i] == 'L':
            vs_lefty_results[p1].append((day, 1 if p1_won else 0)); _trim(vs_lefty_results[p1], 30)
        if p1_hand[i] == 'L':
            vs_lefty_results[p2].append((day, 0 if p1_won else 1)); _trim(vs_lefty_results[p2], 30)

        # Mise à jour du tenant du titre si finale
        if round_[i] == 'F':
            tourney_champions[tourney_name_arr[i]] = p1 if p1_won else p2

        if i % 20000 == 0 and i > 0:
            elapsed = time.time() - t0
            rate = i / elapsed
            print(f"  {i}/{n} matchs traités ({rate:.0f}/s, ETA {(n - i) / rate:.0f}s)", flush=True)

    if state_only:
        result = None
    else:
        result = pd.DataFrame({
            "match_id": match_id,
            "tourney_date": tourney_date_out,
            "surface": surface,
            "indoor": indoor_arr,
            "tourney_level": tourney_level,
            "best_of": best_of,
            "round": round_,
            "retirement": retirement,
            "hand_matchup": hand_matchup_arr,
            "target": target,
            **out,
        })
        for k in SERVE_RETURN_KEYS:
            result[f"{k}_20_diff"] = serve_diff_20[k]

    return result, {
        "elo": dict(elo),
        "elo_surface": {s: dict(d) for s, d in elo_surface.items()},
        "elo_surface_w": {s: dict(d) for s, d in elo_surface_w.items()},
        "serve_elo": dict(serve_elo),
        "return_elo": dict(return_elo),
        "serve_elo_surface": {s: dict(d) for s, d in serve_elo_surface.items()},
        "return_elo_surface": {s: dict(d) for s, d in return_elo_surface.items()},
        "elo_history": dict(elo_history),
        "rank_history": dict(rank_history),
        "peak_rank": dict(peak_rank),
        "career_matches": dict(career_matches),
        "career_retirements": dict(career_retirements),
        "first_match_day": dict(first_match_day),
        "surface_career_count": {p: dict(d) for p, d in surface_career_count.items()},
        "surface_career_wins": {p: dict(d) for p, d in surface_career_wins.items()},
        "last_surface": dict(last_surface),
        "last_tourney_id": dict(last_tourney_id),
        "last_tourney_country": dict(last_tourney_country),
        "matches_this_tourney": dict(matches_this_tourney),
        "tourney_games_won": dict(tourney_games_won),
        "tourney_games_total": dict(tourney_games_total),
        "tourney_sets_won": dict(tourney_sets_won),
        "tourney_sets_total": dict(tourney_sets_total),
        "recent_results": dict(recent_results),
        "streak": dict(streak),
        "last_play_date": dict(last_play_date),
        "last_retirement": dict(last_retirement),
        "matches_since_long_break": dict(matches_since_long_break),
        "bo5_matches": dict(bo5_matches),
        "bo5_wins": dict(bo5_wins),
        "h2h": {p: {q: list(v) for q, v in d.items()} for p, d in h2h.items()},
        "h2h_surface": {p: {q: {s: list(v) for s, v in sd.items()}
                            for q, sd in d.items()} for p, d in h2h_surface.items()},
        "h2h_history": {p: {q: list(v) for q, v in d.items()}
                        for p, d in h2h_history.items()},
        "last_h2h_day": {p: dict(d) for p, d in last_h2h_day.items()},
        "last_h2h_result": {p: dict(d) for p, d in last_h2h_result.items()},
        "serve_return_hist": dict(serve_return_hist),
        "wins_vs_arch": {p: dict(d) for p, d in wins_vs_arch.items()},
        "matches_vs_arch": {p: dict(d) for p, d in matches_vs_arch.items()},
        "tourney_champions": dict(tourney_champions),
        "game_dominance_hist": {p: list(v) for p, v in game_dominance_hist.items()},
        "vs_lefty_results": dict(vs_lefty_results),
        "high_altitude_results": dict(high_altitude_results),
        "player_ioc_dict": dict(player_ioc_dict),
        "tourney_cpi_yearly": dict(tourney_cpi_yearly),
        "fast_results": dict(fast_results),
        "medium_results": dict(medium_results),
        "slow_results": dict(slow_results),
        "tourney_countries": dict(tourney_countries) if 'tourney_countries' in locals() else {},
        # Infos statiques
        "last_rank": last_rank,
        "last_points": last_points,
        "last_ht": last_ht,
        "last_hand": last_hand,
        "last_age": last_age,
        "last_age_day": last_age_day,
        # Méta
        "date_min": date_min,
        "last_day": int(date_days[-1]),
    }


# ---------------------------------------------------------------------------
# Helpers sur l'historique glissant. Index des tuples dans recent_results :
# 0 day, 1 win, 2 opp_better_ranked, 3 surface, 4 decided_win, 5 comeback,
# 6 tb_played, 7 tb_won, 8 minutes, 9 n_sets, 10 is_late_round
# ---------------------------------------------------------------------------

def _update_streak(s, won):
    """Série signée : +N = N victoires d'affilée, -N = N défaites d'affilée.
    Repart proprement à 1 (ou -1) dès que la tendance s'inverse."""
    if won:
        return s + 1 if s >= 0 else 1
    else:
        return s - 1 if s <= 0 else -1


def _trim(lst, max_len):
    if len(lst) > max_len:
        del lst[: len(lst) - max_len]


def _impute_hand(h):
    """Imputation par le mode (~85% des joueurs sont droitiers) plutôt que
    par une catégorie 'inconnue' séparée. Sans ça, 'main inconnue' devient
    un proxy caché pour 'joueur obscur / bas niveau' (données manquantes
    bien plus fréquentes en qualifs/futures), ce qui pollue la feature de
    matchup gaucher/droitier avec un signal qui n'a rien à voir avec la
    main. Le signal de complétude des données est gardé séparément et
    explicitement via hand_missing_diff."""
    return h if h in ("R", "L") else "R"


def _winrate_n(history, n_matches):
    if not history:
        return 0.5
    window = history[-n_matches:]
    return sum(t[1] for t in window) / len(window)


def _winrate_days(history, day, days):
    if not history:
        return 0.5
    window = [t[1] for t in history if day - t[0] <= days]
    return sum(window) / len(window) if window else 0.5


def _count_recent_tuples(history, day, days):
    return sum(1 for t in history if 0 <= day - t[0] <= days)


def _consistency(history, n):
    window = history[-n:]
    if len(window) < 3:
        return 0.5  # valeur neutre, pas assez de données
    vals = [1.0 if t[1] else 0.0 for t in window]
    return float(np.std(vals))


def _avg_minutes(history, n):
    window = history[-n:]
    vals = [t[8] for t in window if t[8] == t[8]]
    return sum(vals) / len(vals) if vals else np.nan


def _mean_field(history, field_idx):
    vals = [t[field_idx] for t in history if t[field_idx] == t[field_idx]]
    return sum(vals) / len(vals) if vals else np.nan


def _tb_rate(history):
    played = sum(t[6] for t in history)
    won = sum(t[7] for t in history)
    return won / played if played > 0 else np.nan


def _giant_killer_rate(history):
    window = [t[1] for t in history if t[2] is True]
    return sum(window) / len(window) if window else np.nan


# ---------------------------------------------------------------------------
# Nouveaux helpers
# ---------------------------------------------------------------------------

def _sets_count_recent(history, day, days):
    """Nombre de sets joués sur les derniers `days` jours (t[9] = n_sets)."""
    return sum(t[9] for t in history if 0 <= day - t[0] <= days and len(t) > 9)


def _winrate_surface(history, surf, n):
    """Winrate sur les `n` derniers matchs joués sur la surface `surf`."""
    window = [t for t in history if t[3] == surf][-n:]
    return sum(t[1] for t in window) / len(window) if window else 0.5


def _winrate_surface_days(history, surf, day, days):
    """Winrate sur les matchs joués sur `surf` dans les `days` derniers jours."""
    window = [t[1] for t in history if t[3] == surf and 0 <= day - t[0] <= days]
    return sum(window) / len(window) if window else 0.5


def _giant_killer_rate_n(history, n):
    """% victoires contre mieux classé sur les `n` derniers matchs seulement."""
    window = history[-n:]
    vs_better = [t[1] for t in window if t[2] is True]
    return sum(vs_better) / len(vs_better) if vs_better else np.nan


def _upset_rate_n(history, n):
    """% défaites contre moins bien classé sur les `n` derniers matchs.
    Mesure la tendance à 'se faire surprendre' par des outsiders."""
    window = history[-n:]
    vs_worse = [t[1] for t in window if t[2] is False]
    # Proportion de matchs perdus contre des moins bien classés
    return sum(1 - v for v in vs_worse) / len(vs_worse) if vs_worse else np.nan


def _winrate_round_type(history, day, days, late=True):
    """Winrate en tours tardifs (QF/SF/F) ou en premiers tours sur les 2 derniers
    ans. t[10] = is_late_round (bool). Retourne 0.5 si pas d'historique."""
    window = [t[1] for t in history
              if 0 <= day - t[0] <= days
              and len(t) > 10
              and bool(t[10]) == late]
    return sum(window) / len(window) if window else 0.5


def _rank_n_days_ago(rank_hist, day, days):
    """Cherche, en remontant l'historique, la valeur de classement la plus
    proche d'il y a `days` jours (au moins aussi vieille)."""
    for d, r in reversed(rank_hist):
        if day - d >= days:
            return r
    return np.nan


def _extract_serve_return_stats(own, opp, i, minutes):
    svpt = own["svpt"][i]
    if not (svpt == svpt) or svpt == 0:
        return (np.nan,) * 9
    ace, df_ = own["ace"][i], own["df_"][i]
    first_in, first_won, second_won = own["1stIn"][i], own["1stWon"][i], own["2ndWon"][i]
    bp_saved, bp_faced = own["bpSaved"][i], own["bpFaced"][i]
    second_svpt = svpt - first_in if (first_in == first_in) else np.nan

    opp_svpt = opp["svpt"][i]
    opp_1stWon, opp_2ndWon = opp["1stWon"][i], opp["2ndWon"][i]
    opp_bpFaced, opp_bpSaved = opp["bpFaced"][i], opp["bpSaved"][i]
    return_pts_won = (opp_svpt - opp_1stWon - opp_2ndWon) if opp_svpt == opp_svpt else np.nan
    bp_converted = (opp_bpFaced - opp_bpSaved) if opp_bpFaced == opp_bpFaced else np.nan

    # Calculate grind index (minutes per game)
    own_games = own["SvGms"][i] if "SvGms" in own else np.nan
    opp_games = opp["SvGms"][i] if "SvGms" in opp else np.nan
    total_games = own_games + opp_games if (own_games == own_games and opp_games == opp_games) else np.nan
    grind = minutes / total_games if (minutes == minutes and total_games == total_games and total_games > 0) else np.nan

    return (
        ace / svpt if ace == ace else np.nan,
        df_ / svpt if df_ == df_ else np.nan,
        first_in / svpt if first_in == first_in else np.nan,
        first_won / first_in if (first_won == first_won and first_in) else np.nan,
        second_won / second_svpt if (second_won == second_won and second_svpt) else np.nan,
        bp_saved / bp_faced if (bp_saved == bp_saved and bp_faced) else np.nan,
        return_pts_won / opp_svpt if (return_pts_won == return_pts_won and opp_svpt) else np.nan,
        bp_converted / opp_bpFaced if (bp_converted == bp_converted and opp_bpFaced) else np.nan,
        grind
    )


def _rolling_stats(history):
    """Retourne (moyennes 5 derniers, moyennes 20 derniers), tuples de 9
    valeurs dans l'ordre de SERVE_RETURN_KEYS."""
    if not history:
        nan_val = (np.nan,) * 9
        return nan_val, nan_val

    def avg(window):
        if not window:
            return (np.nan,) * 9
        out = []
        for k in range(9):
            vals = [w[k] for w in window if w[k] == w[k]]
            out.append(sum(vals) / len(vals) if vals else np.nan)
        return tuple(out)

    return avg(history[-5:]), avg(history[-20:])

def _surface_bias(rr, current_day, current_surf):
    count_52w = 0
    surf_count_52w = 0
    for res in reversed(rr):
        day = res[0]
        if current_day - day > 365:
            break
        count_52w += 1
        if res[3] == current_surf:
            surf_count_52w += 1
    return surf_count_52w / count_52w if count_52w > 0 else 0.0

def _get_archetype(avg_20, surf_bias):
    if not avg_20 or avg_20[0] != avg_20[0]: return "Polyvalent" # NaN check
    ace_rate = avg_20[0]
    first_won = avg_20[3]
    return_won = avg_20[6]
    grind = avg_20[8]
    
    if ace_rate > 0.10 and first_won > 0.73:
        return "Gros Serveur"
    elif grind > 4.5 and return_won > 0.38:
        return "Défenseur / Grinder"
    elif return_won > 0.38 and (avg_20[2] * first_won + (1 - avg_20[2]) * avg_20[4]) > 0.65:
        return "All-Court Élite"
    elif surf_bias > 0.65:
        return "Terrien" if surf_bias > 0.65 else "Spécialiste Surface" # Simplifié pour la démo
    elif grind < 4.0 and first_won > 0.70:
        return "Attaquant de Fond"
    else:
        return "Polyvalent"


def prune_player_state(player_state, max_years=5):
    """
    Élagage agressif et optimisation mémoire pour respecter la limite de 512 Mo de RAM sur Render.
    Réduit la taille de l'état en mémoire de ~192 Mo à ~45 Mo.
    """
    for k in ["serve_return_hist", "h2h_history"]:
        if k in player_state:
            del player_state[k]

    last_day_val = player_state.get("last_day", 0)
    # Filtrer les joueurs ayant joué au moins un match dans les N dernières années (1859 joueurs vs 6518)
    active_players = {p for p, d in player_state.get("last_play_date", {}).items() if (last_day_val - d) <= 365 * max_years}

    heavy_keys = [
        "recent_results", "fast_results", "medium_results", "slow_results",
        "high_altitude_results", "vs_lefty_results", "rank_history", "elo_history",
        "game_dominance_hist"
    ]
    for k in heavy_keys:
        if k in player_state:
            player_state[k] = {
                p: (v[-20:] if len(v) > 20 else v)
                for p, v in player_state[k].items()
                if p in active_players and len(v) > 0
            }

    for k in ["h2h", "h2h_surface", "last_h2h_day", "last_h2h_result"]:
        if k in player_state:
            new_d = {}
            for p1, inner in player_state[k].items():
                if p1 in active_players:
                    filtered_inner = {
                        p2: v for p2, v in inner.items()
                        if p2 in active_players and (v != [0, 0] if isinstance(v, list) else True)
                    }
                    if filtered_inner:
                        new_d[p1] = filtered_inner
            player_state[k] = new_d

    for k in ["elo_surface", "elo_surface_w", "serve_elo_surface", "return_elo_surface"]:
        if k in player_state:
            player_state[k] = {
                surf: {p: val for p, val in inner.items() if p in active_players}
                for surf, inner in player_state[k].items()
            }

    for k in ["surface_career_count", "surface_career_wins", "wins_vs_arch", "matches_vs_arch"]:
        if k in player_state:
            player_state[k] = {
                p: inner for p, inner in player_state[k].items() if p in active_players
            }

    for k in ["elo", "serve_elo", "return_elo", "peak_rank", "career_matches", "career_retirements",
              "first_match_day", "last_surface", "last_tourney_id", "last_tourney_country",
              "matches_this_tourney", "tourney_games_won", "tourney_games_total",
              "tourney_sets_won", "tourney_sets_total", "streak", "last_play_date",
              "last_retirement", "last_rank", "last_points", "last_ht", "last_hand",
              "last_age", "last_age_day", "matches_since_long_break", "bo5_matches", "bo5_wins"]:
        if k in player_state:
            player_state[k] = {p: val for p, val in player_state[k].items() if p in active_players}

    return player_state


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--circuit", default="atp", choices=["atp", "wta"], help="Circuit to process (atp or wta)")
    parser.add_argument("--state-only", action="store_true", help="Only compute and update player state and players/tournaments metadata (lightweight, uses < 80MB RAM)")
    args = parser.parse_args()

    in_path = PROCESSED_DIR / f"matches_symmetric_{args.circuit}.parquet"
    if not in_path.exists():
        raise FileNotFoundError(f"{in_path} introuvable. Lance d'abord: python 01_build_dataset.py --circuit {args.circuit}")

    if args.state_only:
        cols = [
            "p1_name", "p2_name", "p1_ioc", "p2_ioc", "surface", "tourney_date", "p1_rank", "p2_rank",
            "p1_rank_points", "p2_rank_points", "p1_age", "p2_age", "p1_ht", "p2_ht",
            "p1_hand", "p2_hand", "p1_seed", "p2_seed", "p1_entry", "p2_entry",
            "target", "match_id", "tourney_id", "tourney_name", "tourney_level",
            "best_of", "round", "retirement", "score", "minutes", "indoor",
            "p1_svpt", "p1_ace", "p1_df", "p1_1stIn", "p1_1stWon", "p1_2ndWon",
            "p1_bpSaved", "p1_bpFaced", "p1_SvGms",
            "p2_svpt", "p2_ace", "p2_df", "p2_1stIn", "p2_1stWon", "p2_2ndWon",
            "p2_bpSaved", "p2_bpFaced", "p2_SvGms"
        ]
        df = pd.read_parquet(in_path, columns=cols)
    else:
        df = pd.read_parquet(in_path)

    t0 = time.time()
    feats, player_state = build_features(df, circuit=args.circuit, state_only=args.state_only)
    print(f"Temps calcul: {time.time() - t0:.1f}s")

    if not args.state_only and feats is not None:
        out_path = PROCESSED_DIR / f"features_{args.circuit}.parquet"
        feats.to_parquet(out_path, index=False)
        print(f"{len(feats)} lignes, {feats.shape[1]} colonnes -> {out_path}")

    # Optimisation de la mémoire et élagage agressif
    player_state = prune_player_state(player_state, max_years=5)

    # Sauvegarde de l'état des joueurs pour 05_predict_match.py
    import pickle
    import json
    state_path = PROCESSED_DIR / f"player_state_{args.circuit}.pkl"
    with open(state_path, "wb") as f:
        pickle.dump(player_state, f, protocol=pickle.HIGHEST_PROTOCOL)
    print(f"Etat des joueurs sauvegarde -> {state_path}")
    print(f"  {len(player_state['elo'])} joueurs actifs conserves dans la base.")

    # Export automatique de players_{circuit}.json pour l'autocomplétion Web/API
    last_day_val = player_state.get("last_day", 0)
    players_list = []
    for p in sorted(player_state["elo"].keys()):
        p_elo = round(player_state["elo"].get(p, 1500))
        p_rank_val = player_state.get("last_rank", {}).get(p)
        p_rank = int(p_rank_val) if (p_rank_val is not None and p_rank_val == p_rank_val) else None
        p_hand = player_state.get("last_hand", {}).get(p, "R")
        p_last_day = player_state.get("last_play_date", {}).get(p)
        days_ago = int(last_day_val - p_last_day) if p_last_day is not None else 9999
        players_list.append({
            "name": p,
            "elo": p_elo,
            "rank": p_rank,
            "hand": p_hand,
            "days_ago": days_ago
        })
    players_json_path = PROCESSED_DIR / f"players_{args.circuit}.json"
    with open(players_json_path, "w", encoding="utf-8") as f:
        json.dump(players_list, f, indent=2)
    print(f"Liste des joueurs ({len(players_list)}) exportee -> {players_json_path}")

    # Mise à jour automatique de tournaments.json
    tournaments_file = PROCESSED_DIR / "tournaments.json"
    existing_tourneys = {}
    if tournaments_file.exists():
        try:
            with open(tournaments_file, "r", encoding="utf-8") as f:
                for t in json.load(f):
                    existing_tourneys[t["name"]] = t
        except Exception:
            pass

    t_groups = df.groupby("tourney_name").agg({
        "surface": lambda x: x.dropna().mode().iloc[0] if len(x.dropna()) > 0 else "Hard",
        "tourney_level": lambda x: x.dropna().mode().iloc[0] if len(x.dropna()) > 0 else "A",
        "indoor": lambda x: int((x.dropna() == "I").mean() > 0.5) if len(x.dropna()) > 0 else 0,
        "best_of": lambda x: int(x.dropna().mode().iloc[0]) if len(x.dropna()) > 0 else 3
    }).reset_index()

    for _, row in t_groups.iterrows():
        t_name = str(row["tourney_name"]).strip()
        if not t_name or t_name.lower() in ("nan", "none", "unknown"):
            continue
        existing_tourneys[t_name] = {
            "name": t_name,
            "surface": str(row["surface"]),
            "level": str(row["tourney_level"]),
            "indoor": int(row["indoor"]),
            "best_of": int(row["best_of"])
        }

    tourneys_list = sorted(existing_tourneys.values(), key=lambda x: x["name"])
    with open(tournaments_file, "w", encoding="utf-8") as f:
        json.dump(tourneys_list, f, indent=2)
    print(f"Tournois ({len(tourneys_list)}) mis a jour -> {tournaments_file}")

