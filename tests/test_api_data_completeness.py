"""Tests that the API results endpoint returns ALL fields the frontend expects.

If a field is added to the frontend TypeScript types but not mapped in the
backend API, these tests will catch it.  Similarly, if someone accidentally
removes a column from a UI table, the corresponding assertion here will fail.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.api import app, _runs
from backend.models import PipelineState, initial_state


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _populate_run(state: PipelineState, run_id: str = "test-data") -> str:
    """Inject a completed run into the API in-memory store."""
    _runs[run_id] = {
        "ticker": state["ticker"],
        "status": "complete",
        "state": state,
    }
    return run_id


@pytest.fixture(autouse=True)
def _cleanup_runs():
    """Remove test runs after each test."""
    yield
    _runs.pop("test-data", None)


# ---------------------------------------------------------------------------
# Insider trades: every field from InsiderTransaction must be present
# ---------------------------------------------------------------------------


def test_insider_trades_all_fields_present(
    sample_state_v2: PipelineState,
):
    """API returns all InsiderTransaction fields: insider_name, title, tx_date,
    tx_code, shares, price, value."""
    run_id = _populate_run(sample_state_v2)

    client = TestClient(app)
    resp = client.get(f"/api/results/{run_id}")
    assert resp.status_code == 200
    data = resp.json()

    trades = data["insider_trades"]
    assert len(trades) > 0, "Expected at least one insider trade"

    required_fields = {"insider_name", "title", "tx_date", "tx_code", "shares", "price", "value"}
    for i, trade in enumerate(trades):
        missing = required_fields - set(trade.keys())
        assert not missing, f"Trade #{i} missing fields: {missing}"


def test_insider_trades_no_truncation(
    sample_state_v2: PipelineState,
):
    """API returns ALL insider trades — no slicing or limiting."""
    # Add many trades to the state
    base_trade = {
        "insider_name": "Jane Doe",
        "insider_title": "VP",
        "transaction_date": "2025-06-01",
        "transaction_code": "S",
        "shares": 1000,
        "price_per_share": 150.0,
        "value": 150000,
        "filing_date": "2025-06-03",
    }
    many_trades = [base_trade.copy() for _ in range(200)]
    sample_state_v2["silver_insider_trades"] = many_trades

    run_id = _populate_run(sample_state_v2)
    client = TestClient(app)
    resp = client.get(f"/api/results/{run_id}")
    data = resp.json()

    assert len(data["insider_trades"]) == 200, (
        f"Expected 200 trades, got {len(data['insider_trades'])} — API is truncating"
    )


# ---------------------------------------------------------------------------
# Institutional holders: all fields present
# ---------------------------------------------------------------------------


def test_institutional_holders_all_fields_present(
    sample_state_v2: PipelineState,
):
    """API returns all InstitutionalHolder fields: holder_name, shares,
    value, change_pct, holder_type."""
    run_id = _populate_run(sample_state_v2)

    client = TestClient(app)
    resp = client.get(f"/api/results/{run_id}")
    data = resp.json()

    holders = data["institutional_holders"]
    assert len(holders) > 0, "Expected at least one institutional holder"

    required_fields = {"holder_name", "shares", "value", "change_pct", "holder_type"}
    for i, holder in enumerate(holders):
        missing = required_fields - set(holder.keys())
        assert not missing, f"Holder #{i} missing fields: {missing}"


# ---------------------------------------------------------------------------
# Material events: all fields present including summary
# ---------------------------------------------------------------------------


def test_material_events_all_fields_present(
    sample_state_v2: PipelineState,
):
    """API returns all MaterialEvent fields: filing_date, item_code,
    item_description, severity, summary."""
    run_id = _populate_run(sample_state_v2)

    client = TestClient(app)
    resp = client.get(f"/api/results/{run_id}")
    data = resp.json()

    events = data["material_events"]
    assert len(events) > 0, "Expected at least one material event"

    required_fields = {"filing_date", "item_code", "item_description", "severity", "summary"}
    for i, event in enumerate(events):
        missing = required_fields - set(event.keys())
        assert not missing, f"Event #{i} missing fields: {missing}"


# ---------------------------------------------------------------------------
# Risk factors: all fields present including summary
# ---------------------------------------------------------------------------


def test_risk_factors_all_fields_present(
    sample_state_v2: PipelineState,
):
    """API returns all RiskFactorItem fields: category, title, summary,
    severity, is_novel."""
    run_id = _populate_run(sample_state_v2)

    client = TestClient(app)
    resp = client.get(f"/api/results/{run_id}")
    data = resp.json()

    factors = data["risk_factors"]
    assert len(factors) > 0, "Expected at least one risk factor"

    required_fields = {"category", "title", "summary", "severity", "is_novel"}
    for i, factor in enumerate(factors):
        missing = required_fields - set(factor.keys())
        assert not missing, f"Risk factor #{i} missing fields: {missing}"


# ---------------------------------------------------------------------------
# Governance: all fields present
# ---------------------------------------------------------------------------


def test_governance_all_fields_present(
    sample_state_v2: PipelineState,
):
    """API returns all GovernanceData fields."""
    run_id = _populate_run(sample_state_v2)

    client = TestClient(app)
    resp = client.get(f"/api/results/{run_id}")
    data = resp.json()

    gov = data["governance"]
    assert gov is not None, "Expected governance data"

    required_fields = {
        "ceo_name", "ceo_total_comp", "ceo_comp_prior", "ceo_pay_growth",
        "median_employee_pay", "ceo_pay_ratio", "board_size",
        "independent_directors", "board_independence_pct",
        "has_poison_pill", "has_staggered_board", "has_dual_class",
        "anti_takeover_provisions", "governance_flags",
    }
    missing = required_fields - set(gov.keys())
    assert not missing, f"Governance missing fields: {missing}"


# ---------------------------------------------------------------------------
# File download: memo_md key must resolve
# ---------------------------------------------------------------------------


def test_memo_md_file_key_exists(
    sample_state_v2: PipelineState,
):
    """API results include memo_md in files dict."""
    run_id = _populate_run(sample_state_v2)

    client = TestClient(app)
    resp = client.get(f"/api/results/{run_id}")
    data = resp.json()

    files = data.get("files", {})
    assert files.get("memo_md") is not None, (
        "files.memo_md is missing — frontend Full Report tab will be broken"
    )


# ---------------------------------------------------------------------------
# Insider signal: all fields present
# ---------------------------------------------------------------------------


def test_insider_signal_all_fields_present(
    sample_state_v2: PipelineState,
):
    """API returns all InsiderSignal fields."""
    run_id = _populate_run(sample_state_v2)

    client = TestClient(app)
    resp = client.get(f"/api/results/{run_id}")
    data = resp.json()

    signal = data["insider_signal"]
    assert signal is not None, "Expected insider signal data"

    required_fields = {
        "total_buys", "total_sells", "net_shares", "buy_sell_ratio",
        "cluster_detected", "cluster_description", "signal",
    }
    missing = required_fields - set(signal.keys())
    assert not missing, f"Insider signal missing fields: {missing}"
