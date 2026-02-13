"""Lightweight mock API server that serves example AAPL data for e2e UI tests.

Usage:
    python tests/e2e/mock_api_server.py

Serves on http://localhost:8000 — the same address the frontend expects.
All responses are built from pre-saved data in examples/AAPL/.
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse

EXAMPLES_DIR = Path(__file__).resolve().parent.parent.parent / "examples" / "AAPL"

app = FastAPI(title="DiligenceOps Mock API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def _build_results() -> dict:
    """Build PipelineResults from example CSVs."""

    # Company info
    ci = pd.read_csv(EXAMPLES_DIR / "bronze_company_info.csv").iloc[0]
    company_info = {
        "ticker": str(ci["ticker"]),
        "company_name": str(ci["company_name"]),
        "cik": str(ci["cik"]),
        "sic": str(ci["sic"]),
        "sic_description": str(ci["sic_description"]),
        "fiscal_year_end": str(ci["fiscal_year_end"]),
        "exchanges": [str(ci.get("exchanges", "Nasdaq"))],
        "entity_type": str(ci.get("entity_type", "")),
        "category": str(ci.get("category", "")),
        "latest_10k_date": str(ci.get("latest_10k_date", "")),
    }

    # KPIs — pivot metric→value
    kpi_df = pd.read_csv(EXAMPLES_DIR / "silver_financial_kpis.csv")
    kv = dict(zip(kpi_df["metric"], kpi_df["value"]))
    kpis = {
        "revenue": kv.get("revenue"),
        "revenue_prior": kv.get("revenue_prior"),
        "revenue_yoy_change": kv.get("revenue_yoy_change"),
        "net_income": kv.get("net_income"),
        "net_income_prior": kv.get("net_income_prior"),
        "gross_profit": kv.get("gross_profit"),
        "gross_margin": kv.get("gross_margin"),
        "operating_income": kv.get("operating_income"),
        "operating_margin": kv.get("operating_margin"),
        "total_assets": kv.get("total_assets"),
        "total_liabilities": kv.get("total_liabilities"),
        "stockholders_equity": kv.get("stockholders_equity"),
        "debt_to_equity": kv.get("debt_to_equity"),
        "long_term_debt": kv.get("long_term_debt"),
        "cash_and_equivalents": kv.get("cash_and_equivalents"),
        "current_ratio": kv.get("current_ratio"),
        "operating_cash_flow": kv.get("operating_cash_flow"),
        "free_cash_flow": kv.get("free_cash_flow"),
        "eps_basic": kv.get("eps_basic"),
        "fiscal_year": int(kpi_df.iloc[0]["fiscal_year"]),
        "period_end": str(kpi_df.iloc[0]["period_end"]),
        "currency": str(kpi_df.iloc[0]["currency"]),
        "source_tags": {},
        "anomalies": [],
    }

    # Risk assessment
    risk_df = pd.read_csv(EXAMPLES_DIR / "gold_risk_assessment.csv")
    dim_rows = risk_df[~risk_df["dimension"].str.startswith(("COMPOSITE", "RED_FLAG"))]
    composite_row = risk_df[risk_df["dimension"] == "COMPOSITE"].iloc[0]
    flag_rows = risk_df[risk_df["dimension"].str.startswith("RED_FLAG")]

    risk_scores = {
        "dimensions": [
            {
                "dimension": str(r["dimension"]),
                "score": int(r["score"]),
                "reasoning": str(r["reasoning"]),
                "key_metrics": str(r["key_metrics"]).split("; ") if pd.notna(r["key_metrics"]) and r["key_metrics"] else [],
            }
            for _, r in dim_rows.iterrows()
        ],
        "composite_score": float(composite_row["score"]),
        "risk_level": "Medium",
        "red_flags": [
            {
                "flag": str(r["dimension"]).replace("RED_FLAG: ", ""),
                "severity": str(r["score"]),  # score column holds severity for red flags
                "evidence": str(r["reasoning"]),
            }
            for _, r in flag_rows.iterrows()
        ],
    }

    # Memo
    memo_path = EXAMPLES_DIR / "results_diligence_memo.md"
    memo_obj = {
        "executive_summary": "Apple Inc. (AAPL) — DO_NOT_PROCEED",
        "company_overview": "Apple Inc. is a leading global technology company.",
        "financial_analysis": "Revenue: $416B, Net income: $112B",
        "risk_assessment": "Composite risk: Medium (2.2/5.0)",
        "key_findings": [
            "Non-reliance statement on financials issued.",
            "High debt-to-equity ratio of 3.87.",
            "Significant insider selling activity.",
        ],
        "recommendation": "DO_NOT_PROCEED",
        "sections": [],
        "generated_at": "2026-02-12 08:35 UTC",
    }

    # Risk factors
    rf_df = pd.read_csv(EXAMPLES_DIR / "silver_risk_factors.csv")
    risk_factors = [
        {
            "category": str(r["category"]),
            "title": str(r["title"]),
            "summary": str(r["summary"]),
            "severity": int(r["severity"]),
            "is_novel": bool(r["is_novel"]),
        }
        for _, r in rf_df.iterrows()
    ]

    # Insider trades
    it_df = pd.read_csv(EXAMPLES_DIR / "silver_insider_transactions.csv")
    insider_trades = [
        {
            "insider_name": str(r["insider_name"]),
            "title": str(r.get("insider_title", "")),
            "tx_date": str(r.get("transaction_date", "")),
            "tx_code": str(r.get("transaction_code", "")),
            "shares": float(r.get("shares", 0)),
            "price": float(r["price_per_share"]) if pd.notna(r.get("price_per_share")) else None,
            "value": float(r["value"]) if pd.notna(r.get("value")) else None,
        }
        for _, r in it_df.iterrows()
    ]

    insider_signal = {
        "total_buys": 0,
        "total_sells": len([t for t in insider_trades if t["tx_code"] in ("S", "M")]),
        "net_shares": 0,
        "buy_sell_ratio": 0.0,
        "cluster_detected": False,
        "cluster_description": None,
        "signal": "bearish",
    }

    # Institutional holders
    ih_df = pd.read_csv(EXAMPLES_DIR / "silver_institutional_holders.csv")
    institutional_holders = [
        {
            "holder_name": str(r["holder_name"]),
            "shares": float(r["shares"]),
            "value": float(r["value"]) if pd.notna(r.get("value")) else None,
            "change_pct": float(r["change_pct"]) if pd.notna(r.get("change_pct")) else None,
            "holder_type": str(r.get("holder_type", "unknown")),
        }
        for _, r in ih_df.iterrows()
    ]

    # Material events
    me_df = pd.read_csv(EXAMPLES_DIR / "silver_material_events.csv")
    material_events = [
        {
            "filing_date": str(r["filing_date"]),
            "item_code": str(r["item_code"]),
            "item_description": str(r["item_description"]),
            "severity": int(r["severity"]),
            "summary": str(r["summary"]) if pd.notna(r.get("summary")) else None,
        }
        for _, r in me_df.iterrows()
    ]

    # Governance
    gov_df = pd.read_csv(EXAMPLES_DIR / "silver_governance.csv")
    g = gov_df.iloc[0]
    governance = {
        "ceo_name": str(g["ceo_name"]) if pd.notna(g.get("ceo_name")) else None,
        "ceo_total_comp": float(g["ceo_total_comp"]) if pd.notna(g.get("ceo_total_comp")) else None,
        "ceo_comp_prior": None,
        "ceo_pay_growth": None,
        "median_employee_pay": None,
        "ceo_pay_ratio": None,
        "board_size": int(g["board_size"]) if pd.notna(g.get("board_size")) else None,
        "independent_directors": int(g["independent_directors"]) if pd.notna(g.get("independent_directors")) else None,
        "board_independence_pct": float(g["board_independence_pct"]) if pd.notna(g.get("board_independence_pct")) else None,
        "has_poison_pill": None,
        "has_staggered_board": None,
        "has_dual_class": None,
        "anti_takeover_provisions": [],
        "governance_flags": [],
    }

    # Cross-workstream flags
    cf_df = pd.read_csv(EXAMPLES_DIR / "gold_cross_workstream_flags.csv")
    cross_flags = [
        {
            "rule_name": str(r["rule_name"]),
            "severity": str(r["severity"]),
            "description": str(r["description"]),
            "evidence": eval(r["evidence"]) if pd.notna(r.get("evidence")) else [],
        }
        for _, r in cf_df.iterrows()
    ]

    return {
        "run_id": "e2e-test-001",
        "ticker": "AAPL",
        "status": "complete",
        "company_info": company_info,
        "kpis": kpis,
        "risk_scores": risk_scores,
        "memo": memo_obj,
        "risk_factors": risk_factors,
        "insider_signal": insider_signal,
        "insider_trades": insider_trades,
        "institutional_holders": institutional_holders,
        "material_events": material_events,
        "governance": governance,
        "cross_workstream_flags": cross_flags,
        "deal_recommendation": "DO_NOT_PROCEED",
        "files": {
            "memo_md": "results_diligence_memo.md",
        },
        "confidence": 1.0,
        "errors": [],
    }


# Pre-build results at import time
_RESULTS = _build_results()
_MEMO_MD = (EXAMPLES_DIR / "results_diligence_memo.md").read_text(encoding="utf-8")


@app.get("/api/health")
async def health():
    return {"status": "ok", "service": "mock"}


@app.post("/api/analyze")
async def analyze():
    return {"run_id": "e2e-test-001", "ticker": "AAPL", "status": "running"}


@app.get("/api/results/{run_id}")
async def results(run_id: str):
    return _RESULTS


@app.get("/api/download/{run_id}/{file_type}")
async def download(run_id: str, file_type: str):
    if file_type == "memo_md":
        return PlainTextResponse(_MEMO_MD)
    return PlainTextResponse("Not found", status_code=404)


if __name__ == "__main__":
    import sys
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
    uvicorn.run(app, host="0.0.0.0", port=port)
