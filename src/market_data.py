"""
Obtención de datos de mercado.

Fuentes:
  * MetaTrader 5 (solo Windows) -> `fetch_mt5`
  * CSV local                   -> `load_csv` / `save_csv`
  * Sintético                   -> `generate_synthetic` (para probar sin MT5)

Todas devuelven un DataFrame con índice temporal UTC llamado `time` y columnas:
open, high, low, close, volume.
"""
from __future__ import annotations

import logging
import hashlib
import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from config import MARKET_DATA_CSV, Settings

log = logging.getLogger(__name__)

OHLCV = ["open", "high", "low", "close", "volume"]
TIMEFRAMES = ("M1", "M5", "M15", "M30", "H1", "H4", "D1")
AUTONOMOUS_TIMEFRAMES = ("M15", "M30", "H1", "H4")
_TIMEFRAME_SECONDS = {"M15": 900, "M30": 1800, "H1": 3600, "H4": 14400}
_PANDAS_FREQ = {"M1": "1min", "M5": "5min", "M15": "15min", "M30": "30min",
                "H1": "1h", "H4": "4h", "D1": "1D"}


# --------------------------------------------------------------------------- #
# MetaTrader 5
# --------------------------------------------------------------------------- #
def _import_mt5():
    try:
        import MetaTrader5 as mt5  # noqa: WPS433 (import tardío a propósito)
        return mt5
    except ImportError:
        return None


def mt5_available() -> bool:
    return _import_mt5() is not None


def resolve_mt5_symbol(mt5, requested: str) -> str:
    """Resuelve nombre exacto o un único símbolo con sufijo publicado por el broker."""
    requested = requested.strip()
    if not requested:
        raise ValueError("El nombre de símbolo está vacío.")
    if mt5.symbol_info(requested) is not None:
        return requested
    names = [getattr(item, "name", "") for item in (mt5.symbols_get() or [])]
    matches = [name for name in names if name and name.upper().startswith(requested.upper())]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        raise ValueError(f"Símbolo ambiguo para {requested}: {', '.join(matches)}")
    err = mt5.last_error() if hasattr(mt5, "last_error") else ""
    raise ValueError(f"El broker no publica el símbolo {requested} (MT5: {err}).")


def connect_mt5(mt5, cfg, timeout_ms: int = 60_000, retries: int = 3) -> None:
    """Conecta con el terminal MT5 de forma robusta.

    1) Se conecta SIN credenciales: reutiliza la sesión que ya tienes abierta en MT5
       (evita el error 'IPC timeout' que aparece al autenticar durante initialize).
    2) Si hay credenciales configuradas, solo verifica que la sesión ya abierta
       coincide; nunca ejecuta login ni cambia la cuenta automáticamente.
    Reintenta si el terminal aún está arrancando. Nunca escribe la contraseña en mensajes.
    """
    kwargs = {"timeout": timeout_ms}
    if cfg.path:
        kwargs["path"] = cfg.path

    last = None
    for attempt in range(1, retries + 1):
        if mt5.initialize(**kwargs):
            break
        last = mt5.last_error()
        log.warning("MT5 initialize intento %d/%d falló: %s", attempt, retries, last)
        mt5.shutdown()
        if attempt < retries:
            time.sleep(3)
    else:
        raise RuntimeError(f"No se pudo inicializar MT5: {last}")

    info = mt5.account_info()
    if info is None:
        mt5.shutdown()
        raise RuntimeError("MT5 no tiene una sesión activa verificable; inicia la cuenta deseada manualmente.")
    if cfg.has_credentials and (info.login != cfg.login or (cfg.server and info.server != cfg.server)):
        mt5.shutdown()
        raise RuntimeError(
            "La sesión abierta en MT5 no coincide con la cuenta configurada. "
            "Se conservó la sesión actual; no se intentó cambiar de cuenta."
        )


