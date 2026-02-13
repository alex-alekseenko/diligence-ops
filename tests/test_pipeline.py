"""End-to-end pipeline test with fully mocked external dependencies.

Validates the 16-node medallion architecture pipeline (7 bronze + 6 silver + 3 gold)
executes correctly with mocked EdgarClient, EdgarFilingsClient, and no LLM API key.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.models import (
    CompanyInfo,
    DiligenceMemo,
    FinancialFact,
    PipelineProgress,
    RiskAssessment,
    RiskDimension,
)
from tests.conftest import MOCK_COMPANY_FACTS, MOCK_COMPANY_TICKERS, MOCK_SUBMISSIONS


def _build_mock_facts() -> list[FinancialFact]:
    """Build FinancialFact list from the conftest mock data."""
    facts = []
    for taxonomy in ("us-gaap", "dei"):
        for tag_name, tag_data in MOCK_COMPANY_FACTS["facts"].get(taxonomy, {}).items():
            for unit_type, entries in tag_data.get("units", {}).items():
                for entry in entries:
                    if entry.get("form") != "10-K":
                        continue
                    facts.append(FinancialFact(
                        tag=tag_name, label=tag_data["label"], value=float(entry["val"]),
                        unit=unit_type, start=entry.get("start"), end=entry["end"],
                        fy=entry["fy"], fp=entry.get("fp", "FY"), form=entry["form"],
                        filed=entry["filed"], accession=entry["accn"],
                        frame=entry.get("frame"), taxonomy=taxonomy,
                    ))
    return facts


def _mock_company_info() -> CompanyInfo:
    """Create mock CompanyInfo."""
    return CompanyInfo(
        ticker="AAPL", company_name="Apple Inc.", cik="0000320193",
        sic="3571", sic_description="Electronic Computers",
        fiscal_year_end="0928", exchanges=["Nasdaq"],
    )


# Modules that use CsvWriter (all 16 agents)
_CSV_WRITER_MODULES = [
    "backend.agents.bronze.resolver",
    "backend.agents.bronze.xbrl_facts",
    "backend.agents.bronze.ten_k",
    "backend.agents.bronze.form4",
    "backend.agents.bronze.thirteen_f",
    "backend.agents.bronze.eight_k",
    "backend.agents.bronze.def14a",
    "backend.agents.silver.financial_kpis",
    "backend.agents.silver.risk_factors",
    "backend.agents.silver.insider_signal",
    "backend.agents.silver.institutional",
    "backend.agents.silver.material_events",
    "backend.agents.silver.governance",
    "backend.agents.gold.risk_assessment",
    "backend.agents.gold.cross_workstream",
    "backend.agents.gold.memo_writer",
]


def _create_csv_writer_patches(tmp_path):
    """Create patch context managers for CsvWriter in all agent modules."""
    from backend.data.csv_writer import CsvWriter as RealWriter

    patches = []
    for module in _CSV_WRITER_MODULES:
        p = patch(
            f"{module}.CsvWriter",
            lambda ticker, _mod=module, **kw: RealWriter(ticker, output_dir=str(tmp_path)),
        )
        patches.append(p)
    return patches


@pytest.mark.asyncio
async def test_full_pipeline_with_mocked_data(tmp_path):
    """Run the full 16-agent v0.3 pipeline with all external deps mocked."""
    from backend.graph import run_pipeline

    mock_company_info = _mock_company_info()
    mock_facts = _build_mock_facts()

    progress_messages: list[PipelineProgress] = []

    def capture_progress(p: PipelineProgress):
        progress_messages.append(p)

    # Create all CsvWriter patches
    csv_patches = _create_csv_writer_patches(tmp_path)

    # Mock EdgarClient used by bronze resolver + bronze xbrl
    edgar_mock = MagicMock()
    edgar_mock.resolve_cik = AsyncMock(return_value="0000320193")
    edgar_mock.get_company_info = AsyncMock(return_value=mock_company_info)
    edgar_mock.get_company_facts = AsyncMock(return_value=mock_facts)

    # Mock EdgarFilingsClient used by bronze 10k, form4, 13f, 8k, def14a
    filings_mock = MagicMock()
    filings_mock.get_10k_risk_factors = AsyncMock(
        return_value="The Company faces significant competitive and regulatory risk..."
    )
    filings_mock.get_form4_filings = AsyncMock(return_value=[
        {"insider_name": "Tim Cook", "insider_title": "CEO",
         "transaction_date": "2025-08-15", "transaction_code": "S",
         "shares": 50000, "price_per_share": 225.0, "value": 11250000,
         "filing_date": "2025-08-17"},
    ])
    filings_mock.get_institutional_holders = AsyncMock(return_value=[
        {"holder_name": "Vanguard Group Inc", "shares": 1_200_000_000,
         "value": 270e9, "holder_type": "unknown"},
    ])
    filings_mock.get_8k_filings = AsyncMock(return_value=[
        {"filing_date": "2025-10-30", "form": "8-K",
         "description": "2.02 Results of Operations and Financial Condition",
         "accession": "0000320193-25-000110"},
    ])
    filings_mock.get_def14a = AsyncMock(return_value={
        "filing_date": "2025-02-15",
        "text": "DEF 14A Proxy Statement for Apple Inc. CEO Tim Cook...",
    })

    # Start all patches
    started_patches = [p.start() for p in csv_patches]

    try:
        with patch("backend.agents.bronze.resolver.EdgarClient", return_value=edgar_mock), \
             patch("backend.agents.bronze.xbrl_facts.EdgarClient", return_value=edgar_mock), \
             patch("backend.agents.bronze.ten_k.EdgarFilingsClient", return_value=filings_mock), \
             patch("backend.agents.bronze.form4.EdgarFilingsClient", return_value=filings_mock), \
             patch("backend.agents.bronze.thirteen_f.EdgarFilingsClient", return_value=filings_mock), \
             patch("backend.agents.bronze.eight_k.EdgarFilingsClient", return_value=filings_mock), \
             patch("backend.agents.bronze.def14a.EdgarFilingsClient", return_value=filings_mock), \
             patch.dict("os.environ", {"OPENAI_API_KEY": ""}):

            result = await run_pipeline(
                "AAPL",
                progress_callback=capture_progress,
                run_id="test-001",
            )
    finally:
        for p in csv_patches:
            p.stop()

    # Pipeline completed all stages
    assert result["current_stage"] == "complete"

    # Company info populated
    assert result["company_info"] is not None
    assert result["company_info"].ticker == "AAPL"

    # Bronze: facts fetched
    assert len(result["bronze_facts"]) > 0

    # Bronze: workstream data populated
    assert result["bronze_10k_risk_text"] != ""
    assert len(result["bronze_form4_transactions"]) > 0
    assert len(result["bronze_13f_holdings"]) > 0
    assert len(result["bronze_8k_filings"]) > 0
    assert result["bronze_def14a_proxy"].get("text") is not None

    # Silver: KPIs extracted
    kpis = result["silver_kpis"]
    assert kpis is not None
    assert kpis.revenue > 0
    assert kpis.net_income > 0
    assert kpis.gross_margin is not None

    # Silver: workstream outputs populated
    assert result.get("silver_risk_factors") is not None
    assert result.get("silver_insider_signal") is not None
    assert result.get("silver_institutional_holders") is not None
    assert result.get("silver_material_events") is not None
    assert result.get("silver_governance") is not None

    # Gold: risk scored (placeholder mode)
    risk = result["gold_risk_scores"]
    assert risk is not None
    assert len(risk.dimensions) == 5
    assert risk.risk_level in ("Low", "Medium", "High", "Critical")

    # Gold: cross-workstream flags evaluated
    assert isinstance(result.get("gold_cross_workstream_flags", []), list)

    # Deal recommendation generated
    assert result.get("deal_recommendation") in (
        "PROCEED", "PROCEED_WITH_CONDITIONS", "DO_NOT_PROCEED",
    )

    # Memo generated (placeholder mode)
    memo = result["result_memo"]
    assert memo is not None
    assert memo.executive_summary
    assert memo.recommendation

    # Confidence calculated
    assert result["confidence"] > 0

    # Progress callbacks fired for each stage
    assert len(progress_messages) >= 16  # one per agent node
    stages = [p.stage for p in progress_messages]
    assert "bronze" in stages
    assert "silver" in stages
    assert "gold" in stages
    assert "complete" in stages


@pytest.mark.asyncio
async def test_pipeline_error_propagation(tmp_path):
    """Pipeline handles EDGAR failure gracefully — resolver uses offline fallback."""
    from backend.data.edgar_client import EdgarClientError
    from backend.graph import run_pipeline

    # EdgarClient fails for resolver
    edgar_mock = MagicMock()
    edgar_mock.resolve_cik = AsyncMock(
        side_effect=EdgarClientError("SEC EDGAR unavailable")
    )
    # XBRL agent also gets an edgar client that fails
    edgar_mock.get_company_facts = AsyncMock(
        side_effect=EdgarClientError("SEC EDGAR unavailable")
    )

    # EdgarFilingsClient fails for all workstream agents
    filings_mock = MagicMock()
    from backend.data.edgar_filings import EdgarFilingsError
    filings_mock.get_10k_risk_factors = AsyncMock(side_effect=EdgarFilingsError("Unavailable"))
    filings_mock.get_form4_filings = AsyncMock(side_effect=EdgarFilingsError("Unavailable"))
    filings_mock.get_institutional_holders = AsyncMock(side_effect=EdgarFilingsError("Unavailable"))
    filings_mock.get_8k_filings = AsyncMock(side_effect=EdgarFilingsError("Unavailable"))
    filings_mock.get_def14a = AsyncMock(side_effect=EdgarFilingsError("Unavailable"))

    csv_patches = _create_csv_writer_patches(tmp_path)
    started = [p.start() for p in csv_patches]

    try:
        with patch("backend.agents.bronze.resolver.EdgarClient", return_value=edgar_mock), \
             patch("backend.agents.bronze.xbrl_facts.EdgarClient", return_value=edgar_mock), \
             patch("backend.agents.bronze.ten_k.EdgarFilingsClient", return_value=filings_mock), \
             patch("backend.agents.bronze.form4.EdgarFilingsClient", return_value=filings_mock), \
             patch("backend.agents.bronze.thirteen_f.EdgarFilingsClient", return_value=filings_mock), \
             patch("backend.agents.bronze.eight_k.EdgarFilingsClient", return_value=filings_mock), \
             patch("backend.agents.bronze.def14a.EdgarFilingsClient", return_value=filings_mock), \
             patch.dict("os.environ", {"OPENAI_API_KEY": ""}):

            result = await run_pipeline("AAPL", run_id="test-err")
    finally:
        for p in csv_patches:
            p.stop()

    # Pipeline should have errors but not crash
    assert len(result.get("errors", [])) > 0
    assert any("EDGAR" in e or "error" in e.lower() or "resolver" in e.lower()
               for e in result.get("errors", []))
