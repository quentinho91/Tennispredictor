"""
Backtest de value betting.

CE SCRIPT ATTEND DES FICHIERS DE COTES DANS data/raw/odds/*.xlsx (format
tennis-data.co.uk). Colonnes attendues au minimum : Date, Winner, Loser,
et au moins un couple de colonnes de cotes parmi PSW/PSL (Pinnacle),
B365W/B365L (Bet365), AvgW/AvgL, MaxW/MaxL.

------------------------------------------------------------------------
LE VRAI DEFI TECHNIQUE ICI : FAIRE CORRESPONDRE LES MATCHS ENTRE 2 SOURCES
------------------------------------------------------------------------
1. Format de nom différent : TML donne "Daniil Medvedev", tennis-data.co.uk
   donne "Medvedev D." -> voir name_matching.py (normalisation + fuzzy
   matching en secours).
2. Date différente : `tourney_date` dans nos données est la date de DEBUT
   du tournoi (convention Sackmann/TML), pas la date exacte du match.
   tennis-data.co.uk donne la date réelle du match. Un Grand Chelem dure
   2 semaines -> on cherche donc, pour une paire de joueurs donnée, une
   correspondance dans une FENETRE de ~21 jours après le début du
   tournoi plutôt qu'une date exacte.
------------------------------------------------------------------------

RAISONNEMENT VALUE BETTING :
1. Retirer la marge du bookmaker (overround) pour obtenir la proba
   "vraie" implicite du marché.
2. Comparer notre proba modèle à cette proba marché : l'écart (edge) doit
   dépasser un seuil pour absorber le bruit du modèle.
3. Kelly fractionné (jamais Kelly plein, bien trop volatil en pratique).
4. Le ROI seul ment sur un petit échantillon (variance énorme au tennis) :
   toujours regarder aussi le nombre de paris et le winrate.
"""

import pandas as pd
import numpy as np
import glob
import sys
from pathlib import Path
from collections import defaultdict

sys.path.insert(0, str(Path(__file__).resolve().parent))
from name_matching import build_candidate_index, match_odds_name, normalize_full_name

BASE_DIR = Path(__file__).resolve().parent.parent
PROCESSED_DIR = BASE_DIR / "data" / "processed"
ODDS_DIR = BASE_DIR / "data" / "raw" / "odds"

TOURNEY_WINDOW_DAYS = 21   # tourney_date = début du tournoi, pas date du match
ODDS_PRIORITY = [("PSW", "PSL"), ("B365W", "B365L"), ("AvgW", "AvgL"), ("MaxW", "MaxL")]


# ---------------------------------------------------------------------------
# Chargement des cotes
# ---------------------------------------------------------------------------

def load_odds():
    files = glob.glob(str(ODDS_DIR / "*.xlsx")) + glob.glob(str(ODDS_DIR / "*.xls"))
    if not files:
        raise FileNotFoundError(
            f"Aucun fichier de cotes trouvé dans {ODDS_DIR}. "
            "Télécharge les fichiers historiques sur tennis-data.co.uk "
            "(un .xlsx par saison, ATP) et place-les dans ce dossier."
        )
    dfs = []
    for f in files:
        try:
            d = pd.read_excel(f)
            d["_source_file"] = Path(f).name
            dfs.append(d)
        except Exception as e:
            print(f"Ignoré (illisible): {f} ({e})")
    odds = pd.concat(dfs, ignore_index=True)
    odds["Date"] = pd.to_datetime(odds["Date"], errors="coerce")
    odds = odds.dropna(subset=["Date", "Winner", "Loser"])

    # Les fichiers couvrant beaucoup d'années ont presque toujours quelques
    # cellules corrompues/texte au lieu d'un nombre -> conversion forcée,
    # les valeurs non convertibles deviennent NaN (traitées comme une cote manquante).
    for wcol, lcol in ODDS_PRIORITY:
        if wcol in odds.columns:
            odds[wcol] = pd.to_numeric(odds[wcol], errors="coerce")
        if lcol in odds.columns:
            odds[lcol] = pd.to_numeric(odds[lcol], errors="coerce")

    # Sélection des cotes par ordre de préférence (Pinnacle en priorité,
    # généralement considéré comme le marché le plus efficient / le moins
    # de marge, donc la référence la plus fiable pour estimer la "vraie" proba)
    odds["odds_winner"] = np.nan
    odds["odds_loser"] = np.nan
    odds["odds_source"] = None
    for wcol, lcol in ODDS_PRIORITY:
        if wcol not in odds.columns or lcol not in odds.columns:
            continue
        mask = odds["odds_winner"].isna() & odds[wcol].notna() & odds[lcol].notna()
        odds.loc[mask, "odds_winner"] = odds.loc[mask, wcol]
        odds.loc[mask, "odds_loser"] = odds.loc[mask, lcol]
        odds.loc[mask, "odds_source"] = wcol.replace("W", "")

    n_before = len(odds)
    odds = odds.dropna(subset=["odds_winner", "odds_loser"])
    print(f"Cotes chargées: {len(odds)}/{n_before} lignes avec au moins une source de cotes exploitable.")
    print(odds["odds_source"].value_counts())
    return odds


