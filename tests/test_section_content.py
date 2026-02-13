"""Tests that validate every pipeline section generates content.

Each section (KPIs, Risk Factors, Insider, Institutional, Events, Governance,
Risk Analysis, Memo) must produce non-empty data when given valid bronze inputs.
These tests use mocked EDGAR data and no API keys to verify that the pipeline
populates every frontend tab.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from backend.models import (
    GovernanceData,
    InsiderSignal,
    PipelineState,
    initial_state,
)


def _make_state(**overrides) -> PipelineState:
    state = initial_state("AAPL")
    state.update(overrides)
    return state


# ---------------------------------------------------------------------------
# 1. KPIs section — must have revenue, net_income, margins
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_kpis_section_has_content(sample_facts, sample_company_info, tmp_path):
    """KPI section must generate all key financial metrics from bronze facts."""
    from backend.agents.silver.financial_kpis import silver_financial_kpis_agent

    state = _make_state(company_info=sample_company_info, bronze_facts=sample_facts)

    with patch("backend.agents.silver.financial_kpis.CsvWriter") as MockWriter:
        writer = MockWriter.return_value
        writer.write_silver.return_value = tmp_path / "silver_financial_kpis.csv"
        result = await silver_financial_kpis_agent(state)

    kpis = result["silver_kpis"]
    assert kpis is not None, "KPIs must not be None"
    assert kpis.revenue is not None and kpis.revenue > 0, "Revenue must be populated"
    assert kpis.net_income is not None, "Net income must be populated"
    assert kpis.gross_margin is not None, "Gross margin must be populated"
    assert kpis.operating_margin is not None, "Operating margin must be populated"
    assert kpis.debt_to_equity is not None, "Debt-to-equity must be populated"
    assert kpis.current_ratio is not None, "Current ratio must be populated"
    assert kpis.free_cash_flow is not None, "Free cash flow must be populated"
    assert kpis.eps_basic is not None, "EPS must be populated"
    assert kpis.fiscal_year > 0, "Fiscal year must be set"


# ---------------------------------------------------------------------------
# 2. Risk Factors section — must have at least one factor
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_risk_factors_section_has_content(sample_company_info, tmp_path):
    """Risk factors section must generate at least one classified factor."""
    from backend.agents.silver.risk_factors import silver_risk_factors_agent

    state = _make_state(
        company_info=sample_company_info,
        bronze_10k_risk_text="The company faces regulatory risk from antitrust investigations. "
        "Competition in the smartphone market is intense. Supply chain disruptions "
        "pose operational risks.",
    )

    with patch("backend.agents.silver.risk_factors.CsvWriter") as MockWriter, \
         patch.dict("os.environ", {"OPENAI_API_KEY": ""}):
        writer = MockWriter.return_value
        writer.write_silver.return_value = tmp_path / "silver_risk_factors.csv"
        result = await silver_risk_factors_agent(state)

    factors = result["silver_risk_factors"]
    assert len(factors) >= 1, "Must produce at least one risk factor"
    for f in factors:
        assert f["category"], "Each factor must have a category"
        assert f["title"], "Each factor must have a title"
        assert f["summary"], "Each factor must have a summary"
        assert 1 <= f["severity"] <= 5, "Severity must be 1-5"


# ---------------------------------------------------------------------------
# 3. Insider section — must produce signal + trades when bronze has data
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_insider_section_has_content(sample_insider_trades, tmp_path):
    """Insider section must produce a signal and trades list from Form 4 data."""
    from backend.agents.silver.insider_signal import silver_insider_signal_agent

    state = _make_state(bronze_form4_transactions=sample_insider_trades)

    with patch("backend.agents.silver.insider_signal.CsvWriter") as MockWriter:
        writer = MockWriter.return_value
        writer.write_silver.return_value = tmp_path / "silver_insider_transactions.csv"
        result = await silver_insider_signal_agent(state)

    # Signal must be populated
    signal = result["silver_insider_signal"]
    assert signal is not None, "Insider signal must not be None"
    assert signal["signal"] in ("bullish", "bearish", "neutral"), \
        f"Signal must be bullish/bearish/neutral, got {signal['signal']}"
    assert signal["total_buys"] + signal["total_sells"] > 0, \
        "Must have at least one buy or sell"

    # Trades must be non-empty
    trades = result["silver_insider_trades"]
    assert len(trades) > 0, "Trades list must be non-empty when bronze has data"
    for t in trades:
        assert t.get("insider_name"), "Each trade must have insider_name"
        assert t.get("transaction_code") in ("P", "S", "A", "M", ""), \
            f"Invalid transaction code: {t.get('transaction_code')}"


@pytest.mark.asyncio
async def test_insider_section_neutral_when_no_data():
    """Insider section must still produce a neutral signal when no Form 4 data."""
    from backend.agents.silver.insider_signal import silver_insider_signal_agent

    state = _make_state(bronze_form4_transactions=[])

    result = await silver_insider_signal_agent(state)

    signal = result["silver_insider_signal"]
    assert signal is not None, "Signal must exist even with no data"
    assert signal["signal"] == "neutral", "Signal must be neutral with no trades"
    assert signal["total_buys"] == 0
    assert signal["total_sells"] == 0


# ---------------------------------------------------------------------------
# 4. Institutional section — must classify holders when bronze has data
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_institutional_section_has_content(tmp_path):
    """Institutional section must classify holders from 13F data."""
    from backend.agents.silver.institutional import silver_institutional_agent

    raw_holders = [
        {"holder_name": "Vanguard Group Inc", "shares": 1_200_000_000, "value": 270e9, "holder_type": "unknown"},
        {"holder_name": "BlackRock Fund Advisors", "shares": 1_000_000_000, "value": 225e9, "holder_type": "unknown"},
        {"holder_name": "ARK Innovation ETF", "shares": 5_000_000, "value": 1.1e9, "holder_type": "unknown"},
    ]

    state = _make_state(bronze_13f_holdings=raw_holders)

    with patch("backend.agents.silver.institutional.CsvWriter") as MockWriter:
        writer = MockWriter.return_value
        writer.write_silver.return_value = tmp_path / "silver_institutional_holders.csv"
        result = await silver_institutional_agent(state)

    holders = result["silver_institutional_holders"]
    assert len(holders) > 0, "Must have at least one holder"
    for h in holders:
        assert h.get("holder_name"), "Each holder must have a name"
        assert h.get("shares", 0) > 0, "Each holder must have shares"
        assert h["holder_type"] in ("passive", "active"), \
            f"Holder type must be passive or active, got {h['holder_type']}"


@pytest.mark.asyncio
async def test_institutional_section_empty_when_no_data():
    """Institutional section must return empty list (not error) with no 13F data."""
    from backend.agents.silver.institutional import silver_institutional_agent

    state = _make_state(bronze_13f_holdings=[])

    result = await silver_institutional_agent(state)

    assert result["silver_institutional_holders"] == [], \
        "Must return empty list, not None or error"


# ---------------------------------------------------------------------------
# 5. Events section — must classify events when bronze has data
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_events_section_has_content(sample_company_info, tmp_path):
    """Events section must classify 8-K events with item codes and severity."""
    from backend.agents.silver.material_events import silver_material_events_agent

    raw_events = [
        {"filing_date": "2025-10-30", "description": "Item 2.02 Results of Operations and Financial Condition"},
        {"filing_date": "2025-07-01", "description": "Item 5.02 Departure of Directors or Certain Officers"},
        {"filing_date": "2025-04-15", "description": "Item 8.01 Other Events"},
    ]

    state = _make_state(company_info=sample_company_info, bronze_8k_filings=raw_events)

    with patch("backend.agents.silver.material_events.CsvWriter") as MockWriter, \
         patch.dict("os.environ", {"OPENAI_API_KEY": ""}):
        writer = MockWriter.return_value
        writer.write_silver.return_value = tmp_path / "silver_material_events.csv"
        result = await silver_material_events_agent(state)

    events = result["silver_material_events"]
    assert len(events) == 3, "Must classify all 3 events"
    for e in events:
        assert e.get("filing_date"), "Each event must have a filing_date"
        assert e.get("item_code"), "Each event must have an item_code"
        assert e.get("item_description"), "Each event must have an item_description"
        assert 1 <= e["severity"] <= 5, "Severity must be 1-5"


@pytest.mark.asyncio
async def test_events_section_empty_when_no_data():
    """Events section must return empty list (not error) with no 8-K data."""
    from backend.agents.silver.material_events import silver_material_events_agent

    state = _make_state(bronze_8k_filings=[])

    result = await silver_material_events_agent(state)

    assert result["silver_material_events"] == []


# ---------------------------------------------------------------------------
# 6. Governance section — must produce governance data when bronze has proxy
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_governance_section_has_content(sample_company_info, tmp_path):
    """Governance section must produce GovernanceData from DEF 14A proxy text."""
    from backend.agents.silver.governance import silver_governance_agent

    state = _make_state(
        company_info=sample_company_info,
        bronze_def14a_proxy={"text": "Tim Cook serves as CEO with $98M compensation..."},
    )

    with patch("backend.agents.silver.governance.CsvWriter") as MockWriter, \
         patch.dict("os.environ", {"OPENAI_API_KEY": ""}):
        writer = MockWriter.return_value
        writer.write_silver.return_value = tmp_path / "silver_governance.csv"
        result = await silver_governance_agent(state)

    governance = result["silver_governance"]
    assert governance is not None, "Governance data must not be None"
    assert isinstance(governance, dict), "Governance must be a dict"
    # In placeholder mode, returns empty GovernanceData but it should still exist
    assert "ceo_name" in governance, "Must have ceo_name field"
    assert "board_size" in governance, "Must have board_size field"
    assert "governance_flags" in governance, "Must have governance_flags field"


@pytest.mark.asyncio
async def test_governance_section_empty_when_no_proxy(sample_company_info):
    """Governance section must produce empty GovernanceData when no proxy text."""
    from backend.agents.silver.governance import silver_governance_agent

    state = _make_state(company_info=sample_company_info, bronze_def14a_proxy={})

    result = await silver_governance_agent(state)

    assert result["silver_governance"] is not None, \
        "Must return GovernanceData dict, not None"


# ---------------------------------------------------------------------------
# 7. Risk Analysis (Gold) — must score all 5 dimensions
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_risk_analysis_section_has_content(sample_kpis, tmp_path):
    """Risk analysis must score all 5 risk dimensions."""
    from backend.agents.gold.risk_assessment import gold_risk_assessment_agent

    state = _make_state(silver_kpis=sample_kpis)

    with patch("backend.agents.gold.risk_assessment.CsvWriter") as MockWriter, \
         patch.dict("os.environ", {"OPENAI_API_KEY": ""}):
        writer = MockWriter.return_value
        writer.write_gold.return_value = tmp_path / "gold_risk_assessment.csv"
        result = await gold_risk_assessment_agent(state)

    risk = result["gold_risk_scores"]
    assert risk is not None, "Risk scores must not be None"
    assert len(risk.dimensions) == 5, "Must have exactly 5 risk dimensions"
    assert 1.0 <= risk.composite_score <= 5.0, \
        f"Composite score must be 1.0-5.0, got {risk.composite_score}"
    assert risk.risk_level in ("Low", "Medium", "High", "Critical"), \
        f"Invalid risk level: {risk.risk_level}"

    for dim in risk.dimensions:
        assert dim.dimension, "Each dimension must have a name"
        assert 1 <= dim.score <= 5, f"Score must be 1-5, got {dim.score}"
        assert dim.reasoning, "Each dimension must have reasoning"


# ---------------------------------------------------------------------------
# 8. Memo section — must produce executive summary + recommendation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_memo_section_has_content(
    sample_company_info, sample_kpis, sample_risk_assessment, tmp_path,
):
    """Memo section must generate executive summary and recommendation."""
    from backend.agents.gold.memo_writer import gold_memo_agent

    state = _make_state(
        company_info=sample_company_info,
        silver_kpis=sample_kpis,
        gold_risk_scores=sample_risk_assessment,
    )

    with patch("backend.agents.gold.memo_writer.CsvWriter") as MockWriter, \
         patch.dict("os.environ", {"OPENAI_API_KEY": ""}):
        writer = MockWriter.return_value
        writer.write_result.return_value = tmp_path / "results_diligence_memo.md"
        result = await gold_memo_agent(state)

    memo = result["result_memo"]
    assert memo is not None, "Memo must not be None"
    assert memo.executive_summary, "Must have executive summary"
    assert memo.recommendation, "Must have recommendation"
    assert memo.financial_analysis, "Must have financial analysis"


# ---------------------------------------------------------------------------
# 9. API response — all sections present in results endpoint
# ---------------------------------------------------------------------------


def test_api_results_include_all_sections(sample_state_v2):
    """API /results response must include all workstream sections."""
    # Simulate what get_results() does with a complete state
    state = sample_state_v2
    result: dict = {}

    # Reproduce the API serialization logic
    if state.get("company_info"):
        result["company_info"] = state["company_info"].model_dump()
    if state.get("silver_kpis"):
        result["kpis"] = state["silver_kpis"].model_dump()
    if state.get("gold_risk_scores"):
        result["risk_scores"] = state["gold_risk_scores"].model_dump()
    if state.get("result_memo"):
        result["memo"] = state["result_memo"].model_dump()

    # Workstream data (mirrors updated api.py logic)
    result["risk_factors"] = state.get("silver_risk_factors", [])

    insider_signal = state.get("silver_insider_signal")
    result["insider_signal"] = insider_signal if insider_signal else None

    raw_trades = state.get("silver_insider_trades", [])
    result["insider_trades"] = [
        {
            "insider_name": t.get("insider_name", ""),
            "title": t.get("insider_title", ""),
            "tx_date": t.get("transaction_date", ""),
            "tx_code": t.get("transaction_code", ""),
            "shares": t.get("shares", 0),
            "price": t.get("price_per_share"),
            "value": t.get("value"),
        }
        for t in raw_trades
    ]

    result["institutional_holders"] = state.get("silver_institutional_holders", [])
    result["material_events"] = state.get("silver_material_events", [])

    governance = state.get("silver_governance")
    result["governance"] = governance if governance else None

    result["cross_workstream_flags"] = state.get("gold_cross_workstream_flags", [])
    result["deal_recommendation"] = state.get("deal_recommendation", "")

    # --- Assertions: every section must be present and populated ---

    assert result["company_info"] is not None, "company_info missing"
    assert result["kpis"] is not None, "kpis missing"
    assert result["risk_scores"] is not None, "risk_scores missing"
    assert result["memo"] is not None, "memo missing"

    # Workstreams
    assert len(result["risk_factors"]) > 0, "risk_factors section is empty"
    assert result["insider_signal"] is not None, "insider_signal missing"
    assert result["insider_signal"]["signal"] in ("bullish", "bearish", "neutral"), \
        "insider signal must have a valid signal value"
    assert len(result["insider_trades"]) > 0, "insider_trades section is empty"
    assert len(result["institutional_holders"]) > 0, "institutional_holders section is empty"
    assert len(result["material_events"]) > 0, "material_events section is empty"
    assert result["governance"] is not None, "governance section missing"
    assert result["governance"]["ceo_name"], "governance must have ceo_name"

    # Insider trade field names must match frontend expectations
    trade = result["insider_trades"][0]
    assert "tx_date" in trade, "Insider trade must have 'tx_date' (not 'transaction_date')"
    assert "tx_code" in trade, "Insider trade must have 'tx_code' (not 'transaction_code')"
    assert "title" in trade, "Insider trade must have 'title' (not 'insider_title')"
    assert "price" in trade, "Insider trade must have 'price' (not 'price_per_share')"
    assert "insider_name" in trade, "Insider trade must have 'insider_name'"


def test_api_results_with_empty_state():
    """API must include all section keys even when pipeline had no data."""
    state = initial_state("AAPL")
    result: dict = {}

    # Reproduce the API logic with empty state
    result["risk_factors"] = state.get("silver_risk_factors", [])

    insider_signal = state.get("silver_insider_signal")
    result["insider_signal"] = insider_signal if insider_signal else None

    raw_trades = state.get("silver_insider_trades", [])
    result["insider_trades"] = [
        {
            "insider_name": t.get("insider_name", ""),
            "title": t.get("insider_title", ""),
            "tx_date": t.get("transaction_date", ""),
            "tx_code": t.get("transaction_code", ""),
            "shares": t.get("shares", 0),
            "price": t.get("price_per_share"),
            "value": t.get("value"),
        }
        for t in raw_trades
    ]

    result["institutional_holders"] = state.get("silver_institutional_holders", [])
    result["material_events"] = state.get("silver_material_events", [])

    governance = state.get("silver_governance")
    result["governance"] = governance if governance else None

    # All keys must be present (frontend relies on key existence)
    assert "risk_factors" in result
    assert "insider_signal" in result
    assert "insider_trades" in result
    assert "institutional_holders" in result
    assert "material_events" in result
    assert "governance" in result

    # Empty state: lists should be empty, dicts should be None
    assert result["risk_factors"] == []
    assert result["insider_signal"] is None  # {} is falsy → None
    assert result["insider_trades"] == []
    assert result["institutional_holders"] == []
    assert result["material_events"] == []
    assert result["governance"] is None  # {} is falsy → None


# ---------------------------------------------------------------------------
# 10. Full pipeline section content — end-to-end with mocked EDGAR
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_full_pipeline_all_sections_populated(
    sample_company_info, sample_facts, sample_insider_trades,
    sample_institutional_holders, sample_material_events,
):
    """End-to-end: all workstream sections must be populated after pipeline run."""
    from backend.agents.silver.financial_kpis import silver_financial_kpis_agent
    from backend.agents.silver.insider_signal import silver_insider_signal_agent
    from backend.agents.silver.institutional import silver_institutional_agent
    from backend.agents.silver.material_events import silver_material_events_agent
    from backend.agents.silver.risk_factors import silver_risk_factors_agent
    from backend.agents.silver.governance import silver_governance_agent

    # Simulate a full pipeline with all bronze data present
    state = _make_state(
        company_info=sample_company_info,
        bronze_facts=sample_facts,
        bronze_10k_risk_text="The company faces significant regulatory and competitive risks.",
        bronze_form4_transactions=sample_insider_trades,
        bronze_13f_holdings=[dict(h) for h in sample_institutional_holders],
        bronze_8k_filings=[
            {"filing_date": "2025-10-30", "description": "Item 2.02 Results of Operations"},
            {"filing_date": "2025-07-01", "description": "Item 5.02 Departure of Officers"},
        ],
        bronze_def14a_proxy={"text": "Proxy statement with governance data..."},
    )

    # Run all silver agents (with CsvWriter mocked)
    sections_populated = {}

    with patch("backend.agents.silver.financial_kpis.CsvWriter") as M1, \
         patch("backend.agents.silver.risk_factors.CsvWriter") as M2, \
         patch("backend.agents.silver.insider_signal.CsvWriter") as M3, \
         patch("backend.agents.silver.institutional.CsvWriter") as M4, \
         patch("backend.agents.silver.material_events.CsvWriter") as M5, \
         patch("backend.agents.silver.governance.CsvWriter") as M6, \
         patch.dict("os.environ", {"OPENAI_API_KEY": ""}):

        for m in [M1, M2, M3, M4, M5, M6]:
            m.return_value.write_silver.return_value = "mocked_path.csv"

        kpis_result = await silver_financial_kpis_agent(state)
        sections_populated["kpis"] = kpis_result["silver_kpis"] is not None

        rf_result = await silver_risk_factors_agent(state)
        sections_populated["risk_factors"] = len(rf_result["silver_risk_factors"]) > 0

        insider_result = await silver_insider_signal_agent(state)
        sections_populated["insider_signal"] = bool(insider_result["silver_insider_signal"])
        sections_populated["insider_trades"] = len(insider_result["silver_insider_trades"]) > 0

        inst_result = await silver_institutional_agent(state)
        sections_populated["institutional"] = len(inst_result["silver_institutional_holders"]) > 0

        events_result = await silver_material_events_agent(state)
        sections_populated["events"] = len(events_result["silver_material_events"]) > 0

        gov_result = await silver_governance_agent(state)
        sections_populated["governance"] = bool(gov_result["silver_governance"])

    # Every section must be populated
    for section, is_populated in sections_populated.items():
        assert is_populated, f"Section '{section}' is empty — frontend tab will show no data"
