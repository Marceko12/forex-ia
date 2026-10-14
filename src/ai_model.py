"""
Modelo de IA (scikit-learn): predice si el precio subirá en las próximas
`horizon` velas, con un movimiento mínimo medido en ATR.

Buenas prácticas incluidas:
  * Features construidas SOLO con datos pasados.
  * Etiqueta = retorno futuro (única parte que "mira al futuro", y solo para entrenar).
  * Validación temporal (sin mezclar aleatoriamente) con "purga" de `horizon` velas
    entre entrenamiento y validación para evitar fuga de información.
  * Se descartan las muestras "neutras" (movimiento menor al umbral).
"""
from __future__ import annotations

import logging
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import accuracy_score, roc_auc_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from config import MODEL_PATH, ModelConfig

log = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Features y etiquetas
# --------------------------------------------------------------------------- #
def build_features(df: pd.DataFrame) -> pd.DataFrame:
    """Features normalizadas (independientes del nivel de precio). Requiere indicadores."""
    close = df["close"]
    f = pd.DataFrame(index=df.index)
    for n in (1, 2, 3, 5, 10, 20):
        f[f"ret_{n}"] = close.pct_change(n)
    f["rsi"] = df["rsi"] / 100.0
    f["macd_hist_n"] = df["macd_hist"] / close
    f["ema_diff"] = (df["ema_fast"] - df["ema_slow"]) / close
    f["trend_dist"] = (close - df["ema_trend"]) / close
    f["atr_pct"] = df["atr"] / close
    f["bb_pos"] = df["bb_pos"]
    f["range_pct"] = (df["high"] - df["low"]) / close
    f["vol_ratio"] = df["atr"] / df["atr"].rolling(50, min_periods=50).mean()
    hour = pd.Series(df.index.hour, index=df.index)
    f["hour_sin"] = np.sin(2 * np.pi * hour / 24)
    f["hour_cos"] = np.cos(2 * np.pi * hour / 24)
    return f.replace([np.inf, -np.inf], np.nan)


FEATURE_COLUMNS = [
    "ret_1", "ret_2", "ret_3", "ret_5", "ret_10", "ret_20", "rsi", "macd_hist_n", "ema_diff",
    "trend_dist", "atr_pct", "bb_pos", "range_pct", "vol_ratio", "hour_sin", "hour_cos",
]


def build_labels(df: pd.DataFrame, horizon: int, atr_mult: float) -> pd.Series:
    """1 = subió más de atr_mult*ATR, 0 = bajó más de atr_mult*ATR, NaN = neutro/sin futuro."""
    future_ret = df["close"].shift(-horizon) / df["close"] - 1.0
    threshold = atr_mult * df["atr"] / df["close"]
    label = pd.Series(np.nan, index=df.index, name="label")
    label[future_ret > threshold] = 1.0
    label[future_ret < -threshold] = 0.0
    label[future_ret.isna() | threshold.isna()] = np.nan
    return label


# --------------------------------------------------------------------------- #
# Modelo
# --------------------------------------------------------------------------- #
class AIModel:
    def __init__(self, cfg: ModelConfig | None = None) -> None:
        self.cfg = cfg or ModelConfig()
        self.pipeline: Pipeline | None = None
        self.metrics: dict = {}

    def _new_pipeline(self) -> Pipeline:
        c = self.cfg
        return Pipeline([
            ("scaler", StandardScaler()),
            ("clf", RandomForestClassifier(
                n_estimators=c.n_estimators,
                max_depth=c.max_depth,
                min_samples_leaf=c.min_samples_leaf,
                class_weight="balanced",
                n_jobs=-1,
                random_state=c.random_state,
            )),
        ])

    @property
    def is_trained(self) -> bool:
        return self.pipeline is not None

    def train(self, df: pd.DataFrame) -> dict:
        """Entrena con `df` (con indicadores). Devuelve métricas de validación temporal."""
        if not df.index.is_monotonic_increasing or df.index.has_duplicates:
            raise ValueError("El entrenamiento requiere datos cronológicos y sin timestamps duplicados.")
        c = self.cfg
        X = build_features(df)[FEATURE_COLUMNS]
        y = build_labels(df, c.horizon, c.label_atr_mult)
        data = X.join(y).dropna()
        if len(data) < 500:
            raise ValueError(f"Muy pocos datos para entrenar ({len(data)} filas útiles). Se necesitan >= 500.")

        # Split temporal con purga: [ train | gap(horizon) | validación ]
        n = len(data)
        split = int(n * (1 - c.val_fraction))
        train = data.iloc[:split]
        val = data.iloc[split + c.horizon:]
        X_tr, y_tr = train[FEATURE_COLUMNS], train["label"].astype(int)
        X_val, y_val = val[FEATURE_COLUMNS], val["label"].astype(int)

        self.pipeline = self._new_pipeline()
        self.pipeline.fit(X_tr, y_tr)

        proba = self._probability_up(self.pipeline, X_val)
        pred = (proba >= 0.5).astype(int)
        has_both = y_val.nunique() > 1
        self.metrics = {
            "train_rows": int(len(train)),
            "val_rows": int(len(val)),
            "val_accuracy": float(accuracy_score(y_val, pred)),
            "val_auc": float(roc_auc_score(y_val, proba)) if has_both else float("nan"),
            "base_rate_up": float(y_val.mean()),
            "top_features": self._top_features(5),
        }
        log.info("Modelo entrenado | acc=%.3f auc=%.3f (azar = auc 0.50)",
                 self.metrics["val_accuracy"], self.metrics["val_auc"])
        return self.metrics

    def _top_features(self, k: int) -> dict:
        importances = self.pipeline.named_steps["clf"].feature_importances_
        order = np.argsort(importances)[::-1][:k]
        return {FEATURE_COLUMNS[i]: round(float(importances[i]), 4) for i in order}

    def predict_proba(self, df: pd.DataFrame) -> pd.Series:
        """P(sube) para cada vela. Donde faltan features -> 0.5 (sin opinión)."""
        if not self.is_trained:
            raise RuntimeError("El modelo no está entrenado ni cargado.")
        X = build_features(df)[FEATURE_COLUMNS]
        valid = X.notna().all(axis=1)
        out = pd.Series(0.5, index=df.index, name="proba_up")
        if valid.any():
            out.loc[valid] = self._probability_up(self.pipeline, X.loc[valid])
        return out

    @staticmethod
    def _probability_up(pipeline: Pipeline, features: pd.DataFrame) -> np.ndarray:
        probabilities = pipeline.predict_proba(features)
        classes = list(pipeline.named_steps["clf"].classes_)
        return probabilities[:, classes.index(1)] if 1 in classes else np.zeros(len(features))

    # ------------------------------------------------------------------ #
    def save(self, path: Path = MODEL_PATH) -> Path:
        if not self.is_trained:
            raise RuntimeError("No hay modelo entrenado para guardar.")
        path.parent.mkdir(parents=True, exist_ok=True)
        joblib.dump({"pipeline": self.pipeline, "cfg": self.cfg, "metrics": self.metrics}, path)
        log.info("Modelo guardado en %s", path)
        return path

    @classmethod
    def load(cls, path: Path = MODEL_PATH) -> "AIModel":
        if not path.exists():
            raise FileNotFoundError(f"No existe {path}. Ejecuta primero: python app.py train")
        # joblib usa pickle: carga SOLO modelos generados por ti.
        blob = joblib.load(path)
        model = cls(blob["cfg"])
        model.pipeline = blob["pipeline"]
        model.metrics = blob.get("metrics", {})
        return model
