"""
Simulación histórica vela a vela.

Supuestos (deliberadamente conservadores):
  * La señal de la vela t-1 se ejecuta en la APERTURA de la vela t.
  * Spread: medio spread en la entrada y medio en la salida.
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

        spec, bt, st = self.spec, self.bt, self.strat
        times = df.index
        o, h, l, c = (df[k].to_numpy(float) for k in ("open", "high", "low", "close"))
        atr = df["atr"].to_numpy(float)
        sig = signals.reindex(df.index).fillna(0).to_numpy(int)
        n = len(df)
        half_spread = bt.spread_pips * spec.pip_size / 2.0

        equity = bt.initial_balance
        risk = RiskManager(self.risk_cfg, equity)
        equity_curve = np.full(n, np.nan)
        trades: list[dict] = []
        pos: dict | None = None
        last_exit_i = -10**9

        def pnl_money(side: int, entry: float, exit_: float, lots: float) -> float:
            pips = side * (exit_ - entry) / spec.pip_size
            return pips * spec.pip_value_per_lot(exit_) * lots

        for i in range(n):
            now = times[i]
            risk.update(now, equity)

            # 1) Entrada: señal de la vela anterior, ejecución a la apertura actual
            if pos is None and i >= 1 and sig[i - 1] != 0 and np.isfinite(atr[i - 1]):
                if i - last_exit_i > st.cooldown_bars:
                    ok, why = risk.can_open(now, equity, open_positions=0)
                    if ok:
                        side = int(sig[i - 1])
                        sl_dist = st.sl_atr_mult * atr[i - 1]
                        lots = risk.position_size(equity, sl_dist / spec.pip_size, spec, o[i])
                        if lots > 0 and sl_dist > 0:
                            pos = {
                                "side": side, "lots": lots, "i": i, "time": now,
                                "entry": o[i] + side * half_spread,
                                "sl": o[i] - side * sl_dist,
                                "tp": o[i] + side * st.rr_ratio * sl_dist,
                            }
                            risk.register_open()
                    else:
                        log.debug("%s | entrada bloqueada: %s", now, why)

            # 2) Salida: SL / TP / tiempo máximo
            if pos is not None:
                side, sl, tp = pos["side"], pos["sl"], pos["tp"]
                exit_px, reason = None, None
                is_new = pos["i"] == i
                if side == 1:
                    if not is_new and o[i] <= sl:
                        exit_px, reason = o[i], "SL"
                    elif l[i] <= sl:
                        exit_px, reason = sl, "SL"
                    elif not is_new and o[i] >= tp:
                        exit_px, reason = o[i], "TP"
                    elif h[i] >= tp:
                        exit_px, reason = tp, "TP"
                else:
                    if not is_new and o[i] >= sl:
                        exit_px, reason = o[i], "SL"
                    elif h[i] >= sl:
                        exit_px, reason = sl, "SL"
                    elif not is_new and o[i] <= tp:
                        exit_px, reason = o[i], "TP"
                    elif l[i] <= tp:
                        exit_px, reason = tp, "TP"

                if exit_px is None and i - pos["i"] >= st.max_bars_in_trade:
                    exit_px, reason = c[i], "TIME"
                if exit_px is None and i == n - 1:
                    exit_px, reason = c[i], "EOD"

                if exit_px is not None:
                    fill = exit_px - side * half_spread
                    pnl = pnl_money(side, pos["entry"], fill, pos["lots"]) - bt.commission_per_lot * pos["lots"]
                    equity += pnl
                    trades.append({
                        "source": "backtest", "symbol": self.symbol,
                        "entry_time": pos["time"], "exit_time": now,
                        "side": "BUY" if side == 1 else "SELL", "lots": pos["lots"],
                        "entry_price": round(pos["entry"], 5), "exit_price": round(fill, 5),
                        "sl": round(sl, 5), "tp": round(tp, 5),
                        "pnl": round(pnl, 2), "exit_reason": reason, "equity_after": round(equity, 2),
                        "bars": i - pos["i"],
                    })
                    pos, last_exit_i = None, i
                    risk.update(now, equity)

            # 3) Curva de equity (mark-to-market)
            floating = 0.0
            if pos is not None:
                floating = pnl_money(pos["side"], pos["entry"], c[i], pos["lots"])
            equity_curve[i] = equity + floating

        trades_df = pd.DataFrame(trades)
        equity_s = pd.Series(equity_curve, index=times, name="equity")
        metrics = compute_metrics(trades_df, equity_s, bt.initial_balance)
        if risk.halted:
            metrics["halted_reason"] = risk.halt_reason
        return BacktestResult(trades_df, equity_s, metrics)


# --------------------------------------------------------------------------- #
# Métricas
# --------------------------------------------------------------------------- #
def compute_metrics(trades: pd.DataFrame, equity: pd.Series, initial_balance: float) -> dict:
    final_equity = float(equity.iloc[-1]) if len(equity) else initial_balance
    max_dd = float((equity / equity.cummax() - 1.0).min()) if len(equity) else 0.0

    daily = equity.resample("1D").last().dropna().pct_change().dropna()
    sharpe = float(daily.mean() / daily.std(ddof=1) * np.sqrt(252)) if len(daily) > 2 and daily.std(ddof=1) > 0 else 0.0

    metrics = {
        "initial_balance": round(initial_balance, 2),
        "final_equity": round(final_equity, 2),
        "total_return_pct": round((final_equity / initial_balance - 1) * 100, 2),
        "max_drawdown_pct": round(max_dd * 100, 2),
        "sharpe_ratio": round(sharpe, 2),
        "n_trades": int(len(trades)),
    }
    if trades.empty:
        metrics.update(win_rate_pct=0.0, profit_factor=0.0, avg_win=0.0, avg_loss=0.0,
                       expectancy=0.0, avg_bars_in_trade=0.0)
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
        "win_rate_pct": "Win rate (%)", "profit_factor": "Profit factor",
        "avg_win": "Ganancia media", "avg_loss": "Pérdida media",
        "expectancy": "Expectativa/operación", "avg_bars_in_trade": "Velas por operación",
        "halted_reason": "Detenido por riesgo",
    }
    return "\n".join(f"  {labels.get(k, k):<24}{v}" for k, v in metrics.items())
