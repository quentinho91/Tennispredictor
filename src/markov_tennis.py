"""
markov_tennis.py — Modèle Markovien Point-par-Point pour le Tennis
(Formulation analytique de Barnett & Clarke 2005 / Newton & Aslam 2006).

Ce module fournit les calculs exacts et optimisés de :
1. P(Hold) : Probabilité de gain d'un jeu de service à partir de P(Point service)
2. P(Tiebreak) : Probabilité de gain d'un tie-break à 7 points (avec alternance de service)
3. P(Set) : Probabilité de gain d'un set et distribution exacte de tous les scores (6-0 ... 7-6)
4. P(Match) : Probabilité en Best-of-3 et Best-of-5, scores en sets (2-0, 2-1, etc.)
5. Espérance du nombre de jeux totaux et probabilités de handicap de jeux
6. Conversion Serve/Return Elo -> Probabilités de points par joueur et par surface
"""

import math
from typing import Dict, Tuple, Any, Optional
import numpy as np


# Taux moyen de points gagnés au service par surface sur le circuit ATP
SURFACE_BASE_SPW = {
    "Hard": 0.638,
    "Clay": 0.615,
    "Grass": 0.665,
    "Carpet": 0.655,
    "Default": 0.635
}

# Taux WTA moyen
SURFACE_BASE_SPW_WTA = {
    "Hard": 0.560,
    "Clay": 0.545,
    "Grass": 0.580,
    "Carpet": 0.570,
    "Default": 0.560
}


def p_game(p: float) -> float:
    """
    Formule analytique fermée de Barnett & Clarke (2005).
    Calcule la probabilité exacte qu'un serveur gagne son jeu de service (Hold)
    donnant la probabilité p de gagner un point sur son service.
    
    g(p) = p^4 * (-8*p^3 + 28*p^2 - 34*p + 15) / (p^2 + (1-p)^2)
    """
    if p <= 0.0:
        return 0.0
    if p >= 1.0:
        return 1.0
    p = float(np.clip(p, 1e-7, 1.0 - 1e-7))
    q = 1.0 - p
    denom = p * p + q * q
    if denom <= 0:
        return 0.5
    p4 = p ** 4
    num = p4 * (-8.0 * (p ** 3) + 28.0 * (p ** 2) - 34.0 * p + 15.0)
    return float(np.clip(num / denom, 0.0, 1.0))


def p_tiebreak(p_a: float, p_b: float, a_serves_first: bool = True) -> float:
    """
    Calcule la probabilité exacte que le joueur A gagne un tie-break à 7 points (écart de 2).
    Prend en compte l'alternance stricte des services :
    - Point 1 : Serveur 1 (A si a_serves_first=True, sinon B)
    - Points 2-3 : Serveur 2
    - Points 4-5 : Serveur 1
    - Points 6-7 : Serveur 2, etc.
    
    Args:
        p_a: probabilité que A gagne le point sur son service
        p_b: probabilité que B gagne le point sur son service (donc A gagne à 1 - p_b au retour)
        a_serves_first: True si A sert le 1er point du tiebreak
    """
    p_a = float(np.clip(p_a, 0.001, 0.999))
    p_b = float(np.clip(p_b, 0.001, 0.999))
    q_a = 1.0 - p_a
    q_b = 1.0 - p_b  # probabilité que A gagne quand B sert

    def get_server_at_point(n: int) -> str:
        # n est l'indice du point joué (0 pour le 1er point)
        if n == 0:
            return "A" if a_serves_first else "B"
        m = (n - 1) % 4
        if m in (0, 1):
            return "B" if a_serves_first else "A"
        else:
            return "A" if a_serves_first else "B"

    # DP sur la grille (i, j) jusqu'à (6, 6)
    dp = np.zeros((8, 8), dtype=float)
    dp[0, 0] = 1.0

    p_a_wins_tb = 0.0

    for i in range(7):
        for j in range(7):
            prob_current = dp[i, j]
            if prob_current <= 0:
                continue

            n = i + j
            server = get_server_at_point(n)
            p_win_pt = p_a if server == "A" else q_b

            # A gagne le point
            if i + 1 == 7 and j < 6:
                p_a_wins_tb += prob_current * p_win_pt
            elif i + 1 <= 6:
                dp[i + 1, j] += prob_current * p_win_pt
            elif i + 1 == 7 and j == 6:
                dp[7, 6] += prob_current * p_win_pt

            # B gagne le point
            if j + 1 == 7 and i < 6:
                pass  # B gagne directement
            elif j + 1 <= 6:
                dp[i, j + 1] += prob_current * (1.0 - p_win_pt)
            elif j + 1 == 7 and i == 6:
                dp[6, 7] += prob_current * (1.0 - p_win_pt)

    # Résolution à partir de (6, 6) :
    # À partir de (6,6), sur chaque paire de 2 points consécutifs, 1 est servi par A et 1 par B.
    # P(A gagne 2 pts) = p_a * q_b
    # P(B gagne 2 pts) = q_a * p_b
    # P(retour à deuce) = p_a * p_b + q_a * q_b
    p_reach_6_6 = dp[6, 6]
    p_win_from_deuce = (p_a * q_b) / (p_a * q_b + q_a * p_b + 1e-12)
    p_a_wins_tb += p_reach_6_6 * p_win_from_deuce

    return float(np.clip(p_a_wins_tb, 0.0, 1.0))