def fetch_mt5(settings: Settings, n_bars: int | None = None, start_pos: int = 1) -> pd.DataFrame:
    """Descarga velas desde MT5.

    `start_pos=1` salta la vela en formación, así solo se usan velas CERRADAS
    (evita operar con información incompleta).
    """
    mt5 = _import_mt5()
    if mt5 is None:
        raise RuntimeError("MetaTrader5 no está instalado (solo Windows). Usa --source csv o synthetic.")

    # Si otro componente (p. ej. el broker) ya inicializó MT5, no lo reiniciamos
    # ni lo cerramos al terminar.
    already_open = mt5.terminal_info() is not None
    if not already_open:
        connect_mt5(mt5, settings.mt5)

    try:
        symbol, tf_name = settings.market.symbol, settings.market.timeframe
        if tf_name not in TIMEFRAMES:
            raise ValueError(f"Timeframe inválido: {tf_name}. Opciones: {TIMEFRAMES}")
        symbol = resolve_mt5_symbol(mt5, symbol)
        if not mt5.symbol_select(symbol, True):
            raise RuntimeError(f"Símbolo no disponible en el broker: {symbol}")

        tf = getattr(mt5, f"TIMEFRAME_{tf_name}")
        count = n_bars or settings.market.n_bars
        rates = mt5.copy_rates_from_pos(symbol, tf, start_pos, count)
        if rates is None or len(rates) == 0:
            raise RuntimeError(f"MT5 no devolvió datos para {symbol} {tf_name}: {mt5.last_error()}")
    finally:
        if not already_open:
            mt5.shutdown()

    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)
    df = df.rename(columns={"tick_volume": "volume"}).set_index("time")
    df = df[OHLCV].sort_index()
    log.info("MT5: %d velas de %s %s descargadas", len(df), symbol, tf_name)
    return df


def parse_utc_datetime(value: str | datetime | pd.Timestamp) -> datetime:
    """Parse an ISO timestamp with an explicit timezone and normalize to UTC."""
    result = pd.Timestamp(value)
    if result.tzinfo is None:
        raise ValueError("La fecha debe incluir zona horaria, por ejemplo 2026-01-01T00:00:00Z.")
    return result.tz_convert("UTC").to_pydatetime()


def validate_mt5_rates(rates, start: datetime, end: datetime) -> pd.DataFrame:
    """Preserve MT5 columns, add evaluator's `volume`, and validate raw bars."""
    if rates is None or len(rates) == 0:
        raise ValueError("MT5 no devolvió velas para el intervalo solicitado.")
    frame = pd.DataFrame(rates)
    required = {"time", "open", "high", "low", "close", "tick_volume", "spread", "real_volume"}
    missing = sorted(required.difference(frame.columns))
    if missing:
        raise ValueError(f"La respuesta MT5 carece de columnas: {missing}")
    frame["time"] = pd.to_datetime(frame["time"], unit="s", utc=True)
    frame = frame.sort_values("time", kind="stable").reset_index(drop=True)
    if frame["time"].duplicated().any():
        raise ValueError("El bloque MT5 contiene timestamps duplicados.")
    start_ts, end_ts = pd.Timestamp(start), pd.Timestamp(end)
    if ((frame["time"] < start_ts) | (frame["time"] > end_ts)).any():
        raise ValueError("MT5 devolvió barras fuera del rango UTC solicitado.")
    numeric = ["open", "high", "low", "close", "tick_volume", "spread", "real_volume"]
    frame[numeric] = frame[numeric].apply(pd.to_numeric, errors="coerce")
    if not np.isfinite(frame[numeric].to_numpy(float)).all():
        raise ValueError("La descarga MT5 contiene precios/volúmenes/spread no finitos.")
    price = frame[["open", "high", "low", "close"]]
    if ((price <= 0).any().any() or (frame[["tick_volume", "real_volume", "spread"]] < 0).any().any()
            or (frame["high"] < price[["open", "close"]].max(axis=1)).any()
            or (frame["low"] > price[["open", "close"]].min(axis=1)).any()
            or (frame["high"] < frame["low"]).any()):
        raise ValueError("La descarga MT5 contiene precios o velas inválidas.")
    frame["volume"] = frame["tick_volume"]
    return frame


