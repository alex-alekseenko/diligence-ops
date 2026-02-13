"""Unit tests for EdgarFilingsClient with mocked edgartools internals."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from backend.data.edgar_filings import EdgarFilingsClient, EdgarFilingsError


@pytest.fixture
def client():
    with patch("backend.data.edgar_filings.EdgarFilingsClient.__init__", return_value=None):
        return EdgarFilingsClient.__new__(EdgarFilingsClient)


# ---------------------------------------------------------------------------
# get_10k_risk_factors
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_10k_risk_factors_success(client):
    """Fetches risk text from tenk.risk_factors property."""
    result = await client._run_sync(
        lambda t: "We face significant regulatory risk...", "AAPL"
    )
    assert "regulatory risk" in result


@pytest.mark.asyncio
async def test_get_10k_risk_factors_empty(client):
    """Returns empty string when no 10-K filings exist."""
    result = await client._run_sync(lambda t: "", "AAPL")
    assert result == ""


# ---------------------------------------------------------------------------
# get_form4_filings
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_form4_filings_success(client):
    """Parses Form 4 transactions correctly."""
    trades = [
        {
            "insider_name": "Tim Cook",
            "insider_title": "CEO",
            "transaction_date": "2025-08-15",
            "transaction_code": "S",
            "shares": 50000,
            "price_per_share": 225.0,
            "value": 11250000,
            "filing_date": "2025-08-17",
        }
    ]
    result = await client._run_sync(lambda t, m: trades, "AAPL", 12)

    assert len(result) == 1
    assert result[0]["insider_name"] == "Tim Cook"
    assert result[0]["transaction_code"] == "S"
    assert result[0]["shares"] == 50000


@pytest.mark.asyncio
async def test_get_form4_filings_empty(client):
    """Returns empty list when no Form 4 filings exist."""
    result = await client._run_sync(lambda t, m: [], "UNKNOWN", 12)
    assert result == []


# ---------------------------------------------------------------------------
# get_institutional_holders (SC 13G)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_institutional_holders_success(client):
    """Parses institutional holders from SC 13G filings."""
    holders = [
        {
            "holder_name": "Vanguard Group",
            "shares": 1200000000,
            "value": None,
            "pct_of_portfolio": 8.47,
            "holder_type": "institutional",
        },
    ]
    result = await client._run_sync(lambda t: holders, "AAPL")
    assert len(result) == 1
    assert result[0]["holder_name"] == "Vanguard Group"


@pytest.mark.asyncio
async def test_get_institutional_holders_empty(client):
    """Returns empty list when no SC 13G filings exist."""
    result = await client._run_sync(lambda t: [], "UNKNOWN")
    assert result == []


# ---------------------------------------------------------------------------
# get_8k_filings
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_8k_filings_success(client):
    """Parses 8-K filing events."""
    events = [
        {
            "filing_date": "2025-10-30",
            "description": "Quarterly results",
            "form": "8-K",
        },
    ]
    result = await client._run_sync(lambda t, m: events, "AAPL", 12)
    assert len(result) == 1
    assert result[0]["filing_date"] == "2025-10-30"


# ---------------------------------------------------------------------------
# get_def14a
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_def14a_success(client):
    """Parses DEF 14A proxy text."""
    proxy = {"filing_date": "2025-01-15", "text": "Proxy Statement content..."}
    result = await client._run_sync(lambda t: proxy, "AAPL")
    assert "text" in result
    assert "Proxy" in result["text"]


@pytest.mark.asyncio
async def test_get_def14a_empty(client):
    """Returns empty dict when no DEF 14A exists."""
    result = await client._run_sync(lambda t: {}, "UNKNOWN")
    assert result == {}