def p_set_exact(p_a: float, p_b: float, a_serves_first: bool = True) -> Tuple[float, Dict[str, float], float, float]:
    """
    Calcule la probabilité exacte qu'un joueur A gagne un set, ainsi que la
    distribution complète des scores (6-0, 6-1, ... 7-6, 0-6 ... 6-7),
    l'espérance du nombre de jeux dans le set et l'espérance du différentiel de jeux (A - B).

    Returns:
        (p_set_a, score_distribution, expected_games, expected_game_diff)
    """
    g_a = p_game(p_a)
    g_b = p_game(p_b)
    q_b = 1.0 - g_b  # proba que A breake B

    # DP sur les jeux (i, j) où i = jeux de A, j = jeux de B
    dp = np.zeros((8, 8), dtype=float)
    dp[0, 0] = 1.0

    scores = {}
    p_set_a = 0.0

    for i in range(7):
        for j in range(7):
            prob = dp[i, j]
            if prob <= 0:
                continue

            game_num = i + j
            # Qui sert dans ce jeu ?
            server_is_a = (game_num % 2 == 0) if a_serves_first else (game_num % 2 == 1)
            p_win_game = g_a if server_is_a else q_b

            # Victoires de jeu A
            if i + 1 == 6 and j <= 4:
                # Set gagné 6-j
                score_str = f"6-{j}"
                scores[score_str] = prob * p_win_game
                p_set_a += prob * p_win_game
            elif i + 1 <= 6:
                dp[i + 1, j] += prob * p_win_game

            # Victoires de jeu B
            if j + 1 == 6 and i <= 4:
                # Set perdu i-6
                score_str = f"{i}-6"
                scores[score_str] = prob * (1.0 - p_win_game)
            elif j + 1 <= 6:
                dp[i, j + 1] += prob * (1.0 - p_win_game)

    # Gestion de 5-5 -> 6-5 ou 5-6
    p_5_5 = dp[5, 5]
    if p_5_5 > 0:
        serv_11_is_a = (10 % 2 == 0) if a_serves_first else (10 % 2 == 1)
        p_win_11 = g_a if serv_11_is_a else q_b

        p_6_5 = p_5_5 * p_win_11
        p_5_6 = p_5_5 * (1.0 - p_win_11)

        # Jeu 12 depuis 6-5
        serv_12_is_a = not serv_11_is_a
        p_win_12_from_6_5 = g_a if serv_12_is_a else q_b

        # 7-5 pour A
        p_7_5 = p_6_5 * p_win_12_from_6_5
        scores["7-5"] = p_7_5
        p_set_a += p_7_5

        # 6-6 depuis 6-5 (B gagne le jeu 12)
        p_6_6_from_6_5 = p_6_5 * (1.0 - p_win_12_from_6_5)

        # Jeu 12 depuis 5-6
        p_win_12_from_5_6 = g_a if serv_12_is_a else q_b
        # 5-7 pour B
        p_5_7 = p_5_6 * (1.0 - p_win_12_from_5_6)
        scores["5-7"] = p_5_7

        # 6-6 depuis 5-6 (A gagne le jeu 12)
        p_6_6_from_5_6 = p_5_6 * p_win_12_from_5_6

        # Total 6-6 menant au tiebreak
        p_6_6 = p_6_6_from_6_5 + p_6_6_from_5_6
        if p_6_6 > 0:
            tb_a_first = a_serves_first
            p_tb_a = p_tiebreak(p_a, p_b, a_serves_first=tb_a_first)

            p_7_6 = p_6_6 * p_tb_a
            p_6_7 = p_6_6 * (1.0 - p_tb_a)

            scores["7-6"] = p_7_6
            scores["6-7"] = p_6_7
            p_set_a += p_7_6

    # Normalisation de sécurité
    total_p = sum(scores.values())
    if total_p > 0 and abs(total_p - 1.0) > 1e-6:
        for k in scores:
            scores[k] /= total_p
        p_set_a = sum(scores[k] for k in scores if int(k.split("-")[0]) > int(k.split("-")[1]))

    # Espérance du total de jeux et du différentiel de jeux dans le set
    exp_games = 0.0
    exp_diff = 0.0
    for score_str, p_sc in scores.items():
        g_a_sc, g_b_sc = map(int, score_str.split("-"))
        exp_games += (g_a_sc + g_b_sc) * p_sc
        exp_diff += (g_a_sc - g_b_sc) * p_sc

    return float(p_set_a), scores, float(exp_games), float(exp_diff)


