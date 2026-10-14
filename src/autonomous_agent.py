"""Autonomous market analysis/decision loop. Execution is opt-in and defaults off."""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import numpy as np
import pandas as pd

from config import Settings
from src.indicators import add_all_indicators
from src.strategy_evaluator import Evaluation, StrategyEvaluator

log = logging.getLogger(__name__)


def _json_safe(value):
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        return float(value) if np.isfinite(value) else None
    if isinstance(value, (pd.Timestamp,)):
        return value.isoformat()
    return value


@dataclass
class CycleDecision:
    timestamp: str
    symbol: str
    candle_time: str | None
    action: str
    strategy: str | None
    reasons: list[str]
    metrics: dict
    spread_pips: float | None = None
    risk_estimate: dict | None = None
    execution_mode: str = "analysis_only"


class AutonomousAgent:
    def __init__(self, settings: Settings, broker, evaluator: StrategyEvaluator,
                 fetch_data: Callable, log_path: Path, state_path: Path,
                 execute: bool = False) -> None:
        self.settings, self.broker, self.evaluator = settings, broker, evaluator
        self.fetch_data, self.log_path, self.state_path = fetch_data, log_path, state_path
        self.execute = execute
        self.last_candle: str | None = None
        self.state_valid = True
        if state_path.exists():
            try:
                self.last_candle = json.loads(state_path.read_text(encoding="utf-8")).get(
                    settings.market.symbol + "|" + settings.market.timeframe
                )
            except (OSError, ValueError, TypeError):
                self.state_valid = False
                log.critical("Estado previo inválido; se bloquearán decisiones hasta revisar el estado.")

    def _record(self, decision: CycleDecision) -> CycleDecision:
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.log_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(_json_safe(decision.__dict__), ensure_ascii=False, allow_nan=False) + "\n")
        return decision

    def _persist_candle(self, candle: str) -> None:
        self.last_candle = candle
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        state = {}
        if self.state_path.exists():
            try:
                state = json.loads(self.state_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                pass
        state[self.settings.market.symbol + "|" + self.settings.market.timeframe] = candle
        self.state_path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")

    def attach_risk_manager(self, manager) -> None:
        self.risk_manager = manager
        try:
            saved = json.loads(self.state_path.read_text(encoding="utf-8")).get("risk", {})
            manager.peak_equity = max(manager.peak_equity, float(saved.get("peak_equity", 0)))
            manager.day_start_equity = float(saved.get("day_start_equity", manager.day_start_equity))
            manager.current_day = pd.Timestamp(saved["current_day"]).date() if saved.get("current_day") else None
            manager.trades_today = int(saved.get("trades_today", 0))
            manager.halted = bool(saved.get("halted", False))
            manager.halt_reason = str(saved.get("halt_reason", ""))
        except (OSError, ValueError, TypeError, KeyError):
            pass

    def _persist_risk(self) -> None:
        manager = getattr(self, "risk_manager", None)
        if manager is None:
            return
        try:
            state = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            state = {}
        state["risk"] = {
            "peak_equity": manager.peak_equity,
            "day_start_equity": manager.day_start_equity,
            "current_day": str(manager.current_day) if manager.current_day else None,
            "trades_today": manager.trades_today,
            "halted": manager.halted,
            "halt_reason": manager.halt_reason,
        }
        self.state_path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")

    def cycle(self) -> CycleDecision:
        now = pd.Timestamp.now(tz="UTC")
        try:
            if not self.state_valid:
                return self._record(CycleDecision(now.isoformat(), self.settings.market.symbol, None,
                    "NO TRADE", None, ["estado de deduplicación/riesgo ilegible; ejecución bloqueada"], {}))
            self._ensure_connection()
            df = self.fetch_data()
            if df is None or len(df) < 1500 or not df.index.is_monotonic_increasing or df.index.has_duplicates:
                return self._record(CycleDecision(now.isoformat(), self.settings.market.symbol, None,
                    "NO TRADE", None, ["datos insuficientes, desordenados o duplicados"], {}))
            if not isinstance(df.index, pd.DatetimeIndex) or df.index.tz is None:
                return self._record(CycleDecision(now.isoformat(), self.settings.market.symbol, None,
                    "NO TRADE", None, ["índice temporal sin zona horaria"], {}))
            required = ("open", "high", "low", "close", "volume")
            if any(col not in df.columns for col in required):
                return self._record(CycleDecision(now.isoformat(), self.settings.market.symbol, None,
                    "NO TRADE", None, ["faltan columnas OHLCV"], {}))
            prices = df[list(required)].to_numpy(float)
            if (not np.isfinite(prices).all() or (prices[:, :4] <= 0).any()
                    or (prices[:, 4] < 0).any()):
                return self._record(CycleDecision(now.isoformat(), self.settings.market.symbol, None,
                    "NO TRADE", None, ["precios OHLC inválidos"], {}))
            if ((df["high"] < df[["open", "close"]].max(axis=1))
                    | (df["low"] > df[["open", "close"]].min(axis=1))
                    | (df["high"] < df["low"])).any():
                return self._record(CycleDecision(now.isoformat(), self.settings.market.symbol, None,
                    "NO TRADE", None, ["velas OHLC inconsistentes"], {}))
            candle = pd.Timestamp(df.index[-1]).isoformat()
            if candle == self.last_candle:
                return self._record(CycleDecision(now.isoformat(), self.settings.market.symbol, candle,
                    "NO TRADE", None, ["vela ya procesada; posible repetición de ciclo"], {}))
            if pd.Timestamp(candle) > now + pd.Timedelta(minutes=2):
                return self._record(CycleDecision(now.isoformat(), self.settings.market.symbol, candle,
                    "NO TRADE", None, ["timestamp de vela futuro"], {}))
            tf_minutes = {"M5": 5, "M15": 15, "M30": 30, "H1": 60, "H4": 240}.get(
                self.settings.market.timeframe, 15)
            if now - pd.Timestamp(candle) > pd.Timedelta(minutes=2 * tf_minutes):
                self._persist_candle(candle)
                return self._record(CycleDecision(now.isoformat(), self.settings.market.symbol, candle,
                    "NO TRADE", None, ["datos de mercado obsoletos"], {}))

            conditions = self.broker.market_conditions()
            if not conditions.get("ok"):
                self._persist_candle(candle)
                return self._record(CycleDecision(now.isoformat(), self.settings.market.symbol, candle,
                    "NO TRADE", None, [conditions.get("reason", "mercado/cotización no disponible")], {},
                    conditions.get("spread_pips")))
            synced = 0
            sync_closed = getattr(self.broker, "sync_closed_trades", None)
            if callable(sync_closed):
                synced = int(sync_closed())
            spread = float(conditions["spread_pips"])
            if not np.isfinite(spread) or spread > self.settings.market.max_spread_pips:
                self._persist_candle(candle)
                return self._record(CycleDecision(now.isoformat(), self.settings.market.symbol, candle,
                    "NO TRADE", None, ["spread supera el límite configurado"], {},
                    spread))

            if getattr(self, "risk_manager", None):
                self.risk_manager.update(now, float(self.broker.equity()))
                self._persist_risk()

            ind = add_all_indicators(df, self.settings.indicators)
            row = ind.iloc[-1]
            if not np.isfinite(row[["atr", "rsi", "ema_fast", "ema_slow", "ema_trend", "macd_hist"]].to_numpy(float)).all():
                self._persist_candle(candle)
                return self._record(CycleDecision(now.isoformat(), self.settings.market.symbol, candle,
                    "NO TRADE", None, ["indicadores incompletos"], {}, spread))

            evaluation: Evaluation = self.evaluator.evaluate(ind, self.settings.model)
            positions = self.broker.open_positions()
            position_notes = []
            for pos in positions:
                bars_held = max(0, int((pd.Timestamp(candle) - pos.open_time).total_seconds()
                                       // (tf_minutes * 60)))
                if bars_held >= self.settings.strategy.max_bars_in_trade:
                    position_notes.append(f"posición {pos.ticket} excedió máximo temporal ({bars_held} velas)")
                    if self.execute:
                        self.broker.close_position(pos, time=pd.Timestamp(candle), reason="TIME")
            if self.execute and position_notes:
                positions = self.broker.open_positions()
            stops_ok = all(
                p.sl > 0 and p.tp > 0 and
                ((p.side == 1 and p.sl < p.entry < p.tp
                  and p.sl < float(conditions["bid"]) < p.tp)
                 or (p.side == -1 and p.tp < p.entry < p.sl
                     and p.tp < float(conditions["ask"]) < p.sl))
                for p in positions
            )
            signal = int(evaluation.signals[evaluation.selected].iloc[-1]) if evaluation.selected else 0
            reasons = [evaluation.reason]
            if position_notes:
                action, signal = "NO TRADE", 0
                reasons.extend(position_notes)
                reasons.append("sin reentrada en el mismo ciclo de gestión")
            elif positions and not stops_ok:
                action = "NO TRADE"
                reasons.append("posición existente requiere revisión inmediata; nuevas entradas bloqueadas")
            elif evaluation.selected is None:
                action = "NO TRADE"
            elif not stops_ok:
                action, signal = "NO TRADE", 0
                reasons.append("posición abierta sin SL/TP válido; entradas bloqueadas")
            elif positions:
                action, signal = "NO TRADE", 0
                reasons.append("ya existe posición abierta; no duplicar exposición")
                reasons.extend(position_notes)
            else:
                action = {1: "BUY", -1: "SELL"}.get(signal, "NO TRADE")
                if signal == 0:
                    reasons.append("la estrategia seleccionada no tiene señal")
            decision = CycleDecision(now.isoformat(), self.settings.market.symbol, candle,
                action, evaluation.selected, reasons,
                {"candidate_metrics": evaluation.strategies, "closed_trades_synced": synced,
                 "trend": "up" if row.ema_fast > row.ema_slow else "down",
                 "rsi": float(row.rsi), "atr": float(row.atr), "model_probability_up":
                 float(evaluation.model.predict_proba(ind).iloc[-1]) if evaluation.model else None},
                spread)
            decision.metrics["candidate_signals"] = {
                name: int(evaluation.signals[name].iloc[-1])
                for name in evaluation.signals.columns
            }
            decision.execution_mode = "execution_enabled" if self.execute else "analysis_only"
            if action in ("BUY", "SELL"):
                decision.risk_estimate = self._estimate_risk(signal, float(row.atr))
                if not decision.risk_estimate or not decision.risk_estimate.get("within_budget", False):
                    decision.action = "NO TRADE"
                    decision.reasons.append("volumen mínimo/riesgo/margen no verificable dentro del límite")
            if getattr(self, "risk_manager", None):
                allowed, why = self.risk_manager.can_open(
                    pd.Timestamp(candle), float(self.broker.equity()), open_positions=len(positions)
                )
                self._persist_risk()
                if decision.action in ("BUY", "SELL") and not allowed:
                    decision.action = "NO TRADE"
                    decision.reasons.append(why)
            # Persist before any possible order. Restarts cannot repeat this candle.
            self._persist_candle(candle)
            if self.execute and decision.action in ("BUY", "SELL"):
                from app import try_open_trade
                risk = getattr(self, "risk_manager", None)
                if risk is None:
                    log.error("Ejecución solicitada sin RiskManager asociado; no se opera.")
                    decision.metrics["execution_result"] = "blocked_missing_risk_manager"
                else:
                    opened = try_open_trade(self.broker, risk, self.settings, pd.Timestamp(candle),
                                            signal, float(row.atr), float(row.close))
                    decision.metrics["execution_result"] = "opened" if opened else "blocked_or_rejected"
                    risk.update(pd.Timestamp(candle), float(self.broker.equity()))
                    self._persist_risk()
            return self._record(decision)
        except Exception as exc:  # disconnect/data failures are recorded and retried next cycle
            log.exception("Fallo en ciclo autónomo; no se envía orden y se reintentará.")
            return self._record(CycleDecision(now.isoformat(), self.settings.market.symbol, None,
                "NO TRADE", None, [f"error recuperable: {type(exc).__name__}"], {}))

    def _ensure_connection(self) -> None:
        if not hasattr(self.broker, "mt5"):
            return
        mt5 = self.broker.mt5
        if mt5 is None:
            self.broker.connect()
        elif (mt5.terminal_info() is None
              or not getattr(mt5.terminal_info(), "connected", True)):
            self.broker.disconnect()
            self.broker.connect()

    def _estimate_risk(self, side: int, atr_value: float) -> dict | None:
        if not all(callable(getattr(self.broker, name, None))
                   for name in ("entry_quote", "loss_per_lot", "margin_for")):
            return None
        price = self.broker.entry_quote(side)
        if price is None:
            return None
        sl = price - side * self.settings.strategy.sl_atr_mult * atr_value
        deviation = int(getattr(self.broker, "deviation", 0))
        mt5 = getattr(self.broker, "mt5", None)
        symbol_info = mt5.symbol_info(self.settings.market.symbol) if mt5 is not None else None
        point = float(getattr(symbol_info, "point", 0.0)) if symbol_info else 0.0
        worst_entry = price + side * deviation * point
        per_lot = self.broker.loss_per_lot(side, worst_entry, sl)
        if per_lot is None:
            return None
        per_lot += float(self.settings.backtest.commission_per_lot)
        equity = float(self.broker.equity())
        lots = self.risk_manager.position_size_for_risk(equity, per_lot, self.broker.spec)
        budget = equity * self.risk_manager.cfg.risk_per_trade
        minimum_loss = per_lot * self.broker.spec.min_lot
        if lots <= 0:
            return {"within_budget": False, "minimum_volume": self.broker.spec.min_lot,
                    "minimum_loss_estimate": minimum_loss, "risk_budget": budget, "sl": sl}
        margin = self.broker.margin_for(side, lots, price)
        account = self.broker.mt5.account_info() if getattr(self.broker, "mt5", None) else None
        free = float(account.margin_free) if account else None
        valid = margin is not None and free is not None and margin <= free and per_lot * lots <= budget
        return {"within_budget": valid, "volume": lots, "sl": sl, "risk_loss_estimate": per_lot * lots,
                "risk_budget": budget, "margin_required": margin, "free_margin": free,
                "entry_quote": price, "worst_entry_for_deviation": worst_entry,
                "commission_assumption_per_lot": float(self.settings.backtest.commission_per_lot)}

    def run_forever(self, cycle_minutes: int = 15, sleep_fn: Callable = time.sleep,
                    stop: Callable[[], bool] | None = None) -> None:
        if cycle_minutes not in (10, 15):
            raise ValueError("cycle_minutes debe ser 10 o 15.")
        while not (stop and stop()):
            self.cycle()
            sleep_fn(cycle_minutes * 60)
