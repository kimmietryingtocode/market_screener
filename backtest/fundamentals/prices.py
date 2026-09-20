"""Optional filing-date prices, isolated from SEC acquisition."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol
import logging

import pandas as pd

from .core import canonical, finalize, number, put_derived, ratio, trailing_rows, utc
from .http import JsonClient, Response

LOG = logging.getLogger(__name__)


def _yahoo_cached(client: JsonClient, identity: str, as_of: pd.Timestamp, fetch) -> Response:
    versions = client.snapshots(identity)
    if not client.offline and (not versions or client.refresh and identity not in client._refreshed):
        try:
            client.store(identity, fetch())
            client._refreshed.add(identity)
        except Exception as exc:
            LOG.warning("yfinance request failed (%s); using eligible cache or empty", type(exc).__name__)
        versions = client.snapshots(identity)
    eligible = [version for version in versions if version.observed_at <= as_of]
    if not eligible:
        raise ValueError("no Yahoo snapshot observed by as_of")
    return eligible[-1]


@dataclass(frozen=True)
class PriceQuote:
    """A dated close with observation time and explicit split-basis evidence."""

    close: float
    date: pd.Timestamp
    available_at: pd.Timestamp
    currency: str
    split_free_since: pd.Timestamp | None = None


class PriceSource(Protocol):
    """Prices must be on the original share basis and available by as_of."""

    name: str

    def quote(self, ticker: str, filing_date: pd.Timestamp, *, as_of: pd.Timestamp) -> PriceQuote | None:
        """Return the filing-day close (previous session on non-trading days)."""
        ...


class YFinancePrices:
    """Replay archived Yahoo observations; unverified historical prices stay missing."""

    name = "yfinance"

    def __init__(self, client: JsonClient | None = None) -> None:
        """Share the versioned response cache with other providers."""
        self.client = client or JsonClient()

    def quote(self, ticker: str, filing_date: pd.Timestamp, *, as_of: pd.Timestamp) -> PriceQuote | None:
        """Return an eligible close; all unofficial Yahoo failures degrade to None."""
        try:
            import yfinance as yf

            stock = yf.Ticker(ticker)
            def history() -> dict:
                frame = stock.history(period="max", auto_adjust=False, actions=True)
                metadata = stock.get_history_metadata()
                return {"currency": metadata.get("currency", "UNKNOWN"), "rows": [{"date": pd.Timestamp(date).date().isoformat(), "close": None if pd.isna(r["Close"]) else float(r["Close"]), "split": float(r.get("Stock Splits", 0))} for date, r in frame.iterrows()]}
            response = _yahoo_cached(self.client, f"yfinance:{ticker}:prices", utc(as_of), history)
            rows = sorted(response.data["rows"], key=lambda r: r["date"])
            if not rows:
                return None
            target = pd.Timestamp(filing_date).date()
            eligible = [r for r in rows if pd.Timestamp(r["date"]).date() <= target and pd.notna(number(r["close"]))]
            if not eligible:
                return None
            chosen = eligible[-1]
            day = pd.Timestamp(chosen["date"])
            if (pd.Timestamp(target) - day).days > 7:
                return None
            # A 16:00 NY timestamp is conservative on early-close sessions.
            close_time = (day + pd.Timedelta(16, unit="h")).tz_localize("America/New_York").tz_convert("UTC")
            available = max(close_time, response.observed_at)
            if available > utc(as_of):
                return None
            splits = [pd.Timestamp(r["date"]) for r in rows if r["split"] and pd.Timestamp(r["date"]) <= utc(as_of).tz_localize(None)]
            # Yahoo Close may be split-adjusted. Do not use old prices across a later split.
            if any(s > day for s in splits):
                return None
            since = max(splits) + pd.Timedelta(1, unit="D") if splits else pd.Timestamp(rows[0]["date"])
            return PriceQuote(number(chosen["close"]), day, available, response.data["currency"], since)
        except Exception as exc:
            LOG.warning("yfinance prices %s unavailable (%s)", ticker, type(exc).__name__)
            return None


def enrich_prices(frame: pd.DataFrame, source: PriceSource) -> pd.DataFrame:
    """Add valuation metrics only when every price and share input is eligible."""
    output = []
    for _, group in frame.groupby(["ticker", "source", "as_of", "currency"], dropna=False):
        rows = group.to_dict("records")
        for original in rows:
            row = dict(original)
            cutoff = utc(row["as_of"])
            if pd.isna(row["filing_date"]):
                output.append(row)
                continue
            # Filing-date pricing ties valuation to publication, never to the earlier period end.
            quote = source.quote(row["ticker"], row["filing_date"], as_of=cutoff)
            if quote is None or utc(quote.available_at) > cutoff or quote.currency != row["currency"] or quote.currency == "UNKNOWN":
                output.append(row)
                continue
            if quote.date > row["filing_date"] or quote.date > cutoff.tz_localize(None) or not quote.close > 0:
                output.append(row)
                continue
            row.update(price=quote.close, price_date=quote.date, price_source=source.name, price_available_at=utc(quote.available_at), price_tag="Close", price_basis="filing_date_close")
            since = quote.split_free_since
            if since is not None and pd.notna(row["shares_outstanding"]) and row["shares_outstanding"] > 0 and row["period_end"] >= since:
                put_derived(row, "market_cap", quote.close * row["shares_outstanding"], [(row, "price"), (row, "shares_outstanding")], "filing_price_period_end_shares")
                # Period-end outstanding shares are an explicit dated proxy, not today's share count.
                put_derived(row, "pb", ratio(row["market_cap"], row["total_equity"]), [(row, "market_cap"), (row, "total_equity")], "filing_price_period_end_shares")
                put_derived(row, "ps", ratio(row["market_cap"], row["revenue_ttm"]), [(row, "market_cap"), (row, "revenue_ttm")], "filing_price_ttm")
            annual = next((r for r in rows if r["fiscal_period"] == "FY" and r["period_end"] == row["period_end"]), None)
            trailing = [annual] if annual is not None else trailing_rows(rows, row)
            if trailing and since is not None and pd.notna(trailing[0]["period_start"]) and trailing[0]["period_start"] >= since:
                put_derived(row, "diluted_eps_ttm", sum(r["diluted_eps"] for r in trailing), [(r, "diluted_eps") for r in trailing], "ttm_split_verified")
                if row["diluted_eps_ttm"] > 0:
                    put_derived(row, "pe", ratio(quote.close, row["diluted_eps_ttm"]), [(row, "price"), (row, "diluted_eps_ttm")], "filing_price_ttm")
            output.append(finalize(row))
    return canonical(output)
