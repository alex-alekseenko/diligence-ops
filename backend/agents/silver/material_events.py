"""Silver: Material Events — classifies 8-K events from bronze filings."""

from __future__ import annotations

import logging
import os

from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field

from backend.data.csv_writer import CsvWriter
from backend.models import MaterialEvent, PipelineState

logger = logging.getLogger(__name__)

ITEM_CODE_MAP: dict[str, tuple[str, int]] = {
    "1.01": ("Entry into Material Agreement", 3),
    "1.02": ("Termination of Material Agreement", 4),
    "2.01": ("Completion of Acquisition/Disposition", 3),
    "2.02": ("Results of Operations", 2),
    "2.04": ("Triggering Events (Default)", 5),
    "2.05": ("Costs from Exit/Disposal", 3),
    "2.06": ("Material Impairment", 4),
    "3.01": ("Delisting/Transfer/Failure to Satisfy", 5),
    "4.01": ("Changes in Accountant", 4),
    "4.02": ("Non-Reliance on Financial Statements", 5),
    "5.01": ("Changes in Control", 5),
    "5.02": ("Departure/Appointment of Officers", 3),
    "5.03": ("Amendments to Articles/Bylaws", 2),
    "7.01": ("Regulation FD Disclosure", 1),
    "8.01": ("Other Events", 2),
    "9.01": ("Financial Statements and Exhibits", 1),
}


class EventClassification(BaseModel):
    """LLM structured output for 8-K event classification."""
    events: list[MaterialEvent] = Field(default_factory=list)


EVENT_PROMPT = """\
You are a financial analyst. Classify each of these 8-K filings for {company_name}.

## 8-K Filings (last 12 months)
{events_text}

## Standard 8-K Item Codes
{item_codes}

For each event provide:
1. **filing_date** — the filing date
2. **item_code** — the most relevant 8-K item code (e.g. "1.01", "5.02")
3. **item_description** — brief description of the event type
4. **severity** — 1 to 5 (1=routine disclosure, 5=critical corporate event)
5. **summary** — 1-2 sentences about the significance for due diligence

Flag any Item 4.01, 4.02, or 5.02 events with severity >= 4.
"""


def _rule_based_classify(raw_events: list[dict]) -> list[dict]:
    """Classify 8-K events using item code lookup (no LLM)."""
    classified = []
    for event in raw_events:
        desc = event.get("description", "")
        matched_code = "8.01"
        matched_severity = 2
        matched_desc = "Other Events"
        for code, (code_desc, sev) in ITEM_CODE_MAP.items():
            # Match "Item X.XX" or code at start of description.
            # Avoids false positives on dates like "2025-01-01" embedded mid-string.
            if f"Item {code}" in desc or desc.startswith(code) or code_desc.lower() in desc.lower():
                matched_code = code
                matched_severity = sev
                matched_desc = code_desc
                break
        classified.append(
            MaterialEvent(
                filing_date=event.get("filing_date", ""),
                item_code=matched_code,
                item_description=matched_desc,
                severity=matched_severity,
                summary=desc[:200] if desc else "8-K filing",
            ).model_dump()
        )
    return classified


async def silver_material_events_agent(state: PipelineState) -> dict:
    """Classify 8-K events from bronze filings.

    Reads: state.bronze_8k_filings
    Writes: silver_material_events.csv
    """
    ticker = state["ticker"]
    company_info = state.get("company_info")
    company_name = company_info.company_name if company_info else ticker
    raw_events = state.get("bronze_8k_filings", [])
    errors: list[str] = []

    if not raw_events:
        return {
            "silver_material_events": [],
            "silver_events_path": None,
            "errors": errors,
            "progress_messages": ["Silver events: no bronze 8-K data"],
        }

    api_key = os.environ.get("OPENAI_API_KEY")
    if api_key:
        try:
            events_text = "\n".join(
                f"- {e['filing_date']}: {e.get('description', 'No description')}"
                for e in raw_events
            )
            item_codes_text = "\n".join(
                f"- {code}: {desc} (default severity {sev})"
                for code, (desc, sev) in ITEM_CODE_MAP.items()
            )
            llm = ChatOpenAI(model="gpt-4o", temperature=0)
            structured_llm = llm.with_structured_output(EventClassification)
            prompt = EVENT_PROMPT.format(
                company_name=company_name,
                events_text=events_text,
                item_codes=item_codes_text,
            )
            result = await structured_llm.ainvoke(prompt)
            classified = [e.model_dump() for e in result.events]
        except Exception as e:
            logger.warning(f"LLM event classification failed: {e}")
            classified = _rule_based_classify(raw_events)
            errors.append(f"Material events LLM failed: {e}")
    else:
        classified = _rule_based_classify(raw_events)
        errors.append("Material events used rule-based mode (no API key)")

    writer = CsvWriter(ticker)
    path = writer.write_silver(
        "material_events", classified, source_bronze="bronze_8k_filings.csv"
    )

    return {
        "silver_material_events": classified,
        "silver_events_path": str(path),
        "errors": errors,
        "progress_messages": [f"Classified {len(classified)} material events"],
    }
