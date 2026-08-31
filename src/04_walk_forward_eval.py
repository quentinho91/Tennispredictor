"""
04_walk_forward_eval.py — Évaluation Temporelle Glissante (Walk-Forward Temporal Backtesting)
Mesure la stabilité, la calibration et la robustesse du modèle année par année.

USAGE :
    python src/04_walk_forward_eval.py --circuit atp --start-year 2021 --end-year 2024
    python src/04_walk_forward_eval.py --circuit wta --start-year 2022 --end-year 2024
"""

import os
import sys
import json
import argparse
from pathlib import Path
import numpy as np
import pandas as pd
from sklearn.metrics import log_loss, brier_score_loss, accuracy_score, roc_auc_score
from sklearn.linear_model import LogisticRegression
from sklearn.isotonic import IsotonicRegression

import xgboost as xgb
import lightgbm as lgb
from catboost import CatBoostClassifier

# Fix Windows console encoding
try:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

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
compute_sample_weights = tm.compute_sample_weights
filter_features = tm.filter_features
load_best_params = tm.load_best_params
calibrate_predictions = tm.calibrate_predictions
evaluate = tm.evaluate


def compute_ece(y_true, p_pred, n_bins=10):
    """Calcule l'Expected Calibration Error (ECE)."""
    p_clamped = np.clip(p_pred, 1e-6, 1.0 - 1e-6)
    bin_edges = np.linspace(0.0, 1.0, n_bins + 1)
    ece = 0.0
    total_samples = len(y_true)

    for i in range(n_bins):
        lo, hi = bin_edges[i], bin_edges[i + 1]
        mask = (p_clamped >= lo) & (p_clamped < hi if i < n_bins - 1 else p_clamped <= hi)
        n_in_bin = mask.sum()
        if n_in_bin > 0:
            bin_acc = y_true[mask].mean()
            bin_conf = p_clamped[mask].mean()
            ece += (n_in_bin / total_samples) * abs(bin_acc - bin_conf)

    return ece


def walk_forward_split(df, test_year, calib_months=12):
    """
    Split temporel strict pour une année de test donnée :
    - Test       : toute l'année test_year (du 01/01 au 31/12)
    - Calib-only : 3 derniers mois avant test_year
    - Early-stop : 9 mois antérieurs
    - Train      : tout l'historique antérieur
    """
    test_start = pd.to_datetime(f"{test_year}-01-01")
    test_end = pd.to_datetime(f"{test_year}-12-31 23:59:59")
    calib_start = test_start - pd.DateOffset(months=calib_months)
    calib_only_start = test_start - pd.DateOffset(months=3)

    train_mask = df["tourney_date"] < calib_start
    early_stop_mask = (df["tourney_date"] >= calib_start) & (df["tourney_date"] < calib_only_start)
    calib_only_mask = (df["tourney_date"] >= calib_only_start) & (df["tourney_date"] < test_start)
    calib_mask = (df["tourney_date"] >= calib_start) & (df["tourney_date"] < test_start)
    test_mask = (df["tourney_date"] >= test_start) & (df["tourney_date"] <= test_end)

    return train_mask, early_stop_mask, calib_only_mask, calib_mask, test_mask


