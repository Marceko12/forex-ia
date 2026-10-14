"""Logging de la app (logs/app.log) y registro de operaciones (logs/trades.csv)."""
from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Iterable, Mapping

import pandas as pd

from config import APP_LOG, LOGS_DIR, TRADES_CSV

TRADE_COLUMNS = [
    "source",       # backtest | demo
    "symbol",
    "entry_time",
    "exit_time",
    "side",         # BUY | SELL
    "lots",
    "entry_price",
    "exit_price",
    "sl",
    "tp",
    "pnl",
    "exit_reason",  # SL | TP | TIME | EOD | OPEN
    "equity_after",
]


def setup_logging(level: str = "INFO", log_file: Path = APP_LOG) -> logging.Logger:
    """Configura el logger raíz una sola vez (consola + archivo rotativo)."""
    root = logging.getLogger()
    if getattr(root, "_forex_ai_ready", False):
        root.setLevel(level.upper())
        return logging.getLogger("forex_ai")

    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s | %(levelname)-8s | %(name)s | %(message)s")

    file_handler = RotatingFileHandler(log_file, maxBytes=2_000_000, backupCount=3, encoding="utf-8")
    file_handler.setFormatter(fmt)
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(fmt)

    root.setLevel(level.upper())
    root.addHandler(file_handler)
    root.addHandler(console)
    root._forex_ai_ready = True  # type: ignore[attr-defined]
    return logging.getLogger("forex_ai")


def _normalize(records: Iterable[Mapping] | pd.DataFrame) -> pd.DataFrame:
    df = records.copy() if isinstance(records, pd.DataFrame) else pd.DataFrame(list(records))
    for col in TRADE_COLUMNS:
        if col not in df.columns:
            df[col] = None
    return df[TRADE_COLUMNS]


def append_trades(records, path: Path = TRADES_CSV) -> None:
    """Agrega operaciones al CSV (crea el archivo con cabecera si no existe)."""
    df = _normalize(records)
    if df.empty:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    write_header = (not path.exists()) or path.stat().st_size == 0
    df.to_csv(path, mode="a", header=write_header, index=False)


def replace_trades(records, source: str, path: Path = TRADES_CSV) -> None:
    """Reemplaza solo las filas de `source`, conservando las de otras fuentes."""
    new = _normalize(records)
    new["source"] = source
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.stat().st_size > 0:
        old = pd.read_csv(path)
        old = old[old["source"] != source]
        new = pd.concat([old, new], ignore_index=True)
    new[TRADE_COLUMNS].to_csv(path, index=False)
