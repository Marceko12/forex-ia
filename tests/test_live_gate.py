"""Tests de la compuerta de dinero real (con un MetaTrader5 falso)."""
from types import SimpleNamespace

import app
import src.broker_demo as bd
from config import LIVE_CONFIRM_PHRASE, LIVE_ENV_VALUE, LIVE_ENV_VAR, MT5Config, get_symbol_spec


class FakeMT5:
    ACCOUNT_TRADE_MODE_DEMO, ACCOUNT_TRADE_MODE_CONTEST, ACCOUNT_TRADE_MODE_REAL = 0, 1, 2
    ORDER_TYPE_BUY, ORDER_TYPE_SELL = 0, 1
    TRADE_ACTION_DEAL, ORDER_TIME_GTC, ORDER_FILLING_IOC = 1, 0, 1
    TRADE_RETCODE_DONE = 10009

    def __init__(self, trade_mode=0, currency="USD", trade_allowed=True, algo_allowed=True):
        self.info = SimpleNamespace(trade_mode=trade_mode, currency=currency, trade_allowed=trade_allowed,
                                    login=1, server="X", balance=1000.0, equity=1000.0, margin_free=1000.0)
        self.term = SimpleNamespace(trade_allowed=algo_allowed)
        self.shutdown_called = False
        self.order_check_retcode = 0
        self.order_send_calls = 0
        self.requests = []

    init_ok = True
    login_ok = True
    init_calls = 0
    login_calls = 0

    def initialize(self, **kw):
        self.init_calls += 1
        self.init_kwargs = kw
        return self.init_ok

    def login(self, *a, **kw):
        self.login_calls += 1
        return self.login_ok

    def last_error(self): return (0, "")
    def account_info(self): return self.info
    def terminal_info(self): return self.term
    def symbol_select(self, *a): return True
    def symbol_info(self, s): return SimpleNamespace(volume_min=0.01, volume_max=50.0, volume_step=0.01,
                                                     trade_contract_size=100000, point=0.00001,
                                                     trade_stops_level=10)
    def symbol_info_tick(self, s): return SimpleNamespace(bid=1.10000, ask=1.10010)
    def order_calc_profit(self, kind, symbol, volume, opening, closing):
        return -abs(closing - opening) * 100000 * volume
    def order_calc_margin(self, kind, symbol, volume, price): return 10.0
    def order_check(self, request):
        self.requests.append(request)
        return SimpleNamespace(retcode=self.order_check_retcode)
    def order_send(self, request):
        self.order_send_calls += 1
        return SimpleNamespace(retcode=self.TRADE_RETCODE_DONE, order=123, price=request["price"])
    def shutdown(self): self.shutdown_called = True


def _connect(fake, allow_live):
    original = bd._import_mt5
    bd._import_mt5 = lambda: fake
    try:
        broker = bd.MT5Broker(MT5Config(), get_symbol_spec("EURUSD"), allow_live=allow_live)
        broker.connect()
        return broker
    finally:
        bd._import_mt5 = original


def _expect(exc, fn):
    try:
        fn()
    except exc:
        return
    raise AssertionError(f"Debió lanzar {exc.__name__}")


def test_real_account_blocked_without_live_flag():
    fake = FakeMT5(trade_mode=2)
    _expect(PermissionError, lambda: _connect(fake, allow_live=False))
    assert fake.shutdown_called  # no deja la sesión abierta


def test_real_account_allowed_with_live_flag():
    broker = _connect(FakeMT5(trade_mode=2), allow_live=True)
    assert broker.is_live and broker.source == "live"


def test_demo_account_ok_without_flag():
    broker = _connect(FakeMT5(trade_mode=0), allow_live=False)
    assert not broker.is_live and broker.source == "demo"


def test_demo_alias_never_allows_live():
    original = bd._import_mt5
    bd._import_mt5 = lambda: FakeMT5(trade_mode=2)
    try:
        _expect(PermissionError, lambda: bd.MT5DemoBroker(MT5Config(), get_symbol_spec("EURUSD")).connect())
    finally:
        bd._import_mt5 = original


def test_live_refuses_non_usd_account():
    _expect(RuntimeError, lambda: _connect(FakeMT5(trade_mode=2, currency="EUR"), allow_live=True))


