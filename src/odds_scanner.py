"""
odds_scanner.py - Scanner Quotidien des Cotes Tennis (Bet365 / The Odds API)

Fonctionnalités :
1. Récupère les matchs du jour et les cotes en direct (Bet365, Pinnacle, Unibet, etc.).
2. Supporte la vue combinée Hommes (ATP) & Femmes (WTA).
3. Résout automatiquement les noms des joueurs, les tournois, les surfaces et les formats (Best-of-3 / Best-of-5).
4. Utilise un cache intelligent en mémoire (TTL: 30 min) pour respecter le quota gratuit (500 req/mois).
5. Analyse instantanément chaque match avec le modèle XGBoost + Markov pour détecter les Value Bets.
6. Mode Démo intégré si aucune clé API n'est fournie.
"""

import os
import time
import math
import logging
from typing import Dict, List, Any, Optional, Tuple
from datetime import datetime, timezone, timedelta
import urllib.request
import urllib.parse
import json
from pathlib import Path
from src.tennisexplorer_scraper import fetch_tennisexplorer_matches

logger = logging.getLogger("tennis_predictor.odds_scanner")

# Cache global en mémoire et sur disque (persistant)
SCANNER_CACHE: Dict[str, Dict[str, Any]] = {}
CACHE_FILE = Path(__file__).resolve().parent.parent / "data" / "processed" / "scanner_cache.json"


def _get_smart_cache_ttl() -> int:
    """
    Retourne un TTL de cache adaptatif selon l'heure locale :
    - Nuit (23h → 08h) : 4h  → pas de matchs en cours, inutile de scraper souvent
    - Journée (08h → 23h) : 30 min → les matchs terminent vite, le cache doit s'actualiser
    """
    h = datetime.now().hour
    if 8 <= h < 23:
        return 30 * 60      # 30 minutes
    return 4 * 60 * 60     # 4 heures


def _load_disk_cache():
    global SCANNER_CACHE
    if not SCANNER_CACHE and CACHE_FILE.exists():
        try:
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                SCANNER_CACHE = json.load(f)
        except Exception:
            pass


