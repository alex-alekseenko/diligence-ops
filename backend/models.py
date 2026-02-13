"""Pydantic v2 schemas and LangGraph state definition for DiligenceOps.

Multi-hop architecture: Bronze (raw ingestion) → Silver (transform) → Gold (analytics).
"""

from __future__ import annotations

import operator
from typing import Annotated, Any, TypedDict

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Bronze-layer models — raw data as received from APIs
# ---------------------------------------------------------------------------


class FinancialFact(BaseModel):
    """A single XBRL financial fact from SEC EDGAR."""

    tag: str = Field(description="XBRL tag name, e.g. 'NetIncomeLoss'")
    label: str = Field(default="", description="Human-readable label")
    value: float = Field(description="Numeric value")
    unit: str = Field(description="Unit type: USD, USD/shares, shares, pure")
    start: str | None = Field(default=None, description="Period start (ISO date)")
    end: str = Field(description="Period end (ISO date)")
    fy: int = Field(description="Fiscal year")
    fp: str = Field(description="Fiscal period: FY, Q1, Q2, Q3, Q4")
    form: str = Field(description="SEC form type: 10-K, 10-Q, etc.")
    filed: str = Field(description="Filing date (ISO)")
    accession: str = Field(description="SEC accession number")
    frame: str | None = Field(default=None, description="CY frame identifier")
    taxonomy: str = Field(default="us-gaap", description="XBRL taxonomy: us-gaap, dei")


class CompanyInfo(BaseModel):
    """Company metadata from SEC EDGAR submissions."""

    ticker: str
    company_name: str
    cik: str = Field(description="10-digit zero-padded CIK")
    sic: str = Field(default="")
    sic_description: str = Field(default="")
    fiscal_year_end: str = Field(default="")
    exchanges: list[str] = Field(default_factory=list)
    entity_type: str = Field(default="")
    category: str = Field(default="")
    latest_10k_date: str | None = Field(default=None)


# ---------------------------------------------------------------------------
# Silver-layer models — cleaned, structured, enriched
# ---------------------------------------------------------------------------


class FinancialKPIs(BaseModel):
    """Extracted and computed financial KPIs from silver layer."""

    revenue: float | None = None
    revenue_prior: float | None = None
    revenue_yoy_change: float | None = None
    net_income: float | None = None
    net_income_prior: float | None = None
    gross_profit: float | None = None
    gross_margin: float | None = None
    operating_income: float | None = None
    operating_margin: float | None = None
    total_assets: float | None = None
    total_liabilities: float | None = None
    stockholders_equity: float | None = None
    debt_to_equity: float | None = None
    long_term_debt: float | None = None
    cash_and_equivalents: float | None = None
    current_ratio: float | None = None
    operating_cash_flow: float | None = None
    free_cash_flow: float | None = None
    eps_basic: float | None = None
    fiscal_year: int = 0
    period_end: str = ""
    currency: str = "USD"
    source_tags: dict[str, str] = Field(
        default_factory=dict,
        description="Maps KPI name to the XBRL tag that sourced it",
    )
    anomalies: list[str] = Field(
        default_factory=list,
        description="LLM-flagged anomalies in the financial data",
    )


class RiskFactorItem(BaseModel):
    """A classified risk factor from 10-K Item 1A."""

    category: str = Field(
        description="One of: regulatory, competitive, operational, financial, "
        "legal, technology, macroeconomic, esg"
    )
    title: str = Field(description="Short title of the risk factor")
    summary: str = Field(description="1-2 sentence summary")
    severity: int = Field(ge=1, le=5, description="1=low, 5=critical")
    is_novel: bool = Field(
        default=False, description="True if not present in prior year filing"
    )


class InsiderTransaction(BaseModel):
    """A single insider trade from Form 4."""

    insider_name: str
    insider_title: str = ""
    transaction_date: str = ""
    transaction_code: str = Field(
        default="", description="P=purchase, S=sale, A=award, M=exercise"
    )
    shares: float = 0
    price_per_share: float | None = None
    value: float | None = None
    shares_owned_after: float | None = None
    is_direct: bool = True
    filing_date: str = ""


