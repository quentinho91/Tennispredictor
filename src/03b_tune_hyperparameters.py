"""
03b_tune_hyperparameters.py — Optimisation Bayésienne des hyperparamètres multi-modèles via Optuna.
Supporte XGBoost, LightGBM et CatBoost.

Minimise directement le Log Loss sur le set d'évaluation.
Sauvegarde les meilleurs hyperparamètres dans :
    - data/processed/best_params_xgb_{circuit}.json (et best_params_{circuit}.json)
    - data/processed/best_params_lgb_{circuit}.json
    - data/processed/best_params_cat_{circuit}.json

USAGE :
    python src/03b_tune_hyperparameters.py --circuit atp --model xgb --n-trials 30
    python src/03b_tune_hyperparameters.py --circuit atp --model lgb --n-trials 30
    python src/03b_tune_hyperparameters.py --circuit atp --model cat --n-trials 30
    python src/03b_tune_hyperparameters.py --circuit atp --model all --n-trials 20
"""

import os
import sys
import json
import argparse
from pathlib import Path
import numpy as np
import pandas as pd
from sklearn.metrics import log_loss, accuracy_score, roc_auc_score
import optuna

import xgboost as xgb
import lightgbm as lgb
from catboost import CatBoostClassifier

# Réduire la verbosité des logs Optuna
optuna.logging.set_verbosity(optuna.logging.WARNING)

BASE_DIR = Path(__file__).resolve().parent.parent
PROCESSED_DIR = BASE_DIR / "data" / "processed"
PROCESSED_DIR.mkdir(parents=True, exist_ok=True)

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

DEFAULT_PARAMS_XGB = dict(
    max_depth=4, learning_rate=0.03, subsample=0.8, colsample_bytree=0.8,
    min_child_weight=5, reg_lambda=1.5, reg_alpha=0.0, gamma=0.0,
)

DEFAULT_PARAMS_LGB = dict(
    learning_rate=0.03, max_depth=5, num_leaves=31, subsample=0.8,
    colsample_bytree=0.8, min_child_samples=25, reg_lambda=2.0, reg_alpha=0.2,
)

DEFAULT_PARAMS_CAT = dict(
    learning_rate=0.03, depth=5, l2_leaf_reg=3.5,
)


def objective_xgb(trial, X_train, y_train, sample_weights, X_early_stop, y_early_stop, X_calib, y_calib):
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
        eval_set=[(X_early_stop, y_early_stop)],
        verbose=False
    )

    p_calib = model.predict_proba(X_calib)[:, 1]
    return log_loss(y_calib, p_calib)


def objective_lgb(trial, X_train, y_train, sample_weights, X_early_stop, y_early_stop, X_calib, y_calib):
    params = {
        "max_depth": trial.suggest_int("max_depth", 3, 8),
        "num_leaves": trial.suggest_int("num_leaves", 15, 63),
        "learning_rate": trial.suggest_float("learning_rate", 0.015, 0.08, log=True),
        "subsample": trial.suggest_float("subsample", 0.65, 0.95),
        "colsample_bytree": trial.suggest_float("colsample_bytree", 0.50, 0.90),
        "min_child_samples": trial.suggest_int("min_child_samples", 10, 50),
        "reg_alpha": trial.suggest_float("reg_alpha", 1e-3, 5.0, log=True),
        "reg_lambda": trial.suggest_float("reg_lambda", 0.5, 15.0, log=True),
        "n_estimators": 800,
        "random_state": 42,
        "n_jobs": -1,
        "verbose": -1
    }

    model = lgb.LGBMClassifier(**params)
    model.fit(
        X_train, y_train,
        sample_weight=sample_weights,
        eval_set=[(X_early_stop, y_early_stop)],
        callbacks=[lgb.early_stopping(30, verbose=False)]
    )

    p_calib = model.predict_proba(X_calib)[:, 1]
    return log_loss(y_calib, p_calib)


