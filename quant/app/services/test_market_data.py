import math

import pytest

from app.services.market_data import (
    DEFAULT_MARKET_TICKERS,
    _float_or_none,
    _int_or_none,
    _json_value,
    parse_tickers,
)


def test_parse_tickers_defaults_when_empty():
    assert parse_tickers(None) == DEFAULT_MARKET_TICKERS
    assert parse_tickers("") == DEFAULT_MARKET_TICKERS


def test_parse_tickers_dedupes_and_uppercases():
    assert parse_tickers("aapl, AAPL , msft") == ["AAPL", "MSFT"]


def test_parse_tickers_rejects_blank_after_stripping():
    with pytest.raises(ValueError, match="at least one ticker"):
        parse_tickers(" , , ")


def test_parse_tickers_rejects_too_many():
    too_many = ",".join(f"T{i}" for i in range(26))
    with pytest.raises(ValueError, match="at most 25"):
        parse_tickers(too_many)


def test_json_value_converts_nan_and_inf_to_none():
    assert _json_value(float("nan")) is None
    assert _json_value(float("inf")) is None


def test_json_value_passes_through_plain_values():
    assert _json_value("USD") == "USD"
    assert _json_value(42) == 42


def test_float_or_none_handles_bad_input():
    assert _float_or_none(None) is None
    assert _float_or_none("not a number") is None
    assert _float_or_none("3.5") == 3.5


def test_int_or_none_handles_bad_input():
    assert _int_or_none(None) is None
    assert _int_or_none(math.nan) is None
    assert _int_or_none("100") == 100
