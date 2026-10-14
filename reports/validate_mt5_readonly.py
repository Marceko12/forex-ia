r"""Read-only MT5 demo validation. This script never calls order_send.

Run with the desktop MT5 terminal already logged into the intended DEMO account:
    .venv\Scripts\python.exe reports\validate_mt5_readonly.py
It initializes by terminal path only, without configured login/password values.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import MetaTrader5 as mt5
import pandas as pd

from config import get_settings
from src.indicators import atr as atr_series
from src.market_data import resolve_mt5_symbol


OUT = ROOT / "reports" / "mt5_readonly_validation.json"


def main() -> int:
    settings = get_settings()
    report = {"connected": False, "orders_sent": 0, "read_only": True, "symbols": {}}
    if not settings.mt5.path:
        report["reason"] = "MT5_PATH is not configured"
    elif not mt5.initialize(path=settings.mt5.path, timeout=15000):
        error = mt5.last_error()
        report["reason"] = "initialize_failed"
        report["error_code"] = error[0] if error else None
        mt5.shutdown()
    else:
        try:
            report["connected"] = True
            account = mt5.account_info()
            if account is None:
                report["reason"] = "account_info_unavailable"
            else:
                mode = account.trade_mode
                mode_name = ("DEMO" if mode == mt5.ACCOUNT_TRADE_MODE_DEMO else
                             "CONTEST" if mode == mt5.ACCOUNT_TRADE_MODE_CONTEST else
                             "REAL" if mode == mt5.ACCOUNT_TRADE_MODE_REAL else "UNKNOWN")
                report.update({"account_mode": mode_name, "account_currency": account.currency,
                               "account_trade_allowed": bool(account.trade_allowed),
                               "approx_7usd_balance": abs(float(account.balance) - 7.0) <= 2.0})
                if mode_name != "DEMO":
                    report["reason"] = "aborted_non_demo_account"
                else:
                    for requested in ("EURCADmicro", "EURUSDmicro"):
                        try:
                            symbol = resolve_mt5_symbol(mt5, requested)
                            if not mt5.symbol_select(symbol, True):
                                raise RuntimeError("symbol_select_failed")
                            info = mt5.symbol_info(symbol)
                            tick = mt5.symbol_info_tick(symbol)
                            if info is None or tick is None:
                                raise RuntimeError("symbol_info_or_tick_missing")
                            pip = 0.01 if "JPY" in symbol.upper()[:6] else 0.0001
                            tick_age = time.time() - float(tick.time)
                            rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M15, 1, 5000)
                            if rates is None or len(rates) < 100:
                                raise RuntimeError("insufficient_closed_M15_bars")
                            bars = pd.DataFrame(rates)
                            atr_df = pd.DataFrame({"high": bars.high, "low": bars.low, "close": bars.close})
                            atr = atr_series(atr_df, 14).dropna()
                            if atr.empty:
                                raise RuntimeError("ATR_unavailable")
                            sl_distance = float(atr.tail(1000).median() * settings.strategy.sl_atr_mult)
                            row = {"resolved_symbol": symbol, "min_lot": float(info.volume_min),
                                   "max_lot": float(info.volume_max), "volume_step": float(info.volume_step),
                                   "contract_size": float(info.trade_contract_size),
                                   "tick_size": float(info.trade_tick_size),
                                   "tick_value_profit": float(info.trade_tick_value_profit),
                                   "tick_value_loss": float(info.trade_tick_value_loss),
                                   "stops_level_points": int(info.trade_stops_level),
                                   "spread_pips_now": float((tick.ask - tick.bid) / pip),
                                   "tick_age_seconds": round(tick_age, 1), "quote_recent": tick_age <= 120,
                                   "m15_closed_bars": int(len(bars)),
                                   "median_sl_distance_price": sl_distance}
                            if account.currency == "USD":
                                calculations = []
                                for side, price in ((mt5.ORDER_TYPE_BUY, float(tick.ask)),
                                                    (mt5.ORDER_TYPE_SELL, float(tick.bid))):
                                    sl = price - sl_distance if side == mt5.ORDER_TYPE_BUY else price + sl_distance
                                    pnl = mt5.order_calc_profit(side, symbol, float(info.volume_min), price, sl)
                                    margin = mt5.order_calc_margin(side, symbol, float(info.volume_min), price)
                                    calculations.append({"side": "BUY" if side == mt5.ORDER_TYPE_BUY else "SELL",
                                                         "min_lot_loss_usd": abs(float(pnl)) if pnl is not None else None,
                                                         "min_lot_margin_usd": float(margin) if margin is not None else None})
                                row["order_calc_profit_margin"] = calculations
                                row["default_risk_budget_usd_at_7"] = 7.0 * settings.risk.risk_per_trade
                                row["live_risk_budget_usd_at_7"] = 7.0 * settings.live_risk.risk_per_trade

                                # Preflight only. Construct a non-submitted request and call order_check;
                                # this file intentionally has no order_send reference.
                                side = mt5.ORDER_TYPE_BUY
                                entry = float(tick.ask)
                                stop_distance = max(sl_distance,
                                                    float(info.trade_stops_level) * float(info.point) + 2 * float(info.point))
                                sl = round(entry - stop_distance, int(info.digits))
                                tp = round(entry + settings.strategy.rr_ratio * stop_distance, int(info.digits))
                                request = {"action": mt5.TRADE_ACTION_DEAL, "symbol": symbol,
                                           "volume": float(info.volume_min), "type": side, "price": entry,
                                           "sl": sl, "tp": tp, "deviation": 20, "magic": 20260101,
                                           "comment": "forex-ai read-only check", "type_time": mt5.ORDER_TIME_GTC,
                                           "type_filling": mt5.ORDER_FILLING_IOC}
                                checked = mt5.order_check(request) if tick_age <= 120 else None
                                row["order_check_retcode"] = getattr(checked, "retcode", None)
                                row["order_check_comment"] = getattr(checked, "comment", None)
                            report["symbols"][requested] = row
                        except Exception as exc:
                            report["symbols"][requested] = {"error": str(exc)[:160]}
        finally:
            mt5.shutdown()

    OUT.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, ensure_ascii=False))
    print(f"Saved {OUT}")
    return 0 if report.get("connected") and report.get("account_mode") == "DEMO" else 2


if __name__ == "__main__":
    raise SystemExit(main())
