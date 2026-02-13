"""Silver: Financial KPIs — extracts and computes KPIs from bronze XBRL facts."""

from __future__ import annotations

import logging
import os
from collections import Counter

from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field

from backend.data.csv_writer import CsvWriter
from backend.models import FinancialFact, FinancialKPIs, PipelineState

logger = logging.getLogger(__name__)

# Deterministic mapping: XBRL tag → KPI field name
# Priority order matters — first match wins for each KPI
XBRL_KPI_MAP: list[tuple[str, str]] = [
    ("RevenueFromContractWithCustomerExcludingAssessedTax", "revenue"),
    ("Revenues", "revenue"),
    ("SalesRevenueNet", "revenue"),
    ("NetIncomeLoss", "net_income"),
    ("GrossProfit", "gross_profit"),
    ("OperatingIncomeLoss", "operating_income"),
    ("Assets", "total_assets"),
    ("Liabilities", "total_liabilities"),
    ("StockholdersEquity", "stockholders_equity"),
    ("LongTermDebt", "long_term_debt"),
    ("LongTermDebtNoncurrent", "long_term_debt"),
    ("CashAndCashEquivalentsAtCarryingValue", "cash_and_equivalents"),
    ("NetCashProvidedByUsedInOperatingActivities", "operating_cash_flow"),
    ("EarningsPerShareBasic", "eps_basic"),
    ("AssetsCurrent", "_assets_current"),
    ("LiabilitiesCurrent", "_liabilities_current"),
    ("PaymentsToAcquirePropertyPlantAndEquipment", "_capex"),
]


class AnomalyAnalysis(BaseModel):
    """LLM-generated anomaly flags for financial KPIs."""

    anomalies: list[str] = Field(
        description="List of anomalies or concerns in the financial data, "
        "each as a concise sentence referencing specific metrics."
    )


def _get_value_for_period(
    facts: list[FinancialFact], tag: str, end_date: str
) -> tuple[float | None, str | None]:
    """Get the value for a tag at a specific period end date."""
    matching = [f for f in facts if f.tag == tag and f.end == end_date]
    if not matching:
        return None, None
    latest = max(matching, key=lambda f: f.filed)
    return latest.value, latest.tag


def _get_annual_end_dates(facts: list[FinancialFact]) -> list[str]:
    """Get distinct annual period end dates sorted descending."""
    duration_facts = [
        f for f in facts
        if f.fp == "FY" and f.start is not None and f.taxonomy == "us-gaap"
    ]
    if not duration_facts:
        duration_facts = [f for f in facts if f.fp == "FY" and f.taxonomy == "us-gaap"]
    if not duration_facts:
        return []

    end_counts = Counter(f.end for f in duration_facts)
    return sorted(end_counts.keys(), reverse=True)


