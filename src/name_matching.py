"""
Matching de noms de joueurs entre deux sources qui n'utilisent pas le même
format :
  - TML-Database (nos données de matchs) : "Daniil Medvedev" (prénom complet + nom)
  - tennis-data.co.uk (cotes)            : "Medvedev D."     (nom + initiale prénom)

STRATEGIE EN DEUX TEMPS :
1. Clé normalisée "NOM INITIALE" (accents retirés, particules de nom
   composé gérées : "del Potro", "van de Zandschulp", "de Minaur"...).
   Ça résout la grande majorité des cas en O(1) par lookup.
2. Pour ce qui ne matche pas exactement (fautes de frappe, formats
   inhabituels, joueurs homonymes), matching approximatif (rapidfuzz)
   restreint aux joueurs déjà vus dans une fenêtre de dates proche, pour
   ne pas comparer contre des milliers de candidats à chaque fois.
"""

import re
import unicodedata
from rapidfuzz import fuzz, process

# Particules qui font partie du nom de famille, pas du prénom
SURNAME_PARTICLES = {
    "de", "del", "della", "di", "da", "van", "von", "der", "den",
    "le", "la", "du", "dos", "das", "mc", "mac", "al",
}

# Alias manuels : noms bruts tels qu'écrits dans tennis-data.co.uk -> nom TML canonique.
# Nécessaire quand la source des cotes utilise une initiale différente de TML
# (ex: prénom ukrainien vs translittération anglaise) ou un format vraiment atypique.
ODDS_NAME_ALIASES: dict[str, str] = {
    # Oleksandr (ukrainien) vs Alexandr (translittération anglaise utilisée par TML)
    "Dolgopolov O.": "Alexandr Dolgopolov",
    # Ajouter d'autres alias ici si de nouveaux cas apparaissent, format :
    # "Nom tel que dans les cotes": "Prénom Nom tel que dans TML",
}


def strip_accents(s):
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")


def normalize_full_name(name):
    """'Juan Martin del Potro' -> ('DEL POTRO', 'J')
    'Juan Ignacio Londero'    -> ('LONDERO', 'J')
    'Daniil Medvedev'         -> ('MEDVEDEV', 'D')

    Heuristique : si une particule connue (del/van/de/mc...) apparaît dans
    le nom, le nom de famille commence à cette particule (gère les prénoms
    composés comme 'Juan Martin'). Sinon, on suppose que seul le DERNIER
    token est le nom de famille (et pas 'tout sauf le premier mot', ce qui
    casserait les prénoms composés comme 'Juan Ignacio Londero')."""
    if not isinstance(name, str) or not name.strip():
        return None
    tokens = strip_accents(name).strip().split()
    tokens = [re.sub(r"[^a-zA-Z'\-]", "", t) for t in tokens]
    tokens = [t for t in tokens if t]
    if len(tokens) < 2:
        return None

    particle_idx = None
    for idx in range(1, len(tokens) - 1):  # jamais le premier ni le tout dernier token
        if tokens[idx].lower() in SURNAME_PARTICLES:
            particle_idx = idx
            break

    if particle_idx is not None:
        surname = " ".join(tokens[particle_idx:]).upper()
    else:
        surname = tokens[-1].upper()  # dernier token seulement, pas tokens[1:]

    initial = tokens[0][0].upper()
    return (surname, initial)


def normalize_odds_name(name):
    """'Medvedev D.' ou 'Del Potro J.M.' -> ('DEL POTRO', 'J') -- ne garde
    que la première initiale.

    Traitement spécial des traits d'union : 'Carreno-Busta P.' -> ('CARRENO BUSTA', 'C').
    Traitement des initiales doubles : 'Kuznetsov An.' -> ('KUZNETSOV', 'A')
    (certaines sources utilisent 2 lettres pour lever l'ambiguïté entre
    homonymes, on ne garde que la première initiale pour la clé)."""
    if not isinstance(name, str) or not name.strip():
        return None
    s = strip_accents(name).strip()
    # Format standard : "Nom Initiale(s)." -- capture 1 ou 2 lettres avant le point
    match = re.match(r"^(.*?)\s+([A-Za-z]{1,2})\.", s)
    if match:
        surname = re.sub(r"[^a-zA-Z'\- ]", "", match.group(1)).strip()
        surname = surname.replace("-", " ").upper()
        initial = match.group(2)[0].upper()  # toujours la 1ère lettre seulement
        return (surname, initial)
    return None


def build_key(surname, initial):
    return f"{surname}_{initial}"


