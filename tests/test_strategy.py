"""Tests de indicadores, estrategia, modelo de IA y backtester."""
import numpy as np
import pandas as pd

from config import BacktestConfig, ModelConfig, RiskConfig, StrategyConfig, get_symbol_spec
from src.ai_model import FEATURE_COLUMNS, AIModel, build_features, build_labels
from src.backtester import Backtester
from src.indicators import add_all_indicators, rsi
from src.market_data import generate_synthetic
from src.strategy import build_signals, combine_signals, generate_ai_signals, generate_rule_signals


def _trend_df(direction: int, n: int = 600) -> pd.DataFrame:
    """Serie con tendencia limpia (con algo de ruido) para probar las reglas."""
    rng = np.random.default_rng(1)
    idx = pd.date_range("2024-01-01", periods=n, freq="1h", tz="UTC")
    close = 1.10 + direction * np.linspace(0, 0.08, n) + rng.normal(0, 0.0004, n)
    open_ = np.concatenate([[close[0]], close[:-1]])
    high = np.maximum(open_, close) + 0.0003
    low = np.minimum(open_, close) - 0.0003
    return pd.DataFrame({"open": open_, "high": high, "low": low, "close": close, "volume": 1000.0}, index=idx)


# --------------------------- indicadores ---------------------------------- #
def test_rsi_is_bounded_0_100():
    df = generate_synthetic(2000)
    r = rsi(df["close"]).dropna()
    assert len(r) > 0
    assert r.min() >= 0 and r.max() <= 100


def test_rsi_flat_series_is_neutral():
    flat = pd.Series([1.0] * 100)
    assert rsi(flat).dropna().eq(50.0).all()


def test_indicators_add_expected_columns():
    ind = add_all_indicators(generate_synthetic(1000))
    for col in ("ema_fast", "ema_slow", "ema_trend", "rsi", "macd_hist", "atr", "bb_pos"):
        assert col in ind.columns
    assert (ind["atr"].dropna() > 0).all()


# ----------------------------- reglas ------------------------------------- #
def test_signals_only_valid_values():
    ind = add_all_indicators(generate_synthetic(3000))
    sig = generate_rule_signals(ind)
    assert set(sig.unique()).issubset({-1, 0, 1})
    assert len(sig) == len(ind)


def test_uptrend_never_sells_and_downtrend_never_buys():
    up = generate_rule_signals(add_all_indicators(_trend_df(+1)))
    down = generate_rule_signals(add_all_indicators(_trend_df(-1)))
    assert (up == -1).sum() == 0 and (up == 1).sum() > 0
    assert (down == 1).sum() == 0 and (down == -1).sum() > 0


def test_warmup_rows_have_no_signal():
    ind = add_all_indicators(generate_synthetic(1000))
    sig = generate_rule_signals(ind)
    assert (sig.iloc[:150] == 0).all()  # EMA(200) aún no existe


def test_signals_are_causal_no_lookahead():
    """La señal en t no puede cambiar si se recortan los datos futuros."""
    df = generate_synthetic(1500)
    full = generate_rule_signals(add_all_indicators(df))
    cut = generate_rule_signals(add_all_indicators(df.iloc[:1000]))
    assert full.iloc[:1000].equals(cut)


def test_hybrid_requires_agreement():
    idx = pd.RangeIndex(5)
    rules = pd.Series([1, 1, -1, 0, -1], index=idx, dtype="int8")
    ai = pd.Series([1, -1, -1, 1, 0], index=idx, dtype="int8")
    assert combine_signals(rules, ai, "hybrid").tolist() == [1, 0, -1, 0, 0]
    assert combine_signals(rules, ai, "rules").tolist() == rules.tolist()
    assert combine_signals(rules, ai, "ai").tolist() == ai.tolist()


def test_ai_modes_need_ai_signal():
    rules = pd.Series([1, 0], dtype="int8")
    try:
        combine_signals(rules, None, "hybrid")
    except ValueError:
        return
    raise AssertionError("Debió lanzar ValueError")


def test_ai_thresholds():
    proba = pd.Series([0.9, 0.6, 0.5, 0.4, 0.1])
    sig = generate_ai_signals(proba, StrategyConfig(ai_buy_threshold=0.55, ai_sell_threshold=0.45))
    assert sig.tolist() == [1, 1, 0, -1, -1]


