"""Recheck data structure, chronology and local provenance evidence."""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "market_data.csv"
LOG = ROOT / "logs" / "app.log"
OUT = ROOT / "reports" / "data_validation.json"
LIMA = ZoneInfo("America/Lima")


def main():
    raw_bytes = DATA.read_bytes()
    frame = pd.read_csv(DATA, parse_dates=["time"])
    times = pd.DatetimeIndex(frame["time"]).tz_convert("UTC")
    deltas = times.to_series().diff().dropna()
    minutes = deltas.dt.total_seconds() / 60
    prices = frame[["open", "high", "low", "close"]].apply(pd.to_numeric, errors="coerce")
    volume = pd.to_numeric(frame["volume"], errors="coerce")
    gaps = []
    for j in np.flatnonzero(minutes.to_numpy() > 15):
        k = int(j) + 1
        gaps.append({"from_utc": times[k-1].isoformat(), "to_utc": times[k].isoformat(),
                     "minutes": float(minutes.iloc[j])})

    log_text = LOG.read_text(encoding="utf-8", errors="replace")
    download_lines = re.findall(r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d,\d+).*MT5: 5000 velas de (\S+) M15 descargadas$", log_text, re.M)
    save_lines = re.findall(r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d,\d+).*Datos guardados en .*market_data\.csv \(5000 filas\)$", log_text, re.M)
    file_mtime = datetime.fromtimestamp(DATA.stat().st_mtime, tz=ZoneInfo("UTC"))
    last_download = download_lines[-1] if download_lines else None
    last_save = save_lines[-1] if save_lines else None
    save_match = False
    if last_save:
        local = datetime.strptime(last_save.split(",")[0], "%Y-%m-%d %H:%M:%S").replace(tzinfo=LIMA)
        save_match = abs((file_mtime - local.astimezone(ZoneInfo("UTC"))).total_seconds()) < 120

    finite = np.isfinite(prices.to_numpy()).all()
    consistent = ((prices.high >= prices[["open", "close"]].max(axis=1))
                  & (prices.low <= prices[["open", "close"]].min(axis=1))
                  & (prices.high >= prices.low)).all()
    returns = prices.close.pct_change().dropna()
    report = {
        "path": "data/market_data.csv", "sha256": hashlib.sha256(raw_bytes).hexdigest(),
        "rows": len(frame), "timestamp_timezone": str(frame.time.dt.tz),
        "first_utc": times[0].isoformat(), "last_utc": times[-1].isoformat(),
        "strictly_chronological": bool(times.is_monotonic_increasing),
        "duplicate_timestamps": int(times.duplicated().sum()),
        "delta_counts_minutes": {str(int(k)): int(v) for k, v in minutes.value_counts().sort_index().items()},
        "gaps_over_15_minutes": gaps,
        "ohlc_finite": bool(finite), "ohlc_positive": bool((prices.to_numpy() > 0).all()),
        "ohlc_consistent": bool(consistent), "missing_ohlc": int(prices.isna().sum().sum()),
        "missing_volume": int(volume.isna().sum()), "nonpositive_volume": int((volume <= 0).sum()),
        "close_return_abs_max_pct": float(returns.abs().max() * 100),
        "close_return_abs_p99_pct": float(returns.abs().quantile(.99) * 100),
        "close_return_abs_over_1pct_count": int((returns.abs() > .01).sum()),
        "provenance_evidence": {"last_mt5_download_log": last_download,
                                "last_csv_save_log": last_save,
                                "csv_mtime_utc": file_mtime.isoformat(),
                                "mtime_matches_lima_save_log": save_match,
                                "inferred_final_instrument": last_download[1] if last_download else None,
                                "account_and_server_identity_confirmed": False,
                                "note": "Logs and mtime strongly support MT5 ingestion of EURUSDmicro; original EURUSD download was overwritten. Local logs are not independent authentication."},
    }
    OUT.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, ensure_ascii=False))
    print(f"Saved {OUT}")


if __name__ == "__main__":
    main()
