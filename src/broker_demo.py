"""
Conectores de broker.

* PaperBroker    -> simulador en memoria (funciona en cualquier sistema).
* MT5Broker      -> MetaTrader 5. Por defecto SOLO opera cuentas demo/concurso:
                    una cuenta REAL se rechaza salvo que se cree con `allow_live=True`
                    (en la app, eso exige `--live` + variable de entorno + confirmación escrita).
* MT5DemoBroker  -> alias de MT5Broker con `allow_live=False` (bloqueo duro).
"""
from __future__ import annotations

import logging
import math
from abc import ABC, abstractmethod
from dataclasses import dataclass, replace

import pandas as pd

from config import MT5Config, SymbolSpec
from src.logger import append_trades
from src.market_data import _import_mt5, connect_mt5, resolve_mt5_symbol

log = logging.getLogger(__name__)

MAGIC_NUMBER = 20260101  # identifica las órdenes de este bot en MT5


@dataclass
class Position:
    ticket: int
    symbol: str
    side: int          # +1 compra, -1 venta
    lots: float
    entry: float
    sl: float
    tp: float
    open_time: pd.Timestamp


class BaseBroker(ABC):
    @abstractmethod
    def connect(self) -> None: ...

    @abstractmethod
    def disconnect(self) -> None: ...

    @abstractmethod
    def equity(self) -> float: ...

    @abstractmethod
    def open_positions(self) -> list[Position]: ...

    @abstractmethod
    def market_order(self, side: int, lots: float, sl: float, tp: float,
                     price: float | None = None, time: pd.Timestamp | None = None) -> Position | None: ...

    @abstractmethod
    def close_position(self, position: Position, price: float | None = None,
                       time: pd.Timestamp | None = None, reason: str = "MANUAL") -> None: ...


# --------------------------------------------------------------------------- #
# Simulador (paper trading)
# --------------------------------------------------------------------------- #
class PaperBroker(BaseBroker):
    def __init__(self, spec: SymbolSpec, initial_balance: float = 10_000.0,
                 spread_pips: float = 1.0, commission_per_lot: float = 7.0,
                 log_trades: bool = True) -> None:
        self.spec = spec
        self.balance = float(initial_balance)
        self.half_spread = spread_pips * spec.pip_size / 2.0
        self.commission = commission_per_lot
        self.log_trades = log_trades
        self.positions: list[Position] = []
        self.closed: list[dict] = []
        self.last_price = 0.0
        self._next_ticket = 1

    def connect(self) -> None:
        log.info("PaperBroker listo | balance=%.2f", self.balance)

    def disconnect(self) -> None:
        pass

    def _pnl(self, pos: Position, exit_price: float) -> float:
        pips = pos.side * (exit_price - pos.entry) / self.spec.pip_size
        return pips * self.spec.pip_value_per_lot(exit_price) * pos.lots

    def equity(self) -> float:
        floating = sum(self._pnl(p, self.last_price) for p in self.positions) if self.last_price else 0.0
        return self.balance + floating

    def open_positions(self) -> list[Position]:
        return list(self.positions)

    def market_order(self, side, lots, sl, tp, price=None, time=None) -> Position | None:
        price = price if price is not None else self.last_price
        if price <= 0 or lots <= 0:
            log.error("Orden rechazada (precio=%s, lotes=%s)", price, lots)
            return None
        pos = Position(self._next_ticket, self.spec.symbol, int(side), lots,
                       entry=price + side * self.half_spread, sl=sl, tp=tp,
                       open_time=time if time is not None else pd.Timestamp.now(tz="UTC"))
        self._next_ticket += 1
        self.positions.append(pos)
        log.info("ABRE #%d %s %.2f lotes @ %.5f | SL %.5f TP %.5f",
                 pos.ticket, "BUY" if side == 1 else "SELL", lots, pos.entry, sl, tp)
        return pos

    def close_position(self, position, price=None, time=None, reason="MANUAL") -> None:
        price = price if price is not None else self.last_price
        fill = price - position.side * self.half_spread
        pnl = self._pnl(position, fill) - self.commission * position.lots
        self.balance += pnl
        self.positions = [p for p in self.positions if p.ticket != position.ticket]
        record = {
            "source": "demo", "symbol": position.symbol,
            "entry_time": position.open_time, "exit_time": time if time is not None else pd.Timestamp.now(tz="UTC"),
            "side": "BUY" if position.side == 1 else "SELL", "lots": position.lots,
            "entry_price": round(position.entry, 5), "exit_price": round(fill, 5),
            "sl": round(position.sl, 5), "tp": round(position.tp, 5),
            "pnl": round(pnl, 2), "exit_reason": reason, "equity_after": round(self.balance, 2),
        }
        self.closed.append(record)
        log.info("CIERRA #%d por %s | PnL %.2f | balance %.2f", position.ticket, reason, pnl, self.balance)
        if self.log_trades:
            append_trades([record])

    def on_bar(self, time: pd.Timestamp, o: float, h: float, l: float, c: float) -> None:
        """Procesa una vela nueva: revisa SL/TP (SL primero si ambos se tocan)."""
        self.last_price = c
        for pos in list(self.positions):
            if pos.side == 1:
                if o <= pos.sl or l <= pos.sl:
                    self.close_position(pos, min(o, pos.sl), time, "SL")
                elif o >= pos.tp or h >= pos.tp:
                    self.close_position(pos, max(o, pos.tp), time, "TP")
            else:
                if o >= pos.sl or h >= pos.sl:
                    self.close_position(pos, max(o, pos.sl), time, "SL")
                elif o <= pos.tp or l <= pos.tp:
                    self.close_position(pos, min(o, pos.tp), time, "TP")


