"""FastAPI application — REST API + WebSocket for pipeline execution."""

from __future__ import annotations

import asyncio
import logging
import uuid
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from backend.graph import run_pipeline
from backend.models import PipelineProgress

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="DiligenceOps API",
    description="AI-Powered Multi-Workstream Due Diligence Pipeline for M&A",
    version="0.3.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://localhost:3001"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# In-memory stores
_runs: dict[str, dict] = {}  # run_id → {state, status, ticker}
_ws_connections: dict[str, list[WebSocket]] = {}  # run_id → [websockets]


class AnalyzeRequest(BaseModel):
    ticker: str


class AnalyzeResponse(BaseModel):
    run_id: str
    ticker: str
    status: str


@app.get("/api/health")
async def health_check():
    return {"status": "ok", "service": "diligenceops", "version": "0.3.0"}


@app.post("/api/analyze", response_model=AnalyzeResponse)
async def start_analysis(request: AnalyzeRequest):
    """Start a new pipeline analysis for a ticker."""
    ticker = request.ticker.upper().strip()
    if not ticker or len(ticker) > 5:
        return JSONResponse(
            status_code=400,
            content={"error": "Invalid ticker symbol"},
        )

    run_id = str(uuid.uuid4())[:8]
    _runs[run_id] = {"state": None, "status": "running", "ticker": ticker}

    # Run pipeline in background task
    asyncio.create_task(_execute_pipeline(run_id, ticker))

    return AnalyzeResponse(run_id=run_id, ticker=ticker, status="running")


async def _execute_pipeline(run_id: str, ticker: str):
    """Execute the pipeline and broadcast progress via WebSocket."""

    async def progress_callback(progress: PipelineProgress):
        progress.run_id = run_id
        await _broadcast_ws(run_id, progress.model_dump())

    try:
        state = await run_pipeline(
            ticker, progress_callback=progress_callback, run_id=run_id
        )
        _runs[run_id]["state"] = state
        _runs[run_id]["status"] = "complete"

        await _broadcast_ws(
            run_id,
            PipelineProgress(
                run_id=run_id,
                stage="complete",
                agent="pipeline",
                message="Pipeline completed successfully",
                progress_pct=100,
            ).model_dump(),
        )
    except Exception as e:
        logger.error(f"Pipeline failed for {ticker}: {e}")
        _runs[run_id]["status"] = "error"
        _runs[run_id]["error"] = str(e)
        await _broadcast_ws(
            run_id,
            PipelineProgress(
                run_id=run_id,
                stage="error",
                agent="pipeline",
                message=f"Pipeline failed: {e}",
                progress_pct=0,
            ).model_dump(),
        )


async def _broadcast_ws(run_id: str, data: dict):
    """Send data to all WebSocket connections for a run."""
    import json

    connections = _ws_connections.get(run_id, [])
    dead: list[WebSocket] = []
    for ws in connections:
        try:
            await ws.send_text(json.dumps(data))
        except Exception:
            dead.append(ws)
    for ws in dead:
        connections.remove(ws)


@app.websocket("/ws/pipeline/{run_id}")
async def pipeline_ws(websocket: WebSocket, run_id: str):
    """WebSocket endpoint for real-time pipeline progress."""
    await websocket.accept()

    if run_id not in _ws_connections:
        _ws_connections[run_id] = []
    _ws_connections[run_id].append(websocket)

    try:
        # Keep connection alive until client disconnects
        while True:
            # Wait for any message from client (ping/pong or close)
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        if run_id in _ws_connections:
            conns = _ws_connections[run_id]
            if websocket in conns:
                conns.remove(websocket)


