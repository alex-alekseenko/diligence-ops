"""Silver: Governance — extracts governance data from bronze DEF 14A proxy text.

Uses chunked map-reduce extraction:
  1. Split full proxy text into ~30K char chunks
  2. Extract governance data points from each chunk in parallel (gpt-4o-mini)
  3. Merge all extracted data into final GovernanceData (gpt-4o)
"""

from __future__ import annotations

import asyncio
import json
import logging
import os

from langchain_openai import ChatOpenAI
from pydantic import BaseModel

from backend.data.csv_writer import CsvWriter
from backend.models import (
    DirectorInfo,
    GovernanceData,
    NEOCompensation,
    PipelineState,
)

logger = logging.getLogger(__name__)

CHUNK_SIZE = 30_000  # chars per chunk for map phase


# ---------------------------------------------------------------------------
# Map phase: extract raw data points from each chunk
# ---------------------------------------------------------------------------

EXTRACT_PROMPT = """\
You are a governance analyst extracting data from a DEF 14A proxy statement \
for {company_name}.

## Text (chunk {chunk_num} of {total_chunks})
{text}

## Instructions
Extract EVERY concrete data point you can find related to:

1. **CEO / Executive Compensation**: names, total comp amounts, salary, \
stock awards, bonuses, incentive plan payouts, prior-year comp, pay ratios
2. **Board Composition**: director names, independence status, committee \
memberships, board roles (Chairman, Lead Independent Director, etc.), \
age, and year appointed to the board (Director Since)
3. **Governance Provisions**: anti-takeover measures, dual-class shares, \
poison pills, staggered board, shareholder rights plans
4. **Named Executive Officers (NEOs)**: names, titles, individual \
compensation breakdowns (salary, stock awards, non-equity incentive, other)

Return a JSON object with these arrays (use empty arrays if nothing found):
{{
  "compensation": [
    {{"field": "ceo_total_comp|ceo_salary|ceo_stock_awards|ceo_pay_ratio|\
median_employee_pay|ceo_comp_prior|ceo_pay_growth|say_on_pay_pct",
      "value": "<numeric_or_string_value>",
      "year": <fiscal_year_int_or_null>}}
  ],
  "directors": [
    {{"name": "...", "is_independent": true/false/null,
      "committees": ["Audit", ...], "role": "Chairman|Lead Independent|...",
      "age": <int_or_null>, "director_since": <int_year_or_null>}}
  ],
  "neo_compensation": [
    {{"name": "...", "title": "CEO|CFO|COO|...",
      "total_comp": <float_or_null>, "salary": <float_or_null>,
      "stock_awards": <float_or_null>, "non_equity_incentive": <float_or_null>,
      "other_comp": <float_or_null>, "fiscal_year": <int_or_null>}}
  ],
  "governance": [
    {{"field": "has_poison_pill|has_staggered_board|has_dual_class|\
anti_takeover_provision|governance_flag",
      "value": "<value>"}}
  ]
}}

Only include data explicitly stated in the text. Do not guess or infer.
Return ONLY the JSON object, no other text.
"""


# ---------------------------------------------------------------------------
# Reduce phase: merge all chunk data into GovernanceData
# ---------------------------------------------------------------------------

MERGE_PROMPT = """\
You are a governance analyst consolidating data extracted from multiple chunks \
of {company_name}'s DEF 14A proxy statement.

## Extracted Data (from {n_chunks} chunks)
{extracted_json}

## Instructions
Merge all extracted data into a single, deduplicated governance record. \
When the same data point appears in multiple chunks, prefer the most specific \
or most recent value.

Return a JSON object with exactly these fields:
{{
  "ceo_name": "<string or empty>",
  "ceo_total_comp": <float_dollars_or_null>,
  "ceo_comp_prior": <float_prior_year_total_comp_or_null>,
  "ceo_pay_growth": <float_decimal_like_0.15_or_null>,
  "median_employee_pay": <float_or_null>,
  "ceo_pay_ratio": <float_ratio_like_533_or_null>,
  "board_size": <int_or_null>,
  "independent_directors": <int_or_null>,
  "board_independence_pct": <float_decimal_like_0.875_or_null>,
  "directors": [
    {{"name": "...", "is_independent": true/false/null,
      "committees": [...], "role": "...",
      "age": <int_or_null>, "director_since": <int_year_or_null>}}
  ],
  "has_poison_pill": <bool_or_null>,
  "has_staggered_board": <bool_or_null>,
  "has_dual_class": <bool_or_null>,
  "anti_takeover_provisions": ["..."],
  "neo_compensation": [
    {{"name": "...", "title": "...", "total_comp": ..., "salary": ...,
      "stock_awards": ..., "non_equity_incentive": ..., "other_comp": ...,
      "fiscal_year": ...}}
  ],
  "governance_flags": ["..."]
}}

Compute derived fields:
- **ceo_pay_growth**: (ceo_total_comp - ceo_comp_prior) / ceo_comp_prior \
if both values are available
- **board_independence_pct**: independent_directors / board_size if both available
- If the CEO is also in the NEO list, ensure consistency between ceo_total_comp \
and their NEO total_comp

Set fields to null if not found. Return ONLY the JSON object.
"""


