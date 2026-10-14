import json
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from config import RiskConfig, Settings, SymbolSpec
from src.autonomous_agent import AutonomousAgent
from src.broker_demo import Position
from src.risk_manager import RiskManager
from src.strategy_evaluator import Evaluation


class FakeBroker:
    def __init__(self, spread=1.0):
        self.spread = spread
        self.evaluations = 0
        self.orders = 0
        self.positions = []

    def market_conditions(self):
        return {"ok": True, "spread_pips": self.spread, "bid": 1.1, "ask": 1.1001}

    def open_positions(self):
        return list(self.positions)

    def equity(self):
        return 1000.0


class ExecutableFakeBroker(FakeBroker):
    def __init__(self):
        super().__init__()
        self.spec = SymbolSpec("EURUSD", .0001, min_lot=.01, max_lot=5, lot_step=.01)
        self.mt5 = SimpleNamespace(terminal_info=lambda: object(),
                                   account_info=lambda: SimpleNamespace(margin_free=1000),
                                   symbol_info=lambda symbol: SimpleNamespace(point=.00001))

    def entry_quote(self, side):
        return 1.1

    def loss_per_lot(self, side, entry, sl):
        return 1.0

    def margin_for(self, side, lots, price):
        return 1.0

    def market_order(self, side, lots, sl, tp, **kwargs):
        self.orders += 1
        pos = Position(self.orders, "EURUSD", side, lots, 1.1, sl, tp, pd.Timestamp.now(tz="UTC"))
        self.positions.append(pos)
        return pos


class ReconnectableFakeBroker(FakeBroker):
    def __init__(self):
        super().__init__()
        self.mt5 = SimpleNamespace(terminal_info=lambda: None)
        self.reconnects = 0

    def disconnect(self):
        self.mt5 = None

    def connect(self):
        self.reconnects += 1
        self.mt5 = SimpleNamespace(terminal_info=lambda: SimpleNamespace(connected=True))


class SelectingEvaluator:
    def __init__(self):
        self.calls = 0

    def evaluate(self, ind, config):
        self.calls += 1
        signals = pd.DataFrame({"rules": 1}, index=ind.index)
        return Evaluation("rules", "validation_filters_passed",
                          {"rules": {"validation_periods": []}}, None, signals)


class FakeEvaluator:
    def __init__(self, signal=0):
        self.signal = signal
        self.calls = 0

    def evaluate(self, ind, config):
        self.calls += 1
        signals = pd.DataFrame({"rules": self.signal}, index=ind.index)
        return Evaluation(None, "no_candidate_passed_all_validation_periods", {}, None, signals)


def _data():
    end = pd.Timestamp.now(tz="UTC").floor("15min") - pd.Timedelta(minutes=15)
    idx = pd.date_range(end=end, periods=1600, freq="15min")
    close = 1.1 + np.linspace(0, 0.01, len(idx)) + np.sin(np.arange(len(idx)) / 20) * 0.0002
    op = np.r_[close[0], close[:-1]]
    return pd.DataFrame({"open": op, "high": np.maximum(op, close) + .0002,
                         "low": np.minimum(op, close) - .0002, "close": close,
                         "volume": 1000}, index=idx)


def _agent(tmp_path, broker=None, evaluator=None, fetch=None):
    settings = Settings(mt5=None)
    settings.market.timeframe = "M15"
    broker = broker or FakeBroker()
    evaluator = evaluator or FakeEvaluator()
    agent = AutonomousAgent(settings, broker, evaluator, fetch or _data,
                            tmp_path / "decisions.jsonl", tmp_path / "state.json")
    agent.risk_manager = RiskManager(settings.risk, 1000)
    return agent, broker, evaluator


def test_cycle_skips_repeated_closed_candle_and_logs_decisions(tmp_path):
    agent, _, evaluator = _agent(tmp_path)
    first = agent.cycle()
    second = agent.cycle()
    assert first.action == "NO TRADE"
    assert "vela ya procesada" in second.reasons[0]
    assert evaluator.calls == 1
    records = [json.loads(line) for line in (tmp_path / "decisions.jsonl").read_text().splitlines()]
    assert len(records) == 2 and all(r["action"] == "NO TRADE" for r in records)
    assert (tmp_path / "state.json").exists()


def test_corrupt_persistent_state_fails_closed(tmp_path):
    settings = Settings(mt5=None)
    state = tmp_path / "state.json"
    state.write_text("{broken")
    evaluator = SelectingEvaluator()
    broker = ExecutableFakeBroker()
    agent = AutonomousAgent(settings, broker, evaluator, _data,
                            tmp_path / "decisions.jsonl", state, execute=True)
    result = agent.cycle()
    assert result.action == "NO TRADE"
    assert evaluator.calls == 0
    assert broker.orders == 0


def test_cycle_blocks_excessive_spread_before_strategy_evaluation(tmp_path):
    broker, evaluator = FakeBroker(spread=4), FakeEvaluator(signal=1)
    agent, _, _ = _agent(tmp_path, broker, evaluator)
    result = agent.cycle()
    assert result.action == "NO TRADE"
    assert evaluator.calls == 0
    assert "spread" in result.reasons[0]


