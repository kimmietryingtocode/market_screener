import pandas as pd

from backtest.make_test_targets import make_targets


def test_make_targets_uses_configured_symbols_and_monthly_sessions(tmp_path):
    prices = tmp_path / "prices.csv"
    output = tmp_path / "targets.csv"
    dates = pd.date_range("2024-12-31", "2025-04-02", freq="B")
    rows = [
        {"date": day.date(), "ticker": ticker, "adjusted_close": 100}
        for day in dates
        for ticker in ("AAA", "BBB", "BENCH")
    ]
    pd.DataFrame(rows).to_csv(prices, index=False)

    result = make_targets(
        prices,
        output,
        symbols=["AAA", "BBB"],
        benchmark="BENCH",
        signal_start=pd.Timestamp("2024-12-31").date(),
        end=pd.Timestamp("2025-03-31").date(),
        target_weight=0.2,
        provenance_id="test",
    )

    assert set(result["security_id"]) == {"AAA", "BBB"}
    assert result["target_weight"].eq(0.2).all()
    assert result["execution_date"].astype(str).iloc[0] == "2025-01-02"
    assert output.is_file()
