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
import math
import sys
import time
from pathlib import Path

import pandas as pd

import os

from config import (LIVE_CONFIRM_PHRASE, LIVE_ENV_VALUE, LIVE_ENV_VAR, MARKET_DATA_CSV, MODEL_PATH,
                    Settings, get_settings)
from src.ai_model import AIModel
from src.autonomous_agent import AutonomousAgent
from src.backtester import Backtester, format_metrics
from src.broker_demo import MAGIC_NUMBER, BaseBroker, MT5Broker, MT5DemoBroker, PaperBroker
from src.indicators import add_all_indicators
from src.logger import replace_trades, setup_logging
from src.market_data import (AUTONOMOUS_TIMEFRAMES, _import_mt5, connect_mt5,
                             download_mt5_history, fetch_mt5, get_market_data,
                             load_csv, parse_utc_datetime, save_csv, save_mt5_history)
from src.risk_manager import RiskManager
from src.strategy_evaluator import StrategyEvaluator
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


def cmd_download_range(args, settings: Settings) -> None:
    start, end = parse_utc_datetime(args.date_from), parse_utc_datetime(args.date_to)
    symbol = args.symbol.strip()
    if not symbol:
        raise ValueError("Indica el nombre exacto de símbolo MT5.")
    mt5 = _import_mt5()
    if mt5 is None:
        raise RuntimeError("MetaTrader5 no está instalado en este Python de Windows.")
    terminal = mt5.terminal_info()
    already_connected = terminal is not None and getattr(terminal, "connected", True)
    if not already_connected:
        connect_mt5(mt5, settings.mt5)  # Reusa sesión abierta; nunca ejecuta login.
    try:
        frame, manifest = download_mt5_history(
            mt5, symbol, args.timeframe, start, end, chunk_days=args.chunk_days
        )
        if args.output:
            output = Path(args.output)
        else:
            token = f"{symbol}_{args.timeframe}_{start:%Y%m%d}_{end:%Y%m%d}.csv"
            output = Path("data") / "history" / token
        csv_path, manifest_path = save_mt5_history(frame, manifest, output)
        print(f"Descargadas {len(frame)} velas de {symbol} {args.timeframe}.")
        print(f"UTC: {frame['time'].iloc[0].isoformat()} -> {frame['time'].iloc[-1].isoformat()}")
        print(f"Huecos detectados: {len(manifest['gaps_over_1_5_bars'])}; chunks vacíos: {manifest['empty_chunks']}.")
        print(f"Integridad total certificada: no; revisar límites Max bars y manifest: {manifest_path}")
        print(f"CSV: {csv_path}")
    finally:
        if not already_connected:
            mt5.shutdown()


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
    requested_lots = settings.requested_volume_by_symbol.get(spec.symbol)
    loss_fn = getattr(broker, "loss_per_lot", None)
    if requested_lots is not None:
        valid, why = risk.validate_requested_volume(float(requested_lots), spec)
        if not valid:
            log.warning("Entrada rechazada para %s: %s.", spec.symbol, why)
            return False
        if callable(loss_fn):
            loss_per_lot = loss_fn(signal, price, sl)
            if loss_per_lot is None:
                log.warning("Entrada rechazada: MT5 no pudo calcular el riesgo monetario al SL.")
                return False
        else:
            try:
                loss_per_lot = (sl_dist / spec.pip_size) * spec.pip_value_per_lot(price)
            except (ValueError, ZeroDivisionError):
                log.warning("Entrada rechazada: no se puede estimar el riesgo por lote.")
                return False
        commission = float(settings.backtest.commission_per_lot)
        if not math.isfinite(commission) or commission < 0:
            log.warning("Entrada rechazada: comisión estimada inválida.")
            return False
        estimated_loss = (float(loss_per_lot) + commission) * float(requested_lots)
        risk_budget = broker.equity() * risk.cfg.risk_per_trade
        if not math.isfinite(estimated_loss) or estimated_loss > risk_budget:
            log.warning("Entrada rechazada: volumen %.2f; pérdida estimada USD %.4f "
                        "supera presupuesto USD %.4f (SL %.5f, comisión asumida USD %.2f/lote).",
                        requested_lots, estimated_loss, risk_budget, sl, commission)
            return False
        lots = float(requested_lots)
        margin_fn = getattr(broker, "margin_for", None)
        mt5 = getattr(broker, "mt5", None)
        if callable(margin_fn) and mt5 is not None:
            required_margin = margin_fn(signal, lots, price)
            account = mt5.account_info()
            free_margin = getattr(account, "margin_free", None) if account is not None else None
            if (required_margin is None or free_margin is None
                    or not math.isfinite(float(required_margin))
                    or not math.isfinite(float(free_margin))):
                log.warning("Entrada rechazada: volumen %.2f; pérdida estimada USD %.4f; "
                            "margen disponible no verificable.", lots, estimated_loss)
                return False
            if float(required_margin) > float(free_margin):
                log.warning("Entrada rechazada: volumen %.2f; pérdida estimada USD %.4f; "
                            "margen requerido USD %.4f supera margen libre USD %.4f.",
                            lots, estimated_loss, float(required_margin), float(free_margin))
                return False
    elif callable(loss_fn):
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

    if hasattr(broker, "estimated_commission_per_lot"):
        broker.estimated_commission_per_lot = float(settings.backtest.commission_per_lot)
    pos = broker.market_order(
        signal, lots, sl, tp, price=price, time=now,
        max_loss=broker.equity() * risk.cfg.risk_per_trade,
        max_spread_pips=settings.market.max_spread_pips,
    )
    if pos is not None:
        risk.register_open()
        return True
    return False