def objective_cat(trial, X_train, y_train, sample_weights, X_early_stop, y_early_stop, X_calib, y_calib):
    params = {
        "depth": trial.suggest_int("depth", 3, 7),
        "learning_rate": trial.suggest_float("learning_rate", 0.015, 0.08, log=True),
        "l2_leaf_reg": trial.suggest_float("l2_leaf_reg", 1.0, 10.0, log=True),
        "random_strength": trial.suggest_float("random_strength", 1e-3, 5.0, log=True),
        "bagging_temperature": trial.suggest_float("bagging_temperature", 0.0, 1.0),
        "iterations": 800,
        "early_stopping_rounds": 30,
        "random_seed": 42,
        "thread_count": -1,
        "verbose": False
    }

    model = CatBoostClassifier(**params)
    model.fit(
        X_train, y_train,
        sample_weight=sample_weights,
        eval_set=(X_early_stop, y_early_stop)
    )

    p_calib = model.predict_proba(X_calib)[:, 1]
    return log_loss(y_calib, p_calib)


def tune_model(model_name, X_train, y_train, sample_weights, X_early_stop, y_early_stop, X_calib, y_calib, X_test, y_test, n_trials=30, circuit="atp"):
    name_upper = model_name.upper()
    print("\n" + "=" * 65)
    print(f"  OPTIMISATION BAYÉSIENNE {name_upper} ({circuit.upper()})")
    print(f"  Objectif : Minimiser le Log Loss ({n_trials} essais)")
    print("=" * 65)

    if model_name == "xgb":
        objective_fn = objective_xgb
        base_model = xgb.XGBClassifier(
            n_estimators=600, eval_metric="logloss", early_stopping_rounds=30,
            n_jobs=-1, tree_method="hist", **DEFAULT_PARAMS_XGB
        )
        base_fit_kwargs = dict(eval_set=[(X_early_stop, y_early_stop)], verbose=False)
    elif model_name == "lgb":
        objective_fn = objective_lgb
        base_model = lgb.LGBMClassifier(
            n_estimators=600, random_state=42, n_jobs=-1, verbose=-1, **DEFAULT_PARAMS_LGB
        )
        base_fit_kwargs = dict(eval_set=[(X_early_stop, y_early_stop)], callbacks=[lgb.early_stopping(30, verbose=False)])
    elif model_name == "cat":
        objective_fn = objective_cat
        base_model = CatBoostClassifier(
            iterations=600, random_seed=42, early_stopping_rounds=30,
            verbose=False, thread_count=-1, **DEFAULT_PARAMS_CAT
        )
        base_fit_kwargs = dict(eval_set=(X_early_stop, y_early_stop))
    else:
        raise ValueError(f"Modèle inconnu : {model_name}")

    # Baseline
    print(f"\nÉvaluation baseline ({model_name.upper()} paramètres par défaut)...")
    base_model.fit(X_train, y_train, sample_weight=sample_weights, **base_fit_kwargs)
    p_base_test = base_model.predict_proba(X_test)[:, 1]
    base_loss_test = log_loss(y_test, p_base_test)
    base_acc_test = accuracy_score(y_test, p_base_test > 0.5)
    print(f"Baseline Test LogLoss : {base_loss_test:.4f} | Accuracy: {base_acc_test:.2%}")

    # Optuna
    sampler = optuna.samplers.TPESampler(seed=42)
    study = optuna.create_study(direction="minimize", sampler=sampler)

    def print_callback(study, trial):
        print(f"  Trial {trial.number+1:>2}/{n_trials} | LogLoss: {trial.value:.4f} (Best: {study.best_value:.4f})")

    study.optimize(
        lambda t: objective_fn(t, X_train, y_train, sample_weights, X_early_stop, y_early_stop, X_calib, y_calib),
        n_trials=n_trials,
        callbacks=[print_callback]
    )

    best_params = study.best_params
    print("\n" + "-" * 50)
    print(f"  Meilleurs hyperparamètres {name_upper} :")
    print("-" * 50)
    for k, v in sorted(best_params.items()):
        print(f"  • {k:<20} : {v}")

    # Évaluation optimisée
    if model_name == "xgb":
        best_model = xgb.XGBClassifier(
            n_estimators=800, eval_metric="logloss", early_stopping_rounds=30,
            n_jobs=-1, tree_method="hist", **best_params
        )
        best_fit_kwargs = dict(eval_set=[(X_early_stop, y_early_stop)], verbose=False)
    elif model_name == "lgb":
        best_model = lgb.LGBMClassifier(
            n_estimators=800, random_state=42, n_jobs=-1, verbose=-1, **best_params
        )
        best_fit_kwargs = dict(eval_set=[(X_early_stop, y_early_stop)], callbacks=[lgb.early_stopping(30, verbose=False)])
    else:
        best_model = CatBoostClassifier(
            iterations=800, random_seed=42, early_stopping_rounds=30,
            verbose=False, thread_count=-1, **best_params
        )
        best_fit_kwargs = dict(eval_set=(X_early_stop, y_early_stop))

    best_model.fit(X_train, y_train, sample_weight=sample_weights, **best_fit_kwargs)
    p_opt_test = best_model.predict_proba(X_test)[:, 1]
    opt_loss_test = log_loss(y_test, p_opt_test)
    opt_acc_test = accuracy_score(y_test, p_opt_test > 0.5)
    opt_auc_test = roc_auc_score(y_test, p_opt_test)

    print(f"\n--- Comparaison Test Set ({name_upper}) ---")
    print(f"  Avant : Log Loss = {base_loss_test:.4f} | Accuracy = {base_acc_test:.2%}")
    print(f"  Après : Log Loss = {opt_loss_test:.4f} | Accuracy = {opt_acc_test:.2%} | AUC = {opt_auc_test:.4f}")

    # Sauvegarde
    out_path = PROCESSED_DIR / f"best_params_{model_name}_{circuit}.json"
    with open(out_path, "w") as f:
        json.dump(best_params, f, indent=2)
    print(f"[OK] Paramètres sauvegardés dans : {out_path}")

    if model_name == "xgb":
        # Rétrocompatibilité
        legacy_path = PROCESSED_DIR / f"best_params_{circuit}.json"
        with open(legacy_path, "w") as f:
            json.dump(best_params, f, indent=2)

    return best_params


