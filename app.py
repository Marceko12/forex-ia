"""
forex-ai: punto de entrada.

Comandos:
    python app.py download  --source synthetic|mt5      # guarda data/market_data.csv
    python app.py train                                 # entrena y guarda el modelo
    python app.py backtest  --mode rules|ai|hybrid      # simulación fuera de muestra
    python app.py demo      --broker paper|mt5          # trading en DEMO
    python app.py demo      --broker mt5 --live         # cuenta REAL (doble confirmación)

Nada de esto es asesoría financiera ni garantiza ganancias.
"""
from __future__ import annotations

import argparse
import logging
import sys
import time

import pandas as pd

import os

from config import (LIVE_CONFIRM_PHRASE, LIVE_ENV_VALUE, LIVE_ENV_VAR, MARKET_DATA_CSV, MODEL_PATH,
                    Settings, get_settings)
from src.ai_model import AIModel
from src.backtester import Backtester, format_metrics
from src.broker_demo import BaseBroker, MT5Broker, PaperBroker
from src.indicators import add_all_indicators
from src.logger import replace_trades, setup_logging
from src.market_data import fetch_mt5, get_market_data, load_csv, save_csv
from src.risk_manager import RiskManager
from src.strategy import build_signals, side_name

log = logging.getLogger("forex_ai")


# --------------------------------------------------------------------------- #
# Utilidades
# --------------------------------------------------------------------------- #
def apply_cli_overrides(settings: Settings, args: argparse.Namespace) -> Settings:
    if getattr(args, "symbol", None):
        settings.market.symbol = args.symbol.strip()
    if getattr(args, "timeframe", None):
        settings.market.timeframe = args.timeframe.upper()
    if getattr(args, "bars", None):
        settings.market.n_bars = args.bars
    if getattr(args, "mode", None):
        settings.strategy.mode = args.mode
    return settings


def split_train_test(ind: pd.DataFrame, train_fraction: float) -> tuple[pd.DataFrame, pd.DataFrame, int]:
    split = int(len(ind) * train_fraction)
    return ind.iloc[:split], ind.iloc[split:], split


def load_data(settings: Settings, source: str) -> pd.DataFrame:
    df = get_market_data(settings, source) if source != "csv" else load_csv(MARKET_DATA_CSV)
    if len(df) < 1_000:
        raise ValueError(f"Pocos datos ({len(df)} velas). Se recomiendan >= 5.000.")
    return df


def build_strategy_signals(settings: Settings, ind: pd.DataFrame, model: AIModel | None) -> pd.Series:
    proba = model.predict_proba(ind) if model is not None else None
    return build_signals(ind, proba, settings.strategy)


# --------------------------------------------------------------------------- #
# Comandos
# --------------------------------------------------------------------------- #
def cmd_download(args, settings: Settings) -> None:
    df = get_market_data(settings, args.source)
    save_csv(df, MARKET_DATA_CSV)
    print(f"OK: {len(df)} velas | {df.index[0]} -> {df.index[-1]}")


def cmd_train(args, settings: Settings) -> None:
    df = load_data(settings, args.source)
    ind = add_all_indicators(df, settings.indicators)
    data = ind if args.all_data else split_train_test(ind, settings.backtest.train_fraction)[0]

    model = AIModel(settings.model)
    metrics = model.train(data)
    model.save(MODEL_PATH)

    print("\n=== Entrenamiento ===")
    for k, v in metrics.items():
        print(f"  {k:<16}{v}")
    print("\nAUC ~ 0.50 significa que el modelo NO supera al azar. Desconfía de AUC > 0.65 en forex.")