def _expire_old_positions(broker: BaseBroker, held: dict, max_bars: int, now: pd.Timestamp, price: float) -> None:
    for pos in broker.open_positions():
        if getattr(pos, "magic", 0) != MAGIC_NUMBER:
            continue  # Manual/foreign positions are never managed by this bot.
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

    broker = MT5Broker(settings.mt5, settings.spec, allow_live=True) if live else MT5DemoBroker(settings.mt5, settings.spec)
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


def cmd_analyze(args, settings: Settings) -> None:
    """Real-time MT5 analysis; broker instance rejects any order method call."""
    args.execute = False
    args.live = False
    cmd_autonomous(args, settings)


def cmd_paper(args, settings: Settings) -> None:
    """Historical paper replay from the validated project CSV, without MT5."""
    from types import SimpleNamespace
    if getattr(args, "source", "csv") != "csv":
        raise ValueError("El modo paper actual utiliza el CSV local validado (--source csv).")
    cmd_demo(SimpleNamespace(live=False, broker="paper", replay_bars=args.replay_bars), settings)


def cmd_autonomous(args, settings: Settings) -> None:
    if args.timeframe not in ("M15", "M30", "H1", "H4"):
        raise ValueError("El agente autónomo requiere timeframe M15 o superior.")
    if args.live and not args.execute:
        raise ValueError("--live requiere --execute; sin --execute el ciclo es solo análisis.")
    if args.live:
        confirm_live_trading()
        settings.risk = settings.live_risk
    if not args.execute:
        log.warning("Ciclo autónomo en SOLO ANÁLISIS: no se enviarán órdenes.")
    if args.execute and not args.live:
        broker = MT5DemoBroker(settings.mt5, settings.spec)
    else:
        broker = MT5Broker(settings.mt5, settings.spec, allow_live=bool(args.live),
                            allow_read_only_real=not args.execute, read_only=not args.execute)
    broker.connect()
    try:
        settings.market.symbol = broker.spec.symbol
        if args.live and not broker.is_live:
            raise RuntimeError("--live fue solicitado, pero la sesión MT5 abierta no es REAL.")
        evaluator = StrategyEvaluator(broker.spec, settings.backtest, settings.risk, settings.strategy)
        # This evaluator is fed only by the connected MT5 terminal and the
        # exact symbol/specification resolved by the broker connection.
        evaluator.source_verified = True
        risk = RiskManager(settings.risk, broker.equity())
        agent = AutonomousAgent(
            settings, broker, evaluator,
            fetch_data=lambda: fetch_mt5(settings, n_bars=max(5_000, settings.market.n_bars), start_pos=1),
            log_path=Path("logs/autonomous_decisions.jsonl"),
            state_path=Path("logs/autonomous_state.json"),
            execute=args.execute,
        )
        agent.attach_risk_manager(risk)
        agent.run_forever(args.cycle_minutes)
    finally:
        broker.disconnect()


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

    hist = sub.add_parser("download-range", help="Descarga histórico MT5 en UTC sin sobrescribir archivos")
    hist.add_argument("--symbol", required=True, help="Nombre exacto publicado por MT5; no se sustituye por alias")
    hist.add_argument("--timeframe", required=True, choices=AUTONOMOUS_TIMEFRAMES)
    hist.add_argument("--from", dest="date_from", required=True, help="ISO-8601 con zona, p.ej. 2025-01-01T00:00:00Z")
    hist.add_argument("--to", dest="date_to", required=True, help="ISO-8601 con zona, p.ej. 2026-01-01T00:00:00Z")
    hist.add_argument("--chunk-days", type=int, default=30)
    hist.add_argument("--output", help="Ruta CSV nueva; se rechaza si ya existe")

    t = sub.add_parser("train", help="Entrena el modelo de IA")
    common(t)
    t.add_argument("--all-data", action="store_true", help="Entrenar con todo el histórico (sin reservar test)")

    b = sub.add_parser("backtest", help="Backtest fuera de muestra")
    common(b)

    paper = sub.add_parser("paper", help="Replay histórico en PaperBroker; no conecta ni envía órdenes MT5")
    common(paper, "csv")
    paper.set_defaults(mode="rules")
    paper.add_argument("--replay-bars", type=int, default=500)

    analyze = sub.add_parser("analyze", help="Ciclo autónomo MT5 en solo lectura; nunca envía órdenes")
    analyze.add_argument("--symbol", help="Nombre exacto de símbolo MT5")
    analyze.add_argument("--timeframe", default="M15", choices=list(AUTONOMOUS_TIMEFRAMES))
    analyze.add_argument("--mode", default="hybrid", choices=["rules", "ai", "hybrid"])
    analyze.add_argument("--cycle-minutes", type=int, choices=[10, 15], default=15)

    m = sub.add_parser("demo", help="Trading con paper, MT5 demo o MT5 real (--live)")
    common(m)
    m.add_argument("--broker", default="paper", choices=["paper", "mt5"])
    m.add_argument("--replay-bars", type=int, default=500, help="Velas a reproducir en modo paper")
    m.add_argument("--poll", type=int, default=30, help="Segundos entre consultas en modo mt5")
    m.add_argument("--live", action="store_true",
                   help="Permite cuenta REAL (exige ALLOW_LIVE_TRADING en .env + confirmación escrita)")
    a = sub.add_parser("autonomous", help="Análisis autónomo periódico; solo análisis por defecto")
    a.add_argument("--symbol", help="Símbolo exacto de MT5")
    a.add_argument("--timeframe", default="M15", choices=["M15", "M30", "H1", "H4"])
    a.add_argument("--mode", default="hybrid", choices=["rules", "ai", "hybrid"])
    a.add_argument("--cycle-minutes", type=int, choices=[10, 15], default=15)
    a.add_argument("--execute", action="store_true", help="Autoriza órdenes del broker conectado")
    a.add_argument("--live", action="store_true", help="Cuenta REAL; exige gate y frase de confirmación")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    setup_logging(args.log_level)
    settings = apply_cli_overrides(get_settings(), args)

    handlers = {"download": cmd_download, "download-range": cmd_download_range,
                "train": cmd_train, "backtest": cmd_backtest,
                "paper": cmd_paper, "analyze": cmd_analyze,
                "demo": cmd_demo, "autonomous": cmd_autonomous}
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
