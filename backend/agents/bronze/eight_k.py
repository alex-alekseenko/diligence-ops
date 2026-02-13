"""Bronze: 8-K — fetches raw material event filings."""

from __future__ import annotations

import logging

from backend.data.csv_writer import CsvWriter
from backend.data.edgar_filings import EdgarFilingsClient, EdgarFilingsError
from backend.models import PipelineState

logger = logging.getLogger(__name__)


async def bronze_8k_agent(state: PipelineState) -> dict:
    """Fetch 8-K material event filings for the past 12 months.

    Writes: bronze_8k_filings.csv
    Pure I/O — no classification.
    """
    ticker = state["ticker"]
    errors: list[str] = []

    try:
        client = EdgarFilingsClient()
        events = await client.get_8k_filings(ticker, months=12)
    except (EdgarFilingsError, Exception) as e:
        logger.warning(f"8-K fetch failed for {ticker}: {e}")
        errors.append(f"Bronze 8-K: {e}")
        events = []

    if not events:
        return {
            "bronze_8k_filings": [],
            "bronze_8k_path": None,
            "errors": errors,
            "progress_messages": ["Bronze 8-K: no filings found"],
        }

    writer = CsvWriter(ticker)
    path = writer.write_bronze(
        "8k_filings",
        events,
        source_url="https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&type=8-K",
    )

    return {
        "bronze_8k_filings": events,
        "bronze_8k_path": str(path),
        "errors": errors,
        "progress_messages": [f"Fetched {len(events)} 8-K filings"],
    }
