"""Multi-hop (Bronze/Silver/Gold/Results) CSV persistence layer.

Output structure:
    pipeline_output/{TICKER}/
        bronze_company_info.csv
        bronze_xbrl_facts.csv
        bronze_10k_risk_text.csv
        bronze_form4_transactions.csv
        bronze_13f_holdings.csv
        bronze_8k_filings.csv
        bronze_def14a_proxy.csv
        silver_financial_kpis.csv
        silver_risk_factors.csv
        silver_insider_transactions.csv
        silver_institutional_holders.csv
        silver_material_events.csv
        silver_governance.csv
        gold_risk_assessment.csv
        gold_cross_workstream_flags.csv
        results_diligence_memo.md
        run_metadata.json
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd


class CsvWriter:
    """Writes pipeline artifacts to per-ticker directory with layer prefixes."""

    def __init__(self, ticker: str, output_dir: str = "pipeline_output"):
        self.ticker = ticker.upper().strip()
        self.output_dir = Path(output_dir) / self.ticker
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def _now_iso(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    # ── Bronze Layer ─────────────────────────────────────────────────────

    def write_bronze(
        self,
        table_name: str,
        data: list[dict] | pd.DataFrame,
        source_url: str = "",
    ) -> Path:
        """Write a bronze table: raw data + ingested_at + source_url."""
        if isinstance(data, list):
            df = pd.DataFrame(data) if data else pd.DataFrame()
        else:
            df = data

        if not df.empty:
            df["ingested_at"] = self._now_iso()
            df["source_url"] = source_url

        path = self.output_dir / f"bronze_{table_name}.csv"
        df.to_csv(path, index=False)
        return path

    # ── Silver Layer ─────────────────────────────────────────────────────

    def write_silver(
        self,
        table_name: str,
        data: list[dict] | pd.DataFrame,
        source_bronze: str = "",
    ) -> Path:
        """Write a silver table: transformed data + processed_at + source_bronze."""
        if isinstance(data, list):
            df = pd.DataFrame(data) if data else pd.DataFrame()
        else:
            df = data

        if not df.empty:
            df["processed_at"] = self._now_iso()
            df["source_bronze"] = source_bronze

        path = self.output_dir / f"silver_{table_name}.csv"
        df.to_csv(path, index=False)
        return path

    # ── Gold Layer ───────────────────────────────────────────────────────

    def write_gold(
        self,
        table_name: str,
        data: list[dict] | pd.DataFrame,
        source_tables: str = "",
    ) -> Path:
        """Write a gold table: analytics output + analyzed_at + source_tables."""
        if isinstance(data, list):
            df = pd.DataFrame(data) if data else pd.DataFrame()
        else:
            df = data

        if not df.empty:
            df["analyzed_at"] = self._now_iso()
            df["source_tables"] = source_tables

        path = self.output_dir / f"gold_{table_name}.csv"
        df.to_csv(path, index=False)
        return path

    # ── Results Layer ─────────────────────────────────────────────────

    def write_result(self, name: str, content: str) -> Path:
        """Write a results-layer artifact (final LLM output) as Markdown."""
        path = self.output_dir / f"results_{name}.md"
        path.write_text(content, encoding="utf-8")
        return path

    # ── Metadata ─────────────────────────────────────────────────────────

    def write_run_metadata(
        self,
        run_id: str = "",
        started_at: str = "",
        errors: list[str] | None = None,
    ) -> Path:
        """Write run metadata JSON."""
        # Count files by layer
        all_files = list(self.output_dir.iterdir())
        bronze_count = sum(1 for f in all_files if f.name.startswith("bronze_"))
        silver_count = sum(1 for f in all_files if f.name.startswith("silver_"))
        gold_count = sum(1 for f in all_files if f.name.startswith("gold_"))
        results_count = sum(1 for f in all_files if f.name.startswith("results_"))

        metadata = {
            "run_id": run_id,
            "ticker": self.ticker,
            "started_at": started_at,
            "completed_at": self._now_iso(),
            "bronze_count": bronze_count,
            "silver_count": silver_count,
            "gold_count": gold_count,
            "results_count": results_count,
            "errors": errors or [],
        }

        path = self.output_dir / "run_metadata.json"
        path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
        return path
