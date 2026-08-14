# Tennis Match Predictor — XGBoost + Value Betting

## Résultats actuels (V1, sans les cotes)

| Modèle | Log loss | Accuracy | AUC |
|---|---|---|---|
| Classement ATP seul (baseline) | 0.671 | 63.9% | 0.669 |
| **XGBoost (Elo + forme + service)** | **0.609** | **66.0%** | **0.724** |

Entraîné sur ~64k matchs ATP 2000-2021, testé sur 2023-2025 (split temporel strict).

## Structure du projet

```
tennis_predictor/
├── data/
│   ├── raw/            # CSV bruts par année (source: TML-Database)
│   │   └── odds/       # <- À REMPLIR PAR TOI (voir plus bas)
│   └── processed/       # datasets transformés + modèle entraîné
├── src/
│   ├── 01_build_dataset.py           # winner/loser -> format symétrique player_1/player_2
│   ├── 02_feature_engineering.py     # Elo, forme, H2H, repos, stats de service (walk-forward)
│   ├── 03_train_model.py             # split temporel + XGBoost + calibration
│   ├── 04_backtest_value_betting.py  # fusion cotes + Kelly + ROI
│   └── 05_predict_match.py          # prédiction interactive pour un match à venir
└── README.md
```

Pour tout relancer depuis zéro :
```bash
python src/01_build_dataset.py
python src/02_feature_engineering.py
python src/03_train_model.py
python src/04_backtest_value_betting.py  # optionnel (nécessite des cotes)
```

Pour prédire un match à venir :
```bash
python src/05_predict_match.py
```

## D'où viennent les données

Le dataset historique de référence pour ce type de projet est normalement
`JeffSackmann/tennis_atp` sur GitHub. **Ce repo n'est plus public actuellement**
(son profil GitHub ne liste plus qu'un seul dépôt). J'ai utilisé à la place
**`Tennismylife/TML-Database`**, un mirror actif avec le même schéma de colonnes,
qui couvre 1968 à aujourd'hui. Si tu retrouves un accès au repo original ou à
une autre source (Kaggle a aussi des copies), le format est identique donc
c'est un remplacement direct.

## Les features (ta "grande liste" — comment la prioriser)

Ce que j'ai codé couvre les familles qui comptent le plus en pratique, par
ordre d'importance réelle observée sur ce dataset :

1. **Elo général + Elo par surface** (~40% de l'importance du modèle à eux
   seuls). C'est LA feature qui résume le mieux "qui est le meilleur joueur
   en ce moment", bien plus que le classement ATP qui réagit lentement.
2. **Classement / points ATP** (en log, plus stable que le rang brut qui a
   une échelle très non-linéaire entre le top 10 et le rang 200).
3. **Forme récente** (10 derniers matchs, 90 derniers jours).
4. **Face-à-face (H2H)** — utile mais attention, souvent sur-interprété par
   les parieurs amateurs ; avec peu de confrontations c'est surtout du bruit.
5. **Repos / fatigue** (jours depuis dernier match, matchs sur 7/14 jours) —
   pertinent surtout en fin de tournoi/saison ou après un match marathon.
6. **Stats de service glissantes** (% premier service, points gagnés au
   service, break points sauvés) sur 5 et 20 derniers matchs.
7. **Statique** : âge, taille, main dominante (matchup gaucher/droitier).

### Ce que tu peux ajouter ensuite (si tu as une "grande liste" d'idées)
- **Style de jeu** (agressivité au retour, variance du % de premier service)
  si tu as accès au Match Charting Project de Sackmann (point-by-point)
- **Altitude / conditions** (certains tournois, ex: Bogota, favorisent le
  service)
- **Qualité de l'adversaire battu récemment** (Elo pondéré des adversaires,
  pas juste le winrate brut)

Attention : plus tu ajoutes de features corrélées entre elles (rank + rank
points + Elo par ex.), moins chacune apporte marginalement. Mieux vaut 10
features bien pensées que 50 redondantes — regarde le classement
`feature_importances_` affiché par `03_train_model.py` pour trier.

## 05_predict_match.py — Prédiction interactive

Prédit la probabilité de victoire pour un match à venir en reconstituant toutes
les features exactement comme pendant l'entraînement.

**Prérequis** : avoir lancé `02_feature_engineering.py` et `03_train_model.py`
au moins une fois (pour générer `player_state.pkl`, `xgb_model.json` et
`feature_cols.pkl` dans `data/processed/`).

### Champs demandés interactivement

