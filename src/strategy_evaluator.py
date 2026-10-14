"""Chronological strategy comparison; selection uses validation only."""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from config import BacktestConfig, RiskConfig, StrategyConfig, SymbolSpec
from src.ai_model import AIModel
from src.backtester import Backtester
from src.strategy import build_signals


@dataclass
class Evaluation:
    selected: str | None
    reason: str
    strategies: dict
    model: AIModel | None
    signals: pd.DataFrame


class StrategyEvaluator:
    """Compare rules/AI/hybrid on a purged temporal train/validation/test split.

    The newest test segment is diagnostic only and never participates in selection.
    Each validation third must independently pass the minimum-trade, expectancy,
    profit-factor, return and drawdown filters or the result is NO TRADE.
    """

    CANDIDATES = ("rules", "ai", "hybrid")

    def __init__(self, spec: SymbolSpec, backtest: BacktestConfig, risk: RiskConfig,
                 strategy: StrategyConfig, min_trades_per_period: int = 5,
                 min_profit_factor: float = 1.05, max_drawdown_pct: float = 5.0) -> None:
        self.spec, self.backtest, self.risk, self.strategy = spec, backtest, risk, strategy
        self.min_trades = min_trades_per_period
        self.min_pf = min_profit_factor
        self.max_dd = max_drawdown_pct

    def evaluate(self, ind: pd.DataFrame, model_config) -> Evaluation:
        n = len(ind)
        if n < 1500:
            return Evaluation(None, "insufficient_history", {}, None, pd.DataFrame(index=ind.index))
        if not ind.index.is_monotonic_increasing or ind.index.has_duplicates:
            return Evaluation(None, "invalid_chronology", {}, None, pd.DataFrame(index=ind.index))
        train_end, val_end = int(n * .60), int(n * .80)
        train, validation, test = ind.iloc[:train_end], ind.iloc[train_end:val_end], ind.iloc[val_end:]
        if min(len(train), len(validation), len(test)) < 100:
            return Evaluation(None, "insufficient_temporal_segments", {}, None, pd.DataFrame(index=ind.index))

        model = AIModel(model_config)
        model.train(train)
        probabilities = model.predict_proba(ind)
        signals: dict[str, pd.Series] = {}
        metrics: dict[str, dict] = {}
        val_blocks = [part for part in (validation.iloc[:len(validation)//3],
                                        validation.iloc[len(validation)//3:2*len(validation)//3],
                                        validation.iloc[2*len(validation)//3:]) if len(part)]
        for name in self.CANDIDATES:
            candidate_cfg = StrategyConfig(**vars(self.strategy))
            candidate_cfg.mode = name
            proba = probabilities if name in ("ai", "hybrid") else None
            signal = build_signals(ind, proba, candidate_cfg)
            signals[name] = signal
            blocks = []
            for part in val_blocks:
                res = Backtester(self.spec, self.backtest, self.risk, candidate_cfg).run(
                    part, signal.reindex(part.index)
                )
                blocks.append(res.metrics)
            # Report the held-out test once. It is not read by the selection predicate.
            test_metrics = Backtester(self.spec, self.backtest, self.risk, candidate_cfg).run(
                test, signal.reindex(test.index)
            ).metrics
            metrics[name] = {"validation_periods": blocks, "test_diagnostic_only": test_metrics}
            returns = [float(m.get("total_return_pct", 0)) for m in blocks]
            metrics[name]["validation_stability"] = {
                "positive_periods": sum(value > 0 for value in returns),
                "period_count": len(returns),
                "return_range_pct": max(returns) - min(returns) if returns else None,
                "profitable_periods_required": 3,
            }
        baseline = {"initial_balance": self.backtest.initial_balance,
                    "final_equity": self.backtest.initial_balance, "total_return_pct": 0.0,
                    "max_drawdown_pct": 0.0, "n_trades": 0, "total_costs": 0.0,
                    "profit_factor": 0.0, "expectancy": 0.0}
        metrics["no_trade_reference"] = {
            "validation_periods": [dict(baseline) for _ in val_blocks],
            "test_diagnostic_only": dict(baseline),
            "validation_stability": {"positive_periods": 0, "period_count": len(val_blocks),
                                     "return_range_pct": 0.0, "profitable_periods_required": 3},
        }
        signals["no_trade_reference"] = pd.Series(0, index=ind.index, dtype="int8")

        eligible = []
        for name, row in metrics.items():
            blocks = row["validation_periods"]
            if len(blocks) == 3 and all(
                int(m.get("n_trades", 0)) >= self.min_trades
                and float(m.get("expectancy", 0)) > 0
                and float(m.get("profit_factor", 0)) >= self.min_pf
                and float(m.get("total_return_pct", 0)) > 0
                and abs(float(m.get("max_drawdown_pct", 0))) <= self.max_dd
                for m in blocks
            ):
                eligible.append((name, sum(float(m["expectancy"]) for m in blocks)))
        selected = max(eligible, key=lambda item: item[1])[0] if eligible else None
        reason = "validation_filters_passed" if selected else "no_candidate_passed_all_validation_periods"
        frame = pd.DataFrame(signals)
        frame.index = ind.index
        return Evaluation(selected, reason, metrics, model, frame)
