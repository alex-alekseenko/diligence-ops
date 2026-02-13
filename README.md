# DiligenceOps

AI-powered multi-workstream due diligence pipeline for M&A. Ingests SEC filings via EDGAR, runs them through a 16-agent LangGraph pipeline (bronze/silver/gold medallion architecture), and renders a structured diligence report in a Next.js dashboard.

## Architecture

```
SEC EDGAR ──► Bronze (ingest) ──► Silver (transform) ──► Gold (analyze) ──► Report
                  7 agents            6 agents              3 agents
```

**Bronze** agents fetch raw data from SEC EDGAR in parallel:

| Agent | Source | Output |
|-------|--------|--------|
| `resolver` | EDGAR submissions | Company metadata (CIK, SIC, fiscal year) |
| `xbrl_facts` | XBRL API | Financial facts (revenue, net income, etc.) |
| `ten_k` | 10-K filing | Risk factor text |
| `form4` | Form 4 filings | Insider transactions |
| `thirteen_f` | 13-F filings | Institutional holdings |
| `eight_k` | 8-K filings | Material events |
| `def14a` | DEF 14A proxy | Governance & compensation text |

**Silver** agents transform bronze data via LLM extraction:

| Agent | Output |
|-------|--------|
| `financial_kpis` | Standardized KPIs (margins, ratios, cash flow) |
| `risk_factors` | Categorized risks with severity scores |
| `insider_signal` | Buy/sell signal, cluster detection |
| `institutional` | Top holders with position changes |
| `material_events` | Flagged 8-K events with severity |
| `governance` | Board composition, CEO comp, anti-takeover provisions |

**Gold** agents synthesize cross-workstream insights:

| Agent | Output |
|-------|--------|
| `risk_assessment` | 5-dimension risk scoring (1-5 scale) |
| `cross_workstream` | Correlated red flags across workstreams |
| `memo_writer` | Final diligence memo with recommendation |

## Pipeline Flow

```
START → bronze_resolver
  → [bronze_xbrl, bronze_10k, bronze_form4,           (parallel)
     bronze_13f, bronze_8k, bronze_def14a]
  → [silver_financial_kpis, silver_risk_factors,       (each after its bronze)
     silver_insider_signal, silver_institutional,
     silver_material_events, silver_governance]
  → gold_risk_assessment (after silver_financial_kpis)
  → gold_cross_workstream (fan-in: all silver + gold_risk)
  → gold_memo
→ END
```

## Data Layer

All pipeline outputs are persisted as flat CSV files in `pipeline_output/<TICKER>/`:

```
pipeline_output/AAPL/
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
  silver_governance_directors.csv
  gold_risk_assessment.csv
  gold_cross_workstream_flags.csv
  results_diligence_memo.md
```

## Tech Stack

**Backend:** Python 3.12+, FastAPI, LangGraph, LangChain, Pydantic v2, edgartools

**Frontend:** Next.js 16, React 19, TypeScript, Tailwind CSS 4, Tremor, Recharts

**LLMs:** OpenAI gpt-4o (reduce/analysis), gpt-4o-mini (map/extraction)

## Quick Start

### Prerequisites

- Python 3.12+
- Node.js 20+
- [uv](https://docs.astral.sh/uv/) (Python package manager)
- OpenAI API key

### Backend

```bash
# Install dependencies
uv sync

# Create .env
echo "OPENAI_API_KEY=sk-..." > .env

# Start API server
uv run uvicorn backend.api:app --host 0.0.0.0 --port 8000
```

### Frontend

```bash
cd frontend
npm install
npm run dev
# Open http://localhost:3000
```

Enter a ticker symbol (e.g., `AAPL`) and the pipeline will run all 16 agents with real-time progress via WebSocket.

## API

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/health` | Health check |
| POST | `/api/analyze` | Start pipeline (`{"ticker": "AAPL"}`) |
| GET | `/api/results/{run_id}` | Get pipeline results |
| GET | `/api/download/{run_id}/{file_type}` | Download a CSV output |
| WS | `/ws/pipeline/{run_id}` | Real-time progress updates |

## Project Structure

```
backend/
  api.py                 # FastAPI app + WebSocket
  graph.py               # LangGraph pipeline orchestration (16 nodes)
  models.py              # Pydantic schemas + pipeline state
  agents/
    bronze/              # 7 ingestion agents (EDGAR -> CSV)
    silver/              # 6 transformation agents (CSV -> structured data)
    gold/                # 3 synthesis agents (structured -> insights)
  data/
    edgar_client.py      # SEC EDGAR API client
    edgar_filings.py     # Filing retrieval helpers
    csv_writer.py        # Medallion CSV writer
frontend/
  src/app/page.tsx       # Main dashboard (single-page app)
  src/lib/types.ts       # TypeScript interfaces
examples/
  AAPL/                  # Sample pipeline output for debugging
pipeline_output/         # Live pipeline output (gitignored)
```

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `OPENAI_API_KEY` | Yes | OpenAI API key for gpt-4o / gpt-4o-mini agents |

## Testing

```bash
uv run pytest
```

## License

Proprietary.
