"""Silver: Risk Factors — classifies 10-K Item 1A risk factors from bronze text."""

from __future__ import annotations

import logging
import os

from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field

from backend.data.csv_writer import CsvWriter
from backend.models import PipelineState, RiskFactorItem

logger = logging.getLogger(__name__)

RISK_CATEGORIES = [
    "regulatory", "competitive", "operational", "financial",
    "legal", "technology", "macroeconomic", "esg",
]


class RiskFactorAnalysis(BaseModel):
    """LLM structured output: classified risk factors."""
    risk_factors: list[RiskFactorItem] = Field(default_factory=list)


RISK_NARRATIVE_PROMPT = """\
You are a senior financial analyst performing due diligence. \
Analyze the following risk factor disclosures from {company_name}'s 10-K filing (Item 1A).

## Item 1A Risk Factors Text
{risk_text}

## Instructions
Classify each distinct risk factor into one of these categories: {categories}

For each risk factor provide:
1. **category** — from the list above
2. **title** — short descriptive name (5-10 words)
3. **summary** — 1-2 sentence explanation of the risk
4. **severity** — 1 to 5 (1=low impact, 5=existential threat to the business)
5. **is_novel** — true if this risk appears specific/unique rather than boilerplate

Identify the 15-20 most significant risk factors. Prioritize by severity.
"""


async def silver_risk_factors_agent(state: PipelineState) -> dict:
    """Classify risk factors from bronze 10-K text.

    Reads: state.bronze_10k_risk_text
    Writes: silver_risk_factors.csv
    """
    ticker = state["ticker"]
    company_info = state.get("company_info")
    company_name = company_info.company_name if company_info else ticker
    risk_text = state.get("bronze_10k_risk_text", "")
    errors: list[str] = []

    if not risk_text:
        return {
            "silver_risk_factors": [],
            "silver_risk_factors_path": None,
            "errors": errors,
            "progress_messages": ["Silver risk factors: no bronze 10-K data"],
        }

    # Item 1A from edgartools is typically 15-30K chars, but some companies
    # (e.g. large financials) can have 60K+ risk disclosures.
    risk_text_truncated = risk_text[:45000]

    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        logger.warning("OPENAI_API_KEY not set, generating placeholder risk factors")
        factors = [RiskFactorItem(
            category="operational", title="General Business Risk",
            summary="Risk factors were identified but require LLM for classification.",
            severity=3, is_novel=False,
        ).model_dump()]
        errors.append("Risk factors used placeholder mode (no API key)")
    else:
        llm = ChatOpenAI(model="gpt-4o", temperature=0)
        structured_llm = llm.with_structured_output(RiskFactorAnalysis)
        prompt = RISK_NARRATIVE_PROMPT.format(
            company_name=company_name,
            risk_text=risk_text_truncated,
            categories=", ".join(RISK_CATEGORIES),
        )
        try:
            result = await structured_llm.ainvoke(prompt)
            factors = [f.model_dump() for f in result.risk_factors]
        except Exception as e:
            logger.error(f"LLM risk narrative failed: {e}")
            factors = [RiskFactorItem(
                category="operational", title="General Business Risk",
                summary="Risk factors were identified but require LLM for classification.",
                severity=3, is_novel=False,
            ).model_dump()]
            errors.append(f"Risk factors LLM failed: {e}")

    writer = CsvWriter(ticker)
    path = writer.write_silver("risk_factors", factors, source_bronze="bronze_10k_risk_text.csv")

    return {
        "silver_risk_factors": factors,
        "silver_risk_factors_path": str(path),
        "errors": errors,
        "progress_messages": [f"Classified {len(factors)} risk factors"],
    }
