# DiligenceOps — PRD v0.2: Multi-Workstream Expansion

**Incremental Scope Addition to v0.1** | February 2026 | A.Team Portfolio Project

---

## 1. Scope of This Document

This PRD is an **incremental expansion** to DiligenceOps v0.1. It does not repeat the existing architecture, tech stack, build timeline, or testing strategy. It defines **five new analysis workstreams** and an upgraded report structure that transform DiligenceOps from a financial-only analyzer into a **multi-workstream due diligence platform** comparable to what M&A advisory firms produce.

Everything from v0.1 remains unchanged: the bronze-silver-gold CSV architecture, LangGraph orchestration, Next.js/Tailwind/Tremor frontend, and SEC EDGAR XBRL API as the financial data source. v0.2 adds new agents that consume additional free EDGAR endpoints alongside the existing pipeline.

### 1.1 v0.1 → v0.2 Comparison

| Dimension | v0.1 (Current) | v0.2 (This Document) |
|-----------|---------------|---------------------|
| **Scope** | Financial statements only (XBRL KPIs) | 6 workstreams: Financial + Risk Narrative + Insider Trading + Institutional + Material Events + Governance |
| **Agents** | 4 agents (parse, extract, analyze, write) | 9 agents organized in parallel workstreams |
| **Data Sources** | Company Facts API (XBRL) | + Submissions API, Full-text filings, Form 4, 13F, 8-K, DEF 14A |
| **Report** | 5-section memo | 10-section enterprise DD report with deal recommendation |
| **Library** | Raw HTTP to data.sec.gov | edgartools (`pip install edgartools`) — MIT licensed |
| **Cost** | Free (SEC EDGAR) | Still 100% free — all data from SEC EDGAR |

---

## 2. New Data Sources (All Free)

Every new data source is available through SEC EDGAR with no API key, no authentication, and no cost. The `edgartools` library provides a clean Python interface over all of them.

### 2.1 edgartools Library

**edgartools** is the fastest open-source SEC EDGAR library (MIT license, 1000+ tests, 1.3M+ PyPI downloads). It replaces raw HTTP calls to data.sec.gov with structured Python objects and pandas DataFrames.

```python
pip install edgartools

from edgar import *
set_identity("DiligenceOps/0.2 contact@example.com")
company = Company("AAPL")
balance_sheet = company.get_financials().balance_sheet()   # DataFrame
insider_filings = company.get_filings(form="4")            # Form 4 objects
holdings = company.get_filings(form="13F-HR")              # 13F holdings
```

### 2.2 EDGAR Endpoints Used by v0.2

| Source | Endpoint Pattern | Data Extracted | Used By |
|--------|-----------------|----------------|---------|
| **Submissions API** | `data.sec.gov/submissions/CIK{cik}.json` | Filing history, metadata, form types, dates | All new agents |
| **Full-text filing HTML** | `sec.gov/Archives/edgar/data/{cik}/{accession}/{file}` | 10-K sections: Item 1, 1A, 7, 7A full text | Risk Narrative Agent |
| **Form 4 filings** | via edgartools `company.get_filings(form="4")` | Insider buy/sell transactions, shares, dates | Insider Signal Agent |
| **13F-HR filings** | via edgartools `company.get_filings(form="13F-HR")` | Institutional holdings: fund, shares, value | Institutional Agent |
| **8-K filings** | via edgartools `company.get_filings(form="8-K")` | Material events: M&A, leadership, debt, restatements | Material Events Agent |
| **DEF 14A proxy** | via edgartools `company.get_filings(form="DEF 14A")` | Executive compensation, board composition, proposals | Governance Agent |

**Rate limit:** 10 requests/second to SEC EDGAR (unchanged from v0.1). edgartools handles rate limiting and retries internally.

---

## 3. New Agent Definitions

v0.2 adds five new agents that run in parallel after the existing Financial pipeline (Agents 1–4 from v0.1) completes its bronze-silver-gold flow. A new Orchestrator node in LangGraph fans out to all workstreams and fans in to the upgraded Memo Writer.

### 3.1 Agent 5 — Risk Narrative Analyzer

