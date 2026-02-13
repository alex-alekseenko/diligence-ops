"""Gold: Memo Writer — generates 10-section DD report from all pipeline data."""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from pathlib import Path

from jinja2 import Environment, FileSystemLoader
from langchain_openai import ChatOpenAI

from backend.agents.silver.financial_kpis import _format_kpis_for_prompt
from backend.data.csv_writer import CsvWriter
from backend.models import DiligenceMemo, PipelineState

logger = logging.getLogger(__name__)

MEMO_V2_PROMPT = """\
You are a senior M&A analyst writing a comprehensive due diligence report for \
{company_name} ({ticker}).

## Financial KPIs (from SEC 10-K XBRL data)
{kpi_summary}

## Risk Assessment
Composite Risk: {risk_level} ({composite_score}/5.0)
{risk_details}

## Red Flags (Financial)
{red_flags}

## Risk Factors (10-K Item 1A)
{risk_factors_summary}

## Insider Trading Signal
{insider_summary}

## Institutional Ownership
{institutional_summary}

## Material Events (8-K)
{events_summary}

## Governance & Compensation
{governance_summary}

## Cross-Workstream Red Flags
{cross_flags_summary}

## Deal Recommendation: {deal_recommendation}

## Instructions
Write a professional 10-section due diligence report:

1. **Executive Summary** (4-6 sentences): Key takeaway, deal recommendation \
({deal_recommendation}), top 3 risks, confidence level
2. **Company Overview** (3-5 sentences): Business description, market position
3. **Financial Analysis** (paragraph): Revenue, profitability, balance sheet. \
Cite [source: TagName, FY{fiscal_year}].
4. **Risk Factor Analysis** (paragraph): Summarize key risks by category
5. **Insider Trading Signals** (paragraph): Buy/sell patterns, cluster activity
6. **Institutional Ownership** (paragraph): Major holders, notable changes
7. **Material Events** (paragraph): Significant 8-K filings
8. **Governance & Compensation** (paragraph): CEO pay, board independence
9. **Cross-Workstream Red Flags** (paragraph): Correlated signals
10. **Recommendation & Caveats** (paragraph): Final verdict with conditions

Be specific — cite actual numbers. Do not be generic.
"""


