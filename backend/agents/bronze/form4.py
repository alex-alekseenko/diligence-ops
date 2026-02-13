"""Bronze: Form 4 — fetches raw insider transactions from Form 4 filings."""

from __future__ import annotations

import logging

from backend.data.csv_writer import CsvWriter
from backend.data.edgar_filings import EdgarFilingsClient, EdgarFilingsError
from backend.models import PipelineState

logger = logging.getLogger(__name__)


async def bronze_form4_agent(state: PipelineState) -> dict:
    """Fetch Form 4 insider transactions for the past 12 months.

    Writes: bronze_form4_transactions.csv
    Pure I/O — no signal computation.
    """
    ticker = state["ticker"]
    errors: list[str] = []

    try:
        client = EdgarFilingsClient()
        transactions = await client.get_form4_filings(ticker, months=12)
    except (EdgarFilingsError, Exception) as e:
        logger.warning(f"Form 4 fetch failed for {ticker}: {e}")
        errors.append(f"Bronze Form 4: {e}")
        transactions = []

    if not transactions:
        return {
            "bronze_form4_transactions": [],
            "bronze_form4_path": None,
            "errors": errors,
            "progress_messages": ["Bronze Form 4: no transactions found"],
        }

    writer = CsvWriter(ticker)
    path = writer.write_bronze(
        "form4_transactions",
        transactions,
        source_url="https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&type=4",
    )

    return {
        "bronze_form4_transactions": transactions,
        "bronze_form4_path": str(path),
        "errors": errors,
        "progress_messages": [f"Fetched {len(transactions)} Form 4 transactions"],
    }
