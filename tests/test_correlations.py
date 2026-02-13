"""Unit tests for cross-workstream correlation rules and deal recommendation."""

from __future__ import annotations

import pytest

from backend.agents.gold.cross_workstream import _compute_deal_recommendation, _evaluate_correlations
from backend.models import (
    FinancialKPIs,
    RiskAssessment,
    RiskDimension,
    initial_state,
)


def _make_state(**overrides):
    """Create a pipeline state with overrides."""
    state = initial_state("TEST")
    state.update(overrides)
    return state


# ---------------------------------------------------------------------------
# Correlation Rules
# ---------------------------------------------------------------------------


def test_rule1_critical_insider_revenue_auditor(sample_kpis):
    """Rule 1: Insider cluster sell + Revenue decline >10% + Auditor change -> Critical."""
    kpis = sample_kpis.model_copy()
    kpis.revenue_yoy_change = -0.15  # 15% revenue decline

    state = _make_state(
        silver_kpis=kpis,
        silver_insider_signal={
            "cluster_detected": True,
            "signal": "bearish",
            "cluster_description": "3 insiders selling",
        },
        silver_material_events=[
            {"item_code": "4.01", "filing_date": "2025-01-15"},  # Auditor change
        ],
    )
    flags = _evaluate_correlations(state)
    assert len(flags) >= 1
    critical_flags = [f for f in flags if f["severity"] == "Critical"]
    assert len(critical_flags) >= 1
    assert "Insider+Revenue+Auditor" in critical_flags[0]["rule_name"]


def test_rule2_critical_non_reliance(sample_kpis):
    """Rule 2: 8-K Item 4.02 non-reliance -> Critical."""
    state = _make_state(
        silver_kpis=sample_kpis,
        silver_material_events=[
            {"item_code": "4.02", "filing_date": "2025-03-01"},
        ],
    )
    flags = _evaluate_correlations(state)
    critical_flags = [f for f in flags if f["severity"] == "Critical"]
    assert len(critical_flags) >= 1
    assert any("Non-Reliance" in f["rule_name"] for f in critical_flags)


def test_rule3_high_pay_performance_mismatch(sample_kpis):
    """Rule 3: CEO pay growth >3x revenue growth + Low board independence -> High."""
    state = _make_state(
        silver_kpis=sample_kpis,  # revenue_yoy_change = 0.0642
        silver_governance={
            "ceo_pay_growth": 0.50,  # 50% pay growth vs 6.4% revenue -> >3x
            "board_independence_pct": 0.50,  # <67% -> low
        },
    )
    flags = _evaluate_correlations(state)
    high_flags = [f for f in flags if f["severity"] == "High"]
    assert len(high_flags) >= 1
    assert any("Pay-Performance" in f["rule_name"] for f in high_flags)


def test_rule3_not_triggered_high_board_independence(sample_kpis):
    """Rule 3 not triggered when board independence is >= 67%."""
    state = _make_state(
        silver_kpis=sample_kpis,
        silver_governance={
            "ceo_pay_growth": 0.50,
            "board_independence_pct": 0.80,  # Above threshold
        },
    )
    flags = _evaluate_correlations(state)
    pay_flags = [f for f in flags if "Pay-Performance" in f.get("rule_name", "")]
    assert len(pay_flags) == 0


def test_rule4_high_novel_regulatory_insider_selling():
    """Rule 4: Novel regulatory risk + Insider selling -> High."""
    state = _make_state(
        silver_risk_factors=[
            {"category": "regulatory", "is_novel": True, "title": "DOJ investigation"},
        ],
        silver_insider_signal={"signal": "bearish"},
    )
    flags = _evaluate_correlations(state)
    high_flags = [f for f in flags if f["severity"] == "High"]
    assert len(high_flags) >= 1
    assert any("Novel Regulatory" in f["rule_name"] for f in high_flags)


def test_rule5_medium_institutional_exodus(sample_kpis):
    """Rule 5: 2+ institutional holders reducing >20% + margins -> Medium."""
    state = _make_state(
        silver_kpis=sample_kpis,
        silver_institutional_holders=[
            {"holder_name": "Fund A", "change_pct": -0.25},
            {"holder_name": "Fund B", "change_pct": -0.30},
        ],
    )
    flags = _evaluate_correlations(state)
    medium_flags = [f for f in flags if f["severity"] == "Medium"]
    assert len(medium_flags) >= 1
    assert any("Institutional Exodus" in f["rule_name"] for f in medium_flags)