def _extract_kpis(facts: list[FinancialFact]) -> FinancialKPIs:
    """Deterministically extract KPIs from bronze facts."""
    if not facts:
        return FinancialKPIs()

    end_dates = _get_annual_end_dates(facts)
    if not end_dates:
        return FinancialKPIs()

    latest_end = end_dates[0]
    prior_end = end_dates[1] if len(end_dates) > 1 else None

    latest_fy_facts = [f for f in facts if f.end == latest_end]
    latest_fy = max(f.fy for f in latest_fy_facts) if latest_fy_facts else 0

    raw: dict[str, float | None] = {}
    source_tags: dict[str, str] = {}

    for xbrl_tag, kpi_name in XBRL_KPI_MAP:
        if kpi_name in raw and raw[kpi_name] is not None:
            continue
        value, actual_tag = _get_value_for_period(facts, xbrl_tag, latest_end)
        if value is not None:
            raw[kpi_name] = value
            source_tags[kpi_name] = actual_tag or xbrl_tag

    # Prior year revenue for YoY
    revenue_prior = None
    if prior_end:
        for xbrl_tag, kpi_name in XBRL_KPI_MAP:
            if kpi_name == "revenue":
                val, _ = _get_value_for_period(facts, xbrl_tag, prior_end)
                if val is not None:
                    revenue_prior = val
                    break

    net_income_prior = None
    if prior_end:
        net_income_prior, _ = _get_value_for_period(facts, "NetIncomeLoss", prior_end)

    # Compute derived metrics
    revenue = raw.get("revenue")
    gross_profit = raw.get("gross_profit")
    operating_income = raw.get("operating_income")
    total_liabilities = raw.get("total_liabilities")
    stockholders_equity = raw.get("stockholders_equity")
    operating_cash_flow = raw.get("operating_cash_flow")
    capex = raw.get("_capex")

    gross_margin = (gross_profit / revenue) if revenue and gross_profit else None
    operating_margin = (operating_income / revenue) if revenue and operating_income else None
    debt_to_equity = (
        (total_liabilities / stockholders_equity)
        if stockholders_equity and total_liabilities
        else None
    )
    revenue_yoy = (
        ((revenue - revenue_prior) / abs(revenue_prior))
        if revenue and revenue_prior and revenue_prior != 0
        else None
    )

    assets_current = raw.get("_assets_current")
    liabilities_current = raw.get("_liabilities_current")
    current_ratio = (
        (assets_current / liabilities_current)
        if assets_current and liabilities_current and liabilities_current != 0
        else None
    )

    free_cash_flow = (
        (operating_cash_flow - capex)
        if operating_cash_flow is not None and capex is not None
        else None
    )

    # Record derived metric sources
    if gross_margin is not None:
        source_tags["gross_margin"] = "derived: gross_profit / revenue"
    if operating_margin is not None:
        source_tags["operating_margin"] = "derived: operating_income / revenue"
    if debt_to_equity is not None:
        source_tags["debt_to_equity"] = "derived: total_liabilities / stockholders_equity"
    if revenue_yoy is not None:
        source_tags["revenue_yoy_change"] = "derived: YoY change"
    if current_ratio is not None:
        source_tags["current_ratio"] = "derived: assets_current / liabilities_current"
    if free_cash_flow is not None:
        source_tags["free_cash_flow"] = "derived: operating_cash_flow - capex"

    return FinancialKPIs(
        revenue=revenue,
        revenue_prior=revenue_prior,
        revenue_yoy_change=revenue_yoy,
        net_income=raw.get("net_income"),
        net_income_prior=net_income_prior,
        gross_profit=gross_profit,
        gross_margin=gross_margin,
        operating_income=operating_income,
        operating_margin=operating_margin,
        total_assets=raw.get("total_assets"),
        total_liabilities=total_liabilities,
        stockholders_equity=stockholders_equity,
        debt_to_equity=debt_to_equity,
        long_term_debt=raw.get("long_term_debt"),
        cash_and_equivalents=raw.get("cash_and_equivalents"),
        current_ratio=current_ratio,
        operating_cash_flow=operating_cash_flow,
        free_cash_flow=free_cash_flow,
        eps_basic=raw.get("eps_basic"),
        fiscal_year=latest_fy,
        period_end=latest_end,
        source_tags=source_tags,
    )


