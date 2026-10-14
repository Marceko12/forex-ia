"""Reproducibly evaluate agent candidates on chronological data; never places orders."""
from __future__ import annotations

import hashlib
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import numpy as np
import pandas as pd

from config import BacktestConfig, RiskConfig, StrategyConfig, SymbolSpec, get_settings
from src.indicators import add_all_indicators
from src.market_data import load_csv
from src.strategy_evaluator import StrategyEvaluator


DATA = ROOT / "data" / "market_data.csv"
OUT = ROOT / "reports" / "agent_strategy_evaluation.json"


def clean(value):
    if isinstance(value, dict):
        return {str(k): clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [clean(v) for v in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        return float(value) if math.isfinite(float(value)) else None
    return value


def main() -> int:
    raw_bytes = DATA.read_bytes()
    df = load_csv(DATA)
    settings = get_settings()
    settings.market.symbol = "EURUSDmicro"
    ind = add_all_indicators(df, settings.indicators)
    spec = SymbolSpec("EURUSDmicro", .0001, contract_size=1000,
                      min_lot=.1, max_lot=100, lot_step=.01)
    evaluator = StrategyEvaluator(spec, settings.backtest, settings.risk, settings.strategy)
    result = evaluator.evaluate(ind, settings.model)
    payload = {
        "data": {"path": "data/market_data.csv", "sha256": hashlib.sha256(raw_bytes).hexdigest(),
                 "rows": len(ind), "first": ind.index[0].isoformat(), "last": ind.index[-1].isoformat(),
                 "source_authenticated": False},
        "cost_assumptions": {"spread_pips": settings.backtest.spread_pips,
                             "commission_per_lot": settings.backtest.commission_per_lot,
                             "slippage_pips_each_side": settings.backtest.slippage_pips,
                             "account_symbol_spec_confirmed": False},
        "selection": {"selected": result.selected, "action_if_current_signal": "NO TRADE" if result.selected is None else "depends_on_validated_signal",
                      "reason": result.reason, "selection_uses": "3 chronological validation subperiods only",
                      "test_use": "diagnostic only; excluded from strategy selection"},
        "candidate_metrics": result.strategies,
        "limits": ["CSV provenance is locally supported but not authenticated.",
                   "EURUSDmicro contract/volume and all costs are assumptions, not queried MT5 specifications.",
                   "No order or MT5 connection is performed by this report script.",
                   "A passing historical filter would not establish a durable edge or guarantee outcomes."],
    }
    OUT.write_text(json.dumps(clean(payload), indent=2, ensure_ascii=False, allow_nan=False) + "\n",
                   encoding="utf-8")
    print(f"selected={result.selected}; reason={result.reason}")
    print(f"saved={OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