def build_candidate_index(names):
    """names : itérable de noms complets (format TML). Retourne un dict
    clé normalisée -> nom original (en cas de collision, garde une liste).

    Plusieurs clés alternatives sont indexées par joueur pour couvrir les
    cas où les deux sources utilisent des formats différents du nom composé :
    - Clé principale : résultat de normalize_full_name (ex: 'BUSTA_P')
    - Clé sans tiret : 'CARRENO-BUSTA' -> 'CARRENO BUSTA'
    - Clé composée  : pour 'Pablo Carreno Busta', on ajoute aussi les 2
      derniers tokens ('CARRENO BUSTA_P') car tennis-data.co.uk écrit souvent
      le nom composé complet alors que TML ne prend que le dernier token.
    """
    index = {}
    for name in names:
        parsed = normalize_full_name(name)
        if parsed is None:
            continue
        surname_norm, initial = parsed

        def _add(key, s_norm):
            index.setdefault(key, []).append((name, s_norm))

        # Clé principale
        _add(build_key(surname_norm, initial), surname_norm)

        # Clé secondaire sans tiret
        surname_no_hyphen = surname_norm.replace("-", " ")
        if surname_no_hyphen != surname_norm:
            _add(build_key(surname_no_hyphen, initial), surname_no_hyphen)

        # Clé composée : tokens[-2:] du nom complet (hors particules déjà gérées)
        # Utile pour 'Pablo Carreno Busta' -> clé principale = 'BUSTA_P'
        # -> on ajoute aussi 'CARRENO BUSTA_P' pour matcher 'Carreno-Busta P.'
        all_tokens = strip_accents(name).strip().split()
        all_tokens = [re.sub(r"[^a-zA-Z'\-]", "", t) for t in all_tokens if t]
        # Dernier token = surname_norm (cas sans particule), avant-dernier = possible préfixe
        if len(all_tokens) >= 3:
            # Clé 2 derniers tokens : 'CARRENO BUSTA_P' pour 'Pablo Carreno Busta'
            compound = (all_tokens[-2] + " " + all_tokens[-1]).upper()
            if compound != surname_norm:
                _add(build_key(compound, initial), compound)
                compound_no_hyphen = compound.replace("-", " ")
                if compound_no_hyphen != compound:
                    _add(build_key(compound_no_hyphen, initial), compound_no_hyphen)
            # Clé avant-dernier token seul : 'BAUTISTA_R' pour 'Roberto Bautista Agut'
            # (les cotes n'écrivent parfois que le premier mot du nom composé)
            penultimate = all_tokens[-2].upper()
            if penultimate != surname_norm and " " not in penultimate:
                _add(build_key(penultimate, initial), penultimate)
                penultimate_no_hyphen = penultimate.replace("-", " ")
                if penultimate_no_hyphen != penultimate:
                    _add(build_key(penultimate_no_hyphen, initial), penultimate_no_hyphen)

    return index


def match_odds_name(odds_name, candidate_index, fuzzy_pool=None, fuzzy_threshold=85):
    """Tente de faire correspondre un nom au format tennis-data.co.uk à un
    nom TML. Retourne (nom_tml, methode) ou (None, None) si rien trouvé.
    methode est 'exact', 'alias' ou 'fuzzy', utile pour auditer le taux de matching.

    fuzzy_pool est ignoré (gardé pour compatibilité d'appel) -- le fallback
    fuzzy compare directement contre candidate_index, filtré par initiale
    pour rester rapide et pertinent (comparer un nom de famille contre un
    nom de famille, pas contre une chaîne complète 'Prénom Nom')."""
    # 0. Alias manuels : cas irréductibles par normalisation (initiale différente, etc.)
    if odds_name in ODDS_NAME_ALIASES:
        return ODDS_NAME_ALIASES[odds_name], "alias"

    parsed = normalize_odds_name(odds_name)
    if parsed is None:
        return None, None
    surname, initial = parsed
    key = build_key(surname, initial)

    candidates = candidate_index.get(key)
    if candidates:
        return candidates[0][0], "exact"  # en cas d'homonymie exacte, ambigu -> on garde le 1er

    # Fallback fuzzy : uniquement parmi les joueurs de la MEME initiale
    # (réduit drastiquement le nombre de comparaisons et évite les faux
    # positifs entre joueurs sans rapport)
    same_initial_surnames = [
        (orig_name, surname_norm)
        for k, entries in candidate_index.items()
        for orig_name, surname_norm in entries
        if k.endswith(f"_{initial}")
    ]
    if not same_initial_surnames:
        return None, None

    best_match, best_score = None, 0
    for orig_name, surname_norm in same_initial_surnames:
        score = fuzz.ratio(surname, surname_norm)
        if score > best_score:
            best_match, best_score = orig_name, score

    if best_score >= fuzzy_threshold:
        return best_match, "fuzzy"

    # Dernier recours : certaines sources abrègent parfois les noms composés
    # au seul dernier mot (ex: 'Zandschulp' au lieu de 'Van De Zandschulp').
    # On compare alors uniquement le dernier mot du nom de famille TML.
    best_match, best_score = None, 0
    for orig_name, surname_norm in same_initial_surnames:
        last_word = surname_norm.split()[-1]
        score = fuzz.ratio(surname, last_word)
        if score > best_score:
            best_match, best_score = orig_name, score
    if best_score >= fuzzy_threshold:
        return best_match, "fuzzy"

    return None, None
