"""Unit tests for medallion architecture agents with mocked external dependencies."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.models import (
    CompanyInfo,
    DiligenceMemo,
    FinancialKPIs,
    PipelineState,
    RiskAssessment,
    RiskDimension,
    initial_state,
)


# ---------------------------------------------------------------------------
# Bronze: Resolver
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bronze_resolver_agent(sample_company_info, tmp_path):
    from backend.agents.bronze.resolver import bronze_resolver_agent

    state = initial_state("AAPL")

    with patch("backend.agents.bronze.resolver.EdgarClient") as MockClient, \
         patch("backend.agents.bronze.resolver.CsvWriter") as MockWriter:

        client = MockClient.return_value
        client.resolve_cik = AsyncMock(return_value="0000320193")
        client.get_company_info = AsyncMock(return_value=sample_company_info)

        writer = MockWriter.return_value
        writer.write_bronze.return_value = tmp_path / "bronze_company_info.csv"

        result = await bronze_resolver_agent(state)

    assert result["company_info"].company_name == "Apple Inc."
    assert result["bronze_company_info_path"] is not None
    assert result["current_stage"] == "bronze"
    assert not [e for e in result.get("errors", []) if "ERROR" in e]


@pytest.mark.asyncio
async def test_bronze_resolver_offline_fallback():
    from backend.agents.bronze.resolver import bronze_resolver_agent
    from backend.data.edgar_client import EdgarClientError

    state = initial_state("AAPL")

    with patch("backend.agents.bronze.resolver.EdgarClient") as MockClient, \
         patch("backend.agents.bronze.resolver.CsvWriter") as MockWriter:

        client = MockClient.return_value
        client.resolve_cik = AsyncMock(side_effect=EdgarClientError("Network down"))

        writer = MockWriter.return_value
        writer.write_bronze.return_value = Path("pipeline_output/AAPL/bronze_company_info.csv")

        result = await bronze_resolver_agent(state)

    assert result["company_info"] is not None
    assert "(offline)" in result["company_info"].company_name
    assert any("resolver" in e.lower() for e in result.get("errors", []))


# ---------------------------------------------------------------------------
# Bronze: XBRL Facts
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bronze_xbrl_agent(sample_company_info, sample_facts, tmp_path):
    from backend.agents.bronze.xbrl_facts import bronze_xbrl_agent

    state = initial_state("AAPL")
    state["company_info"] = sample_company_info

    with patch("backend.agents.bronze.xbrl_facts.EdgarClient") as MockClient, \
         patch("backend.agents.bronze.xbrl_facts.CsvWriter") as MockWriter:

        client = MockClient.return_value
        client.get_company_facts = AsyncMock(return_value=sample_facts)

        writer = MockWriter.return_value
        writer.write_bronze.return_value = tmp_path / "bronze_xbrl_facts.csv"

        result = await bronze_xbrl_agent(state)

    assert len(result["bronze_facts"]) > 0
    assert result["bronze_xbrl_facts_path"] is not None


@pytest.mark.asyncio
async def test_bronze_xbrl_no_cik():
    from backend.agents.bronze.xbrl_facts import bronze_xbrl_agent

    # Use a ticker with no offline fallback file in examples/
    state = initial_state("ZZZZ")
    state["company_info"] = None

    result = await bronze_xbrl_agent(state)

    assert result["bronze_facts"] == []
    assert result["bronze_xbrl_facts_path"] is None


# ---------------------------------------------------------------------------
# Silver: Financial KPIs
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_silver_financial_kpis_agent(sample_facts, sample_company_info, tmp_path):
    from backend.agents.silver.financial_kpis import silver_financial_kpis_agent

    state = initial_state("AAPL")
    state["company_info"] = sample_company_info
    state["bronze_facts"] = sample_facts

    with patch("backend.agents.silver.financial_kpis.CsvWriter") as MockWriter, \
         patch.dict("os.environ", {"OPENAI_API_KEY": ""}):
        writer = MockWriter.return_value
        writer.write_silver.return_value = tmp_path / "silver_financial_kpis.csv"

        result = await silver_financial_kpis_agent(state)

    kpis = result["silver_kpis"]
    assert kpis is not None
    assert isinstance(kpis, FinancialKPIs)
    assert kpis.revenue == pytest.approx(416160000000, rel=0.01)
    assert kpis.net_income == pytest.approx(112005000000, rel=0.01)
    assert kpis.gross_margin is not None and kpis.gross_margin > 0
    assert kpis.operating_margin is not None and kpis.operating_margin > 0
    assert kpis.debt_to_equity is not None
    assert result["current_stage"] == "silver"


@pytest.mark.asyncio
async def test_silver_kpis_handles_empty_facts():
    from backend.agents.silver.financial_kpis import silver_financial_kpis_agent

    state = initial_state("AAPL")

    result = await silver_financial_kpis_agent(state)
    assert result["silver_kpis"] is None
    assert result["current_stage"] == "error"
    assert any("No bronze" in e for e in result["errors"])


def test_kpi_extraction_yoy(sample_facts):
    """Verify that YoY change is calculated from two periods."""
    from backend.agents.silver.financial_kpis import _extract_kpis

    kpis = _extract_kpis(sample_facts)
    assert kpis.revenue_yoy_change is not None
    # Revenue went from ~391B to ~416B, so YoY ~ 6.4%
    assert kpis.revenue_yoy_change == pytest.approx(0.0642, abs=0.01)


def test_kpi_extraction_derived_metrics(sample_facts):
    """Verify derived metrics: gross margin, D/E, current ratio, free cash flow."""
    from backend.agents.silver.financial_kpis import _extract_kpis

    kpis = _extract_kpis(sample_facts)

    # Gross margin = gross_profit / revenue
    assert kpis.gross_margin is not None
    assert 0.40 < kpis.gross_margin < 0.55

    # D/E = total_liabilities / stockholders_equity
    assert kpis.debt_to_equity is not None
    assert kpis.debt_to_equity > 3  # Apple has high leverage

    # Current ratio < 1 for Apple
    assert kpis.current_ratio is not None
    assert kpis.current_ratio < 1.0

    # Free cash flow = operating_cash_flow - capex
    assert kpis.free_cash_flow is not None
    assert kpis.free_cash_flow > 0


def test_annual_end_dates_excludes_dei(sample_facts):
    """Verify that DEI facts don't pollute annual end date detection."""
    from backend.agents.silver.financial_kpis import _get_annual_end_dates

    end_dates = _get_annual_end_dates(sample_facts)

    # DEI fact has end date 2025-10-17 — should NOT appear
    assert "2025-10-17" not in end_dates

    # Should have 2025-09-27 (latest) and 2024-09-28 (prior)
    assert "2025-09-27" in end_dates
    assert "2024-09-28" in end_dates


