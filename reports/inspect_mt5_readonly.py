"""Inspect the already-open MT5 terminal without logging in or placing orders.

Uses initialize(path=..., timeout=...) only. It never reads or prints account login,
server, password, or account identifiers, and never calls order_send/login.
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
                    "balance": float(account.balance),
                    "equity": float(account.equity),
                    "free_margin": float(account.margin_free),
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
                        row.update({
                            "visible": bool(info.visible),
                            "trade_mode": int(info.trade_mode),
                            "volume_min": float(info.volume_min),
                            "volume_max": float(info.volume_max),
                            "volume_step": float(info.volume_step),
                            "contract_size": float(info.trade_contract_size),
                            "point": float(info.point),
                            "tick_size": float(info.trade_tick_size),
                            "tick_value_loss": float(info.trade_tick_value_loss),
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
                                "age_seconds": round(age, 1), "fresh_120s": age <= 120,
                            }
                        positions = mt5.positions_get(symbol=name)
                        row["open_positions_count"] = len(positions) if positions is not None else None
                    report["symbols"][name] = row
        finally:
            mt5.shutdown()

    OUT.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, ensure_ascii=False))
    print(f"Saved {OUT}")
    return 0 if report.get("connected") else 2


if __name__ == "__main__":
    raise SystemExit(main())
