"""
03_train_model.py — Entraînement et Stacking Multi-Modèles (XGBoost + LightGBM + CatBoost)
avec Meta-Learner, split temporel glissant, pondération temporelle et calibration.

Points clés :
1. SPLIT TEMPOREL GLISSANT DYNAMIQUE :
   - Test  : les 6 derniers mois (ex: de M-6 à aujourd'hui)
   - Calib : les 12 mois précédents (ex: de M-18 à M-6)
   - Train : tout l'historique antérieur (ex: 2000 jusqu'à M-18)

2. ENSEMBLE & STACKING MULTI-MODÈLES :
   - Modèle 1 : XGBoost Classifier (arbres depth-wise régularisés)
   - Modèle 2 : LightGBM Classifier (arbres leaf-wise rapides avec forte diversité)
   - Modèle 3 : CatBoost Classifier (optimisé pour les patterns non-linéaires complexes)
   - Méta-Learner : Régression Logistique sur les probabilités hors-échantillon (OOF / Calib)

3. CALIBRATION PROBABILISTE OPTIMALE (Isotonic / Temperature Scaling / Bucket)
4. SAUVEGARDE DE L'ENSEMBLE COMPLET dans data/processed/ensemble_{circuit}.pkl
"""

import os
import sys
import json
import joblib
import argparse
from pathlib import Path

# Fix Windows console encoding
try:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import numpy as np
import pandas as pd
from scipy.optimize import minimize_scalar
from sklearn.metrics import log_loss, brier_score_loss, accuracy_score, roc_auc_score
from sklearn.isotonic import IsotonicRegression
from sklearn.model_selection import KFold
from sklearn.linear_model import LogisticRegression

import xgboost as xgb
import lightgbm as lgb
from catboost import CatBoostClassifier

BASE_DIR = Path(__file__).resolve().parent.parent
PROCESSED_DIR = BASE_DIR / "data" / "processed"
PROCESSED_DIR.mkdir(parents=True, exist_ok=True)


def get_parser():
    parser = argparse.ArgumentParser(description="Entraînement Stacking Multi-Modèles (XGBoost + LightGBM + CatBoost).")
    parser.add_argument("--circuit", default="atp", choices=["atp", "wta"], help="Circuit à traiter (atp ou wta)")
    parser.add_argument("--calib-months", type=int, default=12, help="Nombre de mois pour la calibration (défaut: 12)")
    parser.add_argument("--test-months", type=int, default=6, help="Nombre de mois pour le test (défaut: 6)")
    parser.add_argument("--ref-date", default=None, help="Date de référence (AAAA-MM-JJ). Défaut: date max du dataset.")
    parser.add_argument("--half-life-years", type=float, default=7.0, help="Demi-vie en années pour la pondération temporelle (0 pour désactiver, défaut: 7.0)")
    parser.add_argument("--oof-splits", type=int, default=5, help="Nombre de plis pour les prédictions Out-Of-Fold du meta-learner (défaut: 5, 0 pour désactiver)")
    return parser


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

DEFAULT_PARAMS = DEFAULT_PARAMS_XGB


def load_best_params(circuit="atp", model_type="xgb"):
    path_specific = PROCESSED_DIR / f"best_params_{model_type}_{circuit}.json"
    path_legacy = PROCESSED_DIR / f"best_params_{circuit}.json"

    if path_specific.exists():
        try:
            with open(path_specific) as f:
                params = json.load(f)
            print(f"Hyperparamètres {model_type.upper()} chargés depuis {path_specific}")
            return params
        except Exception:
            pass

    if model_type == "xgb" and path_legacy.exists():
        try:
            with open(path_legacy) as f:
                params = json.load(f)
            print(f"Hyperparamètres XGB chargés depuis {path_legacy}")
            return params
        except Exception:
            pass

    if model_type == "lgb":
        return DEFAULT_PARAMS_LGB
    elif model_type == "cat":
        return DEFAULT_PARAMS_CAT
    return DEFAULT_PARAMS_XGB


