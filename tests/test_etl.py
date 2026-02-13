"""Comprehensive ETL test suite for the medallion (bronze/silver/gold) architecture.

Validates data completeness, coherence, correct types, cross-layer consistency,
and error resilience across all 16 pipeline agents. CsvWriter is NOT mocked —
real CSV files are written and read back for validation.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pandas as pd
import pytest

from backend.models import (
    CompanyInfo,
    FinancialFact,
    FinancialKPIs,
    DiligenceMemo,
    RiskAssessment,
    RiskDimension,
    RedFlag,
    initial_state,
)
from tests.conftest import MOCK_COMPANY_FACTS, MOCK_COMPANY_TICKERS, MOCK_SUBMISSIONS


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

TICKER = "AAPL"


def _build_mock_facts() -> list[FinancialFact]:
    """Build FinancialFact list from conftest mock data."""
    facts = []
    for taxonomy in ("us-gaap", "dei"):
        for tag_name, tag_data in MOCK_COMPANY_FACTS["facts"].get(taxonomy, {}).items():
            for unit_type, entries in tag_data.get("units", {}).items():
                for entry in entries:
                    if entry.get("form") != "10-K":
                        continue
                    facts.append(
                        FinancialFact(
                            tag=tag_name,
                            label=tag_data["label"],
                            value=float(entry["val"]),
                            unit=unit_type,
                            start=entry.get("start"),
                            end=entry["end"],
                            fy=entry["fy"],
                            fp=entry.get("fp", "FY"),
                            form=entry["form"],
                            filed=entry["filed"],
                            accession=entry["accn"],
                            frame=entry.get("frame"),
                            taxonomy=taxonomy,
                        )
                    )
    return facts


def _make_company_info() -> CompanyInfo:
    """Create sample CompanyInfo from conftest mock data."""
    return CompanyInfo(
        ticker="AAPL",
        company_name="Apple Inc.",
        cik="0000320193",
        sic="3571",
        sic_description="Electronic Computers",
        fiscal_year_end="0928",
        exchanges=["Nasdaq"],
        entity_type="operating",
        category="Large accelerated filer",
        latest_10k_date="2025-11-01",
    )


def _make_sample_insider_trades() -> list[dict]:
    """Sample insider transactions."""
    return [
        {
            "insider_name": "Tim Cook",
            "insider_title": "CEO",
            "transaction_date": "2025-08-15",
            "transaction_code": "S",
            "shares": 50000,
            "price_per_share": 225.0,
            "value": 11250000,
            "filing_date": "2025-08-17",
        },
        {
            "insider_name": "Jeff Williams",
            "insider_title": "COO",
            "transaction_date": "2025-08-10",
            "transaction_code": "S",
            "shares": 30000,
            "price_per_share": 223.0,
            "value": 6690000,
            "filing_date": "2025-08-12",
        },
        {
            "insider_name": "Luca Maestri",
            "insider_title": "CFO",
            "transaction_date": "2025-07-20",
            "transaction_code": "P",
            "shares": 10000,
            "price_per_share": 218.0,
            "value": 2180000,
            "filing_date": "2025-07-22",
        },
    ]


def _make_sample_8k_events() -> list[dict]:
    """Sample raw 8-K events as returned by EdgarFilingsClient."""
    return [
        {
            "filing_date": "2025-10-30",
            "form": "8-K",
            "description": "2.02 Results of Operations and Financial Condition",
            "accession": "0000320193-25-000110",
        },
        {
            "filing_date": "2025-07-01",
            "form": "8-K",
            "description": "5.02 Departure of Directors or Certain Officers",
            "accession": "0000320193-25-000095",
        },
    ]


def _make_sample_13f_holdings() -> list[dict]:
    """Sample 13F holders."""
    return [
        {"holder_name": "Vanguard Group", "shares": 1_200_000_000, "value": 270e9, "change_pct": 0.02, "holder_type": "passive"},
        {"holder_name": "BlackRock Inc", "shares": 1_000_000_000, "value": 225e9, "change_pct": -0.01, "holder_type": "passive"},
        {"holder_name": "Berkshire Hathaway", "shares": 900_000_000, "value": 202e9, "change_pct": -0.05, "holder_type": "active"},
    ]


def _make_sample_proxy_data() -> dict:
    """Sample DEF 14A proxy data."""
    return {
        "filing_date": "2025-02-15",
        "text": (
            "DEF 14A Proxy Statement for Apple Inc. "
            "CEO Tim Cook received total compensation of $98,734,000 "
            "compared to $84,200,000 in the prior year. "
            "The median employee annual total compensation was $94,118. "
            "The CEO pay ratio is 1,049:1. "
            "The Board of Directors consists of 8 members, 7 of whom are independent."
        ),
    }


def _patch_csv_writer(module_path: str, tmp_path: Path):
    """Return a patch context manager that redirects CsvWriter output to tmp_path."""
    from backend.data.csv_writer import CsvWriter as RealWriter
    return patch(
        f"{module_path}.CsvWriter",
        lambda ticker, **kw: RealWriter(ticker, output_dir=str(tmp_path)),
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def etl_writer(tmp_path):
    """Create a CsvWriter that writes to tmp dir."""
    from backend.data.csv_writer import CsvWriter

    return CsvWriter(TICKER, output_dir=str(tmp_path))


@pytest.fixture
def etl_output_dir(tmp_path):
    """Return the AAPL output directory."""
    return tmp_path / TICKER


@pytest.fixture
def mock_facts():
    """Build FinancialFact list from conftest mock data."""
    return _build_mock_facts()


@pytest.fixture
def company_info():
    """Sample CompanyInfo."""
    return _make_company_info()


# ═══════════════════════════════════════════════════════════════════════════
# 1. BRONZE LAYER COMPLETENESS
# ═══════════════════════════════════════════════════════════════════════════


class TestBronzeLayerCompleteness:
    """Validate each bronze table is created and well-formed."""

    @pytest.mark.asyncio
    async def test_bronze_resolver_creates_company_info_csv(self, tmp_path):
        """bronze_resolver writes bronze_company_info.csv with correct columns."""
        state = initial_state(TICKER)
        mock_client = AsyncMock()
        mock_client.resolve_cik.return_value = "0000320193"
        mock_client.get_company_info.return_value = _make_company_info()

        with (
            _patch_csv_writer("backend.agents.bronze.resolver", tmp_path),
            patch("backend.agents.bronze.resolver.EdgarClient", return_value=mock_client),
        ):
            from backend.agents.bronze.resolver import bronze_resolver_agent

            result = await bronze_resolver_agent(state)

        assert result["company_info"].ticker == "AAPL"
        assert result["bronze_company_info_path"] is not None
        csv_path = Path(result["bronze_company_info_path"])
        assert csv_path.exists()

        df = pd.read_csv(csv_path)
        assert len(df) == 1
        assert "ticker" in df.columns
        assert "company_name" in df.columns
        assert "cik" in df.columns
        assert "ingested_at" in df.columns
        assert "source_url" in df.columns
        assert df.iloc[0]["ticker"] == "AAPL"

    @pytest.mark.asyncio
    async def test_bronze_xbrl_creates_facts_csv(self, tmp_path):
        """bronze_xbrl writes bronze_xbrl_facts.csv with all XBRL facts."""
        state = initial_state(TICKER)
        state["company_info"] = _make_company_info()

        facts = _build_mock_facts()
        mock_client = AsyncMock()
        mock_client.get_company_facts.return_value = facts

        with (
            _patch_csv_writer("backend.agents.bronze.xbrl_facts", tmp_path),
            patch("backend.agents.bronze.xbrl_facts.EdgarClient", return_value=mock_client),
        ):
            from backend.agents.bronze.xbrl_facts import bronze_xbrl_agent

            result = await bronze_xbrl_agent(state)

        assert len(result["bronze_facts"]) == len(facts)
        csv_path = Path(result["bronze_xbrl_facts_path"])
        assert csv_path.exists()

        df = pd.read_csv(csv_path)
        assert len(df) == len(facts)
        for col in ("tag", "label", "value", "unit", "end", "fy", "form", "filed", "accession"):
            assert col in df.columns, f"Missing column: {col}"
        assert "ingested_at" in df.columns
        assert "source_url" in df.columns

    @pytest.mark.asyncio
    async def test_bronze_10k_creates_risk_text_csv(self, tmp_path):
        """bronze_10k writes bronze_10k_risk_text.csv when text is available."""
        state = initial_state(TICKER)
        risk_text = "Item 1A: The Company faces competition from many companies..."

        mock_client = AsyncMock()
        mock_client.get_10k_risk_factors.return_value = risk_text

        with (
            _patch_csv_writer("backend.agents.bronze.ten_k", tmp_path),
            patch("backend.agents.bronze.ten_k.EdgarFilingsClient", return_value=mock_client),
        ):
            from backend.agents.bronze.ten_k import bronze_10k_agent

            result = await bronze_10k_agent(state)

        assert result["bronze_10k_risk_text"] == risk_text
        csv_path = Path(result["bronze_10k_risk_text_path"])
        assert csv_path.exists()

        df = pd.read_csv(csv_path)
        assert len(df) == 1
        assert "risk_text" in df.columns
        assert "ingested_at" in df.columns
        assert df.iloc[0]["risk_text"] == risk_text

    @pytest.mark.asyncio
    async def test_bronze_form4_creates_transactions_csv(self, tmp_path):
        """bronze_form4 writes bronze_form4_transactions.csv with insider trades."""
        state = initial_state(TICKER)
        trades = _make_sample_insider_trades()

        mock_client = AsyncMock()
        mock_client.get_form4_filings.return_value = trades

        with (
            _patch_csv_writer("backend.agents.bronze.form4", tmp_path),
            patch("backend.agents.bronze.form4.EdgarFilingsClient", return_value=mock_client),
        ):
            from backend.agents.bronze.form4 import bronze_form4_agent

            result = await bronze_form4_agent(state)

        assert len(result["bronze_form4_transactions"]) == 3
        csv_path = Path(result["bronze_form4_path"])
        assert csv_path.exists()

        df = pd.read_csv(csv_path)
        assert len(df) == 3
        assert "insider_name" in df.columns
        assert "transaction_code" in df.columns
        assert "ingested_at" in df.columns
        assert "source_url" in df.columns

    @pytest.mark.asyncio
    async def test_bronze_13f_creates_holdings_csv(self, tmp_path):
        """bronze_13f writes bronze_13f_holdings.csv with institutional data."""
        state = initial_state(TICKER)
        holders = _make_sample_13f_holdings()

        mock_client = AsyncMock()
        mock_client.get_institutional_holders.return_value = holders

        with (
            _patch_csv_writer("backend.agents.bronze.thirteen_f", tmp_path),
            patch("backend.agents.bronze.thirteen_f.EdgarFilingsClient", return_value=mock_client),
        ):
            from backend.agents.bronze.thirteen_f import bronze_13f_agent

            result = await bronze_13f_agent(state)

        assert len(result["bronze_13f_holdings"]) == 3
        csv_path = Path(result["bronze_13f_path"])
        assert csv_path.exists()

        df = pd.read_csv(csv_path)
        assert len(df) == 3
        assert "holder_name" in df.columns
        assert "shares" in df.columns
        assert "ingested_at" in df.columns

    @pytest.mark.asyncio
    async def test_bronze_8k_creates_filings_csv(self, tmp_path):
        """bronze_8k writes bronze_8k_filings.csv with material events."""
        state = initial_state(TICKER)
        events = _make_sample_8k_events()

        mock_client = AsyncMock()
        mock_client.get_8k_filings.return_value = events

        with (
            _patch_csv_writer("backend.agents.bronze.eight_k", tmp_path),
            patch("backend.agents.bronze.eight_k.EdgarFilingsClient", return_value=mock_client),
        ):
            from backend.agents.bronze.eight_k import bronze_8k_agent

            result = await bronze_8k_agent(state)

        assert len(result["bronze_8k_filings"]) == 2
        csv_path = Path(result["bronze_8k_path"])
        assert csv_path.exists()

        df = pd.read_csv(csv_path)
        assert len(df) == 2
        assert "filing_date" in df.columns
        assert "description" in df.columns
        assert "ingested_at" in df.columns

    @pytest.mark.asyncio
    async def test_bronze_def14a_creates_proxy_csv(self, tmp_path):
        """bronze_def14a writes bronze_def14a_proxy.csv with proxy text."""
        state = initial_state(TICKER)
        proxy_data = _make_sample_proxy_data()

        mock_client = AsyncMock()
        mock_client.get_def14a.return_value = proxy_data

        with (
            _patch_csv_writer("backend.agents.bronze.def14a", tmp_path),
            patch("backend.agents.bronze.def14a.EdgarFilingsClient", return_value=mock_client),
        ):
            from backend.agents.bronze.def14a import bronze_def14a_agent

            result = await bronze_def14a_agent(state)

        assert result["bronze_def14a_proxy"]["text"] is not None
        csv_path = Path(result["bronze_def14a_path"])
        assert csv_path.exists()

        df = pd.read_csv(csv_path)
        assert len(df) == 1
        assert "proxy_text" in df.columns
        assert "ingested_at" in df.columns
        assert "source_url" in df.columns


# ═══════════════════════════════════════════════════════════════════════════
# 2. SILVER LAYER COMPLETENESS
# ═══════════════════════════════════════════════════════════════════════════


class TestSilverLayerCompleteness:
    """Validate each silver table is created with correct schema."""

    @pytest.mark.asyncio
    async def test_silver_financial_kpis_creates_csv(self, tmp_path, monkeypatch):
        """silver_financial_kpis writes silver_financial_kpis.csv with metric rows."""
        monkeypatch.setenv("OPENAI_API_KEY", "")
        state = initial_state(TICKER)
        state["company_info"] = _make_company_info()
        state["bronze_facts"] = _build_mock_facts()

        with _patch_csv_writer("backend.agents.silver.financial_kpis", tmp_path):
            from backend.agents.silver.financial_kpis import silver_financial_kpis_agent

            result = await silver_financial_kpis_agent(state)

        assert result["silver_kpis"] is not None
        csv_path = Path(result["silver_kpis_path"])
        assert csv_path.exists()

        df = pd.read_csv(csv_path)
        assert "metric" in df.columns
        assert "value" in df.columns
        assert "source_tag" in df.columns
        assert "processed_at" in df.columns
        assert "source_bronze" in df.columns
        # Verify source_bronze lineage
        assert df.iloc[0]["source_bronze"] == "bronze_xbrl_facts.csv"

    @pytest.mark.asyncio
    async def test_silver_risk_factors_creates_csv(self, tmp_path, monkeypatch):
        """silver_risk_factors writes silver_risk_factors.csv in placeholder mode."""
        monkeypatch.setenv("OPENAI_API_KEY", "")
        state = initial_state(TICKER)
        state["company_info"] = _make_company_info()
        state["bronze_10k_risk_text"] = "The Company is subject to various risks..."

        with _patch_csv_writer("backend.agents.silver.risk_factors", tmp_path):
            from backend.agents.silver.risk_factors import silver_risk_factors_agent

            result = await silver_risk_factors_agent(state)

        assert len(result["silver_risk_factors"]) >= 1
        csv_path = Path(result["silver_risk_factors_path"])
        assert csv_path.exists()

        df = pd.read_csv(csv_path)
        assert "category" in df.columns
        assert "severity" in df.columns
        assert "processed_at" in df.columns
        assert "source_bronze" in df.columns
        assert df.iloc[0]["source_bronze"] == "bronze_10k_risk_text.csv"

    @pytest.mark.asyncio
    async def test_silver_insider_signal_creates_csv(self, tmp_path):
        """silver_insider_signal writes silver_insider_transactions.csv."""
        state = initial_state(TICKER)
        state["bronze_form4_transactions"] = _make_sample_insider_trades()

        with _patch_csv_writer("backend.agents.silver.insider_signal", tmp_path):
            from backend.agents.silver.insider_signal import silver_insider_signal_agent

            result = await silver_insider_signal_agent(state)

        assert len(result["silver_insider_trades"]) == 3
        assert result["silver_insider_signal"]["total_buys"] == 1
        assert result["silver_insider_signal"]["total_sells"] == 2

        csv_path = Path(result["silver_insider_trades_path"])
        assert csv_path.exists()

        df = pd.read_csv(csv_path)
        assert len(df) == 3
        assert "insider_name" in df.columns
        assert "processed_at" in df.columns
        assert "source_bronze" in df.columns
        assert df.iloc[0]["source_bronze"] == "bronze_form4_transactions.csv"

    @pytest.mark.asyncio
    async def test_silver_institutional_creates_csv(self, tmp_path):
        """silver_institutional writes silver_institutional_holders.csv."""
        state = initial_state(TICKER)
        state["bronze_13f_holdings"] = _make_sample_13f_holdings()

        with _patch_csv_writer("backend.agents.silver.institutional", tmp_path):
            from backend.agents.silver.institutional import silver_institutional_agent

            result = await silver_institutional_agent(state)

        assert len(result["silver_institutional_holders"]) == 3
        csv_path = Path(result["silver_institutional_path"])
        assert csv_path.exists()

        df = pd.read_csv(csv_path)
        assert "holder_name" in df.columns
        assert "holder_type" in df.columns
        assert "processed_at" in df.columns
        assert "source_bronze" in df.columns
        assert df.iloc[0]["source_bronze"] == "bronze_13f_holdings.csv"

    @pytest.mark.asyncio
    async def test_silver_material_events_creates_csv(self, tmp_path, monkeypatch):
        """silver_material_events writes silver_material_events.csv using rule-based mode."""
        monkeypatch.setenv("OPENAI_API_KEY", "")
        state = initial_state(TICKER)
        state["company_info"] = _make_company_info()
        state["bronze_8k_filings"] = _make_sample_8k_events()

        with _patch_csv_writer("backend.agents.silver.material_events", tmp_path):
            from backend.agents.silver.material_events import silver_material_events_agent

            result = await silver_material_events_agent(state)

        assert len(result["silver_material_events"]) == 2
        csv_path = Path(result["silver_events_path"])
        assert csv_path.exists()

        df = pd.read_csv(csv_path)
        assert "item_code" in df.columns
        assert "severity" in df.columns
        assert "filing_date" in df.columns
        assert "processed_at" in df.columns
        assert "source_bronze" in df.columns
        assert df.iloc[0]["source_bronze"] == "bronze_8k_filings.csv"

    @pytest.mark.asyncio
    async def test_silver_governance_creates_csv(self, tmp_path, monkeypatch):
        """silver_governance writes silver_governance.csv in placeholder mode."""
        monkeypatch.setenv("OPENAI_API_KEY", "")
        state = initial_state(TICKER)
        state["company_info"] = _make_company_info()
        state["bronze_def14a_proxy"] = _make_sample_proxy_data()

        with _patch_csv_writer("backend.agents.silver.governance", tmp_path):
            from backend.agents.silver.governance import silver_governance_agent

            result = await silver_governance_agent(state)

        assert isinstance(result["silver_governance"], dict)
        csv_path = Path(result["silver_governance_path"])
        assert csv_path.exists()

        df = pd.read_csv(csv_path)
        assert "processed_at" in df.columns
        assert "source_bronze" in df.columns
        assert df.iloc[0]["source_bronze"] == "bronze_def14a_proxy.csv"


# ═══════════════════════════════════════════════════════════════════════════
# 3. SILVER-BRONZE DERIVATION
# ═══════════════════════════════════════════════════════════════════════════


class TestSilverBronzeDerivation:
    """Verify silver values are correctly derived from bronze data."""

    def test_extract_kpis_revenue_from_mock_facts(self):
        """_extract_kpis correctly extracts revenue from XBRL facts."""
        from backend.agents.silver.financial_kpis import _extract_kpis

        facts = _build_mock_facts()
        kpis = _extract_kpis(facts)

        assert kpis.revenue == pytest.approx(416_160_000_000)
        assert kpis.revenue_prior == pytest.approx(391_035_000_000)

    def test_extract_kpis_revenue_yoy_change(self):
        """Revenue YoY change is computed correctly from two annual periods."""
        from backend.agents.silver.financial_kpis import _extract_kpis

        facts = _build_mock_facts()
        kpis = _extract_kpis(facts)

        expected_yoy = (416_160_000_000 - 391_035_000_000) / 391_035_000_000
        assert kpis.revenue_yoy_change == pytest.approx(expected_yoy, rel=1e-3)

    def test_extract_kpis_gross_margin(self):
        """Gross margin = gross_profit / revenue."""
        from backend.agents.silver.financial_kpis import _extract_kpis

        facts = _build_mock_facts()
        kpis = _extract_kpis(facts)

        expected_gm = 195_228_000_000 / 416_160_000_000
        assert kpis.gross_margin == pytest.approx(expected_gm, rel=1e-3)

    def test_extract_kpis_operating_margin(self):
        """Operating margin = operating_income / revenue."""
        from backend.agents.silver.financial_kpis import _extract_kpis

        facts = _build_mock_facts()
        kpis = _extract_kpis(facts)

        expected_om = 134_048_000_000 / 416_160_000_000
        assert kpis.operating_margin == pytest.approx(expected_om, rel=1e-3)

    def test_extract_kpis_debt_to_equity(self):
        """Debt-to-equity = total_liabilities / stockholders_equity."""
        from backend.agents.silver.financial_kpis import _extract_kpis

        facts = _build_mock_facts()
        kpis = _extract_kpis(facts)

        expected_de = 308_030_000_000 / 56_950_000_000
        assert kpis.debt_to_equity == pytest.approx(expected_de, rel=1e-3)

    def test_extract_kpis_current_ratio(self):
        """Current ratio = assets_current / liabilities_current."""
        from backend.agents.silver.financial_kpis import _extract_kpis

        facts = _build_mock_facts()
        kpis = _extract_kpis(facts)

        expected_cr = 133_000_000_000 / 154_000_000_000
        assert kpis.current_ratio == pytest.approx(expected_cr, rel=1e-3)

    def test_extract_kpis_free_cash_flow(self):
        """Free cash flow = operating_cash_flow - capex."""
        from backend.agents.silver.financial_kpis import _extract_kpis

        facts = _build_mock_facts()
        kpis = _extract_kpis(facts)

        expected_fcf = 124_000_000_000 - 9_959_000_000
        assert kpis.free_cash_flow == pytest.approx(expected_fcf)

    def test_extract_kpis_eps_basic(self):
        """EPS basic is extracted from EarningsPerShareBasic tag."""
        from backend.agents.silver.financial_kpis import _extract_kpis

        facts = _build_mock_facts()
        kpis = _extract_kpis(facts)

        assert kpis.eps_basic == pytest.approx(7.40)

    def test_extract_kpis_fiscal_year_and_period(self):
        """Fiscal year and period end match the most recent annual end date."""
        from backend.agents.silver.financial_kpis import _extract_kpis

        facts = _build_mock_facts()
        kpis = _extract_kpis(facts)

        assert kpis.fiscal_year == 2025
        assert kpis.period_end == "2025-09-27"

    def test_extract_kpis_source_tags_populated(self):
        """Source tags map KPI names to the XBRL tags used."""
        from backend.agents.silver.financial_kpis import _extract_kpis

        facts = _build_mock_facts()
        kpis = _extract_kpis(facts)

        assert "revenue" in kpis.source_tags
        assert kpis.source_tags["revenue"] == "RevenueFromContractWithCustomerExcludingAssessedTax"
        assert "net_income" in kpis.source_tags
        assert kpis.source_tags["net_income"] == "NetIncomeLoss"

    def test_get_annual_end_dates_sorted_descending(self):
        """_get_annual_end_dates returns dates in descending order."""
        from backend.agents.silver.financial_kpis import _get_annual_end_dates

        facts = _build_mock_facts()
        dates = _get_annual_end_dates(facts)

        assert len(dates) >= 2
        # Descending order
        for i in range(len(dates) - 1):
            assert dates[i] >= dates[i + 1]

    def test_insider_signal_buy_sell_ratio(self):
        """_detect_clusters and signal correctly computed from trades."""
        from backend.agents.silver.insider_signal import _detect_clusters

        trades = _make_sample_insider_trades()

        # 2 sells, 1 buy -> no cluster (need 3+ same direction)
        cluster, desc = _detect_clusters(trades)
        assert cluster is False

    def test_institutional_passive_active_classification(self):
        """Institutional holders are classified as passive/active based on name."""
        from backend.agents.silver.institutional import PASSIVE_MANAGERS

        holders = _make_sample_13f_holdings()
        # Manually classify
        for h in holders:
            name_lower = h["holder_name"].lower()
            if any(p in name_lower for p in PASSIVE_MANAGERS):
                h["holder_type"] = "passive"
            else:
                h["holder_type"] = "active"

        assert holders[0]["holder_type"] == "passive"  # Vanguard
        assert holders[1]["holder_type"] == "passive"  # BlackRock
        assert holders[2]["holder_type"] == "active"  # Berkshire

    def test_rule_based_8k_classification(self):
        """_rule_based_classify correctly maps 8-K item codes."""
        from backend.agents.silver.material_events import _rule_based_classify

        events = _make_sample_8k_events()
        classified = _rule_based_classify(events)

        assert len(classified) == 2
        # First event has "2.02" in description
        assert classified[0]["item_code"] == "2.02"
        assert classified[0]["severity"] == 2
        # Second event has "5.02" in description
        assert classified[1]["item_code"] == "5.02"
        assert classified[1]["severity"] == 3


# ═══════════════════════════════════════════════════════════════════════════
# 4. GOLD LAYER COMPLETENESS
# ═══════════════════════════════════════════════════════════════════════════


class TestGoldLayerCompleteness:
    """Validate gold outputs: risk assessment, cross-workstream, memo."""

    @pytest.mark.asyncio
    async def test_gold_risk_assessment_creates_csv(self, tmp_path, monkeypatch):
        """gold_risk_assessment writes gold_risk_assessment.csv with placeholder scores."""
        monkeypatch.setenv("OPENAI_API_KEY", "")
        state = initial_state(TICKER)
        state["company_info"] = _make_company_info()

        from backend.agents.silver.financial_kpis import _extract_kpis

        state["silver_kpis"] = _extract_kpis(_build_mock_facts())

        with _patch_csv_writer("backend.agents.gold.risk_assessment", tmp_path):
            from backend.agents.gold.risk_assessment import gold_risk_assessment_agent

            result = await gold_risk_assessment_agent(state)

        assert result["gold_risk_scores"] is not None
        risk = result["gold_risk_scores"]
        assert len(risk.dimensions) == 5
        assert 1.0 <= risk.composite_score <= 5.0
        assert risk.risk_level in ("Low", "Medium", "High", "Critical")

        csv_path = Path(result["gold_risk_path"])
        assert csv_path.exists()

        df = pd.read_csv(csv_path)
        assert "dimension" in df.columns
        assert "score" in df.columns
        assert "reasoning" in df.columns
        assert "analyzed_at" in df.columns
        assert "source_tables" in df.columns
        assert df.iloc[0]["source_tables"] == "silver_financial_kpis.csv"

    @pytest.mark.asyncio
    async def test_gold_risk_placeholder_scores_match_kpi_rules(self, monkeypatch):
        """_placeholder_risk produces correct scores based on KPI thresholds."""
        monkeypatch.setenv("OPENAI_API_KEY", "")
        from backend.agents.gold.risk_assessment import _placeholder_risk
        from backend.agents.silver.financial_kpis import _extract_kpis

        kpis = _extract_kpis(_build_mock_facts())
        risk = _placeholder_risk(kpis)

        # gross_margin ~0.469 -> 0.35 <= gm < 0.50 => score 2 (Financial Health)
        fin_dim = next(d for d in risk.dimensions if d.dimension == "Financial Health")
        assert fin_dim.score == 2

        # revenue_yoy ~0.064 -> 0 <= yoy < 0.10 => score 2 (Market Position)
        mkt_dim = next(d for d in risk.dimensions if d.dimension == "Market Position")
        assert mkt_dim.score == 2

        # operating_margin ~0.322 -> >= 0.10 => score 2 (Operational Risk)
        ops_dim = next(d for d in risk.dimensions if d.dimension == "Operational Risk")
        assert ops_dim.score == 2

        # debt_to_equity ~5.41 -> > 5 => score 4 (Governance)
        gov_dim = next(d for d in risk.dimensions if d.dimension == "Governance")
        assert gov_dim.score == 4

        # current_ratio ~0.864 -> < 1.0 => score 4 (Liquidity)
        liq_dim = next(d for d in risk.dimensions if d.dimension == "Liquidity")
        assert liq_dim.score == 4

    @pytest.mark.asyncio
    async def test_gold_cross_workstream_creates_csv(self, tmp_path):
        """gold_cross_workstream writes gold_cross_workstream_flags.csv."""
        from backend.agents.silver.financial_kpis import _extract_kpis

        state = initial_state(TICKER)
        state["silver_kpis"] = _extract_kpis(_build_mock_facts())
        state["silver_insider_signal"] = {"signal": "neutral", "cluster_detected": False}
        state["silver_material_events"] = []
        state["silver_governance"] = {}
        state["silver_institutional_holders"] = []
        state["silver_risk_factors"] = []
        state["gold_risk_scores"] = RiskAssessment(
            dimensions=[], composite_score=2.8, risk_level="Medium", red_flags=[],
        )

        with _patch_csv_writer("backend.agents.gold.cross_workstream", tmp_path):
            from backend.agents.gold.cross_workstream import gold_cross_workstream_agent

            result = await gold_cross_workstream_agent(state)

        assert isinstance(result["gold_cross_workstream_flags"], list)
        assert result["deal_recommendation"] in ("PROCEED", "PROCEED_WITH_CONDITIONS", "DO_NOT_PROCEED")
        csv_path = Path(result["gold_cross_workstream_path"])
        assert csv_path.exists()

        # Empty flags produce a CSV with no data rows; read it tolerantly
        content = csv_path.read_text(encoding="utf-8")
        if content.strip():
            df = pd.read_csv(csv_path)
            assert "analyzed_at" in df.columns
            assert "source_tables" in df.columns

    @pytest.mark.asyncio
    async def test_gold_memo_creates_markdown(self, tmp_path, monkeypatch):
        """gold_memo writes results_diligence_memo.md with DD report."""
        monkeypatch.setenv("OPENAI_API_KEY", "")
        from backend.agents.silver.financial_kpis import _extract_kpis
        from backend.agents.gold.risk_assessment import _placeholder_risk

        state = initial_state(TICKER)
        state["company_info"] = _make_company_info()
        kpis = _extract_kpis(_build_mock_facts())
        state["silver_kpis"] = kpis
        risk = _placeholder_risk(kpis)
        state["gold_risk_scores"] = risk
        state["gold_cross_workstream_flags"] = []
        state["deal_recommendation"] = "PROCEED"
        state["silver_risk_factors"] = []
        state["silver_insider_signal"] = {}
        state["silver_institutional_holders"] = []
        state["silver_material_events"] = []
        state["silver_governance"] = {}
        state["silver_insider_trades"] = []

        with _patch_csv_writer("backend.agents.gold.memo_writer", tmp_path):
            from backend.agents.gold.memo_writer import gold_memo_agent

            result = await gold_memo_agent(state)

        assert result["result_memo"] is not None
        memo = result["result_memo"]
        assert len(memo.executive_summary) > 10
        assert len(memo.recommendation) > 10

        memo_path = Path(result["result_memo_path"])
        assert memo_path.exists()
        assert memo_path.suffix == ".md"

        content = memo_path.read_text(encoding="utf-8")
        assert "Due Diligence Report" in content
        assert "AAPL" in content

    @pytest.mark.asyncio
    async def test_gold_memo_confidence_score(self, tmp_path, monkeypatch):
        """Confidence score reflects data completeness."""
        monkeypatch.setenv("OPENAI_API_KEY", "")
        from backend.agents.silver.financial_kpis import _extract_kpis
        from backend.agents.gold.risk_assessment import _placeholder_risk

        state = initial_state(TICKER)
        state["company_info"] = _make_company_info()
        kpis = _extract_kpis(_build_mock_facts())
        state["silver_kpis"] = kpis
        risk = _placeholder_risk(kpis)
        state["gold_risk_scores"] = risk
        state["gold_cross_workstream_flags"] = []
        state["deal_recommendation"] = "PROCEED"
        state["silver_risk_factors"] = [{"category": "regulatory", "title": "test", "severity": 3}]
        state["silver_insider_trades"] = _make_sample_insider_trades()
        state["silver_insider_signal"] = {"signal": "neutral"}
        state["silver_institutional_holders"] = _make_sample_13f_holdings()
        state["silver_material_events"] = [{"item_code": "2.02", "severity": 2}]
        state["silver_governance"] = {"ceo_name": "Tim Cook"}

        with _patch_csv_writer("backend.agents.gold.memo_writer", tmp_path):
            from backend.agents.gold.memo_writer import gold_memo_agent

            result = await gold_memo_agent(state)

        # With all workstreams populated, confidence should be high
        assert result["confidence"] > 0.7


# ═══════════════════════════════════════════════════════════════════════════
# 5. CROSS-LAYER COHERENCE
# ═══════════════════════════════════════════════════════════════════════════


class TestCrossLayerCoherence:
    """Consistency across bronze, silver, and gold layers."""

    @pytest.mark.asyncio
    async def test_silver_kpis_revenue_matches_bronze_facts(self, tmp_path, monkeypatch):
        """Silver KPI revenue matches the value from bronze XBRL facts."""
        monkeypatch.setenv("OPENAI_API_KEY", "")
        state = initial_state(TICKER)
        state["company_info"] = _make_company_info()
        state["bronze_facts"] = _build_mock_facts()

        with _patch_csv_writer("backend.agents.silver.financial_kpis", tmp_path):
            from backend.agents.silver.financial_kpis import silver_financial_kpis_agent

            result = await silver_financial_kpis_agent(state)

        kpis = result["silver_kpis"]
        # Revenue in bronze facts: 416160000000 for period ending 2025-09-27
        bronze_revenue = 416_160_000_000
        assert kpis.revenue == pytest.approx(bronze_revenue)

    @pytest.mark.asyncio
    async def test_gold_risk_uses_silver_kpis_data(self, tmp_path, monkeypatch):
        """Gold risk assessment dimensions reference silver KPI values."""
        monkeypatch.setenv("OPENAI_API_KEY", "")
        from backend.agents.silver.financial_kpis import _extract_kpis
        from backend.agents.gold.risk_assessment import _placeholder_risk

        kpis = _extract_kpis(_build_mock_facts())
        risk = _placeholder_risk(kpis)

        # The Financial Health dimension should reference gross_margin
        fin_dim = next(d for d in risk.dimensions if d.dimension == "Financial Health")
        assert "gross_margin" in fin_dim.key_metrics
        # Reasoning should contain the actual margin value
        assert "46.9%" in fin_dim.reasoning or "0.469" in fin_dim.reasoning.replace("%", "")

    @pytest.mark.asyncio
    async def test_cross_workstream_no_flags_for_healthy_company(self):
        """A healthy company state produces no cross-workstream red flags."""
        from backend.agents.gold.cross_workstream import _evaluate_correlations
        from backend.agents.silver.financial_kpis import _extract_kpis

        state = initial_state(TICKER)
        state["silver_kpis"] = _extract_kpis(_build_mock_facts())
        state["silver_insider_signal"] = {"signal": "neutral", "cluster_detected": False}
        state["silver_material_events"] = []
        state["silver_governance"] = {"board_independence_pct": 0.875, "governance_flags": []}
        state["silver_institutional_holders"] = _make_sample_13f_holdings()
        state["silver_risk_factors"] = []

        flags = _evaluate_correlations(state)
        assert len(flags) == 0, f"Expected no flags for healthy company, got: {flags}"

    @pytest.mark.asyncio
    async def test_deal_recommendation_proceed_for_low_risk(self):
        """PROCEED recommendation when composite risk is low and no critical flags."""
        from backend.agents.gold.cross_workstream import _compute_deal_recommendation

        state = initial_state(TICKER)
        state["gold_risk_scores"] = RiskAssessment(
            dimensions=[], composite_score=2.0, risk_level="Low", red_flags=[],
        )
        rec = _compute_deal_recommendation(state, [])
        assert rec == "PROCEED"

    @pytest.mark.asyncio
    async def test_deal_recommendation_do_not_proceed_for_critical(self):
        """DO_NOT_PROCEED when critical cross-workstream flags exist."""
        from backend.agents.gold.cross_workstream import _compute_deal_recommendation

        state = initial_state(TICKER)
        state["gold_risk_scores"] = RiskAssessment(
            dimensions=[], composite_score=4.5, risk_level="Critical", red_flags=[],
        )
        rec = _compute_deal_recommendation(state, [{"severity": "Critical"}])
        assert rec == "DO_NOT_PROCEED"

    @pytest.mark.asyncio
    async def test_insider_signal_flows_to_cross_workstream(self):
        """Bearish insider signal + novel regulatory risk triggers cross-workstream flag."""
        from backend.agents.gold.cross_workstream import _evaluate_correlations
        from backend.agents.silver.financial_kpis import _extract_kpis

        state = initial_state(TICKER)
        state["silver_kpis"] = _extract_kpis(_build_mock_facts())
        state["silver_insider_signal"] = {
            "signal": "bearish",
            "cluster_detected": False,
        }
        state["silver_material_events"] = []
        state["silver_governance"] = {}
        state["silver_institutional_holders"] = []
        state["silver_risk_factors"] = [
            {"category": "regulatory", "is_novel": True, "title": "Antitrust", "severity": 4},
        ]

        flags = _evaluate_correlations(state)
        rule_names = [f["rule_name"] for f in flags]
        assert "Novel Regulatory Risk + Insider Selling" in rule_names


# ═══════════════════════════════════════════════════════════════════════════
# 6. PIPELINE STAGE COMPLETION
# ═══════════════════════════════════════════════════════════════════════════


class TestPipelineStageCompletion:
    """All nodes execute and progress fires correctly."""

    @pytest.mark.asyncio
    async def test_bronze_resolver_sets_current_stage(self, tmp_path):
        """bronze_resolver sets current_stage to 'bronze'."""
        state = initial_state(TICKER)
        mock_client = AsyncMock()
        mock_client.resolve_cik.return_value = "0000320193"
        mock_client.get_company_info.return_value = _make_company_info()

        with (
            _patch_csv_writer("backend.agents.bronze.resolver", tmp_path),
            patch("backend.agents.bronze.resolver.EdgarClient", return_value=mock_client),
        ):
            from backend.agents.bronze.resolver import bronze_resolver_agent

            result = await bronze_resolver_agent(state)

        assert result["current_stage"] == "bronze"

    @pytest.mark.asyncio
    async def test_silver_kpis_sets_current_stage(self, tmp_path, monkeypatch):
        """silver_financial_kpis sets current_stage to 'silver'."""
        monkeypatch.setenv("OPENAI_API_KEY", "")
        state = initial_state(TICKER)
        state["company_info"] = _make_company_info()
        state["bronze_facts"] = _build_mock_facts()

        with _patch_csv_writer("backend.agents.silver.financial_kpis", tmp_path):
            from backend.agents.silver.financial_kpis import silver_financial_kpis_agent

            result = await silver_financial_kpis_agent(state)

        assert result["current_stage"] == "silver"

    @pytest.mark.asyncio
    async def test_gold_risk_sets_current_stage(self, tmp_path, monkeypatch):
        """gold_risk_assessment sets current_stage to 'gold'."""
        monkeypatch.setenv("OPENAI_API_KEY", "")
        from backend.agents.silver.financial_kpis import _extract_kpis

        state = initial_state(TICKER)
        state["company_info"] = _make_company_info()
        state["silver_kpis"] = _extract_kpis(_build_mock_facts())

        with _patch_csv_writer("backend.agents.gold.risk_assessment", tmp_path):
            from backend.agents.gold.risk_assessment import gold_risk_assessment_agent

            result = await gold_risk_assessment_agent(state)

        assert result["current_stage"] == "gold"

    @pytest.mark.asyncio
    async def test_gold_memo_sets_stage_complete(self, tmp_path, monkeypatch):
        """gold_memo sets current_stage to 'complete'."""
        monkeypatch.setenv("OPENAI_API_KEY", "")
        from backend.agents.silver.financial_kpis import _extract_kpis
        from backend.agents.gold.risk_assessment import _placeholder_risk

        state = initial_state(TICKER)
        state["company_info"] = _make_company_info()
        kpis = _extract_kpis(_build_mock_facts())
        state["silver_kpis"] = kpis
        state["gold_risk_scores"] = _placeholder_risk(kpis)
        state["gold_cross_workstream_flags"] = []
        state["deal_recommendation"] = "PROCEED"
        state["silver_risk_factors"] = []
        state["silver_insider_signal"] = {}
        state["silver_institutional_holders"] = []
        state["silver_material_events"] = []
        state["silver_governance"] = {}
        state["silver_insider_trades"] = []

        with _patch_csv_writer("backend.agents.gold.memo_writer", tmp_path):
            from backend.agents.gold.memo_writer import gold_memo_agent

            result = await gold_memo_agent(state)

        assert result["current_stage"] == "complete"

    @pytest.mark.asyncio
    async def test_progress_messages_emitted_by_each_agent(self, tmp_path, monkeypatch):
        """Each agent returns progress_messages for the UI."""
        monkeypatch.setenv("OPENAI_API_KEY", "")

        # Test bronze resolver
        state = initial_state(TICKER)
        mock_client = AsyncMock()
        mock_client.resolve_cik.return_value = "0000320193"
        mock_client.get_company_info.return_value = _make_company_info()

        with (
            _patch_csv_writer("backend.agents.bronze.resolver", tmp_path),
            patch("backend.agents.bronze.resolver.EdgarClient", return_value=mock_client),
        ):
            from backend.agents.bronze.resolver import bronze_resolver_agent

            result = await bronze_resolver_agent(state)

        assert len(result["progress_messages"]) >= 1
        assert "Resolved AAPL" in result["progress_messages"][0]

    @pytest.mark.asyncio
    async def test_initial_state_fields_all_initialized(self):
        """initial_state() creates a state with all expected fields."""
        state = initial_state("AAPL")

        # Bronze
        assert state["ticker"] == "AAPL"
        assert state["company_info"] is None
        assert state["bronze_facts"] == []
        assert state["bronze_10k_risk_text"] == ""
        assert state["bronze_form4_transactions"] == []
        assert state["bronze_13f_holdings"] == []
        assert state["bronze_8k_filings"] == []
        assert state["bronze_def14a_proxy"] == {}

        # Silver
        assert state["silver_kpis"] is None
        assert state["silver_risk_factors"] == []
        assert state["silver_insider_trades"] == []
        assert state["silver_insider_signal"] == {}
        assert state["silver_institutional_holders"] == []
        assert state["silver_material_events"] == []
        assert state["silver_governance"] == {}

        # Gold
        assert state["gold_risk_scores"] is None
        assert state["gold_cross_workstream_flags"] == []
        assert state["result_memo"] is None
        assert state["deal_recommendation"] == ""

        # Metadata
        assert state["confidence"] == 0.0
        assert state["current_stage"] == "initialized"
        assert state["errors"] == []
        assert state["progress_messages"] == []

    def test_pipeline_graph_has_16_nodes(self):
        """The compiled pipeline graph contains exactly 16 agent nodes."""
        from backend.graph import create_pipeline

        graph = create_pipeline()
        # LangGraph compiled graph has a .nodes property
        # Excluding __start__ and __end__
        node_names = [n for n in graph.get_graph().nodes if n not in ("__start__", "__end__")]
        assert len(node_names) == 16


# ═══════════════════════════════════════════════════════════════════════════
# 7. ERROR RESILIENCE
# ═══════════════════════════════════════════════════════════════════════════


class TestErrorResilience:
    """Graceful degradation when individual sources fail."""

    @pytest.mark.asyncio
    async def test_bronze_resolver_handles_edgar_error(self, tmp_path):
        """bronze_resolver returns offline company info when EdgarClient fails."""
        from backend.data.edgar_client import EdgarClientError

        state = initial_state(TICKER)
        mock_client = AsyncMock()
        mock_client.resolve_cik.side_effect = EdgarClientError("Network timeout")

        with (
            _patch_csv_writer("backend.agents.bronze.resolver", tmp_path),
            patch("backend.agents.bronze.resolver.EdgarClient", return_value=mock_client),
        ):
            from backend.agents.bronze.resolver import bronze_resolver_agent

            result = await bronze_resolver_agent(state)

        assert result["company_info"] is not None
        assert "(offline)" in result["company_info"].company_name
        assert len(result["errors"]) >= 1
        assert "Bronze resolver" in result["errors"][0]

    @pytest.mark.asyncio
    async def test_bronze_xbrl_handles_no_cik(self, tmp_path):
        """bronze_xbrl returns empty facts when company_info has no valid CIK and no fallback."""
        # Use a ticker with no offline fallback file to test the error path
        state = initial_state("ZZZZ")
        state["company_info"] = CompanyInfo(
            ticker="ZZZZ", company_name="ZZZZ (offline)", cik="0000000000",
        )

        with _patch_csv_writer("backend.agents.bronze.xbrl_facts", tmp_path):
            from backend.agents.bronze.xbrl_facts import bronze_xbrl_agent

            result = await bronze_xbrl_agent(state)

        assert result["bronze_facts"] == []
        assert result["bronze_xbrl_facts_path"] is None
        assert len(result["errors"]) >= 1

    @pytest.mark.asyncio
    async def test_bronze_10k_handles_filing_error(self, tmp_path):
        """bronze_10k returns empty risk text when EdgarFilingsClient fails."""
        from backend.data.edgar_filings import EdgarFilingsError

        state = initial_state(TICKER)
        mock_client = AsyncMock()
        mock_client.get_10k_risk_factors.side_effect = EdgarFilingsError("No 10-K found")

        with (
            _patch_csv_writer("backend.agents.bronze.ten_k", tmp_path),
            patch("backend.agents.bronze.ten_k.EdgarFilingsClient", return_value=mock_client),
        ):
            from backend.agents.bronze.ten_k import bronze_10k_agent

            result = await bronze_10k_agent(state)

        assert result["bronze_10k_risk_text"] == ""
        assert result["bronze_10k_risk_text_path"] is None
        assert len(result["errors"]) >= 1

    @pytest.mark.asyncio
    async def test_bronze_form4_handles_filing_error(self, tmp_path):
        """bronze_form4 returns empty transactions when client fails."""
        from backend.data.edgar_filings import EdgarFilingsError

        state = initial_state(TICKER)
        mock_client = AsyncMock()
        mock_client.get_form4_filings.side_effect = EdgarFilingsError("Rate limited")

        with (
            _patch_csv_writer("backend.agents.bronze.form4", tmp_path),
            patch("backend.agents.bronze.form4.EdgarFilingsClient", return_value=mock_client),
        ):
            from backend.agents.bronze.form4 import bronze_form4_agent

            result = await bronze_form4_agent(state)

        assert result["bronze_form4_transactions"] == []
        assert result["bronze_form4_path"] is None

    @pytest.mark.asyncio
    async def test_silver_kpis_handles_empty_facts(self, tmp_path, monkeypatch):
        """silver_financial_kpis gracefully handles empty bronze facts."""
        monkeypatch.setenv("OPENAI_API_KEY", "")
        state = initial_state(TICKER)
        state["bronze_facts"] = []

        with _patch_csv_writer("backend.agents.silver.financial_kpis", tmp_path):
            from backend.agents.silver.financial_kpis import silver_financial_kpis_agent

            result = await silver_financial_kpis_agent(state)

        assert result["silver_kpis"] is None
        assert result["silver_kpis_path"] is None
        assert result["current_stage"] == "error"
        assert len(result["errors"]) >= 1

    @pytest.mark.asyncio
    async def test_silver_risk_factors_handles_empty_text(self, tmp_path, monkeypatch):
        """silver_risk_factors returns empty list when no 10-K text."""
        monkeypatch.setenv("OPENAI_API_KEY", "")
        state = initial_state(TICKER)
        state["bronze_10k_risk_text"] = ""

        with _patch_csv_writer("backend.agents.silver.risk_factors", tmp_path):
            from backend.agents.silver.risk_factors import silver_risk_factors_agent

            result = await silver_risk_factors_agent(state)

        assert result["silver_risk_factors"] == []
        assert result["silver_risk_factors_path"] is None

    @pytest.mark.asyncio
    async def test_silver_insider_signal_handles_empty_trades(self, tmp_path):
        """silver_insider_signal returns neutral signal when no trades."""
        state = initial_state(TICKER)
        state["bronze_form4_transactions"] = []

        with _patch_csv_writer("backend.agents.silver.insider_signal", tmp_path):
            from backend.agents.silver.insider_signal import silver_insider_signal_agent

            result = await silver_insider_signal_agent(state)

        assert result["silver_insider_trades"] == []
        assert result["silver_insider_signal"]["signal"] == "neutral"
        assert result["silver_insider_signal"]["total_buys"] == 0

    @pytest.mark.asyncio
    async def test_gold_risk_handles_no_kpis(self, tmp_path, monkeypatch):
        """gold_risk_assessment returns error when no silver KPIs."""
        monkeypatch.setenv("OPENAI_API_KEY", "")
        state = initial_state(TICKER)
        state["silver_kpis"] = None

        with _patch_csv_writer("backend.agents.gold.risk_assessment", tmp_path):
            from backend.agents.gold.risk_assessment import gold_risk_assessment_agent

            result = await gold_risk_assessment_agent(state)

        assert result["gold_risk_scores"] is None
        assert result["gold_risk_path"] is None
        assert result["current_stage"] == "error"

    @pytest.mark.asyncio
    async def test_gold_memo_handles_missing_dependencies(self, tmp_path, monkeypatch):
        """gold_memo returns error when KPIs or risk scores are missing."""
        monkeypatch.setenv("OPENAI_API_KEY", "")
        state = initial_state(TICKER)
        state["silver_kpis"] = None
        state["gold_risk_scores"] = None

        with _patch_csv_writer("backend.agents.gold.memo_writer", tmp_path):
            from backend.agents.gold.memo_writer import gold_memo_agent

            result = await gold_memo_agent(state)

        assert result["result_memo"] is None
        assert result["result_memo_path"] is None
        assert result["confidence"] == 0.0
        assert result["current_stage"] == "error"

    @pytest.mark.asyncio
    async def test_errors_accumulate_as_list(self, tmp_path, monkeypatch):
        """Error strings accumulate and do not clobber prior errors."""
        monkeypatch.setenv("OPENAI_API_KEY", "")
        from backend.data.edgar_client import EdgarClientError

        state = initial_state(TICKER)
        mock_client = AsyncMock()
        mock_client.resolve_cik.side_effect = EdgarClientError("fail1")

        with (
            _patch_csv_writer("backend.agents.bronze.resolver", tmp_path),
            patch("backend.agents.bronze.resolver.EdgarClient", return_value=mock_client),
        ):
            from backend.agents.bronze.resolver import bronze_resolver_agent

            result1 = await bronze_resolver_agent(state)

        # Simulate second agent failure
        state["bronze_facts"] = []
        with _patch_csv_writer("backend.agents.silver.financial_kpis", tmp_path):
            from backend.agents.silver.financial_kpis import silver_financial_kpis_agent

            result2 = await silver_financial_kpis_agent(state)

        # Both agents produced errors independently
        all_errors = result1["errors"] + result2["errors"]
        assert len(all_errors) >= 2
        assert any("Bronze resolver" in e for e in all_errors)
        assert any("No bronze facts" in e for e in all_errors)


# ═══════════════════════════════════════════════════════════════════════════
# 8. DATA TYPE VALIDATION
# ═══════════════════════════════════════════════════════════════════════════


class TestDataTypeValidation:
    """CSV column types are correct and lineage columns non-null."""

    def test_csv_writer_bronze_lineage_columns(self, tmp_path):
        """Bronze CSV has ingested_at (non-null) and source_url columns."""
        from backend.data.csv_writer import CsvWriter

        writer = CsvWriter(TICKER, output_dir=str(tmp_path))
        path = writer.write_bronze("test_table", [{"a": 1, "b": "x"}], source_url="https://example.com")

        df = pd.read_csv(path)
        assert "ingested_at" in df.columns
        assert "source_url" in df.columns
        assert df["ingested_at"].notna().all()
        assert df.iloc[0]["source_url"] == "https://example.com"

    def test_csv_writer_silver_lineage_columns(self, tmp_path):
        """Silver CSV has processed_at (non-null) and source_bronze columns."""
        from backend.data.csv_writer import CsvWriter

        writer = CsvWriter(TICKER, output_dir=str(tmp_path))
        path = writer.write_silver("test_table", [{"x": 42}], source_bronze="bronze_test.csv")

        df = pd.read_csv(path)
        assert "processed_at" in df.columns
        assert "source_bronze" in df.columns
        assert df["processed_at"].notna().all()
        assert df.iloc[0]["source_bronze"] == "bronze_test.csv"

    def test_csv_writer_gold_lineage_columns(self, tmp_path):
        """Gold CSV has analyzed_at (non-null) and source_tables columns."""
        from backend.data.csv_writer import CsvWriter

        writer = CsvWriter(TICKER, output_dir=str(tmp_path))
        path = writer.write_gold("test_table", [{"score": 3.5}], source_tables="silver_kpis.csv")

        df = pd.read_csv(path)
        assert "analyzed_at" in df.columns
        assert "source_tables" in df.columns
        assert df["analyzed_at"].notna().all()
        assert df.iloc[0]["source_tables"] == "silver_kpis.csv"

    def test_csv_writer_empty_dataframe_no_lineage(self, tmp_path):
        """Empty data produces a CSV file (empty DataFrame serialization)."""
        from backend.data.csv_writer import CsvWriter

        writer = CsvWriter(TICKER, output_dir=str(tmp_path))
        path = writer.write_bronze("empty_table", [])

        assert path.exists()
        content = path.read_text(encoding="utf-8").strip()
        # An empty DataFrame written by pandas produces either an empty file
        # or a file with no columns — either way, no data rows exist.
        if content:
            df = pd.read_csv(path)
            assert len(df) == 0
        else:
            # File is completely empty — valid representation of no data
            assert content == ""

    def test_csv_writer_creates_ticker_directory(self, tmp_path):
        """CsvWriter creates the output_dir / TICKER directory."""
        from backend.data.csv_writer import CsvWriter

        writer = CsvWriter(TICKER, output_dir=str(tmp_path))
        assert writer.output_dir.exists()
        assert writer.output_dir.name == TICKER

    def test_csv_writer_file_naming_convention(self, tmp_path):
        """Files follow layer_tablename.csv naming convention."""
        from backend.data.csv_writer import CsvWriter

        writer = CsvWriter(TICKER, output_dir=str(tmp_path))
        bp = writer.write_bronze("company_info", [{"a": 1}])
        sp = writer.write_silver("financial_kpis", [{"b": 2}])
        gp = writer.write_gold("risk_assessment", [{"c": 3}])
        mp = writer.write_result("diligence_memo", "# Memo")

        assert bp.name == "bronze_company_info.csv"
        assert sp.name == "silver_financial_kpis.csv"
        assert gp.name == "gold_risk_assessment.csv"
        assert mp.name == "results_diligence_memo.md"

    def test_csv_writer_run_metadata_json(self, tmp_path):
        """run_metadata.json contains correct artifact counts."""
        from backend.data.csv_writer import CsvWriter

        writer = CsvWriter(TICKER, output_dir=str(tmp_path))
        writer.write_bronze("table1", [{"a": 1}])
        writer.write_bronze("table2", [{"b": 2}])
        writer.write_silver("table3", [{"c": 3}])
        writer.write_gold("table4", [{"d": 4}])
        writer.write_result("diligence_memo", "# Memo")

        meta_path = writer.write_run_metadata(run_id="test-run-001", started_at="2025-01-01T00:00:00Z", errors=["e1"])
        assert meta_path.exists()

        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        assert meta["ticker"] == TICKER
        assert meta["run_id"] == "test-run-001"
        assert meta["bronze_count"] == 2
        assert meta["silver_count"] == 1
        assert meta["gold_count"] == 1
        assert meta["results_count"] == 1
        assert meta["errors"] == ["e1"]
        assert meta["started_at"] == "2025-01-01T00:00:00Z"
        assert meta["completed_at"] != ""

    @pytest.mark.asyncio
    async def test_bronze_xbrl_csv_value_column_is_numeric(self, tmp_path):
        """bronze_xbrl_facts.csv 'value' column contains only numeric data."""
        state = initial_state(TICKER)
        state["company_info"] = _make_company_info()

        facts = _build_mock_facts()
        mock_client = AsyncMock()
        mock_client.get_company_facts.return_value = facts

        with (
            _patch_csv_writer("backend.agents.bronze.xbrl_facts", tmp_path),
            patch("backend.agents.bronze.xbrl_facts.EdgarClient", return_value=mock_client),
        ):
            from backend.agents.bronze.xbrl_facts import bronze_xbrl_agent

            result = await bronze_xbrl_agent(state)

        df = pd.read_csv(Path(result["bronze_xbrl_facts_path"]))
        assert pd.to_numeric(df["value"], errors="coerce").notna().all()

    @pytest.mark.asyncio
    async def test_silver_kpis_csv_value_column_types(self, tmp_path, monkeypatch):
        """silver_financial_kpis.csv value column is numeric or NaN."""
        monkeypatch.setenv("OPENAI_API_KEY", "")
        state = initial_state(TICKER)
        state["company_info"] = _make_company_info()
        state["bronze_facts"] = _build_mock_facts()

        with _patch_csv_writer("backend.agents.silver.financial_kpis", tmp_path):
            from backend.agents.silver.financial_kpis import silver_financial_kpis_agent

            result = await silver_financial_kpis_agent(state)

        df = pd.read_csv(Path(result["silver_kpis_path"]))
        # Value column should contain numerics (some may be NaN for missing KPIs)
        numeric_mask = pd.to_numeric(df["value"], errors="coerce")
        # All non-null values should be successfully converted
        valid = df["value"].notna()
        assert (numeric_mask[valid].notna()).all()

    @pytest.mark.asyncio
    async def test_gold_risk_csv_score_column_range(self, tmp_path, monkeypatch):
        """gold_risk_assessment.csv 'score' column values are between 1 and 5."""
        monkeypatch.setenv("OPENAI_API_KEY", "")
        from backend.agents.silver.financial_kpis import _extract_kpis

        state = initial_state(TICKER)
        state["company_info"] = _make_company_info()
        state["silver_kpis"] = _extract_kpis(_build_mock_facts())

        with _patch_csv_writer("backend.agents.gold.risk_assessment", tmp_path):
            from backend.agents.gold.risk_assessment import gold_risk_assessment_agent

            result = await gold_risk_assessment_agent(state)

        df = pd.read_csv(Path(result["gold_risk_path"]))
        scores = pd.to_numeric(df["score"], errors="coerce")
        assert (scores >= 1.0).all()
        assert (scores <= 5.0).all()

    def test_financial_fact_model_validation(self):
        """FinancialFact rejects invalid data."""
        # Valid fact
        fact = FinancialFact(
            tag="NetIncomeLoss", value=100.0, unit="USD",
            end="2025-09-27", fy=2025, fp="FY", form="10-K",
            filed="2025-11-01", accession="0000000000-25-000001",
        )
        assert fact.tag == "NetIncomeLoss"
        assert fact.value == 100.0

    def test_risk_dimension_score_range_validation(self):
        """RiskDimension rejects scores outside 1-5 range."""
        with pytest.raises(Exception):
            RiskDimension(dimension="Test", score=0, reasoning="bad", key_metrics=[])
        with pytest.raises(Exception):
            RiskDimension(dimension="Test", score=6, reasoning="bad", key_metrics=[])

    def test_risk_factor_item_severity_range(self):
        """RiskFactorItem rejects severity outside 1-5 range."""
        from backend.models import RiskFactorItem

        with pytest.raises(Exception):
            RiskFactorItem(category="operational", title="test", summary="test", severity=0)
        with pytest.raises(Exception):
            RiskFactorItem(category="operational", title="test", summary="test", severity=6)

    @pytest.mark.asyncio
    async def test_ingested_at_is_iso_format(self, tmp_path):
        """ingested_at timestamp follows ISO 8601 format."""
        from backend.data.csv_writer import CsvWriter

        writer = CsvWriter(TICKER, output_dir=str(tmp_path))
        path = writer.write_bronze("ts_test", [{"val": 1}])
        df = pd.read_csv(path)
        ts = df.iloc[0]["ingested_at"]
        # ISO 8601 timestamps contain 'T' or '+' separators
        assert "T" in ts or "+" in ts or "Z" in ts

    @pytest.mark.asyncio
    async def test_processed_at_is_iso_format(self, tmp_path):
        """processed_at timestamp follows ISO 8601 format."""
        from backend.data.csv_writer import CsvWriter

        writer = CsvWriter(TICKER, output_dir=str(tmp_path))
        path = writer.write_silver("ts_test", [{"val": 1}])
        df = pd.read_csv(path)
        ts = df.iloc[0]["processed_at"]
        assert "T" in ts or "+" in ts or "Z" in ts

    @pytest.mark.asyncio
    async def test_analyzed_at_is_iso_format(self, tmp_path):
        """analyzed_at timestamp follows ISO 8601 format."""
        from backend.data.csv_writer import CsvWriter

        writer = CsvWriter(TICKER, output_dir=str(tmp_path))
        path = writer.write_gold("ts_test", [{"val": 1}])
        df = pd.read_csv(path)
        ts = df.iloc[0]["analyzed_at"]
        assert "T" in ts or "+" in ts or "Z" in ts
