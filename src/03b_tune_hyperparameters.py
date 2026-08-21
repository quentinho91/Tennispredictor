"""
03b_tune_hyperparameters.py — Optimisation Bayésienne des hyperparamètres XGBoost via Optuna.

Minimise directement le Log Loss sur le set de calibration temporel.
Sauvegarde les meilleurs hyperparamètres dans data/processed/best_params_{circuit}.json,
qui sont automatiquement chargés par src/03_train_model.py.

USAGE :
    python src/03b_tune_hyperparameters.py --circuit atp --n-trials 30
    python src/03b_tune_hyperparameters.py --circuit wta --n-trials 30
"""

import os
import sys
import json
import argparse
from pathlib import Path
import numpy as np
import pandas as pd
import xgboost as xgb
from sklearn.metrics import log_loss, brier_score_loss, accuracy_score, roc_auc_score
import optuna

# Réduire la verbosité des logs Optuna
optuna.logging.set_verbosity(optuna.logging.WARNING)

BASE_DIR = Path(__file__).resolve().parent.parent
PROCESSED_DIR = BASE_DIR / "data" / "processed"

import importlib.util

_train_path = Path(__file__).parent / "03_train_model.py"
_spec = importlib.util.spec_from_file_location("tm", _train_path)
tm = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(tm)

load_data = tm.load_data
prepare_xy = tm.prepare_xy
dynamic_temporal_split = tm.dynamic_temporal_split
compute_sample_weights = tm.compute_sample_weights
filter_features = tm.filter_features
DEFAULT_PARAMS = tm.DEFAULT_PARAMS


def objective(trial, X_train, y_train, sample_weights, X_calib, y_calib):
    params = {
        "max_depth": trial.suggest_int("max_depth", 3, 7),
        "learning_rate": trial.suggest_float("learning_rate", 0.015, 0.08, log=True),
        "subsample": trial.suggest_float("subsample", 0.65, 0.95),
        "colsample_bytree": trial.suggest_float("colsample_bytree", 0.50, 0.90),
        "min_child_weight": trial.suggest_int("min_child_weight", 3, 20),
        "reg_alpha": trial.suggest_float("reg_alpha", 1e-3, 5.0, log=True),
        "reg_lambda": trial.suggest_float("reg_lambda", 0.5, 15.0, log=True),
        "gamma": trial.suggest_float("gamma", 1e-3, 3.0, log=True),
        "tree_method": "hist",
        "n_jobs": -1,
        "eval_metric": "logloss",
        "n_estimators": 800,
        "early_stopping_rounds": 30
    }

    model = xgb.XGBClassifier(**params)
    model.fit(
        X_train, y_train,
        sample_weight=sample_weights,
        eval_set=[(X_calib, y_calib)],
        verbose=False
    )

    p_calib = model.predict_proba(X_calib)[:, 1]
    loss = log_loss(y_calib, p_calib)
    return loss