async def gold_memo_agent(state: PipelineState) -> dict:
    """Generate the final DD report from all pipeline data.

    Reads: ALL silver and gold state fields
    Writes: results_diligence_memo.md
    """
    ticker = state["ticker"]
    kpis = state.get("silver_kpis")
    risk_scores = state.get("gold_risk_scores")
    company_info = state.get("company_info")
    errors: list[str] = []

    if not kpis or not risk_scores:
        errors.append("Missing KPIs or risk scores for report generation")
        return {
            "result_memo": None,
            "result_memo_path": None,
            "confidence": 0.0,
            "errors": errors,
            "current_stage": "error",
            "progress_messages": ["Gold memo: insufficient data for report"],
        }

    company_name = company_info.company_name if company_info else ticker
    cross_flags = state.get("gold_cross_workstream_flags", [])
    deal_rec = state.get("deal_recommendation", "PROCEED")

    # Build summaries
    kpi_summary = _format_kpis_for_prompt(kpis)
    risk_details = "\n".join(
        f"- {d.dimension}: {d.score}/5 — {d.reasoning}" for d in risk_scores.dimensions
    )
    red_flags_text = "\n".join(
        f"- {f.flag} ({f.severity}): {f.evidence}" for f in risk_scores.red_flags
    ) or "No critical red flags identified."

    risk_factors = state.get("silver_risk_factors", [])
    risk_factors_summary = "\n".join(
        f"- [{rf.get('category', 'unknown')}] {rf.get('title', '')}: "
        f"{rf.get('summary', '')} (severity {rf.get('severity', '?')}/5"
        f"{', NOVEL' if rf.get('is_novel') else ''})"
        for rf in risk_factors[:10]
    ) or "No risk factor data available."

    insider = state.get("silver_insider_signal", {})
    insider_summary = (
        f"Signal: {insider.get('signal', 'N/A')}, "
        f"Buys: {insider.get('total_buys', 0)}, "
        f"Sells: {insider.get('total_sells', 0)}, "
        f"Buy/Sell Ratio: {insider.get('buy_sell_ratio', 'N/A')}"
    )
    if insider.get("cluster_detected"):
        insider_summary += f"\nCLUSTER: {insider.get('cluster_description', '')}"

    holders = state.get("silver_institutional_holders", [])
    institutional_summary = "\n".join(
        f"- {h.get('holder_name', 'Unknown')}: "
        f"{h.get('shares', 0):,.0f} shares ({h.get('holder_type', 'unknown')})"
        for h in holders[:5]
    ) or "No institutional data available."

    events = state.get("silver_material_events", [])
    events_summary = "\n".join(
        f"- {e.get('filing_date', '')}: {e.get('item_code', '')} "
        f"{e.get('item_description', '')} (severity {e.get('severity', '?')}/5)"
        for e in events[:10]
    ) or "No material events in the past 12 months."

    governance = state.get("silver_governance", {})
    gov_parts = []
    if governance.get("ceo_name"):
        gov_parts.append(f"CEO: {governance['ceo_name']}")
    if governance.get("ceo_total_comp"):
        gov_parts.append(f"CEO Comp: ${governance['ceo_total_comp']:,.0f}")
    if governance.get("board_independence_pct") is not None:
        gov_parts.append(f"Board Independence: {governance['board_independence_pct']:.0%}")
    if governance.get("governance_flags"):
        gov_parts.append(f"Flags: {', '.join(governance['governance_flags'][:3])}")
    governance_summary = "\n".join(gov_parts) or "No governance data available."

    cross_flags_summary = "\n".join(
        f"- [{cf.get('severity', '')}] {cf.get('rule_name', '')}: {cf.get('description', '')}"
        for cf in cross_flags
    ) or "No cross-workstream red flags identified."

    # Generate memo
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        logger.warning("OPENAI_API_KEY not set, generating placeholder memo")
        memo = _placeholder_memo(company_name, ticker, kpis, risk_scores, deal_rec)
    else:
        prompt = MEMO_V2_PROMPT.format(
            company_name=company_name, ticker=ticker, kpi_summary=kpi_summary,
            risk_level=risk_scores.risk_level, composite_score=risk_scores.composite_score,
            risk_details=risk_details, red_flags=red_flags_text,
            risk_factors_summary=risk_factors_summary, insider_summary=insider_summary,
            institutional_summary=institutional_summary, events_summary=events_summary,
            governance_summary=governance_summary, cross_flags_summary=cross_flags_summary,
            deal_recommendation=deal_rec, fiscal_year=kpis.fiscal_year,
        )
        llm = ChatOpenAI(model="gpt-4o", temperature=0.3)
        structured_llm = llm.with_structured_output(DiligenceMemo)
        try:
            memo = await structured_llm.ainvoke(prompt)
        except Exception as e:
            logger.error(f"LLM memo generation failed: {e}")
            memo = _placeholder_memo(company_name, ticker, kpis, risk_scores, deal_rec)
            errors.append(f"LLM memo generation failed: {e}")

    memo.generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    confidence = _calculate_confidence(state, memo)

    # Render memo content
    memo_content = _plain_memo(
        company_name, ticker, memo, confidence, risk_scores,
        deal_rec, cross_flags, state,
    )

    writer = CsvWriter(ticker)
    memo_path = writer.write_result("diligence_memo", memo_content)

    return {
        "result_memo": memo,
        "result_memo_path": str(memo_path),
        "confidence": confidence,
        "errors": errors,
        "current_stage": "complete",
        "progress_messages": [f"DD report generated: {deal_rec} (confidence: {confidence:.0%})"],
    }


def _calculate_confidence(state: PipelineState, memo) -> float:
    """Score pipeline confidence based on data completeness."""
    score = 0.0
    total = 0.0
    kpis = state.get("silver_kpis")
    risk_scores = state.get("gold_risk_scores")

    # KPI completeness (25%)
    if kpis:
        kpi_fields = [
            kpis.revenue, kpis.net_income, kpis.gross_margin, kpis.operating_margin,
            kpis.total_assets, kpis.total_liabilities, kpis.stockholders_equity,
            kpis.debt_to_equity, kpis.cash_and_equivalents, kpis.operating_cash_flow,
        ]
        score += 0.25 * (sum(1 for f in kpi_fields if f is not None) / len(kpi_fields))
    total += 0.25

    # Risk assessment (15%)
    if risk_scores and risk_scores.dimensions:
        score += 0.15 * (len(risk_scores.dimensions) / 5)
    total += 0.15

    # Memo (15%)
    if memo:
        memo_fields = [memo.executive_summary, memo.company_overview, memo.financial_analysis,
                       memo.risk_assessment, memo.recommendation]
        score += 0.15 * (sum(1 for f in memo_fields if f) / len(memo_fields))
    total += 0.15

    # Workstream completeness (45%)
    workstream_checks = [
        bool(state.get("silver_risk_factors")),
        bool(state.get("silver_insider_trades")),
        bool(state.get("silver_institutional_holders")),
        bool(state.get("silver_material_events")),
        bool(state.get("silver_governance", {}).get("ceo_name")),
    ]
    score += 0.45 * (sum(workstream_checks) / len(workstream_checks))
    total += 0.45

    return round(score / total, 2) if total > 0 else 0.0


