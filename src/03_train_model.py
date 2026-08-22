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
    return parser


DEFAULT_PARAMS = dict(
    max_depth=4, learning_rate=0.03, subsample=0.8, colsample_bytree=0.8,
    min_child_weight=5, reg_lambda=1.5, reg_alpha=0.0, gamma=0.0,
)


def load_best_params(circuit="atp"):
    path = PROCESSED_DIR / f"best_params_{circuit}.json"
    if path.exists():
        try:
            with open(path) as f:
                params = json.load(f)
            print(f"Hyperparamètres chargés depuis {path}")
            return params
        except Exception:
            pass
    return DEFAULT_PARAMS


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
    df = pd.get_dummies(df, columns=cat_cols, dummy_na=True)

    drop_cols = ["match_id", "tourney_date", "target", "retirement"]
    feature_cols = [c for c in df.columns if c not in drop_cols]

    X = df[feature_cols]
    y = df["target"]
    return X, y, feature_cols


def dynamic_temporal_split(df, X, y, calib_months=12, test_months=6, ref_date=None):
    """
    Découpage temporel glissant dynamique :
    - Train : tout l'historique jusqu'à (ref_date - calib_months - test_months)
    - Calib : fenêtre de calib_months avant le test set
    - Test  : les test_months les plus récents
    """
    if ref_date is None:
        ref_date = df["tourney_date"].max()
    else:
        ref_date = pd.to_datetime(ref_date)

    test_start = ref_date - pd.DateOffset(months=test_months)
    calib_start = test_start - pd.DateOffset(months=calib_months)

    train_mask = df["tourney_date"] < calib_start
    calib_mask = (df["tourney_date"] >= calib_start) & (df["tourney_date"] < test_start)
    test_mask = df["tourney_date"] >= test_start

    print(f"\n--- Découpage Temporel Glissant (Réf: {ref_date.strftime('%Y-%m-%d')}) ---")
    print(f"  • Train : < {calib_start.strftime('%Y-%m-%d')} ({train_mask.sum():,} matchs)")
    print(f"  • Calib : {calib_start.strftime('%Y-%m-%d')} à {test_start.strftime('%Y-%m-%d')} ({calib_mask.sum():,} matchs)")
    print(f"  • Test  : >= {test_start.strftime('%Y-%m-%d')} ({test_mask.sum():,} matchs)")

    return (X[train_mask], y[train_mask], train_mask,
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


def calibrate_predictions(p_calib, y_calib, p_test, label_prefix="Ensemble"):
    """
    Calibre les probabilités en comparant Isotonic Regression, Temperature Scaling et Bucket Calibration.
    """
    y_calib_arr = np.array(y_calib)
    p_calib_clamped = np.clip(p_calib, 1e-6, 1.0 - 1e-6)
    p_test_clamped = np.clip(p_test, 1e-6, 1.0 - 1e-6)

    # 1. Global Isotonic
    cal_iso = IsotonicRegression(out_of_bounds="clip")
    cal_iso.fit(p_calib_clamped, y_calib_arr)
    p_iso_test = np.clip(cal_iso.predict(p_test_clamped), 0.001, 0.999)
    metrics_iso = evaluate(y_calib_arr, cal_iso.predict(p_calib_clamped), f"{label_prefix} + Iso (Calib)")

    # 2. Temperature Scaling
    def nll_temp(T):
        logits = np.log(p_calib_clamped / (1.0 - p_calib_clamped)) / T
        p_t = 1.0 / (1.0 + np.exp(-logits))
        return log_loss(y_calib_arr, np.clip(p_t, 1e-6, 1.0 - 1e-6))

    res_t = minimize_scalar(nll_temp, bounds=(0.5, 3.0), method="bounded")
    T_opt = float(res_t.x)
    logits_test = np.log(p_test_clamped / (1.0 - p_test_clamped)) / T_opt
    p_temp_test = np.clip(1.0 / (1.0 + np.exp(-logits_test)), 0.001, 0.999)
    logits_cal = np.log(p_calib_clamped / (1.0 - p_calib_clamped)) / T_opt
    metrics_temp = evaluate(y_calib_arr, 1.0 / (1.0 + np.exp(-logits_cal)), f"{label_prefix} + Temp (T={T_opt:.2f})")

    # 3. Bucket Calibration
    bucket_edges = [0.0, 0.20, 0.35, 0.50, 0.65, 0.80, 1.01]
    bucket_cals = {}
    for i in range(len(bucket_edges) - 1):
        lo, hi = bucket_edges[i], bucket_edges[i + 1]
        m = (p_calib_clamped >= lo) & (p_calib_clamped < hi)
        if m.sum() >= 25:
            b_cal = IsotonicRegression(out_of_bounds="clip")
            b_cal.fit(p_calib_clamped[m], y_calib_arr[m])
            bucket_cals[(lo, hi)] = b_cal
        else:
            bucket_cals[(lo, hi)] = None

    p_bucket_test = np.copy(p_test_clamped)
    for (lo, hi), b_cal in bucket_cals.items():
        m_test = (p_test_clamped >= lo) & (p_test_clamped < hi)
        if b_cal is not None and m_test.sum() > 0:
            p_bucket_test[m_test] = b_cal.predict(p_test_clamped[m_test])
        elif m_test.sum() > 0:
            p_bucket_test[m_test] = cal_iso.predict(p_test_clamped[m_test])
    p_bucket_test = np.clip(p_bucket_test, 0.001, 0.999)

    # Choix optimal basé sur le LogLoss de calibration
    options = {
        "temperature": (p_temp_test, metrics_temp["log_loss"], {"type": "temperature_scaling", "temperature": T_opt}),
        "global": (p_iso_test, metrics_iso["log_loss"], cal_iso),
        "bucket": (p_bucket_test, metrics_iso["log_loss"] + 0.001, {"type": "bucket", "calibrators": bucket_cals, "bucket_edges": bucket_edges, "fallback": cal_iso}),
        "raw": (p_test_clamped, log_loss(y_calib_arr, p_calib_clamped), None)
    }
    best_name = min(options, key=lambda k: options[k][1])
    best_p_test, _, best_cal_obj = options[best_name]
    return best_p_test, best_cal_obj, best_name


if __name__ == "__main__":
    args = get_parser().parse_args()
    print(f"\n{'='*75}")
    print(f"  ENTRAÎNEMENT STACKING MULTI-MODÈLES ({args.circuit.upper()})")
    print(f"  Architectures : XGBoost + LightGBM + CatBoost + Méta-Learner Stacking")
    print(f"{'='*75}")

    df = load_data(circuit=args.circuit)
    X, y, feature_cols = prepare_xy(df)

    # Découpage temporel glissant
    X_train, y_train, train_mask, X_calib, y_calib, X_test, y_test, df_test = dynamic_temporal_split(
        df, X, y,
        calib_months=args.calib_months,
        test_months=args.test_months,
        ref_date=args.ref_date
    )

    # Nettoyage et sélection des features
    feature_cols = filter_features(X_train, y_train, feature_cols, correlation_threshold=0.95)
    X_train = X_train[feature_cols]
    X_calib = X_calib[feature_cols]
    X_test = X_test[feature_cols]

    # Pondération temporelle des échantillons
    train_dates = df.loc[train_mask, "tourney_date"]
    sample_weights = compute_sample_weights(train_dates, half_life_years=args.half_life_years)
    if sample_weights is not None:
        print(f"Pondération temporelle active : demi-vie = {args.half_life_years} ans (poids moyen = {sample_weights.mean():.3f})")

    best_params = load_best_params(circuit=args.circuit)

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
        **best_params,
    )
    xgb_model.fit(
        X_train, y_train,
        sample_weight=sample_weights,
        eval_set=[(X_calib, y_calib)],
        verbose=False
    )
    p_xgb_calib = xgb_model.predict_proba(X_calib)[:, 1]
    p_xgb_test = xgb_model.predict_proba(X_test)[:, 1]

    # -----------------------------------------------------------------------
    # 2. MODÈLE 2 : LightGBM Classifier
    # -----------------------------------------------------------------------
    print("\n[2/3] Entraînement de LightGBM Classifier...")
    lgb_model = lgb.LGBMClassifier(
        n_estimators=700,
        learning_rate=0.03,
        max_depth=5,
        num_leaves=31,
        subsample=0.8,
        colsample_bytree=0.8,
        min_child_samples=25,
        reg_lambda=2.0,
        reg_alpha=0.2,
        random_state=42,
        n_jobs=-1,
        verbose=-1
    )
    lgb_model.fit(
        X_train, y_train,
        sample_weight=sample_weights,
        eval_set=[(X_calib, y_calib)],
        callbacks=[lgb.early_stopping(30, verbose=False)]
    )
    p_lgb_calib = lgb_model.predict_proba(X_calib)[:, 1]
    p_lgb_test = lgb_model.predict_proba(X_test)[:, 1]

    # -----------------------------------------------------------------------
    # 3. MODÈLE 3 : CatBoost Classifier
    # -----------------------------------------------------------------------
    print("\n[3/3] Entraînement de CatBoost Classifier...")
    cat_model = CatBoostClassifier(
        iterations=700,
        learning_rate=0.03,
        depth=5,
        l2_leaf_reg=3.5,
        random_seed=42,
        early_stopping_rounds=30,
        verbose=False,
        thread_count=-1
    )
    cat_model.fit(
        X_train, y_train,
        sample_weight=sample_weights,
        eval_set=(X_calib, y_calib)
    )
    p_cat_calib = cat_model.predict_proba(X_calib)[:, 1]
    p_cat_test = cat_model.predict_proba(X_test)[:, 1]

    # -----------------------------------------------------------------------
    # 4. MÉTA-LEARNER STACKING (Régression Logistique Blending)
    # -----------------------------------------------------------------------
    print("\n--- Entraînement du Méta-Learner (Stacking Logistic Regression) ---")
    M_calib = np.column_stack([p_xgb_calib, p_lgb_calib, p_cat_calib])
    M_test = np.column_stack([p_xgb_test, p_lgb_test, p_cat_test])

    meta_learner = LogisticRegression(C=1.0, fit_intercept=True, random_state=42)
    meta_learner.fit(M_calib, y_calib)

    coefs = meta_learner.coef_[0]
    weights_norm = np.maximum(0, coefs) / (np.maximum(0, coefs).sum() + 1e-12)
    print(f"  • Poids relatifs : XGBoost = {weights_norm[0]*100:.1f}% | LightGBM = {weights_norm[1]*100:.1f}% | CatBoost = {weights_norm[2]*100:.1f}%")

    p_ensemble_calib_raw = meta_learner.predict_proba(M_calib)[:, 1]
    p_ensemble_test_raw = meta_learner.predict_proba(M_test)[:, 1]

    # -----------------------------------------------------------------------
    # 5. CALIBRATION DE L'ENSEMBLE
    # -----------------------------------------------------------------------
    p_ensemble_test_calib, calibrator_obj, best_calib_name = calibrate_predictions(
        p_ensemble_calib_raw, y_calib, p_ensemble_test_raw, label_prefix="Ensemble"
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