def run_walk_forward(circuit="atp", start_year=2021, end_year=2024, calib_months=12, half_life_years=7.0, fast=False):
    print("=" * 85)
    print(f"  ÉVALUATION TEMPORELLE GLISSANTE (WALK-FORWARD) — CIRCUIT {circuit.upper()}")
    print(f"  Période de test : {start_year} à {end_year} | Calibration : {calib_months} mois")
    print("=" * 85)

    df = load_data(circuit=circuit)
    X, y, feature_cols_all = prepare_xy(df)

    params_xgb = load_best_params(circuit=circuit, model_type="xgb")
    params_lgb = load_best_params(circuit=circuit, model_type="lgb")
    params_cat = load_best_params(circuit=circuit, model_type="cat")

    n_est = 350 if fast else 700
    early_stop_r = 25 if fast else 30

    yearly_metrics = []

    for test_year in range(start_year, end_year + 1):
        print(f"\n>>> Évaluation de l'année {test_year}...")
        train_mask, early_stop_mask, calib_only_mask, calib_mask, test_mask = walk_forward_split(
            df, test_year, calib_months=calib_months
        )

        n_test = test_mask.sum()
        if n_test == 0:
            print(f"  [Ignoré] Aucun match trouvé pour l'année {test_year}")
            continue

        print(f"  • Train : {train_mask.sum():,} | Calib : {calib_mask.sum():,} | Test ({test_year}) : {n_test:,} matchs")

        # Feature selection on historical train only
        X_train_raw = X[train_mask]
        y_train = y[train_mask]
        selected_cols = filter_features(X_train_raw, y_train, feature_cols_all, correlation_threshold=0.95)

        X_train = X_train_raw[selected_cols]
        X_early_stop = X[early_stop_mask][selected_cols]
        y_early_stop = y[early_stop_mask]
        X_calib_only = X[calib_only_mask][selected_cols]
        y_calib_only = y[calib_only_mask]
        X_test = X[test_mask][selected_cols]
        y_test = y[test_mask].values

        train_dates = df.loc[train_mask, "tourney_date"]
        sample_weights = compute_sample_weights(train_dates, half_life_years=half_life_years)

        # Baseline Elo seul
        lr_base = LogisticRegression()
        lr_base.fit(X_train[["elo_diff"]].fillna(0), y_train)
        p_base_test = lr_base.predict_proba(X_test[["elo_diff"]].fillna(0))[:, 1]
        ll_base = log_loss(y_test, np.clip(p_base_test, 1e-6, 1.0 - 1e-6))
        acc_base = accuracy_score(y_test, p_base_test > 0.5)

        # 1. XGBoost
        xgb_m = xgb.XGBClassifier(
            n_estimators=n_est, eval_metric="logloss", early_stopping_rounds=early_stop_r,
            n_jobs=-1, tree_method="hist", **params_xgb
        )
        xgb_m.fit(X_train, y_train, sample_weight=sample_weights, eval_set=[(X_early_stop, y_early_stop)], verbose=False)
        p_xgb_calib_only = xgb_m.predict_proba(X_calib_only)[:, 1]
        p_xgb_test = xgb_m.predict_proba(X_test)[:, 1]

        # 2. LightGBM
        lgb_m = lgb.LGBMClassifier(
            n_estimators=n_est, random_state=42, n_jobs=-1, verbose=-1, **params_lgb
        )
        lgb_m.fit(X_train, y_train, sample_weight=sample_weights, eval_set=[(X_early_stop, y_early_stop)], callbacks=[lgb.early_stopping(early_stop_r, verbose=False)])
        p_lgb_calib_only = lgb_m.predict_proba(X_calib_only)[:, 1]
        p_lgb_test = lgb_m.predict_proba(X_test)[:, 1]

        # 3. CatBoost
        cat_m = CatBoostClassifier(
            iterations=n_est, random_seed=42, early_stopping_rounds=early_stop_r,
            verbose=False, thread_count=-1, **params_cat
        )
        cat_m.fit(X_train, y_train, sample_weight=sample_weights, eval_set=(X_early_stop, y_early_stop))
        p_cat_calib_only = cat_m.predict_proba(X_calib_only)[:, 1]
        p_cat_test = cat_m.predict_proba(X_test)[:, 1]

        # 4. Meta-Learner (Simple Blending weights)
        M_calib_only = np.column_stack([p_xgb_calib_only, p_lgb_calib_only, p_cat_calib_only])
        M_test = np.column_stack([p_xgb_test, p_lgb_test, p_cat_test])

        meta_lr = LogisticRegression(C=1.0, fit_intercept=True, random_state=42)
        meta_lr.fit(M_calib_only, y_calib_only)
        p_ens_calib_raw = meta_lr.predict_proba(M_calib_only)[:, 1]
        p_ens_test_raw = meta_lr.predict_proba(M_test)[:, 1]

        # 5. Calibration
        p_ens_test_calib, _, best_cal_name = calibrate_predictions(
            p_ens_calib_raw, y_calib_only, p_ens_test_raw, label_prefix=f"{test_year}"
        )

        # Metrics
        p_final = np.clip(p_ens_test_calib, 1e-6, 1.0 - 1e-6)
        ll = log_loss(y_test, p_final)
        brier = brier_score_loss(y_test, p_final)
        acc = accuracy_score(y_test, p_final > 0.5)
        auc = roc_auc_score(y_test, p_final)
        ece = compute_ece(y_test, p_final)

        # XGB solo metrics for comparison
        ll_xgb = log_loss(y_test, np.clip(p_xgb_test, 1e-6, 1.0 - 1e-6))
        acc_xgb = accuracy_score(y_test, p_xgb_test > 0.5)

        row = {
            "year": test_year,
            "n_matches": n_test,
            "log_loss_base": round(ll_base, 4),
            "acc_base": round(acc_base * 100, 2),
            "log_loss_xgb": round(ll_xgb, 4),
            "acc_xgb": round(acc_xgb * 100, 2),
            "log_loss_ensemble": round(ll, 4),
            "brier": round(brier, 4),
            "accuracy": round(acc * 100, 2),
            "auc": round(auc, 4),
            "ece": round(ece * 100, 2),
            "best_calib": best_cal_name,
            "ll_gain_vs_base": round((ll_base - ll), 4)
        }
        yearly_metrics.append(row)

        print(f"  -> Résultat {test_year} : LogLoss={ll:.4f} (Base={ll_base:.4f}, Gain={ll_base-ll:+.4f}) | Acc={acc*100:.2f}% | AUC={auc:.4f} | ECE={ece*100:.2f}%")

    # Synthèse générale
    res_df = pd.DataFrame(yearly_metrics)
    print("\n" + "=" * 95)
    print(f"  SYNTHÈSE WALK-FORWARD TEMPORAL BACKTEST ({circuit.upper()} - {start_year} à {end_year})")
    print("=" * 95)
    print(res_df[["year", "n_matches", "log_loss_base", "log_loss_ensemble", "accuracy", "auc", "ece", "ll_gain_vs_base", "best_calib"]].to_string(index=False))

    total_m = res_df["n_matches"].sum()
    weighted_ll = (res_df["log_loss_ensemble"] * res_df["n_matches"]).sum() / total_m
    weighted_acc = (res_df["accuracy"] * res_df["n_matches"]).sum() / total_m
    weighted_auc = (res_df["auc"] * res_df["n_matches"]).sum() / total_m
    weighted_ece = (res_df["ece"] * res_df["n_matches"]).sum() / total_m
    std_ll = res_df["log_loss_ensemble"].std()

    print("-" * 95)
    print(f"MOYENNE PONDÉRÉE : LogLoss = {weighted_ll:.4f} (σ={std_ll:.4f}) | Accuracy = {weighted_acc:.2f}% | AUC = {weighted_auc:.4f} | ECE = {weighted_ece:.2f}%")
    print("=" * 95)

    # Sauvegarde
    out_json = PROCESSED_DIR / f"walk_forward_{circuit}.json"
    out_csv = PROCESSED_DIR / f"walk_forward_{circuit}.csv"
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump(yearly_metrics, f, indent=2)
    res_df.to_csv(out_csv, index=False)
    print(f"\n[OK] Résultats sauvegardés dans : {out_json} et {out_csv}")


def main():
    parser = argparse.ArgumentParser(description="Walk-Forward Temporal Backtesting pour Tennis Predictor.")
    parser.add_argument("--circuit", default="atp", choices=["atp", "wta"], help="Circuit (atp ou wta)")
    parser.add_argument("--start-year", type=int, default=2021, help="Première année de test (défaut: 2021)")
    parser.add_argument("--end-year", type=int, default=2024, help="Dernière année de test (défaut: 2024)")
    parser.add_argument("--calib-months", type=int, default=12, help="Mois de calibration (défaut: 12)")
    parser.add_argument("--half-life-years", type=float, default=7.0, help="Demi-vie temporelle (défaut: 7.0)")
    parser.add_argument("--fast", action="store_true", help="Mode rapide avec moins d'arbres pour tester")
    args = parser.parse_args()

    run_walk_forward(
        circuit=args.circuit,
        start_year=args.start_year,
        end_year=args.end_year,
        calib_months=args.calib_months,
        half_life_years=args.half_life_years,
        fast=args.fast
    )


if __name__ == "__main__":
    main()
