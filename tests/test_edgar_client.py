"""Unit tests for the SEC EDGAR client."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from backend.data.edgar_client import EdgarClient, EdgarClientError


@pytest.mark.asyncio
async def test_resolve_cik_valid_ticker(mock_company_tickers):
    client = EdgarClient()
    with patch.object(client, "_get_json", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = mock_company_tickers
        cik = await client.resolve_cik("AAPL")
    assert cik == "0000320193"
    assert len(cik) == 10


@pytest.mark.asyncio
async def test_resolve_cik_case_insensitive(mock_company_tickers):
    client = EdgarClient()
    with patch.object(client, "_get_json", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = mock_company_tickers
        cik = await client.resolve_cik("aapl")
    assert cik == "0000320193"


@pytest.mark.asyncio
async def test_resolve_cik_invalid_ticker(mock_company_tickers):
    client = EdgarClient()
    with patch.object(client, "_get_json", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = mock_company_tickers
        with pytest.raises(EdgarClientError, match="not found"):
            await client.resolve_cik("XXXX")


@pytest.mark.asyncio
async def test_get_company_info(mock_submissions):
    client = EdgarClient()
    with patch.object(client, "_get_json", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = mock_submissions
        info = await client.get_company_info("0000320193")

    assert info.company_name == "Apple Inc."
    assert info.cik == "0000320193"
    assert info.sic == "3571"
    assert info.sic_description == "Electronic Computers"
    assert info.latest_10k_date == "2025-11-01"
    assert "Nasdaq" in info.exchanges


@pytest.mark.asyncio
async def test_get_company_facts_structure(mock_company_facts):
    client = EdgarClient()
    with patch.object(client, "_get_json", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = mock_company_facts
        facts = await client.get_company_facts("0000320193")

    assert len(facts) > 0

    # Check that all facts are 10-K
    for fact in facts:
        assert fact.form == "10-K"

    # Check we have revenue facts
    revenue_facts = [f for f in facts if f.tag == "RevenueFromContractWithCustomerExcludingAssessedTax"]
    assert len(revenue_facts) >= 1

    # Check structure
    first_revenue = revenue_facts[0]
    assert first_revenue.unit == "USD"
    assert first_revenue.value > 0
    assert first_revenue.end  # has an end date


@pytest.mark.asyncio
async def test_get_company_facts_filters_10k_only(mock_company_facts):
    # Add a non-10-K entry to the mock data
    import copy
    facts_data = copy.deepcopy(mock_company_facts)
    facts_data["facts"]["us-gaap"]["NetIncomeLoss"]["units"]["USD"].append(
        {"start": "2025-01-01", "end": "2025-03-31", "val": 25000000000, "accn": "acc-q1", "fy": 2025, "fp": "Q2", "form": "10-Q", "filed": "2025-05-01"}
    )

    client = EdgarClient()
    with patch.object(client, "_get_json", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = facts_data
        facts = await client.get_company_facts("0000320193")

    # Should not include the 10-Q entry
    q1_facts = [f for f in facts if f.form == "10-Q"]
    assert len(q1_facts) == 0


@pytest.mark.asyncio
async def test_get_company_facts_includes_both_taxonomies(mock_company_facts):
    client = EdgarClient()
    with patch.object(client, "_get_json", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = mock_company_facts
        facts = await client.get_company_facts("0000320193")

    taxonomies = {f.taxonomy for f in facts}
    assert "us-gaap" in taxonomies
    assert "dei" in taxonomies


@pytest.mark.asyncio
async def test_fetch_for_ticker(mock_company_tickers, mock_submissions, mock_company_facts):
    client = EdgarClient()

    call_count = 0

    async def mock_get_json(url):
        nonlocal call_count
        call_count += 1
        if "company_tickers" in url:
            return mock_company_tickers
        elif "submissions" in url:
            return mock_submissions
        elif "companyfacts" in url:
            return mock_company_facts
        raise ValueError(f"Unexpected URL: {url}")

    with patch.object(client, "_get_json", side_effect=mock_get_json):
        info, facts = await client.fetch_for_ticker("AAPL")

    assert info.company_name == "Apple Inc."
    assert len(facts) > 0


@pytest.mark.asyncio
async def test_load_bronze_csv(sample_facts, tmp_path):
    """Test roundtrip: write facts to CSV then load them back."""
    from backend.data.csv_writer import CsvWriter

    writer = CsvWriter("AAPL", output_dir=str(tmp_path))
    records = [f.model_dump() for f in sample_facts]
    csv_path = writer.write_bronze("xbrl_facts", records)

    loaded = EdgarClient.load_bronze_csv(csv_path)
    assert len(loaded) == len(sample_facts)
    assert loaded[0].tag == sample_facts[0].tag
    assert loaded[0].value == sample_facts[0].value