# ---------------------------------------------------------------------------
# Gold: Risk Assessment
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_gold_risk_assessment_placeholder_mode(sample_kpis, sample_company_info, tmp_path):
    """Risk assessment works in placeholder mode without API key."""
    from backend.agents.gold.risk_assessment import gold_risk_assessment_agent

    state = initial_state("AAPL")
    state["company_info"] = sample_company_info
    state["silver_kpis"] = sample_kpis

    with patch("backend.agents.gold.risk_assessment.CsvWriter") as MockWriter, \
         patch.dict("os.environ", {"OPENAI_API_KEY": ""}):
        writer = MockWriter.return_value
        writer.write_gold.return_value = tmp_path / "gold_risk_assessment.csv"

        result = await gold_risk_assessment_agent(state)

    risk = result["gold_risk_scores"]
    assert risk is not None
    assert isinstance(risk, RiskAssessment)
    assert len(risk.dimensions) == 5
    assert 1.0 <= risk.composite_score <= 5.0
    assert risk.risk_level in ("Low", "Medium", "High", "Critical")
    assert result["current_stage"] == "gold"


@pytest.mark.asyncio
async def test_gold_risk_assessment_handles_missing_kpis():
    """Risk assessment returns error state when no KPIs available."""
    from backend.agents.gold.risk_assessment import gold_risk_assessment_agent

    state = initial_state("AAPL")

    result = await gold_risk_assessment_agent(state)
    assert result["gold_risk_scores"] is None
    assert result["current_stage"] == "error"


