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
import time
from pathlib import Path

import numpy as np
import pandas as pd

from config import MARKET_DATA_CSV, Settings

log = logging.getLogger(__name__)

OHLCV = ["open", "high", "low", "close", "volume"]
TIMEFRAMES = ("M1", "M5", "M15", "M30", "H1", "H4", "D1")
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
    2) Solo si la cuenta abierta no coincide con MT5_LOGIN, hace login explícito.
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
    if cfg.has_credentials and (info is None or info.login != cfg.login):
        if not mt5.login(cfg.login, password=cfg.password, server=cfg.server, timeout=timeout_ms):
            err = mt5.last_error()
            mt5.shutdown()
            raise RuntimeError(f"No se pudo iniciar sesión en la cuenta {cfg.login} ({cfg.server}): {err}")


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
    missing = [c for c in OHLCV if c not in df.columns]
    if missing:
        raise ValueError(f"Faltan columnas en {path}: {missing}")
    return df[OHLCV].sort_index().dropna()


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
