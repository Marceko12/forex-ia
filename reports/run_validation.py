r"""Reproducible, chronological validation for the locally saved MT5 CSV.

Run from the project root with .venv\Scripts\python.exe reports\run_validation.py
No MT5 connection or order submission is performed.
"""
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
from sklearn.metrics import accuracy_score, roc_auc_score

from config import BacktestConfig, ModelConfig, RiskConfig, StrategyConfig, SymbolSpec, get_settings
from src.ai_model import AIModel, build_labels
from src.backtester import Backtester
from src.indicators import add_all_indicators
from src.market_data import load_csv
from src.strategy import build_signals


DATA_PATH = ROOT / "data" / "market_data.csv"
OUTPUT_PATH = ROOT / "reports" / "validation_results.json"
INITIAL_BALANCE = 10_000.0


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


def run_backtest(df, signals, spec, label, cfg, risk_cfg=None):
    result = Backtester(
        spec, cfg, risk_cfg or RiskConfig(), StrategyConfig(mode="rules"), symbol=spec.symbol
    ).run(df, signals)
    return {"run": label, **result.metrics}


def prediction_metrics(df, proba, model_cfg):
    labels = build_labels(df, model_cfg.horizon, model_cfg.label_atr_mult)
    sample = pd.concat([labels.rename("label"), proba.rename("proba")], axis=1).dropna()
    if sample.empty:
        return {"rows": 0, "accuracy": None, "auc": None, "base_rate_up": None}
    y = sample["label"].astype(int)
    pred = sample["proba"] >= 0.5
    auc = roc_auc_score(y, sample["proba"]) if y.nunique() > 1 else None
    return {"rows": int(len(sample)), "accuracy": float(accuracy_score(y, pred)),
            "auc": float(auc) if auc is not None else None, "base_rate_up": float(y.mean())}


