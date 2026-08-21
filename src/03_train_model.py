"""
Entraînement + évaluation du modèle XGBoost avec split temporel glissant, pondération temporelle et calibration isotonique.

Points clés :
1. SPLIT TEMPOREL GLISSANT DYNAMIQUE :
   - Test  : les 6 derniers mois (ex: de M-6 à aujourd'hui)
   - Calib : les 12 mois précédents (ex: de M-18 à M-6)
   - Train : tout l'historique antérieur (ex: 2000 jusqu'à M-18)

2. PONDÉRATION TEMPORELLE EXPONENTIELLE (Sample Weighting) :
   - Priorise les matchs récents (tennis moderne) par rapport aux matchs anciens (demi-vie configurable, ex: 7 ans).

3. Métriques d'évaluation : Log Loss, Brier Score, Accuracy, AUC.
4. Calibration isotonique (globale ou par bucket) pour corriger les biais conditionnels.
5. Sauvegarde du modèle XGBoost, du calibrateur et des colonnes de features.
"""

import pandas as pd
import numpy as np
import xgboost as xgb
from sklearn.metrics import log_loss, brier_score_loss, accuracy_score, roc_auc_score
from sklearn.isotonic import IsotonicRegression
from sklearn.model_selection import KFold
from sklearn.linear_model import LogisticRegression
from scipy.optimize import minimize_scalar
from pathlib import Path
import json
import joblib
import argparse

def get_parser():
    parser = argparse.ArgumentParser(description="Entraînement XGBoost avec split temporel glissant et pondération temporelle.")
    parser.add_argument("--circuit", default="atp", choices=["atp", "wta"], help="Circuit à traiter (atp ou wta)")
    parser.add_argument("--calib-months", type=int, default=12, help="Nombre de mois pour la calibration (défaut: 12)")
    parser.add_argument("--test-months", type=int, default=6, help="Nombre de mois pour le test (défaut: 6)")
    parser.add_argument("--ref-date", default=None, help="Date de référence (AAAA-MM-JJ). Défaut: date max du dataset.")
    parser.add_argument("--half-life-years", type=float, default=7.0, help="Demi-vie en années pour la pondération temporelle (0 pour désactiver, défaut: 7.0)")
    return parser

BASE_DIR = Path(__file__).resolve().parent.parent
PROCESSED_DIR = BASE_DIR / "data" / "processed"

# Hyperparamètres par défaut
DEFAULT_PARAMS = dict(
    max_depth=4, learning_rate=0.03, subsample=0.8, colsample_bytree=0.8,
    min_child_weight=5, reg_lambda=1.5, reg_alpha=0.0, gamma=0.0,
)


def load_best_params(circuit="atp"):
    path = PROCESSED_DIR / f"best_params_{circuit}.json"
    if path.exists():
        with open(path) as f:
            params = json.load(f)
        print(f"Hyperparamètres chargés depuis {path}")
        return params
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
    - Match récent dans le train set : poids = 1.0
    - Match datant de half_life_years : poids = 0.5
    - Plancher min_weight pour conserver l'information structurelle ancienne.
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
    Supprime automatiquement les features redondantes :
    1. Features à variance nulle (constantes).
    2. Paires collinéaires (|r| > correlation_threshold) en conservant la feature la plus corrélée à y.
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
        print(f"\nNettoyage des features : {len(feature_cols)} -> {len(selected)} features retenues ({len(all_dropped)} supprimées : {sorted(list(all_dropped))})")
    return selected


def evaluate(y_true, p_pred, label):
    ll = log_loss(y_true, p_pred)
    brier = brier_score_loss(y_true, p_pred)
    acc = accuracy_score(y_true, (p_pred > 0.5).astype(int))
    auc = roc_auc_score(y_true, p_pred)
    print(f"[{label}] log_loss={ll:.4f}  brier={brier:.4f}  accuracy={acc:.4f}  auc={auc:.4f}")
    return {"log_loss": ll, "brier": brier, "accuracy": acc, "auc": auc}


