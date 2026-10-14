"""
Reglas de trading.

Convención de señales (una por vela CERRADA):
    +1 = comprar   -1 = vender   0 = no hacer nada

La señal de la vela `t` se calcula con datos hasta el cierre de `t` y se
ejecuta en la apertura de `t+1` (el backtester respeta esto).

Modos:
    rules  -> solo reglas técnicas
    ai     -> solo el modelo de machine learning
    hybrid -> reglas técnicas + confirmación del modelo (ambos deben coincidir)
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from config import StrategyConfig

MODES = ("rules", "ai", "hybrid")
REQUIRED_COLUMNS = ["close", "ema_fast", "ema_slow", "ema_trend", "rsi", "macd_hist"]


def generate_rule_signals(df: pd.DataFrame, cfg: StrategyConfig | None = None) -> pd.Series:
    """Estrategia de continuación de tendencia con filtro de momentum.

    Compra: EMA rápida > EMA lenta, precio sobre EMA de tendencia,
            MACD histograma > 0 y RSI entre 50 y sobrecompra.
    Venta : lo contrario.
    """
    cfg = cfg or StrategyConfig()
    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"Faltan indicadores: {missing}. Usa add_all_indicators() primero.")

    uptrend = (df["ema_fast"] > df["ema_slow"]) & (df["close"] > df["ema_trend"])
    downtrend = (df["ema_fast"] < df["ema_slow"]) & (df["close"] < df["ema_trend"])

    long_cond = uptrend & (df["macd_hist"] > 0) & (df["rsi"] > 50) & (df["rsi"] < cfg.rsi_overbought)
    short_cond = downtrend & (df["macd_hist"] < 0) & (df["rsi"] < 50) & (df["rsi"] > cfg.rsi_oversold)

    # Las comparaciones con NaN dan False -> el calentamiento queda en 0.
    signal = np.where(long_cond, 1, np.where(short_cond, -1, 0))
    return pd.Series(signal, index=df.index, name="signal", dtype="int8")


def generate_ai_signals(proba_up: pd.Series, cfg: StrategyConfig | None = None) -> pd.Series:
    """Convierte P(sube) en señales usando umbrales de confianza."""
    cfg = cfg or StrategyConfig()
    signal = np.where(proba_up >= cfg.ai_buy_threshold, 1,
                      np.where(proba_up <= cfg.ai_sell_threshold, -1, 0))
    return pd.Series(signal, index=proba_up.index, name="signal", dtype="int8")


def combine_signals(rule_signal: pd.Series, ai_signal: pd.Series | None, mode: str = "hybrid") -> pd.Series:
    mode = mode.lower()
    if mode not in MODES:
        raise ValueError(f"Modo inválido '{mode}'. Opciones: {MODES}")
    if mode == "rules":
        return rule_signal.rename("signal")
    if ai_signal is None:
        raise ValueError(f"El modo '{mode}' necesita señales del modelo de IA.")
    if mode == "ai":
        return ai_signal.rename("signal")
    agree = rule_signal == ai_signal
    return rule_signal.where(agree, 0).astype("int8").rename("signal")


def build_signals(
    df: pd.DataFrame,
    proba_up: pd.Series | None = None,
    cfg: StrategyConfig | None = None,
) -> pd.Series:
    """Atajo: reglas + IA (si corresponde) -> serie final de señales."""
    cfg = cfg or StrategyConfig()
    rules = generate_rule_signals(df, cfg)
    ai = generate_ai_signals(proba_up.reindex(df.index).fillna(0.5), cfg) if proba_up is not None else None
    return combine_signals(rules, ai, cfg.mode)


def side_name(signal: int) -> str:
    return {1: "BUY", -1: "SELL"}.get(int(signal), "FLAT")