def main():
    parser = argparse.ArgumentParser(description="Optimisation bayésienne multi-modèles pour Tennis Predictor.")
    parser.add_argument("--circuit", default="atp", choices=["atp", "wta"], help="Circuit (atp ou wta)")
    parser.add_argument("--model", default="all", choices=["xgb", "lgb", "cat", "all"], help="Modèle à optimiser (xgb, lgb, cat, all)")
    parser.add_argument("--n-trials", type=int, default=30, help="Nombre de tirages Optuna (défaut: 30)")
    parser.add_argument("--half-life-years", type=float, default=7.0, help="Demi-vie pour sample weights")
    args = parser.parse_args()

    df = load_data(circuit=args.circuit)
    X, y, feature_cols = prepare_xy(df)

    (X_train, y_train, train_mask,
     X_early_stop, y_early_stop,
     X_calib_only, y_calib_only,
     X_calib, y_calib,
     X_test, y_test, df_test) = dynamic_temporal_split(df, X, y)

    feature_cols = filter_features(X_train, y_train, feature_cols, correlation_threshold=0.95)
    X_train = X_train[feature_cols]
    X_early_stop = X_early_stop[feature_cols]
    X_calib = X_calib[feature_cols]
    X_test = X_test[feature_cols]

    train_dates = df.loc[train_mask, "tourney_date"]
    sample_weights = compute_sample_weights(train_dates, half_life_years=args.half_life_years)

    models_to_tune = ["xgb", "lgb", "cat"] if args.model == "all" else [args.model]
    for m in models_to_tune:
        tune_model(
            m, X_train, y_train, sample_weights,
            X_early_stop, y_early_stop, X_calib, y_calib, X_test, y_test,
            n_trials=args.n_trials, circuit=args.circuit
        )

    print("\n[OK] Optimisation terminée avec succès !")


if __name__ == "__main__":
    main()