async def _extract_chunk(
    llm: ChatOpenAI,
    company_name: str,
    chunk: str,
    chunk_num: int,
    total_chunks: int,
) -> dict:
    """Extract governance data points from a single text chunk."""
    prompt = EXTRACT_PROMPT.format(
        company_name=company_name,
        chunk_num=chunk_num,
        total_chunks=total_chunks,
        text=chunk,
    )
    try:
        resp = await llm.ainvoke(prompt)
        content = resp.content.strip()
        # Strip markdown code fences
        if content.startswith("```"):
            content = content.split("\n", 1)[1].rsplit("```", 1)[0].strip()
        return json.loads(content)
    except Exception as e:
        logger.warning("Chunk %d extraction failed: %s", chunk_num, e)
        return {"compensation": [], "directors": [], "neo_compensation": [], "governance": []}


async def _merge_chunks(
    llm: ChatOpenAI,
    company_name: str,
    chunk_results: list[dict],
) -> dict:
    """Merge extracted data from all chunks into final GovernanceData."""
    # Combine all chunk results into one consolidated object
    combined = {
        "compensation": [],
        "directors": [],
        "neo_compensation": [],
        "governance": [],
    }
    for cr in chunk_results:
        for key in combined:
            combined[key].extend(cr.get(key, []))

    # If nothing was extracted at all, short-circuit
    total_items = sum(len(v) for v in combined.values())
    if total_items == 0:
        return {}

    prompt = MERGE_PROMPT.format(
        company_name=company_name,
        n_chunks=len(chunk_results),
        extracted_json=json.dumps(combined, indent=2, default=str),
    )
    resp = await llm.ainvoke(prompt)
    content = resp.content.strip()
    if content.startswith("```"):
        content = content.split("\n", 1)[1].rsplit("```", 1)[0].strip()
    return json.loads(content)


def _build_governance_data(merged: dict) -> GovernanceData:
    """Convert merged JSON dict into GovernanceData model."""
    directors = []
    for d in merged.get("directors", []):
        if isinstance(d, dict):
            try:
                directors.append(DirectorInfo(**d))
            except Exception:
                logger.warning("Skipping invalid director: %s", d)

    neo_comp = []
    for n in merged.get("neo_compensation", []):
        if isinstance(n, dict):
            try:
                neo_comp.append(NEOCompensation(**n))
            except Exception:
                logger.warning("Skipping invalid NEO: %s", n)

    return GovernanceData(
        ceo_name=merged.get("ceo_name") or "",
        ceo_total_comp=merged.get("ceo_total_comp"),
        ceo_comp_prior=merged.get("ceo_comp_prior"),
        ceo_pay_growth=merged.get("ceo_pay_growth"),
        median_employee_pay=merged.get("median_employee_pay"),
        ceo_pay_ratio=merged.get("ceo_pay_ratio"),
        board_size=merged.get("board_size"),
        independent_directors=merged.get("independent_directors"),
        board_independence_pct=merged.get("board_independence_pct"),
        directors=directors,
        has_poison_pill=merged.get("has_poison_pill"),
        has_staggered_board=merged.get("has_staggered_board"),
        has_dual_class=merged.get("has_dual_class"),
        anti_takeover_provisions=merged.get("anti_takeover_provisions") or [],
        neo_compensation=neo_comp,
        governance_flags=merged.get("governance_flags") or [],
    )


class GovernanceAnalysis(BaseModel):
    """LLM structured output: governance and compensation data (single-call fallback)."""
    governance: GovernanceData


# Single-call prompt for short proxy texts that fit in one chunk
GOVERNANCE_PROMPT = """\
You are a governance analyst performing due diligence on {company_name}.

Extract governance and compensation data from this DEF 14A proxy statement.

## Proxy Statement Text
{proxy_text}

## Instructions
Extract as many of these fields as you can find in the text:

1. **ceo_name** — name of the CEO
2. **ceo_total_comp** — CEO's total compensation (current year, in dollars)
3. **ceo_comp_prior** — CEO's total compensation (prior year, if available)
4. **ceo_pay_growth** — percent change in CEO pay (decimal, e.g. 0.15 for 15%)
5. **median_employee_pay** — median employee annual compensation
6. **ceo_pay_ratio** — CEO pay ratio to median employee (e.g. 256)
7. **board_size** — total number of board directors
8. **independent_directors** — number of independent directors
9. **board_independence_pct** — fraction of independent directors (decimal, e.g. 0.80)
10. **directors** — list of directors with name, is_independent, committees, role, age, director_since (year)
11. **has_poison_pill** — true if poison pill / shareholder rights plan exists
12. **has_staggered_board** — true if board is classified/staggered
13. **has_dual_class** — true if dual-class share structure exists
14. **anti_takeover_provisions** — list of any anti-takeover provisions found
15. **neo_compensation** — list of named executive officers with name, title, \
total_comp, salary, stock_awards, non_equity_incentive, other_comp, fiscal_year
16. **governance_flags** — list of any governance concerns

Set fields to null if not found. Do not guess values.
"""