def test_cycle_blocks_incomplete_market_data(tmp_path):
    evaluator = FakeEvaluator()
    frame = _data()
    frame.iloc[-1, frame.columns.get_loc("close")] = np.nan
    agent, _, _ = _agent(tmp_path, evaluator=evaluator, fetch=lambda: frame)
    result = agent.cycle()
    assert result.action == "NO TRADE"
    assert evaluator.calls == 0


def test_cycle_blocks_new_entry_if_existing_position_has_invalid_stops(tmp_path):
    broker, evaluator = FakeBroker(), SelectingEvaluator()
    broker.positions = [Position(1, "EURUSD", 1, .1, 1.1, 0, 1.2,
                                 pd.Timestamp.now(tz="UTC") - pd.Timedelta(minutes=15))]
    agent, _, _ = _agent(tmp_path, broker, evaluator)
    agent.execute = True
    result = agent.cycle()
    assert result.action == "NO TRADE"
    assert "revisión inmediata" in " ".join(result.reasons)
    assert broker.orders == 0


def test_cycle_recovers_after_fetch_disconnect_without_order(tmp_path):
    calls = {"n": 0}

    def fetch():
        calls["n"] += 1
        if calls["n"] == 1:
            raise ConnectionError("ipc")
        return _data()

    agent, broker, _ = _agent(tmp_path, fetch=fetch)
    assert agent.cycle().action == "NO TRADE"
    result = agent.cycle()
    assert result.action == "NO TRADE"
    assert calls["n"] == 2 and broker.orders == 0


def test_cycle_reconnects_terminal_before_fetch(tmp_path):
    broker, evaluator = ReconnectableFakeBroker(), FakeEvaluator()
    agent, _, _ = _agent(tmp_path, broker, evaluator)
    result = agent.cycle()
    assert result.action == "NO TRADE"
    assert broker.reconnects == 1
    assert evaluator.calls == 1


def test_execution_attempt_is_not_repeated_for_same_closed_candle(tmp_path):
    broker, evaluator = ExecutableFakeBroker(), SelectingEvaluator()
    agent, _, _ = _agent(tmp_path, broker, evaluator)
    agent.execute = True
    first = agent.cycle()
    second = agent.cycle()
    assert first.action == "BUY"
    assert broker.orders == 1
    assert "vela ya procesada" in second.reasons[0]
    assert evaluator.calls == 1


@pytest.mark.parametrize("minutes,expected", [(10, [600, 600]), (15, [900, 900])])
def test_cycle_interval_is_configurable(minutes, expected, tmp_path):
    agent, _, _ = _agent(tmp_path)
    cycles = {"count": 0}
    sleeps = []

    def cycle():
        cycles["count"] += 1

    agent.cycle = cycle

    def sleep(seconds):
        sleeps.append(seconds)

    agent.run_forever(minutes, sleep_fn=sleep, stop=lambda: len(sleeps) >= 2)
    assert sleeps == expected
    assert cycles["count"] == 2


def test_cycle_rejects_unsupported_interval(tmp_path):
    agent, _, _ = _agent(tmp_path)
    with pytest.raises(ValueError):
        agent.run_forever(12, stop=lambda: True)


def test_strategy_evaluation_abstains_without_history(tmp_path):
    from config import BacktestConfig, StrategyConfig
    from src.strategy_evaluator import StrategyEvaluator
    evaluator = StrategyEvaluator(SymbolSpec("EURUSD", .0001), BacktestConfig(),
                                  RiskConfig(), StrategyConfig())
    ind = pd.DataFrame(index=pd.date_range("2025-01-01", periods=100, freq="15min", tz="UTC"))
    result = evaluator.evaluate(ind, Settings(mt5=None).model)
    assert result.selected is None
    assert result.reason == "insufficient_history"


@pytest.mark.parametrize("symbol,min_lot,contract,tick_value", [
    ("EURUSDmicro", .1, 1000, .01),
    ("EURUSD", .01, 100000, 1.0),
    ("EURCADmicro", .1, 1000, .01),
    ("EURCAD", .01, 100000, 1.0),
])
def test_minimum_volume_risk_never_rounds_up(symbol, min_lot, contract, tick_value):
    spec = SymbolSpec(symbol, .0001, contract_size=contract, min_lot=min_lot,
                      max_lot=100, lot_step=.01, tick_size=.00001,
                      tick_value_profit=tick_value, tick_value_loss=tick_value)
    risk = RiskManager(RiskConfig(risk_per_trade=.0025), 7.0)
    # 100 pips of stop; tick values are fixture values, not live XM specifications.
    loss = (0.01 / .00001) * tick_value
    lots = risk.position_size_for_risk(7.0, loss, spec)
    if loss * min_lot > 7.0 * .0025:
        assert lots == 0
    else:
        assert lots >= min_lot
        assert lots * loss <= 7.0 * .0025
