"""Bronze: 10-K — fetches raw Item 1A risk factors text from latest 10-K."""

from __future__ import annotations

import logging

from backend.data.csv_writer import CsvWriter
from backend.data.edgar_filings import EdgarFilingsClient, EdgarFilingsError
from backend.models import PipelineState

logger = logging.getLogger(__name__)


async def bronze_10k_agent(state: PipelineState) -> dict:
    """Fetch Item 1A risk factors text from the latest 10-K filing.

    Writes: bronze_10k_risk_text.csv
    Pure I/O — no classification or LLM calls.
    """
    ticker = state["ticker"]
    errors: list[str] = []

    try:
        client = EdgarFilingsClient()
        risk_text = await client.get_10k_risk_factors(ticker)
    except (EdgarFilingsError, Exception) as e:
        logger.warning(f"10-K fetch failed for {ticker}: {e}")
        errors.append(f"Bronze 10-K: {e}")
        risk_text = ""

    if not risk_text:
        return {
            "bronze_10k_risk_text": "",
            "bronze_10k_risk_text_path": None,
            "errors": errors,
            "progress_messages": ["Bronze 10-K: no risk text available"],
        }

    writer = CsvWriter(ticker)
    path = writer.write_bronze(
        "10k_risk_text",
        [{"ticker": ticker.upper(), "risk_text": risk_text}],
        source_url="https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&type=10-K",
    )

    return {
        "bronze_10k_risk_text": risk_text,
        "bronze_10k_risk_text_path": str(path),
        "errors": errors,
        "progress_messages": [f"Fetched 10-K risk text ({len(risk_text)} chars)"],
    }