def _save_disk_cache():
    try:
        CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(SCANNER_CACHE, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.warning(f"Impossible de sauvegarder le cache disque du scanner: {e}")

# Mappings des tournois connus vers surface / niveau / format
KNOWN_TOURNAMENT_PATTERNS = {
    "australian open": {"surface": "Hard", "level": "G", "best_of_men": 5, "indoor": 0},
    "roland garros": {"surface": "Clay", "level": "G", "best_of_men": 5, "indoor": 0},
    "french open": {"surface": "Clay", "level": "G", "best_of_men": 5, "indoor": 0},
    "wimbledon": {"surface": "Grass", "level": "G", "best_of_men": 5, "indoor": 0},
    "us open": {"surface": "Hard", "level": "G", "best_of_men": 5, "indoor": 0},
    "cincinnati": {"surface": "Hard", "level": "M", "best_of_men": 3, "indoor": 0},
    "indian wells": {"surface": "Hard", "level": "M", "best_of_men": 3, "indoor": 0},
    "miami": {"surface": "Hard", "level": "M", "best_of_men": 3, "indoor": 0},
    "monte carlo": {"surface": "Clay", "level": "M", "best_of_men": 3, "indoor": 0},
    "madrid": {"surface": "Clay", "level": "M", "best_of_men": 3, "indoor": 0},
    "rome": {"surface": "Clay", "level": "M", "best_of_men": 3, "indoor": 0},
    "canada": {"surface": "Hard", "level": "M", "best_of_men": 3, "indoor": 0},
    "montreal": {"surface": "Hard", "level": "M", "best_of_men": 3, "indoor": 0},
    "toronto": {"surface": "Hard", "level": "M", "best_of_men": 3, "indoor": 0},
    "shanghai": {"surface": "Hard", "level": "M", "best_of_men": 3, "indoor": 0},
    "paris": {"surface": "Hard", "level": "M", "best_of_men": 3, "indoor": 1},
    "dubai": {"surface": "Hard", "level": "A", "best_of_men": 3, "indoor": 0},
    "doha": {"surface": "Hard", "level": "A", "best_of_men": 3, "indoor": 0},
    "barcelona": {"surface": "Clay", "level": "A", "best_of_men": 3, "indoor": 0},
    "halle": {"surface": "Grass", "level": "A", "best_of_men": 3, "indoor": 0},
    "queens": {"surface": "Grass", "level": "A", "best_of_men": 3, "indoor": 0},
    "beijing": {"surface": "Hard", "level": "A", "best_of_men": 3, "indoor": 0},
    "tokyo": {"surface": "Hard", "level": "A", "best_of_men": 3, "indoor": 0},
    "vienna": {"surface": "Hard", "level": "A", "best_of_men": 3, "indoor": 1},
    "basel": {"surface": "Hard", "level": "A", "best_of_men": 3, "indoor": 1},
    "rotterdam": {"surface": "Hard", "level": "A", "best_of_men": 3, "indoor": 1},
    "winston-salem": {"surface": "Hard", "level": "A", "best_of_men": 3, "indoor": 0},
    "cleveland": {"surface": "Hard", "level": "A", "best_of_men": 3, "indoor": 0},
    "monterrey": {"surface": "Hard", "level": "A", "best_of_men": 3, "indoor": 0},
}


def resolve_tournament_context(sport_title: str, circuit: str = "atp") -> Dict[str, Any]:
    """Déduit automatiquement la surface, le niveau, le format et l'environnement d'après le nom du tournoi."""
    title_lower = sport_title.lower()
    
    context = {
        "tournament": sport_title,
        "surface": "Hard",
        "level": "A",
        "best_of": 3,
        "indoor": 0,
        "round": "R32"
    }

    for pattern, meta in KNOWN_TOURNAMENT_PATTERNS.items():
        if pattern in title_lower:
            context["surface"] = meta["surface"]
            context["level"] = meta["level"]
            context["indoor"] = meta["indoor"]
            if circuit.lower() == "atp":
                context["best_of"] = meta["best_of_men"]
            else:
                context["best_of"] = 3
            break

    if "clay" in title_lower or "terre" in title_lower:
        context["surface"] = "Clay"
    elif "grass" in title_lower or "gazon" in title_lower:
        context["surface"] = "Grass"
    elif "indoor" in title_lower:
        context["indoor"] = 1

    return context


def fetch_the_odds_api_sports(api_key: str) -> List[Dict[str, Any]]:
    """Récupère la liste de tous les tournois de tennis disponibles sur The Odds API (ATP + WTA)."""
    url = f"https://api.the-odds-api.com/v4/sports?apiKey={api_key}&all=true"
    req = urllib.request.Request(url, headers={"User-Agent": "TennisPredictor/1.0"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.loads(resp.read().decode("utf-8"))
        return [
            s for s in data
            if (s.get("key", "").startswith("tennis_") or s.get("group", "").lower() == "tennis")
            and not s.get("key", "").endswith("_winner")
            and not s.get("key", "").endswith("_outrights")
            and "championship_winner" not in s.get("key", "").lower()
        ]


def fetch_odds_for_sport(
    sport_key: str,
    api_key: str,
    bookmakers: Optional[str] = None
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Récupère les cotes du tournoi sur tous les bookmakers mondiaux sans restriction restrictive."""
    base_url = f"https://api.the-odds-api.com/v4/sports/{sport_key}/odds"
    params = {
        "apiKey": api_key,
        "regions": "eu,us,uk,au",
        "markets": "h2h,totals,spreads",
        "oddsFormat": "decimal"
    }
    if bookmakers:
        params["bookmakers"] = bookmakers
    query_string = urllib.parse.urlencode(params)
    url = f"{base_url}?{query_string}"

    req = urllib.request.Request(url, headers={"User-Agent": "TennisPredictor/1.0"})
    with urllib.request.urlopen(req, timeout=12) as resp:
        headers = dict(resp.getheaders())
        quota_info = {
            "requests_remaining": headers.get("x-requests-remaining") or headers.get("X-Requests-Remaining"),
            "requests_used": headers.get("x-requests-used") or headers.get("X-Requests-Used")
        }
        data = json.loads(resp.read().decode("utf-8"))
        return data, quota_info


def extract_match_odds(event: Dict[str, Any], target_bookmaker: str = "betclic") -> Dict[str, Any]:
    """
    Extrait les cotes du match en priorisant Betclic (FR), puis Winamax, Unibet, Bet365, Pinnacle.
    Si Betclic ne fournit que le vainqueur, complète intelligemment les totaux et handicaps
    depuis Unibet/Pinnacle pour avoir tous les marchés remplis.
    """
    home_name = event.get("home_team", "")
    away_name = event.get("away_team", "")
    bookmakers_list = event.get("bookmakers", [])

    if not bookmakers_list:
        return {}

    # Ordre de priorité strict :
    # 1. Bookmakers Français (ANJ) : Betclic > Winamax > Unibet FR > ParionsSport/FDJ > ZeBet > PMU > VBet > NetBet > Bwin FR
    # 2. Bookmakers Européens proches (référence cotes) : Bet365 > William Hill > Unibet EU > Betway > Pinnacle > Betfair > MarathonBet
    # 3. Bookmakers Internationaux / US : Bovada > BetRivers > DraftKings > FanDuel > MyBookie
    h2h_priority = [
        # --- 1. FRANCE (ANJ) ---
        "betclic_fr", "betclic",
        "winamax_fr", "winamax",
        "unibet_fr",
        "parionssport_fr", "parionssport", "fdj",
        "zebet_fr", "zebet",
        "pmu_fr", "pmu",
        "vbet_fr", "vbet",
        "netbet_fr", "netbet",
        "bwin_fr", "bwin",
        # --- 2. EUROPE / BENCHMARK PROCHE ---
        "bet365",
        "williamhill",
        "unibet_eu", "unibet",
        "betway",
        "pinnacle",
        "betfair", "betfair_ex_eu",
        "marathonbet",
        "1xbet",
        # --- 3. US / INTERNATIONAL ---
        "bovada", "betrivers", "draftkings", "fanduel", "mybookieag"
    ]

    extracted = {
        "bookmaker_name": "Betclic",
        "bookmaker_key": "betclic_fr",
        "odds1": None,
        "odds2": None,
        "total_line": None,
        "odds_over": None,
        "odds_under": None,
        "handicap_line": None,
        "odds_h1": None,
        "odds_h2": None,
    }

    # 1. Extraction du Vainqueur (H2H) selon la priorité stricte (FR d'abord, puis proches)
    found_bm_name = None
    found_bm_key = None

    for pref in h2h_priority:
        for bm in bookmakers_list:
            bm_key = bm.get("key", "").lower()
            if pref == bm_key or pref in bm_key:
                for market in bm.get("markets", []):
                    if market.get("key") == "h2h":
                        o1, o2 = None, None
                        for out in market.get("outcomes", []):
                            if out.get("name") == home_name:
                                o1 = float(out.get("price")) if out.get("price") else None
                            elif out.get("name") == away_name:
                                o2 = float(out.get("price")) if out.get("price") else None
                        if o1 and o2:
                            extracted["odds1"] = o1
                            extracted["odds2"] = o2
                            found_bm_name = bm.get("title", pref.replace("_fr", "").capitalize())
                            found_bm_key = bm_key
                            break
            if found_bm_name:
                break
        if found_bm_name:
            break

    # Si aucun des bookmakers de la liste prioritaire n'a été trouvé, prendre le premier bookmaker valide disponible
    if not (extracted["odds1"] and extracted["odds2"]):
        for bm in bookmakers_list:
            bm_key = bm.get("key", "").lower()
            for market in bm.get("markets", []):
                if market.get("key") == "h2h":
                    o1, o2 = None, None
                    for out in market.get("outcomes", []):
                        if out.get("name") == home_name:
                            o1 = float(out.get("price")) if out.get("price") else None
                        elif out.get("name") == away_name:
                            o2 = float(out.get("price")) if out.get("price") else None
                    if o1 and o2:
                        extracted["odds1"] = o1
                        extracted["odds2"] = o2
                        found_bm_name = bm.get("title", "Bookmaker Direct")
                        found_bm_key = bm_key
                        break
            if found_bm_name:
                break

    if found_bm_name:
        extracted["bookmaker_name"] = found_bm_name
        extracted["bookmaker_key"] = found_bm_key or "direct"
    elif bookmakers_list:
        extracted["bookmaker_name"] = bookmakers_list[0].get("title", "Betclic")

    # 2. Extraction du Total de Jeux (Totals)
    for bm in bookmakers_list:
        for market in bm.get("markets", []):
            if market.get("key") == "totals" and extracted["total_line"] is None:
                for out in market.get("outcomes", []):
                    name = out.get("name", "").lower()
                    point = out.get("point")
                    price = out.get("price")
                    if point is not None and extracted["total_line"] is None:
                        extracted["total_line"] = float(point)
                    if "over" in name and extracted["odds_over"] is None:
                        extracted["odds_over"] = float(price) if price else None
                    elif "under" in name and extracted["odds_under"] is None:
                        extracted["odds_under"] = float(price) if price else None
                if extracted["total_line"] is not None:
                    break
        if extracted["total_line"] is not None:
            break

    # 3. Extraction du Handicap de Jeux (Spreads)
    for bm in bookmakers_list:
        for market in bm.get("markets", []):
            if market.get("key") == "spreads" and extracted["handicap_line"] is None:
                for out in market.get("outcomes", []):
                    name = out.get("name", "")
                    point = out.get("point")
                    price = out.get("price")
                    if name == home_name and extracted["odds_h1"] is None:
                        if point is not None:
                            extracted["handicap_line"] = abs(float(point))
                        extracted["odds_h1"] = float(price) if price else None
                    elif name == away_name and extracted["odds_h2"] is None:
                        extracted["odds_h2"] = float(price) if price else None
                if extracted["handicap_line"] is not None:
                    break
        if extracted["handicap_line"] is not None:
            break

    return extracted


def get_demo_matches(circuit: str = "all") -> List[Dict[str, Any]]:
    """Génère des matchs de démonstration réalistes combinant ATP et WTA avec cotes Bet365."""
    atp_matches = [
        {
            "id": "demo_atp_1",
            "circuit": "atp",
            "sport_title": "ATP Cincinnati Masters",
            "tournament": "Cincinnati Masters",
            "surface": "Hard",
            "level": "M",
            "best_of": 3,
            "indoor": 0,
            "commence_time": "2026-08-22T15:30:00Z",
            "time_display": "17:30",
            "p1_raw": "Carlos Alcaraz",
            "p2_raw": "Jannik Sinner",
            "p1": "Carlos Alcaraz",
            "p2": "Jannik Sinner",
            "odds1": 2.10,
            "odds2": 1.75,
            "total_line": 22.5,
            "odds_over": 1.85,
            "odds_under": 1.95,
            "handicap_line": 1.5,
            "odds_h1": 1.90,
            "odds_h2": 1.90,
            "bookmaker": "Bet365"
        },
        {
            "id": "demo_atp_2",
            "circuit": "atp",
            "sport_title": "ATP Winston-Salem Open",
            "tournament": "Winston-Salem",
            "surface": "Hard",
            "level": "A",
            "best_of": 3,
            "indoor": 0,
            "commence_time": "2026-08-22T17:00:00Z",
            "time_display": "19:00",
            "p1_raw": "Arthur Fils",
            "p2_raw": "Flavio Cobolli",
            "p1": "Arthur Fils",
            "p2": "Flavio Cobolli",
            "odds1": 1.36,
            "odds2": 3.20,
            "total_line": 22.5,
            "odds_over": 1.77,
            "odds_under": 2.05,
            "handicap_line": 3.5,
            "odds_h1": 2.15,
            "odds_h2": 1.73,
            "bookmaker": "Bet365"
        },
        {
            "id": "demo_atp_3",
            "circuit": "atp",
            "sport_title": "ATP Cincinnati Masters",
            "tournament": "Cincinnati Masters",
            "surface": "Hard",
            "level": "M",
            "best_of": 3,
            "indoor": 0,
            "commence_time": "2026-08-22T19:30:00Z",
            "time_display": "21:30",
            "p1_raw": "Alexander Zverev",
            "p2_raw": "Daniil Medvedev",
            "p1": "Alexander Zverev",
            "p2": "Daniil Medvedev",
            "odds1": 1.90,
            "odds2": 1.90,
            "total_line": 23.5,
            "odds_over": 1.83,
            "odds_under": 1.97,
            "handicap_line": 1.5,
            "odds_h1": 1.85,
            "odds_h2": 1.95,
            "bookmaker": "Bet365"
        }
    ]

    wta_matches = [
        {
            "id": "demo_wta_1",
            "circuit": "wta",
            "sport_title": "WTA Cincinnati Open",
            "tournament": "Cincinnati Open",
            "surface": "Hard",
            "level": "M",
            "best_of": 3,
            "indoor": 0,
            "commence_time": "2026-08-22T16:00:00Z",
            "time_display": "18:00",
            "p1_raw": "Aryna Sabalenka",
            "p2_raw": "Iga Swiatek",
            "p1": "Aryna Sabalenka",
            "p2": "Iga Swiatek",
            "odds1": 2.05,
            "odds2": 1.80,
            "total_line": 21.5,
            "odds_over": 1.85,
            "odds_under": 1.95,
            "handicap_line": 2.5,
            "odds_h1": 1.85,
            "odds_h2": 1.95,
            "bookmaker": "Bet365"
        },
        {
            "id": "demo_wta_2",
            "circuit": "wta",
            "sport_title": "WTA Cleveland Open",
            "tournament": "Cleveland",
            "surface": "Hard",
            "level": "A",
            "best_of": 3,
            "indoor": 0,
            "commence_time": "2026-08-22T18:00:00Z",
            "time_display": "20:00",
            "p1_raw": "Coco Gauff",
            "p2_raw": "Elena Rybakina",
            "p1": "Coco Gauff",
            "p2": "Elena Rybakina",
            "odds1": 1.85,
            "odds2": 1.95,
            "total_line": 21.5,
            "odds_over": 1.80,
            "odds_under": 2.00,
            "handicap_line": 1.5,
            "odds_h1": 1.90,
            "odds_h2": 1.90,
            "bookmaker": "Bet365"
        }
    ]

    c_clean = circuit.lower()
    if c_clean == "atp":
        return atp_matches
    elif c_clean == "wta":
        return wta_matches
    else:
        # Combined
        return atp_matches + wta_matches


def generate_daily_parlays(analyzed_matches: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Génère automatiquement les combinés du jour optimisés par l'IA :
    1. 'max_odds' : Maximise la cote totale en empilant les sélections les plus fiables (haute probabilité de validation).
    2. 'safe' : Concentré des 2-3 plus gros favoris avec probabilité maximale.
    3. 'value' : Combiné des meilleurs Value Bets détectés (si existants).
    """
    if not analyzed_matches:
        return {"max_odds": None, "safe": None, "value": None, "has_parlays": False}

    valid_candidates = []
    vb_candidates = []

    for m in analyzed_matches:
        pred = m.get("prediction")
        if not pred:
            continue
        p1 = m.get("p1", "")
        p2 = m.get("p2", "")
        odds1 = m.get("odds1")
        odds2 = m.get("odds2")
        proba1 = pred.get("proba_p1")
        proba2 = pred.get("proba_p2")
        conf_score = pred.get("match_confidence", 75)
        tourn = m.get("sport_title") or m.get("tournament") or "Tournoi"
        surf = m.get("surface", "Hard")
        circuit = (m.get("circuit") or "atp").upper()
        time_disp = m.get("time_display", "Aujourd'hui")
        m_id = m.get("id") or f"{p1}_{p2}"

        # 1. Candidat favori match sec
        if proba1 is not None and proba2 is not None and odds1 and odds2:
            if proba1 >= proba2 and odds1 >= 1.12:
                fav_name = p1
                fav_odds = float(odds1)
                fav_proba = float(proba1)
                dog_name = p2
            elif proba2 > proba1 and odds2 >= 1.12:
                fav_name = p2
                fav_odds = float(odds2)
                fav_proba = float(proba2)
                dog_name = p1
            else:
                fav_name = None

            if fav_name:
                rel_score = (fav_proba * 0.70) + (min(conf_score, 100) / 100.0 * 0.30)
                reason = f"Proba IA {round(fav_proba * 100, 1)}% vs {dog_name}"
                if fav_proba >= 0.75:
                    reason = f"Favori solide ({round(fav_proba * 100, 1)}% proba IA)"
                elif fav_proba >= 0.65:
                    reason = f"Avantage net ({round(fav_proba * 100, 1)}% proba IA)"

                valid_candidates.append({
                    "match_id": m_id,
                    "p1": p1,
                    "p2": p2,
                    "match_display": f"{p1} vs {p2}",
                    "selection": fav_name,
                    "selection_label": f"Victoire {fav_name}",
                    "market": "Vainqueur Match",
                    "odds": round(fav_odds, 2),
                    "prob_pct": round(fav_proba * 100, 1),
                    "confidence_score": round(float(conf_score), 1),
                    "tournament": tourn,
                    "surface": surf,
                    "circuit": circuit,
                    "time_display": time_disp,
                    "reason": reason,
                    "reliability_score": rel_score,
                    "is_value_bet": bool(m.get("has_value_bet", False))
                })

        # 2. Candidat Value Bet
        if m.get("has_value_bet") and m.get("top_value_bet"):
            tvb = m["top_value_bet"]
            vb_prob = tvb.get("prob", 50.0) / 100.0 if tvb.get("prob", 50.0) > 1 else tvb.get("prob", 0.5)
            vb_odds = tvb.get("offered_odds", 2.0)
            if vb_odds and not math.isnan(float(vb_odds)):
                vb_candidates.append({
                    "match_id": m_id,
                    "p1": p1,
                    "p2": p2,
                    "match_display": f"{p1} vs {p2}",
                    "selection": tvb.get("selection", "Value Bet"),
                    "selection_label": tvb.get("selection", "Value Bet"),
                    "market": tvb.get("market", "Value Bet"),
                    "odds": round(float(vb_odds), 2),
                    "prob_pct": round(float(vb_prob * 100), 1),
                    "confidence_score": round(float(tvb.get("confidence", {}).get("score", conf_score)), 1),
                    "ev_pct": tvb.get("ev_pct", 0.0),
                    "edge_pct": tvb.get("edge_pct", 0.0),
                    "tournament": tourn,
                    "surface": surf,
                    "circuit": circuit,
                    "time_display": time_disp,
                    "reason": f"Value Bet (+{tvb.get('ev_pct', 0)}% EV)",
                    "reliability_score": (vb_prob * 0.5) + (min(conf_score, 100) / 100.0 * 0.3) + (min(tvb.get('ev_pct', 0), 20) / 100.0 * 0.2),
                    "is_value_bet": True
                })

    # Fonction helper pour assembler un ticket combiné
    def build_parlay_payload(picks: List[Dict[str, Any]], p_type: str, title: str, icon: str, badge: str, desc: str) -> Optional[Dict[str, Any]]:
        if len(picks) < 2:
            return None

        total_odds = 1.0
        cum_prob = 1.0
        conf_sum = 0.0

        for p in picks:
            total_odds *= p["odds"]
            cum_prob *= (p["prob_pct"] / 100.0)
            conf_sum += p["confidence_score"]

        total_odds = round(total_odds, 2)
        cum_prob_pct = round(cum_prob * 100.0, 1)
        fair_odds = round(1.0 / max(cum_prob, 0.001), 2)
        ev_pct = round((cum_prob * total_odds - 1.0) * 100.0, 1)
        avg_conf = round(conf_sum / len(picks), 1)

        conf_label = "Très haute confiance" if avg_conf >= 78 else ("Bonne confiance" if avg_conf >= 65 else "Confiance modérée")

        return {
            "type": p_type,
            "title": title,
            "icon": icon,
            "badge": badge,
            "description": desc,
            "selections": picks,
            "count": len(picks),
            "total_odds": total_odds,
            "combined_prob_pct": cum_prob_pct,
            "fair_odds": fair_odds,
            "ev_pct": ev_pct,
            "confidence_score": avg_conf,
            "confidence_label": conf_label
        }

    # 1. PARLAY MAX ODDS (Max Multiplicateur / Probable)
    sorted_candidates = sorted(valid_candidates, key=lambda x: x["reliability_score"], reverse=True)
    max_odds_picks = []
    seen_matches = set()
    for c in sorted_candidates:
        if c["match_id"] not in seen_matches:
            if len(max_odds_picks) < 2 or c["prob_pct"] >= 60.0:
                max_odds_picks.append(c)
                seen_matches.add(c["match_id"])
            if len(max_odds_picks) >= 6:
                break

    if len(max_odds_picks) < 2 and len(sorted_candidates) >= 2:
        max_odds_picks = sorted_candidates[:min(4, len(sorted_candidates))]

    max_odds_parlay = build_parlay_payload(
        picks=max_odds_picks,
        p_type="max_odds",
        title="Combiné IA Optimisé (Max Cote)",
        icon="🔥",
        badge="Multiplicateur Maximal",
        desc="Empilement optimisé des meilleures sélections de la journée pour maximiser le gain tout en restant très crédible."
    )

    # 2. PARLAY SAFE (2-3 Matchs Ultra-Sécurisés)
    safe_candidates = [c for c in valid_candidates if c.get("odds", 2.0) <= 1.55]
    if len(safe_candidates) < 2:
        safe_candidates = valid_candidates
    safe_sorted = sorted(safe_candidates, key=lambda x: x["prob_pct"], reverse=True)
    safe_picks = []
    seen_safe = set()
    for c in safe_sorted:
        if c["match_id"] not in seen_safe:
            safe_picks.append(c)
            seen_safe.add(c["match_id"])
            if len(safe_picks) >= 3:
                break

    safe_parlay = build_parlay_payload(
        picks=safe_picks,
        p_type="safe",
        title="Combiné Sécurité IA",
        icon="🛡️",
        badge="Haute Probabilité",
        desc="Les 2 à 3 favoris les plus indiscutables de la journée pour limiter au maximum la variance."
    )

    # 3. PARLAY VALUE (Meilleurs Value Bets)
    sorted_vbs = sorted(vb_candidates, key=lambda x: x.get("ev_pct", 0.0), reverse=True)
    value_picks = []
    seen_vbs = set()
    for c in sorted_vbs:
        if c["match_id"] not in seen_vbs:
            value_picks.append(c)
            seen_vbs.add(c["match_id"])
            if len(value_picks) >= 3:
                break

    value_parlay = build_parlay_payload(
        picks=value_picks,
        p_type="value",
        title="Combiné Value Bets EV+",
        icon="🚀",
        badge="Espérance Positive",
        desc="Combinaison des meilleures opportunités mathématiques où les cotes offertes sous-estiment les probabilités réelles."
    )

    has_any = bool(max_odds_parlay or safe_parlay or value_parlay)

    return {
        "has_parlays": has_any,
        "max_odds": max_odds_parlay,
        "safe": safe_parlay,
        "value": value_parlay
    }


def scan_daily_matches(
    circuit: str = "all",
    bookmaker: str = "bet365",
    api_key: Optional[str] = None,
    source: str = "tennisexplorer",
    force_refresh: bool = False,
    predict_func: Optional[Any] = None,
    get_resources_func: Optional[Any] = None,
    smart_resolve_func: Optional[Any] = None
) -> Dict[str, Any]:
    """
    Exécute le scan quotidien des matchs (ATP + WTA combinés ou séparés) :
    1. Vérifie le cache en mémoire (TTL: 30 min).
    2. Récupère les matchs via TennisExplorer ou The Odds API.
    3. Résout automatiquement les contextes et noms des joueurs.
    4. Exécute l'analyse prédictive ML + Markov pour chaque match.
    5. Génère automatiquement les Combinés du Jour (Max Cote, Safe, Value).
    6. Retourne les matchs et combinés enrichis.
    """
    circuit_key = circuit.lower()
    source_key = source.lower() if source else "tennisexplorer"
    cache_lookup_key = f"{circuit_key}_{source_key}_{bookmaker}"
    now_ts = time.time()

    # Si rafraîchissement forcé demandé, vider le cache immédiatement
    if force_refresh:
        SCANNER_CACHE.pop(cache_lookup_key, None)
        SCANNER_CACHE.pop(f"{circuit_key}_tennisexplorer_{bookmaker}", None)
        SCANNER_CACHE.pop(f"{circuit_key}_the_odds_api_{bookmaker}", None)
        SCANNER_CACHE.pop(f"all_tennisexplorer_{bookmaker}", None)
        SCANNER_CACHE.pop(f"all_the_odds_api_{bookmaker}", None)

    # 1. Vérification du cache en mémoire ou sur disque persistant
    _load_disk_cache()
    if not force_refresh and cache_lookup_key in SCANNER_CACHE:
        cached_entry = SCANNER_CACHE[cache_lookup_key]
        if (now_ts - cached_entry["timestamp"]) < _get_smart_cache_ttl():
            logger.info(f"Retour des matchs scannés depuis le cache ({source_key}, âge: {int(now_ts - cached_entry['timestamp'])}s)")
            cached_data = dict(cached_entry["data"])
            cached_data["cached"] = True
            cached_data["cache_age_seconds"] = int(now_ts - cached_entry["timestamp"])
            return cached_data

    raw_matches = []
    quota_info = {"requests_remaining": None, "requests_used": None}
    source_used = source_key
    is_demo_mode = False

    # 2. Récupération des données selon la source sélectionnée
    if source_key in ("tennisexplorer", "auto"):
        try:
            logger.info(f"Scan des matchs du jour via TennisExplorer (circuit={circuit_key})...")
            raw_matches = fetch_tennisexplorer_matches(circuit=circuit_key)
            if raw_matches:
                source_used = "tennisexplorer"
                quota_info = {"requests_remaining": "Illimité", "requests_used": "Scraping Direct"}
        except Exception as te_err:
            logger.warning(f"Erreur extraction TennisExplorer: {te_err}")
            raw_matches = []

    # Si la source demandée est The Odds API ou si TennisExplorer a échoué en mode auto
    if not raw_matches and source_key in ("the_odds_api", "auto"):
        resolved_api_key = api_key or os.getenv("ODDS_API_KEY") or os.getenv("THE_ODDS_API_KEY")
        is_demo_mode = not bool(resolved_api_key and len(resolved_api_key.strip()) > 8)

        if is_demo_mode:
            logger.info("Aucune clé The Odds API valide -> Mode Démo")
            raw_matches = get_demo_matches(circuit_key)
            source_used = "demo"
        else:
            try:
                source_used = "the_odds_api"
                active_sports = fetch_the_odds_api_sports(resolved_api_key)

                in_season = [s for s in active_sports if s.get("active", False)]
                out_of_season = [s for s in active_sports if not s.get("active", False)]
                sorted_sports = in_season + out_of_season

                if circuit_key == "atp":
                    target_sport_keys = [s["key"] for s in sorted_sports if "atp" in s["key"]][:8]
                elif circuit_key == "wta":
                    target_sport_keys = [s["key"] for s in sorted_sports if ("wta" in s["key"] or "women" in s.get("title", "").lower())][:8]
                else:
                    atp_keys = [s["key"] for s in sorted_sports if "atp" in s["key"]]
                    wta_keys = [s["key"] for s in sorted_sports if ("wta" in s["key"] or "women" in s.get("title", "").lower())]
                    other_keys = [s["key"] for s in sorted_sports if s["key"] not in atp_keys and s["key"] not in wta_keys]
                    target_sport_keys = atp_keys[:5] + wta_keys[:5] + other_keys[:2]

                for s_key in target_sport_keys:
                    m_circuit = "wta" if ("wta" in s_key or "women" in s_key) else "atp"
                    try:
                        events, q_info = fetch_odds_for_sport(s_key, resolved_api_key)
                        if q_info.get("requests_remaining"):
                            quota_info = q_info
                    except Exception as sport_err:
                        logger.warning(f"Impossible de charger les cotes pour {s_key}: {sport_err}")
                        continue

                    for ev in events:
                        odds = extract_match_odds(ev, target_bookmaker=bookmaker)
                        if not odds.get("odds1") or not odds.get("odds2"):
                            continue

                        commence_raw = ev.get("commence_time", "")
                        time_display = "Aujourd'hui"
                        if commence_raw:
                            try:
                                dt_utc = datetime.fromisoformat(commence_raw.replace("Z", "+00:00"))
                                dt_local = dt_utc.astimezone()
                                now_local = datetime.now().astimezone()
                                today_start = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
                                tomorrow_night_cutoff = today_start + timedelta(days=1, hours=9)  # Max 09h00 le lendemain matin

                                # Ignorer les matchs postérieurs à la session de nuit de la journée
                                if dt_local > tomorrow_night_cutoff:
                                    continue
                                if dt_local < (today_start - timedelta(hours=3)):
                                    continue

                                if dt_local.date() == now_local.date():
                                    time_display = dt_local.strftime("%H:%M")
                                elif dt_local.date() > now_local.date() and dt_local.hour < 9:
                                    time_display = f"Nuit {dt_local.strftime('%H:%M')}"
                                else:
                                    time_display = dt_local.strftime("%H:%M")
                            except Exception:
                                pass

                        sport_title = ev.get("sport_title", "Tournoi Tennis")
                        context = resolve_tournament_context(sport_title, circuit=m_circuit)

                        raw_matches.append({
                            "id": ev.get("id", f"match_{len(raw_matches)}"),
                            "circuit": m_circuit,
                            "sport_title": sport_title,
                            "tournament": context["tournament"],
                            "surface": context["surface"],
                            "level": context["level"],
                            "best_of": context["best_of"],
                            "indoor": context["indoor"],
                            "commence_time": commence_raw,
                            "time_display": time_display,
                            "p1_raw": ev.get("home_team", ""),
                            "p2_raw": ev.get("away_team", ""),
                            "p1": ev.get("home_team", ""),
                            "p2": ev.get("away_team", ""),
                            "odds1": odds.get("odds1"),
                            "odds2": odds.get("odds2"),
                            "total_line": odds.get("total_line"),
                            "odds_over": odds.get("odds_over"),
                            "odds_under": odds.get("odds_under"),
                            "handicap_line": odds.get("handicap_line"),
                            "odds_h1": odds.get("odds_h1"),
                            "odds_h2": odds.get("odds_h2"),
                            "bookmaker": odds.get("bookmaker_name", bookmaker.capitalize())
                        })
            except Exception as e:
                logger.error(f"Erreur appel The Odds API: {e} -> Fallback Démo")
                raw_matches = get_demo_matches(circuit_key)
                is_demo_mode = True
                source_used = "demo"

    if not raw_matches:
        raw_matches = get_demo_matches(circuit_key)
        is_demo_mode = True
        source_used = "demo"

    # Filtrer strictement les tournois ATP et WTA (exclure Challengers, ITF, Futures et matchs terminés)
    filtered_raw_matches = []
    for m in raw_matches:
        if m.get("is_finished"):
            continue
        t_name = (m.get("sport_title") or m.get("tournament") or "").lower()
        if "challenger" in t_name or "itf" in t_name or "futures" in t_name or "utr" in t_name:
            continue
        filtered_raw_matches.append(m)
    raw_matches = filtered_raw_matches

    # 3. Résolution des noms et analyse prédictive des matchs isolée par circuit
    import gc

    analyzed_matches = []
    total_vbs_found = 0
    atp_count = 0
    wta_count = 0

    # Séparer les matchs par circuit
    matches_by_circuit = {"atp": [], "wta": []}
    for m in raw_matches:
        c = m.get("circuit", "atp").lower()
        if c not in matches_by_circuit:
            matches_by_circuit[c] = []
        matches_by_circuit[c].append(m)

    for current_circuit, circuit_match_list in matches_by_circuit.items():
        if not circuit_match_list:
            continue

        res = get_resources_func(current_circuit) if get_resources_func else {}
        known_players = res.get("players", [])
        player_state = res.get("state", {})

        for m in circuit_match_list:
            if current_circuit == "atp":
                atp_count += 1
            else:
                wta_count += 1

            p1_resolved = m["p1_raw"]
            p2_resolved = m["p2_raw"]

            if smart_resolve_func and known_players and player_state:
                p1_resolved = smart_resolve_func(m["p1_raw"], known_players, player_state)
                p2_resolved = smart_resolve_func(m["p2_raw"], known_players, player_state)

            if p1_resolved.lower() == p2_resolved.lower():
                continue

            m_item = {
                "id": m.get("id"),
                "circuit": current_circuit,
                "sport_title": m.get("sport_title"),
                "tournament": m.get("tournament"),
                "surface": m.get("surface"),
                "level": m.get("level"),
                "best_of": m.get("best_of"),
                "indoor": m.get("indoor"),
                "commence_time": m.get("commence_time"),
                "time_display": m.get("time_display"),
                "p1_raw": m["p1_raw"],
                "p2_raw": m["p2_raw"],
                "p1": p1_resolved,
                "p2": p2_resolved,
                "bookmaker": m.get("bookmaker", "Bet365"),
                "odds1": m.get("odds1"),
                "odds2": m.get("odds2"),
                "total_line": m.get("total_line"),
                "odds_over": m.get("odds_over"),
                "odds_under": m.get("odds_under"),
                "handicap_line": m.get("handicap_line"),
                "odds_h1": m.get("odds_h1"),
                "odds_h2": m.get("odds_h2"),
                "prediction": None,
                "full_report": None,
                "has_value_bet": False,
                "top_value_bet": None,
                "all_value_bets": []
            }

            # Exécuter la prédiction complète
            if predict_func:
                try:
                    class DummyReq:
                        def __init__(self, **kwargs):
                            for k, v in kwargs.items():
                                setattr(self, k, v)

                    req_obj = DummyReq(
                        circuit=current_circuit,
                        p1=p1_resolved,
                        p2=p2_resolved,
                        surface=m.get("surface", "Hard"),
                        tournament=m.get("tournament", "Tournoi"),
                        level=m.get("level", "A"),
                        round="R32",
                        best_of=m.get("best_of", 3),
                        indoor=m.get("indoor", 0),
                        date=None,
                        odds1=m.get("odds1"),
                        odds2=m.get("odds2"),
                        total_line=None,
                        odds_over=None,
                        odds_under=None,
                        handicap_line=None,
                        odds_h1=None,
                        odds_h2=None,
                        odds_set1_p1=None,
                        odds_set1_p2=None,
                        odds_sets_over25=None,
                        odds_sets_under25=None,
                        odds_tb_yes=None,
                        odds_tb_no=None
                    )

                    pred_res = predict_func(req_obj)
                    m_item["prediction"] = {
                        "proba_p1": pred_res.get("proba_p1"),
                        "proba_p2": pred_res.get("proba_p2"),
                        "fair_odds_p1": pred_res.get("fair_odds_p1"),
                        "fair_odds_p2": pred_res.get("fair_odds_p2"),
                        "match_confidence": pred_res.get("confidence", {}).get("score", 75),
                        "confidence_level": pred_res.get("confidence", {}).get("level", "Moyenne"),
                        "individual_probas": pred_res.get("individual_probas"),
                        "shap_explanation": pred_res.get("shap_explanation")
                    }
                    m_item["full_report"] = pred_res

                    all_vbs = pred_res.get("recommended_value_bets") or pred_res.get("all_value_bets") or []
                    valid_vbs = [vb for vb in all_vbs if vb.get("is_value_bet")]

                    if not valid_vbs:
                        for k in ["vb_p1", "vb_p2", "vb_over", "vb_under", "vb_h1", "vb_h2", "vb_tb_yes", "vb_tb_no"]:
                            vb_item = pred_res.get(k)
                            if vb_item and vb_item.get("is_value_bet"):
                                valid_vbs.append(vb_item)
                                break
                        if not valid_vbs:
                            for vb in pred_res.get("all_value_bets", []):
                                if vb.get("is_value_bet"):
                                    valid_vbs.append(vb)

                    m_item["all_value_bets"] = valid_vbs
                    if valid_vbs:
                        m_item["has_value_bet"] = True
                        m_item["top_value_bet"] = valid_vbs[0]
                        total_vbs_found += 1
                    else:
                        m_item["has_value_bet"] = False
                        m_item["top_value_bet"] = None
                except Exception as pe:
                    logger.warning(f"Erreur prédiction pour {p1_resolved} vs {p2_resolved}: {pe}")

            analyzed_matches.append(m_item)

        del res
        del known_players
        del player_state
        gc.collect()

    # Trier chronologiquement l'ensemble des matchs analysés
    analyzed_matches.sort(key=lambda x: (x.get("commence_time") or "9999-99-99T99:99:00", x.get("time_display") or "99:99"))

    now_utc = datetime.now(timezone.utc)
    now_datetime = datetime.now()
    last_update_str = f"{now_datetime.strftime('%H:%M')}"

    # 4. Génération intelligente des Combinés du Jour
    daily_parlays = generate_daily_parlays(analyzed_matches)

    response_payload = {
        "success": True,
        "circuit": circuit_key,
        "source": source_used,
        "bookmaker": bookmaker,
        "is_demo_mode": is_demo_mode,
        "cached": False,
        "cache_ttl_minutes": int(_get_smart_cache_ttl() / 60),
        "timestamp_epoch": now_ts,
        "timestamp_iso": now_utc.isoformat(),
        "last_update": last_update_str,
        "total_matches": len(analyzed_matches),
        "atp_count": atp_count,
        "wta_count": wta_count,
        "value_bets_count": total_vbs_found,
        "quota_info": quota_info,
        "matches": analyzed_matches,
        "daily_parlays": daily_parlays
    }

    SCANNER_CACHE[cache_lookup_key] = {
        "timestamp": now_ts,
        "data": response_payload,
        "bookmaker": bookmaker
    }
    _save_disk_cache()

    return response_payload