if __name__ == "__main__":
    args = get_parser().parse_args()
    print(f"=== ENTRAÎNEMENT DU MODÈLE XGBOOST ({args.circuit.upper()}) ===")
    df = load_data(circuit=args.circuit)
    X, y, feature_cols = prepare_xy(df)

    # Découpage temporel glissant
    X_train, y_train, train_mask, X_calib, y_calib, X_test, y_test, df_test = dynamic_temporal_split(
        df, X, y,
        calib_months=args.calib_months,
        test_months=args.test_months,
        ref_date=args.ref_date
    )

    # Nettoyage et sélection automatique des features
    feature_cols = filter_features(X_train, y_train, feature_cols, correlation_threshold=0.95)
    X_train = X_train[feature_cols]
    X_calib = X_calib[feature_cols]
    X_test = X_test[feature_cols]

    # Pondération temporelle des échantillons
    train_dates = df.loc[train_mask, "tourney_date"]
    sample_weights = compute_sample_weights(train_dates, half_life_years=args.half_life_years)
    if sample_weights is not None:
        print(f"Pondération temporelle active : demi-vie = {args.half_life_years} ans (poids moyen = {sample_weights.mean():.3f})")
    else:
        print("Pondération temporelle désactivée (poids uniformes).")

    best_params = load_best_params(circuit=args.circuit)

    # Entraînement XGBoost pur
    print("\nEntraînement de XGBoost Classifier...")
    xgb_model = xgb.XGBClassifier(
        n_estimators=600,
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

    # Prédictions brutes
    p_raw = xgb_model.predict_proba(X_test)[:, 1]
    metrics_raw = evaluate(y_test, p_raw, "XGBoost Brut")

    # Calibration isotonique
    p_calib_raw = xgb_model.predict_proba(X_calib)[:, 1]
    y_calib_arr = y_calib.values

    # 1. Calibration Globale
    kf = KFold(n_splits=5, shuffle=False)
    oof_calib_preds = np.zeros_like(p_calib_raw)
    for train_idx, val_idx in kf.split(p_calib_raw):
        fold_calibrator = IsotonicRegression(out_of_bounds="clip")
        fold_calibrator.fit(p_calib_raw[train_idx], y_calib_arr[train_idx])
        oof_calib_preds[val_idx] = fold_calibrator.predict(p_calib_raw[val_idx])

    calibrator_global = IsotonicRegression(out_of_bounds="clip")
    calibrator_global.fit(p_calib_raw, y_calib_arr)

    p_global_calib = calibrator_global.predict(p_raw)
    metrics_global_calib = evaluate(y_test, p_global_calib, "XGBoost + Calibration globale")

    # 2. Calibration par Bucket
    bucket_edges = [0.0, 0.20, 0.35, 0.50, 0.65, 0.80, 1.01]
    bucket_calibrators = {}

    print("\n--- Calibration par bucket (set de calibration) ---")
    print(f"{'Bucket':<15} {'N':>6} {'P_raw moy':>10} {'Taux réel':>10} {'Écart':>8} {'P_calib moy':>12}")

    for i in range(len(bucket_edges) - 1):
        lo, hi = bucket_edges[i], bucket_edges[i + 1]
        mask = (p_calib_raw >= lo) & (p_calib_raw < hi)
        n_bucket = mask.sum()

        if n_bucket < 20:
            bucket_calibrators[(lo, hi)] = None
            continue

        bucket_cal = IsotonicRegression(out_of_bounds="clip")
        bucket_cal.fit(p_calib_raw[mask], y_calib_arr[mask])
        bucket_calibrators[(lo, hi)] = bucket_cal

        p_bucket_calib = bucket_cal.predict(p_calib_raw[mask])
        p_raw_mean = p_calib_raw[mask].mean()
        taux_reel = y_calib_arr[mask].mean()
        p_calib_mean = p_bucket_calib.mean()
        ecart = (p_raw_mean - taux_reel) * 100

        label = f"[{lo:.2f}-{hi:.2f})"
        print(f"{label:<15} {n_bucket:>6} {p_raw_mean*100:>9.1f}% {taux_reel*100:>9.1f}% {ecart:>+7.1f}% {p_calib_mean*100:>11.1f}%")

    # Application bucket sur test
    p_bucket_final = np.copy(p_raw)
    for (lo, hi), cal in bucket_calibrators.items():
        mask_test = (p_raw >= lo) & (p_raw < hi)
        if cal is not None and mask_test.sum() > 0:
            p_bucket_final[mask_test] = cal.predict(p_raw[mask_test])
        elif mask_test.sum() > 0:
            p_bucket_final[mask_test] = calibrator_global.predict(p_raw[mask_test])

    p_bucket_final = np.clip(p_bucket_final, 0.001, 0.999)
    metrics_bucket_calib = evaluate(y_test, p_bucket_final, "XGBoost + Calibration par bucket")

    # 3. Temperature Scaling (optimisation de T sur le set de calibration)
    p_calib_arr = np.array(p_calib_raw)
    y_calib_arr_np = np.array(y_calib_arr)

    def nll_temperature(T):
        p_c = np.clip(p_calib_arr, 1e-6, 1 - 1e-6)
        logits = np.log(p_c / (1.0 - p_c)) / T
        p_t = 1.0 / (1.0 + np.exp(-logits))
        return log_loss(y_calib_arr_np, p_t)

    res_temp = minimize_scalar(nll_temperature, bounds=(0.5, 3.0), method="bounded")
    T_opt = res_temp.x
    p_temp_raw_test = np.clip(p_raw, 1e-6, 1 - 1e-6)
    logits_test = np.log(p_temp_raw_test / (1.0 - p_temp_raw_test)) / T_opt
    p_temp_final = np.clip(1.0 / (1.0 + np.exp(-logits_test)), 0.001, 0.999)
    metrics_temp = evaluate(y_test, p_temp_final, f"XGBoost + Temperature Scaling (T={T_opt:.3f})")

    # Sélection de la meilleure calibration
    options = {
        "brut": (p_raw, metrics_raw["log_loss"]),
        "global": (p_global_calib, metrics_global_calib["log_loss"]),
        "bucket": (p_bucket_final, metrics_bucket_calib["log_loss"]),
        "temperature": (p_temp_final, metrics_temp["log_loss"]),
    }
    best_name = min(options, key=lambda k: options[k][1])
    p_final = options[best_name][0]
    print(f"\n=> Calibration retenue : {best_name} (log_loss = {options[best_name][1]:.4f})")

    # Baseline simple : Elo seul
    lr = LogisticRegression()
    lr.fit(X_train[["elo_diff"]].fillna(0), y_train)
    p_rank = lr.predict_proba(X_test[["elo_diff"]].fillna(0))[:, 1]
    evaluate(y_test, p_rank, "Baseline: Elo seul")

    # Feature Importance
    importances = pd.Series(xgb_model.feature_importances_, index=feature_cols).sort_values(ascending=False)
    print("\nTop 15 Features (XGBoost) :")
    print(importances.head(15))

    # Sauvegarde des modèles et artefacts
    model_path = PROCESSED_DIR / f"xgb_model_{args.circuit}.json"
    xgb_model.save_model(str(model_path))
    print(f"\nModèle XGBoost sauvegardé dans {model_path}")

    calib_path = PROCESSED_DIR / f"calibrator_{args.circuit}.pkl"
    if best_name == "temperature":
        joblib.dump({"type": "temperature_scaling", "temperature": float(T_opt)}, calib_path)
    elif best_name == "bucket":
        joblib.dump({"type": "bucket", "calibrators": bucket_calibrators,
                     "bucket_edges": bucket_edges, "fallback": calibrator_global}, calib_path)
    elif best_name == "global":
        joblib.dump(calibrator_global, calib_path)
    else:
        joblib.dump(None, calib_path)
    print(f"Calibrateur ({best_name}) sauvegardé dans {calib_path}")

    fcols_path = PROCESSED_DIR / f"feature_cols_{args.circuit}.pkl"
    joblib.dump(feature_cols, fcols_path)
    print(f"Colonnes de features sauvegardées dans {fcols_path}")

    df_test = df_test.copy()
    df_test["p_model"] = p_final
    test_pred_path = PROCESSED_DIR / f"test_predictions_{args.circuit}.parquet"
    df_test.to_parquet(test_pred_path, index=False)
    print(f"Prédictions de test sauvegardées dans {test_pred_path}")
