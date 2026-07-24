import math
from datetime import date, datetime
from typing import Any

import pandas as pd
import yfinance as yf

DEFAULT_MARKET_TICKERS = ["SPY", "QQQ", "DIA", "IWM", "GLD", "TLT", "BTC-USD"]
MAX_TICKERS_PER_REQUEST = 25


def parse_tickers(raw_tickers: str | None) -> list[str]:
    if not raw_tickers:
        return DEFAULT_MARKET_TICKERS

    tickers = [ticker.strip().upper() for ticker in raw_tickers.split(",") if ticker.strip()]
    deduped = list(dict.fromkeys(tickers))

    if not deduped:
        raise ValueError("at least one ticker is required")
    if len(deduped) > MAX_TICKERS_PER_REQUEST:
        raise ValueError(f"at most {MAX_TICKERS_PER_REQUEST} tickers can be requested at once")

    return deduped


def _json_value(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    if pd.isna(value):
        return None
    if hasattr(value, "item"):
        return _json_value(value.item())
    return value


def _float_or_none(value: Any) -> float | None:
    value = _json_value(value)
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int_or_none(value: Any) -> int | None:
    value = _json_value(value)
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _fast_info_value(fast_info: Any, *keys: str) -> Any:
    for key in keys:
        try:
            value = fast_info.get(key)
        except Exception:
            value = None
        if value is not None:
            return value

        try:
            value = getattr(fast_info, key)
        except Exception:
            value = None
        if value is not None:
            return value

    return None


def fetch_market_quotes(tickers: list[str]) -> list[dict[str, Any]]:
    quote_bundle = yf.Tickers(" ".join(tickers))
    quotes: list[dict[str, Any]] = []

    for ticker in tickers:
        yticker = quote_bundle.tickers.get(ticker) or yf.Ticker(ticker)

        try:
            fast_info = yticker.fast_info
        except Exception as exc:
            raise ValueError(f"failed to fetch quote for {ticker}") from exc

        last_price = _float_or_none(_fast_info_value(fast_info, "lastPrice", "last_price"))
        previous_close = _float_or_none(
            _fast_info_value(fast_info, "previousClose", "regularMarketPreviousClose")
        )
        change = None
        change_percent = None
        if last_price is not None and previous_close not in (None, 0):
            change = last_price - previous_close
            change_percent = change / previous_close

        quote = {
            "ticker": ticker,
            "price": last_price,
            "previous_close": previous_close,
            "change": change,
            "change_percent": change_percent,
            "open": _float_or_none(_fast_info_value(fast_info, "open")),
            "day_high": _float_or_none(_fast_info_value(fast_info, "dayHigh", "day_high")),
            "day_low": _float_or_none(_fast_info_value(fast_info, "dayLow", "day_low")),
            "fifty_two_week_high": _float_or_none(_fast_info_value(fast_info, "yearHigh")),
            "fifty_two_week_low": _float_or_none(_fast_info_value(fast_info, "yearLow")),
            "volume": _int_or_none(_fast_info_value(fast_info, "lastVolume", "last_volume")),
            "market_cap": _int_or_none(_fast_info_value(fast_info, "marketCap", "market_cap")),
            "currency": _json_value(_fast_info_value(fast_info, "currency")),
            "exchange": _json_value(_fast_info_value(fast_info, "exchange")),
            "quote_type": _json_value(_fast_info_value(fast_info, "quoteType", "quote_type")),
        }

        data_fields = [value for key, value in quote.items() if key != "ticker"]
        if all(value is None for value in data_fields):
            raise RuntimeError(f"no quote data returned for {ticker}")

        quotes.append(quote)

    return quotes


def _ticker_history_frame(data: pd.DataFrame, ticker: str, tickers: list[str]) -> pd.DataFrame:
    if isinstance(data.columns, pd.MultiIndex):
        if ticker not in data.columns.get_level_values(1):
            return pd.DataFrame()
        return data.xs(ticker, axis=1, level=1, drop_level=True)

    if len(tickers) == 1:
        return data

    return pd.DataFrame()


def fetch_price_bars(tickers: list[str], period: str, interval: str) -> dict[str, list[dict[str, Any]]]:
    data = yf.download(
        tickers,
        period=period,
        interval=interval,
        auto_adjust=False,
        progress=False,
        group_by="column",
        threads=True,
    )
    if data.empty:
        raise ValueError("no historical price data returned for given tickers/period/interval")

    history: dict[str, list[dict[str, Any]]] = {}
    for ticker in tickers:
        frame = _ticker_history_frame(data, ticker, tickers)
        if frame.empty:
            history[ticker] = []
            continue

        frame = frame.dropna(how="all")
        bars: list[dict[str, Any]] = []
        for index, row in frame.iterrows():
            bars.append(
                {
                    "timestamp": _json_value(index),
                    "open": _float_or_none(row.get("Open")),
                    "high": _float_or_none(row.get("High")),
                    "low": _float_or_none(row.get("Low")),
                    "close": _float_or_none(row.get("Close")),
                    "adjusted_close": _float_or_none(row.get("Adj Close")),
                    "volume": _int_or_none(row.get("Volume")),
                }
            )
        history[ticker] = bars

    return history
