import pandas as pd
import pytest

from backtest.normalize_price_csv import normalize


def test_normalize_yahoo_long_csv(tmp_path):
    source = tmp_path / "source.csv"
    output = tmp_path / "prices.csv"
    pd.DataFrame(
        [
            {"ticker": " aapl ", "trading_date": "2025-01-02", "close": "243.85"},
            {"ticker": "SPY", "trading_date": "2025-01-02", "close": "590.00"},
        ]
    ).to_csv(source, index=False)

    manifest = normalize(source, output, price_basis="yahoo_auto_adjust_true")

    assert pd.read_csv(output).to_dict("records") == [
        {"date": "2025-01-02", "ticker": "AAPL", "adjusted_close": 243.85},
        {"date": "2025-01-02", "ticker": "SPY", "adjusted_close": 590.0},
    ]
    assert manifest["source_price_basis"] == "yahoo_auto_adjust_true"
    assert manifest["rows"] == 2


def test_normalize_accepts_already_normalized_prices(tmp_path):
    source = tmp_path / "source.csv"
    output = tmp_path / "prices.csv"
    pd.DataFrame([{"date": "2025-01-02", "ticker": "SPY", "adjusted_close": 590}]).to_csv(source, index=False)

    normalize(source, output)

    assert output.read_text().splitlines() == [
        "date,ticker,adjusted_close",
        "2025-01-02,SPY,590",
    ]


@pytest.mark.parametrize(
    "rows, message",
    [
        ([{"ticker": "SPY", "trading_date": "2025-01-02", "close": 0}], "nonpositive"),
        ([{"ticker": "SPY", "trading_date": "2025-01-02", "close": 590}, {"ticker": "SPY", "trading_date": "2025-01-02", "close": 591}], "duplicate"),
    ],
)
def test_normalize_rejects_bad_prices(tmp_path, rows, message):
    source = tmp_path / "source.csv"
    pd.DataFrame(rows).to_csv(source, index=False)

    with pytest.raises(ValueError, match=message):
        normalize(source, tmp_path / "prices.csv")
