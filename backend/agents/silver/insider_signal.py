"""Silver: Insider Signal — computes buy/sell ratio and cluster detection from bronze Form 4."""

from __future__ import annotations

import logging
from datetime import datetime

from backend.data.csv_writer import CsvWriter
from backend.models import InsiderSignal, PipelineState

logger = logging.getLogger(__name__)


def _detect_clusters(
    trades: list[dict], window_days: int = 30, min_insiders: int = 3
) -> tuple[bool, str]:
    """Detect clusters of 3+ insiders trading same direction within 30 days."""
    for direction, code in [("sell", "S"), ("buy", "P")]:
        dir_trades = [t for t in trades if t.get("transaction_code") == code]
        if len(dir_trades) < min_insiders:
            continue

        all_dated: list[tuple[datetime, str]] = []
        for t in dir_trades:
            try:
                dt = datetime.strptime(t["transaction_date"], "%Y-%m-%d")
                all_dated.append((dt, t["insider_name"]))
            except (ValueError, KeyError):
                continue

        all_dated.sort()

        for i in range(len(all_dated)):
            window_insiders: set[str] = set()
            for j in range(i, len(all_dated)):
                if (all_dated[j][0] - all_dated[i][0]).days <= window_days:
                    window_insiders.add(all_dated[j][1])
                else:
                    break
            if len(window_insiders) >= min_insiders:
                start_date = all_dated[i][0].strftime("%Y-%m-%d")
                return (
                    True,
                    f"Cluster {direction}: {len(window_insiders)} insiders "
                    f"within {window_days} days starting {start_date}",
                )
    return False, ""


async def silver_insider_signal_agent(state: PipelineState) -> dict:
    """Compute insider trading signals from bronze Form 4 data.

    Reads: state.bronze_form4_transactions
    Writes: silver_insider_transactions.csv
    """
    ticker = state["ticker"]
    raw_trades = state.get("bronze_form4_transactions", [])
    errors: list[str] = []

    if not raw_trades:
        signal = InsiderSignal().model_dump()
        return {
            "silver_insider_trades": [],
            "silver_insider_trades_path": None,
            "silver_insider_signal": signal,
            "errors": errors,
            "progress_messages": ["Silver insider: no bronze Form 4 data"],
        }

    buys = [t for t in raw_trades if t.get("transaction_code") == "P"]
    sells = [t for t in raw_trades if t.get("transaction_code") == "S"]

    total_buy_shares = sum(t.get("shares", 0) for t in buys)
    total_sell_shares = sum(t.get("shares", 0) for t in sells)
    net_shares = total_buy_shares - total_sell_shares

    if total_sell_shares > 0:
        buy_sell_ratio = round(total_buy_shares / total_sell_shares, 2)
    else:
        # No sells: ratio is undefined (buys-only or no trades at all)
        buy_sell_ratio = None

    cluster_detected, cluster_desc = _detect_clusters(raw_trades)

    if cluster_detected and "sell" in cluster_desc.lower():
        signal_str = "bearish"
    elif cluster_detected and "buy" in cluster_desc.lower():
        signal_str = "bullish"
    elif len(sells) > len(buys) * 2:
        signal_str = "bearish"
    elif len(buys) > len(sells) * 2:
        signal_str = "bullish"
    else:
        signal_str = "neutral"

    signal = InsiderSignal(
        total_buys=len(buys),
        total_sells=len(sells),
        net_shares=net_shares,
        buy_sell_ratio=buy_sell_ratio,
        cluster_detected=cluster_detected,
        cluster_description=cluster_desc,
        signal=signal_str,
    ).model_dump()

    writer = CsvWriter(ticker)
    path = writer.write_silver(
        "insider_transactions", raw_trades, source_bronze="bronze_form4_transactions.csv"
    )

    return {
        "silver_insider_trades": raw_trades,
        "silver_insider_trades_path": str(path),
        "silver_insider_signal": signal,
        "errors": errors,
        "progress_messages": [
            f"Insider signal: {len(buys)} buys, {len(sells)} sells, signal={signal_str}"
        ],
    }