async def silver_governance_agent(state: PipelineState) -> dict:
    """Extract governance data from bronze DEF 14A proxy text.

    Uses chunked map-reduce for large proxy texts (>30K chars):
      - Map: parallel gpt-4o-mini calls extract data from each chunk
      - Reduce: gpt-4o merges all extracted data into GovernanceData

    Falls back to single gpt-4o call for short texts.

    Reads: state.bronze_def14a_proxy
    Writes: silver_governance.csv
    """
    ticker = state["ticker"]
    company_info = state.get("company_info")
    company_name = company_info.company_name if company_info else ticker
    proxy_data = state.get("bronze_def14a_proxy", {})
    errors: list[str] = []

    proxy_text = proxy_data.get("text", "")
    if not proxy_text:
        governance = GovernanceData().model_dump()
        return {
            "silver_governance": governance,
            "silver_governance_path": None,
            "errors": errors,
            "progress_messages": ["Silver governance: no bronze DEF 14A data"],
        }

    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        logger.warning("OPENAI_API_KEY not set, generating placeholder governance")
        governance = GovernanceData().model_dump()
        errors.append("Governance used placeholder mode (no API key)")
    elif len(proxy_text) <= CHUNK_SIZE:
        # Short text — single LLM call
        llm = ChatOpenAI(model="gpt-4o", temperature=0)
        structured_llm = llm.with_structured_output(GovernanceAnalysis)
        prompt = GOVERNANCE_PROMPT.format(
            company_name=company_name,
            proxy_text=proxy_text,
        )
        try:
            result = await structured_llm.ainvoke(prompt)
            governance = result.governance.model_dump()
        except Exception as e:
            logger.error(f"LLM governance analysis failed: {e}")
            governance = GovernanceData().model_dump()
            errors.append(f"Governance LLM failed: {e}")
    else:
        # Chunked map-reduce extraction
        try:
            governance = await _chunked_extraction(
                company_name, proxy_text, errors
            )
        except Exception as e:
            logger.error(f"Chunked governance extraction failed: {e}")
            governance = GovernanceData().model_dump()
            errors.append(f"Governance chunked extraction failed: {e}")

    # Flatten governance for CSV (exclude nested lists-of-models)
    gov_flat = {
        k: v for k, v in governance.items()
        if k not in ("directors", "neo_compensation")
    }

    writer = CsvWriter(ticker)
    path = writer.write_silver(
        "governance", [gov_flat], source_bronze="bronze_def14a_proxy.csv"
    )

    # Write directors as a separate flat CSV table
    directors = governance.get("directors", [])
    if directors:
        writer.write_silver(
            "governance_directors", directors, source_bronze="bronze_def14a_proxy.csv"
        )

    return {
        "silver_governance": governance,
        "silver_governance_path": str(path),
        "errors": errors,
        "progress_messages": [f"Governance analysis complete for {company_name}"],
    }


async def _chunked_extraction(
    company_name: str,
    proxy_text: str,
    errors: list[str],
) -> dict:
    """Run chunked map-reduce governance extraction."""
    # Split into chunks
    chunks = [
        proxy_text[i : i + CHUNK_SIZE]
        for i in range(0, len(proxy_text), CHUNK_SIZE)
    ]
    logger.info(
        "Chunked governance extraction: %d chars → %d chunks",
        len(proxy_text), len(chunks),
    )

    # Map phase: extract from each chunk in parallel using gpt-4o-mini
    map_llm = ChatOpenAI(model="gpt-4o-mini", temperature=0)
    tasks = [
        _extract_chunk(map_llm, company_name, chunk, i + 1, len(chunks))
        for i, chunk in enumerate(chunks)
    ]
    chunk_results = await asyncio.gather(*tasks)

    # Log extraction stats
    total_items = sum(
        sum(len(cr.get(k, [])) for k in ("compensation", "directors", "neo_compensation", "governance"))
        for cr in chunk_results
    )
    active_chunks = sum(
        1 for cr in chunk_results
        if any(cr.get(k) for k in ("compensation", "directors", "neo_compensation", "governance"))
    )
    logger.info(
        "Map phase: %d data points from %d/%d chunks",
        total_items, active_chunks, len(chunks),
    )

    if total_items == 0:
        errors.append("Chunked extraction found no governance data")
        return GovernanceData().model_dump()

    # Reduce phase: merge all data using gpt-4o
    reduce_llm = ChatOpenAI(model="gpt-4o", temperature=0)
    merged = await _merge_chunks(reduce_llm, company_name, chunk_results)

    governance_data = _build_governance_data(merged)
    return governance_data.model_dump()
