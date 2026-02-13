"""Silver: Institutional — classifies and ranks 13F institutional holders."""

from __future__ import annotations

import logging

from backend.data.csv_writer import CsvWriter
from backend.models import PipelineState

logger = logging.getLogger(__name__)

PASSIVE_MANAGERS = {
    "vanguard", "blackrock", "state street", "ishares",
    "fidelity index", "schwab", "spdr", "invesco",
}


async def silver_institutional_agent(state: PipelineState) -> dict:
    """Classify passive/active holders from bronze 13F data.

    Reads: state.bronze_13f_holdings
    Writes: silver_institutional_holders.csv
    """
    ticker = state["ticker"]
    raw_holders = list(state.get("bronze_13f_holdings", []))
    errors: list[str] = []

    if not raw_holders:
        return {
            "silver_institutional_holders": [],
            "silver_institutional_path": None,
            "errors": errors,
            "progress_messages": ["Silver institutional: no bronze 13F data"],
        }

    # Classify passive vs active
    for holder in raw_holders:
        name_lower = holder.get("holder_name", "").lower()
        if any(p in name_lower for p in PASSIVE_MANAGERS):
            holder["holder_type"] = "passive"
        else:
            holder["holder_type"] = "active"

    # Take top 10 by shares
    sorted_holders = sorted(
        raw_holders, key=lambda h: h.get("shares", 0), reverse=True
    )[:10]

    writer = CsvWriter(ticker)
    path = writer.write_silver(
        "institutional_holders", sorted_holders, source_bronze="bronze_13f_holdings.csv"
    )

    return {
        "silver_institutional_holders": sorted_holders,
        "silver_institutional_path": str(path),
        "errors": errors,
        "progress_messages": [f"Institutional: {len(sorted_holders)} top holders classified"],
    }
