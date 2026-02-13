"""Unit tests for v0.3 workstream agents (bronze ingestion + silver transformation)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.models import (
    GovernanceData,
    InsiderSignal,
    PipelineState,
    initial_state,
)


def _make_state(**overrides) -> PipelineState:
    """Create a v0.3 pipeline state with optional overrides."""
    state = initial_state("AAPL")
    state.update(overrides)
    return state


# ---------------------------------------------------------------------------
# Bronze: 10-K + Silver: Risk Factors
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bronze_10k_agent(tmp_path):
    """Bronze 10-K agent fetches risk text."""
    from backend.agents.bronze.ten_k import bronze_10k_agent

    state = _make_state()

    with patch("backend.agents.bronze.ten_k.EdgarFilingsClient") as MockClient, \
         patch("backend.agents.bronze.ten_k.CsvWriter") as MockWriter:

        client = MockClient.return_value
        client.get_10k_risk_factors = AsyncMock(return_value="Company faces significant risks...")

        writer = MockWriter.return_value
        writer.write_bronze.return_value = tmp_path / "bronze_10k_risk_text.csv"

        result = await bronze_10k_agent(state)

    assert result["bronze_10k_risk_text"] == "Company faces significant risks..."
    assert result["bronze_10k_risk_text_path"] is not None


@pytest.mark.asyncio
async def test_silver_risk_factors_placeholder_mode(sample_company_info, tmp_path):
    """Silver risk factors produces placeholder factors without API key."""
    from backend.agents.silver.risk_factors import silver_risk_factors_agent

    state = _make_state(
        company_info=sample_company_info,
        bronze_10k_risk_text="Company faces significant risks in the market...",
    )

    with patch("backend.agents.silver.risk_factors.CsvWriter") as MockWriter, \
         patch.dict("os.environ", {"OPENAI_API_KEY": ""}):

        writer = MockWriter.return_value
        writer.write_silver.return_value = tmp_path / "silver_risk_factors.csv"

        result = await silver_risk_factors_agent(state)

    assert result["silver_risk_factors"] is not None
    assert len(result["silver_risk_factors"]) >= 1
    assert result["silver_risk_factors_path"] is not None


@pytest.mark.asyncio
async def test_silver_risk_factors_no_bronze_data(sample_company_info):
    """Silver risk factors returns empty when no bronze 10-K data available."""
    from backend.agents.silver.risk_factors import silver_risk_factors_agent

    state = _make_state(company_info=sample_company_info)

    result = await silver_risk_factors_agent(state)

    assert result["silver_risk_factors"] == []
    assert result["silver_risk_factors_path"] is None


# ---------------------------------------------------------------------------
# Bronze: Form 4 + Silver: Insider Signal
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bronze_form4_agent(sample_insider_trades, tmp_path):
    """Bronze Form 4 agent fetches transactions."""
    from backend.agents.bronze.form4 import bronze_form4_agent

    state = _make_state()

    with patch("backend.agents.bronze.form4.EdgarFilingsClient") as MockClient, \
         patch("backend.agents.bronze.form4.CsvWriter") as MockWriter:

        client = MockClient.return_value
        client.get_form4_filings = AsyncMock(return_value=sample_insider_trades)

        writer = MockWriter.return_value
        writer.write_bronze.return_value = tmp_path / "bronze_form4_transactions.csv"

        result = await bronze_form4_agent(state)

    assert len(result["bronze_form4_transactions"]) == 3
    assert result["bronze_form4_path"] is not None


@pytest.mark.asyncio
async def test_bronze_form4_edgar_error():
    """Bronze Form 4 handles EDGAR failure gracefully."""
    from backend.agents.bronze.form4 import bronze_form4_agent
    from backend.data.edgar_filings import EdgarFilingsError

    state = _make_state()

    with patch("backend.agents.bronze.form4.EdgarFilingsClient") as MockClient:
        client = MockClient.return_value
        client.get_form4_filings = AsyncMock(side_effect=EdgarFilingsError("timeout"))

        result = await bronze_form4_agent(state)

    assert result["bronze_form4_transactions"] == []
    assert any("form 4" in e.lower() for e in result["errors"])


@pytest.mark.asyncio
async def test_silver_insider_signal_with_trades(sample_insider_trades, tmp_path):
    """Silver insider signal computes signal from bronze Form 4 data."""
    from backend.agents.silver.insider_signal import silver_insider_signal_agent

    state = _make_state(bronze_form4_transactions=sample_insider_trades)

    with patch("backend.agents.silver.insider_signal.CsvWriter") as MockWriter:
        writer = MockWriter.return_value
        writer.write_silver.return_value = tmp_path / "silver_insider_transactions.csv"

        result = await silver_insider_signal_agent(state)

    signal = result["silver_insider_signal"]
    assert signal["total_buys"] == 1
    assert signal["total_sells"] == 2
    assert signal["net_shares"] < 0
    assert result["silver_insider_trades_path"] is not None


@pytest.mark.asyncio
async def test_silver_insider_signal_no_trades():
    """Silver insider signal returns neutral when no bronze Form 4 data."""
    from backend.agents.silver.insider_signal import silver_insider_signal_agent

    state = _make_state()

    result = await silver_insider_signal_agent(state)

    assert result["silver_insider_signal"]["signal"] == "neutral"
    assert result["silver_insider_trades"] == []


def test_cluster_detection_detects_sell_cluster():
    """Cluster detection finds 3+ insiders selling within 30 days."""
    from backend.agents.silver.insider_signal import _detect_clusters

    trades = [
        {"insider_name": "Alice", "transaction_code": "S", "transaction_date": "2025-08-01"},
        {"insider_name": "Bob", "transaction_code": "S", "transaction_date": "2025-08-05"},
        {"insider_name": "Charlie", "transaction_code": "S", "transaction_date": "2025-08-20"},
    ]
    detected, desc = _detect_clusters(trades)
    assert detected
    assert "sell" in desc.lower()
    assert "3 insiders" in desc


def test_cluster_detection_no_cluster_insufficient_insiders():
    """No cluster when fewer than 3 insiders."""
    from backend.agents.silver.insider_signal import _detect_clusters

    trades = [
        {"insider_name": "Alice", "transaction_code": "S", "transaction_date": "2025-08-01"},
        {"insider_name": "Bob", "transaction_code": "S", "transaction_date": "2025-08-05"},
    ]
    detected, desc = _detect_clusters(trades)
    assert not detected


def test_cluster_detection_no_cluster_too_spread_out():
    """No cluster when insiders trade too far apart."""
    from backend.agents.silver.insider_signal import _detect_clusters

    trades = [
        {"insider_name": "Alice", "transaction_code": "S", "transaction_date": "2025-01-01"},
        {"insider_name": "Bob", "transaction_code": "S", "transaction_date": "2025-04-01"},
        {"insider_name": "Charlie", "transaction_code": "S", "transaction_date": "2025-08-01"},
    ]
    detected, desc = _detect_clusters(trades)
    assert not detected


# ---------------------------------------------------------------------------
# Bronze: 13F + Silver: Institutional
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bronze_13f_agent(tmp_path):
    """Bronze 13F agent fetches holdings."""
    from backend.agents.bronze.thirteen_f import bronze_13f_agent

    raw_holders = [
        {"holder_name": "Vanguard Group Inc", "shares": 1_200_000_000, "value": 270e9},
    ]

    state = _make_state()

    with patch("backend.agents.bronze.thirteen_f.EdgarFilingsClient") as MockClient, \
         patch("backend.agents.bronze.thirteen_f.CsvWriter") as MockWriter:

        client = MockClient.return_value
        client.get_institutional_holders = AsyncMock(return_value=raw_holders)

        writer = MockWriter.return_value
        writer.write_bronze.return_value = tmp_path / "bronze_13f_holdings.csv"

        result = await bronze_13f_agent(state)

    assert len(result["bronze_13f_holdings"]) == 1
    assert result["bronze_13f_path"] is not None


@pytest.mark.asyncio
async def test_silver_institutional_with_holders(tmp_path):
    """Silver institutional classifies passive/active holders."""
    from backend.agents.silver.institutional import silver_institutional_agent

    raw_holders = [
        {"holder_name": "Vanguard Group Inc", "shares": 1_200_000_000, "value": 270e9},
        {"holder_name": "ARK Invest", "shares": 5_000_000, "value": 1.1e9},
        {"holder_name": "BlackRock Fund Advisors", "shares": 1_000_000_000, "value": 225e9},
    ]

    state = _make_state(bronze_13f_holdings=raw_holders)

    with patch("backend.agents.silver.institutional.CsvWriter") as MockWriter:
        writer = MockWriter.return_value
        writer.write_silver.return_value = tmp_path / "silver_institutional_holders.csv"

        result = await silver_institutional_agent(state)

    holders = result["silver_institutional_holders"]
    assert len(holders) == 3
    # Vanguard and BlackRock should be passive
    vanguard = next(h for h in holders if "Vanguard" in h["holder_name"])
    assert vanguard["holder_type"] == "passive"
    blackrock = next(h for h in holders if "BlackRock" in h["holder_name"])
    assert blackrock["holder_type"] == "passive"
    # ARK should be active
    ark = next(h for h in holders if "ARK" in h["holder_name"])
    assert ark["holder_type"] == "active"


@pytest.mark.asyncio
async def test_silver_institutional_no_holders():
    """Silver institutional handles no bronze 13F data."""
    from backend.agents.silver.institutional import silver_institutional_agent

    state = _make_state()

    result = await silver_institutional_agent(state)

    assert result["silver_institutional_holders"] == []
    assert result["silver_institutional_path"] is None


# ---------------------------------------------------------------------------
# Bronze: 8-K + Silver: Material Events
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bronze_8k_agent(tmp_path):
    """Bronze 8-K agent fetches filings."""
    from backend.agents.bronze.eight_k import bronze_8k_agent

    raw_events = [
        {"filing_date": "2025-10-30", "description": "Item 2.02 Results of Operations"},
    ]

    state = _make_state()

    with patch("backend.agents.bronze.eight_k.EdgarFilingsClient") as MockClient, \
         patch("backend.agents.bronze.eight_k.CsvWriter") as MockWriter:

        client = MockClient.return_value
        client.get_8k_filings = AsyncMock(return_value=raw_events)

        writer = MockWriter.return_value
        writer.write_bronze.return_value = tmp_path / "bronze_8k_filings.csv"

        result = await bronze_8k_agent(state)

    assert len(result["bronze_8k_filings"]) == 1
    assert result["bronze_8k_path"] is not None


@pytest.mark.asyncio
async def test_silver_material_events_rule_based(tmp_path):
    """Silver material events classifies events using rules (no API key)."""
    from backend.agents.silver.material_events import silver_material_events_agent

    raw_events = [
        {"filing_date": "2025-10-30", "description": "Item 2.02 Results of Operations"},
        {"filing_date": "2025-07-01", "description": "Item 5.02 Departure of CEO"},
    ]

    state = _make_state(bronze_8k_filings=raw_events)

    with patch("backend.agents.silver.material_events.CsvWriter") as MockWriter, \
         patch.dict("os.environ", {"OPENAI_API_KEY": ""}):

        writer = MockWriter.return_value
        writer.write_silver.return_value = tmp_path / "silver_material_events.csv"

        result = await silver_material_events_agent(state)

    events = result["silver_material_events"]
    assert len(events) == 2
    codes = [e["item_code"] for e in events]
    assert "2.02" in codes
    assert "5.02" in codes


@pytest.mark.asyncio
async def test_silver_material_events_no_data():
    """Silver material events handles no bronze 8-K data."""
    from backend.agents.silver.material_events import silver_material_events_agent

    state = _make_state()

    result = await silver_material_events_agent(state)

    assert result["silver_material_events"] == []
    assert result["silver_events_path"] is None


def test_rule_based_classify_matches_item_codes():
    """Rule-based classifier correctly identifies item codes from description text."""
    from backend.agents.silver.material_events import _rule_based_classify

    events = [
        {"filing_date": "2025-01-15", "description": "4.02 Non-Reliance on Financial Statements"},
        {"filing_date": "2025-02-10", "description": "Item 1.01 Material Agreement"},
        {"filing_date": "2025-03-05", "description": "Some generic 8-K filing"},
    ]
    classified = _rule_based_classify(events)
    assert len(classified) == 3
    assert classified[0]["item_code"] == "4.02"
    assert classified[0]["severity"] == 5
    assert classified[1]["item_code"] == "1.01"
    assert classified[2]["item_code"] == "8.01"  # Default fallback


# ---------------------------------------------------------------------------
# Bronze: DEF 14A + Silver: Governance
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bronze_def14a_agent(tmp_path):
    """Bronze DEF 14A agent fetches proxy text."""
    from backend.agents.bronze.def14a import bronze_def14a_agent

    state = _make_state()

    with patch("backend.agents.bronze.def14a.EdgarFilingsClient") as MockClient, \
         patch("backend.agents.bronze.def14a.CsvWriter") as MockWriter:

        client = MockClient.return_value
        client.get_def14a = AsyncMock(return_value={"text": "Proxy statement text...", "filing_date": "2025-04-01"})

        writer = MockWriter.return_value
        writer.write_bronze.return_value = tmp_path / "bronze_def14a_proxy.csv"

        result = await bronze_def14a_agent(state)

    assert result["bronze_def14a_proxy"]["text"] == "Proxy statement text..."
    assert result["bronze_def14a_path"] is not None


@pytest.mark.asyncio
async def test_bronze_def14a_edgar_error():
    """Bronze DEF 14A handles EDGAR failure gracefully."""
    from backend.agents.bronze.def14a import bronze_def14a_agent
    from backend.data.edgar_filings import EdgarFilingsError

    state = _make_state()

    with patch("backend.agents.bronze.def14a.EdgarFilingsClient") as MockClient:
        client = MockClient.return_value
        client.get_def14a = AsyncMock(side_effect=EdgarFilingsError("Network error"))

        result = await bronze_def14a_agent(state)

    assert result["bronze_def14a_proxy"] == {}
    assert result["bronze_def14a_path"] is None
    assert any("def 14a" in e.lower() for e in result["errors"])


@pytest.mark.asyncio
async def test_silver_governance_placeholder_mode(sample_company_info, tmp_path):
    """Silver governance produces placeholder when no API key."""
    from backend.agents.silver.governance import silver_governance_agent

    state = _make_state(
        company_info=sample_company_info,
        bronze_def14a_proxy={"text": "Proxy statement text..."},
    )

    with patch("backend.agents.silver.governance.CsvWriter") as MockWriter, \
         patch.dict("os.environ", {"OPENAI_API_KEY": ""}):

        writer = MockWriter.return_value
        writer.write_silver.return_value = tmp_path / "silver_governance.csv"

        result = await silver_governance_agent(state)

    assert result["silver_governance"] is not None
    assert result["silver_governance_path"] is not None


@pytest.mark.asyncio
async def test_silver_governance_no_proxy_data(sample_company_info):
    """Silver governance returns empty GovernanceData when no bronze DEF 14A text."""
    from backend.agents.silver.governance import silver_governance_agent

    state = _make_state(company_info=sample_company_info)

    result = await silver_governance_agent(state)

    # Should return empty governance data, not crash
    assert result["silver_governance"] is not None
    assert result["silver_governance_path"] is None


# ---------------------------------------------------------------------------
# Gold: Cross-Workstream
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_gold_cross_workstream_with_clean_data(sample_kpis, sample_risk_assessment, tmp_path):
    """Cross-workstream produces PROCEED with clean data."""
    from backend.agents.gold.cross_workstream import gold_cross_workstream_agent

    state = _make_state(
        silver_kpis=sample_kpis,
        gold_risk_scores=sample_risk_assessment,
    )

    with patch("backend.agents.gold.cross_workstream.CsvWriter") as MockWriter:
        writer = MockWriter.return_value
        writer.write_gold.return_value = tmp_path / "gold_cross_workstream_flags.csv"

        result = await gold_cross_workstream_agent(state)

    assert result["deal_recommendation"] == "PROCEED"
    assert isinstance(result["gold_cross_workstream_flags"], list)


@pytest.mark.asyncio
async def test_gold_cross_workstream_critical_flag(sample_kpis, tmp_path):
    """Cross-workstream detects critical flags and recommends DO_NOT_PROCEED."""
    from backend.agents.gold.cross_workstream import gold_cross_workstream_agent
    from backend.models import RiskAssessment, RiskDimension

    high_risk = RiskAssessment(
        dimensions=[
            RiskDimension(dimension="Test", score=5, reasoning="Critical.", key_metrics=[]),
        ],
        composite_score=5.0,
        risk_level="Critical",
    )

    state = _make_state(
        silver_kpis=sample_kpis,
        gold_risk_scores=high_risk,
    )

    with patch("backend.agents.gold.cross_workstream.CsvWriter") as MockWriter:
        writer = MockWriter.return_value
        writer.write_gold.return_value = tmp_path / "gold_cross_workstream_flags.csv"

        result = await gold_cross_workstream_agent(state)

    assert result["deal_recommendation"] == "DO_NOT_PROCEED"