# --------------------------------------------------------------------------- #
# MetaTrader 5
# --------------------------------------------------------------------------- #
class MT5Broker(BaseBroker):
    def __init__(self, cfg: MT5Config, spec: SymbolSpec, allow_live: bool = False, deviation: int = 20) -> None:
        self.cfg = cfg
        self.spec = spec
        self.allow_live = allow_live
        self.deviation = deviation
        self.mt5 = None
        self.is_live = False
        self._logged_deals: set[int] = set()
        self._connected_at = pd.Timestamp.now(tz="UTC")

    @property
    def source(self) -> str:
        return "live" if self.is_live else "demo"

    def connect(self) -> None:
        mt5 = _import_mt5()
        if mt5 is None:
            raise RuntimeError("MetaTrader5 no está instalado (solo Windows).")
        connect_mt5(mt5, self.cfg)

        try:
            self._validate_account(mt5)
        except Exception:
            mt5.shutdown()
            raise
        self.mt5 = mt5

    def _validate_account(self, mt5) -> None:
        info = mt5.account_info()
        if info is None:
            raise RuntimeError("MT5 no devolvió información de la cuenta.")

        self.is_live = info.trade_mode == mt5.ACCOUNT_TRADE_MODE_REAL
        if self.is_live and not self.allow_live:
            raise PermissionError("BLOQUEADO: la cuenta es REAL y el modo real no fue autorizado (usa --live).")
        if not info.trade_allowed:
            raise RuntimeError("La cuenta no tiene permitido operar (trade_allowed=False).")
        term = mt5.terminal_info()
        if term is not None and not term.trade_allowed:
            raise RuntimeError("Activa el botón 'Algo Trading' en MetaTrader 5.")
        if self.is_live and info.currency != "USD":
            raise RuntimeError(
                f"Cuenta REAL en {info.currency}: el cálculo de lotes asume USD. "
                "Usa una cuenta en USD o adapta SymbolSpec.pip_value_per_lot."
            )
        symbol = resolve_mt5_symbol(mt5, self.spec.symbol)
        if not mt5.symbol_select(symbol, True):
            raise RuntimeError(f"Símbolo no disponible: {symbol}")
        if symbol != self.spec.symbol:
            self.spec = replace(self.spec, symbol=symbol)

                # Límites y valor del tick reales del broker
        sym = mt5.symbol_info(self.spec.symbol)
        if sym is None:
            raise RuntimeError(
                f"No se pudieron obtener las especificaciones de {self.spec.symbol}"
            )

        self.spec = replace(
            self.spec,
            min_lot=sym.volume_min,
            max_lot=sym.volume_max,
            lot_step=sym.volume_step,
            contract_size=sym.trade_contract_size,
            tick_size=getattr(sym, "trade_tick_size", 0.0),
            tick_value_profit=getattr(sym, "trade_tick_value_profit", 0.0),
            tick_value_loss=getattr(sym, "trade_tick_value_loss", 0.0),
        )

        log.warning("MT5 conectado | modo=%s balance=%.2f %s",
                    "REAL" if self.is_live else "DEMO", info.balance, info.currency)

    def disconnect(self) -> None:
        if self.mt5:
            self.mt5.shutdown()
            self.mt5 = None

    def equity(self) -> float:
        return float(self.mt5.account_info().equity)

    def open_positions(self) -> list[Position]:
        raw = self.mt5.positions_get(symbol=self.spec.symbol) or []
        return [
            Position(p.ticket, p.symbol, 1 if p.type == self.mt5.POSITION_TYPE_BUY else -1, p.volume,
                     p.price_open, p.sl, p.tp, pd.Timestamp(p.time, unit="s", tz="UTC"))
            for p in raw if p.magic == MAGIC_NUMBER
        ]

    def entry_quote(self, side: int) -> float | None:
        """Precio ejecutable actual (ask para BUY, bid para SELL)."""
        if self.mt5 is None:
            return None
        tick = self.mt5.symbol_info_tick(self.spec.symbol)
        if tick is None:
            return None
        value = float(tick.ask if side == 1 else tick.bid)
        return value if value > 0 else None

    def loss_per_lot(self, side: int, entry: float, sl: float) -> float | None:
        """Pérdida en divisa de cuenta calculada por el motor de MT5."""
        if self.mt5 is None or entry <= 0 or sl <= 0:
            return None
        order_type = self.mt5.ORDER_TYPE_BUY if side == 1 else self.mt5.ORDER_TYPE_SELL
        amount = self.mt5.order_calc_profit(order_type, self.spec.symbol, 1.0, entry, sl)
        if amount is None or not float(amount) < 0:
            return None
        return abs(float(amount))

    def market_order(self, side, lots, sl, tp, price=None, time=None) -> Position | None:
        mt5 = self.mt5
        if mt5 is None or side not in (-1, 1) or not all(map(math.isfinite, [lots, sl, tp])):
            log.error("Orden rechazada: conexión, dirección o valores numéricos inválidos.")
            return None
        if sl <= 0 or tp <= 0:
            log.error("Orden rechazada: toda operación requiere Stop Loss y Take Profit válidos.")
            return None
        if lots < self.spec.min_lot or lots > self.spec.max_lot:
            log.error("Orden rechazada: volumen %.8f fuera de [%.8f, %.8f].", lots,
                      self.spec.min_lot, self.spec.max_lot)
            return None
        steps = (lots - self.spec.min_lot) / self.spec.lot_step
        if abs(steps - round(steps)) > 1e-7:
            log.error("Orden rechazada: volumen %.8f no respeta el paso %.8f.", lots, self.spec.lot_step)
            return None
        tick = mt5.symbol_info_tick(self.spec.symbol)
        sym = mt5.symbol_info(self.spec.symbol)
        if tick is None or sym is None:
            log.error("Sin cotización/especificaciones válidas para %s", self.spec.symbol)
            return None
        if tick.bid <= 0 or tick.ask < tick.bid:
            log.error("Cotización inválida para %s", self.spec.symbol)
            return None
        order_type = mt5.ORDER_TYPE_BUY if side == 1 else mt5.ORDER_TYPE_SELL
        px = tick.ask if side == 1 else tick.bid
        min_distance = float(getattr(sym, "trade_stops_level", 0)) * float(getattr(sym, "point", 0))
        if sl <= 0 or tp <= 0 or (side == 1 and not (sl < px < tp)) or (side == -1 and not (tp < px < sl)):
            log.error("Orden rechazada: SL/TP no corresponden con dirección y cotización actual.")
            return None
        if min_distance and (abs(px - sl) < min_distance or abs(tp - px) < min_distance):
            log.error("Orden rechazada: SL/TP más cerca que stops_level (%.8f).", min_distance)
            return None
        request = {
            "action": mt5.TRADE_ACTION_DEAL, "symbol": self.spec.symbol, "volume": float(lots),
            "type": order_type, "price": px, "sl": float(sl), "tp": float(tp),
            "deviation": self.deviation, "magic": MAGIC_NUMBER, "comment": "forex-ai",
            "type_time": mt5.ORDER_TIME_GTC, "type_filling": mt5.ORDER_FILLING_IOC,
        }
        risk = mt5.order_calc_profit(order_type, self.spec.symbol, float(lots), px, float(sl))
        if risk is None or float(risk) >= 0:
            log.error("Orden rechazada: MT5 no pudo estimar pérdida al SL (%s).", risk)
            return None
        margin = mt5.order_calc_margin(order_type, self.spec.symbol, float(lots), px)
        account = mt5.account_info()
        free_margin = getattr(account, "margin_free", None) if account is not None else None
        if margin is None or free_margin is None or float(margin) < 0 or float(margin) > float(free_margin):
            log.error("Orden rechazada: margen insuficiente o no verificable (estimado=%s, libre=%s).",
                      margin, free_margin)
            return None
        log.info("Preflight %s %s | pérdida estimada al SL=%.2f | margen=%.2f | libre=%.2f",
                 self.spec.symbol, "BUY" if side == 1 else "SELL", abs(float(risk)),
                 float(margin), float(free_margin))
        check = mt5.order_check(request)
        if check is None or getattr(check, "retcode", None) != 0:
            log.error("Orden rechazada por order_check: %s", check)
            return None
        result = mt5.order_send(request)
        if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
            log.error("Orden rechazada: %s", result)
            return None
        pos = Position(result.order, self.spec.symbol, int(side), lots, result.price, sl, tp,
                       pd.Timestamp.now(tz="UTC"))
        log.warning("MT5 [%s] ABRE #%s %s %.2f lotes @ %.5f | SL %.5f TP %.5f", self.source.upper(),
                    result.order, "BUY" if side == 1 else "SELL", lots, result.price, sl, tp)
        append_trades([{
            "source": self.source, "symbol": pos.symbol, "entry_time": pos.open_time, "exit_time": None,
            "side": "BUY" if side == 1 else "SELL", "lots": lots, "entry_price": result.price,
            "sl": sl, "tp": tp, "pnl": None, "exit_reason": "OPEN", "equity_after": self.equity(),
        }])
        return pos

    def close_position(self, position, price=None, time=None, reason="MANUAL") -> None:
        mt5 = self.mt5
        tick = mt5.symbol_info_tick(position.symbol)
        closing_buy = position.side == -1
        request = {
            "action": mt5.TRADE_ACTION_DEAL, "symbol": position.symbol, "volume": float(position.lots),
            "type": mt5.ORDER_TYPE_BUY if closing_buy else mt5.ORDER_TYPE_SELL,
            "position": position.ticket, "price": tick.ask if closing_buy else tick.bid,
            "deviation": self.deviation, "magic": MAGIC_NUMBER, "comment": f"forex-ai {reason}",
            "type_time": mt5.ORDER_TIME_GTC, "type_filling": mt5.ORDER_FILLING_IOC,
        }
        result = mt5.order_send(request)
        if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
            log.error("No se pudo cerrar #%s: %s", position.ticket, result)
        else:
            log.warning("MT5 CIERRA #%s por %s", position.ticket, reason)

    def sync_closed_trades(self) -> int:
        """Registra en trades.csv los cierres (SL/TP/manual) hechos por el servidor.

        Lee el historial de deals de MT5 desde que se conectó el bot. Devuelve cuántos agregó.
        """
        mt5 = self.mt5
        start = (self._connected_at - pd.Timedelta(minutes=1)).to_pydatetime()
        end = (pd.Timestamp.now(tz="UTC") + pd.Timedelta(days=1)).to_pydatetime()
        deals = mt5.history_deals_get(start, end) or []
        records = []
        for d in deals:
            if d.magic != MAGIC_NUMBER or d.entry != mt5.DEAL_ENTRY_OUT or d.ticket in self._logged_deals:
                continue
            self._logged_deals.add(d.ticket)
            reason = "TP" if d.reason == mt5.DEAL_REASON_TP else "SL" if d.reason == mt5.DEAL_REASON_SL else "CLOSE"
            records.append({
                "source": self.source, "symbol": d.symbol,
                "entry_time": None, "exit_time": pd.Timestamp(d.time, unit="s", tz="UTC"),
                # el deal de salida de una compra es de tipo SELL, y viceversa
                "side": "BUY" if d.type == mt5.DEAL_TYPE_SELL else "SELL",
                "lots": d.volume, "entry_price": None, "exit_price": d.price,
                "sl": None, "tp": None, "pnl": round(d.profit + d.commission + d.swap, 2),
                "exit_reason": reason, "equity_after": self.equity(),
            })
        if records:
            append_trades(records)
        return len(records)


class MT5DemoBroker(MT5Broker):
    """Atajo con bloqueo duro: nunca opera una cuenta real."""

    def __init__(self, cfg: MT5Config, spec: SymbolSpec, deviation: int = 20) -> None:
        super().__init__(cfg, spec, allow_live=False, deviation=deviation)
