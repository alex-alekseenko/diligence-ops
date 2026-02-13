"""CLI interface for running the DiligenceOps pipeline."""

import asyncio
import logging
import sys

from dotenv import load_dotenv

from backend.graph import run_pipeline
from backend.models import PipelineProgress

load_dotenv()


def on_progress(progress: PipelineProgress):
    """Print pipeline progress to terminal."""
    icon = {
        "bronze": "[>]",
        "silver": "[*]",
        "gold": "[#]",
        "complete": "[+]",
        "error": "[!]",
    }.get(progress.stage, "[.]")
    print(f"  {icon} [{progress.stage.upper():>7}] {progress.agent}: {progress.message} ({progress.progress_pct}%)")


async def main(ticker: str):
    print(f"\n{'='*60}")
    print(f"  DiligenceOps v0.3 — Medallion Architecture Pipeline")
    print(f"  Analyzing: {ticker.upper()}")
    print(f"{'='*60}\n")

    result = await run_pipeline(ticker, progress_callback=on_progress, run_id="cli")

    print(f"\n{'='*60}")
    print(f"  Pipeline Complete!")
    print(f"{'='*60}\n")

    # Company summary
    if result.get("company_info"):
        print(f"  Company:    {result['company_info'].company_name}")

    if result.get("silver_kpis"):
        kpis = result["silver_kpis"]
        print(f"  Fiscal Year: FY{kpis.fiscal_year}")
        if kpis.revenue:
            print(f"  Revenue:    ${kpis.revenue:,.0f}")
        if kpis.net_income:
            print(f"  Net Income: ${kpis.net_income:,.0f}")
        if kpis.gross_margin:
            print(f"  Gross Margin: {kpis.gross_margin:.1%}")

    if result.get("gold_risk_scores"):
        risk = result["gold_risk_scores"]
        print(f"  Risk Level: {risk.risk_level} ({risk.composite_score}/5.0)")

    # Deal recommendation
    if result.get("deal_recommendation"):
        rec = result["deal_recommendation"]
        icon = {"PROCEED": "+", "PROCEED_WITH_CONDITIONS": "~", "DO_NOT_PROCEED": "!"}.get(rec, "?")
        print(f"  Deal Rec:   [{icon}] {rec}")

    print(f"  Confidence: {result.get('confidence', 0):.0%}")

    # Insider signal summary
    if result.get("silver_insider_signal"):
        sig = result["silver_insider_signal"]
        if isinstance(sig, dict):
            signal_str = sig.get("signal", "N/A")
            buys = sig.get("total_buys", 0)
            sells = sig.get("total_sells", 0)
            cluster = " (CLUSTER)" if sig.get("cluster_detected") else ""
        else:
            signal_str = sig.signal
            buys = sig.total_buys
            sells = sig.total_sells
            cluster = " (CLUSTER)" if sig.cluster_detected else ""
        print(f"  Insider:    {signal_str} — {buys} buys / {sells} sells{cluster}")

    # Risk factors count
    if result.get("silver_risk_factors"):
        rf = result["silver_risk_factors"]
        count = len(rf) if isinstance(rf, list) else 0
        print(f"  Risk Factors: {count} identified")

    # Cross-workstream flags
    if result.get("gold_cross_workstream_flags"):
        flags = result["gold_cross_workstream_flags"]
        if flags:
            print(f"  Red Flags:  {len(flags)} cross-workstream flags")

    # Output files — organized by layer
    print(f"\n  Output Files:")
    bronze_keys = [
        ("bronze_company_info_path", "Company Info"),
        ("bronze_xbrl_facts_path", "XBRL Facts"),
        ("bronze_10k_risk_text_path", "10-K Risk Text"),
        ("bronze_form4_path", "Form 4"),
        ("bronze_13f_path", "13F Holdings"),
        ("bronze_8k_path", "8-K Filings"),
        ("bronze_def14a_path", "DEF 14A Proxy"),
    ]
    silver_keys = [
        ("silver_kpis_path", "Financial KPIs"),
        ("silver_risk_factors_path", "Risk Factors"),
        ("silver_insider_trades_path", "Insider Trades"),
        ("silver_institutional_path", "Institutional"),
        ("silver_events_path", "Material Events"),
        ("silver_governance_path", "Governance"),
    ]
    gold_keys = [
        ("gold_risk_path", "Risk Assessment"),
        ("gold_cross_workstream_path", "Cross-Workstream"),
    ]
    results_keys = [
        ("result_memo_path", "DD Memo"),
    ]

    for label, keys in [("Bronze", bronze_keys), ("Silver", silver_keys), ("Gold", gold_keys), ("Results", results_keys)]:
        printed = False
        for key, name in keys:
            if result.get(key):
                if not printed:
                    print(f"    [{label}]")
                    printed = True
                print(f"      {name:>17}: {result[key]}")

    # Errors
    errors = result.get("errors", [])
    if errors:
        print(f"\n  Warnings/Errors:")
        for err in errors:
            print(f"    [!] {err}")

    print()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python cli.py <TICKER>")
        print("Example: python cli.py AAPL")
        sys.exit(1)

    logging.basicConfig(level=logging.WARNING)
    asyncio.run(main(sys.argv[1]))
