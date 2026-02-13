"""Async SEC EDGAR API client for XBRL data retrieval."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import httpx
import pandas as pd

from backend.models import CompanyInfo, FinancialFact

logger = logging.getLogger(__name__)

# SEC EDGAR rate limit: 10 requests/second
_RATE_LIMIT = asyncio.Semaphore(10)
_MAX_RETRIES = 3
_BACKOFF_BASE = 1.0  # seconds


class EdgarClientError(Exception):
    """Raised when an EDGAR API request fails."""


class EdgarClient:
    """Async client for SEC EDGAR XBRL and submissions APIs."""

    BASE_URL = "https://data.sec.gov"
    TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"

    def __init__(self, user_agent: str = "DiligenceOps/0.1 (contact@example.com)"):
        self.user_agent = user_agent
        self._tickers_cache: dict[str, dict] | None = None

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            headers={"User-Agent": self.user_agent, "Accept-Encoding": "gzip, deflate"},
            timeout=30.0,
        )

    async def _get_json(self, url: str) -> dict:
        """Fetch JSON with rate limiting and exponential backoff."""
        for attempt in range(_MAX_RETRIES):
            async with _RATE_LIMIT:
                async with self._client() as client:
                    try:
                        resp = await client.get(url)
                        if resp.status_code == 429:
                            wait = _BACKOFF_BASE * (2**attempt)
                            logger.warning(f"Rate limited, retrying in {wait}s...")
                            await asyncio.sleep(wait)
                            continue
                        resp.raise_for_status()
                        return resp.json()
                    except httpx.HTTPStatusError as e:
                        if attempt == _MAX_RETRIES - 1:
                            raise EdgarClientError(
                                f"EDGAR API error {e.response.status_code}: {url}"
                            ) from e
                        await asyncio.sleep(_BACKOFF_BASE * (2**attempt))
                    except httpx.RequestError as e:
                        if attempt == _MAX_RETRIES - 1:
                            raise EdgarClientError(
                                f"Network error fetching {url}: {e}"
                            ) from e
                        await asyncio.sleep(_BACKOFF_BASE * (2**attempt))
        raise EdgarClientError(f"Max retries exceeded for {url}")

    async def _load_tickers(self) -> dict[str, dict]:
        """Load and cache the ticker→CIK mapping from SEC."""
        if self._tickers_cache is not None:
            return self._tickers_cache
        data = await self._get_json(self.TICKERS_URL)
        # data is {idx: {cik_str, ticker, title}}
        self._tickers_cache = {
            v["ticker"].upper(): v for v in data.values()
        }
        return self._tickers_cache

    async def resolve_cik(self, ticker: str) -> str:
        """Resolve a ticker symbol to a 10-digit zero-padded CIK."""
        tickers = await self._load_tickers()
        ticker_upper = ticker.upper().strip()
        if ticker_upper not in tickers:
            raise EdgarClientError(
                f"Ticker '{ticker_upper}' not found in SEC database"
            )
        cik_num = tickers[ticker_upper]["cik_str"]
        return str(cik_num).zfill(10)

    async def get_company_info(self, cik: str) -> CompanyInfo:
        """Fetch company metadata from the submissions API."""
        url = f"{self.BASE_URL}/submissions/CIK{cik}.json"
        data = await self._get_json(url)

        # Find latest 10-K filing date
        latest_10k_date = None
        recent = data.get("filings", {}).get("recent", {})
        forms = recent.get("form", [])
        dates = recent.get("filingDate", [])
        for form, date in zip(forms, dates):
            if form == "10-K":
                latest_10k_date = date
                break

        return CompanyInfo(
            ticker=data.get("tickers", [""])[0] if data.get("tickers") else "",
            company_name=data.get("name", ""),
            cik=cik,
            sic=str(data.get("sic", "")),
            sic_description=data.get("sicDescription", ""),
            fiscal_year_end=data.get("fiscalYearEnd", ""),
            exchanges=data.get("exchanges", []),
            entity_type=data.get("entityType", ""),
            category=data.get("category", ""),
            latest_10k_date=latest_10k_date,
        )

    async def get_company_facts(self, cik: str) -> list[FinancialFact]:
        """
        Fetch all XBRL company facts and return as FinancialFact list.

        Filters to 10-K (annual) filings only from us-gaap and dei taxonomies.
        """
        url = f"{self.BASE_URL}/api/xbrl/companyfacts/CIK{cik}.json"
        data = await self._get_json(url)

        facts: list[FinancialFact] = []
        for taxonomy in ("us-gaap", "dei"):
            taxonomy_data = data.get("facts", {}).get(taxonomy, {})
            for tag_name, tag_data in taxonomy_data.items():
                label = tag_data.get("label") or tag_name
                for unit_type, entries in tag_data.get("units", {}).items():
                    for entry in entries:
                        # Only keep 10-K annual filings
                        if entry.get("form") != "10-K":
                            continue
                        try:
                            facts.append(
                                FinancialFact(
                                    tag=tag_name,
                                    label=label,
                                    value=float(entry["val"]),
                                    unit=unit_type,
                                    start=entry.get("start"),
                                    end=entry["end"],
                                    fy=entry["fy"],
                                    fp=entry.get("fp", "FY"),
                                    form=entry["form"],
                                    filed=entry["filed"],
                                    accession=entry["accn"],
                                    frame=entry.get("frame"),
                                    taxonomy=taxonomy,
                                )
                            )
                        except (KeyError, ValueError, TypeError) as e:
                            logger.warning(f"Skipping malformed fact {tag_name}: {e}")
        return facts

    async def fetch_for_ticker(
        self, ticker: str
    ) -> tuple[CompanyInfo, list[FinancialFact]]:
        """Convenience: resolve ticker, fetch company info and facts in parallel."""
        cik = await self.resolve_cik(ticker)
        company_info, facts = await asyncio.gather(
            self.get_company_info(cik),
            self.get_company_facts(cik),
        )
        return company_info, facts

    @staticmethod
    def load_bronze_csv(path: Path) -> list[FinancialFact]:
        """Load bronze CSV as fallback (offline mode)."""
        df = pd.read_csv(path)
        facts = []
        for _, row in df.iterrows():
            facts.append(
                FinancialFact(
                    tag=row["tag"],
                    label=row["label"],
                    value=float(row["value"]),
                    unit=row["unit"],
                    start=row.get("start") if pd.notna(row.get("start")) else None,
                    end=row["end"],
                    fy=int(row["fy"]),
                    fp=row["fp"],
                    form=row["form"],
                    filed=row["filed"],
                    accession=row["accession"],
                    frame=row.get("frame") if pd.notna(row.get("frame")) else None,
                    taxonomy=row.get("taxonomy", "us-gaap"),
                )
            )
        return facts