def cmd_backtest(args, settings: Settings) -> None:
    df = load_data(settings, args.source)
    ind = add_all_indicators(df, settings.indicators)
    train_df, test_df, _ = split_train_test(ind, settings.backtest.train_fraction)

    model = None
    if settings.strategy.mode in ("ai", "hybrid"):
        model = AIModel(settings.model)
        model.train(train_df)  # SOLO con datos de entrenamiento
        print(f"Modelo (val): AUC={model.metrics['val_auc']:.3f}  acc={model.metrics['val_accuracy']:.3f}")

    # Se predice sobre todo `ind` (las features usan solo pasado) y se recorta al tramo de test.
    signals = build_strategy_signals(settings, ind, model).loc[test_df.index]

    bt = Backtester(settings.spec, settings.backtest, settings.risk, settings.strategy, settings.market.symbol)
    result = bt.run(test_df, signals)

    print(f"\n=== Backtest {settings.market.symbol} {settings.market.timeframe} | modo={settings.strategy.mode} ===")
    print(f"Periodo fuera de muestra: {test_df.index[0]} -> {test_df.index[-1]}  ({len(test_df)} velas)")
    print(format_metrics(result.metrics))

    if not result.trades.empty:
        replace_trades(result.trades, source="backtest")
        print("\nOperaciones guardadas en logs/trades.csv")


# --------------------------------------------------------------------------- #
# Compuerta de seguridad para dinero real
# --------------------------------------------------------------------------- #
def confirm_live_trading(input_fn=input, environ=None) -> None:
    """Exige DOS confirmaciones independientes antes de operar con dinero real.

    1) variable de entorno ALLOW_LIVE_TRADING=YES_I_ACCEPT_THE_RISK
    2) escribir a mano la frase "OPERAR EN REAL"
    Lanza PermissionError si falla cualquiera.
    """
    environ = os.environ if environ is None else environ
    if environ.get(LIVE_ENV_VAR, "") != LIVE_ENV_VALUE:
        raise PermissionError(
            f"Modo real bloqueado. Define {LIVE_ENV_VAR}={LIVE_ENV_VALUE} en tu .env para habilitarlo."
        )
    print("\n" + "!" * 64)
    print("  VAS A OPERAR CON DINERO REAL. Puedes perder todo el capital depositado.")
    print("  Este bot no ha sido probado con datos reales ni garantiza ganancias.")
    print("!" * 64)
    if input_fn(f'Escribe exactamente "{LIVE_CONFIRM_PHRASE}" para continuar: ').strip() != LIVE_CONFIRM_PHRASE:
        raise PermissionError("Confirmación incorrecta. No se operará.")


# --------------------------------------------------------------------------- #
# Demo / real (paper o MT5)
# --------------------------------------------------------------------------- #
def try_open_trade(broker: BaseBroker, risk: RiskManager, settings: Settings, now: pd.Timestamp,
                   signal: int, atr_value: float, price: float) -> bool:
    """Aplica límites de riesgo, calcula tamaño y envía la orden. True si se abrió."""
    open_pos = broker.open_positions()
    ok, why = risk.can_open(now, broker.equity(), open_positions=len(open_pos))
    if not ok:
        # Con una posición ya abierta es lo normal: no ensuciar el log en INFO.
        level = logging.DEBUG if open_pos else logging.INFO
        log.log(level, "Señal %s ignorada: %s", side_name(signal), why)
        return False

    spec = getattr(broker, "spec", settings.spec)  # MT5Broker trae los límites reales del broker
    sl_dist = settings.strategy.sl_atr_mult * atr_value
    if not (sl_dist > 0):
        return False
    quote_fn = getattr(broker, "entry_quote", None)
    if callable(quote_fn):
        quote = quote_fn(signal)
        if quote is None:
            log.warning("Entrada rechazada: no hay cotización ejecutable actual para %s.", spec.symbol)
            return False
        price = quote
    sl = price - signal * sl_dist
    tp = price + signal * settings.strategy.rr_ratio * sl_dist
    loss_fn = getattr(broker, "loss_per_lot", None)
    if callable(loss_fn):
        loss_per_lot = loss_fn(signal, price, sl)
        if loss_per_lot is None:
            log.warning("Entrada rechazada: MT5 no pudo calcular el riesgo monetario al SL.")
            return False
        # Coste round-turn configurado como estimación; debe verificarse contra XM.
        estimated_commission = float(settings.backtest.commission_per_lot)
        if estimated_commission < 0:
            log.warning("Entrada rechazada: comisión estimada inválida.")
            return False
        loss_per_lot += estimated_commission
        lots = risk.position_size_for_risk(broker.equity(), loss_per_lot, spec)
    else:
        try:
            lots = risk.position_size(broker.equity(), sl_dist / spec.pip_size, spec, price)
        except (ValueError, ZeroDivisionError):
            lots = 0.0
    if lots <= 0:
        log.warning("Tamaño 0: el riesgo mínimo del broker excede el límite configurado.")
        return False

    pos = broker.market_order(signal, lots, sl, tp, price=price, time=now)
    if pos is not None:
        risk.register_open()
        return True
    return False