def main():
    parser = argparse.ArgumentParser(description="Optimisation bayésienne XGBoost pour Tennis Predictor.")
    parser.add_argument("--circuit", default="atp", choices=["atp", "wta"], help="Circuit (atp ou wta)")
    parser.add_argument("--n-trials", type=int, default=30, help="Nombre de tirages Optuna (défaut: 30)")
    parser.add_argument("--half-life-years", type=float, default=7.0, help="Demi-vie pour sample weights")
    args = parser.parse_args()

    print("=" * 65)
    print(f"  OPTIMISATION BAYÉSIENNE XGBOOST ({args.circuit.upper()})")
    print(f"  Objectif : Minimiser le Log Loss ({args.n_trials} essais)")
    print("=" * 65)

    df = load_data(circuit=args.circuit)
    X, y, feature_cols = prepare_xy(df)

    X_train, y_train, train_mask, X_calib, y_calib, X_test, y_test, df_test = dynamic_temporal_split(
        df, X, y
    )

    feature_cols = filter_features(X_train, y_train, feature_cols, correlation_threshold=0.95)
    X_train = X_train[feature_cols]
    X_calib = X_calib[feature_cols]
    X_test = X_test[feature_cols]

    train_dates = df.loc[train_mask, "tourney_date"]
    sample_weights = compute_sample_weights(train_dates, half_life_years=args.half_life_years)

    # 1. Baseline par défaut
    print("\nÉvaluation du baseline (paramètres par défaut)...")
    base_model = xgb.XGBClassifier(
        n_estimators=600,
        eval_metric="logloss",
        early_stopping_rounds=30,
        n_jobs=-1,
        tree_method="hist",
        **DEFAULT_PARAMS
    )
    base_model.fit(
        X_train, y_train,
        sample_weight=sample_weights,
        eval_set=[(X_calib, y_calib)],
        verbose=False
    )
    p_base_calib = base_model.predict_proba(X_calib)[:, 1]
    p_base_test = base_model.predict_proba(X_test)[:, 1]
    base_loss_calib = log_loss(y_calib, p_base_calib)
    base_loss_test = log_loss(y_test, p_base_test)
    base_acc_test = accuracy_score(y_test, p_base_test > 0.5)

    print(f"Baseline Calib LogLoss : {base_loss_calib:.4f}")
    print(f"Baseline Test LogLoss  : {base_loss_test:.4f} | Accuracy: {base_acc_test:.2%}")

    # 2. Étude Optuna
    print(f"\nLancement de l'optimisation bayésienne ({args.n_trials} itérations)...")
    sampler = optuna.samplers.TPESampler(seed=42)
    study = optuna.create_study(direction="minimize", sampler=sampler)

    def print_callback(study, trial):
        print(f"  Trial {trial.number+1:>2}/{args.n_trials} | LogLoss: {trial.value:.4f} (Best: {study.best_value:.4f})")

    study.optimize(
        lambda t: objective(t, X_train, y_train, sample_weights, X_calib, y_calib),
        n_trials=args.n_trials,
        callbacks=[print_callback]
    )

    best_params = study.best_params
    print("\n" + "=" * 65)
    print("  MEILLEURS HYPERPARAMÈTRES TROUVÉS :")
    print("=" * 65)
    for k, v in sorted(best_params.items()):
        print(f"  • {k:<20} : {v}")

    # 3. Évaluation du modèle optimisé
    best_model = xgb.XGBClassifier(
        n_estimators=800,
        eval_metric="logloss",
        early_stopping_rounds=30,
        n_jobs=-1,
        tree_method="hist",
        **best_params
    )
    best_model.fit(
        X_train, y_train,
        sample_weight=sample_weights,
        eval_set=[(X_calib, y_calib)],
        verbose=False
    )
    p_opt_test = best_model.predict_proba(X_test)[:, 1]
    opt_loss_test = log_loss(y_test, p_opt_test)
    opt_acc_test = accuracy_score(y_test, p_opt_test > 0.5)
    opt_auc_test = roc_auc_score(y_test, p_opt_test)

    print("\n--- Comparaison Test Set (6 derniers mois) ---")
    print(f"  Avant : Log Loss = {base_loss_test:.4f} | Accuracy = {base_acc_test:.2%}")
    print(f"  Après : Log Loss = {opt_loss_test:.4f} | Accuracy = {opt_acc_test:.2%} | AUC = {opt_auc_test:.4f}")

    # 4. Sauvegarde
    out_path = PROCESSED_DIR / f"best_params_{args.circuit}.json"
    with open(out_path, "w") as f:
        json.dump(best_params, f, indent=2)
    print(f"\n[OK] Parametres optimaux sauvegardes dans : {out_path}")
    print("Ces parametres seront automatiquement utilises lors du prochain appel a 03_train_model.py !")


if __name__ == "__main__":
    main()