def test_refuses_when_trading_not_allowed():
    _expect(RuntimeError, lambda: _connect(FakeMT5(trade_allowed=False), allow_live=False))
    _expect(RuntimeError, lambda: _connect(FakeMT5(algo_allowed=False), allow_live=False))


def test_broker_uses_real_lot_limits():
    fake = FakeMT5()
    fake.symbol_info = lambda s: SimpleNamespace(volume_min=0.1, volume_max=20.0, volume_step=0.1,
                                                 trade_contract_size=100000)
    broker = _connect(fake, allow_live=False)
    assert broker.spec.min_lot == 0.1 and broker.spec.lot_step == 0.1


def test_order_without_stop_loss_is_rejected():
    broker = _connect(FakeMT5(), allow_live=False)
    assert broker.market_order(1, 0.1, sl=0.0, tp=1.2) is None


def test_order_is_sent_only_after_successful_preflight(monkeypatch):
    fake = FakeMT5()
    broker = _connect(fake, allow_live=False)
    monkeypatch.setattr(bd, "append_trades", lambda _: None)
    position = broker.market_order(1, 0.01, sl=1.098, tp=1.104)
    assert position is not None
    assert len(fake.requests) == 1 and fake.order_send_calls == 1


def test_failed_order_check_never_sends_order(monkeypatch):
    fake = FakeMT5()
    fake.order_check_retcode = 10016
    broker = _connect(fake, allow_live=False)
    monkeypatch.setattr(bd, "append_trades", lambda _: None)
    assert broker.market_order(1, 0.01, sl=1.098, tp=1.104) is None
    assert len(fake.requests) == 1 and fake.order_send_calls == 0


# ------------------------- confirmación escrita --------------------------- #
def test_confirm_requires_env_var():
    _expect(PermissionError, lambda: app.confirm_live_trading(lambda _: LIVE_CONFIRM_PHRASE, environ={}))


def test_confirm_requires_exact_phrase():
    env = {LIVE_ENV_VAR: LIVE_ENV_VALUE}
    _expect(PermissionError, lambda: app.confirm_live_trading(lambda _: "si", environ=env))
    _expect(PermissionError, lambda: app.confirm_live_trading(lambda _: "operar en real", environ=env))


def test_confirm_passes_with_both():
    app.confirm_live_trading(lambda _: LIVE_CONFIRM_PHRASE, environ={LIVE_ENV_VAR: LIVE_ENV_VALUE})


def test_live_risk_is_stricter_than_default():
    from config import get_settings
    s = get_settings()
    assert s.live_risk.risk_per_trade < s.risk.risk_per_trade
    assert s.live_risk.max_drawdown < s.risk.max_drawdown
    assert s.live_risk.max_daily_loss < s.risk.max_daily_loss


# ------------------------- conexión robusta ------------------------------- #
def test_connect_uses_open_session_without_credentials_and_with_timeout():
    fake = FakeMT5()  # cuenta abierta: login=1
    cfg = MT5Config(login=1, password="secreta", server="X")
    bd.connect_mt5(fake, cfg)
    assert "password" not in fake.init_kwargs and "login" not in fake.init_kwargs
    assert fake.init_kwargs["timeout"] >= 30_000
    assert fake.login_calls == 0  # ya estaba en la cuenta correcta


def test_connect_logs_in_only_if_account_differs():
    fake = FakeMT5()  # abierta: login=1
    bd.connect_mt5(fake, MT5Config(login=999, password="x", server="S"))
    assert fake.login_calls == 1


def test_connect_failure_does_not_leak_password():
    fake = FakeMT5()
    fake.init_ok = False
    fake.last_error = lambda: (-10005, "IPC timeout")
    import time as _t
    original_sleep, _t.sleep = _t.sleep, lambda s: None
    try:
        try:
            bd.connect_mt5(fake, MT5Config(login=1, password="secreta", server="S"))
        except RuntimeError as exc:
            assert "secreta" not in str(exc) and "IPC timeout" in str(exc)
        else:
            raise AssertionError("Debió lanzar RuntimeError")
    finally:
        _t.sleep = original_sleep
    assert fake.init_calls == 3  # reintentó