def test_rule6_medium_leadership_instability():
    """Rule 6: 2+ leadership changes + governance flags -> Medium."""
    state = _make_state(
        silver_material_events=[
            {"item_code": "5.02", "filing_date": "2025-01-01"},
            {"item_code": "5.02", "filing_date": "2025-06-01"},
        ],
        silver_governance={"governance_flags": ["combined CEO/Chair"]},
    )
    flags = _evaluate_correlations(state)
    medium_flags = [f for f in flags if f["severity"] == "Medium"]
    assert len(medium_flags) >= 1
    assert any("Leadership Instability" in f["rule_name"] for f in medium_flags)


def test_no_false_positives_with_clean_data(sample_kpis):
    """No flags when data is clean / no concerning patterns."""
    state = _make_state(
        silver_kpis=sample_kpis,
        silver_insider_signal={"signal": "neutral", "cluster_detected": False},
        silver_material_events=[],
        silver_governance={"ceo_pay_growth": 0.05, "board_independence_pct": 0.85},
        silver_risk_factors=[],
        silver_institutional_holders=[],
    )
    flags = _evaluate_correlations(state)
    assert len(flags) == 0


def test_no_flags_with_empty_state():
    """No flags when all workstream data is empty/default."""
    state = initial_state("TEST")
    flags = _evaluate_correlations(state)
    assert len(flags) == 0


# ---------------------------------------------------------------------------
# Deal Recommendation
# ---------------------------------------------------------------------------


def test_deal_recommendation_proceed(sample_kpis):
    """PROCEED when no flags and composite < 3.5."""
    risk = RiskAssessment(
        dimensions=[RiskDimension(dimension="Test", score=2, reasoning="OK", key_metrics=[])],
        composite_score=2.0,
        risk_level="Low",
    )
    state = _make_state(gold_risk_scores=risk, silver_kpis=sample_kpis)
    rec = _compute_deal_recommendation(state, [])
    assert rec == "PROCEED"


def test_deal_recommendation_proceed_with_conditions():
    """PROCEED_WITH_CONDITIONS when high flags present."""
    risk = RiskAssessment(
        dimensions=[RiskDimension(dimension="Test", score=3, reasoning="OK", key_metrics=[])],
        composite_score=3.0,
        risk_level="Medium",
    )
    state = _make_state(gold_risk_scores=risk)
    high_flags = [{"severity": "High", "rule_name": "Test"}]
    rec = _compute_deal_recommendation(state, high_flags)
    assert rec == "PROCEED_WITH_CONDITIONS"


def test_deal_recommendation_do_not_proceed_critical_flag():
    """DO_NOT_PROCEED when critical flag present."""
    risk = RiskAssessment(
        dimensions=[RiskDimension(dimension="Test", score=3, reasoning="OK", key_metrics=[])],
        composite_score=3.0,
        risk_level="Medium",
    )
    state = _make_state(gold_risk_scores=risk)
    critical_flags = [{"severity": "Critical", "rule_name": "Test Critical"}]
    rec = _compute_deal_recommendation(state, critical_flags)
    assert rec == "DO_NOT_PROCEED"


def test_deal_recommendation_do_not_proceed_high_composite():
    """DO_NOT_PROCEED when composite score >= 4.5."""
    risk = RiskAssessment(
        dimensions=[
            RiskDimension(dimension="D1", score=5, reasoning="Bad", key_metrics=[]),
            RiskDimension(dimension="D2", score=5, reasoning="Bad", key_metrics=[]),
            RiskDimension(dimension="D3", score=4, reasoning="Bad", key_metrics=[]),
        ],
        composite_score=4.7,
        risk_level="Critical",
    )
    state = _make_state(gold_risk_scores=risk)
    rec = _compute_deal_recommendation(state, [])
    assert rec == "DO_NOT_PROCEED"


def test_deal_recommendation_proceed_with_conditions_high_composite():
    """PROCEED_WITH_CONDITIONS when composite score >= 3.5 but < 4.5."""
    risk = RiskAssessment(
        dimensions=[RiskDimension(dimension="Test", score=4, reasoning="High", key_metrics=[])],
        composite_score=3.8,
        risk_level="High",
    )
    state = _make_state(gold_risk_scores=risk)
    rec = _compute_deal_recommendation(state, [])
    assert rec == "PROCEED_WITH_CONDITIONS"