def download_mt5_history(mt5, symbol: str, timeframe: str, date_from, date_to,
                         chunk_days: int = 30) -> tuple[pd.DataFrame, dict]:
    """Download a verifiable range from an already-connected terminal.

    Requires an exact symbol name; it deliberately does not substitute a suffix.
    Gaps and empty chunks are reported, not silently declared complete.
    """
    if timeframe not in AUTONOMOUS_TIMEFRAMES:
        raise ValueError(f"Timeframe histórico no admitido: {timeframe}; usa {AUTONOMOUS_TIMEFRAMES}.")
    symbol_info = mt5.symbol_info(symbol) if symbol else None
    if not symbol or symbol_info is None:
        raise ValueError(f"Símbolo exacto no disponible en MT5: {symbol}")
    start, end = parse_utc_datetime(date_from), parse_utc_datetime(date_to)
    if end <= start:
        raise ValueError("date_to debe ser posterior a date_from.")
    if chunk_days < 1 or chunk_days > 365:
        raise ValueError("chunk_days debe estar entre 1 y 365.")
    if not mt5.symbol_select(symbol, True):
        raise RuntimeError(f"No se pudo habilitar consulta del símbolo exacto {symbol}.")
    tf_const = getattr(mt5, f"TIMEFRAME_{timeframe}", None)
    if tf_const is None:
        raise RuntimeError(f"La versión de MT5 no expone TIMEFRAME_{timeframe}.")

    blocks: list[pd.DataFrame] = []
    chunks: list[dict] = []
    cursor = start
    while cursor <= end:
        chunk_end = min(cursor + timedelta(days=chunk_days), end)
        rates = mt5.copy_rates_range(symbol, tf_const, cursor, chunk_end)
        count = 0 if rates is None else len(rates)
        chunks.append({"from_utc": cursor.isoformat(), "to_utc": chunk_end.isoformat(), "rows": count})
        if rates is not None and count:
            blocks.append(validate_mt5_rates(rates, cursor, chunk_end))
        if chunk_end >= end:
            break
        cursor = chunk_end

    if not blocks:
        raise RuntimeError(f"MT5 no devolvió datos en el rango para {symbol} {timeframe}: {mt5.last_error()}")
    combined = pd.concat(blocks, ignore_index=True).sort_values("time", kind="stable")
    duplicates = combined["time"].duplicated(keep=False)
    boundary_duplicates = 0
    if duplicates.any():
        for _, group in combined.loc[duplicates].groupby("time", sort=False):
            if len(group.drop_duplicates()) != 1:
                raise ValueError("La descarga por fragmentos produjo timestamps conflictivos.")
        boundary_duplicates = int(combined["time"].duplicated().sum())
        combined = combined.drop_duplicates("time", keep="first")
    combined = combined.sort_values("time", kind="stable").reset_index(drop=True)
    gaps = []
    max_seconds = _TIMEFRAME_SECONDS[timeframe]
    deltas = combined["time"].diff().dt.total_seconds()
    for pos in np.flatnonzero(deltas.to_numpy() > max_seconds * 1.5):
        gaps.append({"from_utc": combined.loc[pos - 1, "time"].isoformat(),
                     "to_utc": combined.loc[pos, "time"].isoformat(),
                     "minutes": float(deltas.iloc[pos] / 60)})
    returned = combined["time"]
    terminal = mt5.terminal_info() if callable(getattr(mt5, "terminal_info", None)) else None
    max_bars = getattr(terminal, "maxbars", None) if terminal is not None else None
    manifest = {
        "source": "MetaTrader5.copy_rates_range", "symbol": symbol, "timeframe": timeframe,
        "requested_from_utc": start.isoformat(), "requested_to_utc": end.isoformat(),
        "first_bar_utc": returned.iloc[0].isoformat(), "last_bar_utc": returned.iloc[-1].isoformat(),
        "rows": int(len(combined)), "empty_chunks": sum(c["rows"] == 0 for c in chunks),
        "terminal_maxbars": max_bars,
        "rows_at_or_above_terminal_maxbars": bool(max_bars and len(combined) >= int(max_bars)),
        "symbol_specifications": {
            key: getattr(symbol_info, key, None) for key in (
                "trade_mode", "volume_min", "volume_max", "volume_step", "trade_tick_size",
                "trade_tick_value", "trade_tick_value_profit", "trade_tick_value_loss", "point",
                "digits", "trade_stops_level", "currency_profit", "currency_margin",
                "trade_contract_size", "spread",
            )
        },
        "identical_chunk_boundary_duplicates_removed": boundary_duplicates,
        "gaps_over_1_5_bars": gaps, "chunks": chunks,
        "complete": False,
        "completeness_note": "MT5 does not certify full historical coverage; compare requested endpoints, gaps, terminal Max bars and broker history before treating as complete.",
        "validation": "UTC, required raw MT5 columns, strict timestamps, OHLC and finite/nonnegative fields checked",
    }
    return combined, manifest


def save_mt5_history(frame: pd.DataFrame, manifest: dict, output: Path) -> tuple[Path, Path]:
    """Create raw MT5 CSV and a manifest without overwriting either file."""
    output = Path(output)
    sidecar = output.with_suffix(output.suffix + ".manifest.json")
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists() or sidecar.exists():
        raise FileExistsError(f"El destino o su manifest ya existe; se conserva: {output}")
    raw = frame.copy()
    raw["time"] = pd.to_datetime(raw["time"], utc=True).map(lambda value: value.isoformat())
    csv_bytes = raw.to_csv(index=False).encode("utf-8")
    enriched = dict(manifest)
    enriched["sha256"] = hashlib.sha256(csv_bytes).hexdigest()
    enriched["csv_file"] = output.name
    with output.open("xb") as stream:
        stream.write(csv_bytes)
    try:
        with sidecar.open("x", encoding="utf-8") as stream:
            json.dump(enriched, stream, indent=2, ensure_ascii=False)
            stream.write("\n")
    except Exception:
        # Keep the CSV as evidence; never overwrite or silently remove it.
        raise
    return output, sidecar