| Attribute | Detail |
|-----------|--------|
| **Input** | 10-K filing URL (latest annual report from Submissions API) |
| **Data Fetched** | Item 1A (Risk Factors) full text — typically 10–30 pages of narrative |
| **Processing** | LLM classifies each risk paragraph into categories: Regulatory, Competitive, Operational, Financial, Legal, Cybersecurity, ESG, Macroeconomic. Assigns severity (1–5) and novelty flag (new vs. boilerplate). |
| **Output CSV** | `{ticker}_risk_factors.csv` — columns: `risk_id, category, severity, summary, is_novel, source_text_snippet` |
| **Key Insight** | Distinguishes boilerplate risks (every company has them) from novel/specific risks (e.g., "pending DOJ investigation") that signal real danger |

### 3.2 Agent 6 — Insider Signal Analyzer

| Attribute | Detail |
|-----------|--------|
| **Input** | Company CIK (from v0.1 pipeline) |
| **Data Fetched** | Form 4 filings from last 12 months via edgartools: `company.get_filings(form="4")` |
| **Processing** | Parses each Form 4 for: reporter name, title, transaction type (P=Purchase, S=Sale, M=Exercise), shares, price, date. Calculates: net buy/sell ratio, cluster detection (3+ insiders same direction in 30 days), 10b5-1 plan flag. |
| **Output CSV** | `{ticker}_insider_trades.csv` — columns: `date, insider_name, title, tx_type, shares, price, value, is_10b5_1` |
| **Key Insight** | Cluster selling (multiple C-suite selling simultaneously) is one of the strongest red flags in DD. Distinguishes informative trades from routine option exercises. |

### 3.3 Agent 7 — Institutional Ownership Analyzer

| Attribute | Detail |
|-----------|--------|
| **Input** | Company ticker (from v0.1 state) |
| **Data Fetched** | 13F-HR filings that hold this company's stock via edgartools fund search |
| **Processing** | Identifies top 10 institutional holders by value. If 2 quarters available: calculates QoQ change in shares held and flags significant position reductions (>20% drop). |
| **Output CSV** | `{ticker}_institutional.csv` — columns: `fund_name, shares, value_usd, pct_portfolio, qoq_change_pct` |
| **Key Insight** | When top funds like Berkshire or Bridgewater reduce positions, it's a signal. When passive ETFs (Vanguard, BlackRock) increase, it's mechanical rebalancing — the agent should distinguish these. |

### 3.4 Agent 8 — Material Events Analyzer

| Attribute | Detail |
|-----------|--------|
| **Input** | Company CIK (from v0.1 pipeline) |
| **Data Fetched** | 8-K filings from last 12 months via edgartools: `company.get_filings(form="8-K")` |
| **Processing** | Parses 8-K item codes to classify events: 1.01 (M&A entry), 1.02 (M&A termination), 2.01 (asset acquisition), 2.04 (obligation trigger), 5.02 (officer departure/appointment), 4.01 (auditor change), 4.02 (non-reliance on financials). LLM summarizes each event and assigns impact severity. |
| **Output CSV** | `{ticker}_events.csv` — columns: `date, item_code, event_type, summary, severity, filing_url` |
| **Key Insight** | 8-K Item 4.02 (non-reliance on prior financials) is a nuclear red flag. Item 5.02 (CFO/CEO departure) during active M&A is a deal-breaker. This agent catches what pure financial analysis cannot. |

### 3.5 Agent 9 — Governance & Compensation Analyzer

| Attribute | Detail |
|-----------|--------|
| **Input** | Latest DEF 14A (proxy statement) filing URL |
| **Data Fetched** | Proxy statement text via edgartools: `company.get_filings(form="DEF 14A").latest().obj()` |
| **Processing** | LLM extracts: CEO total compensation, CEO pay vs. median employee ratio, board size and independence %, anti-takeover provisions (poison pills, staggered board), shareholder proposal outcomes. Compares CEO pay growth to revenue/earnings growth. |
| **Output CSV** | `{ticker}_governance.csv` — columns: `metric, value, benchmark, flag` |
| **Key Insight** | CEO pay growing 40% while revenue is flat is a misalignment flag. Low board independence (<50%) or dual-class share structures signal governance risk that investors care about. |

---

## 4. Updated Pipeline Architecture

### 4.1 LangGraph Execution Flow

The v0.2 pipeline extends v0.1 with a fan-out/fan-in pattern. The existing 4-agent financial pipeline runs first (producing bronze/silver/gold CSVs), then 5 new agents execute in parallel, then results merge into the upgraded Memo Writer.

