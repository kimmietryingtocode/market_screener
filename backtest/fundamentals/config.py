"""Canonical SEC concepts; ordered alternatives are resolved per period."""

CONCEPTS = {
    "revenue": {"tags": ("Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax", "RevenueFromContractWithCustomerIncludingAssessedTax", "SalesRevenueNet")},
    "cost_of_revenue": {"tags": ("CostOfRevenue", "CostOfGoodsAndServicesSold", "CostOfGoodsSold")},
    "gross_profit": {"tags": ("GrossProfit",), "formula": ("revenue", "-", "cost_of_revenue")},
    "operating_income": {"tags": ("OperatingIncomeLoss",)},
    "depreciation_and_amortization": {"tags": ("DepreciationDepletionAndAmortization", "DepreciationDepletionAndAmortizationPropertyPlantAndEquipment", "DepreciationAndAmortization")},
    "net_income": {"tags": ("NetIncomeLoss", "ProfitLoss")},
    "diluted_eps": {"tags": ("EarningsPerShareDiluted",), "unit": "USD/shares", "additive": False},
    "diluted_shares": {"tags": ("WeightedAverageNumberOfDilutedSharesOutstanding",), "unit": "shares", "additive": False},
    "total_assets": {"tags": ("Assets",), "instant": True},
    "total_equity": {"tags": ("StockholdersEquity", "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"), "instant": True},
    "current_assets": {"tags": ("AssetsCurrent",), "instant": True},
    "current_liabilities": {"tags": ("LiabilitiesCurrent",), "instant": True},
    "total_debt": {"tags": ("LongTermDebtAndShortTermBorrowings",), "instant": True, "formula": ("current_debt", "+", "noncurrent_debt")},
    "operating_cash_flow": {"tags": ("NetCashProvidedByUsedInOperatingActivities",)},
    "capex": {"tags": ("PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsToAcquireProductiveAssets")},
    "shares_outstanding": {"tags": ("CommonStockSharesOutstanding",), "instant": True, "unit": "shares"},
    "current_debt": {"tags": ("ShortTermBorrowingsAndCurrentPortionOfLongTermDebt",), "instant": True, "internal": True, "formula": ("current_long_term_debt", "+", "short_term_borrowings")},
    "current_long_term_debt": {"tags": ("LongTermDebtCurrent",), "instant": True, "internal": True},
    "noncurrent_debt": {"tags": ("LongTermDebtNoncurrent",), "instant": True, "internal": True},
    "short_term_borrowings": {"tags": ("ShortTermBorrowings", "CommercialPaper"), "instant": True, "internal": True},
}

RAW = tuple(k for k, v in CONCEPTS.items() if not v.get("internal"))
METRICS = ("gross_margin", "operating_margin", "net_margin", "current_ratio", "debt_to_equity", "roe", "roa", "asset_turnover", "ebitda", "free_cash_flow")
PRICE_METRICS = ("pe", "pb", "ps", "market_cap", "ev_ebitda")