def _placeholder_memo(company_name, ticker, kpis, risk_scores, deal_rec):
    """Generate a basic memo when LLM is unavailable."""
    revenue_str = f"${kpis.revenue:,.0f}" if kpis.revenue else "N/A"
    ni_str = f"${kpis.net_income:,.0f}" if kpis.net_income else "N/A"
    return DiligenceMemo(
        executive_summary=(
            f"{company_name} ({ticker}) has been analyzed based on their "
            f"FY{kpis.fiscal_year} 10-K filing and 5 additional workstreams. "
            f"Revenue: {revenue_str}, Net income: {ni_str}. "
            f"Overall risk: {risk_scores.risk_level} ({risk_scores.composite_score}/5.0). "
            f"Deal recommendation: {deal_rec}."
        ),
        company_overview=(
            f"{company_name} is a publicly traded company (ticker: {ticker}). "
            "This analysis is based on SEC EDGAR filings."
        ),
        financial_analysis=(
            f"Revenue: {revenue_str}. Net income: {ni_str}. "
            + (f"Gross margin: {kpis.gross_margin:.1%}. " if kpis.gross_margin else "")
            + (f"Operating margin: {kpis.operating_margin:.1%}. " if kpis.operating_margin else "")
            + "Full financial analysis requires LLM processing."
        ),
        risk_assessment=(
            f"Composite risk score: {risk_scores.composite_score}/5.0 ({risk_scores.risk_level}). "
            "Detailed risk reasoning requires LLM processing."
        ),
        key_findings=[
            f"Revenue: {revenue_str} (FY{kpis.fiscal_year})",
            f"Risk level: {risk_scores.risk_level}",
            f"Deal recommendation: {deal_rec}",
        ],
        recommendation=(
            f"Deal recommendation: {deal_rec}. "
            "Set OPENAI_API_KEY for complete report generation."
        ),
    )


def _plain_memo(company_name, ticker, memo, confidence, risk_scores, deal_rec, cross_flags, state):
    """Generate a plain markdown DD report."""
    lines = [
        f"# Due Diligence Report: {company_name} ({ticker})",
        f"\n**Generated:** {memo.generated_at}",
        f"**Confidence:** {confidence:.2f}",
        f"**Risk Level:** {risk_scores.risk_level if risk_scores else 'N/A'}",
        f"**Deal Recommendation:** {deal_rec}",
        "\n---\n",
        "## 1. Executive Summary\n", memo.executive_summary,
        "\n## 2. Company Overview\n", memo.company_overview,
        "\n## 3. Financial Analysis\n", memo.financial_analysis,
        "\n## 4. Risk Factor Analysis\n", memo.risk_assessment,
        "\n## 5. Insider Trading Signals\n",
    ]
    insider = state.get("silver_insider_signal", {})
    if insider:
        lines.append(f"Signal: {insider.get('signal', 'N/A')}, Buys: {insider.get('total_buys', 0)}, Sells: {insider.get('total_sells', 0)}")
    else:
        lines.append("No insider trading data available.")

    lines.append("\n## 6. Institutional Ownership\n")
    for h in state.get("silver_institutional_holders", [])[:5]:
        lines.append(f"- {h.get('holder_name', 'Unknown')}: {h.get('shares', 0):,.0f} shares ({h.get('holder_type', 'unknown')})")

    lines.append("\n## 7. Material Events\n")
    for e in state.get("silver_material_events", [])[:5]:
        lines.append(f"- {e.get('filing_date', '')}: {e.get('item_code', '')} {e.get('item_description', '')} (severity {e.get('severity', '?')}/5)")

    lines.append("\n## 8. Governance & Compensation\n")
    gov = state.get("silver_governance", {})
    lines.append(f"CEO: {gov.get('ceo_name', 'N/A')}" if gov.get("ceo_name") else "No governance data available.")

    lines.append("\n## 9. Cross-Workstream Red Flags\n")
    if cross_flags:
        for cf in cross_flags:
            lines.append(f"- **[{cf.get('severity', '')}] {cf.get('rule_name', '')}**: {cf.get('description', '')}")
    else:
        lines.append("No cross-workstream red flags identified.")

    lines.extend([
        "\n## 10. Recommendation & Caveats\n",
        f"**Deal Recommendation: {deal_rec}**\n", memo.recommendation,
        "\n## Key Findings\n",
    ])
    for finding in memo.key_findings:
        lines.append(f"- {finding}")
    lines.append(f"\n---\n*Generated by DiligenceOps v0.3 | Confidence: {confidence:.2f} | {memo.generated_at}*")
    return "\n".join(lines)