```
[Ticker Input]
     │
     ▼
[Agent 1: Parser] → [Agent 2: Extractor]     ← existing v0.1 financial pipeline
     │                        │
     ▼                        ▼
[Bronze CSV]            [Silver CSV]
                              │
                              ▼
[Agent 3: Financial Analyzer] → [Gold CSV]
                              │
          ┌─────── FAN OUT ───────┐
          │       │       │       │       │
          ▼       ▼       ▼       ▼       ▼
       [Ag.5]  [Ag.6]  [Ag.7]  [Ag.8]  [Ag.9]
       Risk   Insider  Instit. Events  Govern.
          │       │       │       │       │
          └─────── FAN IN ────────┘
                     │
                     ▼
          [Agent 4*: Memo Writer v2]
                     │
                     ▼
          [Full DD Report + Gold CSVs]
```

### 4.2 New Shared State Fields

New fields added to the Pydantic `DiligenceState` model (all additive to v0.1):

| Field | Type | Description |
|-------|------|-------------|
| `risk_factors` | `list[dict]` | Classified risk items from 10-K Item 1A |
| `risk_factors_path` | `str \| None` | Path to `{ticker}_risk_factors.csv` |
| `insider_trades` | `list[dict]` | Parsed Form 4 transactions (last 12 months) |
| `insider_trades_path` | `str \| None` | Path to `{ticker}_insider_trades.csv` |
| `insider_signal` | `dict` | Summary: net_buy_sell_ratio, cluster_flag, total_value |
| `institutional_holders` | `list[dict]` | Top institutional holders with QoQ changes |
| `institutional_path` | `str \| None` | Path to `{ticker}_institutional.csv` |
| `material_events` | `list[dict]` | Classified 8-K events from last 12 months |
| `events_path` | `str \| None` | Path to `{ticker}_events.csv` |
| `governance` | `dict` | Extracted governance metrics from proxy |
| `governance_path` | `str \| None` | Path to `{ticker}_governance.csv` |
| `deal_recommendation` | `str` | `PROCEED` \| `PROCEED_WITH_CONDITIONS` \| `DO_NOT_PROCEED` |

---

## 5. Enterprise DD Report Structure

The v0.2 Memo Writer (Agent 4 upgraded) produces a report that mirrors what M&A advisory firms deliver. Each section maps to a specific agent's output.

| # | Report Section | Content | Source Agent | Data File |
|---|---------------|---------|-------------|-----------|
| 1 | **Executive Summary** | Deal recommendation (Proceed / Conditions / Do Not Proceed), top 3 risks, key metrics, confidence score | All agents | — |
| 2 | **Company Overview** | Business description, SIC industry, state of incorporation, fiscal year, exchange/ticker | Agent 1 (v0.1) | bronze CSV |
| 3 | **Financial Analysis** | Revenue, margins, debt ratios, cash flow, YoY trends, Altman Z-score | Agents 2–3 (v0.1) | silver + gold CSV |
| 4 | **Risk Factor Analysis** | Classified risks by category with severity scores. Novel vs. boilerplate tagging. Top 5 critical risks. | Agent 5 (new) | risk_factors CSV |
| 5 | **Insider Trading Signals** | 12-month buy/sell timeline, net ratio, cluster detection, notable transactions table | Agent 6 (new) | insider_trades CSV |
| 6 | **Institutional Ownership** | Top 10 holders table, QoQ changes, smart money vs. passive distinction | Agent 7 (new) | institutional CSV |
| 7 | **Material Events** | 12-month event timeline, classified by type and severity, critical events highlighted | Agent 8 (new) | events CSV |
| 8 | **Governance & Compensation** | CEO pay analysis, pay-performance alignment, board independence, anti-takeover provisions | Agent 9 (new) | governance CSV |
| 9 | **Cross-Workstream Red Flags** | Correlated signals across workstreams (see 5.1 below) | Memo Writer | — |
| 10 | **Recommendation & Caveats** | Final verdict with conditions, suggested follow-up items, limitations disclaimer | Memo Writer | — |

### 5.1 Cross-Workstream Correlation Rules

The Memo Writer doesn't just concatenate sections. It cross-references signals to surface correlated red flags that no single agent would catch:

