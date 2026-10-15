"""Chronological train/select/validate/test comparison; test never selects."""
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
    selected_config: StrategyConfig | None = None


class StrategyEvaluator:
    """Fit on train, select on selection, gate on validation, diagnose on test.

    Validation is pass/fail for the single recipe picked on selection; it is not
    used to rank alternatives. Final test metrics are computed only after both
    gates pass, and are never fed back into the decision.
    """

    # Bounded, predeclared family: the agent may synthesize/select a recipe,
    # but does not search an unbounded parameter space or inspect test results.
    CANDIDATE_RECIPES = (
        ("rules_balanced", "rules", 70.0, 30.0, 0.55, 0.45, 1.5, 2.0),
        ("rules_selective", "rules", 65.0, 35.0, 0.55, 0.45, 1.5, 2.0),
        ("rules_wide_stop", "rules", 70.0, 30.0, 0.55, 0.45, 2.0, 2.0),
        ("rules_rr_1_5", "rules", 70.0, 30.0, 0.55, 0.45, 1.5, 1.5),
        ("ai_balanced", "ai", 70.0, 30.0, 0.55, 0.45, 1.5, 2.0),
        ("hybrid_balanced", "hybrid", 70.0, 30.0, 0.55, 0.45, 1.5, 2.0),
        ("hybrid_selective", "hybrid", 65.0, 35.0, 0.58, 0.42, 1.5, 2.0),
    )

    def __init__(self, spec: SymbolSpec, backtest: BacktestConfig, risk: RiskConfig,
                 strategy: StrategyConfig, min_trades_per_period: int = 5,
                 min_profit_factor: float = 1.05, max_drawdown_pct: float = 5.0) -> None:
        self.spec, self.backtest, self.risk, self.strategy = spec, backtest, risk, strategy
        self.min_trades = min_trades_per_period
        self.min_pf = min_profit_factor
        self.max_dd = max_drawdown_pct
        self.source_verified = False

    def evaluate(self, ind: pd.DataFrame, model_config) -> Evaluation:
        n = len(ind)
        if n < 1500:
            return Evaluation(None, "insufficient_history", {}, None, pd.DataFrame(index=ind.index))
        if (not isinstance(ind.index, pd.DatetimeIndex) or ind.index.tz is None
                or not ind.index.is_monotonic_increasing or ind.index.has_duplicates):
            return Evaluation(None, "invalid_chronology", {}, None, pd.DataFrame(index=ind.index))
        train_end, selection_end, validation_end = int(n * .50), int(n * .70), int(n * .85)
        train = ind.iloc[:train_end]
        selection = ind.iloc[train_end:selection_end]
        validation = ind.iloc[selection_end:validation_end]
        test = ind.iloc[validation_end:]
        if min(len(train), len(selection), len(validation), len(test)) < 100:
            return Evaluation(None, "insufficient_temporal_segments", {}, None, pd.DataFrame(index=ind.index))

        model = AIModel(model_config)
        model.train(train)
        probabilities = model.predict_proba(ind)
        signals: dict[str, pd.Series] = {}
        metrics: dict[str, dict] = {}
        candidate_configs: dict[str, StrategyConfig] = {}
        selection_blocks = [part for part in (selection.iloc[:len(selection)//3],
                                              selection.iloc[len(selection)//3:2*len(selection)//3],
                                              selection.iloc[2*len(selection)//3:]) if len(part)]
        validation_blocks = [part for part in (validation.iloc[:len(validation)//3],
                                               validation.iloc[len(validation)//3:2*len(validation)//3],
                                               validation.iloc[2*len(validation)//3:]) if len(part)]
        for name, mode, overbought, oversold, buy, sell, sl_atr, rr in self.CANDIDATE_RECIPES:
            candidate_cfg = StrategyConfig(**vars(self.strategy))
            candidate_cfg.mode = mode
            candidate_cfg.rsi_overbought = overbought
            candidate_cfg.rsi_oversold = oversold
            candidate_cfg.ai_buy_threshold = buy
            candidate_cfg.ai_sell_threshold = sell
            candidate_cfg.sl_atr_mult = sl_atr
            candidate_cfg.rr_ratio = rr
            candidate_configs[name] = candidate_cfg
            proba = probabilities if mode in ("ai", "hybrid") else None
            signal = build_signals(ind, proba, candidate_cfg)
            signals[name] = signal
            blocks = []
            for part in selection_blocks:
                res = Backtester(self.spec, self.backtest, self.risk, candidate_cfg).run(
                    part, signal.reindex(part.index)
                )
                blocks.append(res.metrics)
            metrics[name] = {
                "recipe": {"mode": mode, "rsi_overbought": overbought,
                           "rsi_oversold": oversold, "ai_buy_threshold": buy,
                           "ai_sell_threshold": sell, "sl_atr_mult": sl_atr,
                           "rr_ratio": rr},
                "selection_periods": blocks,
            }
            returns = [float(m.get("total_return_pct", 0)) for m in blocks]
            metrics[name]["selection_stability"] = {
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
            "selection_periods": [dict(baseline) for _ in selection_blocks],
            "validation_periods": [dict(baseline) for _ in validation_blocks],
            "test_diagnostic_only": dict(baseline),
            "selection_stability": {"positive_periods": 0, "period_count": len(selection_blocks),
                                     "return_range_pct": 0.0, "profitable_periods_required": 3},
        }
        signals["no_trade_reference"] = pd.Series(0, index=ind.index, dtype="int8")

        eligible = []
        for name, row in metrics.items():
            blocks = row["selection_periods"]
            if len(blocks) == 3 and all(
                int(m.get("n_trades", 0)) >= self.min_trades
                and float(m.get("expectancy", 0)) > 0
                and float(m.get("profit_factor", 0)) >= self.min_pf
                and float(m.get("total_return_pct", 0)) > 0
                and abs(float(m.get("max_drawdown_pct", 0))) <= self.max_dd
                for m in blocks
            ):
                eligible.append((name, sum(float(m["expectancy"]) for m in blocks)))
        provisional = max(eligible, key=lambda item: item[1])[0] if eligible else None
        selected = None
        validation_passes = False
        if provisional is not None:
            validation_metrics = [Backtester(
                self.spec, self.backtest, self.risk, candidate_configs[provisional]
            ).run(part, signals[provisional].reindex(part.index)).metrics
                for part in validation_blocks]
            metrics[provisional]["validation_periods"] = validation_metrics
            passes_validation = len(validation_metrics) == 3 and all(
                int(m.get("n_trades", 0)) >= self.min_trades
                and float(m.get("expectancy", 0)) > 0
                and float(m.get("profit_factor", 0)) >= self.min_pf
                and float(m.get("total_return_pct", 0)) > 0
                and abs(float(m.get("max_drawdown_pct", 0))) <= self.max_dd
                for m in validation_metrics
            )
            if passes_validation:
                validation_passes = True
                metrics[provisional]["test_diagnostic_only"] = Backtester(
                    self.spec, self.backtest, self.risk, candidate_configs[provisional]
                ).run(test, signals[provisional].reindex(test.index)).metrics
        if provisional and validation_passes and self.source_verified:
            selected = provisional
        if not provisional:
            reason = "no_candidate_passed_selection"
        elif not validation_passes:
            reason = "validation_gate_failed"
        elif not self.source_verified:
            reason = "unverified_data_source"
        else:
            reason = "selection_and_validation_passed"
        metrics["evaluation_protocol"] = {
            "train_fraction": 0.50, "selection_fraction": 0.20,
            "validation_fraction": 0.15, "final_test_fraction": 0.15,
            "source_verified": self.source_verified,
            "provisional_recipe": provisional,
            "test_is_diagnostic_only": True,
        }
        frame = pd.DataFrame(signals)
        frame.index = ind.index
        return Evaluation(selected, reason, metrics, model, frame,
                          candidate_configs.get(selected) if selected else None)
