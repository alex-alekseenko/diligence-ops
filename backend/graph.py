"""LangGraph StateGraph orchestration — wires the 16-agent medallion pipeline.

v0.3 architecture (bronze → silver → gold → results):
  bronze_resolver → [6 bronze agents in parallel]
  each bronze → its silver agent
  silver_financial_kpis → gold_risk_assessment
  [all silver + gold_risk_assessment] → gold_cross_workstream → gold_memo → END
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from langgraph.graph import END, START, StateGraph

# Bronze agents
from backend.agents.bronze.def14a import bronze_def14a_agent
from backend.agents.bronze.eight_k import bronze_8k_agent
from backend.agents.bronze.form4 import bronze_form4_agent
from backend.agents.bronze.resolver import bronze_resolver_agent
from backend.agents.bronze.ten_k import bronze_10k_agent
from backend.agents.bronze.thirteen_f import bronze_13f_agent
from backend.agents.bronze.xbrl_facts import bronze_xbrl_agent

# Silver agents
from backend.agents.silver.financial_kpis import silver_financial_kpis_agent
from backend.agents.silver.governance import silver_governance_agent
from backend.agents.silver.insider_signal import silver_insider_signal_agent
from backend.agents.silver.institutional import silver_institutional_agent
from backend.agents.silver.material_events import silver_material_events_agent
from backend.agents.silver.risk_factors import silver_risk_factors_agent

# Gold agents
from backend.agents.gold.cross_workstream import gold_cross_workstream_agent
from backend.agents.gold.memo_writer import gold_memo_agent
from backend.agents.gold.risk_assessment import gold_risk_assessment_agent

from backend.models import PipelineProgress, PipelineState, initial_state

logger = logging.getLogger(__name__)


def create_pipeline() -> Any:
    """Build and compile the 16-node medallion LangGraph pipeline.

    Flow:
      START → bronze_resolver
        → [bronze_xbrl, bronze_10k, bronze_form4, bronze_13f,
           bronze_8k, bronze_def14a]                          (parallel)
        → [silver_financial_kpis, silver_risk_factors,
           silver_insider_signal, silver_institutional,
           silver_material_events, silver_governance]          (each after its bronze)
        → gold_risk_assessment (after silver_financial_kpis)
        → gold_cross_workstream (fan-in: all silver + gold_risk_assessment)
        → gold_memo
      → END
    """
    graph = StateGraph(PipelineState)

    # --- Bronze nodes ---
    graph.add_node("bronze_resolver", bronze_resolver_agent)
    graph.add_node("bronze_xbrl", bronze_xbrl_agent)
    graph.add_node("bronze_10k", bronze_10k_agent)
    graph.add_node("bronze_form4", bronze_form4_agent)
    graph.add_node("bronze_13f", bronze_13f_agent)
    graph.add_node("bronze_8k", bronze_8k_agent)
    graph.add_node("bronze_def14a", bronze_def14a_agent)

    # --- Silver nodes ---
    graph.add_node("silver_financial_kpis", silver_financial_kpis_agent)
    graph.add_node("silver_risk_factors", silver_risk_factors_agent)
    graph.add_node("silver_insider_signal", silver_insider_signal_agent)
    graph.add_node("silver_institutional", silver_institutional_agent)
    graph.add_node("silver_material_events", silver_material_events_agent)
    graph.add_node("silver_governance", silver_governance_agent)

    # --- Gold nodes ---
    graph.add_node("gold_risk_assessment", gold_risk_assessment_agent)
    graph.add_node("gold_cross_workstream", gold_cross_workstream_agent)
    graph.add_node("gold_memo", gold_memo_agent)

    # === Edges ===

    # START → bronze_resolver (resolves ticker→CIK, fetches company info)
    graph.add_edge(START, "bronze_resolver")

    # Fan-out: bronze_resolver → 6 parallel bronze agents
    graph.add_edge("bronze_resolver", "bronze_xbrl")
    graph.add_edge("bronze_resolver", "bronze_10k")
    graph.add_edge("bronze_resolver", "bronze_form4")
    graph.add_edge("bronze_resolver", "bronze_13f")
    graph.add_edge("bronze_resolver", "bronze_8k")
    graph.add_edge("bronze_resolver", "bronze_def14a")

    # Bronze → Silver (each silver starts after its bronze dependency)
    graph.add_edge("bronze_xbrl", "silver_financial_kpis")
    graph.add_edge("bronze_10k", "silver_risk_factors")
    graph.add_edge("bronze_form4", "silver_insider_signal")
    graph.add_edge("bronze_13f", "silver_institutional")
    graph.add_edge("bronze_8k", "silver_material_events")
    graph.add_edge("bronze_def14a", "silver_governance")

    # Silver → Gold: risk assessment depends on silver KPIs
    graph.add_edge("silver_financial_kpis", "gold_risk_assessment")

    # Fan-in: gold_cross_workstream waits for ALL silver outputs + gold_risk_assessment
    graph.add_edge("gold_risk_assessment", "gold_cross_workstream")
    graph.add_edge("silver_risk_factors", "gold_cross_workstream")
    graph.add_edge("silver_insider_signal", "gold_cross_workstream")
    graph.add_edge("silver_institutional", "gold_cross_workstream")
    graph.add_edge("silver_material_events", "gold_cross_workstream")
    graph.add_edge("silver_governance", "gold_cross_workstream")

    # gold_cross_workstream → gold_memo → END
    graph.add_edge("gold_cross_workstream", "gold_memo")
    graph.add_edge("gold_memo", END)

    return graph.compile()


# Pre-compiled pipeline instance
pipeline = create_pipeline()

# Agent name mapping for progress updates
_NODE_META: dict[str, dict[str, Any]] = {
    # Bronze layer (0-30%)
    "bronze_resolver":  {"stage": "bronze", "agent": "resolver",  "pct": 5},
    "bronze_xbrl":      {"stage": "bronze", "agent": "xbrl",      "pct": 10},
    "bronze_10k":       {"stage": "bronze", "agent": "10k",       "pct": 13},
    "bronze_form4":     {"stage": "bronze", "agent": "form4",     "pct": 16},
    "bronze_13f":       {"stage": "bronze", "agent": "13f",       "pct": 19},
    "bronze_8k":        {"stage": "bronze", "agent": "8k",        "pct": 22},
    "bronze_def14a":    {"stage": "bronze", "agent": "def14a",    "pct": 25},
    # Silver layer (30-70%)
    "silver_financial_kpis":  {"stage": "silver", "agent": "financial_kpis",  "pct": 35},
    "silver_risk_factors":    {"stage": "silver", "agent": "risk_factors",    "pct": 42},
    "silver_insider_signal":  {"stage": "silver", "agent": "insider_signal",  "pct": 49},
    "silver_institutional":   {"stage": "silver", "agent": "institutional",   "pct": 53},
    "silver_material_events": {"stage": "silver", "agent": "material_events", "pct": 57},
    "silver_governance":      {"stage": "silver", "agent": "governance",      "pct": 61},
    # Gold layer (70-100%)
    "gold_risk_assessment":   {"stage": "gold", "agent": "risk_assessment",   "pct": 75},
    "gold_cross_workstream":  {"stage": "gold", "agent": "cross_workstream",  "pct": 88},
    "gold_memo":              {"stage": "gold", "agent": "memo_writer",       "pct": 100},
}


async def run_pipeline(
    ticker: str,
    progress_callback: Callable[[PipelineProgress], Any] | None = None,
    run_id: str = "",
) -> PipelineState:
    """Run the full diligence pipeline for a ticker.

    Args:
        ticker: Stock ticker symbol (e.g., "AAPL")
        progress_callback: Optional async/sync callback for progress updates
        run_id: Unique run identifier for progress tracking

    Returns:
        Final PipelineState with all results
    """
    state = initial_state(ticker)
    final_state = state

    async for event in pipeline.astream(state, stream_mode="updates"):
        for node_name, node_output in event.items():
            # Merge node output into running state, respecting
            # operator.add reducers for list fields (errors, progress_messages)
            if isinstance(node_output, dict):
                for key, value in node_output.items():
                    if key in ("errors", "progress_messages") and isinstance(value, list):
                        final_state[key] = final_state.get(key, []) + value
                    else:
                        final_state[key] = value

            # Send progress update
            if progress_callback and node_name in _NODE_META:
                meta = _NODE_META[node_name]
                messages = (
                    node_output.get("progress_messages", [])
                    if isinstance(node_output, dict)
                    else []
                )
                message = messages[-1] if messages else f"Completed {node_name}"
                progress = PipelineProgress(
                    run_id=run_id,
                    stage=meta["stage"],
                    agent=meta["agent"],
                    message=message,
                    progress_pct=meta["pct"],
                )
                result = progress_callback(progress)
                # Await if callback is async
                if hasattr(result, "__await__"):
                    await result

    # Fire completion callback
    if progress_callback:
        progress = PipelineProgress(
            run_id=run_id,
            stage="complete",
            agent="pipeline",
            message=f"Pipeline complete for {ticker}",
            progress_pct=100,
        )
        result = progress_callback(progress)
        if hasattr(result, "__await__"):
            await result

    return final_state
