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
    def position_size(self, equity: float, sl_pips: float, spec: SymbolSpec, price: float,
                      cost_per_lot: float = 0.0) -> float:
        """Lotes para arriesgar `risk_per_trade` del equity si salta el Stop Loss.

        Devuelve 0.0 si el tamaño mínimo del broker ya arriesgaría de más
        (nunca se "redondea hacia arriba" el riesgo).
        """
        if sl_pips <= 0 or equity <= 0 or price <= 0:
            return 0.0
        risk_amount = equity * self.cfg.risk_per_trade
        try:
            pip_value = spec.pip_value_per_lot(price)
        except (ValueError, ZeroDivisionError):
            return 0.0
        if not math.isfinite(cost_per_lot) or cost_per_lot < 0:
            return 0.0
        return self.position_size_for_risk(equity, sl_pips * pip_value + cost_per_lot, spec)

    def position_size_for_risk(self, equity: float, loss_per_lot: float, spec: SymbolSpec) -> float:
        """Dimensiona usando pérdida monetaria por lote calculada por el broker."""
        if (not math.isfinite(equity) or not math.isfinite(loss_per_lot) or equity <= 0
                or loss_per_lot <= 0 or spec.lot_step <= 0 or spec.min_lot <= 0):
            return 0.0
        risk_amount = equity * self.cfg.risk_per_trade
        raw_lots = risk_amount / loss_per_lot
        maximum = min(spec.max_lot, self.cfg.max_lots)
        if raw_lots < spec.min_lot or maximum < spec.min_lot:
            return 0.0
        allowed_steps = math.floor((maximum - spec.min_lot) / spec.lot_step + 1e-9)
        desired_steps = math.floor((raw_lots - spec.min_lot) / spec.lot_step + 1e-9)
        steps = min(allowed_steps, desired_steps)
        precision = max(0, min(8, int(math.ceil(-math.log10(spec.lot_step))) + 2,
                               int(math.ceil(-math.log10(spec.min_lot))) + 2))
        lots = round(spec.min_lot + steps * spec.lot_step, precision)
        return lots if lots >= spec.min_lot else 0.0

    def validate_requested_volume(self, volume: float, spec: SymbolSpec) -> tuple[bool, str]:
        """Validate an explicitly requested volume without rounding it."""
        if not math.isfinite(volume) or volume <= 0:
            return False, "volumen solicitado no es finito y positivo"
        if (not math.isfinite(spec.min_lot) or not math.isfinite(spec.max_lot)
                or not math.isfinite(spec.lot_step) or spec.min_lot <= 0
                or spec.max_lot < spec.min_lot or spec.lot_step <= 0):
            return False, "especificaciones de volumen MT5 incompletas o inválidas"
        if volume < spec.min_lot or volume > spec.max_lot:
            return False, (f"volumen solicitado {volume:.2f} fuera del rango MT5 "
                           f"[{spec.min_lot:.2f}, {spec.max_lot:.2f}]")
        steps = (volume - spec.min_lot) / spec.lot_step
        if abs(steps - round(steps)) > 1e-7:
            return False, (f"volumen solicitado {volume:.2f} no respeta el paso MT5 "
                           f"{spec.lot_step:.2f}")
        if volume > self.cfg.max_lots + 1e-9:
            return False, (f"volumen solicitado {volume:.2f} supera el límite de riesgo "
                           f"max_lots={self.cfg.max_lots:.2f}")
        return True, "ok"