def test_placeholder_risk_scoring(sample_kpis):
    """Test the rule-based placeholder risk scorer."""
    from backend.agents.gold.risk_assessment import _placeholder_risk

    risk = _placeholder_risk(sample_kpis)
    assert len(risk.dimensions) == 5

    dim_names = {d.dimension for d in risk.dimensions}
    assert "Financial Health" in dim_names
    assert "Liquidity" in dim_names

    # Apple has high D/E — governance should be elevated
    governance = next(d for d in risk.dimensions if d.dimension == "Governance")
    assert governance.score >= 3

    # Apple has current ratio < 1 — liquidity should be elevated
    liquidity = next(d for d in risk.dimensions if d.dimension == "Liquidity")
    assert liquidity.score >= 3


# ---------------------------------------------------------------------------
# Gold: Memo Writer
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_gold_memo_agent_placeholder_mode(
    sample_kpis, sample_company_info, sample_risk_assessment, tmp_path
):
    """Memo writer works in placeholder mode without API key."""
    from backend.agents.gold.memo_writer import gold_memo_agent

    state = initial_state("AAPL")
    state.update(
        company_info=sample_company_info,
        silver_kpis=sample_kpis,
        gold_risk_scores=sample_risk_assessment,
        deal_recommendation="PROCEED",
    )

    with patch("backend.agents.gold.memo_writer.CsvWriter") as MockWriter, \
         patch.dict("os.environ", {"OPENAI_API_KEY": ""}):
        writer = MockWriter.return_value
        writer.write_result.return_value = tmp_path / "results_diligence_memo.md"

        result = await gold_memo_agent(state)

    assert result["result_memo"] is not None
    assert isinstance(result["result_memo"], DiligenceMemo)
    assert result["result_memo"].executive_summary
    assert result["result_memo_path"] is not None
    assert result["confidence"] > 0
    assert result["current_stage"] == "complete"


@pytest.mark.asyncio
async def test_gold_memo_handles_missing_data():
    """Memo writer returns error state when KPIs or risk scores missing."""
    from backend.agents.gold.memo_writer import gold_memo_agent

    state = initial_state("AAPL")

    result = await gold_memo_agent(state)
    assert result["result_memo"] is None
    assert result["current_stage"] == "error"
    assert result["confidence"] == 0.0


def test_confidence_calculation(sample_state_v2):
    """Verify confidence scoring logic with full state."""
    from backend.agents.gold.memo_writer import _calculate_confidence, DiligenceMemo

    memo = sample_state_v2["result_memo"]
    score = _calculate_confidence(sample_state_v2, memo)
    # Fully populated state → should be high
    assert 0.7 <= score <= 1.0


def test_confidence_with_partial_data():
    """Confidence should be lower with missing data."""
    from backend.agents.gold.memo_writer import _calculate_confidence

    state = initial_state("AAPL")
    sparse_kpis = FinancialKPIs(revenue=100e9, fiscal_year=2024, period_end="2024-12-31")
    state["silver_kpis"] = sparse_kpis
    state["gold_risk_scores"] = RiskAssessment(
        dimensions=[RiskDimension(dimension="Test", score=3, reasoning="Test.", key_metrics=[])],
        composite_score=3.0, risk_level="Medium",
    )

    sparse_memo = DiligenceMemo(
        executive_summary="Test", company_overview="", financial_analysis="",
        risk_assessment="", key_findings=[], recommendation="",
    )

    score = _calculate_confidence(state, sparse_memo)
    assert score < 0.5  # Missing lots of data