# ---------------------------------------------------------------------------
# Matching odds <-> matchs (noms + fenêtre de dates)
# ---------------------------------------------------------------------------

def match_odds_to_matches(odds, all_matches, test_match_ids):
    """all_matches : DataFrame avec match_id, tourney_date, p1_name, p2_name
    sur TOUTE la base (pas seulement la période test) -- indispensable pour
    résoudre les noms de joueurs retraités avant la période de test (ex:
    Federer, qui n'apparaît dans aucun match du test set 2023+, mais dont
    le nom doit quand même être reconnu s'il apparaît dans les cotes).

    test_match_ids : set des match_id éligibles au backtest (période test
    uniquement). Utilisé seulement à la toute fin, pour ne backtester que
    sur du vrai out-of-sample."""
    all_names = pd.concat([all_matches["p1_name"], all_matches["p2_name"]]).unique()
    candidate_index = build_candidate_index(all_names)

    # Index des matchs par paire de joueurs (frozenset) -> liste de
    # (match_id, tourney_date, p1_name), sur TOUTE la base
    pair_index = defaultdict(list)
    for row in all_matches.itertuples(index=False):
        pair = frozenset((row.p1_name, row.p2_name))
        pair_index[pair].append((row.match_id, row.tourney_date, row.p1_name))

    results = []
    stats = {"matched_exact": 0, "matched_fuzzy": 0, "name_unresolved": 0,
              "no_pair_candidate": 0, "matched_but_outside_test_period": 0, "no_date_candidate": 0}
    unresolved_sample = []

    for row in odds.itertuples(index=False):
        winner_tml, method_w = match_odds_name(row.Winner, candidate_index)
        loser_tml, method_l = match_odds_name(row.Loser, candidate_index)

        if winner_tml is None or loser_tml is None:
            stats["name_unresolved"] += 1
            if len(unresolved_sample) < 30:
                unresolved_sample.append((row.Winner, row.Loser, winner_tml is None, loser_tml is None))
            continue
        if method_w == "fuzzy" or method_l == "fuzzy":
            stats["matched_fuzzy"] += 1
        else:
            stats["matched_exact"] += 1

        pair = frozenset((winner_tml, loser_tml))
        candidates = pair_index.get(pair)
        if not candidates:
            stats["no_pair_candidate"] += 1
            continue

        # Parmi les candidats (mêmes 2 joueurs), on garde le match dont la
        # date de tournoi tombe dans la fenêtre [date_cote - 3j, date_cote + FENETRE]
        # et on prend le plus proche en cas d'ambiguïté (2 confrontations
        # entre les mêmes joueurs à quelques semaines d'intervalle, rare
        # mais possible sur une saison chargée).
        best = None
        best_delta = None
        for match_id, tourney_date, p1_name in candidates:
            delta = (row.Date - tourney_date).days
            if -3 <= delta <= TOURNEY_WINDOW_DAYS:
                if best is None or abs(delta - TOURNEY_WINDOW_DAYS / 2) < best_delta:
                    best = (match_id, p1_name)
                    best_delta = abs(delta - TOURNEY_WINDOW_DAYS / 2)

        if best is None:
            stats["no_date_candidate"] += 1
            continue

        match_id, p1_name = best

        if match_id not in test_match_ids:
            stats["matched_but_outside_test_period"] += 1
            continue

        # p1 est-il le vainqueur (winner_tml) ou le perdant ?
        p1_is_winner = (p1_name == winner_tml)
        odds_p1 = row.odds_winner if p1_is_winner else row.odds_loser
        odds_p2 = row.odds_loser if p1_is_winner else row.odds_winner

        results.append({
            "match_id": match_id,
            "date": row.Date,
            "odds_p1": odds_p1,
            "odds_p2": odds_p2,
            "odds_source": row.odds_source,
        })

    total = len(odds)
    print(f"\nMatching odds -> matchs sur {total} lignes de cotes :")
    for k, v in stats.items():
        print(f"  {k}: {v} ({v / total:.1%})")
    print(f"  matchés au final (période test): {len(results)} ({len(results) / total:.1%})")
    n_eligible = stats['matched_but_outside_test_period'] + len(results)
    if n_eligible > 0:
        print(f"  -> parmi les cotes correctement résolues à un match connu ({n_eligible}), "
              f"{len(results) / n_eligible:.1%} tombent dans la période test.")

    if unresolved_sample:
        print(f"\nEchantillon de noms non résolus (sur {stats['name_unresolved']} au total) :")
        print(f"  {'Winner (brut)':25s} {'Loser (brut)':25s}  problème")
        for w, l, w_bad, l_bad in unresolved_sample[:15]:
            prob = ("Winner" if w_bad else "") + (" & " if w_bad and l_bad else "") + ("Loser" if l_bad else "")
            print(f"  {w:25s} {l:25s}  {prob}")

    return pd.DataFrame(results)