| Champ | Obligatoire | Notes |
|---|---|---|
| Joueur 1 / Joueur 2 | ✅ | Recherche floue : tape "Djoko" pour "Djokovic N." |
| Surface | ✅ | Hard / Clay / Grass / Carpet |
| Niveau | ✅ | G=GC, M=M1000, A=500, D=250, C=Challenger, F=Finals |
| Tour | ✅ | R128 / R64 / R32 / R16 / QF / SF / F |
| Best-of | ✅ | 3 ou 5 |
| Indoor | ✅ | 0=outdoor, 1=indoor |
| Date du match | ✅ | AAAA-MM-JJ (défaut : aujourd'hui) |
| Classement ATP actuel | ⬜ | Défaut : dernier classement connu du joueur |
| Tête de série (seed) | ⬜ | Numéro de seed, vide si non tête de série |
| Statut d'entrée | ⬜ | `WC` = wildcard, `Q` = qualifié, vide = aucun |
| Stats intra-tournoi | ⬜ | Matchs joués + jeux/sets gagnés·totaux dans le tournoi en cours — **non demandé pour R128/R64/R32** (toujours 0 par définition) |
| Cotes bookmaker | ⬜ | Si renseignées : edge calculé + recommandation value bet |

### Logique des stats intra-tournoi

À partir des R16, le script demande combien de matchs chaque joueur a déjà joués
dans le tournoi, puis (si > 0) les jeux et sets gagnés/totaux. Ces données
alimentent `tourney_game_winpct_diff`, `tourney_sets_winpct_diff`,
`matches_this_tourney_diff` et `sets_tourney_diff` — des features qui capturent
si un joueur a dominé ou galéré sur ses matchs précédents dans le même tournoi.
Laisser à 0 si l'information n'est pas disponible (le modèle reste cohérent avec
la valeur neutre 0.5 utilisée à l'entraînement pour les premiers tours).

### Sortie

```
============================================================
  RESULTAT
============================================================
  Sinner J.                             68.3%
  Alcaraz C.                            31.7%

  Elo : Sinner J. = 2187  |  Alcaraz C. = 2143
  Elo Clay : Sinner J. = 2051  |  Alcaraz C. = 2198
  H2H : Sinner J. 3-5 Alcaraz C.

  --- Analyse value bet (seuil edge > 3%) ---
  Joueur                               p_modele   p_marche      Edge
  Sinner J.                               68.3%      62.5%    +5.8%  <= VALUE BET!
  Alcaraz C.                              31.7%      37.5%    -5.8%
  => PARIER SUR : Sinner J.  @ 1.55  (edge +5.8%)
```

## Value betting (activé)

Le backtest a besoin de **cotes historiques** que je ne peux pas récupérer
depuis mon environnement (site non accessible). Étapes :

1. Va sur **tennis-data.co.uk** (section "ATP"), télécharge les fichiers
   `.xlsx` par saison (2000 à aujourd'hui si possible). Ils contiennent les
   cotes Pinnacle (`PSW`/`PSL`), Bet365 (`B365W`/`B365L`), moyenne du marché
   (`AvgW`/`AvgL`) et max du marché (`MaxW`/`MaxL`).
2. Place ces fichiers dans `data/raw/odds/`.
3. Lance `python 04_backtest_value_betting.py`.

Le script gère automatiquement :
- **La priorité des cotes** : Pinnacle en premier (marché le plus efficient,
  donc la référence la plus fiable), puis Bet365, puis moyenne, puis max,
  selon ce qui est disponible ligne par ligne.
- **Le matching des noms** (`name_matching.py`) : TML donne "Daniil Medvedev",
  tennis-data.co.uk donne "Medvedev D." — normalisation en "NOM + INITIALE"
  (gère aussi les particules de noms composés : "del Potro", "van de
  Zandschulp"...), avec repli en matching approximatif (`rapidfuzz`) pour
  les fautes de frappe ou noms composés abrégés différemment entre les
  deux sources.
- **Le décalage de date** : `tourney_date` dans nos données est la date de
  DEBUT du tournoi, pas la date exacte du match (un match de finale de
  Grand Chelem a lieu ~2 semaines après le début du tournoi). Le script
  cherche donc, pour une paire de joueurs donnée, une correspondance dans
  une fenêtre de ~21 jours plutôt qu'une date exacte.

Le script affiche un rapport de matching (taux de succès exact/fuzzy,
échecs par catégorie) à chaque exécution — **vérifie ce taux avant de faire
confiance au backtest**. Sur données réelles, attends-toi à un taux entre
85% et 97% selon la qualité/complétude des fichiers tennis-data.co.uk pour
les années concernées (plus bas sur les tout premiers Challengers/Futures,
souvent absents de tennis-data.co.uk qui couvre surtout le tour principal).

## Pièges à éviter (au-delà de ce qui est déjà géré dans le code)

- **Ne jamais split aléatoirement** train/test en tennis — toujours temporel.
- **Ne jamais utiliser une stat calculée APRÈS le match** comme feature
  (ex: `w_ace` du match lui-même — c'est un résultat, pas une info pré-match).
- **Le ROI seul ment** sur un petit échantillon (variance énorme au tennis,
  un seul tournoi peut faire basculer le ROI). Regarde aussi le log loss /
  Brier sur le test set, et si possible la CLV (closing line value).
- **Les cotes bougent** — utilise si possible les cotes d'ouverture (pas de
  clôture) pour simuler ce que tu aurais réellement pu obtenir en pariant
  avant le marché.
