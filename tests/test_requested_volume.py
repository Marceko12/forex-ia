"""Explicit symbol volume remains subject to every risk gate."""
from types import SimpleNamespace

import pandas as pd

from config import RiskConfig, Settings, SymbolSpec
from src.risk_manager import RiskManager


def _settings_and_spec():
    settings = Settings(mt5=None)
    settings.market.symbol = "EURUSDmicro"
    spec = SymbolSpec(
        "EURUSDmicro", .0001, contract_size=1000, min_lot=.10,
        max_lot=100, lot_step=.01, tick_size=.00001,
        tick_value_profit=.01, tick_value_loss=.01, currency_profit="USD",
    )
    return settings, spec


class _Broker:
    def __init__(self, spec, equity, *, free_margin=1000, required_margin=5):
        self.spec = spec
        self._equity = equity
        self.orders = []
        self.deviation = 0
        self.required_margin = required_margin
        self.mt5 = SimpleNamespace(
            symbol_info=lambda _symbol: SimpleNamespace(point=.00001),
            account_info=lambda: SimpleNamespace(margin_free=free_margin),
        )

    def open_positions(self):
        return []

    def equity(self):
        return self._equity

    def entry_quote(self, side):
        return 1.1001 if side == 1 else 1.1000

    def loss_per_lot(self, side, entry, sl):
        return abs(entry - sl) / .0001 * .10

    def margin_for(self, side, lots, price):
        return self.required_margin

    def market_order(self, side, lots, sl, tp, **kwargs):
        self.orders.append({"side": side, "lots": lots, "sl": sl, "tp": tp, **kwargs})
        return SimpleNamespace(ticket=1)


def test_eurusdmicro_uses_exact_requested_volume_010_when_risk_allows():
    import app

    settings, spec = _settings_and_spec()
    broker = _Broker(spec, equity=1_000)
    risk = RiskManager(RiskConfig(risk_per_trade=.01, max_lots=.5), 1_000)

    opened = app.try_open_trade(
        broker, risk, settings, pd.Timestamp("2026-10-14T12:00:00Z"),
        signal=1, atr_value=.0001, price=1.1,
    )

    assert opened
    assert broker.orders[0]["lots"] == .10
    assert settings.requested_volume_by_symbol["EURUSDmicro"] == .10


def test_eurusdmicro_requested_volume_is_rejected_when_risk_exceeds_budget(caplog):
    import app

    settings, spec = _settings_and_spec()
    broker = _Broker(spec, equity=7)
    risk = RiskManager(RiskConfig(risk_per_trade=.0025, max_lots=.5), 7)

    opened = app.try_open_trade(
        broker, risk, settings, pd.Timestamp("2026-10-14T12:00:00Z"),
        signal=1, atr_value=.0001, price=1.1,
    )

    assert not opened
    assert broker.orders == []
    assert "volumen 0.10" in caplog.text
    assert "pérdida estimada USD" in caplog.text
    assert "presupuesto USD 0.0175" in caplog.text


def test_eurusdmicro_requested_volume_is_rejected_when_margin_is_insufficient(caplog):
    import app

    settings, spec = _settings_and_spec()
    broker = _Broker(spec, equity=1_000, free_margin=.01, required_margin=5)
    risk = RiskManager(RiskConfig(risk_per_trade=.01, max_lots=.5), 1_000)

    opened = app.try_open_trade(
        broker, risk, settings, pd.Timestamp("2026-10-14T12:00:00Z"),
        signal=1, atr_value=.0001, price=1.1,
    )

    assert not opened
    assert broker.orders == []
    assert "pérdida estimada USD 0.7150" in caplog.text
    assert "margen requerido USD 5.0000 supera margen libre USD 0.0100" in caplog.text


def test_explicit_volume_validation_rejects_invalid_mt5_ranges_steps_and_risk_cap():
    settings, spec = _settings_and_spec()
    risk = RiskManager(RiskConfig(risk_per_trade=.01, max_lots=.5), 1_000)

    assert risk.validate_requested_volume(.10, spec) == (True, "ok")
    assert not risk.validate_requested_volume(.09, spec)[0]
    assert not risk.validate_requested_volume(.105, spec)[0]
    assert not risk.validate_requested_volume(.51, spec)[0]


def test_autonomous_preflight_estimates_the_explicit_volume_and_rejects_over_budget(tmp_path):
    from src.autonomous_agent import AutonomousAgent

    settings, spec = _settings_and_spec()
    broker = _Broker(spec, equity=7)
    agent = AutonomousAgent(settings, broker, None, lambda: None,
                            tmp_path / "decisions.jsonl", tmp_path / "state.json")
    agent.attach_risk_manager(RiskManager(
        RiskConfig(risk_per_trade=.0025, max_lots=.5), 7,
    ))

    estimate = agent._estimate_risk(side=1, atr_value=.0001)

    assert estimate["requested_volume"] == .10
    assert estimate["volume"] == .10
    assert estimate["risk_loss_estimate"] > estimate["risk_budget"]
    assert estimate["reason"].startswith("pérdida estimada USD")
    assert not estimate["within_budget"]