def _expire_old_positions(broker: BaseBroker, held: dict, max_bars: int, now: pd.Timestamp, price: float) -> None:
    for pos in broker.open_positions():
        held[pos.ticket] = held.get(pos.ticket, 0) + 1
        if held[pos.ticket] >= max_bars:
            broker.close_position(pos, price=price, time=now, reason="TIME")
            held.pop(pos.ticket, None)


def run_paper_replay(settings: Settings, ind: pd.DataFrame, signals: pd.Series, bars: int) -> None:
    broker = PaperBroker(settings.spec, settings.backtest.initial_balance,
                         settings.backtest.spread_pips, settings.backtest.commission_per_lot)
    broker.connect()
    risk = RiskManager(settings.risk, broker.equity())
    held: dict[int, int] = {}
    window = ind.iloc[-bars:]

    for ts, row in window.iterrows():
        broker.on_bar(ts, row.open, row.high, row.low, row.close)
        _expire_old_positions(broker, held, settings.strategy.max_bars_in_trade, ts, row.close)
        sig = int(signals.loc[ts])
        if sig != 0:
            try_open_trade(broker, risk, settings, ts, sig, row.atr, row.close)
        risk.update(ts, broker.equity())

    for pos in broker.open_positions():
        broker.close_position(pos, price=broker.last_price, time=window.index[-1], reason="EOD")

    closed = pd.DataFrame(broker.closed)
    print(f"\n=== Demo (paper replay, {len(window)} velas) ===")
    print(f"  Balance final : {broker.balance:.2f}")
    print(f"  Operaciones   : {len(closed)}")
    if not closed.empty:
        print(f"  Win rate      : {(closed['pnl'] > 0).mean() * 100:.1f}%")
    if risk.halted:
        print(f"  Detenido      : {risk.halt_reason}")
    print("  Registro      : logs/trades.csv")


def run_mt5_live(settings: Settings, model: AIModel | None, poll_seconds: int, live: bool = False) -> None:
    if live:
        confirm_live_trading()
        settings.risk = settings.live_risk  # límites estrictos para dinero real
        log.warning("MODO REAL | riesgo/op=%.2f%% pérdida diaria=%.1f%% DD máx=%.1f%% lotes máx=%.2f",
                    settings.risk.risk_per_trade * 100, settings.risk.max_daily_loss * 100,
                    settings.risk.max_drawdown * 100, settings.risk.max_lots)

    broker = MT5Broker(settings.mt5, settings.spec, allow_live=live)
    broker.connect()  # PermissionError si la cuenta es real y no pasaste --live
    if live and not broker.is_live:
        log.warning("Pediste --live pero la cuenta conectada es DEMO.")

    risk = RiskManager(settings.risk, broker.equity())
    held: dict[int, int] = {}
    last_bar = None
    log.info("Bot activo en %s %s (Ctrl+C para detener)", settings.market.symbol, settings.market.timeframe)

    try:
        while True:
            df = fetch_mt5(settings, n_bars=500, start_pos=1)  # solo velas cerradas
            if df.index[-1] != last_bar:
                last_bar = df.index[-1]
                broker.sync_closed_trades()
                ind = add_all_indicators(df, settings.indicators)
                signals = build_strategy_signals(settings, ind, model)
                row = ind.iloc[-1]
                _expire_old_positions(broker, held, settings.strategy.max_bars_in_trade, last_bar, row.close)
                sig = int(signals.iloc[-1])
                log.info("Vela %s | cierre=%.5f | señal=%s", last_bar, row.close, side_name(sig))
                if sig != 0:
                    try_open_trade(broker, risk, settings, last_bar, sig, row.atr, row.close)
                risk.update(last_bar, broker.equity())
                if risk.halted:
                    log.critical("Bot detenido por gestión de riesgo: %s. Las posiciones abiertas "
                                 "conservan su SL/TP en el servidor; revísalas en MT5.", risk.halt_reason)
                    break
            time.sleep(poll_seconds)
    except KeyboardInterrupt:
        log.info("Detenido por el usuario. Revisa posiciones abiertas en MT5.")
    finally:
        try:
            broker.sync_closed_trades()
        finally:
            broker.disconnect()


