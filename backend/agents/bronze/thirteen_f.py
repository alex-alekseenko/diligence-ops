"""Bronze: Institutional Holdings — fetches ownership data from SC 13G filings."""

from __future__ import annotations

import logging

from backend.data.csv_writer import CsvWriter
from backend.data.edgar_filings import EdgarFilingsClient, EdgarFilingsError
from backend.models import PipelineState

logger = logging.getLogger(__name__)


async def bronze_13f_agent(state: PipelineState) -> dict:
    """Fetch institutional holdings from SC 13G filings.

    SC 13G filings are filed by institutional investors holding >5% of a
    company's shares.  Unlike 13F-HR (filed BY the manager), SC 13G filings
    appear on the target company's EDGAR page, making them discoverable.

    Writes: bronze_13f_holdings.csv
    Pure I/O — no classification.
    """
    ticker = state["ticker"]
    errors: list[str] = []

    try:
        client = EdgarFilingsClient()
        holders = await client.get_institutional_holders(ticker)
    except (EdgarFilingsError, Exception) as e:
        logger.warning(f"SC 13G fetch failed for {ticker}: {e}")
        errors.append(f"Bronze SC 13G: {e}")
        holders = []

    if not holders:
        return {
            "bronze_13f_holdings": [],
            "bronze_13f_path": None,
            "errors": errors,
            "progress_messages": ["Bronze SC 13G: no institutional holders found"],
        }

    writer = CsvWriter(ticker)
    path = writer.write_bronze(
        "13f_holdings",
        holders,
        source_url="https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&type=SC+13G",
    )

    return {
        "bronze_13f_holdings": holders,
        "bronze_13f_path": str(path),
        "errors": errors,
        "progress_messages": [f"Fetched {len(holders)} institutional holders from SC 13G"],
    }
