"""Transformation regression tests: bronze→silver→gold data quality.

Validates that each pipeline transformation produces non-garbage output
when given realistic input data.  Catches "garbage in, garbage out" issues
like the DEF 14A truncation bug (governance sections lost to naive truncation).

Run with: pytest tests/test_transformations.py -v
"""

from __future__ import annotations

import os

import pytest

from backend.data.edgar_filings import extract_proxy_sections
from backend.models import (
    FinancialKPIs,
    GovernanceData,
    InsiderSignal,
    MaterialEvent,
    RiskFactorItem,
    initial_state,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _build_proxy_text(sections: dict[str, str], padding: int = 60_000) -> str:
    """Build a realistic DEF 14A proxy text with governance sections buried deep.

    Simulates a real proxy where the first ~60K chars are shareholder letters,
    proposals, and ToC, and governance sections appear later in the document.
    Each section is followed by prose-like content (multiple sentences) so
    the extraction heuristic can distinguish body headings from ToC entries.
    """
    toc_entries = "\n".join(f"  {100 + i}      {heading}" for i, heading in enumerate(sections))
    preamble = "X" * padding + "\n" + toc_entries + "\n" + "Y" * 5000

    body_parts = []
    for heading, content in sections.items():
        body_parts.append(
            f"\n{heading}\n\n"
            f"This section provides important details. The Board determined the following. "
            f"These policies apply to all directors and officers.\n\n"
            f"{content}\n"
        )

    return preamble + "\n".join(body_parts) + "\n" + "Z" * 10_000


REALISTIC_PROXY_SECTIONS = {
    "Corporate Governance": (
        "The Company maintains high standards of corporate governance. "
        "The Board has adopted Corporate Governance Guidelines that address director "
        "qualifications, responsibilities, access to management, and compensation. "
        "The Board regularly reviews these guidelines."
    ),
    "Director Independence": (
        "The Board has determined that 7 of its 9 directors are independent under "
        "Nasdaq listing standards. Jane Smith (CEO) and John Doe (CTO) are not "
        "independent due to their management roles. Board independence: 77.8%."
    ),
    "Board Leadership Structure": (
        "The roles of Chair and CEO are separated. Alice Johnson serves as "
        "independent Chair. This separation allows the CEO to focus on operations "
        "while the Chair oversees Board effectiveness."
    ),
    "Board Role in Risk Oversight": (
        "The Board oversees risk through its committees. The Audit Committee monitors "
        "financial risks. The Compensation Committee evaluates compensation-related risks. "
        "Management reports quarterly on enterprise risk."
    ),
    "Board Meetings and Committees": (
        "During 2024, the Board held 10 meetings. All directors attended at least 75% "
        "of meetings. Standing committees include: Audit Committee (4 members), "
        "Compensation Committee (3 members), and Nominating Committee (3 members)."
    ),
    "Executive Officers": (
        "Jane Smith, Age 52, CEO since 2020. Previously CFO. "
        "Robert Brown, Age 45, CFO since 2023. Previously VP Finance. "
        "Maria Garcia, Age 48, General Counsel since 2021."
    ),
    "Compensation Discussion and Analysis": (
        "Our NEOs for 2024 were Jane Smith (CEO), Robert Brown (CFO), and "
        "Maria Garcia (GC). The Committee designed compensation to align with "
        "shareholder value creation. Base salaries are competitive with our "
        "peer group of 15 companies. CEO base salary was $1,200,000 for 2024. "
        "Annual bonus targets are set at 150% of base salary for the CEO. "
        "Long-term equity incentives vest over 4 years."
    ),
    "Summary Compensation Table": (
        "Jane Smith 2024 Salary $1,200,000 Bonus $0 Stock Awards $8,500,000 "
        "Option Awards $0 Non-Equity Incentive $1,800,000 All Other $145,000 "
        "Total $11,645,000. "
        "Jane Smith 2023 Salary $1,100,000 Total $9,800,000. "
        "Robert Brown 2024 Salary $600,000 Stock Awards $3,200,000 Total $4,950,000."
    ),
    "Pay Versus Performance": (
        "Year 2024 Summary Compensation $11,645,000 Compensation Actually Paid "
        "$14,200,000 TSR 34% Peer Group TSR 18% Net Income $2,400,000,000. "
        "Year 2023 Summary Compensation $9,800,000 TSR 12% Net Income $2,100,000,000."
    ),
    "Pay Ratio Disclosure": (
        "CEO annual total compensation: $11,645,000. "
        "Median employee annual total compensation: $78,500. "
        "CEO pay ratio: 148 to 1. The median employee was identified using "
        "W-2 wages for all US employees as of December 31, 2024."
    ),
    "Compensation of Directors": (
        "Non-employee directors receive $250,000 annual retainer in equity. "
        "The Chair receives an additional $100,000. Committee chairs receive "
        "$25,000. Alice Johnson total 2024: $375,000. Other directors: $275,000 each."
    ),
    "Equity Compensation Plan Information": (
        "2020 Equity Incentive Plan: 50,000,000 shares authorized, "
        "32,000,000 shares available for grant as of December 31, 2024. "
        "2015 Employee Stock Purchase Plan: 10,000,000 shares authorized."
    ),
}


# ===========================================================================
# 1. Section Extraction Tests
# ===========================================================================


class TestSectionExtraction:
    """Tests for extract_proxy_sections()."""

    def test_short_text_returned_as_is(self):
        """Text within budget should be returned unchanged."""
        text = "Short proxy text with some governance info."
        result, sections = extract_proxy_sections(text, budget=50_000)
        assert result == text
        assert sections == ["full_text"]

    def test_long_text_extracts_target_sections(self):
        """Sections buried deep in a long document should be found."""
        full_text = _build_proxy_text(REALISTIC_PROXY_SECTIONS)
        assert len(full_text) > 80_000, "Simulated proxy should exceed budget"

        result, sections = extract_proxy_sections(full_text, budget=50_000)

        assert len(sections) >= 5, f"Expected >=5 sections, got {len(sections)}: {sections}"
        assert len(result) <= 50_000, "Extracted text should respect budget"

    def test_key_governance_data_preserved(self):
        """Critical governance data should survive extraction."""
        full_text = _build_proxy_text(REALISTIC_PROXY_SECTIONS)
        result, _ = extract_proxy_sections(full_text, budget=50_000)

        # Key data points from each section
        assert "77.8%" in result or "7 of its 9" in result, "Director independence data missing"
        assert "148" in result or "78,500" in result, "Pay ratio data missing"
        assert "11,645,000" in result, "CEO compensation data missing"
        assert "10 meetings" in result, "Board meetings data missing"

    def test_toc_entries_skipped(self):
        """Table of Contents entries should not be confused with body sections."""
        # Build text where ToC mentions sections but body has the real content
        full_text = _build_proxy_text(REALISTIC_PROXY_SECTIONS)
        result, sections = extract_proxy_sections(full_text, budget=50_000)

        # The extracted text should contain prose, not ToC page numbers
        assert "This section provides" in result, "Body text not found — ToC may have been selected"

    def test_fallback_when_no_sections_found(self):
        """If no target headings exist, fall back to first N chars."""
        boring_text = "A" * 100_000
        result, sections = extract_proxy_sections(boring_text, budget=50_000)

        assert sections == ["truncated"]
        assert len(result) == 50_000

    def test_budget_scaling(self):
        """Larger budget should yield more extracted content."""
        full_text = _build_proxy_text(REALISTIC_PROXY_SECTIONS)

        small, sec_small = extract_proxy_sections(full_text, budget=10_000)
        large, sec_large = extract_proxy_sections(full_text, budget=50_000)

        assert len(large) > len(small), "Larger budget should extract more"
        # Both should find sections
        assert len(sec_small) >= 1
        assert len(sec_large) >= 1

    def test_sections_in_document_order(self):
        """Extracted sections should appear in their original document order."""
        full_text = _build_proxy_text(REALISTIC_PROXY_SECTIONS)
        result, sections = extract_proxy_sections(full_text, budget=50_000)

        # Director Independence should appear before Compensation Discussion
        if "Director Independence" in sections and "Compensation Discussion & Analysis" in sections:
            di_pos = result.find("Director Independence")
            cd_pos = result.find("Compensation Discussion and Analysis")
            if di_pos >= 0 and cd_pos >= 0:
                assert di_pos < cd_pos, "Sections should be in document order"


# ===========================================================================
# 2. Bronze → Silver Transformation Tests
# ===========================================================================


class TestBronzeToSilverXBRL:
    """XBRL facts → Financial KPIs."""

    @pytest.mark.asyncio
    async def test_kpis_extracted_from_facts(self, sample_facts, sample_company_info, monkeypatch, tmp_path):
        monkeypatch.setenv("OPENAI_API_KEY", "")
        monkeypatch.setenv("PIPELINE_OUTPUT_DIR", str(tmp_path))

        from backend.agents.silver.financial_kpis import silver_financial_kpis_agent

        state = initial_state("AAPL")
        state["bronze_facts"] = sample_facts
        state["company_info"] = sample_company_info

        result = await silver_financial_kpis_agent(state)
        kpis: FinancialKPIs = result["silver_kpis"]

        # Core metrics must be populated (not garbage out)
        assert kpis.revenue is not None and kpis.revenue > 0, "Revenue should be positive"
        assert kpis.net_income is not None, "Net income should be extracted"
        assert kpis.gross_profit is not None, "Gross profit should be extracted"
        assert kpis.total_assets is not None, "Total assets should be extracted"
        assert kpis.total_liabilities is not None, "Total liabilities should be extracted"

    @pytest.mark.asyncio
    async def test_derived_metrics_computed(self, sample_facts, sample_company_info, monkeypatch, tmp_path):
        monkeypatch.setenv("OPENAI_API_KEY", "")
        monkeypatch.setenv("PIPELINE_OUTPUT_DIR", str(tmp_path))

        from backend.agents.silver.financial_kpis import silver_financial_kpis_agent

        state = initial_state("AAPL")
        state["bronze_facts"] = sample_facts
        state["company_info"] = sample_company_info

        result = await silver_financial_kpis_agent(state)
        kpis: FinancialKPIs = result["silver_kpis"]

        assert kpis.gross_margin is not None, "Gross margin should be derived"
        assert 0 < kpis.gross_margin < 1, f"Gross margin {kpis.gross_margin} should be between 0-1"
        assert kpis.operating_margin is not None, "Operating margin should be derived"
        assert kpis.debt_to_equity is not None, "D/E ratio should be derived"
        assert kpis.current_ratio is not None, "Current ratio should be derived"
        assert kpis.free_cash_flow is not None, "FCF should be derived"
        assert kpis.revenue_yoy_change is not None, "Revenue YoY should be computed"

    @pytest.mark.asyncio
    async def test_empty_facts_produces_empty_kpis(self, monkeypatch, tmp_path):
        monkeypatch.setenv("OPENAI_API_KEY", "")
        monkeypatch.setenv("PIPELINE_OUTPUT_DIR", str(tmp_path))

        from backend.agents.silver.financial_kpis import silver_financial_kpis_agent

        state = initial_state("AAPL")
        state["bronze_facts"] = []

        result = await silver_financial_kpis_agent(state)
        assert result["silver_kpis"] is None
        assert len(result["errors"]) > 0


class TestBronzeToSilverRiskFactors:
    """10-K risk text → Risk factors (placeholder mode)."""

    @pytest.mark.asyncio
    async def test_placeholder_produces_valid_factors(self, sample_company_info, monkeypatch, tmp_path):
        monkeypatch.setenv("OPENAI_API_KEY", "")
        monkeypatch.setenv("PIPELINE_OUTPUT_DIR", str(tmp_path))

        from backend.agents.silver.risk_factors import silver_risk_factors_agent

        state = initial_state("AAPL")
        state["company_info"] = sample_company_info
        state["bronze_10k_risk_text"] = (
            "The Company faces significant risks from regulatory changes, "
            "intense competition in the technology sector, supply chain "
            "disruptions, and macroeconomic uncertainty."
        )

        result = await silver_risk_factors_agent(state)
        factors = result["silver_risk_factors"]

        assert len(factors) >= 1, "Should produce at least 1 risk factor"
        for f in factors:
            assert f["category"] in (
                "regulatory", "competitive", "operational", "financial",
                "legal", "technology", "macroeconomic", "esg",
            ), f"Invalid category: {f['category']}"
            assert 1 <= f["severity"] <= 5
            assert f["title"], "Title should be non-empty"
            assert f["summary"], "Summary should be non-empty"

    @pytest.mark.asyncio
    async def test_no_risk_text_produces_empty(self, monkeypatch, tmp_path):
        monkeypatch.setenv("OPENAI_API_KEY", "")
        monkeypatch.setenv("PIPELINE_OUTPUT_DIR", str(tmp_path))

        from backend.agents.silver.risk_factors import silver_risk_factors_agent

        state = initial_state("AAPL")
        state["bronze_10k_risk_text"] = ""

        result = await silver_risk_factors_agent(state)
        assert result["silver_risk_factors"] == []


class TestBronzeToSilverInsider:
    """Form 4 transactions → Insider signal."""

    @pytest.mark.asyncio
    async def test_signal_computed_from_trades(self, sample_insider_trades, monkeypatch, tmp_path):
        monkeypatch.setenv("PIPELINE_OUTPUT_DIR", str(tmp_path))

        from backend.agents.silver.insider_signal import silver_insider_signal_agent

        state = initial_state("AAPL")
        state["bronze_form4_transactions"] = sample_insider_trades

        result = await silver_insider_signal_agent(state)
        signal = result["silver_insider_signal"]

        assert signal["total_buys"] == 1, "Should count 1 buy (P)"
        assert signal["total_sells"] == 2, "Should count 2 sells (S)"
        assert signal["net_shares"] < 0, "Net should be negative (more selling)"
        assert signal["signal"] in ("bullish", "bearish", "neutral")
        assert signal["buy_sell_ratio"] is not None

    @pytest.mark.asyncio
    async def test_empty_trades_neutral_signal(self, monkeypatch, tmp_path):
        monkeypatch.setenv("PIPELINE_OUTPUT_DIR", str(tmp_path))

        from backend.agents.silver.insider_signal import silver_insider_signal_agent

        state = initial_state("AAPL")
        state["bronze_form4_transactions"] = []

        result = await silver_insider_signal_agent(state)
        signal = result["silver_insider_signal"]
        assert signal["signal"] == "neutral"
        assert signal["total_buys"] == 0
        assert signal["total_sells"] == 0

    @pytest.mark.asyncio
    async def test_cluster_detection_fires(self, monkeypatch, tmp_path):
        """3+ insiders selling within 30 days triggers cluster."""
        monkeypatch.setenv("PIPELINE_OUTPUT_DIR", str(tmp_path))

        from backend.agents.silver.insider_signal import silver_insider_signal_agent

        trades = [
            {"insider_name": f"Insider {i}", "transaction_date": f"2025-08-{10+i:02d}",
             "transaction_code": "S", "shares": 10000, "price_per_share": 200.0,
             "value": 2000000, "filing_date": f"2025-08-{12+i:02d}"}
            for i in range(4)
        ]

        state = initial_state("AAPL")
        state["bronze_form4_transactions"] = trades

        result = await silver_insider_signal_agent(state)
        signal = result["silver_insider_signal"]
        assert signal["cluster_detected"] is True, "Should detect sell cluster"
        assert signal["signal"] == "bearish"


    @pytest.mark.asyncio
    async def test_buy_sell_ratio_no_sentinel(self, monkeypatch, tmp_path):
        """Buys-only should produce None ratio, not a magic sentinel value."""
        monkeypatch.setenv("PIPELINE_OUTPUT_DIR", str(tmp_path))

        from backend.agents.silver.insider_signal import silver_insider_signal_agent

        trades = [
            {"insider_name": "Buyer A", "transaction_date": "2025-08-10",
             "transaction_code": "P", "shares": 10000},
            {"insider_name": "Buyer B", "transaction_date": "2025-08-12",
             "transaction_code": "P", "shares": 20000},
        ]

        state = initial_state("AAPL")
        state["bronze_form4_transactions"] = trades

        result = await silver_insider_signal_agent(state)
        signal = result["silver_insider_signal"]

        assert signal["total_buys"] == 2
        assert signal["total_sells"] == 0
        assert signal["buy_sell_ratio"] is None, (
            "Buys with zero sells should yield None, not a sentinel like 999.0"
        )


class TestBronzeToSilverInstitutional:
    """13F holdings → Institutional holders."""

    @pytest.mark.asyncio
    async def test_passive_active_classification(self, monkeypatch, tmp_path):
        monkeypatch.setenv("PIPELINE_OUTPUT_DIR", str(tmp_path))

        from backend.agents.silver.institutional import silver_institutional_agent

        holders = [
            {"holder_name": "Vanguard Group", "shares": 1_000_000, "value": 200e6},
            {"holder_name": "BlackRock Inc", "shares": 800_000, "value": 160e6},
            {"holder_name": "Activist Capital Partners", "shares": 500_000, "value": 100e6},
            {"holder_name": "State Street Global", "shares": 400_000, "value": 80e6},
        ]

        state = initial_state("AAPL")
        state["bronze_13f_holdings"] = holders

        result = await silver_institutional_agent(state)
        classified = result["silver_institutional_holders"]

        assert len(classified) == 4
        # Vanguard and BlackRock should be passive
        vanguard = next(h for h in classified if "Vanguard" in h["holder_name"])
        blackrock = next(h for h in classified if "BlackRock" in h["holder_name"])
        activist = next(h for h in classified if "Activist" in h["holder_name"])
        state_st = next(h for h in classified if "State Street" in h["holder_name"])

        assert vanguard["holder_type"] == "passive"
        assert blackrock["holder_type"] == "passive"
        assert state_st["holder_type"] == "passive"
        assert activist["holder_type"] == "active"

    @pytest.mark.asyncio
    async def test_sorted_by_shares_desc(self, monkeypatch, tmp_path):
        monkeypatch.setenv("PIPELINE_OUTPUT_DIR", str(tmp_path))

        from backend.agents.silver.institutional import silver_institutional_agent

        holders = [
            {"holder_name": "Small Fund", "shares": 100},
            {"holder_name": "Big Fund", "shares": 1_000_000},
            {"holder_name": "Medium Fund", "shares": 50_000},
        ]

        state = initial_state("AAPL")
        state["bronze_13f_holdings"] = holders

        result = await silver_institutional_agent(state)
        classified = result["silver_institutional_holders"]

        shares_list = [h["shares"] for h in classified]
        assert shares_list == sorted(shares_list, reverse=True), "Should be sorted by shares desc"

    @pytest.mark.asyncio
    async def test_top_10_limit(self, monkeypatch, tmp_path):
        monkeypatch.setenv("PIPELINE_OUTPUT_DIR", str(tmp_path))

        from backend.agents.silver.institutional import silver_institutional_agent

        holders = [
            {"holder_name": f"Fund {i}", "shares": (15 - i) * 1000}
            for i in range(15)
        ]

        state = initial_state("AAPL")
        state["bronze_13f_holdings"] = holders

        result = await silver_institutional_agent(state)
        assert len(result["silver_institutional_holders"]) == 10


class TestBronzeToSilverMaterialEvents:
    """8-K filings → Material events (rule-based mode)."""

    @pytest.mark.asyncio
    async def test_rule_based_classification(self, sample_company_info, monkeypatch, tmp_path):
        monkeypatch.setenv("OPENAI_API_KEY", "")
        monkeypatch.setenv("PIPELINE_OUTPUT_DIR", str(tmp_path))

        from backend.agents.silver.material_events import silver_material_events_agent

        events = [
            {"filing_date": "2025-10-30", "description": "Item 2.02 Results of Operations and Financial Condition"},
            {"filing_date": "2025-07-01", "description": "Item 5.02 Departure of Directors or Certain Officers"},
            {"filing_date": "2025-05-15", "description": "Item 4.02 Non-Reliance on Previously Issued Financial Statements"},
        ]

        state = initial_state("AAPL")
        state["company_info"] = sample_company_info
        state["bronze_8k_filings"] = events

        result = await silver_material_events_agent(state)
        classified = result["silver_material_events"]

        assert len(classified) == 3
        for event in classified:
            assert event["item_code"], "Item code should be assigned"
            assert 1 <= event["severity"] <= 5, f"Severity {event['severity']} out of range"
            assert event["item_description"], "Description should be populated"

        # 4.02 should be high severity
        non_reliance = [e for e in classified if e["item_code"] == "4.02"]
        assert len(non_reliance) == 1
        assert non_reliance[0]["severity"] == 5, "4.02 Non-reliance should be severity 5"

    @pytest.mark.asyncio
    async def test_no_events_produces_empty(self, monkeypatch, tmp_path):
        monkeypatch.setenv("OPENAI_API_KEY", "")
        monkeypatch.setenv("PIPELINE_OUTPUT_DIR", str(tmp_path))

        from backend.agents.silver.material_events import silver_material_events_agent

        state = initial_state("AAPL")
        state["bronze_8k_filings"] = []

        result = await silver_material_events_agent(state)
        assert result["silver_material_events"] == []

    @pytest.mark.asyncio
    async def test_date_not_misclassified_as_item_code(self, sample_company_info, monkeypatch, tmp_path):
        """Filing dates like '2025-01-01' should NOT match item code '1.01'."""
        monkeypatch.setenv("OPENAI_API_KEY", "")
        monkeypatch.setenv("PIPELINE_OUTPUT_DIR", str(tmp_path))

        from backend.agents.silver.material_events import silver_material_events_agent

        events = [
            {"filing_date": "2025-01-01", "description": "Quarterly earnings update"},
        ]

        state = initial_state("AAPL")
        state["company_info"] = sample_company_info
        state["bronze_8k_filings"] = events

        result = await silver_material_events_agent(state)
        classified = result["silver_material_events"]

        assert len(classified) == 1
        # Should default to "8.01" (Other Events), NOT "1.01" (Material Agreement)
        assert classified[0]["item_code"] != "1.01", (
            "Date '2025-01-01' should not match item code '1.01'"
        )


class TestBronzeToSilverGovernance:
    """DEF 14A → Governance (placeholder mode)."""

    @pytest.mark.asyncio
    async def test_placeholder_mode_has_correct_shape(self, sample_company_info, monkeypatch, tmp_path):
        monkeypatch.setenv("OPENAI_API_KEY", "")
        monkeypatch.setenv("PIPELINE_OUTPUT_DIR", str(tmp_path))

        from backend.agents.silver.governance import silver_governance_agent

        state = initial_state("AAPL")
        state["company_info"] = sample_company_info
        state["bronze_def14a_proxy"] = {"text": "Some proxy statement text with governance info."}

        result = await silver_governance_agent(state)
        gov = result["silver_governance"]

        # Even in placeholder mode, all GovernanceData fields should exist
        expected_fields = set(GovernanceData.model_fields.keys())
        assert expected_fields.issubset(set(gov.keys())), (
            f"Missing fields: {expected_fields - set(gov.keys())}"
        )

    @pytest.mark.asyncio
    async def test_no_proxy_text_returns_empty(self, monkeypatch, tmp_path):
        monkeypatch.setenv("OPENAI_API_KEY", "")
        monkeypatch.setenv("PIPELINE_OUTPUT_DIR", str(tmp_path))

        from backend.agents.silver.governance import silver_governance_agent

        state = initial_state("AAPL")
        state["bronze_def14a_proxy"] = {}

        result = await silver_governance_agent(state)
        gov = result["silver_governance"]
        assert gov["ceo_name"] == ""
        assert gov["board_size"] is None


# ===========================================================================
# 3. Silver → Gold Transformation Tests
# ===========================================================================


class TestSilverToGoldRiskAssessment:
    """Financial KPIs → Risk scores (placeholder mode)."""

    @pytest.mark.asyncio
    async def test_risk_scores_from_kpis(self, sample_kpis, sample_company_info, monkeypatch, tmp_path):
        monkeypatch.setenv("OPENAI_API_KEY", "")
        monkeypatch.setenv("PIPELINE_OUTPUT_DIR", str(tmp_path))

        from backend.agents.gold.risk_assessment import gold_risk_assessment_agent

        state = initial_state("AAPL")
        state["silver_kpis"] = sample_kpis
        state["company_info"] = sample_company_info

        result = await gold_risk_assessment_agent(state)
        risk = result["gold_risk_scores"]

        assert risk is not None
        assert len(risk.dimensions) == 5, "Should score 5 dimensions"
        for dim in risk.dimensions:
            assert 1 <= dim.score <= 5, f"{dim.dimension} score {dim.score} out of range"
            assert dim.reasoning, f"{dim.dimension} missing reasoning"
            assert len(dim.key_metrics) > 0, f"{dim.dimension} missing key metrics"

        assert 1.0 <= risk.composite_score <= 5.0
        assert risk.risk_level in ("Low", "Medium", "High", "Critical")

    @pytest.mark.asyncio
    async def test_high_leverage_detected(self, sample_company_info, monkeypatch, tmp_path):
        """High D/E ratio should produce elevated Governance risk score."""
        monkeypatch.setenv("OPENAI_API_KEY", "")
        monkeypatch.setenv("PIPELINE_OUTPUT_DIR", str(tmp_path))

        from backend.agents.gold.risk_assessment import gold_risk_assessment_agent

        stressed_kpis = FinancialKPIs(
            revenue=100e9, gross_profit=30e9, gross_margin=0.30,
            operating_income=10e9, operating_margin=0.10,
            total_assets=200e9, total_liabilities=180e9,
            stockholders_equity=20e9, debt_to_equity=9.0,
            current_ratio=0.7, fiscal_year=2025, period_end="2025-12-31",
        )

        state = initial_state("STRESS")
        state["silver_kpis"] = stressed_kpis
        state["company_info"] = sample_company_info

        result = await gold_risk_assessment_agent(state)
        risk = result["gold_risk_scores"]

        gov_dim = next(d for d in risk.dimensions if d.dimension == "Governance")
        liq_dim = next(d for d in risk.dimensions if d.dimension == "Liquidity")
        assert gov_dim.score >= 3, f"D/E of 9.0 should give Governance score >= 3, got {gov_dim.score}"
        assert liq_dim.score >= 3, f"Current ratio 0.7 should give Liquidity score >= 3, got {liq_dim.score}"

    @pytest.mark.asyncio
    async def test_no_kpis_returns_error(self, monkeypatch, tmp_path):
        monkeypatch.setenv("OPENAI_API_KEY", "")
        monkeypatch.setenv("PIPELINE_OUTPUT_DIR", str(tmp_path))

        from backend.agents.gold.risk_assessment import gold_risk_assessment_agent

        state = initial_state("AAPL")
        state["silver_kpis"] = None

        result = await gold_risk_assessment_agent(state)
        assert result["gold_risk_scores"] is None
        assert len(result["errors"]) > 0


class TestSilverToGoldCrossWorkstream:
    """All silver tables → Cross-workstream correlation flags."""

    @pytest.mark.asyncio
    async def test_clean_data_no_flags(
        self, sample_kpis, sample_company_info, sample_risk_assessment,
        sample_governance, sample_insider_signal, sample_institutional_holders,
        sample_material_events, sample_risk_factors, monkeypatch, tmp_path,
    ):
        """Clean data should produce no cross-workstream flags."""
        monkeypatch.setenv("PIPELINE_OUTPUT_DIR", str(tmp_path))

        from backend.agents.gold.cross_workstream import gold_cross_workstream_agent

        state = initial_state("AAPL")
        state.update(
            company_info=sample_company_info,
            silver_kpis=sample_kpis,
            silver_governance=sample_governance,
            silver_insider_signal=sample_insider_signal,
            silver_institutional_holders=sample_institutional_holders,
            silver_material_events=sample_material_events,
            silver_risk_factors=sample_risk_factors,
            gold_risk_scores=sample_risk_assessment,
        )

        result = await gold_cross_workstream_agent(state)
        assert result["deal_recommendation"] == "PROCEED"
        assert len(result["gold_cross_workstream_flags"]) == 0

    @pytest.mark.asyncio
    async def test_rule3_pay_performance_mismatch(
        self, sample_kpis, sample_company_info, sample_risk_assessment,
        monkeypatch, tmp_path,
    ):
        """CEO pay growth >> revenue growth + low board independence → flag."""
        monkeypatch.setenv("PIPELINE_OUTPUT_DIR", str(tmp_path))

        from backend.agents.gold.cross_workstream import gold_cross_workstream_agent

        weak_governance = GovernanceData(
            ceo_name="Bad CEO",
            ceo_pay_growth=0.50,  # 50% pay increase
            board_independence_pct=0.50,  # Only 50% independent
        ).model_dump()

        state = initial_state("BAD")
        state.update(
            company_info=sample_company_info,
            silver_kpis=sample_kpis,  # Revenue growth ~6.4%
            silver_governance=weak_governance,
            silver_insider_signal={},
            silver_institutional_holders=[],
            silver_material_events=[],
            silver_risk_factors=[],
            gold_risk_scores=sample_risk_assessment,
        )

        result = await gold_cross_workstream_agent(state)
        flags = result["gold_cross_workstream_flags"]

        rule3_flags = [f for f in flags if f["rule_name"] == "Pay-Performance Mismatch + Weak Board"]
        assert len(rule3_flags) == 1, "Rule 3 should fire"
        assert rule3_flags[0]["severity"] == "High"

    @pytest.mark.asyncio
    async def test_rule3_fires_on_negative_revenue_with_pay_raise(
        self, sample_company_info, sample_risk_assessment,
        monkeypatch, tmp_path,
    ):
        """CEO getting a raise while revenue declines should fire Rule 3."""
        monkeypatch.setenv("PIPELINE_OUTPUT_DIR", str(tmp_path))

        from backend.agents.gold.cross_workstream import gold_cross_workstream_agent

        declining_kpis = FinancialKPIs(
            revenue=90e9, revenue_prior=100e9, revenue_yoy_change=-0.10,
            fiscal_year=2025, period_end="2025-12-31",
        )
        weak_governance = GovernanceData(
            ceo_name="Overpaid CEO",
            ceo_pay_growth=0.25,  # 25% raise while revenue drops 10%
            board_independence_pct=0.50,
        ).model_dump()

        state = initial_state("DECLINE")
        state.update(
            company_info=sample_company_info,
            silver_kpis=declining_kpis,
            silver_governance=weak_governance,
            silver_insider_signal={},
            silver_institutional_holders=[],
            silver_material_events=[],
            silver_risk_factors=[],
            gold_risk_scores=sample_risk_assessment,
        )

        result = await gold_cross_workstream_agent(state)
        flags = result["gold_cross_workstream_flags"]

        rule3_flags = [f for f in flags if f["rule_name"] == "Pay-Performance Mismatch + Weak Board"]
        assert len(rule3_flags) == 1, (
            "Rule 3 should fire when CEO pay rises while revenue declines"
        )

    @pytest.mark.asyncio
    async def test_critical_flag_blocks_deal(
        self, sample_kpis, sample_company_info, sample_risk_assessment,
        monkeypatch, tmp_path,
    ):
        """Non-reliance on financials (4.02) should produce DO_NOT_PROCEED."""
        monkeypatch.setenv("PIPELINE_OUTPUT_DIR", str(tmp_path))

        from backend.agents.gold.cross_workstream import gold_cross_workstream_agent

        state = initial_state("TOXIC")
        state.update(
            company_info=sample_company_info,
            silver_kpis=sample_kpis,
            silver_governance={},
            silver_insider_signal={},
            silver_institutional_holders=[],
            silver_material_events=[
                MaterialEvent(
                    filing_date="2025-10-01", item_code="4.02",
                    item_description="Non-Reliance", severity=5,
                    summary="Company restated financials.",
                ).model_dump(),
            ],
            silver_risk_factors=[],
            gold_risk_scores=sample_risk_assessment,
        )

        result = await gold_cross_workstream_agent(state)
        assert result["deal_recommendation"] == "DO_NOT_PROCEED"
        critical_flags = [f for f in result["gold_cross_workstream_flags"] if f["severity"] == "Critical"]
        assert len(critical_flags) >= 1


# ===========================================================================
# 4. Data Quality Guards (regression tests)
# ===========================================================================


class TestDataQualityGuards:
    """Regression tests: ensure no all-null silver outputs when bronze has data."""

    @pytest.mark.asyncio
    async def test_kpis_not_all_null_with_valid_facts(self, sample_facts, sample_company_info, monkeypatch, tmp_path):
        """KPI extraction should produce at least 5 non-null fields."""
        monkeypatch.setenv("OPENAI_API_KEY", "")
        monkeypatch.setenv("PIPELINE_OUTPUT_DIR", str(tmp_path))

        from backend.agents.silver.financial_kpis import silver_financial_kpis_agent

        state = initial_state("AAPL")
        state["bronze_facts"] = sample_facts
        state["company_info"] = sample_company_info

        result = await silver_financial_kpis_agent(state)
        kpis = result["silver_kpis"]

        non_null_count = sum(
            1 for field in ["revenue", "net_income", "gross_profit", "total_assets",
                            "total_liabilities", "stockholders_equity", "operating_income",
                            "cash_and_equivalents", "operating_cash_flow", "eps_basic"]
            if getattr(kpis, field) is not None
        )
        assert non_null_count >= 5, f"Only {non_null_count} non-null KPIs — likely extraction bug"

    @pytest.mark.asyncio
    async def test_insider_signal_consistent_with_trades(self, monkeypatch, tmp_path):
        """Signal should be consistent with trade direction."""
        monkeypatch.setenv("PIPELINE_OUTPUT_DIR", str(tmp_path))

        from backend.agents.silver.insider_signal import silver_insider_signal_agent

        # All sells, no buys
        trades = [
            {"insider_name": f"Seller {i}", "transaction_date": "2025-08-15",
             "transaction_code": "S", "shares": 50000}
            for i in range(5)
        ]

        state = initial_state("AAPL")
        state["bronze_form4_transactions"] = trades

        result = await silver_insider_signal_agent(state)
        signal = result["silver_insider_signal"]

        assert signal["total_buys"] == 0
        assert signal["total_sells"] == 5
        assert signal["signal"] == "bearish", "All-sell should be bearish"
        assert signal["net_shares"] < 0

    @pytest.mark.asyncio
    async def test_material_events_severity_range(self, sample_company_info, monkeypatch, tmp_path):
        """All classified events should have severity in [1, 5]."""
        monkeypatch.setenv("OPENAI_API_KEY", "")
        monkeypatch.setenv("PIPELINE_OUTPUT_DIR", str(tmp_path))

        from backend.agents.silver.material_events import silver_material_events_agent

        events = [
            {"filing_date": "2025-01-15", "description": f"Item {code} {desc}"}
            for code, (desc, _) in [
                ("1.01", ("Entry into Material Agreement", 3)),
                ("2.02", ("Results of Operations", 2)),
                ("4.02", ("Non-Reliance on Financial Statements", 5)),
                ("5.02", ("Departure/Appointment of Officers", 3)),
                ("7.01", ("Regulation FD Disclosure", 1)),
            ]
        ]

        state = initial_state("AAPL")
        state["company_info"] = sample_company_info
        state["bronze_8k_filings"] = events

        result = await silver_material_events_agent(state)
        for event in result["silver_material_events"]:
            assert 1 <= event["severity"] <= 5, f"Severity {event['severity']} out of range for {event['item_code']}"

    @pytest.mark.asyncio
    async def test_governance_fields_complete_schema(self, sample_company_info, monkeypatch, tmp_path):
        """GovernanceData should have all 14 fields regardless of mode."""
        monkeypatch.setenv("OPENAI_API_KEY", "")
        monkeypatch.setenv("PIPELINE_OUTPUT_DIR", str(tmp_path))

        from backend.agents.silver.governance import silver_governance_agent

        state = initial_state("AAPL")
        state["company_info"] = sample_company_info
        state["bronze_def14a_proxy"] = {"text": "Annual proxy statement text."}

        result = await silver_governance_agent(state)
        gov = result["silver_governance"]

        required_fields = {
            "ceo_name", "ceo_total_comp", "ceo_comp_prior", "ceo_pay_growth",
            "median_employee_pay", "ceo_pay_ratio", "board_size",
            "independent_directors", "board_independence_pct",
            "has_poison_pill", "has_staggered_board", "has_dual_class",
            "anti_takeover_provisions", "governance_flags",
        }
        assert required_fields.issubset(set(gov.keys())), (
            f"Missing fields: {required_fields - set(gov.keys())}"
        )

    def test_risk_assessment_dimensions_complete(self, sample_kpis):
        """Placeholder risk scoring should cover all 5 dimensions."""
        from backend.agents.gold.risk_assessment import _placeholder_risk

        risk = _placeholder_risk(sample_kpis)

        dimension_names = {d.dimension for d in risk.dimensions}
        expected = {"Financial Health", "Market Position", "Operational Risk", "Governance", "Liquidity"}
        assert dimension_names == expected, f"Missing dimensions: {expected - dimension_names}"

    def test_cross_workstream_rule_coverage(self):
        """Verify all 6 rules are evaluated (not silently dropped)."""
        from backend.agents.gold.cross_workstream import _evaluate_correlations

        # Build a state that triggers ALL rules
        kpis = FinancialKPIs(
            revenue=100e9, revenue_prior=120e9, revenue_yoy_change=-0.167,
            gross_margin=0.30, fiscal_year=2025, period_end="2025-12-31",
        )

        state = initial_state("TOXIC")
        state.update(
            silver_kpis=kpis,
            silver_insider_signal=InsiderSignal(
                total_buys=0, total_sells=10, signal="bearish",
                cluster_detected=True, cluster_description="Cluster sell: 5 insiders",
            ).model_dump(),
            silver_material_events=[
                MaterialEvent(item_code="4.01", severity=4, filing_date="2025-01-01").model_dump(),
                MaterialEvent(item_code="4.02", severity=5, filing_date="2025-02-01").model_dump(),
                MaterialEvent(item_code="5.02", severity=3, filing_date="2025-03-01").model_dump(),
                MaterialEvent(item_code="5.02", severity=3, filing_date="2025-04-01").model_dump(),
            ],
            silver_governance=GovernanceData(
                ceo_pay_growth=0.80, board_independence_pct=0.40,
                governance_flags=["Dual class shares", "No say-on-pay"],
            ).model_dump(),
            silver_institutional_holders=[
                {"holder_name": f"Fund {i}", "shares": 1e6, "change_pct": -0.30}
                for i in range(3)
            ],
            silver_risk_factors=[
                RiskFactorItem(
                    category="regulatory", title="New Regulation",
                    summary="Novel regulatory risk.", severity=4, is_novel=True,
                ).model_dump(),
            ],
        )

        flags = _evaluate_correlations(state)
        rule_names = {f["rule_name"] for f in flags}

        # All 6 rules should fire with this toxic state:
        # Rule 3 fires because revenue is declining while CEO pay is rising
        expected_rules = {
            "Insider+Revenue+Auditor",          # Rule 1
            "Non-Reliance on Financials",       # Rule 2
            "Pay-Performance Mismatch + Weak Board",    # Rule 3
            "Novel Regulatory Risk + Insider Selling",  # Rule 4
            "Institutional Exodus + Margin Pressure",   # Rule 5
            "Leadership Instability + Governance Concerns",  # Rule 6
        }
        assert expected_rules.issubset(rule_names), (
            f"Missing rules: {expected_rules - rule_names}"
        )


class TestSectionExtractionEdgeCases:
    """Edge cases for DEF 14A section extraction."""

    def test_overlapping_sections_not_double_counted(self):
        """If two patterns match nearby positions, content shouldn't be duplicated."""
        text = "A" * 70_000 + (
            "\nCompensation Discussion and Analysis\n\n"
            "The Committee reviewed CEO pay structure. This is detailed below. "
            "Performance metrics are tied to TSR.\n"
            + "B" * 3000 +
            "\nSummary Compensation Table\n\n"
            "The following table shows compensation. Awards vest over 4 years. "
            "Non-equity incentives are paid annually.\n"
            + "C" * 3000
        ) + "D" * 20_000

        result, sections = extract_proxy_sections(text, budget=50_000)

        # Both sections should be found
        assert "Compensation Discussion & Analysis" in sections or "Summary Compensation Table" in sections
        # No massive duplication
        assert result.count("The Committee reviewed") <= 1

    def test_very_small_budget_still_extracts(self):
        """Even with tiny budget, should extract something useful."""
        full_text = _build_proxy_text(REALISTIC_PROXY_SECTIONS)
        result, sections = extract_proxy_sections(full_text, budget=2_000)

        assert len(result) <= 2_000
        assert len(sections) >= 1

    def test_unicode_in_proxy_text(self):
        """Proxy text with unicode (checkboxes, em-dashes) should not crash."""
        sections = {"Director Independence": "Board is 80% independent. ☒ Confirmed."}
        full_text = _build_proxy_text(sections)
        result, found = extract_proxy_sections(full_text, budget=50_000)
        assert "80% independent" in result