def _format_kpis_for_prompt(kpis: FinancialKPIs) -> str:
    """Format KPIs as readable text for LLM prompts."""
    lines = [f"Fiscal Year: {kpis.fiscal_year}", f"Period End: {kpis.period_end}"]
    if kpis.revenue is not None:
        lines.append(f"Revenue: ${kpis.revenue:,.0f}")
    if kpis.revenue_prior is not None:
        lines.append(f"Revenue (Prior Year): ${kpis.revenue_prior:,.0f}")
    if kpis.revenue_yoy_change is not None:
        lines.append(f"Revenue YoY Change: {kpis.revenue_yoy_change:.1%}")
    if kpis.net_income is not None:
        lines.append(f"Net Income: ${kpis.net_income:,.0f}")
    if kpis.gross_margin is not None:
        lines.append(f"Gross Margin: {kpis.gross_margin:.1%}")
    if kpis.operating_margin is not None:
        lines.append(f"Operating Margin: {kpis.operating_margin:.1%}")
    if kpis.total_assets is not None:
        lines.append(f"Total Assets: ${kpis.total_assets:,.0f}")
    if kpis.total_liabilities is not None:
        lines.append(f"Total Liabilities: ${kpis.total_liabilities:,.0f}")
    if kpis.stockholders_equity is not None:
        lines.append(f"Stockholders' Equity: ${kpis.stockholders_equity:,.0f}")
    if kpis.debt_to_equity is not None:
        lines.append(f"Debt-to-Equity: {kpis.debt_to_equity:.2f}")
    if kpis.long_term_debt is not None:
        lines.append(f"Long-term Debt: ${kpis.long_term_debt:,.0f}")
    if kpis.cash_and_equivalents is not None:
        lines.append(f"Cash & Equivalents: ${kpis.cash_and_equivalents:,.0f}")
    if kpis.current_ratio is not None:
        lines.append(f"Current Ratio: {kpis.current_ratio:.2f}")
    if kpis.operating_cash_flow is not None:
        lines.append(f"Operating Cash Flow: ${kpis.operating_cash_flow:,.0f}")
    if kpis.free_cash_flow is not None:
        lines.append(f"Free Cash Flow: ${kpis.free_cash_flow:,.0f}")
    if kpis.eps_basic is not None:
        lines.append(f"EPS (Basic): ${kpis.eps_basic:.2f}")
    return "\n".join(lines)


async def _flag_anomalies(kpis: FinancialKPIs, company_name: str) -> list[str]:
    """Use GPT-4o to flag anomalies in the extracted KPIs."""
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        logger.warning("OPENAI_API_KEY not set, skipping anomaly detection")
        return []

    llm = ChatOpenAI(model="gpt-4o", temperature=0)
    structured_llm = llm.with_structured_output(AnomalyAnalysis)

    kpi_summary = _format_kpis_for_prompt(kpis)
    result = await structured_llm.ainvoke(
        f"You are a financial analyst. Review these KPIs for {company_name} "
        f"and identify any anomalies, red flags, or concerns. "
        f"Focus on: negative margins, declining revenue, high leverage, "
        f"low liquidity, or unusual ratios.\n\n{kpi_summary}"
    )
    return result.anomalies


async def silver_financial_kpis_agent(state: PipelineState) -> dict:
    """Extract financial KPIs from bronze XBRL facts.

    Reads: state.bronze_facts
    Writes: silver_financial_kpis.csv
    """
    ticker = state["ticker"]
    facts = state.get("bronze_facts", [])
    company_info = state.get("company_info")
    errors: list[str] = []

    if not facts:
        errors.append("No bronze facts available for KPI extraction")
        return {
            "silver_kpis": None,
            "silver_kpis_path": None,
            "errors": errors,
            "current_stage": "error",
            "progress_messages": ["Silver KPIs: no bronze data to extract from"],
        }

    kpis = _extract_kpis(facts)

    # Flag anomalies with LLM
    company_name = company_info.company_name if company_info else ticker
    try:
        anomalies = await _flag_anomalies(kpis, company_name)
        kpis.anomalies = anomalies
    except Exception as e:
        logger.warning(f"Anomaly detection failed: {e}")
        errors.append(f"Anomaly detection skipped: {e}")

    # Write silver table
    kpi_dict = kpis.model_dump(exclude={"source_tags", "anomalies"})
    rows = []
    for field_name, value in kpi_dict.items():
        if field_name in ("fiscal_year", "period_end", "currency"):
            continue
        rows.append({
            "metric": field_name,
            "value": value,
            "source_tag": kpis.source_tags.get(field_name, "derived"),
            "fiscal_year": kpis.fiscal_year,
            "period_end": kpis.period_end,
            "currency": kpis.currency,
        })

    writer = CsvWriter(ticker)
    path = writer.write_silver("financial_kpis", rows, source_bronze="bronze_xbrl_facts.csv")

    return {
        "silver_kpis": kpis,
        "silver_kpis_path": str(path),
        "errors": errors,
        "current_stage": "silver",
        "progress_messages": [
            f"Extracted KPIs for FY{kpis.fiscal_year}: revenue=${kpis.revenue:,.0f}"
            if kpis.revenue
            else "Extracted KPIs (some metrics unavailable)"
        ],
    }
