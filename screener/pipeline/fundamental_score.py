"""Diagnostic scoring for validated FactSet Summary reports."""

from __future__ import annotations

from typing import Any

import pandas as pd

from .factset_common import ValidationIssue
from .factset_summary import FactSetActual


def _cagr(start: float | None, end: float | None, years: int) -> float | None:
    if start is None or end is None or start <= 0 or end <= 0 or years <= 0:
        return None
    return (end / start) ** (1.0 / years) - 1.0


def _percentile_rank(values: pd.Series, *, higher_is_better: bool) -> pd.Series:
    numeric = pd.to_numeric(values, errors="coerce")
    valid = numeric.dropna()
    result = pd.Series(float("nan"), index=values.index, dtype=float)
    if valid.empty:
        return result
    if len(valid) == 1:
        result.loc[valid.index] = 50.0
        return result

    ranks = valid.rank(method="average", ascending=True)
    percentiles = (ranks - 1.0) / (len(valid) - 1.0) * 100.0
    if not higher_is_better:
        percentiles = 100.0 - percentiles
    result.loc[valid.index] = percentiles
    return result


def _mean_if_enough(frame: pd.DataFrame, columns: list[str], minimum: int) -> pd.Series:
    values = frame[columns]
    means = values.mean(axis=1, skipna=True)
    return means.where(values.notna().sum(axis=1) >= minimum)


def _margin(value: float | None, sales: float | None) -> float | None:
    if value is None or sales in (None, 0):
        return None
    return value / sales