def main():
    raw_bytes = DATA_PATH.read_bytes()
    raw = load_csv(DATA_PATH)
    indicators = add_all_indicators(raw)
    n = len(indicators)
    train_end, validation_end = int(n * .60), int(n * .80)
    train = indicators.iloc[:train_end]
    validation = indicators.iloc[train_end:validation_end]
    test = indicators.iloc[validation_end:]

    # The file is named EURUSDmicro in the application log. These provisional
    # public XM Micro contract assumptions are not confirmed against this account.
    spec = SymbolSpec("EURUSDmicro", 0.0001, contract_size=1_000,
                      min_lot=0.1, max_lot=100.0, lot_step=0.01)
    model_cfg = ModelConfig()
    model = AIModel(model_cfg)
    internal_validation = model.train(train)
    probabilities = model.predict_proba(indicators)
    rules = build_signals(indicators, None, StrategyConfig(mode="rules"))
    hybrid = build_signals(indicators, probabilities, StrategyConfig(mode="hybrid"))

    cost_cases = [
        {"name": "base_assumption", "spread_pips": 1.5, "commission_per_1000_unit_lot_usd": 0.0, "slippage_pips_each_side": 0.2},
        {"name": "higher_cost", "spread_pips": 2.0, "commission_per_1000_unit_lot_usd": 0.07, "slippage_pips_each_side": 0.5},
        {"name": "stress_cost", "spread_pips": 3.0, "commission_per_1000_unit_lot_usd": 0.14, "slippage_pips_each_side": 1.0},
    ]
    # USD 7 is evaluated separately. No costs are tuned on validation/test.
    result = {
        "generated_at_utc": pd.Timestamp.now(tz="UTC").isoformat(),
        "data": {"path": "data/market_data.csv", "sha256": hashlib.sha256(raw_bytes).hexdigest(),
                 "rows": n, "symbol_evidence": "EURUSDmicro, inferred from logs and matching file modification time",
                 "broker_identity_and_specs_confirmed": False,
                 "provisional_micro_contract_assumption": {"contract_size": 1000, "min_lot": 0.1,
                                                           "max_lot": 100, "volume_step": 0.01}},
        "split": {"method": "chronological 60/20/20; no parameter tuning on validation or test",
                  "train_rows": len(train), "validation_rows": len(validation), "test_rows": len(test),
                  "train_start": train.index[0].isoformat(), "train_end": train.index[-1].isoformat(),
                  "validation_start": validation.index[0].isoformat(), "validation_end": validation.index[-1].isoformat(),
                  "test_start": test.index[0].isoformat(), "test_end": test.index[-1].isoformat()},
        "model": {"fit_data": "training segment only; AIModel internal chronological 80/20 split with horizon purge",
                  "internal_validation": internal_validation,
                  "external_validation": prediction_metrics(validation, probabilities.loc[validation.index], model_cfg),
                  "test_prediction_metrics": prediction_metrics(test, probabilities.loc[test.index], model_cfg)},
        "cost_sensitivity": [],
        "min_lot_risk_feasibility": [],
        "baseline_no_trade": {"return_pct": 0.0, "expectancy": None, "profit_factor": None,
                              "max_drawdown_pct": 0.0, "trades": 0, "meaning": "flat cash; no predictive advantage"},
        "capital_7_usd": [],
        "limitations": [
            "CSV provenance is supported by local logs but not independently authenticated or tied to a recorded account ID.",
            "EURUSDmicro contract and volume assumptions are provisional; the terminal's symbol_info was not obtained.",
            "Spread, commission, and slippage values are sensitivity assumptions, not observed XM charges.",
            "Swap/financing cannot be quantified without the account-specific rate schedule; it is excluded.",
            "One CSV symbol and a short history are insufficient to claim a durable trading edge.",
        ],
    }

    for period_name, period in (("validation", validation), ("test", test)):
        for case in cost_cases:
            bt_cfg = BacktestConfig(initial_balance=INITIAL_BALANCE,
                                    spread_pips=case["spread_pips"],
                                    commission_per_lot=case["commission_per_1000_unit_lot_usd"],
                                    slippage_pips=case["slippage_pips_each_side"])
            for strat_name, signal_series in (("rules", rules), ("hybrid", hybrid)):
                record = run_backtest(period, signal_series.loc[period.index], spec,
                                      f"{period_name}:{strat_name}:{case['name']}", bt_cfg)
                result["cost_sensitivity"].append({"period": period_name, "strategy": strat_name,
                                                   "cost_case": case, "metrics": record})

    # Approximate $7 test with same published Micro assumption; this is not a
    # substitute for order_calc_profit against the actual connected account.
    settings = get_settings()
    for risk_name, risk_cfg in (("default_demo_risk", settings.risk), ("live_config_risk", settings.live_risk)):
        for case in cost_cases:
            atr_values = test["atr"].dropna()
            pip_value = spec.contract_size * spec.pip_size  # EURUSD quote currency USD
            sl_pips = atr_values * 1.5 / spec.pip_size
            per_lot_cost = (pip_value * (case["spread_pips"] + 2 * case["slippage_pips_each_side"])
                            + case["commission_per_1000_unit_lot_usd"])
            min_lot_risk = (sl_pips * pip_value + per_lot_cost) * spec.min_lot
            if risk_name == "default_demo_risk":
                result["min_lot_risk_feasibility"].append({
                    "cost_case": case, "volume_min_assumption": spec.min_lot,
                    "estimated_stop_loss_cost_usd_quantiles": {
                        str(q): float(min_lot_risk.quantile(q)) for q in (.05, .5, .95)},
                    "default_budget_usd": 7.0 * settings.risk.risk_per_trade,
                    "default_eligible_bar_fraction": float((min_lot_risk <= 7.0 * settings.risk.risk_per_trade).mean()),
                    "live_budget_usd": 7.0 * settings.live_risk.risk_per_trade,
                    "live_eligible_bar_fraction": float((min_lot_risk <= 7.0 * settings.live_risk.risk_per_trade).mean()),
                    "actual_broker_verified": False,
                })
            bt_cfg = BacktestConfig(initial_balance=7.0, spread_pips=case["spread_pips"],
                                    commission_per_lot=case["commission_per_1000_unit_lot_usd"],
                                    slippage_pips=case["slippage_pips_each_side"])
            rec = run_backtest(test, rules.loc[test.index], spec,
                               f"capital7:{risk_name}:{case['name']}", bt_cfg, risk_cfg)
            result["capital_7_usd"].append({"risk_profile": risk_name, "risk_per_trade_pct": risk_cfg.risk_per_trade * 100,
                                            "cost_case": case, "metrics": rec})

    # Verify that the earlier 70/30 reporting setup can be rerun; this is a
    # comparison, not the final validation protocol, and uses the prior generic
    # standard-contract assumption for an apples-to-apples diagnostic only.
    old_cut = int(n * .70)
    old_test = indicators.iloc[old_cut:]
    old_spec = SymbolSpec("EURUSD", 0.0001)
    old_bt = BacktestConfig(initial_balance=INITIAL_BALANCE, spread_pips=1.0,
                            commission_per_lot=7.0, slippage_pips=0.0)
    prior_model = AIModel(model_cfg)
    prior_model_metrics = prior_model.train(indicators.iloc[:old_cut])
    prior_hybrid = build_signals(indicators, prior_model.predict_proba(indicators),
                                 StrategyConfig(mode="hybrid"))
    result["prior_report_reproduction_attempt"] = {
        "not_account_specific": True,
        "rules": run_backtest(old_test, rules.loc[old_test.index], old_spec, "rules", old_bt),
        "hybrid": run_backtest(old_test, prior_hybrid.loc[old_test.index], old_spec, "hybrid", old_bt),
        "model_internal_validation": prior_model_metrics,
        "reported_before": {"rules": {"final_equity": 10717.10, "max_drawdown_pct": -7.95,
                                       "trades": 68, "profit_factor": 1.15, "expectancy": 10.55},
                            "hybrid": {"final_equity": 10902.22, "max_drawdown_pct": -3.72,
                                       "trades": 20, "profit_factor": 1.77, "expectancy": 45.11,
                                       "model_auc": 0.509}},
    }
    OUTPUT_PATH.write_text(json.dumps(clean(result), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Wrote {OUTPUT_PATH}")
    print(f"Rows: {n}; split: {len(train)}/{len(validation)}/{len(test)}")
    print(f"Internal AUC: {internal_validation.get('val_auc')}; external val: {result['model']['external_validation']}; test: {result['model']['test_prediction_metrics']}")
    print(f"Prior report rules attempt: {result['prior_report_reproduction_attempt']['rules']}")
    print(f"Prior report hybrid attempt: {result['prior_report_reproduction_attempt']['hybrid']}")
    for row in result["cost_sensitivity"]:
        print(row["period"], row["strategy"], row["cost_case"]["name"], row["metrics"])
    for row in result["capital_7_usd"]:
        print("capital7", row["cost_case"]["name"], row["metrics"])


if __name__ == "__main__":
    main()