# ---------------------------------------------------------------------------
# Value betting
# ---------------------------------------------------------------------------

def remove_overround(odds_a, odds_b):
    inv_a, inv_b = 1 / odds_a, 1 / odds_b
    overround = inv_a + inv_b
    return inv_a / overround, inv_b / overround


def kelly_fraction(p_model, odds, fraction=0.25):
    b = odds - 1
    q = 1 - p_model
    f_star = (b * p_model - q) / b
    return max(0.0, f_star) * fraction


def backtest(merged, edge_threshold=0.03, label="", flat_unit=1.0):
    """Backtest flat-stake pur : mise fixe `flat_unit` par pari identifié
    comme value bet (p_model > p_marche + edge_threshold).

    C'est la métrique la plus pertinente pour l'usage réel où on misera
    toujours un même pourcentage de bankroll quel que soit le match."""
    if "date" in merged.columns:
        merged = merged.sort_values("date").reset_index(drop=True)

    history = []
    for row in merged.itertuples(index=False):
        p_market_p1, p_market_p2 = remove_overround(row.odds_p1, row.odds_p2)
        edge_p1 = row.p_model - p_market_p1
        edge_p2 = (1 - row.p_model) - p_market_p2

        bet_on, odds_used, edge_used, p_model_used, p_mkt_used = None, None, None, None, None
        if edge_p1 > edge_threshold and edge_p1 > edge_p2:
            bet_on, odds_used, edge_used = "p1", row.odds_p1, edge_p1
            p_model_used, p_mkt_used = row.p_model, p_market_p1
        elif edge_p2 > edge_threshold:
            bet_on, odds_used, edge_used = "p2", row.odds_p2, edge_p2
            p_model_used, p_mkt_used = 1 - row.p_model, p_market_p2

        if bet_on is None:
            continue

        won = (bet_on == "p1" and row.target == 1) or (bet_on == "p2" and row.target == 0)
        pnl = flat_unit * (odds_used - 1) if won else -flat_unit
        history.append({"match_id": row.match_id, "bet_on": bet_on,
                         "odds": odds_used, "edge": edge_used,
                         "p_model": p_model_used, "p_mkt": p_mkt_used,
                         "won": won, "pnl": pnl})

    hist_df = pd.DataFrame(history)
    if len(hist_df) == 0:
        tag = f"[{label}] " if label else ""
        print(f"{tag}edge>{edge_threshold:.0%} : aucun pari.")
        return hist_df

    n = len(hist_df)
    roi = hist_df["pnl"].sum() / (n * flat_unit)
    winrate = hist_df["won"].mean()
    avg_odds = hist_df["odds"].mean()
    avg_edge = hist_df["edge"].mean()
    # Break-even winrate moyenne (variable selon les cotes)
    breakeven = (1 / hist_df["odds"]).mean()
    tag = f"[{label}] " if label else ""
    marker = "  <-- POSITIF!" if roi > 0 else ""
    print(f"  {tag}edge>{edge_threshold:.0%} | n={n:4d} | "
          f"winrate={winrate:.3f} vs break-even={breakeven:.3f} | "
          f"ROI flat={roi:+.2%} | odds moy={avg_odds:.2f} | edge moy={avg_edge:.3f}{marker}")
    return hist_df