def build_summary_scores(
    shortlist: pd.DataFrame,
    summary_periods: list[FactSetActual],
    scoring_config: dict[str, Any],
) -> tuple[pd.DataFrame, list[ValidationIssue]]:
    """Score the required Summary package without changing market ordering."""
    periods_by_id: dict[str, list[FactSetActual]] = {}
    for row in summary_periods:
        periods_by_id.setdefault(row.identifier, []).append(row)

    metric_rows: list[dict[str, Any]] = []
    issues: list[ValidationIssue] = []
    for identifier, periods in periods_by_id.items():
        periods = sorted(periods, key=lambda row: row.fiscal_year)
        actuals = [row for row in periods if row.is_actual]
        forecasts = [row for row in periods if not row.is_actual]
        if not actuals:
            issues.append(
                ValidationIssue(
                    periods[0].source_file,
                    identifier,
                    "warning",
                    "NO_IDENTIFIED_ACTUAL_PERIOD",
                    "is_actual",
                    "Summary has no period identified as actual",
                )
            )
            continue

        latest = actuals[-1]
        comparison = forecasts[-1] if forecasts else actuals[0]
        eligibility_period = forecasts[-1] if forecasts else latest
        years = abs(comparison.fiscal_year - latest.fiscal_year)
        quality_periods = forecasts if forecasts else actuals

        forecast_ebitda_margins = [
            value
            for row in quality_periods
            if (value := _margin(row.ebitda, row.sales)) is not None
        ]
        forecast_fcf_margins = [
            value
            for row in quality_periods
            if (value := _margin(row.free_cash_flow, row.sales)) is not None
        ]

        net_debt = None
        if latest.total_debt is not None and latest.cash_and_short_term_investments is not None:
            net_debt = latest.total_debt - latest.cash_and_short_term_investments
        net_debt_to_ebitda = None
        if net_debt is not None and latest.ebitda is not None and latest.ebitda > 0:
            net_debt_to_ebitda = net_debt / latest.ebitda
        debt_to_assets = None
        if latest.total_debt is not None and latest.total_assets not in (None, 0):
            debt_to_assets = latest.total_debt / latest.total_assets
        net_debt_to_assets = None
        if net_debt is not None and latest.total_assets not in (None, 0):
            net_debt_to_assets = net_debt / latest.total_assets

        metric_rows.append(
            {
                "identifier": identifier,
                "summary_latest_actual_year": latest.fiscal_year,
                "summary_final_forecast_year": forecasts[-1].fiscal_year if forecasts else None,
                "summary_sales_cagr": _cagr(comparison.sales, latest.sales, years)
                if not forecasts
                else _cagr(latest.sales, comparison.sales, years),
                "summary_ebitda_cagr": _cagr(comparison.ebitda, latest.ebitda, years)
                if not forecasts
                else _cagr(latest.ebitda, comparison.ebitda, years),
                "summary_fcf_cagr": _cagr(comparison.free_cash_flow, latest.free_cash_flow, years)
                if not forecasts
                else _cagr(latest.free_cash_flow, comparison.free_cash_flow, years),
                "summary_latest_ebitda_margin": _margin(latest.ebitda, latest.sales),
                "summary_average_forward_ebitda_margin": (
                    sum(forecast_ebitda_margins) / len(forecast_ebitda_margins)
                    if forecast_ebitda_margins
                    else None
                ),
                "summary_latest_fcf_margin": _margin(latest.free_cash_flow, latest.sales),
                "summary_average_forward_fcf_margin": (
                    sum(forecast_fcf_margins) / len(forecast_fcf_margins)
                    if forecast_fcf_margins
                    else None
                ),
                "summary_net_debt": net_debt,
                "summary_net_debt_to_ebitda": net_debt_to_ebitda,
                "summary_debt_to_assets": debt_to_assets,
                "summary_net_debt_to_assets": net_debt_to_assets,
                "summary_final_ebitda": eligibility_period.ebitda,
                "summary_final_fcf": eligibility_period.free_cash_flow,
                "fundamental_viable": bool(
                    eligibility_period.ebitda is not None
                    and eligibility_period.ebitda > 0
                    and eligibility_period.free_cash_flow is not None
                    and eligibility_period.free_cash_flow > 0
                ),
                "turnaround_dependent": bool(
                    forecasts
                    and eligibility_period.ebitda is not None
                    and eligibility_period.ebitda > 0
                    and eligibility_period.free_cash_flow is not None
                    and eligibility_period.free_cash_flow > 0
                    and (
                        latest.ebitda is None
                        or latest.ebitda <= 0
                        or latest.free_cash_flow is None
                        or latest.free_cash_flow <= 0
                    )
                ),
            }
        )

    metrics = pd.DataFrame(metric_rows)
    approved = shortlist[shortlist["factset_symbol"].isin(periods_by_id)].copy()
    approved = approved.merge(
        metrics,
        how="inner",
        left_on="factset_symbol",
        right_on="identifier",
        validate="one_to_one",
    )

    growth_metrics = [
        "summary_sales_cagr",
        "summary_ebitda_cagr",
        "summary_fcf_cagr",
    ]
    quality_metrics = [
        "summary_latest_ebitda_margin",
        "summary_average_forward_ebitda_margin",
        "summary_latest_fcf_margin",
        "summary_average_forward_fcf_margin",
    ]
    strength_metrics = [
        "summary_net_debt_to_ebitda",
        "summary_debt_to_assets",
        "summary_net_debt_to_assets",
    ]
    for column in growth_metrics + quality_metrics:
        approved[f"{column}_percentile"] = _percentile_rank(
            approved[column], higher_is_better=True
        )
    for column in strength_metrics:
        approved[f"{column}_percentile"] = _percentile_rank(
            approved[column], higher_is_better=False
        )

    approved["growth_score"] = _mean_if_enough(
        approved,
        [f"{column}_percentile" for column in growth_metrics],
        minimum=1,
    )
    approved["operating_quality_score"] = _mean_if_enough(
        approved,
        [f"{column}_percentile" for column in quality_metrics],
        minimum=2,
    )
    approved["financial_strength_score"] = _mean_if_enough(
        approved,
        [f"{column}_percentile" for column in strength_metrics],
        minimum=1,
    )

    pillar_weights = scoring_config["pillar_weights"]
    approved["fundamental_score"] = (
        float(pillar_weights["growth"]) * approved["growth_score"]
        + float(pillar_weights["operating_quality"])
        * approved["operating_quality_score"]
        + float(pillar_weights["financial_strength"])
        * approved["financial_strength_score"]
    )
    approved["fundamental_score"] = approved["fundamental_score"].where(
        approved[
            ["growth_score", "operating_quality_score", "financial_strength_score"]
        ].notna().all(axis=1)
    )

    source_by_id = {
        identifier: periods[0].source_file
        for identifier, periods in periods_by_id.items()
    }
    for row in approved.itertuples():
        if pd.isna(row.fundamental_score):
            issues.append(
                ValidationIssue(
                    source_by_id[row.factset_symbol],
                    row.factset_symbol,
                    "warning",
                    "INCOMPLETE_DIAGNOSTIC_SCORE",
                    "fundamental_score",
                    "insufficient usable Summary metrics for all three diagnostic pillars",
                )
            )

    approved["market_score"] = approved["score"]
    approved = approved.sort_values("score", ascending=False, kind="stable").reset_index(drop=True)
    approved.insert(0, "market_rank", range(1, len(approved) + 1))
    approved["eligible"] = approved["fundamental_viable"]
    return approved, issues
