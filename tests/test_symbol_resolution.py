from types import SimpleNamespace

import pytest

from config import get_symbol_spec
from src.market_data import resolve_mt5_symbol


class FakeMT5:
    def __init__(self, names):
        self.names = names

    def symbol_info(self, name):
        return SimpleNamespace(name=name) if name in self.names else None

    def symbols_get(self):
        return [SimpleNamespace(name=name) for name in self.names]

    def last_error(self):
        return (0, "ok")


def test_symbol_spec_preserves_broker_suffix_and_case():
    assert get_symbol_spec("EURUSDm").symbol == "EURUSDm"
    assert get_symbol_spec("EURUSD.m").symbol == "EURUSD.m"


def test_resolver_prefers_exact_symbol():
    mt5 = FakeMT5(["EURUSD", "EURUSDm"])
    assert resolve_mt5_symbol(mt5, "EURUSD") == "EURUSD"


@pytest.mark.parametrize("actual", ["EURUSDm", "EURUSD.m"])
def test_resolver_finds_unique_broker_suffix(actual):
    assert resolve_mt5_symbol(FakeMT5([actual]), "EURUSD") == actual


def test_resolver_refuses_ambiguous_suffixes():
    with pytest.raises(ValueError, match="ambiguo"):
        resolve_mt5_symbol(FakeMT5(["EURUSDm", "EURUSD.a"]), "EURUSD")


def test_resolver_refuses_missing_symbol():
    with pytest.raises(ValueError, match="no publica"):
        resolve_mt5_symbol(FakeMT5(["GBPUSD"]), "EURUSD")