def cmd_demo(args, settings: Settings) -> None:
    model = None
    if settings.strategy.mode in ("ai", "hybrid"):
        model = AIModel.load(MODEL_PATH)

    if args.live and args.broker != "mt5":
        raise ValueError("--live solo aplica con --broker mt5.")
    if args.broker == "mt5":
        run_mt5_live(settings, model, args.poll, live=args.live)
    else:
        df = load_data(settings, "csv")
        ind = add_all_indicators(df, settings.indicators)
        signals = build_strategy_signals(settings, ind, model)
        run_paper_replay(settings, ind, signals, args.replay_bars)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="forex-ai: trading de forex con Python + pandas + scikit-learn (solo DEMO)")
    p.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    sub = p.add_subparsers(dest="command", required=True)

    def common(sp, source_default="csv"):
        sp.add_argument("--symbol", help="Ej: EURUSD, GBPUSD, USDJPY")
        sp.add_argument("--timeframe", help="M1 M5 M15 M30 H1 H4 D1")
        sp.add_argument("--mode", choices=["rules", "ai", "hybrid"], help="Tipo de estrategia")
        sp.add_argument("--source", default=source_default, choices=["auto", "mt5", "csv", "synthetic"])

    d = sub.add_parser("download", help="Descarga/genera datos y los guarda en data/market_data.csv")
    common(d, "auto")
    d.add_argument("--bars", type=int, help="Cantidad de velas")

    t = sub.add_parser("train", help="Entrena el modelo de IA")
    common(t)
    t.add_argument("--all-data", action="store_true", help="Entrenar con todo el histórico (sin reservar test)")

    b = sub.add_parser("backtest", help="Backtest fuera de muestra")
    common(b)

    m = sub.add_parser("demo", help="Trading con paper, MT5 demo o MT5 real (--live)")
    common(m)
    m.add_argument("--broker", default="paper", choices=["paper", "mt5"])
    m.add_argument("--replay-bars", type=int, default=500, help="Velas a reproducir en modo paper")
    m.add_argument("--poll", type=int, default=30, help="Segundos entre consultas en modo mt5")
    m.add_argument("--live", action="store_true",
                   help="Permite cuenta REAL (exige ALLOW_LIVE_TRADING en .env + confirmación escrita)")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    setup_logging(args.log_level)
    settings = apply_cli_overrides(get_settings(), args)

    handlers = {"download": cmd_download, "train": cmd_train, "backtest": cmd_backtest, "demo": cmd_demo}
    try:
        handlers[args.command](args, settings)
        return 0
    except (FileNotFoundError, ValueError, RuntimeError, PermissionError) as exc:
        log.error("%s", exc)
        return 1
    except Exception:  # noqa: BLE001
        log.exception("Error inesperado")
        return 2


if __name__ == "__main__":
    sys.exit(main())