def p_set(p_a: float, p_b: float) -> Tuple[float, Dict[str, float], float, float]:
    """
    Probabilité non conditionnelle de remporter un set (moyenne équitable 50/50
    sur qui sert en premier).
    """
    p_set1, scores1, exp_g1, exp_d1 = p_set_exact(p_a, p_b, a_serves_first=True)
    p_set2, scores2, exp_g2, exp_d2 = p_set_exact(p_a, p_b, a_serves_first=False)

    p_set_avg = 0.5 * (p_set1 + p_set2)
    exp_g_avg = 0.5 * (exp_g1 + exp_g2)
    exp_d_avg = 0.5 * (exp_d1 + exp_d2)

    all_keys = set(scores1.keys()) | set(scores2.keys())
    scores_avg = {k: 0.5 * (scores1.get(k, 0.0) + scores2.get(k, 0.0)) for k in all_keys}

    return float(p_set_avg), scores_avg, float(exp_g_avg), float(exp_d_avg)


def p_match(p_a: float, p_b: float, best_of: int = 3) -> Dict[str, Any]:
    """
    Calcul complet du match (Best of 3 ou Best of 5) via la chaîne de Markov.

    Retourne :
    - proba_a : P(A gagne le match)
    - proba_b : P(B gagne le match)
    - set_proba_a : P(A gagne un set)
    - hold_proba_a : P(A tient son service)
    - hold_proba_b : P(B tient son service)
    - set_scores : distribution exacte des scores en sets (2-0, 2-1, etc.)
    - expected_total_games : espérance du nombre de jeux total
    - expected_game_diff : espérance du différentiel de jeux (A - B)
    - set_game_distribution : distribution moyenne des scores par set (6-0 ... 7-6)
    """
    p_s, set_scores_dist, exp_games_per_set, exp_diff_per_set = p_set(p_a, p_b)
    p_s = float(np.clip(p_s, 0.0001, 0.9999))
    q_s = 1.0 - p_s

    hold_a = p_game(p_a)
    hold_b = p_game(p_b)

    if best_of == 3:
        # Best of 3
        p_2_0 = p_s ** 2
        p_2_1 = 2.0 * (p_s ** 2) * q_s
        p_1_2 = 2.0 * p_s * (q_s ** 2)
        p_0_2 = q_s ** 2

        p_match_a = p_2_0 + p_2_1
        p_match_b = p_1_2 + p_0_2

        set_scores = {
            "2-0": round(p_2_0, 4),
            "2-1": round(p_2_1, 4),
            "1-2": round(p_1_2, 4),
            "0-2": round(p_0_2, 4)
        }

        # Nombre moyen de sets joués : 2 * (p_2_0 + p_0_2) + 3 * (p_2_1 + p_1_2)
        expected_sets = 2.0 * (p_2_0 + p_0_2) + 3.0 * (p_2_1 + p_1_2)
    else:
        # Best of 5 (Grand Chelem)
        p_3_0 = p_s ** 3
        p_3_1 = 3.0 * (p_s ** 3) * q_s
        p_3_2 = 6.0 * (p_s ** 3) * (q_s ** 2)
        p_2_3 = 6.0 * (p_s ** 2) * (q_s ** 3)
        p_1_3 = 3.0 * p_s * (q_s ** 3)
        p_0_3 = q_s ** 3

        p_match_a = p_3_0 + p_3_1 + p_3_2
        p_match_b = p_2_3 + p_1_3 + p_0_3

        set_scores = {
            "3-0": round(p_3_0, 4),
            "3-1": round(p_3_1, 4),
            "3-2": round(p_3_2, 4),
            "2-3": round(p_2_3, 4),
            "1-3": round(p_1_3, 4),
            "0-3": round(p_0_3, 4)
        }

        expected_sets = (3.0 * (p_3_0 + p_0_3) +
                         4.0 * (p_3_1 + p_1_3) +
                         5.0 * (p_3_2 + p_2_3))

    expected_total_games = expected_sets * exp_games_per_set
    expected_game_diff = expected_sets * exp_diff_per_set

    # Convolutions exactes de la distribution des jeux et différentiels de jeux sur tout le match
    target_wins = 2 if best_of == 3 else 3
    states = {(0, 0): {(0, 0): 1.0}}
    finished_dist = {}
    max_steps = 3 if best_of == 3 else 5

    for _ in range(max_steps):
        next_states = {}
        for (sa, sb), dist in states.items():
            if sa == target_wins or sb == target_wins:
                for (tg, diff), prob in dist.items():
                    finished_dist[(tg, diff)] = finished_dist.get((tg, diff), 0.0) + prob
                continue
            for score_str, p_set_score in set_scores_dist.items():
                ga, gb = map(int, score_str.split("-"))
                w_a = ga > gb
                nsa = sa + 1 if w_a else sa
                nsb = sb if w_a else sb + 1
                key = (nsa, nsb)
                if key not in next_states:
                    next_states[key] = {}
                for (tg, diff), prob in dist.items():
                    new_tg = tg + ga + gb
                    new_diff = diff + ga - gb
                    new_prob = prob * p_set_score
                    next_states[key][(new_tg, new_diff)] = next_states[key].get((new_tg, new_diff), 0.0) + new_prob
        states = next_states

    for (sa, sb), dist in states.items():
        if sa == target_wins or sb == target_wins:
            for (tg, diff), prob in dist.items():
                finished_dist[(tg, diff)] = finished_dist.get((tg, diff), 0.0) + prob

    return {
        "proba_a": round(float(p_match_a), 4),
        "proba_b": round(float(p_match_b), 4),
        "set_proba_a": round(float(p_s), 4),
        "set_proba_b": round(float(q_s), 4),
        "hold_proba_a": round(float(hold_a), 4),
        "hold_proba_b": round(float(hold_b), 4),
        "set_scores": set_scores,
        "expected_sets": round(float(expected_sets), 2),
        "expected_total_games": round(float(expected_total_games), 1),
        "expected_game_diff": round(float(expected_game_diff), 2),
        "set_game_distribution": {k: round(v, 4) for k, v in set_scores_dist.items()},
        "match_games_distribution": finished_dist
    }