| Severity | Signal Combination | Interpretation |
|----------|-------------------|----------------|
| 🔴 **Critical** | Insider cluster selling + Revenue decline >10% YoY + 8-K auditor change | Insiders may have advance knowledge of unreported problems |
| 🔴 **Critical** | 8-K Item 4.02 (non-reliance on financials) + Any financial metric | All financial analysis may be based on unreliable data |
| 🟡 **High** | CEO pay growth >3x revenue growth + Low board independence (<50%) | Governance misalignment, weak shareholder protections |
| 🟡 **High** | Novel risk factor (regulatory investigation) + Insider selling | Insiders may be aware of undisclosed regulatory outcomes |
| 🟠 **Medium** | Top institutional holders reducing >20% QoQ + Declining margins | Smart money may be pricing in future deterioration |
| 🟠 **Medium** | Multiple 8-K leadership changes in 12 months + Governance red flags | Organizational instability may affect post-acquisition integration |

---

## 6. Updated Project Structure

New files added to the v0.1 structure (marked with `+`):

```
diligenceops/
  agents/
    parser.py                # Agent 1 (v0.1 — unchanged)
    extractor.py             # Agent 2 (v0.1 — unchanged)
    analyzer.py              # Agent 3 (v0.1 — unchanged)
    writer.py                # Agent 4 (v0.1 — upgraded to v2 report)
+   risk_narrative.py        # Agent 5: 10-K Item 1A risk classifier
+   insider_signal.py        # Agent 6: Form 4 trade analyzer
+   institutional.py         # Agent 7: 13F holdings analyzer
+   material_events.py       # Agent 8: 8-K event classifier
+   governance.py            # Agent 9: DEF 14A proxy analyzer
  data/
    edgar_client.py          # v0.1 XBRL client (unchanged)
+   edgar_filings.py         # edgartools wrapper for filings access
    csv_writer.py            # v0.1 CSV writer (extended for new CSVs)
  models.py                  # Pydantic schemas (extended — see Section 4.2)
  graph.py                   # LangGraph workflow (extended with fan-out/fan-in)

pipeline_output/
  {ticker}_bronze_facts.csv
  {ticker}_silver_kpis.csv
  {ticker}_gold_analysis.csv
+ {ticker}_risk_factors.csv
+ {ticker}_insider_trades.csv
+ {ticker}_institutional.csv
+ {ticker}_events.csv
+ {ticker}_governance.csv
  {ticker}_memo.md            # Now full DD report (10 sections)
```

---

## 7. New Functional Requirements

Additive to v0.1's FR-1.x through FR-5.x. Numbering continues.

| ID | Requirement |
|----|-------------|
| FR-6.1 | Agent 5 shall download the latest 10-K filing URL from the Submissions API and extract Item 1A (Risk Factors) full text |
| FR-6.2 | Agent 5 shall classify each risk into one of 8 categories using LLM with structured output |
| FR-6.3 | Agent 5 shall distinguish novel risks from boilerplate using LLM confidence scoring |
| FR-6.4 | Agent 5 shall persist results to `{ticker}_risk_factors.csv` |
| FR-7.1 | Agent 6 shall retrieve Form 4 filings for the last 12 months using edgartools |
| FR-7.2 | Agent 6 shall parse each Form 4 for transaction details: reporter, title, type, shares, price, date |
| FR-7.3 | Agent 6 shall calculate net buy/sell ratio and detect cluster activity (3+ insiders, same direction, 30-day window) |
| FR-7.4 | Agent 6 shall flag 10b5-1 pre-planned trades vs. discretionary trades |
| FR-8.1 | Agent 7 shall identify top 10 institutional holders via 13F cross-reference |
| FR-8.2 | Agent 7 shall calculate quarter-over-quarter changes where 2 quarters of data are available |
| FR-8.3 | Agent 7 shall distinguish passive index funds from active managers in its analysis |
| FR-9.1 | Agent 8 shall retrieve 8-K filings for the last 12 months using edgartools |
| FR-9.2 | Agent 8 shall classify events by 8-K item code and assign severity (1–5) |
| FR-9.3 | Agent 8 shall flag critical events: Item 4.01 (auditor change), 4.02 (financial non-reliance), 5.02 (officer departure) |
| FR-10.1 | Agent 9 shall extract executive compensation data from the latest DEF 14A proxy statement |
| FR-10.2 | Agent 9 shall calculate CEO pay-to-performance alignment metrics |
| FR-10.3 | Agent 9 shall assess board independence % and flag anti-takeover provisions |
| FR-11.1 | The Memo Writer shall produce a 10-section DD report with deal recommendation |
| FR-11.2 | The Memo Writer shall implement cross-workstream correlation rules (Section 5.1) |
| FR-11.3 | The Memo Writer shall output a final recommendation: `PROCEED` \| `PROCEED_WITH_CONDITIONS` \| `DO_NOT_PROCEED` |

