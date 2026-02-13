"""edgartools wrapper for SEC filings access (10-K, Form 4, SC 13G, 8-K, DEF 14A).

Provides an async-compatible interface over the synchronous edgartools library.
Each method runs edgartools calls in a thread executor and returns primitive
dicts/lists for easy state serialization.
"""

from __future__ import annotations

import asyncio
import logging
import math
import re
from datetime import datetime, timedelta
from functools import partial

logger = logging.getLogger(__name__)


def _safe_float(val) -> float | None:
    """Convert to float, returning None for NaN/Inf/missing values."""
    if val is None:
        return None
    try:
        f = float(val)
        if math.isnan(f) or math.isinf(f):
            return None
        return f
    except (ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# DEF 14A section extraction — targeted governance data capture
# ---------------------------------------------------------------------------

# (section_name, regex_pattern, weight) — weight controls relative budget share
PROXY_SECTION_PATTERNS: list[tuple[str, str, float]] = [
    ("Compensation Discussion & Analysis", r"Compensation\s+Discussion\s+and\s+Analysis", 2.0),
    ("Summary Compensation Table", r"(?:\d{4}\s+)?Summary\s+Compensation\s+Table", 1.5),
    ("Pay Ratio", r"(?:\d{4}\s+)?Pay\s+Ratio", 1.0),
    ("Director Independence", r"Director\s+Independence", 1.0),
    ("Corporate Governance", r"Corporate\s+Governance\b", 1.0),
    ("Board Meetings and Committees", r"Board\s+Meetings\s+and\s+Committees", 1.0),
    ("Board Leadership Structure", r"Board\s+Leadership\s+Structure", 0.8),
    ("Executive Officers", r"Executive\s+Officers\b", 0.8),
    ("Director Compensation", r"(?:Compensation\s+of\s+Directors|Director\s+Compensation)", 1.0),
    ("Pay Versus Performance", r"(?:\d{4}\s+)?Pay\s+(?:Versus|vs\.?)\s+Performance", 1.5),
    ("Equity Compensation Plan", r"Equity\s+Compensation\s+Plan", 0.8),
    ("Board Risk Oversight", r"Board\s+(?:Role\s+in\s+)?Risk\s+Oversight", 0.8),
]


def extract_proxy_sections(full_text: str, budget: int = 50_000) -> tuple[str, list[str]]:
    """Extract governance-relevant sections from a DEF 14A proxy statement.

    Two-pass approach:
      1. Scan the full text to locate all target section headings.
      2. Distribute the character budget across found sections proportionally
         to their weights, then extract.

    Returns:
        (extracted_text, list_of_section_names_found).
        Falls back to naive first-N-chars truncation if no sections are detected.
    """
    if len(full_text) <= budget:
        return full_text, ["full_text"]

    # ── Pass 1: locate sections ──────────────────────────────────────────
    hits: list[tuple[str, float, int]] = []  # (name, weight, char_position)

    for name, pattern, weight in PROXY_SECTION_PATTERNS:
        regex = re.compile(pattern, re.IGNORECASE)

        for m in regex.finditer(full_text):
            # Skip ToC entries: real sections are followed by prose (multiple
            # sentences), while ToC entries are followed by more headings.
            after = full_text[m.end():m.end() + 500]
            if after.count(".") < 2:
                continue
            # Skip if too close to an already-found section (< 200 chars)
            if any(abs(m.start() - pos) < 200 for _, _, pos in hits):
                continue
            hits.append((name, weight, m.start()))
            break

    if not hits:
        logger.warning("No governance sections detected — falling back to truncation")
        return full_text[:budget], ["truncated"]

    # ── Pass 2: allocate budget proportionally and extract ────────────────
    # Sort by position so extracted text is in document order
    hits.sort(key=lambda h: h[2])

    total_weight = sum(w for _, w, _ in hits)
    separator = "\n\n---\n\n"
    separator_overhead = len(separator) * (len(hits) - 1)
    available = budget - separator_overhead

    extracted: list[str] = []
    found_sections: list[str] = []
    used_ranges: list[tuple[int, int]] = []

    for name, weight, pos in hits:
        alloc = int(available * weight / total_weight)
        # Don't exceed the distance to the next found section
        next_positions = [p for _, _, p in hits if p > pos]
        if next_positions:
            max_before_next = next_positions[0] - pos
            alloc = min(alloc, max_before_next)
        # Don't overlap with previously extracted ranges
        for rs, re_ in used_ranges:
            if pos < re_ and pos + alloc > rs:
                alloc = min(alloc, rs - pos)
        if alloc <= 0:
            continue

        chunk = full_text[pos:pos + alloc]
        extracted.append(chunk.strip())
        used_ranges.append((pos, pos + alloc))
        found_sections.append(name)

    if not extracted:
        logger.warning("Section extraction yielded nothing — falling back to truncation")
        return full_text[:budget], ["truncated"]

    logger.info("Extracted %d proxy sections (%d chars): %s",
                len(found_sections), sum(len(p) for p in extracted), found_sections)
    return separator.join(extracted), found_sections


class EdgarFilingsError(Exception):
    """Raised when an edgartools operation fails."""


class EdgarFilingsClient:
    """Wrapper around edgartools for filing-level data access.

    edgartools is synchronous and handles its own SEC rate limiting,
    so we run calls in a thread executor for async compatibility.
    """

    def __init__(self, identity: str = "DiligenceOps/0.2 contact@example.com"):
        from edgar import set_identity

        set_identity(identity)

    @staticmethod
    async def _run_sync(fn, *args, **kwargs):
        """Run a synchronous edgartools call in a thread executor."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, partial(fn, *args, **kwargs))

    async def get_10k_risk_factors(self, ticker: str) -> str:
        """Fetch Item 1A (Risk Factors) text from the latest 10-K filing."""

        def _fetch(t: str) -> str:
            from edgar import Company

            company = Company(t)
            filings = company.get_filings(form="10-K")
            if not filings or len(filings) == 0:
                raise EdgarFilingsError(f"No 10-K filings found for {t}")
            filing = filings[0]
            tenk = filing.obj()
            # edgartools supports both property and dict access
            risk_text = ""
            if hasattr(tenk, "risk_factors") and tenk.risk_factors:
                risk_text = str(tenk.risk_factors)
            elif hasattr(tenk, "__getitem__"):
                try:
                    item_1a = tenk["Item 1A"]
                    risk_text = str(item_1a) if item_1a else ""
                except (KeyError, IndexError):
                    pass
            return risk_text

        return await self._run_sync(_fetch, ticker)

    async def get_form4_filings(
        self, ticker: str, months: int = 12, max_filings: int = 50
    ) -> list[dict]:
        """Fetch Form 4 insider transactions for the past N months."""

        def _fetch(t: str, m: int) -> list[dict]:
            from edgar import Company

            company = Company(t)
            cutoff = datetime.now() - timedelta(days=m * 30)
            cutoff_str = cutoff.strftime("%Y-%m-%d")
            filings = company.get_filings(form="4")
            transactions: list[dict] = []
            if not filings:
                return transactions

            filings_processed = 0
            for filing in filings:
                if filings_processed >= max_filings:
                    break
                filings_processed += 1
                filing_date = str(filing.filing_date) if hasattr(filing, "filing_date") else ""
                if filing_date and filing_date < cutoff_str:
                    break
                try:
                    form4 = filing.obj()

                    # Extract owner info from reporting_owners (plural)
                    owner_name = getattr(form4, "insider_name", "") or ""
                    owner_title = ""
                    if hasattr(form4, "reporting_owners"):
                        for owner in form4.reporting_owners:
                            owner_name = owner_name or getattr(owner, "name", "")
                            owner_title = getattr(owner, "officer_title", "") or ""
                            if not owner_title:
                                if getattr(owner, "is_director", False):
                                    owner_title = "Director"
                                elif getattr(owner, "is_ten_pct_owner", False):
                                    owner_title = "10% Owner"
                            break  # Use first owner

                    # Extract non-derivative transactions
                    ndt = getattr(form4, "non_derivative_table", None)
                    if ndt and hasattr(ndt, "has_transactions") and ndt.has_transactions:
                        for txn in ndt.transactions:
                            shares = _safe_float(txn.shares) if hasattr(txn, "shares") else None
                            shares = shares or 0
                            price = _safe_float(txn.price) if hasattr(txn, "price") else None
                            val = abs(shares * price) if price and shares else None
                            txn_date = str(txn.date) if hasattr(txn, "date") and txn.date else filing_date
                            transactions.append({
                                "insider_name": owner_name,
                                "insider_title": str(owner_title),
                                "transaction_date": txn_date,
                                "transaction_code": getattr(txn, "transaction_code", ""),
                                "shares": shares,
                                "price_per_share": price,
                                "value": val,
                                "shares_owned_after": _safe_float(txn.remaining) if hasattr(txn, "remaining") else None,
                                "is_direct": getattr(txn, "direct_indirect", "D") == "D",
                                "filing_date": filing_date,
                            })

                    # Also extract derivative transactions (option exercises, etc.)
                    dt = getattr(form4, "derivative_table", None)
                    if dt and hasattr(dt, "has_transactions") and dt.has_transactions:
                        for txn in dt.transactions:
                            shares = _safe_float(txn.shares) if hasattr(txn, "shares") else None
                            shares = shares or 0
                            price = _safe_float(txn.price) if hasattr(txn, "price") else None
                            val = abs(shares * price) if price and shares else None
                            txn_date = str(txn.date) if hasattr(txn, "date") and txn.date else filing_date
                            transactions.append({
                                "insider_name": owner_name,
                                "insider_title": str(owner_title),
                                "transaction_date": txn_date,
                                "transaction_code": getattr(txn, "transaction_code", ""),
                                "shares": shares,
                                "price_per_share": price,
                                "value": val,
                                "shares_owned_after": _safe_float(txn.remaining) if hasattr(txn, "remaining") else None,
                                "is_direct": getattr(txn, "direct_indirect", "D") == "D",
                                "filing_date": filing_date,
                            })
                except Exception as e:
                    logger.warning(f"Failed to parse Form 4 filing: {e}")
            return transactions

        return await self._run_sync(_fetch, ticker, months)

    async def get_institutional_holders(self, ticker: str) -> list[dict]:
        """Fetch institutional holders from SC 13G/A filings.

        SC 13G filings are filed by institutional investors who beneficially
        own >5% of a company's shares. They appear on the target company's
        filing page, making them easy to discover (unlike 13F-HR filings
        which are filed by the manager, not the target company).
        """

        def _parse_shares_from_text(text: str) -> tuple[int | None, float | None]:
            """Extract aggregate shares (row 9) and percent (row 11) from SC 13G text."""
            shares = None
            pct = None
            # Row 9: AGGREGATE AMOUNT BENEFICIALLY OWNED
            # Match "9." or "AGGREGATE AMOUNT" followed by the number on a subsequent line
            m = re.search(
                r"(?:9\.\s*AGGREGATE|AGGREGATE\s+AMOUNT\s+BENEFICIALLY)[^\n]*\n+\s*([\d,]+)",
                text,
                re.IGNORECASE,
            )
            if m:
                try:
                    val = int(m.group(1).replace(",", ""))
                    # Sanity check: shares should be at least 1000 for institutional filings
                    if val >= 1000:
                        shares = val
                except ValueError:
                    pass
            # Row 11: PERCENT OF CLASS
            m = re.search(
                r"(?:11\.\s*PERCENT|PERCENT\s+OF\s+CLASS)[^\n]*\n+\s*([\d.]+)\s*%?",
                text,
                re.IGNORECASE,
            )
            if m:
                try:
                    pct = float(m.group(1))
                except ValueError:
                    pass
            return shares, pct

        def _fetch(t: str) -> list[dict]:
            from edgar import Company

            holders: list[dict] = []
            seen_filers: set[str] = set()
            try:
                company = Company(t)
                filings = company.get_filings(form="SC 13G")
                if not filings or len(filings) == 0:
                    return holders

                # Iterate recent filings to collect the latest from each filer.
                # SC 13G filings are typically filed annually in Feb; we only
                # need the most recent per institution.  Cap total filings
                # parsed to avoid excessive SEC requests.
                max_filings = 30
                filings_parsed = 0
                for filing in filings:
                    if len(holders) >= 20 or filings_parsed >= max_filings:
                        break
                    filings_parsed += 1
                    try:
                        filer_name = "Unknown"
                        header = filing.header
                        if header and header.filers:
                            filer_str = str(header.filers[0])
                            match = re.search(r"([\w\s&,.']+)\s*\[\d+\]", filer_str)
                            if match:
                                filer_name = match.group(1).strip()
                        # Deduplicate by filer (keep only the most recent)
                        filer_key = filer_name.upper()
                        if filer_key in seen_filers:
                            continue
                        seen_filers.add(filer_key)

                        filing_date = str(filing.filing_date) if hasattr(filing, "filing_date") else ""
                        text = filing.text()[:8000] if hasattr(filing, "text") else ""
                        shares, pct = _parse_shares_from_text(text)

                        holders.append({
                            "holder_name": filer_name,
                            "shares": shares or 0,
                            "value": None,
                            "pct_of_portfolio": pct,
                            "change_shares": None,
                            "change_pct": None,
                            "holder_type": "institutional",
                            "filing_date": filing_date,
                        })
                    except Exception as e:
                        logger.warning(f"Failed to parse SC 13G filing: {e}")
            except Exception as e:
                logger.warning(f"Failed to fetch SC 13G holders for {t}: {e}")
            return holders

        return await self._run_sync(_fetch, ticker)

    async def get_8k_filings(
        self, ticker: str, months: int = 12
    ) -> list[dict]:
        """Fetch 8-K filings for the past N months."""

        def _fetch(t: str, m: int) -> list[dict]:
            from edgar import Company

            company = Company(t)
            cutoff = datetime.now() - timedelta(days=m * 30)
            cutoff_str = cutoff.strftime("%Y-%m-%d")
            filings = company.get_filings(form="8-K")
            events: list[dict] = []
            if not filings:
                return events
            for filing in filings:
                filing_date = str(filing.filing_date) if hasattr(filing, "filing_date") else ""
                if filing_date and filing_date < cutoff_str:
                    break
                events.append({
                    "filing_date": filing_date,
                    "form": getattr(filing, "form", "8-K"),
                    "description": str(filing.description) if hasattr(filing, "description") else "",
                    "accession": str(filing.accession_number) if hasattr(filing, "accession_number") else "",
                })
            return events

        return await self._run_sync(_fetch, ticker, months)

    async def get_def14a(self, ticker: str) -> dict:
        """Fetch DEF 14A proxy statement text."""

        def _fetch(t: str) -> dict:
            from edgar import Company

            company = Company(t)
            filings = company.get_filings(form="DEF 14A")
            if not filings or len(filings) == 0:
                return {}
            filing = filings[0]
            proxy_text = ""
            try:
                if hasattr(filing, "text"):
                    proxy_text = filing.text()
                elif hasattr(filing, "html"):
                    proxy_text = filing.html()
            except Exception as e:
                logger.warning(f"Failed to extract DEF 14A text: {e}")
            return {
                "filing_date": str(filing.filing_date) if hasattr(filing, "filing_date") else "",
                "text": proxy_text[:500_000],  # Full text for section extraction
            }

        return await self._run_sync(_fetch, ticker)
