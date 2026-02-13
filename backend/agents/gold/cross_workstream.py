"""Gold: Cross-Workstream — evaluates 6 correlation rules across all silver outputs."""

from __future__ import annotations

import logging

from backend.data.csv_writer import CsvWriter
from backend.models import CrossWorkstreamFlag, PipelineState

logger = logging.getLogger(__name__)


def _evaluate_correlations(state: PipelineState) -> list[dict]:
    """Apply 6 cross-workstream correlation rules."""
    flags: list[dict] = []
    kpis = state.get("silver_kpis")
    insider = state.get("silver_insider_signal", {})
    events = state.get("silver_material_events", [])
    governance = state.get("silver_governance", {})
    holders = state.get("silver_institutional_holders", [])
    risk_factors = state.get("silver_risk_factors", [])

    # Rule 1 — Critical: Insider cluster selling + Revenue decline >10% + Auditor change
    has_cluster_sell = insider.get("cluster_detected") and insider.get("signal") == "bearish"
    has_revenue_decline = kpis and kpis.revenue_yoy_change is not None and kpis.revenue_yoy_change < -0.10
    has_auditor_change = any(e.get("item_code") == "4.01" for e in events)
    if has_cluster_sell and has_revenue_decline and has_auditor_change:
        flags.append(CrossWorkstreamFlag(
            rule_name="Insider+Revenue+Auditor", severity="Critical",
            description="Insider cluster selling coincides with >10% revenue decline and an auditor change.",
            workstreams_involved=["insider_signal", "financial_kpis", "material_events"],
            evidence=[
                f"Revenue decline: {kpis.revenue_yoy_change:.1%}",
                f"Insider: {insider.get('cluster_description', '')}",
                "8-K Item 4.01: auditor change filed",
            ],
        ).model_dump())

    # Rule 2 — Critical: 8-K Item 4.02 (non-reliance on financials)
    if any(e.get("item_code") == "4.02" for e in events) and kpis:
        flags.append(CrossWorkstreamFlag(
            rule_name="Non-Reliance on Financials", severity="Critical",
            description="Company issued non-reliance statement. All financial analysis may be unreliable.",
            workstreams_involved=["material_events", "financial_kpis"],
            evidence=["8-K Item 4.02 filed", "All financial metrics should be treated with caution"],
        ).model_dump())

    # Rule 3 — High: CEO pay growth significantly outpaces revenue growth + Low board independence
    # Fires when: (a) CEO pay is rising while revenue is flat/declining, OR
    #              (b) CEO pay growth > 3x positive revenue growth
    # Both cases with board independence below 67%.
    ceo_pay_growth = governance.get("ceo_pay_growth")
    rev_growth = kpis.revenue_yoy_change if kpis else None
    board_indep = governance.get("board_independence_pct")
    if (ceo_pay_growth is not None and ceo_pay_growth > 0
            and rev_growth is not None
            and board_indep is not None and board_indep < 0.67):
        pay_mismatch = (
            rev_growth <= 0  # Revenue flat/declining while CEO pay rises
            or ceo_pay_growth > 3 * rev_growth  # CEO pay growth dwarfs revenue growth
        )
        if pay_mismatch:
            flags.append(CrossWorkstreamFlag(
                rule_name="Pay-Performance Mismatch + Weak Board", severity="High",
                description="CEO pay growth significantly exceeds revenue growth with low board independence.",
                workstreams_involved=["governance", "financial_kpis"],
                evidence=[
                    f"CEO pay growth: {ceo_pay_growth:.1%}",
                    f"Revenue growth: {rev_growth:.1%}",
                    f"Board independence: {board_indep:.0%}",
                ],
            ).model_dump())

    # Rule 4 — High: Novel regulatory risk + Insider selling
    novel_regulatory = any(rf.get("is_novel") and rf.get("category") == "regulatory" for rf in risk_factors)
    if novel_regulatory and insider.get("signal") == "bearish":
        flags.append(CrossWorkstreamFlag(
            rule_name="Novel Regulatory Risk + Insider Selling", severity="High",
            description="New regulatory risk factor identified alongside insider selling activity.",
            workstreams_involved=["risk_factors", "insider_signal"],
            evidence=["Novel regulatory risk factor in 10-K", f"Insider signal: {insider.get('signal')}"],
        ).model_dump())

    # Rule 5 — Medium: Institutional holders reducing >20% + Declining margins
    large_reductions = [h for h in holders if h.get("change_pct") is not None and h["change_pct"] < -0.20]
    if len(large_reductions) >= 2 and kpis and kpis.gross_margin is not None:
        flags.append(CrossWorkstreamFlag(
            rule_name="Institutional Exodus + Margin Pressure", severity="Medium",
            description="Multiple institutional holders reducing positions alongside margin concerns.",
            workstreams_involved=["institutional", "financial_kpis"],
            evidence=[f"{len(large_reductions)} holders reduced >20% QoQ", f"Gross margin: {kpis.gross_margin:.1%}"],
        ).model_dump())

    # Rule 6 — Medium: Multiple leadership changes + Governance flags
    leadership_changes = [e for e in events if e.get("item_code") == "5.02"]
    gov_flags = governance.get("governance_flags", [])
    if len(leadership_changes) >= 2 and len(gov_flags) >= 1:
        flags.append(CrossWorkstreamFlag(
            rule_name="Leadership Instability + Governance Concerns", severity="Medium",
            description="Multiple executive changes alongside governance red flags signal instability.",
            workstreams_involved=["material_events", "governance"],
            evidence=[
                f"{len(leadership_changes)} leadership changes (8-K Item 5.02)",
                f"Governance flags: {', '.join(gov_flags[:3])}",
            ],
        ).model_dump())

    return flags


def _compute_deal_recommendation(state: PipelineState, cross_flags: list[dict]) -> str:
    """Compute deal recommendation based on all signals."""
    critical_flags = [f for f in cross_flags if f.get("severity") == "Critical"]
    high_flags = [f for f in cross_flags if f.get("severity") == "High"]
    risk_scores = state.get("gold_risk_scores")
    composite = risk_scores.composite_score if risk_scores else 2.5

    if critical_flags or composite >= 4.5:
        return "DO_NOT_PROCEED"
    elif high_flags or composite >= 3.5:
        return "PROCEED_WITH_CONDITIONS"
    else:
        return "PROCEED"


async def gold_cross_workstream_agent(state: PipelineState) -> dict:
    """Evaluate cross-workstream correlation rules.

    Reads: ALL silver tables + gold_risk_scores
    Writes: gold_cross_workstream_flags.csv
    """
    ticker = state["ticker"]
    errors: list[str] = []

    cross_flags = _evaluate_correlations(state)
    deal_rec = _compute_deal_recommendation(state, cross_flags)

    writer = CsvWriter(ticker)
    path = writer.write_gold(
        "cross_workstream_flags",
        cross_flags,
        source_tables="all silver tables + gold_risk_assessment.csv",
    )

    return {
        "gold_cross_workstream_flags": cross_flags,
        "gold_cross_workstream_path": str(path),
        "deal_recommendation": deal_rec,
        "errors": errors,
        "progress_messages": [
            f"Cross-workstream: {len(cross_flags)} flags, recommendation={deal_rec}"
        ],
    }
