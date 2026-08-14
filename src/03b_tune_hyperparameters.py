"""
Recherche d'hyperparamètres XGBoost avec validation croisée TEMPORELLE
(walk-forward) — jamais de CV aléatoire classique ici, ce serait une fuite
garantie (le modèle "verrait" indirectement le futur pendant la validation).

PRINCIPE : les matchs triés par date sont découpés en N_FOLDS+1 segments
chronologiques égaux. Pour le pli k, on entraîne sur tout ce qui précède
le segment k et on valide sur le segment k lui-même — comme si on
remontait le temps et qu'on prédisait "le futur proche" à chaque étape.
On moyenne le log loss sur tous les plis pour classer les combinaisons
d'hyperparamètres testées (recherche aléatoire, pas de grille exhaustive :
avec 8 hyperparamètres, une grille complète serait bien trop coûteuse).

Usage :
    python 03b_tune_hyperparameters.py

Sortie : data/processed/best_params.json, lu automatiquement par
03_train_model.py au prochain entraînement (s'il existe).

Temps estimé : N_SEARCH_ITER x N_FOLDS entraînements XGBoost avec arrêt
anticipé. Sur ~200k matchs, compte plusieurs minutes (le script affiche
une progression pour suivre l'avancement).
"""

import pandas as pd
import numpy as np
import xgboost as xgb
from sklearn.metrics import log_loss
from pathlib import Path
import json
import time
import random

BASE_DIR = Path(__file__).resolve().parent.parent
PROCESSED_DIR = BASE_DIR / "data" / "processed"

N_FOLDS = 4
N_SEARCH_ITER = 20
RANDOM_SEED = 42

random.seed(RANDOM_SEED)
np.random.seed(RANDOM_SEED)

SEARCH_SPACE = {
    "max_depth": [3, 4, 5, 6],
    "learning_rate": [0.02, 0.03, 0.05, 0.08],  # évite les très petites valeurs (trop d'itérations avant l'arrêt anticipé)
    "subsample": [0.6, 0.7, 0.8, 0.9, 1.0],
    "colsample_bytree": [0.5, 0.6, 0.7, 0.8, 0.9],
    "min_child_weight": [1, 3, 5, 10, 20],
    "reg_lambda": [0.5, 1.0, 1.5, 2.0, 3.0],
    "reg_alpha": [0.0, 0.1, 0.5, 1.0],
    "gamma": [0.0, 0.1, 0.3, 0.5],
}


def load_data():
    in_path = PROCESSED_DIR / "features.parquet"
    if not in_path.exists():
        raise FileNotFoundError(f"{in_path} introuvable. Lance d'abord: python 02_feature_engineering.py")
    df = pd.read_parquet(in_path)
    df = df[~df["retirement"]]
    df = df.sort_values("tourney_date").reset_index(drop=True)
    return df


def prepare_xy(df):
    cat_cols = ["surface", "tourney_level", "round", "hand_matchup", "indoor"]
    df = pd.get_dummies(df, columns=cat_cols, dummy_na=True)
    drop_cols = ["match_id", "tourney_date", "target", "retirement"]
    feature_cols = [c for c in df.columns if c not in drop_cols]
    return df[feature_cols], df["target"], feature_cols


def make_time_folds(n, n_folds):
    """Découpe en segments chronologiques égaux. Pli k : train = segment
    [0 : fin_k], val = segment [fin_k : fin_k+1]. Renvoie des tuples
    d'indices (pas des slices) pour rester compatible avec iloc."""
    edges = np.linspace(0, n, n_folds + 2, dtype=int)
    folds = []
    for k in range(1, n_folds + 1):
        train_idx = np.arange(0, edges[k])
        val_idx = np.arange(edges[k], edges[k + 1])
        folds.append((train_idx, val_idx))
    return folds, edges


def sample_params():
    return {k: random.choice(v) for k, v in SEARCH_SPACE.items()}


def evaluate_params(params, X, y, folds):
    scores = []
    for train_idx, val_idx in folds:
        X_tr, y_tr = X.iloc[train_idx], y.iloc[train_idx]
        X_val, y_val = X.iloc[val_idx], y.iloc[val_idx]
        model = xgb.XGBClassifier(
            n_estimators=400,
            eval_metric="logloss",
            early_stopping_rounds=30,
            n_jobs=-1,
            tree_method="hist",
            **params,
        )
        model.fit(X_tr, y_tr, eval_set=[(X_val, y_val)], verbose=False)
        p = model.predict_proba(X_val)[:, 1]
        scores.append(log_loss(y_val, p))
    return float(np.mean(scores))


if __name__ == "__main__":
    df = load_data()
    X, y, feature_cols = prepare_xy(df)
    folds, edges = make_time_folds(len(X), N_FOLDS)

    print(f"{len(X)} matchs, {N_FOLDS} plis temporels, {N_SEARCH_ITER} combinaisons testées.")
    for i, (tr, val) in enumerate(folds):
        print(f"  Pli {i + 1}: train=[0:{len(tr)}] val=[{val[0]}:{val[-1] + 1}]")

    results = []
    t0 = time.time()
    for it in range(N_SEARCH_ITER):
        params = sample_params()
        score = evaluate_params(params, X, y, folds)
        results.append((score, params))
        elapsed = time.time() - t0
        eta = elapsed / (it + 1) * (N_SEARCH_ITER - it - 1)
        print(f"[{it + 1}/{N_SEARCH_ITER}] log_loss={score:.4f}  "
              f"({elapsed:.0f}s écoulées, ETA {eta:.0f}s)  {params}")

    results.sort(key=lambda r: r[0])
    best_score, best_params = results[0]
    print(f"\nMeilleure combinaison (log_loss moyen sur les {N_FOLDS} plis = {best_score:.4f}):")
    print(json.dumps(best_params, indent=2))

    out_path = PROCESSED_DIR / "best_params.json"
    with open(out_path, "w") as f:
        json.dump(best_params, f, indent=2)
    print(f"\nSauvegardé -> {out_path}")
    print("Relance 03_train_model.py : il détectera ce fichier et utilisera "
          "automatiquement ces paramètres au lieu des valeurs par défaut.")
