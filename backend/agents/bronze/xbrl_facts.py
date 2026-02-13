"""Bronze: XBRL Facts — fetches raw XBRL company facts from SEC EDGAR."""

from __future__ import annotations

import logging
from pathlib import Path

from backend.data.csv_writer import CsvWriter
from backend.data.edgar_client import EdgarClient, EdgarClientError
from backend.models import FinancialFact, PipelineState

logger = logging.getLogger(__name__)


async def bronze_xbrl_agent(state: PipelineState) -> dict:
    """Fetch all XBRL company facts for the resolved CIK.

    Writes: bronze_xbrl_facts.csv
    Depends on: bronze_resolver (needs company_info.cik)
    Pure I/O — no transformations.
    """
    ticker = state["ticker"]
    company_info = state.get("company_info")
    errors: list[str] = []

    if not company_info or company_info.cik == "0000000000":
        # Try offline fallback
        fallback_path = Path("examples") / f"{ticker.upper()}_bronze_facts.csv"
        if fallback_path.exists():
            facts = EdgarClient.load_bronze_csv(fallback_path)
            errors.append("Using offline fallback for XBRL facts")
        else:
            errors.append("No CIK available and no offline data for XBRL facts")
            return {
                "bronze_facts": [],
                "bronze_xbrl_facts_path": None,
                "errors": errors,
                "progress_messages": ["Bronze XBRL: no data available"],
            }
    else:
        try:
            client = EdgarClient()
            facts = await client.get_company_facts(company_info.cik)
        except EdgarClientError as e:
            logger.warning(f"XBRL fetch failed for {ticker}: {e}")
            # Try offline fallback
            fallback_path = Path("examples") / f"{ticker.upper()}_bronze_facts.csv"
            if fallback_path.exists():
                facts = EdgarClient.load_bronze_csv(fallback_path)
                errors.append(f"Using offline fallback: {e}")
            else:
                errors.append(f"Bronze XBRL: {e}")
                return {
                    "bronze_facts": [],
                    "bronze_xbrl_facts_path": None,
                    "errors": errors,
                    "progress_messages": [f"Bronze XBRL: fetch failed — {e}"],
                }

    # Write bronze table
    writer = CsvWriter(ticker)
    records = [f.model_dump() for f in facts]
    path = writer.write_bronze(
        "xbrl_facts",
        records,
        source_url="https://data.sec.gov/api/xbrl/companyfacts/",
    )

    logger.info(f"Bronze XBRL: {ticker} → {len(facts)} facts")

    return {
        "bronze_facts": facts,
        "bronze_xbrl_facts_path": str(path),
        "errors": errors,
        "progress_messages": [f"Fetched {len(facts)} XBRL facts"],
    }
