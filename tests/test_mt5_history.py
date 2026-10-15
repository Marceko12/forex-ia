from datetime import datetime, timezone
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from src.market_data import (download_mt5_history, fetch_mt5, load_csv, parse_utc_datetime,
                             save_mt5_history, validate_mt5_rates)


def _epoch(value):
    return int(pd.Timestamp(value).timestamp())


def _rate(value, close=1.1):
    return {"time": _epoch(value), "open": close, "high": close + .001,
            "low": close - .001, "close": close, "tick_volume": 100,
            "spread": 10, "real_volume": 0}


class FakeHistoryMT5:
    TIMEFRAME_M15 = 15

    def __init__(self, rates=None):
        self.rates = rates or []
        self.queries = []

    def symbol_info(self, symbol):
        if symbol != "EURUSDmicro":
            return None
        return SimpleNamespace(trade_mode=4, volume_min=.1, volume_max=100,
                               volume_step=.01, trade_tick_size=.00001,
                               trade_tick_value=.01, trade_tick_value_loss=.01,
                               trade_tick_value_profit=.01, point=.00001, digits=5,
                               trade_stops_level=10, currency_profit="USD",
                               currency_margin="USD", trade_contract_size=1000, spread=10)

    def symbol_select(self, symbol, enabled):
        return symbol == "EURUSDmicro" and enabled

    def copy_rates_range(self, symbol, timeframe, start, end):
        self.queries.append((symbol, timeframe, start, end))
        lo, hi = pd.Timestamp(start), pd.Timestamp(end)
        return [r for r in self.rates
                if lo <= pd.Timestamp(r["time"], unit="s", tz="UTC") <= hi]

    def last_error(self):
        return (0, "")


def test_parse_utc_requires_explicit_timezone_and_normalizes():
    assert parse_utc_datetime("2026-01-01T01:00:00+01:00") == datetime(2026, 1, 1, tzinfo=timezone.utc)
    with pytest.raises(ValueError, match="zona horaria"):
        parse_utc_datetime("2026-01-01T00:00:00")


def test_download_history_preserves_mt5_columns_and_chunks_with_manifest():
    rates = [_rate("2026-01-01T00:00:00Z"), _rate("2026-01-01T00:15:00Z"),
             _rate("2026-01-02T00:00:00Z")]
    mt5 = FakeHistoryMT5(rates)
    frame, manifest = download_mt5_history(
        mt5, "EURUSDmicro", "M15", "2026-01-01T00:00:00Z", "2026-01-02T00:15:00Z", chunk_days=1
    )
    assert len(mt5.queries) == 2
    assert len(frame) == 3  # identical inclusive chunk boundary de-duplicated
    assert frame["time"].dt.tz is not None
    assert {"tick_volume", "spread", "real_volume", "volume"}.issubset(frame.columns)
    assert frame["volume"].equals(frame["tick_volume"])
    assert manifest["identical_chunk_boundary_duplicates_removed"] == 1
    assert manifest["complete"] is False
    assert manifest["symbol_specifications"]["trade_contract_size"] == 1000


def test_download_refuses_symbol_alias_and_unsupported_timeframe():
    mt5 = FakeHistoryMT5([_rate("2026-01-01T00:00:00Z")])
    with pytest.raises(ValueError, match="Símbolo exacto"):
        download_mt5_history(mt5, "EURUSD", "M15", "2026-01-01T00:00:00Z", "2026-01-01T01:00:00Z")
    with pytest.raises(ValueError, match="Timeframe histórico"):
        download_mt5_history(mt5, "EURUSDmicro", "M5", "2026-01-01T00:00:00Z", "2026-01-01T01:00:00Z")


@pytest.mark.parametrize("rates,match", [([], "no devolvió velas"), ([{**_rate("2026-01-01T00:00:00Z"), "close": np.nan}], "no finitos")])
def test_history_validation_rejects_empty_or_invalid_prices(rates, match):
    start = parse_utc_datetime("2026-01-01T00:00:00Z")
    end = parse_utc_datetime("2026-01-01T01:00:00Z")
    with pytest.raises(ValueError, match=match):
        validate_mt5_rates(rates, start, end)


def test_history_download_failure_and_conflicting_duplicates_fail_closed():
    start, end = "2026-01-01T00:00:00Z", "2026-01-01T01:00:00Z"
    with pytest.raises(RuntimeError, match="no devolvió datos"):
        download_mt5_history(FakeHistoryMT5(), "EURUSDmicro", "M15", start, end)
    duplicate = _rate(start, close=1.1)
    conflict = _rate(start, close=1.2)
    with pytest.raises(ValueError, match="timestamps duplicados"):
        download_mt5_history(FakeHistoryMT5([duplicate, conflict]), "EURUSDmicro", "M15", start, end)


def test_manifest_marks_empty_chunks_and_never_claims_full_coverage():
    mt5 = FakeHistoryMT5([_rate("2026-01-01T00:00:00Z")])
    frame, manifest = download_mt5_history(
        mt5, "EURUSDmicro", "M15", "2026-01-01T00:00:00Z", "2026-01-03T00:00:00Z", chunk_days=1
    )
    assert len(frame) == 1
    assert manifest["empty_chunks"] == 1
    assert manifest["complete"] is False


def test_history_save_preserves_raw_data_and_never_overwrites(tmp_path):
    frame = pd.DataFrame([_rate("2026-01-01T00:00:00Z")])
    frame["time"] = pd.to_datetime(frame["time"], unit="s", utc=True)
    frame["volume"] = frame["tick_volume"]
    csv_path, manifest_path = save_mt5_history(frame, {"source": "mock"}, tmp_path / "rates.csv")
    saved = pd.read_csv(csv_path)
    assert {"time", "tick_volume", "spread", "real_volume", "volume"}.issubset(saved.columns)
    assert pd.read_json(manifest_path, typ="series")["sha256"]
    with pytest.raises(FileExistsError):
        save_mt5_history(frame, {}, csv_path)


def test_csv_loader_preserves_mt5_spread_points_for_backtester(tmp_path):
    data = pd.DataFrame({
        "time": ["2026-01-01T00:00:00Z", "2026-01-01T00:15:00Z"],
        "open": [1.1, 1.1], "high": [1.101, 1.101],
        "low": [1.099, 1.099], "close": [1.1, 1.1],
        "tick_volume": [10, 11], "real_volume": [0, 0], "volume": [10, 11],
        "spread": [12, 18],
    })
    path = tmp_path / "raw_rates.csv"
    data.to_csv(path, index=False)
    loaded = load_csv(path)
    assert loaded["spread"].tolist() == [12, 18]


def test_positional_mt5_fetch_preserves_bar_spread_points(monkeypatch):
    from config import MT5Config, MarketConfig, Settings
    import src.market_data as market_data

    class ReadOnlyFake(FakeHistoryMT5):
        TIMEFRAME_M15 = 15

        def terminal_info(self):
            return SimpleNamespace(connected=True)

        def copy_rates_from_pos(self, symbol, timeframe, start_pos, count):
            return [
                {**_rate("2026-01-01T00:00:00Z"), "spread": 12},
                {**_rate("2026-01-01T00:15:00Z"), "spread": 18},
            ]

    fake = ReadOnlyFake()
    monkeypatch.setattr(market_data, "_import_mt5", lambda: fake)
    settings = Settings(mt5=MT5Config(), market=MarketConfig(
        symbol="EURUSDmicro", timeframe="M15"))
    result = fetch_mt5(settings, n_bars=2)
    assert result["spread"].tolist() == [12, 18]
