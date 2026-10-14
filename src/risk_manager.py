"""
Gestión de riesgo: tamaño de posición y límites de pérdida.

Es la parte más importante del sistema: ninguna operación se abre sin pasar
por `can_open()`, y el tamaño siempre sale de `position_size()`.
"""
from __future__ import annotations

import logging
import math

import pandas as pd

from config import RiskConfig, SymbolSpec

log = logging.getLogger(__name__)


class RiskManager:
    def __init__(self, cfg: RiskConfig, initial_equity: float) -> None:
        if initial_equity <= 0:
            raise ValueError("El equity inicial debe ser positivo.")
        self.cfg = cfg
        self.peak_equity = float(initial_equity)
        self.day_start_equity = float(initial_equity)
        self.current_day = None
        self.trades_today = 0
        self.halted = False          # kill-switch permanente (drawdown máximo)
        self.halt_reason = ""

    # ------------------------------------------------------------------ #
    # Estado
    # ------------------------------------------------------------------ #
    def update(self, now: pd.Timestamp, equity: float) -> None:
        """Llamar en cada vela: reinicia contadores diarios y vigila el drawdown."""
        day = now.date()
        if day != self.current_day:
            self.current_day = day
            self.day_start_equity = float(equity)
            self.trades_today = 0

        self.peak_equity = max(self.peak_equity, float(equity))
        drawdown = 1.0 - equity / self.peak_equity
        if not self.halted and drawdown >= self.cfg.max_drawdown:
            self.halted = True
            self.halt_reason = f"Drawdown máximo alcanzado ({drawdown:.1%} >= {self.cfg.max_drawdown:.1%})"
            log.critical("KILL-SWITCH: %s", self.halt_reason)

    def daily_loss(self, equity: float) -> float:
        """Pérdida del día como fracción (positiva = pérdida)."""
        return (self.day_start_equity - equity) / self.day_start_equity

    # ------------------------------------------------------------------ #
    # Permisos
    # ------------------------------------------------------------------ #
    def can_open(self, now: pd.Timestamp, equity: float, open_positions: int = 0) -> tuple[bool, str]:
        self.update(now, equity)
        if self.halted:
            return False, self.halt_reason
        if open_positions >= self.cfg.max_open_positions:
            return False, "Máximo de posiciones abiertas"
        if self.trades_today >= self.cfg.max_trades_per_day:
            return False, "Máximo de operaciones por día"
        if self.daily_loss(equity) >= self.cfg.max_daily_loss:
            return False, f"Límite de pérdida diaria ({self.cfg.max_daily_loss:.1%}) alcanzado"
        return True, "ok"

    def register_open(self) -> None:
        self.trades_today += 1

    # ------------------------------------------------------------------ #
    # Tamaño de posición
    # ------------------------------------------------------------------ #
    def position_size(self, equity: float, sl_pips: float, spec: SymbolSpec, price: float) -> float:
        """Lotes para arriesgar `risk_per_trade` del equity si salta el Stop Loss.

        Devuelve 0.0 si el tamaño mínimo del broker ya arriesgaría de más
        (nunca se "redondea hacia arriba" el riesgo).
        """
        if sl_pips <= 0 or equity <= 0 or price <= 0:
            return 0.0
        risk_amount = equity * self.cfg.risk_per_trade
        pip_value = spec.pip_value_per_lot(price)
        raw_lots = risk_amount / (sl_pips * pip_value)

        lots = math.floor(raw_lots / spec.lot_step + 1e-9) * spec.lot_step
        lots = min(lots, spec.max_lot, self.cfg.max_lots)
        lots = round(lots, 2)
        return lots if lots >= spec.min_lot else 0.0
