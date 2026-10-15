"""Tests del gestor de riesgo."""
import pandas as pd

from config import RiskConfig, get_symbol_spec
from src.risk_manager import RiskManager

EURUSD = get_symbol_spec("EURUSD")
USDJPY = get_symbol_spec("USDJPY")


def ts(day: int, hour: int = 10) -> pd.Timestamp:
    return pd.Timestamp(2024, 1, day, hour, tz="UTC")


def test_position_size_matches_risk():
    rm = RiskManager(RiskConfig(risk_per_trade=0.01), 10_000)
    # Riesgo = 100 USD; SL = 20 pips; 1 pip por lote = 10 USD -> 0.5 lotes
    assert rm.position_size(10_000, 20, EURUSD, 1.10) == 0.5


def test_position_size_scales_with_equity_and_sl():
    rm = RiskManager(RiskConfig(risk_per_trade=0.01), 10_000)
    small = rm.position_size(10_000, 40, EURUSD, 1.10)
    wide = rm.position_size(20_000, 40, EURUSD, 1.10)
    assert small == 0.25 and wide == 0.5


def test_position_size_rounds_down_never_up():
    rm = RiskManager(RiskConfig(risk_per_trade=0.01), 10_000)
    lots = rm.position_size(10_000, 33, EURUSD, 1.10)  # 0.303 -> 0.30
    assert lots == 0.30
    assert lots * 33 * 10 <= 100  # nunca arriesga más del 1%


def test_position_size_zero_if_min_lot_exceeds_risk():
    rm = RiskManager(RiskConfig(risk_per_trade=0.01), 100)  # riesgo 1 USD
    assert rm.position_size(100, 50, EURUSD, 1.10) == 0.0   # 0.01 lote ya arriesgaría 5 USD


def test_position_size_respects_max_lots():
    rm = RiskManager(RiskConfig(risk_per_trade=0.5, max_lots=2.0), 1_000_000)
    assert rm.position_size(1_000_000, 10, EURUSD, 1.10) == 2.0


def test_position_size_from_broker_money_loss_rounds_down():
    rm = RiskManager(RiskConfig(risk_per_trade=0.01), 100)
    # Pérdida broker-calculada de 3.40 USD/lote, presupuesto 1 USD.
    assert rm.position_size_for_risk(100, 3.40, EURUSD) == 0.29


def test_spread_and_commission_estimates_reduce_position_size():
    rm = RiskManager(RiskConfig(risk_per_trade=0.01), 10_000)
    without_cost = rm.position_size(10_000, 20, EURUSD, 1.10)
    with_cost = rm.position_size(10_000, 20, EURUSD, 1.10, cost_per_lot=17.0)
    assert with_cost < without_cost


def test_nonzero_volume_origin_and_irregular_step_stay_valid():
    from config import SymbolSpec

    spec = SymbolSpec("TESTUSD", 0.0001, min_lot=0.1, max_lot=1.15, lot_step=0.25)
    rm = RiskManager(RiskConfig(risk_per_trade=0.01, max_lots=1.15), 1_000)
    lots = rm.position_size_for_risk(1_000, 1.0, spec)
    assert lots == 1.1
    assert (lots - spec.min_lot) / spec.lot_step == 4


def test_mt5_tick_spec_multiple_sl_distances_never_exceeds_money_risk():
    """Representative MT5 symbol_info tick values; pure deterministic fixture."""
    from config import SymbolSpec

    spec = SymbolSpec(
        "EURUSDmicro", 0.0001, contract_size=1_000,
        min_lot=0.1, max_lot=10.0, lot_step=0.1,
        tick_size=0.00001, tick_value_profit=0.10, tick_value_loss=0.10,
        point=0.00001, digits=5, currency_profit="USD",
    )
    equity, risk_fraction = 1_000.0, 0.01
    budget = equity * risk_fraction
    rm = RiskManager(RiskConfig(risk_per_trade=risk_fraction, max_lots=10.0), equity)

    # $0.10/tick * 10 ticks/pip = $1/pip/lot for this illustrative spec.
    for stop_pips, expected_lots in ((5, 2.0), (10, 1.0), (20, 0.5)):
        lots = rm.position_size(equity, stop_pips, spec, 1.10)
        loss_at_sl = stop_pips * spec.pip_value_per_lot(1.10) * lots
        assert lots == expected_lots
        assert lots >= spec.min_lot
        assert abs((lots - spec.min_lot) / spec.lot_step - round(
            (lots - spec.min_lot) / spec.lot_step
        )) < 1e-9
        assert loss_at_sl <= budget + 1e-12


def test_tick_spec_minimum_volume_is_rejected_when_stop_risk_exceeds_budget():
    from config import SymbolSpec

    spec = SymbolSpec(
        "EURUSDmicro", 0.0001, contract_size=1_000,
        min_lot=0.1, max_lot=10.0, lot_step=0.1,
        tick_size=0.00001, tick_value_profit=0.10, tick_value_loss=0.10,
        currency_profit="USD",
    )
    rm = RiskManager(RiskConfig(risk_per_trade=0.0025), 100.0)
    # 50 pips x $1/pip/lot x 0.1 minimum lot = $5, over the $0.25 budget.
    assert rm.position_size(100.0, 50.0, spec, 1.10) == 0.0


def test_position_size_invalid_inputs():
    rm = RiskManager(RiskConfig(), 10_000)
    assert rm.position_size(10_000, 0, EURUSD, 1.10) == 0.0
    assert rm.position_size(0, 20, EURUSD, 1.10) == 0.0


def test_jpy_pair_pip_value_uses_price():
    # USDJPY: 100000 * 0.01 / 150 = 6.67 USD por pip y lote
    assert abs(USDJPY.pip_value_per_lot(150.0) - 1000 / 150) < 1e-9


def test_daily_loss_limit_blocks_then_resets_next_day():
    rm = RiskManager(RiskConfig(max_daily_loss=0.03), 10_000)
    assert rm.can_open(ts(2), 10_000)[0]
    ok, why = rm.can_open(ts(2, 15), 9_650)       # -3.5% en el día
    assert not ok and "diaria" in why
    assert rm.can_open(ts(3), 9_650)[0]            # nuevo día: contador reiniciado


def test_max_drawdown_halts_permanently():
    rm = RiskManager(RiskConfig(max_drawdown=0.10, max_daily_loss=0.5), 10_000)
    rm.update(ts(2), 11_000)                        # nuevo pico
    ok, _ = rm.can_open(ts(3), 9_800)               # -10.9% desde el pico
    assert not ok and rm.halted
    assert not rm.can_open(ts(10), 12_000)[0]       # sigue detenido aunque se recupere


def test_max_open_positions():
    rm = RiskManager(RiskConfig(max_open_positions=1), 10_000)
    assert rm.can_open(ts(2), 10_000, open_positions=0)[0]
    assert not rm.can_open(ts(2), 10_000, open_positions=1)[0]


def test_max_trades_per_day():
    rm = RiskManager(RiskConfig(max_trades_per_day=2), 10_000)
    for _ in range(2):
        assert rm.can_open(ts(2), 10_000)[0]
        rm.register_open()
    assert not rm.can_open(ts(2, 20), 10_000)[0]
    assert rm.can_open(ts(3), 10_000)[0]


def test_invalid_equity_rejected():
    try:
        RiskManager(RiskConfig(), 0)
    except ValueError:
        return
    raise AssertionError("Debió lanzar ValueError")
