"""
Entraînement + évaluation.

Points clés pour du VALUE BETTING (pas juste de l'accuracy) :

1. SPLIT TEMPOREL, jamais aléatoire. On entraîne sur le passé, on teste
   sur le futur (ex: train < 2023, test = 2023-2025). Un split aléatoire
   ferait fuir de l'information (le modèle "verrait" indirectement des
   joueurs dans un état de forme qu'il ne devrait pas encore connaître).

2. La métrique reine ici c'est le LOG LOSS (et le Brier score), pas
   l'accuracy. Pour miser intelligemment il faut des probabilités bien
   calibrées : un modèle qui dit "60%" doit avoir raison ~60% du temps,
   pas juste deviner le bon vainqueur.

3. CALIBRATION : XGBoost donne des scores qui ne sont pas toujours des
   probabilités bien calibrées. On ajoute une calibration isotonique
   (sur un set de calibration séparé du test) pour corriger ça.

4. On compare toujours au baseline "classement ATP seul" et, plus tard,
   aux probabilités implicites des bookmakers (voir 04_backtest.py).
"""

import pandas as pd
import numpy as np
import xgboost as xgb
from sklearn.metrics import log_loss, brier_score_loss, accuracy_score, roc_auc_score
from pathlib import Path
import json

BASE_DIR = Path(__file__).resolve().parent.parent
PROCESSED_DIR = BASE_DIR / "data" / "processed"

FEATURE_COLS = None  # rempli dynamiquement

# Valeurs par défaut, écrasées si data/processed/best_params.json existe
# (généré par 03b_tune_hyperparameters.py).
DEFAULT_PARAMS = dict(
    max_depth=4, learning_rate=0.03, subsample=0.8, colsample_bytree=0.8,
    min_child_weight=5, reg_lambda=1.5, reg_alpha=0.0, gamma=0.0,
)


def load_best_params():
    path = PROCESSED_DIR / "best_params.json"
    if path.exists():
        with open(path) as f:
            params = json.load(f)
        print(f"Hyperparamètres chargés depuis {path} (issus de 03b_tune_hyperparameters.py)")
        return params
    print("Pas de best_params.json trouvé, utilisation des valeurs par défaut "
          "(lance 03b_tune_hyperparameters.py pour les optimiser).")
    return DEFAULT_PARAMS


def load_data():
    in_path = PROCESSED_DIR / "features.parquet"
    if not in_path.exists():
        raise FileNotFoundError(f"{in_path} introuvable. Lance d'abord: python 02_feature_engineering.py")
    df = pd.read_parquet(in_path)
    df = df[~df["retirement"]]  # on retire les matchs abandonnés (bruit, pas prédictibles)
    df = df.sort_values("tourney_date").reset_index(drop=True)
    return df


def prepare_xy(df):
    cat_cols = ["surface", "tourney_level", "round", "hand_matchup", "indoor"]
    df = pd.get_dummies(df, columns=cat_cols, dummy_na=True)

    drop_cols = ["match_id", "tourney_date", "target", "retirement"]
    feature_cols = [c for c in df.columns if c not in drop_cols]

    X = df[feature_cols]
    y = df["target"]
    return X, y, feature_cols


def temporal_split(df, X, y, train_end, calib_end, test_start):
    train_mask = df["tourney_date"] < train_end
    calib_mask = (df["tourney_date"] >= train_end) & (df["tourney_date"] < calib_end)
    test_mask = df["tourney_date"] >= test_start

    return (X[train_mask], y[train_mask],
            X[calib_mask], y[calib_mask],
            X[test_mask], y[test_mask],
            df.loc[test_mask])


def evaluate(y_true, p_pred, label):
    ll = log_loss(y_true, p_pred)
    brier = brier_score_loss(y_true, p_pred)
    acc = accuracy_score(y_true, (p_pred > 0.5).astype(int))
    auc = roc_auc_score(y_true, p_pred)
    print(f"[{label}] log_loss={ll:.4f}  brier={brier:.4f}  accuracy={acc:.4f}  auc={auc:.4f}")
    return {"log_loss": ll, "brier": brier, "accuracy": acc, "auc": auc}


