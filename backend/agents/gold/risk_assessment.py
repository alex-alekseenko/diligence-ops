"""Gold: Risk Assessment — scores 5 risk dimensions using GPT-4o."""

from __future__ import annotations

import logging
import os

from langchain_openai import ChatOpenAI

from backend.agents.silver.financial_kpis import _format_kpis_for_prompt
from backend.data.csv_writer import CsvWriter
from backend.models import (
    FinancialKPIs,
    PipelineState,
    RedFlag,
    RiskAssessment,
    RiskDimension,
)

logger = logging.getLogger(__name__)

RISK_ANALYSIS_PROMPT = """\
You are a senior financial analyst performing due diligence on {company_name} ({ticker}).

Based on the following financial KPIs extracted from their latest SEC 10-K filing, \
score the company across five risk dimensions.

## Financial KPIs
{kpi_summary}

## Company Information
- Ticker: {ticker}
- Company: {company_name}
- SIC: {sic_description}
- Fiscal Year End: {fiscal_year_end}

## Instructions
For each risk dimension, provide:
1. A score from 1 to 5 (1 = very low risk, 5 = critical risk)
2. A reasoning paragraph (2-4 sentences) citing specific numbers from the KPIs
3. The key metrics you used for this assessment

Score these five dimensions:
1. **Financial Health**: Profitability, margins, earnings quality
2. **Market Position**: Revenue growth, competitive indicators
3. **Operational Risk**: Cost structure, operational efficiency
4. **Governance**: Leverage, capital structure, financial transparency
5. **Liquidity**: Cash position, current ratio, cash flow adequacy

Also identify the top 3 red flags (if any) with severity (Low/Medium/High/Critical) \
and specific evidence from the data.
"""


def _placeholder_risk(kpis: FinancialKPIs) -> RiskAssessment:
    """Generate rule-based risk scores when LLM is unavailable."""
    dimensions = []

    margin_score = 1
    reasoning = "No margin data available."
    if kpis.gross_margin is not None:
        if kpis.gross_margin < 0.2:
            margin_score = 4
        elif kpis.gross_margin < 0.35:
            margin_score = 3
        elif kpis.gross_margin < 0.5:
            margin_score = 2
        reasoning = f"Gross margin is {kpis.gross_margin:.1%}."
    dimensions.append(RiskDimension(
        dimension="Financial Health", score=margin_score,
        reasoning=reasoning, key_metrics=["gross_margin", "operating_margin", "net_income"],
    ))

    growth_score = 2
    if kpis.revenue_yoy_change is not None:
        if kpis.revenue_yoy_change < -0.1:
            growth_score = 5
        elif kpis.revenue_yoy_change < 0:
            growth_score = 3
        elif kpis.revenue_yoy_change > 0.1:
            growth_score = 1
    dimensions.append(RiskDimension(
        dimension="Market Position", score=growth_score,
        reasoning=f"Revenue YoY change: {kpis.revenue_yoy_change:.1%}." if kpis.revenue_yoy_change else "No YoY data.",
        key_metrics=["revenue", "revenue_yoy_change"],
    ))

    op_score = 2
    if kpis.operating_margin is not None:
        if kpis.operating_margin < 0:
            op_score = 5
        elif kpis.operating_margin < 0.1:
            op_score = 3
    dimensions.append(RiskDimension(
        dimension="Operational Risk", score=op_score,
        reasoning=f"Operating margin: {kpis.operating_margin:.1%}." if kpis.operating_margin else "No operating margin data.",
        key_metrics=["operating_margin", "operating_income"],
    ))

    gov_score = 2
    if kpis.debt_to_equity is not None:
        if kpis.debt_to_equity > 5:
            gov_score = 4
        elif kpis.debt_to_equity > 2:
            gov_score = 3
    dimensions.append(RiskDimension(
        dimension="Governance", score=gov_score,
        reasoning=f"Debt-to-equity: {kpis.debt_to_equity:.2f}." if kpis.debt_to_equity else "No D/E data.",
        key_metrics=["debt_to_equity", "long_term_debt"],
    ))

    liq_score = 2
    if kpis.current_ratio is not None:
        if kpis.current_ratio < 1.0:
            liq_score = 4
        elif kpis.current_ratio < 1.5:
            liq_score = 3
    dimensions.append(RiskDimension(
        dimension="Liquidity", score=liq_score,
        reasoning=f"Current ratio: {kpis.current_ratio:.2f}." if kpis.current_ratio else "No current ratio data.",
        key_metrics=["current_ratio", "cash_and_equivalents"],
    ))

    scores = [d.score for d in dimensions]
    composite = sum(scores) / len(scores) if scores else 2.5
    level = "Low" if composite <= 2 else "Medium" if composite <= 3 else "High" if composite <= 4 else "Critical"

    return RiskAssessment(
        dimensions=dimensions, composite_score=round(composite, 2),
        risk_level=level, red_flags=[],
    )


async def gold_risk_assessment_agent(state: PipelineState) -> dict:
    """Score 5 risk dimensions from silver KPIs.

    Reads: state.silver_kpis, state.company_info
    Writes: gold_risk_assessment.csv
    """
    ticker = state["ticker"]
    kpis = state.get("silver_kpis")
    company_info = state.get("company_info")
    errors: list[str] = []

    if not kpis:
        errors.append("No silver KPIs available for risk analysis")
        return {
            "gold_risk_scores": None,
            "gold_risk_path": None,
            "errors": errors,
            "current_stage": "error",
            "progress_messages": ["Gold risk: no KPI data"],
        }

    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        logger.warning("OPENAI_API_KEY not set, generating placeholder risk scores")
        risk = _placeholder_risk(kpis)
        errors.append("Risk analysis used placeholder scores (no API key)")
    else:
        kpi_summary = _format_kpis_for_prompt(kpis)
        prompt = RISK_ANALYSIS_PROMPT.format(
            company_name=company_info.company_name if company_info else ticker,
            ticker=ticker, kpi_summary=kpi_summary,
            sic_description=company_info.sic_description if company_info else "Unknown",
            fiscal_year_end=company_info.fiscal_year_end if company_info else "Unknown",
        )
        llm = ChatOpenAI(model="gpt-4o", temperature=0)
        structured_llm = llm.with_structured_output(RiskAssessment)
        try:
            risk = await structured_llm.ainvoke(prompt)
        except Exception as e:
            logger.error(f"LLM risk analysis failed: {e}")
            risk = _placeholder_risk(kpis)
            errors.append(f"LLM risk analysis failed, using placeholder: {e}")

    # Write gold table
    rows = []
    for dim in risk.dimensions:
        rows.append({
            "dimension": dim.dimension, "score": dim.score,
            "reasoning": dim.reasoning, "key_metrics": "; ".join(dim.key_metrics),
        })
    rows.append({
        "dimension": "COMPOSITE", "score": round(risk.composite_score, 2),
        "reasoning": f"Risk Level: {risk.risk_level}", "key_metrics": "",
    })
    for flag in risk.red_flags:
        rows.append({
            "dimension": f"RED_FLAG: {flag.flag}", "score": flag.severity,
            "reasoning": flag.evidence, "key_metrics": "",
        })

    writer = CsvWriter(ticker)
    path = writer.write_gold(
        "risk_assessment", rows, source_tables="silver_financial_kpis.csv"
    )

    return {
        "gold_risk_scores": risk,
        "gold_risk_path": str(path),
        "errors": errors,
        "current_stage": "gold",
        "progress_messages": [f"Risk analysis: {risk.risk_level} ({risk.composite_score:.1f}/5.0)"],
    }