def load_data(circuit="atp"):
    in_path = PROCESSED_DIR / f"features_{circuit}.parquet"
    if not in_path.exists():
        raise FileNotFoundError(f"{in_path} introuvable. Lance d'abord: python src/02_feature_engineering.py --circuit {circuit}")
    df = pd.read_parquet(in_path)
    df = df[~df["retirement"]]  # On retire les matchs abandonnés
    df = df.sort_values("tourney_date").reset_index(drop=True)
    return df


def prepare_xy(df):
    cat_cols = ["surface", "tourney_level", "round", "hand_matchup", "indoor"]
    for c in cat_cols:
        if c in df.columns:
            df[c] = df[c].replace("nan", np.nan)
    df = pd.get_dummies(df, columns=cat_cols, dummy_na=True, dtype=float)

    drop_cols = ["match_id", "tourney_date", "target", "retirement"]
    feature_cols = [c for c in df.columns if c not in drop_cols]

    X = df[feature_cols]
    y = df["target"]
    return X, y, feature_cols


def dynamic_temporal_split(df, X, y, calib_months=12, test_months=6, ref_date=None):
    """
    Découpage temporel glissant dynamique :
    - Train      : tout l'historique jusqu'à (ref_date - calib_months - test_months)
    - Early-stop : les premiers 75% de la fenêtre calib (pour early stopping + stacking)
    - Calib-only : les derniers 25% de la fenêtre calib (exclusivement pour la calibration)
    - Test       : les test_months les plus récents

    NOTE : séparer early-stop et calibration limite le "double-dipping" où
    le même sous-ensemble servait à l'early stopping des 3 modèles, au
    stacking, ET au choix de calibration, créant un risque d'optimisme
    structurel corrélé.
    """
    if ref_date is None:
        ref_date = df["tourney_date"].max()
    else:
        ref_date = pd.to_datetime(ref_date)

    test_start = ref_date - pd.DateOffset(months=test_months)
    calib_start = test_start - pd.DateOffset(months=calib_months)
    # Split calib into early_stop (75%) and calib_only (25%)
    calib_only_months = max(3, calib_months // 4)
    calib_only_start = test_start - pd.DateOffset(months=calib_only_months)

    train_mask = df["tourney_date"] < calib_start
    early_stop_mask = (df["tourney_date"] >= calib_start) & (df["tourney_date"] < calib_only_start)
    calib_only_mask = (df["tourney_date"] >= calib_only_start) & (df["tourney_date"] < test_start)
    # Full calib = early_stop + calib_only (for backward compatibility)
    calib_mask = (df["tourney_date"] >= calib_start) & (df["tourney_date"] < test_start)
    test_mask = df["tourney_date"] >= test_start

    print(f"\n--- Découpage Temporel Glissant (Réf: {ref_date.strftime('%Y-%m-%d')}) ---")
    print(f"  • Train      : < {calib_start.strftime('%Y-%m-%d')} ({train_mask.sum():,} matchs)")
    print(f"  • Early-stop : {calib_start.strftime('%Y-%m-%d')} à {calib_only_start.strftime('%Y-%m-%d')} ({early_stop_mask.sum():,} matchs)")
    print(f"  • Calib-only : {calib_only_start.strftime('%Y-%m-%d')} à {test_start.strftime('%Y-%m-%d')} ({calib_only_mask.sum():,} matchs)")
    print(f"  • Test       : >= {test_start.strftime('%Y-%m-%d')} ({test_mask.sum():,} matchs)")

    return (X[train_mask], y[train_mask], train_mask,
            X[early_stop_mask], y[early_stop_mask],
            X[calib_only_mask], y[calib_only_mask],
            X[calib_mask], y[calib_mask],
            X[test_mask], y[test_mask],
            df.loc[test_mask])


def compute_sample_weights(train_dates, half_life_years=7.0, min_weight=0.10):
    """
    Calcule des poids d'échantillons exponentiellement décroissants selon l'ancienneté du match.
    """
    if half_life_years is None or half_life_years <= 0:
        return None
    max_date = train_dates.max()
    days_ago = (max_date - pd.to_datetime(train_dates)).dt.total_seconds() / (24 * 3600.0)
    half_life_days = half_life_years * 365.25
    weights = np.exp(-np.log(2) * (days_ago / half_life_days))
    weights = np.clip(weights, min_weight, 1.0)
    return weights.values


def filter_features(X_train, y_train, feature_cols, correlation_threshold=0.95):
    """
    Supprime automatiquement les features redondantes et collinéaires.
    """
    stds = X_train.std(numeric_only=True)
    zero_var = set(stds[stds == 0].index.tolist())

    sample_size = min(len(X_train), 50000)
    sample_idx = X_train.sample(sample_size, random_state=42).index
    sample_X = X_train.loc[sample_idx].fillna(0)
    sample_y = y_train.loc[sample_idx]

    target_corr = {}
    for col in sample_X.columns:
        if col in zero_var:
            target_corr[col] = 0.0
            continue
        try:
            r = np.abs(np.corrcoef(sample_X[col], sample_y)[0, 1])
            target_corr[col] = 0.0 if np.isnan(r) else r
        except Exception:
            target_corr[col] = 0.0

    corr_matrix = sample_X.corr().abs()
    to_drop_corr = set()
    cols = list(corr_matrix.columns)
    for i in range(len(cols)):
        c1 = cols[i]
        if c1 in to_drop_corr or c1 in zero_var:
            continue
        for j in range(i + 1, len(cols)):
            c2 = cols[j]
            if c2 in to_drop_corr or c2 in zero_var:
                continue
            if corr_matrix.iloc[i, j] > correlation_threshold:
                if target_corr.get(c1, 0) >= target_corr.get(c2, 0):
                    to_drop_corr.add(c2)
                else:
                    to_drop_corr.add(c1)
                    break

    all_dropped = zero_var | to_drop_corr
    selected = [c for c in feature_cols if c not in all_dropped]
    if all_dropped:
        print(f"\nNettoyage des features : {len(feature_cols)} -> {len(selected)} features retenues ({len(all_dropped)} supprimées)")
    return selected


def evaluate(y_true, p_pred, label):
    p_clamped = np.clip(p_pred, 1e-6, 1.0 - 1e-6)
    ll = log_loss(y_true, p_clamped)
    brier = brier_score_loss(y_true, p_clamped)
    acc = accuracy_score(y_true, (p_clamped > 0.5).astype(int))
    auc = roc_auc_score(y_true, p_clamped)
    print(f"[{label:<35}] LogLoss={ll:.4f} | Brier={brier:.4f} | Acc={acc*100:.2f}% | AUC={auc:.4f}")
    return {"log_loss": ll, "brier": brier, "accuracy": acc, "auc": auc}


def _fit_isotonic(p_train, y_train):
    """Fit global isotonic regression."""
    cal = IsotonicRegression(out_of_bounds="clip")
    cal.fit(p_train, y_train)
    return cal


def _fit_temperature(p_train, y_train):
    """Fit temperature scaling and return (T_opt, cal_info_dict)."""
    def nll_temp(T):
        logits = np.log(p_train / (1.0 - p_train)) / T
        p_t = 1.0 / (1.0 + np.exp(-logits))
        return log_loss(y_train, np.clip(p_t, 1e-6, 1.0 - 1e-6))
    res_t = minimize_scalar(nll_temp, bounds=(0.5, 3.0), method="bounded")
    T_opt = float(res_t.x)
    return T_opt


def _apply_temperature(p, T_opt):
    """Apply temperature scaling to probabilities."""
    logits = np.log(p / (1.0 - p)) / T_opt
    return np.clip(1.0 / (1.0 + np.exp(-logits)), 0.001, 0.999)


def _fit_bucket(p_train, y_train, bucket_edges, fallback_iso):
    """Fit per-bucket isotonic calibrators."""
    bucket_cals = {}
    for i in range(len(bucket_edges) - 1):
        lo, hi = bucket_edges[i], bucket_edges[i + 1]
        m = (p_train >= lo) & (p_train < hi)
        if m.sum() >= 25:
            b_cal = IsotonicRegression(out_of_bounds="clip")
            b_cal.fit(p_train[m], y_train[m])
            bucket_cals[(lo, hi)] = b_cal
        else:
            bucket_cals[(lo, hi)] = None
    return bucket_cals


def _apply_bucket(p, bucket_cals, bucket_edges, fallback_iso):
    """Apply bucket calibration to probabilities."""
    p_out = np.copy(p)
    for (lo, hi), b_cal in bucket_cals.items():
        m = (p >= lo) & (p < hi)
        if b_cal is not None and m.sum() > 0:
            p_out[m] = b_cal.predict(p[m])
        elif m.sum() > 0:
            p_out[m] = fallback_iso.predict(p[m])
    return np.clip(p_out, 0.001, 0.999)


def calibrate_predictions(p_calib, y_calib, p_test, label_prefix="Ensemble"):
    """
    Calibre les probabilités en comparant Isotonic Regression, Temperature Scaling
    et Bucket Calibration. La sélection de la meilleure méthode se fait par
    validation croisée 3-fold sur le calib set (au lieu d'une évaluation in-sample
    qui favoriserait systématiquement l'Isotonic, plus flexible).
    """
    y_calib_arr = np.array(y_calib)
    p_calib_clamped = np.clip(p_calib, 1e-6, 1.0 - 1e-6)
    p_test_clamped = np.clip(p_test, 1e-6, 1.0 - 1e-6)
    bucket_edges = [0.0, 0.20, 0.35, 0.50, 0.65, 0.80, 1.01]

    # ---------- 1. Cross-validated method selection ----------
    # Evaluate each calibration method via 3-fold CV to avoid in-sample bias
    # (Isotonic can overfit the calib set due to its nonparametric flexibility)
    kf = KFold(n_splits=3, shuffle=True, random_state=42)
    cv_scores = {"global": [], "temperature": [], "bucket": [], "raw": []}

    for fold_train_idx, fold_val_idx in kf.split(p_calib_clamped):
        p_fold_train = p_calib_clamped[fold_train_idx]
        y_fold_train = y_calib_arr[fold_train_idx]
        p_fold_val = p_calib_clamped[fold_val_idx]
        y_fold_val = y_calib_arr[fold_val_idx]

        # Raw (no calibration)
        cv_scores["raw"].append(log_loss(y_fold_val, p_fold_val))

        # Global Isotonic
        fold_iso = _fit_isotonic(p_fold_train, y_fold_train)
        p_iso_val = np.clip(fold_iso.predict(p_fold_val), 0.001, 0.999)
        cv_scores["global"].append(log_loss(y_fold_val, p_iso_val))

        # Temperature Scaling
        T_fold = _fit_temperature(p_fold_train, y_fold_train)
        p_temp_val = _apply_temperature(p_fold_val, T_fold)
        cv_scores["temperature"].append(log_loss(y_fold_val, p_temp_val))

        # Bucket Calibration
        fold_iso_fallback = fold_iso  # reuse fold isotonic as fallback
        fold_bucket_cals = _fit_bucket(p_fold_train, y_fold_train, bucket_edges, fold_iso_fallback)
        p_bucket_val = _apply_bucket(p_fold_val, fold_bucket_cals, bucket_edges, fold_iso_fallback)
        cv_scores["bucket"].append(log_loss(y_fold_val, p_bucket_val))

    # Print CV results
    print(f"\n  Calibration CV (3-fold) log-loss :")
    for method, scores in cv_scores.items():
        mean_score = np.mean(scores)
        print(f"    {method:<15} : {mean_score:.4f} (±{np.std(scores):.4f})")

    best_name = min(cv_scores, key=lambda k: np.mean(cv_scores[k]))

    # ---------- 2. Refit best method on full calib set, apply to test ----------
    # Global isotonic (always needed as bucket fallback)
    cal_iso = _fit_isotonic(p_calib_clamped, y_calib_arr)

    if best_name == "global":
        p_best_test = np.clip(cal_iso.predict(p_test_clamped), 0.001, 0.999)
        best_cal_obj = cal_iso
        evaluate(y_calib_arr, cal_iso.predict(p_calib_clamped), f"{label_prefix} + Iso (Calib)")
    elif best_name == "temperature":
        T_opt = _fit_temperature(p_calib_clamped, y_calib_arr)
        p_best_test = _apply_temperature(p_test_clamped, T_opt)
        best_cal_obj = {"type": "temperature_scaling", "temperature": T_opt}
        evaluate(y_calib_arr, _apply_temperature(p_calib_clamped, T_opt), f"{label_prefix} + Temp (T={T_opt:.2f})")
    elif best_name == "bucket":
        bucket_cals = _fit_bucket(p_calib_clamped, y_calib_arr, bucket_edges, cal_iso)
        p_best_test = _apply_bucket(p_test_clamped, bucket_cals, bucket_edges, cal_iso)
        best_cal_obj = {"type": "bucket", "calibrators": bucket_cals, "bucket_edges": bucket_edges, "fallback": cal_iso}
        # Compute and display actual bucket log-loss on calib set
        p_bucket_calib = _apply_bucket(p_calib_clamped, bucket_cals, bucket_edges, cal_iso)
        evaluate(y_calib_arr, p_bucket_calib, f"{label_prefix} + Bucket (Calib)")
    else:  # raw
        p_best_test = p_test_clamped
        best_cal_obj = None
        evaluate(y_calib_arr, p_calib_clamped, f"{label_prefix} + Raw (Calib)")

    return p_best_test, best_cal_obj, best_name


def generate_oof_predictions(X_train, y_train, sample_weights, xgb_params, lgb_params, cat_params, n_splits=5):
    """
    Génère de véritables prédictions Out-Of-Fold (OOF) pour les 3 modèles de base
    sur l'ensemble d'entraînement X_train via K-Fold CV.
    """
    print(f"\n--- Génération des métadonnées OOF Stacking ({n_splits}-Fold CV sur {len(X_train):,} matchs) ---")
    kf = KFold(n_splits=n_splits, shuffle=True, random_state=42)
    oof_xgb = np.zeros(len(X_train))
    oof_lgb = np.zeros(len(X_train))
    oof_cat = np.zeros(len(X_train))

    y_arr = np.array(y_train)
    X_mat = X_train.values

    for fold, (train_idx, val_idx) in enumerate(kf.split(X_mat, y_arr), 1):
        X_tr, y_tr = X_train.iloc[train_idx], y_train.iloc[train_idx]
        X_va, y_va = X_train.iloc[val_idx], y_train.iloc[val_idx]
        w_tr = sample_weights[train_idx] if sample_weights is not None else None

        # XGBoost
        m_xgb = xgb.XGBClassifier(
            n_estimators=700, eval_metric="logloss", early_stopping_rounds=30,
            n_jobs=-1, tree_method="hist", **xgb_params
        )
        m_xgb.fit(X_tr, y_tr, sample_weight=w_tr, eval_set=[(X_va, y_va)], verbose=False)
        oof_xgb[val_idx] = m_xgb.predict_proba(X_va)[:, 1]

        # LightGBM
        m_lgb = lgb.LGBMClassifier(
            n_estimators=700, random_state=42, n_jobs=-1, verbose=-1, **lgb_params
        )
        m_lgb.fit(X_tr, y_tr, sample_weight=w_tr, eval_set=[(X_va, y_va)], callbacks=[lgb.early_stopping(30, verbose=False)])
        oof_lgb[val_idx] = m_lgb.predict_proba(X_va)[:, 1]

        # CatBoost
        m_cat = CatBoostClassifier(
            iterations=700, random_seed=42, early_stopping_rounds=30,
            verbose=False, thread_count=-1, **cat_params
        )
        m_cat.fit(X_tr, y_tr, sample_weight=w_tr, eval_set=(X_va, y_va))
        oof_cat[val_idx] = m_cat.predict_proba(X_va)[:, 1]

        print(f"  • Pli {fold}/{n_splits} OOF calculé")

    M_oof = np.column_stack([oof_xgb, oof_lgb, oof_cat])
    return M_oof


if __name__ == "__main__":
    args = get_parser().parse_args()
    print(f"\n{'='*75}")
    print(f"  ENTRAÎNEMENT STACKING MULTI-MODÈLES ({args.circuit.upper()})")
    print(f"  Architectures : XGBoost + LightGBM + CatBoost + Méta-Learner Stacking OOF")
    print(f"{'='*75}")

    df = load_data(circuit=args.circuit)
    X, y, feature_cols = prepare_xy(df)

    # Découpage temporel glissant
    # early_stop : pour early stopping des modèles + stacking (75% de la fenêtre calib)
    # calib_only : exclusivement pour la calibration (25% de la fenêtre calib)
    # calib      : union des deux (pour prédictions OOF du stacking -> calibration)
    (X_train, y_train, train_mask,
     X_early_stop, y_early_stop,
     X_calib_only, y_calib_only,
     X_calib, y_calib,
     X_test, y_test, df_test) = dynamic_temporal_split(
        df, X, y,
        calib_months=args.calib_months,
        test_months=args.test_months,
        ref_date=args.ref_date
    )

    # Nettoyage et sélection des features
    feature_cols = filter_features(X_train, y_train, feature_cols, correlation_threshold=0.95)
    X_train = X_train[feature_cols]
    X_early_stop = X_early_stop[feature_cols]
    X_calib_only = X_calib_only[feature_cols]
    X_calib = X_calib[feature_cols]
    X_test = X_test[feature_cols]

    # Pondération temporelle des échantillons
    train_dates = df.loc[train_mask, "tourney_date"]
    sample_weights = compute_sample_weights(train_dates, half_life_years=args.half_life_years)
    if sample_weights is not None:
        print(f"Pondération temporelle active : demi-vie = {args.half_life_years} ans (poids moyen = {sample_weights.mean():.3f})")

    params_xgb = load_best_params(circuit=args.circuit, model_type="xgb")
    params_lgb = load_best_params(circuit=args.circuit, model_type="lgb")
    params_cat = load_best_params(circuit=args.circuit, model_type="cat")

    # -----------------------------------------------------------------------
    # 1. MODÈLE 1 : XGBoost Classifier
    # -----------------------------------------------------------------------
    print("\n[1/3] Entraînement de XGBoost Classifier...")
    xgb_model = xgb.XGBClassifier(
        n_estimators=700,
        eval_metric="logloss",
        early_stopping_rounds=30,
        n_jobs=-1,
        tree_method="hist",
        **params_xgb,
    )
    xgb_model.fit(
        X_train, y_train,
        sample_weight=sample_weights,
        eval_set=[(X_early_stop, y_early_stop)],
        verbose=False
    )
    p_xgb_calib = xgb_model.predict_proba(X_calib)[:, 1]
    p_xgb_calib_only = xgb_model.predict_proba(X_calib_only)[:, 1]
    p_xgb_test = xgb_model.predict_proba(X_test)[:, 1]

    # -----------------------------------------------------------------------
    # 2. MODÈLE 2 : LightGBM Classifier
    # -----------------------------------------------------------------------
    print("\n[2/3] Entraînement de LightGBM Classifier...")
    lgb_model = lgb.LGBMClassifier(
        n_estimators=700,
        random_state=42,
        n_jobs=-1,
        verbose=-1,
        **params_lgb,
    )
    lgb_model.fit(
        X_train, y_train,
        sample_weight=sample_weights,
        eval_set=[(X_early_stop, y_early_stop)],
        callbacks=[lgb.early_stopping(30, verbose=False)]
    )
    p_lgb_calib = lgb_model.predict_proba(X_calib)[:, 1]
    p_lgb_calib_only = lgb_model.predict_proba(X_calib_only)[:, 1]
    p_lgb_test = lgb_model.predict_proba(X_test)[:, 1]

    # -----------------------------------------------------------------------
    # 3. MODÈLE 3 : CatBoost Classifier
    # -----------------------------------------------------------------------
    print("\n[3/3] Entraînement de CatBoost Classifier...")
    cat_model = CatBoostClassifier(
        iterations=700,
        random_seed=42,
        early_stopping_rounds=30,
        verbose=False,
        thread_count=-1,
        **params_cat,
    )
    cat_model.fit(
        X_train, y_train,
        sample_weight=sample_weights,
        eval_set=(X_early_stop, y_early_stop)
    )
    p_cat_calib = cat_model.predict_proba(X_calib)[:, 1]
    p_cat_calib_only = cat_model.predict_proba(X_calib_only)[:, 1]
    p_cat_test = cat_model.predict_proba(X_test)[:, 1]

    # -----------------------------------------------------------------------
    # 4. MÉTA-LEARNER STACKING (Entraîné sur OOF Train ou Calib)
    # -----------------------------------------------------------------------
    print("\n--- Entraînement du Méta-Learner Stacking (Logistic Regression) ---")
    if args.oof_splits >= 2:
        M_oof_train = generate_oof_predictions(
            X_train, y_train, sample_weights, params_xgb, params_lgb, params_cat, n_splits=args.oof_splits
        )
        meta_learner = LogisticRegression(C=1.0, fit_intercept=True, random_state=42)
        meta_learner.fit(M_oof_train, y_train, sample_weight=sample_weights)
        print("  • Méta-Learner ajusté avec succès sur les prédictions Out-Of-Fold historiques.")
    else:
        M_calib = np.column_stack([p_xgb_calib, p_lgb_calib, p_cat_calib])
        meta_learner = LogisticRegression(C=1.0, fit_intercept=True, random_state=42)
        meta_learner.fit(M_calib, y_calib)
        print("  • Méta-Learner ajusté sur la tranche calib (mode rapide).")

    M_test = np.column_stack([p_xgb_test, p_lgb_test, p_cat_test])
    coefs = meta_learner.coef_[0]
    weights_norm = np.maximum(0, coefs) / (np.maximum(0, coefs).sum() + 1e-12)
    print(f"  • Poids relatifs : XGBoost = {weights_norm[0]*100:.1f}% | LightGBM = {weights_norm[1]*100:.1f}% | CatBoost = {weights_norm[2]*100:.1f}%")

    p_ensemble_test_raw = meta_learner.predict_proba(M_test)[:, 1]

    # -----------------------------------------------------------------------
    # 5. CALIBRATION DE L'ENSEMBLE
    # -----------------------------------------------------------------------
    M_calib_only = np.column_stack([p_xgb_calib_only, p_lgb_calib_only, p_cat_calib_only])
    p_ensemble_calib_only_raw = meta_learner.predict_proba(M_calib_only)[:, 1]

    p_ensemble_test_calib, calibrator_obj, best_calib_name = calibrate_predictions(
        p_ensemble_calib_only_raw, y_calib_only, p_ensemble_test_raw, label_prefix="Ensemble"
    )
    print(f"=> Meilleure calibration retenue : {best_calib_name}")

    # -----------------------------------------------------------------------
    # 6. TABLEAU COMPARATIF DES PERFORMANCES SUR LE TEST SET (6 derniers mois)
    # -----------------------------------------------------------------------
    print("\n" + "=" * 85)
    print(f"  COMPARAISON DES PERFORMANCES SUR LE TEST SET ({args.circuit.upper()} - 6 MOIS)")
    print("=" * 85)
    
    # Baseline
    lr_baseline = LogisticRegression()
    lr_baseline.fit(X_train[["elo_diff"]].fillna(0), y_train)
    p_base_test = lr_baseline.predict_proba(X_test[["elo_diff"]].fillna(0))[:, 1]
    m_base = evaluate(y_test, p_base_test, "Baseline : Elo Seul")

    # Modèles individuels
    m_xgb = evaluate(y_test, p_xgb_test, "Modèle 1 : XGBoost Brut")
    m_lgb = evaluate(y_test, p_lgb_test, "Modèle 2 : LightGBM Brut")
    m_cat = evaluate(y_test, p_cat_test, "Modèle 3 : CatBoost Brut")

    # Ensemble brut et calibré
    m_ens_raw = evaluate(y_test, p_ensemble_test_raw, "Ensemble Stacking (Brut)")
    m_ens_final = evaluate(y_test, p_ensemble_test_calib, f"ENSEMBLE STACKING CALIBRE ({best_calib_name})")
    print("=" * 85)

    # Top Features XGBoost
    importances = pd.Series(xgb_model.feature_importances_, index=feature_cols).sort_values(ascending=False)
    print("\nTop 10 Features (XGBoost) :")
    for f, imp in importances.head(10).items():
        print(f"  • {f:<32} : {imp*100:>5.2f}%")

    # -----------------------------------------------------------------------
    # 7. SAUVEGARDE DES ARTEFACTS (Ensemble complet + fichiers rétro-compatibles)
    # -----------------------------------------------------------------------
    ensemble_bundle = {
        "circuit": args.circuit,
        "xgb_model": xgb_model,
        "lgb_model": lgb_model,
        "cat_model": cat_model,
        "meta_learner": meta_learner,
        "calibrator": calibrator_obj,
        "calibrator_type": best_calib_name,
        "feature_cols": feature_cols,
        "metrics": {
            "baseline": m_base,
            "xgb": m_xgb,
            "lgb": m_lgb,
            "cat": m_cat,
            "ensemble": m_ens_final,
        },
        "weights": {
            "xgb": float(weights_norm[0]),
            "lgb": float(weights_norm[1]),
            "cat": float(weights_norm[2])
        }
    }

    ensemble_path = PROCESSED_DIR / f"ensemble_{args.circuit}.pkl"
    joblib.dump(ensemble_bundle, ensemble_path)
    print(f"\n[OK] Ensemble Stacking sauvegardé dans : {ensemble_path}")

    # Rétro-compatibilité : sauvegarde du modèle XGBoost et du calibrateur individuel
    model_path = PROCESSED_DIR / f"xgb_model_{args.circuit}.json"
    xgb_model.save_model(str(model_path))

    calib_path = PROCESSED_DIR / f"calibrator_{args.circuit}.pkl"
    joblib.dump(calibrator_obj, calib_path)

    fcols_path = PROCESSED_DIR / f"feature_cols_{args.circuit}.pkl"
    joblib.dump(feature_cols, fcols_path)

    df_test = df_test.copy()
    df_test["p_model"] = p_ensemble_test_calib
    df_test["p_xgb"] = p_xgb_test
    df_test["p_lgb"] = p_lgb_test
    df_test["p_cat"] = p_cat_test
    test_pred_path = PROCESSED_DIR / f"test_predictions_{args.circuit}.parquet"
    df_test.to_parquet(test_pred_path, index=False)
    print(f"[OK] Prédictions de test sauvegardées dans : {test_pred_path}")
