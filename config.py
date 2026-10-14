"""
Configuración central del proyecto.

Seguridad:
  * Las credenciales NUNCA van en el código: se leen de variables de entorno
    o de un archivo `.env` (que está en .gitignore).
  * La contraseña se oculta en los `repr` / logs.
  * El conector MT5 se niega a operar si la cuenta no es DEMO.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
LOGS_DIR = BASE_DIR / "logs"
MODELS_DIR = BASE_DIR / "models"

MARKET_DATA_CSV = DATA_DIR / "market_data.csv"
TRADES_CSV = LOGS_DIR / "trades.csv"
APP_LOG = LOGS_DIR / "app.log"
MODEL_PATH = MODELS_DIR / "forex_model.joblib"


# --------------------------------------------------------------------------- #
# Lectura segura de variables de entorno
# --------------------------------------------------------------------------- #
def _load_dotenv(path: Path = BASE_DIR / ".env") -> None:
    """Mini-lector de .env (sin dependencias). No pisa variables ya definidas."""
    if not path.exists():
        return
    # utf-8-sig: tolera el BOM que a veces agrega el Bloc de notas de Windows
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _env_str(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _env_int(name: str, default: int = 0) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


# --------------------------------------------------------------------------- #
# Especificaciones del símbolo
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class SymbolSpec:
    symbol: str
    pip_size: float
    contract_size: float = 100_000
    min_lot: float = 0.01
    max_lot: float = 100.0
    lot_step: float = 0.01

    tick_size: float = 0.0
    tick_value_profit: float = 0.0
    tick_value_loss: float = 0.0

    def pip_value_per_lot(self, price: float) -> float:
        """Valor conservador de un pip por lote, en moneda de la cuenta."""
        if self.tick_size > 0:
            values = [
                self.tick_value_profit,
                self.tick_value_loss,
            ]
            tick_value = max(values)
            if tick_value > 0:
                return tick_value * self.pip_size / self.tick_size

        # Respaldo solo cuando la divisa de cotización ya es USD o USD es
        # la divisa base. Los cruces (p. ej. EURCAD) requieren conversión
        # contemporánea o los cálculos de riesgo quedan mal denominados.
        base = self.symbol.upper()[:3]
        quote = self.symbol.upper()[3:6]
        raw = self.contract_size * self.pip_size

        if quote == "USD":
            return raw
        if base == "USD" and price > 0:
            return raw / price
        raise ValueError(
            f"No se puede estimar pip value en USD para {self.symbol} sin "
            "especificaciones MT5 o tipo de cambio de conversión."
        )


def get_symbol_spec(symbol: str) -> SymbolSpec:
    # NO se fuerza a mayúsculas: el nombre debe coincidir EXACTO con el del broker (p. ej. "EURUSDm").
    symbol = symbol.strip()
    pip = 0.01 if "JPY" in symbol.upper()[:6] else 0.0001
    return SymbolSpec(symbol=symbol, pip_size=pip)


# --------------------------------------------------------------------------- #
# Bloques de configuración
# --------------------------------------------------------------------------- #
@dataclass
class MT5Config:
    login: int = 0
    password: str = field(default="", repr=False)  # oculto en logs
    server: str = ""
    path: str = ""  # ruta a terminal64.exe (opcional)

    @property
    def has_credentials(self) -> bool:
        return bool(self.login and self.password and self.server)


@dataclass
class MarketConfig:
    symbol: str = "EURUSD"
    timeframe: str = "H1"  # M1 M5 M15 M30 H1 H4 D1
    n_bars: int = 20_000
    max_spread_pips: float = 2.0  # hard filter; confirm/adjust only from broker evidence


@dataclass
class IndicatorConfig:
    ema_fast: int = 20
    ema_slow: int = 50
    ema_trend: int = 200
    rsi_period: int = 14
    atr_period: int = 14
    bb_period: int = 20
    bb_std: float = 2.0
    macd_fast: int = 12
    macd_slow: int = 26
    macd_signal: int = 9


@dataclass
class StrategyConfig:
    mode: str = "hybrid"  # rules | ai | hybrid
    rsi_overbought: float = 70.0
    rsi_oversold: float = 30.0
    ai_buy_threshold: float = 0.55
    ai_sell_threshold: float = 0.45
    sl_atr_mult: float = 1.5  # Stop Loss = 1.5 x ATR
    rr_ratio: float = 2.0  # Take Profit = 2 x distancia del SL
    max_bars_in_trade: int = 48
    cooldown_bars: int = 2


@dataclass
class ModelConfig:
    horizon: int = 6  # velas hacia adelante para etiquetar
    label_atr_mult: float = 0.5  # movimiento mínimo (en ATR) para etiquetar
    val_fraction: float = 0.2
    n_estimators: int = 300
    max_depth: int = 6
    min_samples_leaf: int = 50
    random_state: int = 42


@dataclass
class RiskConfig:
    risk_per_trade: float = 0.01  # 1% del equity por operación
    max_daily_loss: float = 0.03  # 3% -> se detiene el día
    max_drawdown: float = 0.10  # 10% desde el pico -> se detiene todo
    max_open_positions: int = 1
    max_trades_per_day: int = 5
    max_lots: float = 5.0


@dataclass
class BacktestConfig:
    initial_balance: float = 10_000.0
    spread_pips: float = 1.0
    commission_per_lot: float = 7.0  # USD por lote (ida y vuelta)
    slippage_pips: float = 0.2  # estimación por ejecución; validar con datos del broker
    train_fraction: float = 0.7


@dataclass
class Settings:
    mt5: MT5Config
    market: MarketConfig = field(default_factory=MarketConfig)
    indicators: IndicatorConfig = field(default_factory=IndicatorConfig)
    strategy: StrategyConfig = field(default_factory=StrategyConfig)
    model: ModelConfig = field(default_factory=ModelConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)
    # Límites mucho más estrictos que se usan automáticamente con --live (dinero real).
    live_risk: RiskConfig = field(default_factory=lambda: RiskConfig(
        risk_per_trade=0.0025,   # 0.25 % por operación
        max_daily_loss=0.01,     # 1 % diario
        max_drawdown=0.05,       # 5 % desde el pico -> el bot se detiene
        max_open_positions=1,
        max_trades_per_day=3,
        max_lots=0.5,
    ))
    backtest: BacktestConfig = field(default_factory=BacktestConfig)

    @property
    def spec(self) -> SymbolSpec:
        return get_symbol_spec(self.market.symbol)


LIVE_ENV_VAR = "ALLOW_LIVE_TRADING"
LIVE_ENV_VALUE = "YES_I_ACCEPT_THE_RISK"
LIVE_CONFIRM_PHRASE = "OPERAR EN REAL"


def get_settings() -> Settings:
    _load_dotenv()
    mt5 = MT5Config(
        login=_env_int("MT5_LOGIN"),
        password=_env_str("MT5_PASSWORD"),
        server=_env_str("MT5_SERVER"),
        path=_env_str("MT5_PATH"),
    )
    settings = Settings(mt5=mt5)
    symbol, timeframe = _env_str("MARKET_SYMBOL"), _env_str("MARKET_TIMEFRAME")
    if symbol:
        settings.market.symbol = symbol
    if timeframe:
        settings.market.timeframe = timeframe.upper()
    return settings