# ------------------------------ modelo IA --------------------------------- #
def test_labels_use_future_and_drop_neutral():
    ind = add_all_indicators(generate_synthetic(2000))
    y = build_labels(ind, horizon=6, atr_mult=0.5)
    assert set(y.dropna().unique()).issubset({0.0, 1.0})
    assert y.iloc[-6:].isna().all()  # sin futuro -> sin etiqueta
    assert y.isna().sum() > 0        # hay muestras neutras descartadas


def test_features_are_causal():
    df = generate_synthetic(1500)
    full = build_features(add_all_indicators(df))[FEATURE_COLUMNS]
    cut = build_features(add_all_indicators(df.iloc[:1000]))[FEATURE_COLUMNS]
    pd.testing.assert_frame_equal(full.iloc[:1000], cut)


def test_model_trains_and_predicts_probabilities():
    ind = add_all_indicators(generate_synthetic(4000))
    model = AIModel(ModelConfig(n_estimators=40, max_depth=4))
    metrics = model.train(ind)
    assert 0.0 <= metrics["val_accuracy"] <= 1.0
    proba = model.predict_proba(ind)
    assert len(proba) == len(ind)
    assert proba.between(0, 1).all()


def test_model_save_and_load():
    import tempfile
    from pathlib import Path

    ind = add_all_indicators(generate_synthetic(3000))
    model = AIModel(ModelConfig(n_estimators=20, max_depth=3))
    model.train(ind)
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "m.joblib"
        model.save(path)
        loaded = AIModel.load(path)
    a, b = model.predict_proba(ind), loaded.predict_proba(ind)
    assert np.allclose(a.to_numpy(), b.to_numpy())


# ------------------------------ backtester -------------------------------- #
def test_backtest_runs_and_is_consistent():
    ind = add_all_indicators(generate_synthetic(6000))
    signals = build_signals(ind, None, StrategyConfig(mode="rules"))
    bt = Backtester(get_symbol_spec("EURUSD"), BacktestConfig(), RiskConfig(), StrategyConfig(mode="rules"))
    res = bt.run(ind, signals)

    assert res.metrics["n_trades"] == len(res.trades) > 0
    assert (res.trades["lots"] > 0).all()
    assert (res.trades["exit_time"] >= res.trades["entry_time"]).all()
    # El equity final debe coincidir con balance inicial + suma de PnL
    expected = BacktestConfig().initial_balance + res.trades["pnl"].sum()
    assert abs(res.metrics["final_equity"] - expected) < 1.0


def test_backtest_without_signals_has_no_trades():
    ind = add_all_indicators(generate_synthetic(2000))
    zero = pd.Series(0, index=ind.index)
    res = Backtester(get_symbol_spec("EURUSD")).run(ind, zero)
    assert res.metrics["n_trades"] == 0
    assert res.metrics["final_equity"] == BacktestConfig().initial_balance


def test_backtest_rejects_invalid_prices_and_unsorted_time():
    import pytest

    ind = add_all_indicators(generate_synthetic(500))
    signals = pd.Series(0, index=ind.index)
    bad_prices = ind.copy()
    bad_prices.iloc[300, bad_prices.columns.get_loc("low")] = bad_prices["high"].iloc[300] + 1
    with pytest.raises(ValueError, match="inconsistentes"):
        Backtester(get_symbol_spec("EURUSD")).run(bad_prices, signals)
    with pytest.raises(ValueError, match="cronológico"):
        Backtester(get_symbol_spec("EURUSD")).run(ind.iloc[::-1], signals.iloc[::-1])


def test_stop_loss_hits_first_when_both_touched():
    """Vela que toca SL y TP a la vez -> se asume SL (conservador)."""
    ind = add_all_indicators(generate_synthetic(500))
    ind = ind.copy()
    i = 300
    sig = pd.Series(0, index=ind.index)
    sig.iloc[i - 1] = 1  # compra a la apertura de la vela i
    ind.iloc[i, ind.columns.get_loc("high")] = ind["open"].iloc[i] + 1.0   # TP seguro
    ind.iloc[i, ind.columns.get_loc("low")] = ind["open"].iloc[i] - 1.0    # SL seguro
    res = Backtester(get_symbol_spec("EURUSD")).run(ind, sig)
    assert res.trades.iloc[0]["exit_reason"] == "SL"
    assert res.trades.iloc[0]["pnl"] < 0
