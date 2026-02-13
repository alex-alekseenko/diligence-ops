"""Bronze: DEF 14A — fetches raw proxy statement text."""

from __future__ import annotations

import logging

from backend.data.csv_writer import CsvWriter
from backend.data.edgar_filings import EdgarFilingsClient, EdgarFilingsError
from backend.models import PipelineState

logger = logging.getLogger(__name__)


async def bronze_def14a_agent(state: PipelineState) -> dict:
    """Fetch the latest DEF 14A proxy statement text.

    Writes: bronze_def14a_proxy.csv
    Pure I/O — stores full text for silver-layer extraction.
    """
    ticker = state["ticker"]
    errors: list[str] = []

    try:
        client = EdgarFilingsClient()
        proxy_data = await client.get_def14a(ticker)
    except (EdgarFilingsError, Exception) as e:
        logger.warning(f"DEF 14A fetch failed for {ticker}: {e}")
        errors.append(f"Bronze DEF 14A: {e}")
        proxy_data = {}

    raw_text = proxy_data.get("text", "")
    if not raw_text:
        return {
            "bronze_def14a_proxy": proxy_data,
            "bronze_def14a_path": None,
            "errors": errors,
            "progress_messages": ["Bronze DEF 14A: no proxy data available"],
        }

    logger.info("DEF 14A for %s: %d chars", ticker, len(raw_text))

    writer = CsvWriter(ticker)
    path = writer.write_bronze(
        "def14a_proxy",
        [{"ticker": ticker.upper(), "filing_date": proxy_data.get("filing_date", ""), "proxy_text": raw_text}],
        source_url="https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&type=DEF+14A",
    )

    return {
        "bronze_def14a_proxy": proxy_data,
        "bronze_def14a_path": str(path),
        "errors": errors,
        "progress_messages": [f"Fetched DEF 14A proxy ({len(raw_text)} chars)"],
    }
