"""Bronze: Resolver — resolves ticker → CIK, fetches company metadata."""

from __future__ import annotations

import logging
from pathlib import Path

from backend.data.csv_writer import CsvWriter
from backend.data.edgar_client import EdgarClient, EdgarClientError
from backend.models import CompanyInfo, PipelineState

logger = logging.getLogger(__name__)


async def bronze_resolver_agent(state: PipelineState) -> dict:
    """Resolve ticker → CIK and fetch company info from SEC submissions API.

    Writes: bronze_company_info.csv
    Pure I/O — no transformations.
    """
    ticker = state["ticker"]
    errors: list[str] = []

    try:
        client = EdgarClient()
        cik = await client.resolve_cik(ticker)
        company_info = await client.get_company_info(cik)
    except EdgarClientError as e:
        logger.warning(f"EDGAR resolver failed for {ticker}: {e}")
        errors.append(f"Bronze resolver: {e}")
        # Minimal offline company info
        company_info = CompanyInfo(
            ticker=ticker.upper(),
            company_name=f"{ticker.upper()} (offline)",
            cik="0000000000",
        )

    # Write bronze table
    writer = CsvWriter(ticker)
    row = company_info.model_dump()
    # Flatten exchanges list to comma-separated string for CSV
    row["exchanges"] = ",".join(row.get("exchanges", []))
    path = writer.write_bronze(
        "company_info",
        [row],
        source_url="https://data.sec.gov/submissions/",
    )

    return {
        "company_info": company_info,
        "bronze_company_info_path": str(path),
        "errors": errors,
        "current_stage": "bronze",
        "progress_messages": [f"Resolved {ticker} → {company_info.company_name}"],
    }
