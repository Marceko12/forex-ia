"""Tests de configuración: .env con BOM, símbolos del broker y overrides por entorno."""
import os
import tempfile
from pathlib import Path

import config
from config import get_symbol_spec


def test_dotenv_with_bom_is_parsed():
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / ".env"
        path.write_bytes("\ufeffTEST_BOM_KEY=valor\r\nOTRA=1\r\n".encode("utf-8"))
        os.environ.pop("TEST_BOM_KEY", None)
        config._load_dotenv(path)
        assert os.environ.get("TEST_BOM_KEY") == "valor"
        os.environ.pop("TEST_BOM_KEY", None)
        os.environ.pop("OTRA", None)


def test_symbol_keeps_broker_suffix_and_case():
    for name in ("EURUSDm", "EURUSD.m", "EURUSD#", "EURUSD"):
        spec = get_symbol_spec(name)
        assert spec.symbol == name
        assert spec.pip_size == 0.0001
        assert spec.pip_value_per_lot(1.10) == 10.0


def test_jpy_pair_with_suffix_detected():
    spec = get_symbol_spec("USDJPYm")
    assert spec.pip_size == 0.01
    assert abs(spec.pip_value_per_lot(150.0) - 1000 / 150) < 1e-9


def test_cross_pair_without_conversion_is_not_guessed():
    import pytest

    with pytest.raises(ValueError, match="conversión"):
        get_symbol_spec("EURCADmicro").pip_value_per_lot(1.45)


def test_settings_read_symbol_from_env():
    os.environ["MARKET_SYMBOL"], os.environ["MARKET_TIMEFRAME"] = "EURUSDm", "m15"
    try:
        s = config.get_settings()
        assert s.market.symbol == "EURUSDm" and s.market.timeframe == "M15"
        assert s.spec.symbol == "EURUSDm"
    finally:
        os.environ.pop("MARKET_SYMBOL", None)
        os.environ.pop("MARKET_TIMEFRAME", None)