---

## 8. Incremental Build Timeline

Assumes v0.1 is already implemented.

| Phase | Task | Details | Time |
|-------|------|---------|------|
| 7 | edgartools Integration | Install edgartools, create `edgar_filings.py` wrapper, test filing access for all 5 form types | 30 min |
| 8 | Risk Narrative Agent | 10-K text download, Item 1A extraction, LLM risk classification prompt, CSV output | 45 min |
| 9 | Insider + Institutional Agents | Form 4 parsing, 13F cross-reference, net buy/sell calculation, cluster detection | 60 min |
| 10 | Events + Governance Agents | 8-K event classification, DEF 14A proxy parsing, governance metrics extraction | 60 min |
| 11 | LangGraph Fan-Out/Fan-In | Extend `graph.py` with parallel execution of Agents 5–9, merge node, updated state schema | 30 min |
| 12 | Memo Writer v2 + Correlations | 10-section report template, cross-workstream correlation rules, deal recommendation logic | 45 min |
| 13 | Dashboard Expansion | New Tremor panels: insider timeline chart, institutional holders table, events timeline, risk category breakdown | 45 min |
| 14 | Testing + Polish | UAT scenarios for new agents, sample data for offline mode, README update | 30 min |
| | **TOTAL v0.2 INCREMENT** | | **5 hrs 45 min** |

**Combined total (v0.1 + v0.2): ~10 hours** for a complete multi-workstream DD platform.

---

## 9. Additional UAT Scenarios

Supplementing the 6 UAT scenarios from v0.1.

| ID | Scenario | Acceptance Criteria |
|----|----------|-------------------|
| UAT-7 | Risk Factor Classification | Run for TSLA. `risk_factors.csv` has 10+ classified risks. At least 3 categories represented. At least 1 risk flagged as novel. |
| UAT-8 | Insider Cluster Detection | Run for GME. `insider_trades.csv` captures Form 4 data. If cluster selling detected, `cluster_flag` populated with insider names and date range. |
| UAT-9 | Institutional Ownership QoQ | Run for AAPL. `institutional.csv` shows top 10 holders. QoQ change calculated where data available. Passive vs. active distinction present. |
| UAT-10 | Critical 8-K Event Detection | Run for a company with recent 8-K filings. `events.csv` classifies events by item code with severity scoring. Item 4.01/4.02/5.02 flagged as critical. |
| UAT-11 | Cross-Workstream Correlation | Run for a company with insider selling and declining revenue. Report Section 9 (Red Flags) surfaces the correlated signal. |
| UAT-12 | Full DD Report Structure | Run for AAPL. Output memo contains all 10 sections. Deal recommendation present. Each section cites its data source. |

---

## 10. Updated Talk Track (90 seconds)

*For A.Team evaluation call, replacing the v0.1 60-second pitch:*

> "DiligenceOps is a multi-workstream due diligence agent built with LangGraph. You give it a public company ticker — say AAPL — and it runs six parallel analysis workstreams: financial statement extraction from XBRL, risk factor classification from 10-K narratives, insider trading pattern detection from Form 4 filings, institutional ownership tracking from 13F data, material event analysis from 8-K disclosures, and governance assessment from proxy statements.
>
> Each workstream produces auditable CSVs in a bronze-silver-gold medallion architecture. Then a synthesis agent cross-references signals across workstreams — for example, if insiders are cluster-selling while revenue is declining and the auditor just changed, that's a correlated red flag that no single analysis would catch.
>
> The output is a 10-section due diligence report with a deal recommendation — the same structure that M&A advisory firms charge six figures for. And every data point is free: it's all SEC EDGAR, no paid APIs.
>
> The architecture is domain-agnostic. Swap SEC filings for healthcare records, insurance claims, or contract repositories — the multi-agent workstream pattern and the bronze-silver-gold pipeline transfer directly."

---

## 11. Dependencies Delta

Single new Python dependency:

```
# requirements.txt addition for v0.2
edgartools>=5.13.0       # MIT license, SEC EDGAR access
```

All other dependencies (LangGraph, LangChain, Pydantic, pandas, pytest, Next.js, Tailwind, Tremor) are unchanged from v0.1.

---

*DiligenceOps PRD v0.2 — Multi-Workstream Expansion — Incremental to v0.1*