# --------------------------------------------------------------------------- #
# CSV
# --------------------------------------------------------------------------- #
def save_csv(df: pd.DataFrame, path: Path = MARKET_DATA_CSV) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index_label="time")
    log.info("Datos guardados en %s (%d filas)", path, len(df))
    return path


def load_csv(path: Path = MARKET_DATA_CSV) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"No existe {path}. Ejecuta primero: python app.py download")
    df = pd.read_csv(path, parse_dates=["time"], index_col="time")
    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC")
    else:
        df.index = df.index.tz_convert("UTC")
    missing = [c for c in OHLCV if c not in df.columns]
    if missing:
        raise ValueError(f"Faltan columnas en {path}: {missing}")
    if not df.index.is_monotonic_increasing or df.index.has_duplicates:
        raise ValueError(f"El CSV debe estar ordenado cronológicamente y sin timestamps duplicados: {path}")
    df = df[OHLCV].apply(pd.to_numeric, errors="coerce")
    if not np.isfinite(df.to_numpy(dtype=float)).all():
        raise ValueError(f"El CSV contiene datos OHLCV ausentes o no numéricos: {path}")
    prices = df[["open", "high", "low", "close"]]
    if (prices <= 0).any().any() or (df["volume"] < 0).any():
        raise ValueError(f"El CSV contiene precios no positivos o volumen negativo: {path}")
    if ((df["high"] < prices[["open", "close"]].max(axis=1))
            | (df["low"] > prices[["open", "close"]].min(axis=1))
            | (df["high"] < df["low"])).any():
        raise ValueError(f"El CSV contiene velas OHLC inconsistentes: {path}")
    return df


# --------------------------------------------------------------------------- #
# Datos sintéticos (solo para pruebas / desarrollo)
# --------------------------------------------------------------------------- #
def generate_synthetic(
    n_bars: int = 20_000,
    timeframe: str = "H1",
    start_price: float = 1.10,
    seed: int = 42,
) -> pd.DataFrame:
    """Serie OHLCV artificial con regímenes de tendencia y volatilidad variable.

    NO representa al mercado real: sirve para probar el pipeline sin MT5.
    """
    rng = np.random.default_rng(seed)
    freq = _PANDAS_FREQ.get(timeframe, "1h")

    # Más fechas de las necesarias para poder quitar los fines de semana.
    idx = pd.date_range("2022-01-03", periods=int(n_bars * 1.5), freq=freq, tz="UTC")
    idx = idx[idx.dayofweek < 5][:n_bars]
    n = len(idx)

    # Regímenes de tendencia (cambian cada ~150-400 velas)
    drift = np.zeros(n)
    i = 0
    while i < n:
        length = int(rng.integers(150, 400))
        drift[i:i + length] = rng.normal(0, 1.2e-4)
        i += length

    # Volatilidad con "clusters" (AR(1) sobre log-vol)
    log_vol = np.zeros(n)
    for t in range(1, n):
        log_vol[t] = 0.98 * log_vol[t - 1] + rng.normal(0, 0.08)
    vol = 4e-4 * np.exp(log_vol)

    returns = drift + vol * rng.standard_normal(n)
    close = start_price * np.exp(np.cumsum(returns))
    open_ = np.concatenate([[start_price], close[:-1]])
    wick = vol * np.abs(rng.standard_normal(n)) * 0.6
    high = np.maximum(open_, close) + wick
    low = np.minimum(open_, close) - wick
    volume = rng.integers(100, 5_000, n).astype(float)

    df = pd.DataFrame({"open": open_, "high": high, "low": low, "close": close, "volume": volume},
                      index=pd.DatetimeIndex(idx, name="time"))
    return df.round(5)


# --------------------------------------------------------------------------- #
# Punto de entrada único
# --------------------------------------------------------------------------- #
def get_market_data(settings: Settings, source: str = "auto") -> pd.DataFrame:
    """source: auto | mt5 | csv | synthetic.

    auto -> MT5 si está disponible; si falla, CSV; si no hay CSV, sintético.
    """
    source = source.lower()
    if source == "mt5":
        return fetch_mt5(settings)
    if source == "csv":
        return load_csv()
    if source == "synthetic":
        return generate_synthetic(settings.market.n_bars, settings.market.timeframe)

    # auto
    if mt5_available():
        try:
            return fetch_mt5(settings)
        except Exception as exc:  # noqa: BLE001
            log.warning("MT5 falló (%s). Probando CSV...", exc)
    try:
        return load_csv()
    except FileNotFoundError:
        log.warning("Sin MT5 ni CSV: usando datos SINTÉTICOS (solo para pruebas).")
        return generate_synthetic(settings.market.n_bars, settings.market.timeframe)