class InsiderSignal(BaseModel):
    """Aggregated insider trading signal."""

    total_buys: int = 0
    total_sells: int = 0
    net_shares: float = 0
    buy_sell_ratio: float | None = None
    cluster_detected: bool = False
    cluster_description: str = ""
    signal: str = Field(
        default="neutral", description="bullish / bearish / neutral"
    )


class InstitutionalHolder(BaseModel):
    """A top institutional holder from 13F-HR."""

    holder_name: str
    shares: float = 0
    value: float | None = None
    pct_of_portfolio: float | None = None
    change_shares: float | None = None
    change_pct: float | None = None
    holder_type: str = Field(
        default="unknown", description="passive / active / unknown"
    )


class MaterialEvent(BaseModel):
    """A material event from 8-K filing."""

    filing_date: str = ""
    item_code: str = Field(default="", description="e.g., '1.01', '4.02'")
    item_description: str = ""
    severity: int = Field(default=2, ge=1, le=5, description="1=routine, 5=critical")
    summary: str = ""


class DirectorInfo(BaseModel):
    """Individual board director details."""

    name: str = ""
    is_independent: bool | None = None
    committees: list[str] = Field(default_factory=list)
    role: str | None = Field(default=None, description="e.g., 'Chairman', 'Lead Independent Director'")
    age: int | None = Field(default=None, description="Director's age as stated in proxy")
    director_since: int | None = Field(default=None, description="Year the director joined the board")


class NEOCompensation(BaseModel):
    """Named Executive Officer compensation."""

    name: str = ""
    title: str | None = None
    total_comp: float | None = None
    salary: float | None = None
    stock_awards: float | None = None
    non_equity_incentive: float | None = None
    other_comp: float | None = None
    fiscal_year: int | None = None


class GovernanceData(BaseModel):
    """Governance and compensation data from DEF 14A."""

    # CEO compensation
    ceo_name: str = ""
    ceo_total_comp: float | None = None
    ceo_comp_prior: float | None = None
    ceo_pay_growth: float | None = None
    median_employee_pay: float | None = None
    ceo_pay_ratio: float | None = None

    # Board composition
    board_size: int | None = None
    independent_directors: int | None = None
    board_independence_pct: float | None = None
    directors: list[DirectorInfo] = Field(default_factory=list)

    # Anti-takeover provisions
    has_poison_pill: bool | None = None
    has_staggered_board: bool | None = None
    has_dual_class: bool | None = None
    anti_takeover_provisions: list[str] = Field(default_factory=list)

    # NEO compensation table
    neo_compensation: list[NEOCompensation] = Field(default_factory=list)

    # Governance flags and provisions
    governance_flags: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Gold-layer models — analytics, scoring, synthesis
# ---------------------------------------------------------------------------


class RiskDimension(BaseModel):
    """A single risk dimension score with reasoning."""

    dimension: str = Field(description="e.g. 'Financial Health'")
    score: int = Field(ge=1, le=5, description="1=low risk, 5=critical")
    reasoning: str = Field(description="2-4 sentences with data citations")
    key_metrics: list[str] = Field(
        default_factory=list, description="Referenced KPI names"
    )


class RedFlag(BaseModel):
    """An identified red flag in the financial data."""

    flag: str
    severity: str = Field(description="Low / Medium / High / Critical")
    evidence: str


class RiskAssessment(BaseModel):
    """Multi-dimensional risk assessment output."""

    dimensions: list[RiskDimension]
    composite_score: float = Field(description="Weighted average 1.0-5.0")
    risk_level: str = Field(description="Low / Medium / High / Critical")
    red_flags: list[RedFlag] = Field(default_factory=list)


class CrossWorkstreamFlag(BaseModel):
    """A cross-workstream correlation red flag."""

    rule_name: str
    severity: str = Field(description="Critical / High / Medium")
    description: str
    workstreams_involved: list[str] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)


class MemoSection(BaseModel):
    """A section of the investment memo."""

    title: str
    content: str
    citations: list[str] = Field(
        default_factory=list, description="XBRL tag references"
    )


class DiligenceMemo(BaseModel):
    """Full investment diligence memo."""

    executive_summary: str
    company_overview: str
    financial_analysis: str
    risk_assessment: str
    key_findings: list[str]
    recommendation: str
    sections: list[MemoSection] = Field(default_factory=list)
    generated_at: str = ""