@app.get("/api/results/{run_id}")
async def get_results(run_id: str):
    """Get results for a completed pipeline run."""
    if run_id not in _runs:
        return JSONResponse(status_code=404, content={"error": "Run not found"})

    run = _runs[run_id]
    state = run.get("state")

    result = {
        "run_id": run_id,
        "ticker": run["ticker"],
        "status": run["status"],
    }

    if run["status"] == "error":
        result["error"] = run.get("error", "Unknown error")
        return result

    if state:
        # Serialize company info
        if state.get("company_info"):
            result["company_info"] = state["company_info"].model_dump()

        # Serialize KPIs
        if state.get("silver_kpis"):
            result["kpis"] = state["silver_kpis"].model_dump()

        # Serialize risk scores
        if state.get("gold_risk_scores"):
            result["risk_scores"] = state["gold_risk_scores"].model_dump()

        # Serialize memo
        if state.get("result_memo"):
            result["memo"] = state["result_memo"].model_dump()

        # Silver workstream data — always include so frontend can show
        # appropriate empty-state messages rather than hiding tabs entirely.
        result["risk_factors"] = state.get("silver_risk_factors", [])

        insider_signal = state.get("silver_insider_signal")
        result["insider_signal"] = insider_signal if insider_signal else None

        # Transform insider trades to match frontend field names
        raw_trades = state.get("silver_insider_trades", [])
        result["insider_trades"] = [
            {
                "insider_name": t.get("insider_name", ""),
                "title": t.get("insider_title", ""),
                "tx_date": t.get("transaction_date", ""),
                "tx_code": t.get("transaction_code", ""),
                "shares": t.get("shares", 0),
                "price": t.get("price_per_share"),
                "value": t.get("value"),
            }
            for t in raw_trades
        ]

        result["institutional_holders"] = state.get(
            "silver_institutional_holders", []
        )
        result["material_events"] = state.get("silver_material_events", [])

        governance = state.get("silver_governance")
        result["governance"] = governance if governance else None

        # Gold data
        result["cross_workstream_flags"] = state.get(
            "gold_cross_workstream_flags", []
        )
        result["deal_recommendation"] = state.get("deal_recommendation", "")

        # File paths — all layers
        result["files"] = {
            # Bronze
            "bronze_company_info": state.get("bronze_company_info_path"),
            "bronze_xbrl_facts": state.get("bronze_xbrl_facts_path"),
            "bronze_10k_risk_text": state.get("bronze_10k_risk_text_path"),
            "bronze_form4_transactions": state.get("bronze_form4_path"),
            "bronze_13f_holdings": state.get("bronze_13f_path"),
            "bronze_8k_filings": state.get("bronze_8k_path"),
            "bronze_def14a_proxy": state.get("bronze_def14a_path"),
            # Silver
            "silver_financial_kpis": state.get("silver_kpis_path"),
            "silver_risk_factors": state.get("silver_risk_factors_path"),
            "silver_insider_transactions": state.get("silver_insider_trades_path"),
            "silver_institutional_holders": state.get("silver_institutional_path"),
            "silver_material_events": state.get("silver_events_path"),
            "silver_governance": state.get("silver_governance_path"),
            # Gold
            "gold_risk_assessment": state.get("gold_risk_path"),
            "gold_cross_workstream_flags": state.get("gold_cross_workstream_path"),
            # Results
            "results_diligence_memo": state.get("result_memo_path"),
            "memo_md": state.get("result_memo_path"),
        }

        result["confidence"] = state.get("confidence", 0)
        result["errors"] = state.get("errors", [])

    return result


@app.get("/api/download/{run_id}/{file_type}")
async def download_file(run_id: str, file_type: str):
    """Download a pipeline output file."""
    if run_id not in _runs:
        return JSONResponse(status_code=404, content={"error": "Run not found"})

    state = _runs[run_id].get("state")
    if not state:
        return JSONResponse(status_code=404, content={"error": "No results available"})

    file_map = {
        # Bronze
        "bronze_company_info": state.get("bronze_company_info_path"),
        "bronze_xbrl_facts": state.get("bronze_xbrl_facts_path"),
        "bronze_10k_risk_text": state.get("bronze_10k_risk_text_path"),
        "bronze_form4_transactions": state.get("bronze_form4_path"),
        "bronze_13f_holdings": state.get("bronze_13f_path"),
        "bronze_8k_filings": state.get("bronze_8k_path"),
        "bronze_def14a_proxy": state.get("bronze_def14a_path"),
        # Silver
        "silver_financial_kpis": state.get("silver_kpis_path"),
        "silver_risk_factors": state.get("silver_risk_factors_path"),
        "silver_insider_transactions": state.get("silver_insider_trades_path"),
        "silver_institutional_holders": state.get("silver_institutional_path"),
        "silver_material_events": state.get("silver_events_path"),
        "silver_governance": state.get("silver_governance_path"),
        # Gold
        "gold_risk_assessment": state.get("gold_risk_path"),
        "gold_cross_workstream_flags": state.get("gold_cross_workstream_path"),
        # Results
        "results_diligence_memo": state.get("result_memo_path"),
        "memo_md": state.get("result_memo_path"),
    }

    path_str = file_map.get(file_type)
    if not path_str:
        return JSONResponse(status_code=404, content={"error": f"File type '{file_type}' not found"})

    path = Path(path_str)
    if not path.exists():
        return JSONResponse(status_code=404, content={"error": "File not found on disk"})

    return FileResponse(path, filename=path.name)
