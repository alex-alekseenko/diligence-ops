"""Shared pytest fixtures: mock EDGAR responses, LLM responses, temp output dir."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.models import (
    CompanyInfo,
    DiligenceMemo,
    FinancialFact,
    FinancialKPIs,
    GovernanceData,
    InsiderSignal,
    InstitutionalHolder,
    MaterialEvent,
    PipelineState,
    RedFlag,
    RiskAssessment,
    RiskDimension,
    RiskFactorItem,
    initial_state,
)

# ---------------------------------------------------------------------------
# Mock SEC EDGAR API responses
# ---------------------------------------------------------------------------

MOCK_COMPANY_TICKERS = {
    "0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."},
    "1": {"cik_str": 1318605, "ticker": "TSLA", "title": "Tesla, Inc."},
    "2": {"cik_str": 1326380, "ticker": "GME", "title": "GameStop Corp."},
}

MOCK_SUBMISSIONS = {
    "cik": "0000320193",
    "name": "Apple Inc.",
    "tickers": ["AAPL"],
    "exchanges": ["Nasdaq"],
    "sic": "3571",
    "sicDescription": "Electronic Computers",
    "fiscalYearEnd": "0928",
    "entityType": "operating",
    "category": "Large accelerated filer",
    "filings": {
        "recent": {
            "form": ["10-K", "10-Q", "8-K"],
            "filingDate": ["2025-11-01", "2025-08-01", "2025-07-15"],
            "accessionNumber": ["0000320193-25-000100", "0000320193-25-000090", "0000320193-25-000080"],
        }
    },
}

MOCK_COMPANY_FACTS = {
    "cik": 320193,
    "entityName": "Apple Inc.",
    "facts": {
        "us-gaap": {
            "RevenueFromContractWithCustomerExcludingAssessedTax": {
                "label": "Revenue from Contract with Customer, Excluding Assessed Tax",
                "units": {
                    "USD": [
                        {"start": "2024-09-29", "end": "2025-09-27", "val": 416160000000, "accn": "0000320193-25-000100", "fy": 2025, "fp": "FY", "form": "10-K", "filed": "2025-11-01"},
                        {"start": "2023-10-01", "end": "2024-09-28", "val": 391035000000, "accn": "0000320193-25-000100", "fy": 2025, "fp": "FY", "form": "10-K", "filed": "2025-11-01"},
                        {"start": "2022-09-25", "end": "2023-09-30", "val": 383285000000, "accn": "0000320193-24-000090", "fy": 2024, "fp": "FY", "form": "10-K", "filed": "2024-11-01"},
                    ]
                },
            },
            "NetIncomeLoss": {
                "label": "Net Income (Loss)",
                "units": {
                    "USD": [
                        {"start": "2024-09-29", "end": "2025-09-27", "val": 112005000000, "accn": "0000320193-25-000100", "fy": 2025, "fp": "FY", "form": "10-K", "filed": "2025-11-01"},
                        {"start": "2023-10-01", "end": "2024-09-28", "val": 93736000000, "accn": "0000320193-25-000100", "fy": 2025, "fp": "FY", "form": "10-K", "filed": "2025-11-01"},
                    ]
                },
            },
            "GrossProfit": {
                "label": "Gross Profit",
                "units": {
                    "USD": [
                        {"start": "2024-09-29", "end": "2025-09-27", "val": 195228000000, "accn": "0000320193-25-000100", "fy": 2025, "fp": "FY", "form": "10-K", "filed": "2025-11-01"},
                    ]
                },
            },
            "OperatingIncomeLoss": {
                "label": "Operating Income (Loss)",
                "units": {
                    "USD": [
                        {"start": "2024-09-29", "end": "2025-09-27", "val": 134048000000, "accn": "0000320193-25-000100", "fy": 2025, "fp": "FY", "form": "10-K", "filed": "2025-11-01"},
                    ]
                },
            },
            "Assets": {
                "label": "Assets",
                "units": {
                    "USD": [
                        {"end": "2025-09-27", "val": 364980000000, "accn": "0000320193-25-000100", "fy": 2025, "fp": "FY", "form": "10-K", "filed": "2025-11-01"},
                    ]
                },
            },
            "Liabilities": {
                "label": "Liabilities",
                "units": {
                    "USD": [
                        {"end": "2025-09-27", "val": 308030000000, "accn": "0000320193-25-000100", "fy": 2025, "fp": "FY", "form": "10-K", "filed": "2025-11-01"},
                    ]
                },
            },
            "StockholdersEquity": {
                "label": "Stockholders' Equity",
                "units": {
                    "USD": [
                        {"end": "2025-09-27", "val": 56950000000, "accn": "0000320193-25-000100", "fy": 2025, "fp": "FY", "form": "10-K", "filed": "2025-11-01"},
                    ]
                },
            },
            "LongTermDebtNoncurrent": {
                "label": "Long-term Debt, Noncurrent",
                "units": {
                    "USD": [
                        {"end": "2025-09-27", "val": 96800000000, "accn": "0000320193-25-000100", "fy": 2025, "fp": "FY", "form": "10-K", "filed": "2025-11-01"},
                    ]
                },
            },
            "CashAndCashEquivalentsAtCarryingValue": {
                "label": "Cash and Cash Equivalents",
                "units": {
                    "USD": [
                        {"end": "2025-09-27", "val": 29943000000, "accn": "0000320193-25-000100", "fy": 2025, "fp": "FY", "form": "10-K", "filed": "2025-11-01"},
                    ]
                },
            },
            "NetCashProvidedByUsedInOperatingActivities": {
                "label": "Operating Cash Flow",
                "units": {
                    "USD": [
                        {"start": "2024-09-29", "end": "2025-09-27", "val": 124000000000, "accn": "0000320193-25-000100", "fy": 2025, "fp": "FY", "form": "10-K", "filed": "2025-11-01"},
                    ]
                },
            },
            "EarningsPerShareBasic": {
                "label": "Earnings Per Share, Basic",
                "units": {
                    "USD/shares": [
                        {"start": "2024-09-29", "end": "2025-09-27", "val": 7.40, "accn": "0000320193-25-000100", "fy": 2025, "fp": "FY", "form": "10-K", "filed": "2025-11-01"},
                    ]
                },
            },
            "AssetsCurrent": {
                "label": "Assets, Current",
                "units": {
                    "USD": [
                        {"end": "2025-09-27", "val": 133000000000, "accn": "0000320193-25-000100", "fy": 2025, "fp": "FY", "form": "10-K", "filed": "2025-11-01"},
                    ]
                },
            },
            "LiabilitiesCurrent": {
                "label": "Liabilities, Current",
                "units": {
                    "USD": [
                        {"end": "2025-09-27", "val": 154000000000, "accn": "0000320193-25-000100", "fy": 2025, "fp": "FY", "form": "10-K", "filed": "2025-11-01"},
                    ]
                },
            },
            "PaymentsToAcquirePropertyPlantAndEquipment": {
                "label": "Capital Expenditures",
                "units": {
                    "USD": [
                        {"start": "2024-09-29", "end": "2025-09-27", "val": 9959000000, "accn": "0000320193-25-000100", "fy": 2025, "fp": "FY", "form": "10-K", "filed": "2025-11-01"},
                    ]
                },
            },
        },
        "dei": {
            "EntityCommonStockSharesOutstanding": {
                "label": "Entity Common Stock, Shares Outstanding",
                "units": {
                    "shares": [
                        {"end": "2025-10-17", "val": 15115000000, "accn": "0000320193-25-000100", "fy": 2025, "fp": "FY", "form": "10-K", "filed": "2025-11-01"},
                    ]
                },
            },
        },
    },
}


@pytest.fixture
def mock_company_tickers():
    return MOCK_COMPANY_TICKERS


@pytest.fixture
def mock_submissions():
    return MOCK_SUBMISSIONS


@pytest.fixture
def mock_company_facts():
    return MOCK_COMPANY_FACTS


@pytest.fixture
def sample_company_info() -> CompanyInfo:
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


@pytest.fixture
def sample_facts() -> list[FinancialFact]:
    """Build FinancialFact list from mock company facts."""
    facts = []
    for taxonomy in ("us-gaap", "dei"):
        taxonomy_data = MOCK_COMPANY_FACTS["facts"].get(taxonomy, {})
        for tag_name, tag_data in taxonomy_data.items():
            label = tag_data.get("label", tag_name)
            for unit_type, entries in tag_data.get("units", {}).items():
                for entry in entries:
                    if entry.get("form") != "10-K":
                        continue
                    facts.append(
                        FinancialFact(
                            tag=tag_name,
                            label=label,
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


@pytest.fixture
def sample_kpis() -> FinancialKPIs:
    return FinancialKPIs(
        revenue=416160000000,
        revenue_prior=391035000000,
        revenue_yoy_change=0.0642,
        net_income=112005000000,
        net_income_prior=93736000000,
        gross_profit=195228000000,
        gross_margin=0.469,
        operating_income=134048000000,
        operating_margin=0.322,
        total_assets=364980000000,
        total_liabilities=308030000000,
        stockholders_equity=56950000000,
        debt_to_equity=5.41,
        long_term_debt=96800000000,
        cash_and_equivalents=29943000000,
        current_ratio=0.864,
        operating_cash_flow=124000000000,
        free_cash_flow=114041000000,
        eps_basic=7.40,
        fiscal_year=2025,
        period_end="2025-09-27",
        source_tags={
            "revenue": "RevenueFromContractWithCustomerExcludingAssessedTax",
            "net_income": "NetIncomeLoss",
            "gross_profit": "GrossProfit",
        },
    )


@pytest.fixture
def sample_risk_assessment() -> RiskAssessment:
    return RiskAssessment(
        dimensions=[
            RiskDimension(dimension="Financial Health", score=1, reasoning="Strong margins and profitability.", key_metrics=["gross_margin", "net_income"]),
            RiskDimension(dimension="Market Position", score=2, reasoning="Revenue growing 6.4% YoY.", key_metrics=["revenue", "revenue_yoy_change"]),
            RiskDimension(dimension="Operational Risk", score=1, reasoning="Operating margin is 32.2%.", key_metrics=["operating_margin"]),
            RiskDimension(dimension="Governance", score=3, reasoning="High D/E ratio of 5.41.", key_metrics=["debt_to_equity"]),
            RiskDimension(dimension="Liquidity", score=3, reasoning="Current ratio below 1.0 at 0.86.", key_metrics=["current_ratio"]),
        ],
        composite_score=2.0,
        risk_level="Low",
        red_flags=[
            RedFlag(flag="High Leverage", severity="Medium", evidence="D/E ratio of 5.41"),
        ],
    )


@pytest.fixture
def sample_memo() -> DiligenceMemo:
    return DiligenceMemo(
        executive_summary="Apple Inc. is a strong, highly profitable company with moderate leverage risk.",
        company_overview="Apple Inc. designs and sells consumer electronics, software, and services worldwide.",
        financial_analysis="Revenue of $416.2B with 46.9% gross margin and 32.2% operating margin.",
        risk_assessment="Overall risk is Low (2.0/5.0). Main concern is high leverage.",
        key_findings=["Revenue $416.2B, up 6.4% YoY", "Gross margin 46.9%", "D/E ratio 5.41"],
        recommendation="Hold. Strong fundamentals but high leverage warrants monitoring.",
        generated_at="2025-01-01 00:00 UTC",
    )


# ---------------------------------------------------------------------------
# Workstream fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def sample_risk_factors() -> list[dict]:
    """Sample classified risk factors."""
    return [
        RiskFactorItem(
            category="regulatory",
            title="Pending Antitrust Investigation",
            summary="DOJ investigating potential anti-competitive practices in App Store.",
            severity=4,
            is_novel=True,
        ).model_dump(),
        RiskFactorItem(
            category="competitive",
            title="Smartphone Market Saturation",
            summary="Global smartphone market declining, competition increasing in AI space.",
            severity=3,
            is_novel=False,
        ).model_dump(),
        RiskFactorItem(
            category="macroeconomic",
            title="China Revenue Risk",
            summary="Significant revenue exposure to China amid trade tensions.",
            severity=3,
            is_novel=False,
        ).model_dump(),
    ]


@pytest.fixture
def sample_insider_trades() -> list[dict]:
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


@pytest.fixture
def sample_insider_signal() -> dict:
    """Sample aggregated insider signal."""
    return InsiderSignal(
        total_buys=1,
        total_sells=2,
        net_shares=-70000,
        buy_sell_ratio=0.13,
        cluster_detected=False,
        cluster_description="",
        signal="neutral",
    ).model_dump()


@pytest.fixture
def sample_institutional_holders() -> list[dict]:
    """Sample institutional holders."""
    return [
        {"holder_name": "Vanguard Group", "shares": 1_200_000_000, "value": 270e9, "change_pct": 0.02, "holder_type": "passive"},
        {"holder_name": "BlackRock Inc", "shares": 1_000_000_000, "value": 225e9, "change_pct": -0.01, "holder_type": "passive"},
        {"holder_name": "Berkshire Hathaway", "shares": 900_000_000, "value": 202e9, "change_pct": -0.05, "holder_type": "active"},
    ]


@pytest.fixture
def sample_material_events() -> list[dict]:
    """Sample material events from 8-K."""
    return [
        MaterialEvent(
            filing_date="2025-10-30",
            item_code="2.02",
            item_description="Results of Operations",
            severity=2,
            summary="Q4 2025 earnings release",
        ).model_dump(),
        MaterialEvent(
            filing_date="2025-07-01",
            item_code="5.02",
            item_description="Departure/Appointment of Officers",
            severity=3,
            summary="CFO transition announcement",
        ).model_dump(),
    ]


@pytest.fixture
def sample_governance() -> dict:
    """Sample governance data."""
    return GovernanceData(
        ceo_name="Tim Cook",
        ceo_total_comp=98734000,
        ceo_comp_prior=84200000,
        ceo_pay_growth=0.1725,
        median_employee_pay=94118,
        ceo_pay_ratio=1049,
        board_size=8,
        independent_directors=7,
        board_independence_pct=0.875,
        has_poison_pill=False,
        has_staggered_board=False,
        has_dual_class=False,
        anti_takeover_provisions=[],
        governance_flags=[],
    ).model_dump()


@pytest.fixture
def sample_state(sample_company_info, sample_facts, sample_kpis, sample_risk_assessment, sample_memo) -> PipelineState:
    """A fully populated v0.3 pipeline state for testing."""
    state = initial_state("AAPL")
    state.update(
        company_info=sample_company_info,
        bronze_facts=sample_facts,
        bronze_company_info_path="pipeline_output/AAPL/bronze_company_info.csv",
        bronze_xbrl_facts_path="pipeline_output/AAPL/bronze_xbrl_facts.csv",
        silver_kpis=sample_kpis,
        silver_kpis_path="pipeline_output/AAPL/silver_financial_kpis.csv",
        gold_risk_scores=sample_risk_assessment,
        gold_risk_path="pipeline_output/AAPL/gold_risk_assessment.csv",
        result_memo=sample_memo,
        result_memo_path="pipeline_output/AAPL/results_diligence_memo.md",
        confidence=0.92,
        current_stage="complete",
    )
    return state


@pytest.fixture
def sample_state_v2(
    sample_company_info, sample_facts, sample_kpis, sample_risk_assessment, sample_memo,
    sample_risk_factors, sample_insider_trades, sample_insider_signal,
    sample_institutional_holders, sample_material_events, sample_governance,
) -> PipelineState:
    """A fully populated v0.3 pipeline state with all workstream data."""
    state = initial_state("AAPL")
    state.update(
        company_info=sample_company_info,
        bronze_facts=sample_facts,
        bronze_company_info_path="pipeline_output/AAPL/bronze_company_info.csv",
        bronze_xbrl_facts_path="pipeline_output/AAPL/bronze_xbrl_facts.csv",
        bronze_10k_risk_text="Company faces significant regulatory risk...",
        bronze_10k_risk_text_path="pipeline_output/AAPL/bronze_10k_risk_text.csv",
        bronze_form4_transactions=sample_insider_trades,
        bronze_form4_path="pipeline_output/AAPL/bronze_form4_transactions.csv",
        bronze_13f_holdings=[h for h in sample_institutional_holders],
        bronze_13f_path="pipeline_output/AAPL/bronze_13f_holdings.csv",
        bronze_8k_filings=[{"filing_date": "2025-10-30", "description": "Item 2.02 Results"}],
        bronze_8k_path="pipeline_output/AAPL/bronze_8k_filings.csv",
        bronze_def14a_proxy={"text": "Proxy statement text..."},
        bronze_def14a_path="pipeline_output/AAPL/bronze_def14a_proxy.csv",
        silver_kpis=sample_kpis,
        silver_kpis_path="pipeline_output/AAPL/silver_financial_kpis.csv",
        silver_risk_factors=sample_risk_factors,
        silver_risk_factors_path="pipeline_output/AAPL/silver_risk_factors.csv",
        silver_insider_trades=sample_insider_trades,
        silver_insider_trades_path="pipeline_output/AAPL/silver_insider_transactions.csv",
        silver_insider_signal=sample_insider_signal,
        silver_institutional_holders=sample_institutional_holders,
        silver_institutional_path="pipeline_output/AAPL/silver_institutional_holders.csv",
        silver_material_events=[e for e in sample_material_events],
        silver_events_path="pipeline_output/AAPL/silver_material_events.csv",
        silver_governance=sample_governance,
        silver_governance_path="pipeline_output/AAPL/silver_governance.csv",
        gold_risk_scores=sample_risk_assessment,
        gold_risk_path="pipeline_output/AAPL/gold_risk_assessment.csv",
        gold_cross_workstream_flags=[],
        gold_cross_workstream_path="pipeline_output/AAPL/gold_cross_workstream_flags.csv",
        result_memo=sample_memo,
        result_memo_path="pipeline_output/AAPL/results_diligence_memo.md",
        deal_recommendation="PROCEED",
        confidence=0.92,
        current_stage="complete",
    )
    return state


@pytest.fixture
def tmp_output_dir(tmp_path):
    """Provide a temporary output directory for CsvWriter."""
    return str(tmp_path / "output")
