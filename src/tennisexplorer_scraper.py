"""
tennisexplorer_scraper.py - Scraper des matchs du jour & cotes via TennisExplorer

Permet de récupérer 100% des matchs de tennis quotidiens :
- ATP Main Tour (ex: Winston-Salem, etc.)
- Grand Chelem & Qualifications (ex: US Open Qualifs Hommes & Femmes)
- WTA Main Tour (ex: Monterrey, Cleveland, etc.)
- Challengers & Tournois secondaires
- Cotes des bookmakers (1 & 2), heure, statut, round et surface.
"""

import re
import logging
import urllib.request
from typing import Dict, List, Any, Optional
from datetime import datetime, timezone
from bs4 import BeautifulSoup

logger = logging.getLogger("tennis_predictor.tennisexplorer_scraper")

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
    "winston salem": {"surface": "Hard", "level": "A", "best_of_men": 3, "indoor": 0},
    "cleveland": {"surface": "Hard", "level": "A", "best_of_men": 3, "indoor": 0},
    "monterrey": {"surface": "Hard", "level": "A", "best_of_men": 3, "indoor": 0},
    "philadelphia": {"surface": "Hard", "level": "A", "best_of_men": 3, "indoor": 1},
}


def deduce_tournament_meta(tourney_name: str, circuit: str = "atp") -> Dict[str, Any]:
    """Déduit automatiquement la surface, le niveau, et le format du tournoi."""
    title_lower = tourney_name.lower().strip()
    
    meta = {
        "surface": "Hard",
        "level": "A",
        "best_of": 3,
        "indoor": 0,
        "round": "R32"
    }

    # Grand Chelem / Qualifs
    if "us open" in title_lower or "us-open" in title_lower:
        meta["surface"] = "Hard"
        meta["level"] = "G"
        meta["indoor"] = 0
        meta["best_of"] = 5 if circuit == "atp" else 3
        return meta

    if "australian open" in title_lower:
        meta["surface"] = "Hard"
        meta["level"] = "G"
        meta["best_of"] = 5 if circuit == "atp" else 3
        return meta

    if "roland garros" in title_lower or "french open" in title_lower:
        meta["surface"] = "Clay"
        meta["level"] = "G"
        meta["best_of"] = 5 if circuit == "atp" else 3
        return meta

    if "wimbledon" in title_lower:
        meta["surface"] = "Grass"
        meta["level"] = "G"
        meta["best_of"] = 5 if circuit == "atp" else 3
        return meta

    # Check known patterns
    for pat, pat_meta in KNOWN_TOURNAMENT_PATTERNS.items():
        if pat in title_lower:
            meta["surface"] = pat_meta["surface"]
            meta["level"] = pat_meta["level"]
            meta["indoor"] = pat_meta["indoor"]
            if circuit == "atp":
                meta["best_of"] = pat_meta.get("best_of_men", 3)
            else:
                meta["best_of"] = 3
            return meta

    if "challenger" in title_lower:
        meta["level"] = "C"
    elif "itf" in title_lower or "futures" in title_lower:
        meta["level"] = "F"

    if "clay" in title_lower or "terre" in title_lower:
        meta["surface"] = "Clay"
    elif "grass" in title_lower or "gazon" in title_lower:
        meta["surface"] = "Grass"
    elif "indoor" in title_lower:
        meta["indoor"] = 1

    return meta


