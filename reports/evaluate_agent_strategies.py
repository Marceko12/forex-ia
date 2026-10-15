"""Reproducibly evaluate agent candidates on chronological data; never places orders."""
from __future__ import annotations

import hashlib
import json
import math
import sys
import argparse
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
    parser = argparse.ArgumentParser(description="Diagnóstico de recetas sobre CSV; no conecta MT5 ni opera.")
    parser.add_argument("--data", type=Path, default=DATA, help="CSV OHLCV; se admite manifest lateral del recolector MT5")
    parser.add_argument("--output", type=Path, default=OUT)
    parser.add_argument("--symbol", help="Símbolo exacto; por defecto se toma del manifest o del CSV histórico del proyecto")
    args = parser.parse_args()
    data_path = args.data
    raw_bytes = data_path.read_bytes()
    df = load_csv(data_path)
    manifest_path = data_path.with_suffix(data_path.suffix + ".manifest.json")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else None
    symbol = args.symbol or (manifest.get("symbol") if manifest else None)
    if not symbol:
        if data_path.resolve() == DATA.resolve():
            symbol = "EURUSDmicro"  # inferencia local, nunca verificación
        else:
            raise ValueError("Indica --symbol o usa el manifest creado por download-range.")
    manifest_hash_ok = bool(manifest and manifest.get("sha256") == hashlib.sha256(raw_bytes).hexdigest())
    manifest_matches = bool(manifest_hash_ok and manifest.get("source") == "MetaTrader5.copy_rates_range"
                            and manifest.get("symbol") == symbol)
    symbol_fields = (manifest or {}).get("symbol_specifications", {})
    specs_ok = bool(manifest_matches and all(
        float(symbol_fields.get(name) or 0) > 0
        for name in ("volume_min", "volume_max", "volume_step", "trade_tick_size",
                     "trade_tick_value_loss", "trade_contract_size")
    ))
    settings = get_settings()
    settings.market.symbol = symbol
    ind = add_all_indicators(df, settings.indicators)
    if specs_ok:
        spec = SymbolSpec(
            symbol, .01 if "JPY" in symbol.upper()[:6] else .0001,
            contract_size=float(symbol_fields["trade_contract_size"]),
            min_lot=float(symbol_fields["volume_min"]), max_lot=float(symbol_fields["volume_max"]),
            lot_step=float(symbol_fields["volume_step"]),
            tick_size=float(symbol_fields["trade_tick_size"]),
            tick_value_profit=float(symbol_fields.get("trade_tick_value_profit") or 0),
            tick_value_loss=float(symbol_fields["trade_tick_value_loss"]),
            point=float(symbol_fields.get("point") or 0), digits=int(symbol_fields.get("digits") or 0),
            trade_stops_level=int(symbol_fields.get("trade_stops_level") or 0),
            currency_profit=str(symbol_fields.get("currency_profit") or ""),
            currency_margin=str(symbol_fields.get("currency_margin") or ""),
        )
    else:
        if data_path.resolve() == DATA.resolve() and symbol == "EURUSDmicro":
            spec = SymbolSpec(symbol, .0001, contract_size=1000, min_lot=.1,
                              max_lot=100, lot_step=.01)
        else:
            spec = SymbolSpec(symbol, .01 if "JPY" in symbol.upper()[:6] else .0001)
    evaluator = StrategyEvaluator(spec, settings.backtest, settings.risk, settings.strategy)
    evaluator.source_verified = manifest_matches and specs_ok
    result = evaluator.evaluate(ind, settings.model)
    train_end, selection_end, validation_end = int(len(ind) * .50), int(len(ind) * .70), int(len(ind) * .85)
    payload = {
        "data": {"path": str(data_path), "sha256": hashlib.sha256(raw_bytes).hexdigest(),
                 "rows": len(ind), "first": ind.index[0].isoformat(), "last": ind.index[-1].isoformat(),
                 "source_authenticated": False,
                 "manifest_path": str(manifest_path) if manifest else None,
                 "manifest_hash_matches": manifest_hash_ok,
                 "source_verified_locally": bool(evaluator.source_verified),
                 "provisional_spec_assumption": None if specs_ok else {
                     "symbol": symbol, "contract_size": spec.contract_size,
                     "volume_min": spec.min_lot, "volume_max": spec.max_lot,
                     "volume_step": spec.lot_step,
                 },
                 "manifest_completeness_claim": manifest.get("complete") if manifest else None},
        "cost_assumptions": {"spread_pips": settings.backtest.spread_pips,
                             "commission_per_lot": settings.backtest.commission_per_lot,
                             "slippage_pips_each_side": settings.backtest.slippage_pips,
                             "account_symbol_spec_confirmed": bool(specs_ok)},
        "split": {
            "method": "chronological train/selection/validation/final-test 50/20/15/15",
            "train": {"rows": train_end, "start": ind.index[0].isoformat(),
                      "end": ind.index[train_end - 1].isoformat()},
            "selection": {"rows": selection_end - train_end,
                          "start": ind.index[train_end].isoformat(),
                          "end": ind.index[selection_end - 1].isoformat()},
            "validation": {"rows": validation_end - selection_end,
                           "start": ind.index[selection_end].isoformat(),
                           "end": ind.index[validation_end - 1].isoformat()},
            "final_test": {"rows": len(ind) - validation_end,
                           "start": ind.index[validation_end].isoformat(),
                           "end": ind.index[-1].isoformat(),
                           "use": "diagnostic only; not used to select or tune"},
        },
        "selection": {"selected": result.selected, "action_if_current_signal": "NO TRADE" if result.selected is None else "depends_on_validated_signal",
                      "reason": result.reason, "selection_uses": "three selection subperiods; independent validation gate",
                      "source_verified": False,
                      "test_use": "diagnostic only, only calculated after prior gates pass"},
        "candidate_metrics": result.strategies,
        "limits": ["CSV provenance is locally supported but not authenticated.",
                   "EURUSDmicro contract/volume and all costs are assumptions, not queried MT5 specifications.",
                   "No order or MT5 connection is performed by this report script.",
                   "A passing historical filter would not establish a durable edge or guarantee outcomes."],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(clean(payload), indent=2, ensure_ascii=False, allow_nan=False) + "\n",
                   encoding="utf-8")
    print(f"selected={result.selected}; reason={result.reason}")
    print(f"saved={args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