def estimate_point_probabilities(
    serve_elo_a: float,
    return_elo_b: float,
    serve_elo_b: float,
    return_elo_a: float,
    surface: str = "Hard",
    circuit: str = "atp"
) -> Tuple[float, float]:
    """
    Convertit les ratings Serve Elo et Return Elo en probabilités de points p_a et p_b.

    Formulation logistique :
    logit(p_A) = logit(base_spw) + (Serve_A - Return_B) / SCALE

    Avec un scaling calibré pour que 100 points d'écart d'Elo correspondent à ~3.8% de SPW.
    """
    surface_clean = surface.capitalize() if isinstance(surface, str) else "Hard"
    base_dict = SURFACE_BASE_SPW_WTA if "wta" in circuit.lower() else SURFACE_BASE_SPW
    base_spw = base_dict.get(surface_clean, base_dict["Default"])

    logit_base = math.log(base_spw / (1.0 - base_spw))

    elo_diff_a = (serve_elo_a - return_elo_b)
    elo_diff_b = (serve_elo_b - return_elo_a)

    scale = 1150.0

    logit_a = logit_base + (elo_diff_a / scale)
    logit_b = logit_base + (elo_diff_b / scale)

    p_a = 1.0 / (1.0 + math.exp(-logit_a))
    p_b = 1.0 / (1.0 + math.exp(-logit_b))

    p_a = float(np.clip(p_a, 0.42, 0.85))
    p_b = float(np.clip(p_b, 0.42, 0.85))

    return p_a, p_b


def price_game_handicap(
    expected_diff: float,
    line: float,
    sigma: float = 4.0,
    match_games_dist: Optional[Dict[Tuple[int, int], float]] = None
) -> Tuple[float, float]:
    """
    Évalue la probabilité de couvrir un handicap de jeux H : P(Games_A - Games_B > H).
    Utilise la distribution exacte Markov par convolution (ou distribution Normale de secours).
    """
    if match_games_dist:
        p_cover_a = sum(p for (tg, diff), p in match_games_dist.items() if diff > line)
    else:
        z = (expected_diff - line) / (sigma * math.sqrt(2.0))
        p_cover_a = 0.5 * (1.0 + math.erf(z))

    p_cover_a = float(np.clip(p_cover_a, 0.01, 0.99))
    p_cover_b = 1.0 - p_cover_a
    return round(p_cover_a, 4), round(p_cover_b, 4)


def price_total_games(
    expected_total: float,
    line: float,
    sigma: float = 3.8,
    match_games_dist: Optional[Dict[Tuple[int, int], float]] = None
) -> Tuple[float, float]:
    """
    Évalue la probabilité Over / Under sur le total de jeux : P(Total > T).
    Utilise la distribution bimodale exacte Markov par convolution (ou distribution Normale de secours).
    """
    if match_games_dist:
        p_over = sum(p for (tg, diff), p in match_games_dist.items() if tg > line)
    else:
        z = (expected_total - line) / (sigma * math.sqrt(2.0))
        p_over = 0.5 * (1.0 + math.erf(z))

    p_over = float(np.clip(p_over, 0.01, 0.99))
    p_under = 1.0 - p_over
    return round(p_over, 4), round(p_under, 4)