if __name__ == "__main__":
    pred_path = PROCESSED_DIR / "test_predictions.parquet"
    matches_path = PROCESSED_DIR / "matches_symmetric.parquet"
    if not pred_path.exists():
        raise FileNotFoundError(f"{pred_path} introuvable. Lance d'abord: python 03_train_model.py")
    if not matches_path.exists():
        raise FileNotFoundError(f"{matches_path} introuvable. Lance d'abord: python 01_build_dataset.py")

    preds = pd.read_parquet(pred_path)[["match_id", "p_model", "target"]]
    matches = pd.read_parquet(matches_path)[["match_id", "tourney_date", "p1_name", "p2_name"]]
    test_match_ids = set(preds["match_id"])
    print(f"Prédictions test chargées: {len(preds)} matchs (période test uniquement)")
    print(f"Base complète pour la résolution des noms: {len(matches)} matchs")

    odds = load_odds()
    matched = match_odds_to_matches(odds, matches, test_match_ids)

    merged = matched.merge(preds, on="match_id", how="inner")
    print(f"\n{len(merged)} matchs avec à la fois une prédiction modèle et une cote exploitable.")

    if len(merged) > 0:
        # ================================================================
        # SWEEP des seuils d'edge -- flat stake (mise fixe)
        # ================================================================
        for source_label, df_src in [
            ("ALL (Pinnacle+B365+Avg)", merged),
            ("Pinnacle uniquement",     merged[merged["odds_source"] == "PS"]),
            ("Bet365 uniquement",       merged[merged["odds_source"] == "B365"]),
        ]:
            if len(df_src) == 0:
                continue
            print(f"\n{'='*70}")
            print(f"  SOURCE : {source_label}  ({len(df_src)} matchs)")
            print(f"{'='*70}")
            for threshold in [0.03, 0.05, 0.07, 0.10]:
                backtest(df_src, edge_threshold=threshold)

        # ================================================================
        # CALIBRATION : p_model vs winrate reel par decile
        # ================================================================
        print(f"\n{'='*70}")
        print("  CALIBRATION : p_model vs winrate reel par decile de p_model")
        print(f"{'='*70}")
        merged_c = merged.copy()
        merged_c["p_mkt"] = 1 / merged_c["odds_p1"] / (
            1 / merged_c["odds_p1"] + 1 / merged_c["odds_p2"])
        merged_c["edge"] = merged_c["p_model"] - merged_c["p_mkt"]
        merged_c["p_decile"] = pd.qcut(merged_c["p_model"], q=10,
                                        labels=False, duplicates="drop")
        calib = merged_c.groupby("p_decile").agg(
            p_model_mean=("p_model", "mean"),
            winrate_reel=("target", "mean"),
            n=("target", "count"),
        ).reset_index()
        print(f"\n  {'Decile':>6}  {'p_model':>8}  {'Winrate':>8}  {'Biais':>7}  {'N':>6}")
        for _, r in calib.iterrows():
            biais = r["p_model_mean"] - r["winrate_reel"]
            flag = " [!]" if abs(biais) > 0.02 else ""
            print(f"  {int(r['p_decile']):>6}  {r['p_model_mean']:>8.3f}  "
                  f"{r['winrate_reel']:>8.3f}  {biais:>+7.3f}  {int(r['n']):>6}{flag}")
        print(f"\n  Biais moyen (p_model - p_marche) : {merged_c['edge'].mean():+.4f}")
        print(f"  Accuracy du modele (p_model>0.5 = vrai gagnant) : "
              f"{((merged_c['p_model'] > 0.5) == (merged_c['target'] == 1)).mean():.3f}")
