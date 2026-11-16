import pytest
from pydantic import ValidationError

from app.schemas.ai import CreateAIReportRequest


def test_report_request_requires_the_matching_target():
    assert CreateAIReportRequest(kind="recommendations").kind == "recommendations"
    assert CreateAIReportRequest(kind="stock", ticker="aapl").ticker == "aapl"
    assert CreateAIReportRequest(kind="backtest", backtest_id=42).backtest_id == 42

    with pytest.raises(ValidationError):
        CreateAIReportRequest(kind="stock")
    with pytest.raises(ValidationError):
        CreateAIReportRequest(kind="backtest")
    with pytest.raises(ValidationError):
        CreateAIReportRequest(kind="recommendations", ticker="AAPL")
