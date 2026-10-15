"""
Simulación histórica vela a vela.

Supuestos (deliberadamente conservadores):
  * La señal de la vela t-1 se ejecuta en la APERTURA de la vela t.
  * Spread y slippage: medio coste por ejecución en entrada y salida.
  * Si en una misma vela se tocan SL y TP, se asume que salta primero el SL.
  * Gaps: si la apertura ya supera el SL/TP, se sale al precio de apertura.
  * Una sola posición a la vez; el tamaño sale del RiskManager.
  * Comisión por lote descontada al cerrar.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np
import pandas as pd

from config import BacktestConfig, RiskConfig, StrategyConfig, SymbolSpec
from src.risk_manager import RiskManager

log = logging.getLogger(__name__)


@dataclass
class BacktestResult:
    trades: pd.DataFrame
    equity: pd.Series
    metrics: dict


class Backtester:
    def __init__(
        self,
        spec: SymbolSpec,
        bt_cfg: BacktestConfig | None = None,
        risk_cfg: RiskConfig | None = None,
        strat_cfg: StrategyConfig | None = None,
        symbol: str | None = None,
    ) -> None:
        self.spec = spec
        self.bt = bt_cfg or BacktestConfig()
        self.risk_cfg = risk_cfg or RiskConfig()
        self.strat = strat_cfg or StrategyConfig()
        self.symbol = symbol or spec.symbol

    # ------------------------------------------------------------------ #
    def run(self, df: pd.DataFrame, signals: pd.Series) -> BacktestResult:
        for col in ("open", "high", "low", "close", "atr"):
            if col not in df.columns:
                raise ValueError(f"Falta la columna '{col}' (¿olvidaste add_all_indicators?).")

        if not df.index.is_monotonic_increasing or df.index.has_duplicates:
            raise ValueError("El backtest requiere un índice temporal estrictamente cronológico y sin duplicados.")
        ohlc = df[["open", "high", "low", "close"]].to_numpy(dtype=float)
        if not np.isfinite(ohlc).all() or (ohlc <= 0).any():
            raise ValueError("El backtest contiene precios OHLC ausentes, no finitos o no positivos.")
        if ((df["high"] < df[["open", "close"]].max(axis=1))
                | (df["low"] > df[["open", "close"]].min(axis=1))
                | (df["high"] < df["low"])).any():
            raise ValueError("El backtest contiene velas OHLC inconsistentes.")
        if not signals.dropna().isin([-1, 0, 1]).all():
            raise ValueError("Las señales solo pueden contener -1, 0 o 1.")

        spec, bt, st = self.spec, self.bt, self.strat
        times = df.index
        o, h, l, c = (df[k].to_numpy(float) for k in ("open", "high", "low", "close"))
        atr = df["atr"].to_numpy(float)
        sig = signals.reindex(df.index).fillna(0).to_numpy(int)
        n = len(df)
        if bt.spread_pips < 0 or bt.slippage_pips < 0 or bt.commission_per_lot < 0:
            raise ValueError("Spread, slippage y comisión no pueden ser negativos.")
        half_cost = (bt.spread_pips / 2.0 + bt.slippage_pips) * spec.pip_size

        balance = bt.initial_balance
        risk = RiskManager(self.risk_cfg, balance)
        equity_curve = np.full(n, np.nan)
        drawdown_curve = np.full(n, np.nan)
        trades: list[dict] = []
        pos: dict | None = None
        last_exit_i = -10**9

        def pnl_money(side: int, entry: float, exit_: float, lots: float) -> float:
            pips = side * (exit_ - entry) / spec.pip_size
            return pips * spec.pip_value_per_lot(exit_) * lots

        def mark_equity(mark_price: float) -> float:
            if pos is None:
                return balance
            side = pos["side"]
            close_fill = mark_price - side * half_cost
            return balance + pnl_money(side, pos["entry"], close_fill, pos["lots"]) - bt.commission_per_lot * pos["lots"]

        for i in range(n):
            now = times[i]
            # Los controles de riesgo ven el equity liquidable de la vela anterior,
            # incluyendo spread, slippage y la comisión estimada de cierre.
            equity_at_open = mark_equity(c[i - 1]) if i > 0 else balance
            risk.update(now, equity_at_open)

            # 1) Entrada: señal de la vela anterior, ejecución a la apertura actual
            if pos is None and i >= 1 and sig[i - 1] != 0 and np.isfinite(atr[i - 1]):
                if i - last_exit_i > st.cooldown_bars:
                    ok, why = risk.can_open(now, equity_at_open, open_positions=0)
                    if ok:
                        side = int(sig[i - 1])
                        sl_dist = st.sl_atr_mult * atr[i - 1]
                        pip_value = spec.pip_value_per_lot(o[i])
                        estimated_cost = pip_value * (bt.spread_pips + 2 * bt.slippage_pips) + bt.commission_per_lot
                        lots = risk.position_size(equity_at_open, sl_dist / spec.pip_size,
                                                  spec, o[i], cost_per_lot=estimated_cost)
                        if lots > 0 and sl_dist > 0:
                            pos = {
                                "side": side, "lots": lots, "i": i, "time": now,
                                "mid_entry": o[i], "entry": o[i] + side * half_cost,
                                "sl": o[i] - side * sl_dist,
                                "tp": o[i] + side * st.rr_ratio * sl_dist,
                            }
                            risk.register_open()
                    else:
                        log.debug("%s | entrada bloqueada: %s", now, why)

            # 2) Salida: SL / TP / tiempo máximo
            bar_worst_equity = None
            if pos is not None:
                side, sl, tp = pos["side"], pos["sl"], pos["tp"]
                exit_px, reason = None, None
                is_new = pos["i"] == i
                if side == 1:
                    # El precio de apertura sucede antes del rango intravela.
                    # Si hay gap a través de cualquiera de los niveles, se
                    # ejecuta allí antes de evaluar high/low.
                    if not is_new and o[i] <= sl:
                        exit_px, reason = o[i], "SL"
                    elif not is_new and o[i] >= tp:
                        exit_px, reason = o[i], "TP"
                    elif l[i] <= sl:
                        exit_px, reason = sl, "SL"
                    elif h[i] >= tp:
                        exit_px, reason = tp, "TP"
                else:
                    if not is_new and o[i] >= sl:
                        exit_px, reason = o[i], "SL"
                    elif not is_new and o[i] <= tp:
                        exit_px, reason = o[i], "TP"
                    elif h[i] >= sl:
                        exit_px, reason = sl, "SL"
                    elif l[i] <= tp:
                        exit_px, reason = tp, "TP"

                if exit_px is None and i - pos["i"] >= st.max_bars_in_trade:
                    exit_px, reason = c[i], "TIME"
                if exit_px is None and i == n - 1:
                    exit_px, reason = c[i], "EOD"

                # MAE intravela conservador: cuando no se ejecutó el SL,
                # suponemos que el extremo adverso ocurrió antes del cierre.
                if exit_px is not None and reason == "SL":
                    adverse_exit = exit_px - side * half_cost
                    adverse_pnl = pnl_money(side, pos["entry"], adverse_exit, pos["lots"]) - bt.commission_per_lot * pos["lots"]
                    bar_worst_equity = balance + adverse_pnl
                else:
                    adverse_price = l[i] if side == 1 else h[i]
                    bar_worst_equity = mark_equity(adverse_price)

                if exit_px is not None:
                    fill = exit_px - side * half_cost
                    pnl = pnl_money(side, pos["entry"], fill, pos["lots"]) - bt.commission_per_lot * pos["lots"]
                    gross_pnl = pnl_money(side, pos["mid_entry"], exit_px, pos["lots"])
                    costs = gross_pnl - pnl
                    balance += pnl
                    trades.append({
                        "source": "backtest", "symbol": self.symbol,
                        "entry_time": pos["time"], "exit_time": now,
                        "side": "BUY" if side == 1 else "SELL", "lots": pos["lots"],
                        "entry_price": round(pos["entry"], 5), "exit_price": round(fill, 5),
                        "sl": round(sl, 5), "tp": round(tp, 5),
                        "gross_pnl": round(gross_pnl, 2), "costs": round(costs, 2),
                        "pnl": round(pnl, 2), "exit_reason": reason, "equity_after": round(balance, 2),
                        "bars": i - pos["i"],
                    })
                    pos, last_exit_i = None, i

            # 3) Curva de equity (mark-to-market)
            equity_curve[i] = mark_equity(c[i])
            drawdown_curve[i] = min(equity_curve[i], bar_worst_equity) if pos is not None or bar_worst_equity is not None else equity_curve[i]
            risk.update(now, drawdown_curve[i])
            risk.update(now, equity_curve[i])

        trades_df = pd.DataFrame(trades)
        equity_s = pd.Series(equity_curve, index=times, name="equity")
        drawdown_s = pd.Series(drawdown_curve, index=times, name="intrabar_adverse_equity")
        metrics = compute_metrics(trades_df, equity_s, bt.initial_balance, drawdown_equity=drawdown_s)
        metrics.update({
            "buy_signals": int((sig == 1).sum()),
            "sell_signals": int((sig == -1).sum()),
            "opened_trades": int(len(trades_df)),
            "closed_winning_trades": int((trades_df["pnl"] > 0).sum()) if len(trades_df) else 0,
            "closed_losing_trades": int((trades_df["pnl"] < 0).sum()) if len(trades_df) else 0,
            "closed_breakeven_trades": int((trades_df["pnl"] == 0).sum()) if len(trades_df) else 0,
        })
        if risk.halted:
            metrics["halted_reason"] = risk.halt_reason
        return BacktestResult(trades_df, equity_s, metrics)


# --------------------------------------------------------------------------- #
# Métricas
# --------------------------------------------------------------------------- #
def compute_metrics(trades: pd.DataFrame, equity: pd.Series, initial_balance: float,
                    drawdown_equity: pd.Series | None = None) -> dict:
    final_equity = float(equity.iloc[-1]) if len(equity) else initial_balance
    dd_source = drawdown_equity if drawdown_equity is not None else equity
    peaks = equity.cummax().clip(lower=initial_balance) if len(equity) else equity
    prior_peaks = equity.cummax().shift(1).fillna(initial_balance).clip(lower=initial_balance) if len(equity) else equity
    # Close-to-close drawdown plus conservative intrabar MAE vs prior high-water mark.
    close_dd = equity / peaks - 1.0 if len(equity) else equity
    intrabar_dd = dd_source / prior_peaks - 1.0 if len(equity) else dd_source
    max_dd = min(float(close_dd.min()), float(intrabar_dd.min())) if len(equity) else 0.0

    daily = equity.resample("1D").last().dropna().pct_change().dropna()
    sharpe = float(daily.mean() / daily.std(ddof=1) * np.sqrt(252)) if len(daily) > 2 and daily.std(ddof=1) > 0 else 0.0
    downside = np.minimum(daily.to_numpy(), 0.0)
    downside_deviation = float(np.sqrt(np.mean(np.square(downside)))) if len(downside) else 0.0
    sortino = float(daily.mean() / downside_deviation * np.sqrt(252)) if downside_deviation > 0 else 0.0

    metrics = {
        "initial_balance": round(initial_balance, 2),
        "final_equity": round(final_equity, 2),
        "total_return_pct": round((final_equity / initial_balance - 1) * 100, 2),
        "max_drawdown_pct": round(max_dd * 100, 2),
        "sharpe_ratio": round(sharpe, 2),
        "sortino_ratio": round(sortino, 2),
        "n_trades": int(len(trades)),
        "total_costs": round(float(trades["costs"].sum()), 2) if "costs" in trades.columns and len(trades) else 0.0,
    }
    if trades.empty:
        metrics.update(win_rate_pct=0.0, profit_factor=0.0, avg_win=0.0, avg_loss=0.0,
                       expectancy=0.0, avg_bars_in_trade=0.0, total_costs=0.0)
        return metrics

    wins = trades.loc[trades["pnl"] > 0, "pnl"]
    losses = trades.loc[trades["pnl"] <= 0, "pnl"]
    gross_win, gross_loss = wins.sum(), -losses.sum()
    metrics.update(
        win_rate_pct=round(len(wins) / len(trades) * 100, 2),
        profit_factor=round(float(gross_win / gross_loss), 2) if gross_loss > 0 else float("inf"),
        avg_win=round(float(wins.mean()), 2) if len(wins) else 0.0,
        avg_loss=round(float(losses.mean()), 2) if len(losses) else 0.0,
        expectancy=round(float(trades["pnl"].mean()), 2),
        avg_bars_in_trade=round(float(trades["bars"].mean()), 1),
    )
    return metrics


def format_metrics(metrics: dict) -> str:
    labels = {
        "initial_balance": "Balance inicial", "final_equity": "Equity final",
        "total_return_pct": "Retorno total (%)", "max_drawdown_pct": "Drawdown máx. (%)",
        "sharpe_ratio": "Sharpe (diario)", "n_trades": "Operaciones",
        "sortino_ratio": "Sortino (diario)", "total_costs": "Costes totales",
        "win_rate_pct": "Win rate (%)", "profit_factor": "Profit factor",
        "avg_win": "Ganancia media", "avg_loss": "Pérdida media",
        "expectancy": "Expectativa/operación", "avg_bars_in_trade": "Velas por operación",
        "halted_reason": "Detenido por riesgo",
    }
    return "\n".join(f"  {labels.get(k, k):<24}{v}" for k, v in metrics.items())
