import { PipelineProgress, PipelineResults } from "./types";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

export async function startAnalysis(
  ticker: string
): Promise<{ run_id: string; ticker: string; status: string }> {
  const res = await fetch(`${API_BASE}/api/analyze`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ticker }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ error: "Request failed" }));
    throw new Error(err.error || `HTTP ${res.status}`);
  }
  return res.json();
}

export async function getResults(runId: string): Promise<PipelineResults> {
  const res = await fetch(`${API_BASE}/api/results/${runId}`);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

export function getDownloadUrl(runId: string, fileType: string): string {
  return `${API_BASE}/api/download/${runId}/${fileType}`;
}

export function connectWebSocket(
  runId: string,
  onMessage: (progress: PipelineProgress) => void,
  onClose?: () => void
): WebSocket {
  const wsBase = API_BASE.replace(/^http/, "ws");
  const ws = new WebSocket(`${wsBase}/ws/pipeline/${runId}`);

  ws.onmessage = (event) => {
    try {
      const data: PipelineProgress = JSON.parse(event.data);
      onMessage(data);
    } catch {
      console.warn("Invalid WS message:", event.data);
    }
  };

  ws.onclose = () => onClose?.();
  ws.onerror = (err) => console.error("WebSocket error:", err);

  return ws;
}