# ---------------------------------------------------------------------------
# Pipeline progress and state
# ---------------------------------------------------------------------------


class PipelineProgress(BaseModel):
    """WebSocket message for pipeline progress updates."""

    run_id: str
    stage: str = Field(
        description="bronze / silver / gold / complete / error"
    )
    agent: str = Field(description="Agent node name")
    message: str
    progress_pct: int = Field(ge=0, le=100)
    timestamp: str = ""


class PipelineState(TypedDict, total=False):
    """LangGraph shared state passed between agents.

    Organized by medallion layer: bronze → silver → gold → results.
    Uses Annotated[list, operator.add] for errors and progress_messages
    so that parallel agents append rather than overwrite.
    """

    ticker: str

    # ── Bronze layer (raw ingestion) ─────────────────────────────────
    company_info: CompanyInfo | None
    bronze_facts: list[FinancialFact]
    bronze_company_info_path: str | None
    bronze_xbrl_facts_path: str | None
    bronze_10k_risk_text: str  # raw Item 1A text
    bronze_10k_risk_text_path: str | None
    bronze_form4_transactions: list[dict]
    bronze_form4_path: str | None
    bronze_13f_holdings: list[dict]
    bronze_13f_path: str | None
    bronze_8k_filings: list[dict]
    bronze_8k_path: str | None
    bronze_def14a_proxy: dict  # {filing_date, text}
    bronze_def14a_path: str | None

    # ── Silver layer (cleaned, transformed) ──────────────────────────
    silver_kpis: FinancialKPIs | None
    silver_kpis_path: str | None
    silver_risk_factors: list[dict]
    silver_risk_factors_path: str | None
    silver_insider_trades: list[dict]
    silver_insider_trades_path: str | None
    silver_insider_signal: dict  # InsiderSignal as dict
    silver_institutional_holders: list[dict]
    silver_institutional_path: str | None
    silver_material_events: list[dict]
    silver_events_path: str | None
    silver_governance: dict  # GovernanceData as dict
    silver_governance_path: str | None

    # ── Gold layer (analytics, scoring) ──────────────────────────────
    gold_risk_scores: RiskAssessment | None
    gold_risk_path: str | None
    gold_cross_workstream_flags: list[dict]
    gold_cross_workstream_path: str | None
    deal_recommendation: str

    # ── Results layer (final LLM outputs) ─────────────────────────
    result_memo: DiligenceMemo | None
    result_memo_path: str | None

    # ── Pipeline metadata ────────────────────────────────────────────
    confidence: float
    current_stage: str
    errors: Annotated[list[str], operator.add]
    progress_messages: Annotated[list[str], operator.add]


def initial_state(ticker: str) -> PipelineState:
    """Create a fresh pipeline state for a ticker."""
    return PipelineState(
        ticker=ticker.upper().strip(),
        # Bronze
        company_info=None,
        bronze_facts=[],
        bronze_company_info_path=None,
        bronze_xbrl_facts_path=None,
        bronze_10k_risk_text="",
        bronze_10k_risk_text_path=None,
        bronze_form4_transactions=[],
        bronze_form4_path=None,
        bronze_13f_holdings=[],
        bronze_13f_path=None,
        bronze_8k_filings=[],
        bronze_8k_path=None,
        bronze_def14a_proxy={},
        bronze_def14a_path=None,
        # Silver
        silver_kpis=None,
        silver_kpis_path=None,
        silver_risk_factors=[],
        silver_risk_factors_path=None,
        silver_insider_trades=[],
        silver_insider_trades_path=None,
        silver_insider_signal={},
        silver_institutional_holders=[],
        silver_institutional_path=None,
        silver_material_events=[],
        silver_events_path=None,
        silver_governance={},
        silver_governance_path=None,
        # Gold
        gold_risk_scores=None,
        gold_risk_path=None,
        gold_cross_workstream_flags=[],
        gold_cross_workstream_path=None,
        deal_recommendation="",
        # Results
        result_memo=None,
        result_memo_path=None,
        # Metadata
        confidence=0.0,
        errors=[],
        current_stage="initialized",
        progress_messages=[],
    )