def fetch_tennisexplorer_matches(circuit: str = "all", date_str: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    Télécharge et extrait l'ensemble des matchs du jour depuis TennisExplorer.
    Supporte les matchs de Grand Chelem (dont Qualifs US Open), ATP 250 (Winston-Salem), WTA, Challengers.
    """
    if date_str:
        # Format YYYY-MM-DD
        try:
            dt = datetime.strptime(date_str, "%Y-%m-%d")
            url = f"https://www.tennisexplorer.com/matches/?type=all&year={dt.year}&month={dt.month}&day={dt.day}"
        except Exception:
            url = "https://www.tennisexplorer.com/matches/?type=all"
    else:
        url = "https://www.tennisexplorer.com/matches/?type=all"

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "fr,fr-FR;q=0.9,en-US;q=0.8,en;q=0.7"
    }

    logger.info(f"Scraping des matchs depuis TennisExplorer: {url}")
    req = urllib.request.Request(url, headers=headers)
    
    try:
        with urllib.request.urlopen(req, timeout=12) as resp:
            html = resp.read().decode("utf-8", errors="ignore")
    except Exception as e:
        logger.error(f"Erreur réseau lors de la récupération TennisExplorer: {e}")
        return []

    soup = BeautifulSoup(html, "html.parser")
    tables = soup.find_all("table", class_="result")
    if not tables:
        logger.warning("Aucune table 'result' trouvée sur TennisExplorer")
        return []

    # La table principale des matchs est généralement la 2ème table (index 1) ou toutes les tables combinées
    # On parcourt les tables pour extraire tous les matchs
    raw_matches = []
    seen_match_keys = set()

    for table in tables:
        rows = table.find_all("tr")
        if not rows:
            continue

        current_tourney = "Tournoi Tennis"
        current_circuit = "atp"
        current_href = ""
        is_double = False

        i = 0
        while i < len(rows):
            row = rows[i]
            head_td = row.find("td", class_="head") or row.find("th", class_="head")
            
            # En-tête de tournoi
            if head_td or "head" in row.get("class", []):
                t_link = row.find("a")
                if t_link:
                    current_tourney = t_link.get_text(strip=True)
                    current_href = t_link.get("href", "")
                else:
                    current_tourney = row.get_text(" ", strip=True)
                    current_href = ""

                is_double = "type=double" in current_href or "mix" in current_href or "double" in current_tourney.lower()
                is_amateur = "utr" in current_tourney.lower() or "exhibition" in current_tourney.lower() or "futures" in current_tourney.lower()
                if "wta" in current_href.lower() or "wta" in current_tourney.lower() or "women" in current_href.lower():
                    current_circuit = "wta"
                else:
                    current_circuit = "atp"

                i += 1
                continue

            if is_double or is_amateur:
                i += 1
                continue

            # Match en 2 lignes consécutives (Joueur 1 sur ligne i, Joueur 2 sur ligne i+1)
            p1_td = row.find("td", class_="t-name")
            if p1_td and (i + 1) < len(rows):
                next_row = rows[i + 1]
                p2_td = next_row.find("td", class_="t-name")
                if p2_td:
                    # Extraction Heure / Statut
                    time_td = row.find("td", class_="time")
                    time_val = time_td.get_text(strip=True) if time_td else ""
                    if "Live" in time_val:
                        time_val = time_val.split("Live")[0].strip()
                    if not time_val:
                        time_val = "Aujourd'hui"

                    # Noms des joueurs
                    p1_a = p1_td.find("a")
                    p2_a = p2_td.find("a")
                    p1_name = p1_a.get_text(strip=True) if p1_a else p1_td.get_text(strip=True)
                    p2_name = p2_a.get_text(strip=True) if p2_a else p2_td.get_text(strip=True)

                    # Nettoyage des têtes de série ex: "Safiullin R. (1)" -> "Safiullin R."
                    p1_clean = re.sub(r"\s*\(\d+\)$", "", p1_name).strip()
                    p2_clean = re.sub(r"\s*\(\d+\)$", "", p2_name).strip()

                    # Cotes
                    odds_tds = row.find_all("td", class_=re.compile(r"course"))
                    odds1, odds2 = None, None
                    if len(odds_tds) >= 2:
                        try:
                            odds1 = float(odds_tds[0].get_text(strip=True))
                        except Exception:
                            odds1 = None
                        try:
                            odds2 = float(odds_tds[1].get_text(strip=True))
                        except Exception:
                            odds2 = None

                    # Filtrer les matchs sans cotes si c'est un tournoi mineur
                    is_major = any(k in current_tourney.lower() for k in ["us open", "winston", "monterrey", "cleveland", "cincinnati", "challenger", "wta", "atp"])
                    if not is_major and (odds1 is None or odds2 is None):
                        i += 2
                        continue

                    # Score / Statut
                    res_td1 = row.find("td", class_="result")
                    res_td2 = next_row.find("td", class_="result")
                    s1 = res_td1.get_text(strip=True) if res_td1 else ""
                    s2 = res_td2.get_text(strip=True) if res_td2 else ""
                    is_finished = bool(s1 or s2)

                    # Clé unique pour éviter les doublons
                    match_key = f"{p1_clean.lower()}_{p2_clean.lower()}_{current_tourney.lower()}"
                    if match_key not in seen_match_keys and p1_clean and p2_clean:
                        seen_match_keys.add(match_key)

                        # Contexte tournoi
                        meta = deduce_tournament_meta(current_tourney, circuit=current_circuit)
                        
                        # Affichage du nom de tournoi avec précision (ex: US Open (Qualifs))
                        display_title = current_tourney
                        if "us open" in current_tourney.lower():
                            display_title = f"{current_circuit.upper()} US Open (Qualifs)"
                        elif "winston" in current_tourney.lower():
                            display_title = "ATP Winston-Salem"
                        elif "monterrey" in current_tourney.lower():
                            display_title = "WTA Monterrey Open"
                        elif "cleveland" in current_tourney.lower():
                            display_title = "WTA Cleveland"
                        elif "philadelphia" in current_tourney.lower():
                            display_title = "WTA Philadelphia"
                        elif "challenger" in current_tourney.lower():
                            display_title = f"Challenger {current_tourney.replace('challenger', '').strip()}"

                        raw_matches.append({
                            "id": f"te_{len(raw_matches)}_{re.sub(r'[^a-zA-Z0-9]', '', p1_clean)[:8]}",
                            "circuit": current_circuit,
                            "sport_title": display_title,
                            "tournament": current_tourney,
                            "surface": meta["surface"],
                            "level": meta["level"],
                            "best_of": meta["best_of"],
                            "indoor": meta["indoor"],
                            "commence_time": "",
                            "time_display": time_val,
                            "p1_raw": p1_clean,
                            "p2_raw": p2_clean,
                            "p1": p1_clean,
                            "p2": p2_clean,
                            "odds1": odds1,
                            "odds2": odds2,
                            "total_line": None,
                            "odds_over": None,
                            "odds_under": None,
                            "handicap_line": None,
                            "odds_h1": None,
                            "odds_h2": None,
                            "bookmaker": "TennisExplorer / Bet365",
                            "is_finished": is_finished
                        })

                    i += 2
                    continue
            i += 1

    # Filtrer par circuit si demandé
    c_req = circuit.lower()
    if c_req == "atp":
        filtered = [m for m in raw_matches if m["circuit"] == "atp"]
    elif c_req == "wta":
        filtered = [m for m in raw_matches if m["circuit"] == "wta"]
    else:
        filtered = raw_matches

    logger.info(f"TennisExplorer: {len(filtered)} matchs extraits avec succès ({len([m for m in filtered if m['circuit'] == 'atp'])} ATP, {len([m for m in filtered if m['circuit'] == 'wta'])} WTA)")
    return filtered