if __name__ == "__main__":
    df = load_data()
    X, y, feature_cols = prepare_xy(df)

    # Découpage temporel : ajuste ces dates selon la période que tu veux backtester
    X_train, y_train, X_calib, y_calib, X_test, y_test, df_test = temporal_split(
        df, X, y,
        train_end="2022-01-01",
        calib_end="2023-01-01",
        test_start="2023-01-01",
    )
    print(f"Train: {len(X_train)}  Calib: {len(X_calib)}  Test: {len(X_test)}")

    best_params = load_best_params()
    model = xgb.XGBClassifier(
        n_estimators=600,
        eval_metric="logloss",
        early_stopping_rounds=30,
        n_jobs=2,  # Evite le deadlock (plantage) d'XGBoost sur GitHub Actions (ubuntu-latest a 2 cœurs)
        tree_method="hist",
        **best_params,
    )
    model.fit(X_train, y_train, eval_set=[(X_calib, y_calib)], verbose=False)

    # Baseline brut (sans calibration)
    p_raw = model.predict_proba(X_test)[:, 1]
    metrics_raw = evaluate(y_test, p_raw, "XGBoost brut")

    # Calibration isotonique manuelle
    from sklearn.isotonic import IsotonicRegression
    from sklearn.model_selection import KFold

    p_calib_raw = model.predict_proba(X_calib)[:, 1]
    
    # Décider d'utiliser la calibration par validation croisée sur le set de calib
    # (pour ne SURTOUT pas regarder les performances sur le set de test)
    kf = KFold(n_splits=5, shuffle=False)
    oof_calib_preds = np.zeros_like(p_calib_raw)
    y_calib_arr = y_calib.values
    for train_idx, val_idx in kf.split(p_calib_raw):
        fold_calibrator = IsotonicRegression(out_of_bounds="clip")
        fold_calibrator.fit(p_calib_raw[train_idx], y_calib_arr[train_idx])
        oof_calib_preds[val_idx] = fold_calibrator.predict(p_calib_raw[val_idx])
        
    ll_calib_raw = log_loss(y_calib, p_calib_raw)
    ll_calib_iso = log_loss(y_calib, oof_calib_preds)
    use_calibration = ll_calib_iso < ll_calib_raw
    
    # Entraînement du calibrateur final sur TOUT le set de calibration
    calibrator = IsotonicRegression(out_of_bounds="clip")
    calibrator.fit(p_calib_raw, y_calib_arr)
    
    p_calib = calibrator.predict(p_raw)
    metrics_calib = evaluate(y_test, p_calib, "XGBoost calibré")

    p_final = p_calib if use_calibration else p_raw
    print(f"  => Prédictions finales : {'calibrées (isotonic)' if use_calibration else 'brutes XGBoost (calibration isotonique contre-productive)'}")

    # Baseline naïf : Elo seul (via elo_diff)
    from sklearn.linear_model import LogisticRegression
    lr = LogisticRegression()
    lr.fit(X_train[["elo_diff"]].fillna(0), y_train)
    p_rank = lr.predict_proba(X_test[["elo_diff"]].fillna(0))[:, 1]
    evaluate(y_test, p_rank, "Baseline: Elo seul")

    # Importance des features
    importances = pd.Series(model.feature_importances_, index=feature_cols).sort_values(ascending=False)
    print("\nTop 15 features:")
    print(importances.head(15))

    # Sauvegarde pour le backtest de value betting
    model.save_model(str(PROCESSED_DIR / "xgb_model.json"))
    import joblib
    joblib.dump(calibrator, PROCESSED_DIR / "calibrator.pkl")
    joblib.dump(feature_cols, PROCESSED_DIR / "feature_cols.pkl")

    df_test = df_test.copy()
    df_test["p_model"] = p_final
    df_test.to_parquet(PROCESSED_DIR / "test_predictions.parquet", index=False)
    print("\nModèle et prédictions test sauvegardés.")
