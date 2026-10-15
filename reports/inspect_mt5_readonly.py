"""Inspect the already-open MT5 terminal without logging in or placing orders.

Uses initialize(path=..., timeout=...) only. It never reads or prints account login,
server, password, or account identifiers, and never calls order_send/login.
"""
from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import MetaTrader5 as mt5

from config import get_settings

OUT = ROOT / "reports" / "mt5_account_inspection.json"
REQUESTED = ("EURUSDmicro", "EURUSD", "EURCADmicro", "EURCAD")


def main() -> int:
    settings = get_settings()
    report: dict = {"connected": False, "read_only": True, "orders_sent": 0,
                    "login_attempted": False, "symbols": {}}
    if not settings.mt5.path:
        report["reason"] = "MT5_PATH is not configured; no initialize/login attempted"
    elif not mt5.initialize(path=settings.mt5.path, timeout=15000):
        err = mt5.last_error()
        report.update(reason="initialize_failed", error_code=err[0] if err else None)
        mt5.shutdown()
    else:
        try:
            report["connected"] = True
            account, terminal = mt5.account_info(), mt5.terminal_info()
            if account is None:
                report["reason"] = "account_info_unavailable"
            else:
                mode = account.trade_mode
                mode_name = {mt5.ACCOUNT_TRADE_MODE_DEMO: "DEMO",
                             mt5.ACCOUNT_TRADE_MODE_CONTEST: "CONTEST",
                             mt5.ACCOUNT_TRADE_MODE_REAL: "REAL"}.get(mode, "UNKNOWN")
                report["account"] = {
                    "mode": mode_name,
                    "currency": str(account.currency),
                    "trade_allowed": bool(account.trade_allowed),
                }
                report["terminal"] = {
                    "connected": bool(getattr(terminal, "connected", False)) if terminal else False,
                    "trade_allowed": bool(getattr(terminal, "trade_allowed", False)) if terminal else False,
                }
                all_names = {str(s.name) for s in (mt5.symbols_get() or [])}
                report["symbols_available"] = sorted(
                    name for name in all_names
                    if name.upper().startswith(("EURUSD", "EURCAD"))
                )
                for name in REQUESTED:
                    info = mt5.symbol_info(name)
                    row: dict = {"exact_name_available": info is not None}
                    if info is None:
                        row["similar_names"] = sorted(
                            n for n in all_names if n.upper().startswith(name[:6].upper())
                        )
                    else:
                        tick = mt5.symbol_info_tick(name)
                        trade_mode_names = {
                            getattr(mt5, "SYMBOL_TRADE_MODE_DISABLED", -100): "DISABLED",
                            getattr(mt5, "SYMBOL_TRADE_MODE_LONGONLY", -101): "LONGONLY",
                            getattr(mt5, "SYMBOL_TRADE_MODE_SHORTONLY", -102): "SHORTONLY",
                            getattr(mt5, "SYMBOL_TRADE_MODE_CLOSEONLY", -103): "CLOSEONLY",
                            getattr(mt5, "SYMBOL_TRADE_MODE_FULL", -104): "FULL",
                        }
                        row.update({
                            "visible": bool(info.visible),
                            "trade_mode": int(info.trade_mode),
                            "trade_mode_name": trade_mode_names.get(int(info.trade_mode), "UNKNOWN"),
                            "volume_min": float(info.volume_min),
                            "volume_max": float(info.volume_max),
                            "volume_step": float(info.volume_step),
                            "contract_size": float(info.trade_contract_size),
                            "point": float(info.point),
                            "digits": int(info.digits),
                            "spread_points_metadata": int(info.spread),
                            "tick_size": float(info.trade_tick_size),
                            "tick_value": float(info.trade_tick_value),
                            "tick_value_profit": float(info.trade_tick_value_profit),
                            "tick_value_loss": float(info.trade_tick_value_loss),
                            "currency_profit": str(info.currency_profit),
                            "currency_margin": str(info.currency_margin),
                            "stops_level_points": int(info.trade_stops_level),
                            "freeze_level_points": int(getattr(info, "trade_freeze_level", 0)),
                        })
                        if tick is None or tick.bid <= 0 or tick.ask < tick.bid:
                            row["quote"] = "missing_or_invalid"
                        else:
                            age = max(0.0, time.time() - float(tick.time))
                            row["quote"] = {
                                "bid": float(tick.bid), "ask": float(tick.ask),
                                "spread_points": float((tick.ask - tick.bid) / info.point),
                                "spread_price": float(tick.ask - tick.bid),
                                "age_seconds": round(age, 1), "fresh_120s": age <= 120,
                            }
                        positions = mt5.positions_get(symbol=name)
                        row["open_positions_count"] = len(positions) if positions is not None else None
                        # MT5 symbol_info has no commission schedule. Report only anonymized
                        # aggregates from actual deals in the prior year, where available.
                        end_utc = datetime.now(timezone.utc)
                        start_utc = end_utc - timedelta(days=365)
                        deals = mt5.history_deals_get(start_utc, end_utc)
                        if deals is None:
                            row["closed_deal_cost_history"] = {
                                "available": False,
                                "reason": "history_deals_get_unavailable",
                            }
                        else:
                            symbol_deals = [deal for deal in deals
                                            if getattr(deal, "symbol", "") == name
                                            and getattr(deal, "type", None) in (
                                                getattr(mt5, "DEAL_TYPE_BUY", 0),
                                                getattr(mt5, "DEAL_TYPE_SELL", 1))]
                            entry_in = getattr(mt5, "DEAL_ENTRY_IN", 0)
                            entry_out = getattr(mt5, "DEAL_ENTRY_OUT", 1)
                            entry_inout = getattr(mt5, "DEAL_ENTRY_INOUT", 2)
                            entry_out_by = getattr(mt5, "DEAL_ENTRY_OUT_BY", 3)
                            closed_position_ids = {
                                getattr(d, "position_id", None) for d in symbol_deals
                                if getattr(d, "entry", None) in (entry_out, entry_inout, entry_out_by)
                            }
                            closed_position_ids.discard(None)
                            closed_deals = [d for d in symbol_deals
                                            if getattr(d, "position_id", None) in closed_position_ids]
                            closed_entry_commission = sum(float(getattr(d, "commission", 0.0))
                                                          for d in closed_deals
                                                          if getattr(d, "entry", None) == entry_in)
                            closed_exit_commission = sum(float(getattr(d, "commission", 0.0))
                                                         for d in closed_deals
                                                         if getattr(d, "entry", None) in
                                                         (entry_out, entry_inout, entry_out_by))
                            closed_volumes = [float(getattr(d, "volume", 0.0)) for d in closed_deals
                                              if getattr(d, "entry", None) == entry_in]
                            total_closed_commission = closed_entry_commission + closed_exit_commission
                            row["closed_deal_cost_history"] = {
                                "available": True,
                                "period_days": 365,
                                "currency": str(account.currency),
                                "deal_count": len(closed_deals),
                                "closed_position_ids_count": len(closed_position_ids),
                                "closed_position_entry_volume": round(sum(closed_volumes), 8),
                                "entry_commission_total": round(closed_entry_commission, 8),
                                "exit_commission_total": round(closed_exit_commission, 8),
                                "commission_total": round(total_closed_commission, 8),
                                "commission_abs_per_closed_entry_lot": (
                                    round(abs(total_closed_commission) / sum(closed_volumes), 8)
                                    if sum(closed_volumes) > 0 else None
                                ),
                                "swap_total": round(sum(float(getattr(d, "swap", 0.0))
                                                        for d in closed_deals), 8),
                                "realized_profit_total": round(sum(float(getattr(d, "profit", 0.0))
                                                                    for d in closed_deals), 8),
                                "note": "Historical realized deals only; may not establish current fee schedule.",
                            }
                    report["symbols"][name] = row
        finally:
            mt5.shutdown()

    OUT.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, ensure_ascii=False))
    print(f"Saved {OUT}")
    return 0 if report.get("connected") else 2


if __name__ == "__main__":
    raise SystemExit(main())
