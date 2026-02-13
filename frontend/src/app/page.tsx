"use client";

import { useState, useEffect, useCallback, useRef } from "react";
import { startAnalysis, getResults, connectWebSocket, getDownloadUrl } from "@/lib/api";
import type {
  PipelineProgress,
  PipelineResults,
  FinancialKPIs,
  RiskAssessment,
  RiskFactorItem,
  InsiderSignal,
  InsiderTransaction,
  InstitutionalHolder,
  MaterialEvent,
  GovernanceData,
  CrossWorkstreamFlag,
} from "@/lib/types";
import ReactMarkdown from "react-markdown";
import IncomeFlowSankey from "@/components/IncomeFlowSankey";

/* ── Prism Semantic Hex Colors (for Recharts which needs concrete values) ── */
const PRISM = {
  revenue: "#4a90d9",
  profit: "#3aaa6d",
  cost: "#d95a5a",
  ochre: "#c49a3c",
  foreground: "#1a1814",
  muted: "#8a8278",
  border: "#c4bfb6",
  gridLine: "#e4e0d8",
};

const SAMPLE_TICKERS = ["AAPL", "TSLA", "GME"];

const STAGE_STEPS = [
  { key: "bronze", label: "Parser", agent: "Fetch EDGAR data" },
  { key: "silver", label: "Extractor", agent: "Extract KPIs" },
  { key: "gold", label: "Analyzer", agent: "Score risks" },
  { key: "workstreams", label: "Workstreams", agent: "5 parallel analyses" },
  { key: "complete", label: "Writer", agent: "Generate report" },
];

const STAGE_ORDER = ["bronze", "silver", "gold", "workstreams", "complete"];

type TabKey = "kpis" | "risk" | "risk_factors" | "insider" | "institutional" | "events" | "governance" | "memo";

const TABS: { key: TabKey; label: string }[] = [
  { key: "kpis", label: "KPIs & Charts" },
  { key: "risk", label: "Risk Analysis" },
  { key: "risk_factors", label: "Risk Factors" },
  { key: "insider", label: "Insider" },
  { key: "institutional", label: "Institutional" },
  { key: "events", label: "Events" },
  { key: "governance", label: "Governance" },
  { key: "memo", label: "Full Report" },
];

function formatCurrency(val: number | null): string {
  if (val === null || val === undefined) return "N/A";
  if (Math.abs(val) >= 1e9) return `$${(val / 1e9).toFixed(1)}B`;
  if (Math.abs(val) >= 1e6) return `$${(val / 1e6).toFixed(1)}M`;
  return `$${val.toLocaleString()}`;
}

function formatPercent(val: number | null): string {
  if (val === null || val === undefined) return "N/A";
  return `${(val * 100).toFixed(1)}%`;
}

function formatNumber(val: number | null, decimals = 2): string {
  if (val === null || val === undefined) return "N/A";
  return val.toFixed(decimals);
}

// --- Components ---

function TickerInput({ onSubmit, disabled }: { onSubmit: (t: string) => void; disabled: boolean }) {
  const [ticker, setTicker] = useState("");
  return (
    <div className="flex items-center gap-3">
      <input
        type="text"
        value={ticker}
        onChange={(e) => setTicker(e.target.value.toUpperCase().slice(0, 5))}
        onKeyDown={(e) => e.key === "Enter" && ticker && onSubmit(ticker)}
        placeholder="Enter ticker (e.g., AAPL)"
        className="px-4 py-2.5 border border-ink-faint rounded-sm text-sm font-mono w-48 focus:ring-2 focus:ring-revenue focus:border-revenue outline-none bg-card text-foreground"
        disabled={disabled}
      />
      <button
        onClick={() => ticker && onSubmit(ticker)}
        disabled={disabled || !ticker}
        className="px-5 py-2.5 rounded-sm text-sm font-mono font-medium transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
        style={{ background: "var(--revenue)", color: "white" }}
      >
        {disabled ? "Analyzing..." : "Analyze"}
      </button>
      <div className="flex gap-1.5 ml-2">
        {SAMPLE_TICKERS.map((t) => (
          <button
            key={t}
            onClick={() => { setTicker(t); onSubmit(t); }}
            disabled={disabled}
            className="px-3 py-1.5 text-xs border border-ink-faint hover:border-revenue hover:text-revenue rounded-sm font-mono font-medium disabled:opacity-40 transition-colors text-muted-foreground"
          >
            {t}
          </button>
        ))}
      </div>
    </div>
  );
}

function DealRecommendationBanner({ recommendation }: { recommendation: string }) {
  const config: Record<string, { border: string; bg: string; text: string; label: string; badgeBg: string }> = {
    PROCEED: {
      border: "border-profit",
      bg: "bg-profit-light",
      text: "text-profit",
      badgeBg: "bg-profit",
      label: "PROCEED",
    },
    PROCEED_WITH_CONDITIONS: {
      border: "border-ochre",
      bg: "bg-ochre-light",
      text: "text-ochre",
      badgeBg: "bg-ochre",
      label: "PROCEED WITH CONDITIONS",
    },
    DO_NOT_PROCEED: {
      border: "border-cost",
      bg: "bg-cost-light",
      text: "text-cost",
      badgeBg: "bg-cost",
      label: "DO NOT PROCEED",
    },
  };
  const c = config[recommendation] || config.PROCEED_WITH_CONDITIONS;
  return (
    <div className={`rounded-sm border-2 p-5 ${c.bg} ${c.border} animate-slide-in`}>
      <div className="flex items-center gap-4">
        <span className={`text-base font-heading font-semibold ${c.text}`}>Deal Recommendation</span>
        <span className={`px-4 py-1.5 rounded-sm text-xs font-bold font-mono text-white tracking-wider ${c.badgeBg}`}>
          {c.label}
        </span>
      </div>
    </div>
  );
}

function PipelineProgressBar({ progress }: { progress: PipelineProgress[] }) {
  const latestStage = progress.length > 0 ? progress[progress.length - 1].stage : "";
  const stageIndex = STAGE_ORDER.indexOf(latestStage);
  const isError = latestStage === "error";

  return (
    <div className="bg-card rounded-sm border border-ink-faint p-6 animate-slide-in">
      <div className="flex items-center justify-between mb-4">
        {STAGE_STEPS.map((step, i) => {
          const isComplete = stageIndex >= i;
          const isCurrent = stageIndex === i;
          return (
            <div key={step.key} className="flex items-start flex-1">
              <div className="flex flex-col items-center flex-1">
                <div
                  className="w-8 h-8 rounded-sm flex items-center justify-center text-xs font-bold font-mono transition-colors"
                  style={{
                    background: isComplete ? (isError && isCurrent ? "var(--cost)" : "var(--revenue)") : "var(--canvas-warm)",
                    color: isComplete ? "white" : "var(--ink-muted)",
                    boxShadow: isCurrent && !isError ? "0 0 0 2px var(--revenue)" : "none",
                  }}
                >
                  {isComplete && (stageIndex >= STAGE_STEPS.length - 1 || !isCurrent) ? "\u2713" : i + 1}
                </div>
                <span className="text-[12px] mt-1.5 font-mono font-medium" style={{ color: isComplete ? "var(--revenue)" : "var(--ink-muted)" }}>
                  {step.label}
                </span>
                <span className="text-[11px] font-mono mt-0.5" style={{ color: "var(--ink-faint)" }}>
                  {step.agent}
                </span>
              </div>
              {i < STAGE_STEPS.length - 1 && (
                <div className="h-0.5 flex-1 mx-2 mt-4 rounded-full transition-colors" style={{ background: stageIndex > i ? "var(--revenue)" : "var(--ink-ghost)" }} />
              )}
            </div>
          );
        })}
      </div>
      {progress.length > 0 && (
        <p className="text-[13px] text-center font-mono mt-2" style={{ color: isError ? "var(--cost)" : "var(--ink-muted)" }}>
          {progress[progress.length - 1].message}
        </p>
      )}
    </div>
  );
}

function KpiCard({ label, value, subtitle, color }: { label: string; value: string; subtitle?: string; color?: string }) {
  return (
    <div className="bg-card rounded-sm border border-border p-5">
      <p className="text-[12px] text-muted-foreground uppercase tracking-[1.5px] font-mono mb-2">{label}</p>
      <p className={`text-2xl font-heading font-semibold leading-none ${color || "text-foreground"}`}>{value}</p>
      {subtitle && <p className="text-[13px] text-muted-foreground mt-1.5 font-mono font-medium">{subtitle}</p>}
    </div>
  );
}

function KpiDashboard({ kpis }: { kpis: FinancialKPIs }) {
  const tickerItems = [
    { idx: "01", label: "Revenue", value: formatCurrency(kpis.revenue), delta: kpis.revenue_yoy_change, deltaLabel: "YoY", color: "var(--revenue)" },
    { idx: "02", label: "Net Income", value: formatCurrency(kpis.net_income), color: "var(--profit)" },
    { idx: "03", label: "Gross Margin", value: formatPercent(kpis.gross_margin), color: "var(--profit)" },
    { idx: "04", label: "Op. Margin", value: formatPercent(kpis.operating_margin), color: "var(--profit)" },
    { idx: "05", label: "EPS (Basic)", value: kpis.eps_basic != null ? `$${kpis.eps_basic.toFixed(2)}` : "N/A", color: "var(--ochre)" },
  ];

  return (
    <div className="animate-slide-in space-y-8">
      {/* Ticker Strip */}
      <div className="flex border border-ink-faint rounded-sm overflow-hidden">
        {tickerItems.map((k, i) => (
          <div key={k.label} className={`flex-1 px-6 py-5 relative ${i < tickerItems.length - 1 ? "border-r border-ink-faint" : ""}`}>
            <span className="absolute top-2 left-2.5 text-[11px] text-ink-faint font-mono">{k.idx}</span>
            <p className="text-[12px] uppercase tracking-[1.5px] text-muted-foreground font-mono mb-2">{k.label}</p>
            <p className="text-[28px] font-heading font-semibold leading-none" style={{ color: k.color }}>{k.value}</p>
            {k.delta != null && (
              <p className={`text-[13px] font-mono font-medium mt-1.5 ${k.delta >= 0 ? "text-profit" : "text-cost"}`}>
                {k.delta >= 0 ? "↑" : "↓"} {formatPercent(Math.abs(k.delta))} {k.deltaLabel}
              </p>
            )}
          </div>
        ))}
      </div>

      {/* Metric Tiles — colored backgrounds like Prism */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <div className="p-5 rounded-sm text-center" style={{ background: "var(--revenue-light)" }}>
          <p className="text-[11px] uppercase tracking-[1.5px] text-muted-foreground font-mono mb-2">Current Ratio</p>
          <p className="text-2xl font-heading font-semibold" style={{ color: "var(--revenue)" }}>{formatNumber(kpis.current_ratio)}</p>
        </div>
        <div className="p-5 rounded-sm text-center" style={{ background: "var(--profit-light)" }}>
          <p className="text-[11px] uppercase tracking-[1.5px] text-muted-foreground font-mono mb-2">Op. Cash Flow</p>
          <p className="text-2xl font-heading font-semibold" style={{ color: "var(--profit)" }}>{formatCurrency(kpis.operating_cash_flow)}</p>
        </div>
        <div className="p-5 rounded-sm text-center" style={{ background: "var(--cost-light)" }}>
          <p className="text-[11px] uppercase tracking-[1.5px] text-muted-foreground font-mono mb-2">D/E Ratio</p>
          <p className="text-2xl font-heading font-semibold" style={{ color: "var(--cost)" }}>{formatNumber(kpis.debt_to_equity)}</p>
        </div>
        <div className="p-5 rounded-sm text-center" style={{ background: "var(--profit-light)" }}>
          <p className="text-[11px] uppercase tracking-[1.5px] text-muted-foreground font-mono mb-2">Free Cash Flow</p>
          <p className="text-2xl font-heading font-semibold" style={{ color: "var(--profit)" }}>{formatCurrency(kpis.free_cash_flow)}</p>
        </div>
      </div>

      {/* Income Statement Flow — Sankey */}
      <div>
        <div className="mb-6 pb-2 border-b border-ink-faint">
          <h3 className="font-heading text-xl font-semibold">Income Statement Flow <em className="font-normal italic text-muted-foreground">— P&amp;L waterfall</em></h3>
          <p className="text-[13px] text-muted-foreground font-mono mt-1.5">Hover or tap nodes &amp; flows to trace the full income statement</p>
        </div>
        <IncomeFlowSankey kpis={kpis} />
      </div>
    </div>
  );
}

function riskColor(score: number): string {
  if (score <= 2) return "bg-profit";
  if (score <= 3) return "bg-ochre";
  return "bg-cost";
}

function riskTextColor(level: string): string {
  if (level === "Low") return "text-profit bg-profit-light";
  if (level === "Medium") return "text-ochre bg-ochre-light";
  if (level === "High") return "text-ochre bg-ochre-light";
  return "text-cost bg-cost-light";
}

function severityBadge(severity: number): string {
  if (severity <= 2) return "bg-profit-light text-profit";
  if (severity <= 3) return "bg-ochre-light text-ochre";
  if (severity <= 4) return "bg-ochre-light text-ochre";
  return "bg-cost-light text-cost";
}

function RiskHeatmap({ risk, flags }: { risk: RiskAssessment; flags?: CrossWorkstreamFlag[] }) {
  return (
    <div className="space-y-4 animate-slide-in">
      <div className="bg-card rounded-sm border border-border p-5">
        <div className="flex items-center justify-between mb-4">
          <h3 className="text-sm font-heading font-semibold text-foreground">Risk Assessment</h3>
          <span className={`px-3 py-1 rounded-sm text-sm font-medium font-mono ${riskTextColor(risk.risk_level)}`}>
            {risk.risk_level} ({risk.composite_score.toFixed(1)}/5.0)
          </span>
        </div>

        <div className="space-y-3">
          {risk.dimensions.map((dim) => (
            <div key={dim.dimension}>
              <div className="flex items-center justify-between mb-1">
                <span className="text-sm text-muted-foreground font-mono">{dim.dimension}</span>
                <span className="text-sm font-heading font-medium text-foreground">{dim.score}/5</span>
              </div>
              <div className="w-full bg-secondary rounded-sm h-2.5">
                <div
                  className={`h-2.5 rounded-sm ${riskColor(dim.score)} transition-all`}
                  style={{ width: `${(dim.score / 5) * 100}%` }}
                />
              </div>
              <p className="text-xs text-muted-foreground mt-1 font-mono">{dim.reasoning.slice(0, 120)}...</p>
            </div>
          ))}
        </div>

        {risk.red_flags.length > 0 && (
          <div className="mt-4 pt-4 border-t border-border">
            <h4 className="text-sm font-heading font-medium text-cost mb-2">Red Flags</h4>
            {risk.red_flags.map((flag, i) => (
              <div key={i} className="flex items-start gap-2 mb-2">
                <span className={`px-1.5 py-0.5 text-xs rounded-sm font-mono ${
                  flag.severity === "High" || flag.severity === "Critical"
                    ? "bg-cost-light text-cost"
                    : flag.severity === "Medium"
                      ? "bg-ochre-light text-ochre"
                      : "bg-secondary text-muted-foreground"
                }`}>
                  {flag.severity}
                </span>
                <div>
                  <p className="text-sm font-medium text-foreground font-mono">{flag.flag}</p>
                  <p className="text-xs text-muted-foreground font-mono">{flag.evidence}</p>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Cross-Workstream Flags */}
      {flags && flags.length > 0 && (
        <div className="bg-card rounded-sm border-2 border-cost p-5">
          <h3 className="text-sm font-heading font-semibold text-cost mb-3">Cross-Workstream Red Flags</h3>
          <div className="space-y-3">
            {flags.map((flag, i) => (
              <div key={i} className="border-l-4 border-cost pl-3 py-1">
                <div className="flex items-center gap-2 mb-1">
                  <span className={`px-2 py-0.5 text-xs rounded-sm font-medium font-mono ${
                    flag.severity === "Critical" ? "bg-cost-light text-cost"
                      : flag.severity === "High" ? "bg-ochre-light text-ochre"
                        : "bg-ochre-light text-ochre"
                  }`}>
                    {flag.severity}
                  </span>
                  <span className="text-sm font-medium text-foreground font-mono">{flag.rule_name}</span>
                </div>
                <p className="text-sm text-muted-foreground font-mono">{flag.description}</p>
                <p className="text-xs text-muted-foreground mt-1 font-mono">Evidence: {flag.evidence.join("; ")}</p>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function RiskFactorPanel({ factors }: { factors: RiskFactorItem[] }) {
  const categories = [...new Set(factors.map((f) => f.category))];
  return (
    <div className="bg-card rounded-sm border border-border p-5 animate-slide-in">
      <div className="flex items-center justify-between mb-4">
        <h3 className="text-sm font-heading font-semibold text-foreground">10-K Risk Factors</h3>
        <span className="text-xs text-muted-foreground font-mono">{factors.length} risks identified</span>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-sm font-mono">
          <thead>
            <tr className="border-b border-border">
              <th className="text-left py-2 text-xs text-muted-foreground font-medium uppercase tracking-wider">Category</th>
              <th className="text-left py-2 text-xs text-muted-foreground font-medium uppercase tracking-wider">Title</th>
              <th className="text-center py-2 text-xs text-muted-foreground font-medium uppercase tracking-wider">Severity</th>
              <th className="text-center py-2 text-xs text-muted-foreground font-medium uppercase tracking-wider">Novel</th>
            </tr>
          </thead>
          <tbody>
            {factors.map((f, i) => (
              <tr key={i} className="border-b border-input hover:bg-accent transition-colors">
                <td className="py-2 text-muted-foreground capitalize">{f.category}</td>
                <td className="py-2 text-foreground">
                  <div>{f.title}</div>
                  {f.summary && (
                    <div className="text-xs text-muted-foreground mt-1">{f.summary}</div>
                  )}
                </td>
                <td className="py-2 text-center">
                  <span className={`px-2 py-0.5 rounded-sm text-xs font-medium ${severityBadge(f.severity)}`}>
                    {f.severity}/5
                  </span>
                </td>
                <td className="py-2 text-center">
                  {f.is_novel && (
                    <span className="px-2 py-0.5 rounded-sm text-xs font-medium bg-accent text-primary">New</span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {categories.length > 0 && (
        <div className="mt-4 pt-3 border-t border-border flex flex-wrap gap-2">
          {categories.map((cat) => {
            const count = factors.filter((f) => f.category === cat).length;
            return (
              <span key={cat} className="px-2 py-1 text-xs bg-secondary rounded-sm capitalize font-mono text-secondary-foreground">
                {cat}: {count}
              </span>
            );
          })}
        </div>
      )}
    </div>
  );
}

function InsiderPanel({ signal, trades }: { signal?: InsiderSignal; trades?: InsiderTransaction[] }) {
  const [page, setPage] = useState(0);
  const perPage = 50;
  const signalColor = signal?.signal === "bullish"
    ? "text-profit bg-profit-light"
    : signal?.signal === "bearish"
      ? "text-cost bg-cost-light"
      : "text-muted-foreground bg-secondary";

  const totalTrades = trades?.length ?? 0;
  const totalPages = Math.ceil(totalTrades / perPage);
  const paginated = trades?.slice(page * perPage, (page + 1) * perPage) ?? [];

  return (
    <div className="space-y-4 animate-slide-in">
      {/* Signal summary */}
      {signal && (
        <div className="bg-card rounded-sm border border-border p-5">
          <div className="flex items-center justify-between mb-4">
            <h3 className="text-sm font-heading font-semibold text-foreground">Insider Signal</h3>
            <span className={`px-3 py-1 rounded-sm text-sm font-medium font-mono capitalize ${signalColor}`}>
              {signal.signal}
            </span>
          </div>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            <KpiCard label="Total Buys" value={String(signal.total_buys)} />
            <KpiCard label="Total Sells" value={String(signal.total_sells)} />
            <KpiCard label="Net Shares" value={signal.net_shares.toLocaleString()} />
            <KpiCard label="Buy/Sell Ratio" value={signal.buy_sell_ratio?.toFixed(2) ?? "N/A"} />
          </div>
          {signal.cluster_detected && (
            <div className="mt-3 p-3 bg-cost-light border border-cost rounded-sm">
              <p className="text-sm text-cost font-medium font-mono">Cluster Alert</p>
              <p className="text-sm text-cost font-mono">{signal.cluster_description}</p>
            </div>
          )}
        </div>
      )}

      {/* Trades table */}
      {trades && trades.length > 0 && (
        <div className="bg-card rounded-sm border border-border p-5">
          <div className="flex items-center justify-between mb-3">
            <h3 className="text-sm font-heading font-semibold text-foreground">Insider Trades</h3>
            <span className="text-xs text-muted-foreground font-mono">{totalTrades} records</span>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-sm font-mono">
              <thead>
                <tr className="border-b border-border">
                  <th className="text-left py-2 text-xs text-muted-foreground font-medium uppercase tracking-wider">Date</th>
                  <th className="text-left py-2 text-xs text-muted-foreground font-medium uppercase tracking-wider">Insider</th>
                  <th className="text-left py-2 text-xs text-muted-foreground font-medium uppercase tracking-wider">Title</th>
                  <th className="text-center py-2 text-xs text-muted-foreground font-medium uppercase tracking-wider">Type</th>
                  <th className="text-right py-2 text-xs text-muted-foreground font-medium uppercase tracking-wider">Shares</th>
                  <th className="text-right py-2 text-xs text-muted-foreground font-medium uppercase tracking-wider">Price</th>
                  <th className="text-right py-2 text-xs text-muted-foreground font-medium uppercase tracking-wider">Value</th>
                </tr>
              </thead>
              <tbody>
                {paginated.map((t, i) => (
                  <tr key={i} className="border-b border-input hover:bg-accent transition-colors">
                    <td className="py-2 text-muted-foreground text-xs">{t.tx_date}</td>
                    <td className="py-2 text-foreground">{t.insider_name}</td>
                    <td className="py-2 text-muted-foreground text-xs">{t.title}</td>
                    <td className="py-2 text-center">
                      <span className={`px-2 py-0.5 rounded-sm text-xs font-medium ${
                        t.tx_code === "P" ? "bg-profit-light text-profit" : "bg-cost-light text-cost"
                      }`}>
                        {t.tx_code === "P" ? "BUY" : "SELL"}
                      </span>
                    </td>
                    <td className="py-2 text-right text-xs">{t.shares.toLocaleString()}</td>
                    <td className="py-2 text-right text-xs">
                      {t.price ? `$${t.price.toFixed(2)}` : "N/A"}
                    </td>
                    <td className="py-2 text-right text-xs">
                      {t.value ? formatCurrency(t.value) : "N/A"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {totalPages > 1 && (
            <div className="flex items-center justify-between mt-3 pt-3 border-t border-border">
              <span className="text-xs text-muted-foreground font-mono">
                Showing {page * perPage + 1}–{Math.min((page + 1) * perPage, totalTrades)} of {totalTrades}
              </span>
              <div className="flex gap-1">
                <button onClick={() => setPage(p => Math.max(0, p - 1))} disabled={page === 0}
                  className="px-3 py-1 text-xs rounded-sm border border-border text-muted-foreground hover:bg-accent disabled:opacity-30 disabled:cursor-not-allowed font-mono transition-colors">
                  Prev
                </button>
                {Array.from({ length: totalPages }, (_, i) => (
                  <button key={i} onClick={() => setPage(i)}
                    className={`px-3 py-1 text-xs rounded-sm border font-mono transition-colors ${i === page ? "bg-primary text-primary-foreground border-primary" : "border-border text-muted-foreground hover:bg-accent"}`}>
                    {i + 1}
                  </button>
                ))}
                <button onClick={() => setPage(p => Math.min(totalPages - 1, p + 1))} disabled={page === totalPages - 1}
                  className="px-3 py-1 text-xs rounded-sm border border-border text-muted-foreground hover:bg-accent disabled:opacity-30 disabled:cursor-not-allowed font-mono transition-colors">
                  Next
                </button>
              </div>
            </div>
          )}
        </div>
      )}

      {!signal && (!trades || trades.length === 0) && (
        <div className="bg-card rounded-sm border border-border p-8 text-center text-muted-foreground font-mono">
          No insider trading data available.
        </div>
      )}
    </div>
  );
}

function InstitutionalPanel({ holders }: { holders: InstitutionalHolder[] }) {
  return (
    <div className="bg-card rounded-sm border border-border p-5 animate-slide-in">
      <div className="flex items-center justify-between mb-4">
        <h3 className="text-sm font-heading font-semibold text-foreground">Top Institutional Holders</h3>
        <span className="text-xs text-muted-foreground font-mono">{holders.length} holders</span>
      </div>
      {holders.length > 0 ? (
        <div className="overflow-x-auto">
          <table className="w-full text-sm font-mono">
            <thead>
              <tr className="border-b border-border">
                <th className="text-left py-2 text-xs text-muted-foreground font-medium uppercase tracking-wider">Holder</th>
                <th className="text-right py-2 text-xs text-muted-foreground font-medium uppercase tracking-wider">Shares</th>
                <th className="text-right py-2 text-xs text-muted-foreground font-medium uppercase tracking-wider">Value</th>
                <th className="text-right py-2 text-xs text-muted-foreground font-medium uppercase tracking-wider">Change %</th>
                <th className="text-center py-2 text-xs text-muted-foreground font-medium uppercase tracking-wider">Type</th>
              </tr>
            </thead>
            <tbody>
              {holders.map((h, i) => (
                <tr key={i} className="border-b border-input hover:bg-accent transition-colors">
                  <td className="py-2 text-foreground">{h.holder_name}</td>
                  <td className="py-2 text-right text-xs">{h.shares.toLocaleString()}</td>
                  <td className="py-2 text-right text-xs">
                    {h.value ? formatCurrency(h.value) : "N/A"}
                  </td>
                  <td className="py-2 text-right text-xs">
                    {h.change_pct != null ? (
                      <span className={h.change_pct >= 0 ? "text-profit" : "text-cost"}>
                        {h.change_pct >= 0 ? "+" : ""}{(h.change_pct * 100).toFixed(1)}%
                      </span>
                    ) : "N/A"}
                  </td>
                  <td className="py-2 text-center">
                    <span className={`px-2 py-0.5 rounded-sm text-xs font-medium ${
                      h.holder_type === "passive" ? "bg-revenue-light text-revenue" : "bg-ochre-light text-ochre"
                    }`}>
                      {h.holder_type}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <p className="text-center text-muted-foreground py-6 font-mono">No institutional ownership data available.</p>
      )}
    </div>
  );
}

function EventsTimeline({ events }: { events: MaterialEvent[] }) {
  return (
    <div className="bg-card rounded-sm border border-border p-5 animate-slide-in">
      <div className="flex items-center justify-between mb-4">
        <h3 className="text-sm font-heading font-semibold text-foreground">Material Events (8-K)</h3>
        <span className="text-xs text-muted-foreground font-mono">{events.length} events</span>
      </div>
      {events.length > 0 ? (
        <div className="overflow-x-auto">
          <table className="w-full text-sm font-mono">
            <thead>
              <tr className="border-b border-border">
                <th className="text-left py-2 text-xs text-muted-foreground font-medium uppercase tracking-wider">Date</th>
                <th className="text-left py-2 text-xs text-muted-foreground font-medium uppercase tracking-wider">Item</th>
                <th className="text-left py-2 text-xs text-muted-foreground font-medium uppercase tracking-wider">Description</th>
                <th className="text-center py-2 text-xs text-muted-foreground font-medium uppercase tracking-wider">Severity</th>
              </tr>
            </thead>
            <tbody>
              {events.map((e, i) => (
                <tr key={i} className="border-b border-input hover:bg-accent transition-colors">
                  <td className="py-2 text-muted-foreground text-xs whitespace-nowrap">{e.filing_date}</td>
                  <td className="py-2 text-foreground text-xs whitespace-nowrap">{e.item_code}</td>
                  <td className="py-2 text-muted-foreground">
                    <div>{e.item_description}</div>
                    {e.summary && (
                      <div className="text-xs text-muted-foreground mt-1">{e.summary}</div>
                    )}
                  </td>
                  <td className="py-2 text-center">
                    <span className={`px-2 py-0.5 rounded-sm text-xs font-medium ${severityBadge(e.severity)}`}>
                      {e.severity}/5
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <p className="text-center text-muted-foreground py-6 font-mono">No material events in the past 12 months.</p>
      )}
    </div>
  );
}

function GovernancePanel({ governance }: { governance: GovernanceData }) {
  const hasData = governance.ceo_name || governance.board_size;
  if (!hasData) {
    return (
      <div className="bg-card rounded-sm border border-border p-8 text-center text-muted-foreground font-mono">
        No governance data available.
      </div>
    );
  }

  return (
    <div className="space-y-4 animate-slide-in">
      <div className="bg-card rounded-sm border border-border p-5">
        <h3 className="text-sm font-heading font-semibold text-foreground mb-4 pb-2 border-b border-border">Governance & Compensation</h3>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
          {governance.ceo_name && (
            <KpiCard label="CEO" value={governance.ceo_name} />
          )}
          {governance.ceo_total_comp != null && (
            <KpiCard label="CEO Total Comp" value={formatCurrency(governance.ceo_total_comp)} />
          )}
          {governance.ceo_comp_prior != null && (
            <KpiCard label="CEO Comp (Prior)" value={formatCurrency(governance.ceo_comp_prior)} />
          )}
          {governance.ceo_pay_growth != null && (
            <KpiCard label="CEO Pay Growth" value={formatPercent(governance.ceo_pay_growth)} />
          )}
          {governance.median_employee_pay != null && (
            <KpiCard label="Median Employee Pay" value={formatCurrency(governance.median_employee_pay)} />
          )}
          {governance.ceo_pay_ratio != null && (
            <KpiCard label="CEO Pay Ratio" value={`${governance.ceo_pay_ratio}x`} />
          )}
          {governance.board_size != null && (
            <KpiCard label="Board Size" value={String(governance.board_size)} />
          )}
          {governance.independent_directors != null && (
            <KpiCard label="Independent Dirs" value={String(governance.independent_directors)} />
          )}
          {governance.board_independence_pct != null && (
            <KpiCard label="Board Independence" value={formatPercent(governance.board_independence_pct)} />
          )}
        </div>

        {/* Structure flags */}
        {(governance.has_poison_pill || governance.has_staggered_board || governance.has_dual_class) && (
          <div className="flex flex-wrap gap-2 mt-3">
            {governance.has_poison_pill && (
              <span className="px-2 py-1 text-xs bg-cost-light text-cost rounded-sm font-mono">Poison Pill</span>
            )}
            {governance.has_staggered_board && (
              <span className="px-2 py-1 text-xs bg-ochre-light text-ochre rounded-sm font-mono">Staggered Board</span>
            )}
            {governance.has_dual_class && (
              <span className="px-2 py-1 text-xs bg-ochre-light text-ochre rounded-sm font-mono">Dual-Class Shares</span>
            )}
          </div>
        )}
      </div>

      {/* Board Members Table */}
      {governance.directors && governance.directors.length > 0 && (
        <div className="bg-card rounded-sm border border-border p-5">
          <div className="flex items-center justify-between mb-4">
            <h3 className="text-sm font-heading font-semibold text-foreground">Board of Directors</h3>
            <span className="text-xs text-muted-foreground font-mono">{governance.directors.length} members</span>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-sm font-mono">
              <thead>
                <tr className="border-b border-border">
                  <th className="text-left py-2 text-xs text-muted-foreground font-medium uppercase tracking-wider">Name</th>
                  <th className="text-left py-2 text-xs text-muted-foreground font-medium uppercase tracking-wider">Role</th>
                  <th className="text-right py-2 text-xs text-muted-foreground font-medium uppercase tracking-wider">Age</th>
                  <th className="text-right py-2 text-xs text-muted-foreground font-medium uppercase tracking-wider">Since</th>
                  <th className="text-center py-2 text-xs text-muted-foreground font-medium uppercase tracking-wider">Independent</th>
                  <th className="text-left py-2 text-xs text-muted-foreground font-medium uppercase tracking-wider">Committees</th>
                </tr>
              </thead>
              <tbody>
                {governance.directors.map((d, i) => (
                  <tr key={i} className="border-b border-input hover:bg-accent transition-colors">
                    <td className="py-2 text-foreground">{d.name}</td>
                    <td className="py-2 text-muted-foreground text-xs">{d.role || "\u2014"}</td>
                    <td className="py-2 text-right text-xs">{d.age ?? "\u2014"}</td>
                    <td className="py-2 text-right text-xs">{d.director_since ?? "\u2014"}</td>
                    <td className="py-2 text-center">
                      {d.is_independent === true ? (
                        <span className="px-2 py-0.5 rounded-sm text-xs font-medium bg-profit-light text-profit">Yes</span>
                      ) : d.is_independent === false ? (
                        <span className="px-2 py-0.5 rounded-sm text-xs font-medium bg-ochre-light text-ochre">No</span>
                      ) : (
                        <span className="text-xs text-muted-foreground">{"\u2014"}</span>
                      )}
                    </td>
                    <td className="py-2 text-xs text-muted-foreground">
                      {d.committees.length > 0 ? d.committees.join(", ") : "\u2014"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Anti-takeover & flags */}
      {(governance.anti_takeover_provisions?.length > 0 || governance.governance_flags?.length > 0) && (
        <div className="bg-card rounded-sm border border-border p-5">
          {governance.anti_takeover_provisions.length > 0 && (
            <div className="mb-3">
              <h4 className="text-sm font-heading font-medium text-foreground mb-2">Anti-Takeover Provisions</h4>
              <div className="flex flex-wrap gap-2">
                {governance.anti_takeover_provisions.map((p, i) => (
                  <span key={i} className="px-2 py-1 text-xs bg-ochre-light text-ochre rounded-sm font-mono">{p}</span>
                ))}
              </div>
            </div>
          )}
          {governance.governance_flags.length > 0 && (
            <div>
              <h4 className="text-sm font-heading font-medium text-cost mb-2">Governance Flags</h4>
              <div className="flex flex-wrap gap-2">
                {governance.governance_flags.map((f, i) => (
                  <span key={i} className="px-2 py-1 text-xs bg-cost-light text-cost rounded-sm font-mono">{f}</span>
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

/* ── Severity Dots (visual 1-5 scale) ─────────────────────────────────── */
function SeverityDots({ score, max = 5 }: { score: number; max?: number }) {
  return (
    <span className="inline-flex gap-[3px] align-middle ml-1.5">
      {Array.from({ length: max }, (_, i) => (
        <span
          key={i}
          className="w-2 h-2 rounded-[2px] inline-block"
          style={{
            background: i < score
              ? score >= 4 ? "var(--cost)" : "var(--ochre)"
              : "var(--ink-ghost)",
          }}
        />
      ))}
    </span>
  );
}

/* ── Full Report Viewer — structured, visual, print-ready ─────────────── */
function ReportViewer({ results, memoText }: { results: PipelineResults; memoText: string }) {
  const kpis = results.kpis;
  const risk = results.risk_scores;
  const memo = results.memo;
  const insider = results.insider_signal;
  const holders = results.institutional_holders ?? [];
  const events = results.material_events ?? [];
  const governance = results.governance;
  const crossFlags = results.cross_workstream_flags ?? [];
  const dealRec = results.deal_recommendation ?? "";
  const confidence = results.confidence ?? 0;
  const company = results.company_info;

  const recConfig: Record<string, { bg: string; border: string; icon: string; label: string }> = {
    PROCEED: { bg: "bg-profit-light", border: "border-profit", icon: "\u2705", label: "PROCEED" },
    PROCEED_WITH_CONDITIONS: { bg: "bg-ochre-light", border: "border-ochre", icon: "\u26A0\uFE0F", label: "PROCEED WITH CONDITIONS" },
    DO_NOT_PROCEED: { bg: "bg-cost-light", border: "border-cost", icon: "\u26D4", label: "DO NOT PROCEED" },
  };
  const rec = recConfig[dealRec] || recConfig.PROCEED_WITH_CONDITIONS;

  const riskLevel = risk?.risk_level ?? "N/A";
  const riskLevelStyle =
    riskLevel === "Low" ? "bg-profit-light text-profit"
      : riskLevel === "Medium" ? "bg-ochre-light text-ochre"
        : riskLevel === "High" ? "bg-ochre-light text-ochre"
          : "bg-cost-light text-cost";

  const signalColor =
    insider?.signal === "bullish" ? "bg-profit-light text-profit"
      : insider?.signal === "bearish" ? "bg-cost-light text-cost"
        : "bg-secondary text-muted-foreground";

  return (
    <div className="report-shell animate-slide-in">
      {/* ── Header ──────────────────────────────────────────────────── */}
      <div className="rpt-header">
        <h1 className="font-heading text-[22px] font-bold text-foreground">
          Due Diligence Report: {company?.company_name ?? results.ticker} ({results.ticker})
        </h1>
        <p className="text-[12px] text-muted-foreground italic font-heading mt-0.5">
          Generated {memo?.generated_at ?? "—"} · DiligenceOps v0.3
        </p>
      </div>

      {/* ── Verdict Banner ──────────────────────────────────────────── */}
      <div className={`rpt-verdict ${rec.bg} ${rec.border}`}>
        <span className="font-heading text-lg font-bold whitespace-nowrap">{rec.icon} {rec.label}</span>
        <p className="text-[12.5px] font-mono leading-relaxed opacity-90">
          {memo?.executive_summary
            ? memo.executive_summary.split(". ").slice(0, 2).join(". ") + "."
            : "No executive summary available."}
        </p>
      </div>

      {/* ── Quick Metrics Strip ─────────────────────────────────────── */}
      {kpis && (
        <div className="rpt-metrics-row">
          <div className="rpt-metric-card">
            <div className="rpt-metric-label">Revenue (FY{kpis.fiscal_year})</div>
            <div className="rpt-metric-value text-revenue">{formatCurrency(kpis.revenue)}</div>
            {kpis.revenue_yoy_change != null && (
              <div className={`rpt-metric-sub ${kpis.revenue_yoy_change >= 0 ? "text-profit" : "text-cost"}`}>
                {kpis.revenue_yoy_change >= 0 ? "▲" : "▼"} {formatPercent(Math.abs(kpis.revenue_yoy_change))} YoY
              </div>
            )}
          </div>
          <div className="rpt-metric-card">
            <div className="rpt-metric-label">Net Income</div>
            <div className="rpt-metric-value text-profit">{formatCurrency(kpis.net_income)}</div>
            {kpis.operating_margin != null && (
              <div className="rpt-metric-sub text-muted-foreground">Op. Margin {formatPercent(kpis.operating_margin)}</div>
            )}
          </div>
          <div className="rpt-metric-card">
            <div className="rpt-metric-label">Debt / Equity</div>
            <div className={`rpt-metric-value ${(kpis.debt_to_equity ?? 0) > 2 ? "text-cost" : "text-foreground"}`}>
              {formatNumber(kpis.debt_to_equity)}
            </div>
            {(kpis.debt_to_equity ?? 0) > 2 && <div className="rpt-metric-sub text-cost">High leverage</div>}
          </div>
          <div className="rpt-metric-card rpt-metric-card-last">
            <div className="rpt-metric-label">Current Ratio</div>
            <div className={`rpt-metric-value ${(kpis.current_ratio ?? 999) < 1 ? "text-cost" : "text-foreground"}`}>
              {formatNumber(kpis.current_ratio)}
            </div>
            {(kpis.current_ratio ?? 999) < 1 && <div className="rpt-metric-sub text-cost">Below 1.0 threshold</div>}
          </div>
        </div>
      )}

      {/* ── Confidence Row ──────────────────────────────────────────── */}
      <div className="rpt-confidence-row">
        <span className="text-[11px] text-muted-foreground uppercase tracking-[0.5px] font-mono">Data Confidence</span>
        <div className="rpt-confidence-track">
          <div className="rpt-confidence-fill" style={{ width: `${confidence * 100}%` }} />
        </div>
        <span className="font-heading font-semibold text-[14px]">{confidence.toFixed(2)}</span>
        <span className={`rpt-badge ${riskLevelStyle}`}>{riskLevel} Risk</span>
      </div>

      {/* ── 1. Executive Summary ────────────────────────────────────── */}
      <div className="rpt-section">
        <div className="rpt-section-num">Section 1</div>
        <h2 className="rpt-section-title">Executive Summary</h2>
        {memo?.executive_summary?.split("\n").filter(Boolean).map((p, i) => (
          <p key={i} className="rpt-paragraph">{p}</p>
        )) || <p className="rpt-paragraph text-muted-foreground">No executive summary available.</p>}
      </div>

      {/* ── 2. Company Overview ─────────────────────────────────────── */}
      <div className="rpt-section">
        <div className="rpt-section-num">Section 2</div>
        <h2 className="rpt-section-title">Company Overview</h2>
        {memo?.company_overview?.split("\n").filter(Boolean).map((p, i) => (
          <p key={i} className="rpt-paragraph">{p}</p>
        )) || <p className="rpt-paragraph text-muted-foreground">No company overview available.</p>}
      </div>

      {/* ── 3. Financial Analysis ───────────────────────────────────── */}
      <div className="rpt-section">
        <div className="rpt-section-num">Section 3</div>
        <h2 className="rpt-section-title">Financial Analysis</h2>
        {memo?.financial_analysis?.split("\n").filter(Boolean).map((p, i) => (
          <p key={i} className="rpt-paragraph">{p}</p>
        )) || <p className="rpt-paragraph text-muted-foreground">No financial analysis available.</p>}

        {/* Profitability margin bars */}
        {kpis && (kpis.gross_margin != null || kpis.operating_margin != null) && (
          <div className="mt-4">
            <div className="text-[10px] uppercase tracking-[0.5px] text-muted-foreground font-mono mb-2.5">Profitability Margins</div>
            <div className="flex gap-5 items-end h-[80px] pb-1 border-b border-border">
              {[
                { label: "Gross", value: kpis.gross_margin, color: "var(--revenue)" },
                { label: "Operating", value: kpis.operating_margin, color: "var(--profit)" },
                { label: "Net", value: kpis.net_income && kpis.revenue ? kpis.net_income / kpis.revenue : null, color: "var(--ochre)" },
              ].filter(m => m.value != null).map((m) => (
                <div key={m.label} className="text-center">
                  <div
                    className="w-[60px] rounded-t-[2px]"
                    style={{
                      height: `${Math.max(8, (m.value! * 100) * 1.3)}px`,
                      background: m.color,
                      opacity: 0.8,
                    }}
                  />
                  <div className="text-[10px] mt-1 text-muted-foreground font-mono">{m.label}</div>
                  <div className="text-[12px] font-mono font-medium">{formatPercent(m.value)}</div>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>

      {/* ── 4. Risk Factor Analysis ─────────────────────────────────── */}
      <div className="rpt-section">
        <div className="rpt-section-num">Section 4</div>
        <h2 className="rpt-section-title">Risk Factor Analysis</h2>
        {memo?.risk_assessment?.split("\n").filter(Boolean).map((p, i) => (
          <p key={i} className="rpt-paragraph">{p}</p>
        )) || <p className="rpt-paragraph text-muted-foreground">No risk assessment available.</p>}

        {/* Risk dimension bars */}
        {risk && risk.dimensions.length > 0 && (
          <div className="mt-3 space-y-2">
            {risk.dimensions.map((dim) => (
              <div key={dim.dimension} className="flex items-center gap-3">
                <span className="w-[160px] text-[12px] font-mono text-right flex-shrink-0">{dim.dimension}</span>
                <div className="flex-1 max-w-[200px] h-2 rounded-[4px] overflow-hidden" style={{ background: "var(--ink-ghost)" }}>
                  <div
                    className="h-full rounded-[4px]"
                    style={{
                      width: `${(dim.score / 5) * 100}%`,
                      background: dim.score >= 4 ? "var(--cost)" : dim.score >= 3 ? "var(--ochre)" : "var(--profit)",
                    }}
                  />
                </div>
                <span className="text-[12px] font-mono font-medium w-[40px] flex-shrink-0" style={{
                  color: dim.score >= 4 ? "var(--cost)" : dim.score >= 3 ? "var(--ochre)" : "var(--profit)",
                }}>
                  {dim.score} / 5
                </span>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* ── 5. Insider Trading Signals ──────────────────────────────── */}
      <div className="rpt-section">
        <div className="rpt-section-num">Section 5</div>
        <h2 className="rpt-section-title">Insider Trading Signals</h2>
        {insider ? (
          <>
            <div className="flex items-center gap-8 mt-2.5">
              <div className="text-center">
                <div className="font-heading text-[28px] font-bold text-profit">{insider.total_buys}</div>
                <div className="text-[10px] uppercase tracking-[0.5px] text-muted-foreground font-mono">Buys</div>
              </div>
              <div className="text-center">
                <div className="font-heading text-[28px] font-bold text-cost">{insider.total_sells}</div>
                <div className="text-[10px] uppercase tracking-[0.5px] text-muted-foreground font-mono">Sells</div>
              </div>
              <span className={`rpt-badge px-4 py-1.5 text-[12px] capitalize font-mono font-medium ${signalColor}`}>
                {insider.signal === "bearish" ? "⬇" : insider.signal === "bullish" ? "⬆" : "—"} {insider.signal}
              </span>
            </div>
            {insider.cluster_detected && insider.cluster_description && (
              <p className="rpt-paragraph mt-2 text-cost">{insider.cluster_description}</p>
            )}
          </>
        ) : (
          <p className="rpt-paragraph text-muted-foreground">No insider trading data available.</p>
        )}
      </div>

      {/* ── 6. Institutional Ownership ──────────────────────────────── */}
      <div className="rpt-section">
        <div className="rpt-section-num">Section 6</div>
        <h2 className="rpt-section-title">Institutional Ownership</h2>
        {holders.length > 0 ? (
          <table className="rpt-table">
            <thead>
              <tr>
                <th className="text-left">Holder</th>
                <th className="text-right">Shares</th>
                <th>Type</th>
              </tr>
            </thead>
            <tbody>
              {holders.slice(0, 8).map((h, i) => (
                <tr key={i}>
                  <td>{h.holder_name}</td>
                  <td className="text-right">{h.shares.toLocaleString()}</td>
                  <td className="text-center">
                    <span className={`rpt-badge ${h.holder_type === "passive" ? "bg-revenue-light text-revenue" : "bg-ochre-light text-ochre"}`}>
                      {h.holder_type}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <p className="rpt-paragraph text-muted-foreground">No institutional ownership data available.</p>
        )}
      </div>

      {/* ── 7. Material Events ──────────────────────────────────────── */}
      <div className="rpt-section">
        <div className="rpt-section-num">Section 7</div>
        <h2 className="rpt-section-title">Material Events</h2>
        {events.length > 0 ? (
          <table className="rpt-table">
            <thead>
              <tr>
                <th className="text-left">Date</th>
                <th className="text-left">Event</th>
                <th>Severity</th>
              </tr>
            </thead>
            <tbody>
              {events.slice(0, 8).map((e, i) => (
                <tr key={i}>
                  <td className="whitespace-nowrap">{e.filing_date}</td>
                  <td>{e.item_code} {e.item_description}</td>
                  <td className="text-center whitespace-nowrap">
                    <SeverityDots score={e.severity} /> {e.severity}/5
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <p className="rpt-paragraph text-muted-foreground">No material events in the past 12 months.</p>
        )}
      </div>

      {/* ── 8. Governance & Compensation ────────────────────────────── */}
      <div className="rpt-section">
        <div className="rpt-section-num">Section 8</div>
        <h2 className="rpt-section-title">Governance &amp; Compensation</h2>
        {governance && (governance.ceo_name || governance.board_size) ? (
          <div className="grid grid-cols-2 gap-3 mt-2.5">
            {governance.ceo_name && (
              <div className="rpt-gov-item">
                <div className="rpt-gov-label">CEO</div>
                <div className="rpt-gov-value">{governance.ceo_name}</div>
              </div>
            )}
            {governance.ceo_total_comp != null && (
              <div className="rpt-gov-item">
                <div className="rpt-gov-label">CEO Total Comp</div>
                <div className="rpt-gov-value">{formatCurrency(governance.ceo_total_comp)}</div>
              </div>
            )}
            {governance.board_independence_pct != null && (
              <div className="rpt-gov-item">
                <div className="rpt-gov-label">Board Independence</div>
                <div className="rpt-gov-value">{formatPercent(governance.board_independence_pct)}</div>
              </div>
            )}
            {governance.ceo_pay_ratio != null && (
              <div className="rpt-gov-item">
                <div className="rpt-gov-label">CEO Pay Ratio</div>
                <div className="rpt-gov-value">{governance.ceo_pay_ratio}x</div>
              </div>
            )}
          </div>
        ) : (
          <p className="rpt-paragraph text-muted-foreground">No governance data available.</p>
        )}
      </div>

      {/* ── 9. Cross-Workstream Red Flags ───────────────────────────── */}
      <div className="rpt-section">
        <div className="rpt-section-num">Section 9</div>
        <h2 className="rpt-section-title">Cross-Workstream Red Flags</h2>
        {crossFlags.length > 0 ? (
          <div className="space-y-2.5 mt-2">
            {crossFlags.map((cf, i) => (
              <div key={i} className="rpt-alert-card">
                <div className="flex items-center gap-2 mb-1">
                  <span className={`rpt-badge ${
                    cf.severity === "Critical" ? "bg-cost-light text-cost"
                      : cf.severity === "High" ? "bg-ochre-light text-ochre"
                        : "bg-secondary text-muted-foreground"
                  }`}>{cf.severity}</span>
                  <span className="font-mono font-medium text-[12.5px]">{cf.rule_name}</span>
                </div>
                <p className="text-[12px] font-mono leading-relaxed" style={{ color: "var(--cost)" }}>{cf.description}</p>
              </div>
            ))}
          </div>
        ) : (
          <p className="rpt-paragraph text-muted-foreground">No cross-workstream red flags identified.</p>
        )}
      </div>

      {/* ── 10. Recommendation & Caveats ────────────────────────────── */}
      <div className="rpt-section">
        <div className="rpt-section-num">Section 10</div>
        <h2 className="rpt-section-title">Recommendation &amp; Caveats</h2>
        {memo?.recommendation?.split("\n").filter(Boolean).map((p, i) => (
          <p key={i} className="rpt-paragraph">{p}</p>
        )) || <p className="rpt-paragraph text-muted-foreground">No recommendation available.</p>}

        {/* Key Findings */}
        {memo?.key_findings && memo.key_findings.length > 0 && (
          <div className="mt-4">
            <div className="text-[10px] uppercase tracking-[0.5px] text-muted-foreground font-mono mb-2">Key Findings</div>
            <ul className="rpt-findings-list">
              {memo.key_findings.map((f, i) => (
                <li key={i}>{f}</li>
              ))}
            </ul>
          </div>
        )}
      </div>

      {/* ── Footer ──────────────────────────────────────────────────── */}
      <div className="rpt-footer">
        Generated by DiligenceOps v0.3 · Confidence: {confidence.toFixed(2)} · {memo?.generated_at ?? "—"}
      </div>
    </div>
  );
}

function DownloadButtons({ runId }: { runId: string }) {
  const files = [
    { type: "bronze_csv", label: "Bronze CSV" },
    { type: "silver_csv", label: "Silver CSV" },
    { type: "gold_csv", label: "Gold CSV" },
    { type: "memo_md", label: "Memo (MD)" },
    { type: "risk_factors_csv", label: "Risk Factors" },
    { type: "insider_trades_csv", label: "Insider Trades" },
    { type: "institutional_csv", label: "Institutional" },
    { type: "events_csv", label: "Events" },
    { type: "governance_csv", label: "Governance" },
  ];
  return (
    <div className="flex flex-wrap gap-2">
      {files.map((f) => (
        <a
          key={f.type}
          href={getDownloadUrl(runId, f.type)}
          className="px-3 py-1.5 text-xs border border-ink-faint hover:border-revenue hover:text-revenue rounded-sm font-medium font-mono transition-colors text-muted-foreground"
          download
        >
          {f.label}
        </a>
      ))}
    </div>
  );
}

// --- Main Page ---

export default function Home() {
  const [runId, setRunId] = useState<string | null>(null);
  const [progress, setProgress] = useState<PipelineProgress[]>([]);
  const [results, setResults] = useState<PipelineResults | null>(null);
  const [memoText, setMemoText] = useState<string>("");
  const [isRunning, setIsRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState<TabKey>("kpis");
  const wsRef = useRef<WebSocket | null>(null);
  const pollRef = useRef<NodeJS.Timeout | null>(null);

  const handleSubmit = useCallback(async (ticker: string) => {
    setIsRunning(true);
    setError(null);
    setProgress([]);
    setResults(null);
    setMemoText("");
    setActiveTab("kpis");

    try {
      const { run_id } = await startAnalysis(ticker);
      setRunId(run_id);

      // Connect WebSocket for progress
      const ws = connectWebSocket(
        run_id,
        (p) => {
          setProgress((prev) => [...prev, p]);
          if (p.stage === "complete" || p.stage === "error") {
            setIsRunning(false);
            // Fetch final results
            getResults(run_id).then((r) => {
              setResults(r);
              // Fetch memo text
              if (r.files?.memo_md) {
                fetch(getDownloadUrl(run_id, "memo_md"))
                  .then((res) => res.text())
                  .then(setMemoText)
                  .catch(() => {});
              }
            });
          }
        },
        () => {
          // On WS close, poll for results if still running
          if (pollRef.current) clearInterval(pollRef.current);
          pollRef.current = setInterval(async () => {
            try {
              const r = await getResults(run_id);
              if (r.status === "complete" || r.status === "error") {
                setResults(r);
                setIsRunning(false);
                if (pollRef.current) clearInterval(pollRef.current);
                if (r.files?.memo_md) {
                  fetch(getDownloadUrl(run_id, "memo_md"))
                    .then((res) => res.text())
                    .then(setMemoText)
                    .catch(() => {});
                }
              }
            } catch {}
          }, 2000);
        }
      );
      wsRef.current = ws;
    } catch (err: any) {
      setError(err.message || "Failed to start analysis");
      setIsRunning(false);
    }
  }, []);

  useEffect(() => {
    return () => {
      wsRef.current?.close();
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, []);

  return (
    <div className="min-h-screen relative z-1">
      {/* Header */}
      <header className="max-w-[1160px] mx-auto px-8 pt-14 pb-8 border-b-2 border-foreground grid grid-cols-[1fr_auto] items-end gap-6 animate-slide-in">
        <div>
          <p className="text-[12px] text-primary uppercase tracking-[3px] font-mono font-medium mb-2">Due Diligence Pipeline</p>
          <h1 className="font-heading text-[clamp(36px,5.5vw,56px)] font-bold leading-none tracking-tight text-foreground">
            Diligence<span className="italic font-normal text-primary">Ops</span>
          </h1>
        </div>
        <TickerInput onSubmit={handleSubmit} disabled={isRunning} />
      </header>

      <main className="max-w-[1160px] mx-auto px-8 py-10 space-y-10 relative z-1">
        {/* Error */}
        {error && (
          <div className="bg-cost-light border border-cost rounded-sm p-4 text-sm text-cost font-mono animate-slide-in">
            {error}
          </div>
        )}

        {/* Progress */}
        {(isRunning || progress.length > 0) && (
          <PipelineProgressBar progress={progress} />
        )}

        {/* Results */}
        {results && results.status === "complete" && (
          <>
            {/* Deal Recommendation Banner */}
            {results.deal_recommendation && (
              <DealRecommendationBanner recommendation={results.deal_recommendation} />
            )}

            {/* Company header */}
            <div className="animate-slide-in pb-6 border-b border-ink-faint">
              <div className="flex items-end justify-between">
                <div>
                  <h2 className="font-heading text-2xl font-semibold text-foreground">
                    {results.company_info?.company_name} <span className="text-muted-foreground font-normal">({results.ticker})</span>
                  </h2>
                  <div className="flex items-center gap-4 mt-2 text-[13px] text-muted-foreground font-mono">
                    <span><strong className="text-foreground font-medium">FY:</strong> {results.kpis?.fiscal_year}</span>
                    <span><strong className="text-foreground font-medium">Sector:</strong> {results.company_info?.sic_description}</span>
                    <span><strong className="text-foreground font-medium">Confidence:</strong> {((results.confidence ?? 0) * 100).toFixed(0)}%</span>
                  </div>
                </div>
                {runId && <DownloadButtons runId={runId} />}
              </div>
            </div>

            {/* Tabs — Period Pills */}
            <div className="flex gap-0.5">
              {TABS.map((tab, i) => (
                <button
                  key={tab.key}
                  onClick={() => setActiveTab(tab.key)}
                  className={`px-4 py-2 text-[13px] font-mono border border-ink-faint transition-colors ${
                    activeTab === tab.key
                      ? "bg-foreground text-background border-foreground"
                      : "text-muted-foreground hover:text-foreground bg-transparent"
                  } ${i === 0 ? "rounded-l-sm" : ""} ${i === TABS.length - 1 ? "rounded-r-sm" : ""}`}
                >
                  {tab.label}
                </button>
              ))}
            </div>

            {/* Tab content with Prism section headers */}
            {activeTab === "kpis" && results.kpis && (
              <div className="space-y-6">
                <div className="flex justify-between items-baseline pb-2 border-b border-ink-faint">
                  <h3 className="font-heading text-xl font-semibold">Financial Overview <em className="font-normal italic text-muted-foreground">— key performance indicators</em></h3>
                </div>
                <KpiDashboard kpis={results.kpis} />
              </div>
            )}
            {activeTab === "risk" && results.risk_scores && (
              <div className="space-y-6">
                <div className="flex justify-between items-baseline pb-2 border-b border-ink-faint">
                  <h3 className="font-heading text-xl font-semibold">Risk Analysis <em className="font-normal italic text-muted-foreground">— dimension scoring</em></h3>
                </div>
                <RiskHeatmap risk={results.risk_scores} flags={results.cross_workstream_flags} />
              </div>
            )}
            {activeTab === "risk_factors" && (
              <div className="space-y-6">
                <div className="flex justify-between items-baseline pb-2 border-b border-ink-faint">
                  <h3 className="font-heading text-xl font-semibold">Risk Factors <em className="font-normal italic text-muted-foreground">— 10-K disclosures</em></h3>
                </div>
                {results.risk_factors && results.risk_factors.length > 0
                  ? <RiskFactorPanel factors={results.risk_factors} />
                  : <div className="bg-card rounded-sm border border-border p-8 text-center text-muted-foreground font-mono">No risk factors data available.</div>
                }
              </div>
            )}
            {activeTab === "insider" && (
              <div className="space-y-6">
                <div className="flex justify-between items-baseline pb-2 border-b border-ink-faint">
                  <h3 className="font-heading text-xl font-semibold">Insider Activity <em className="font-normal italic text-muted-foreground">— trading signals</em></h3>
                </div>
                <InsiderPanel signal={results.insider_signal} trades={results.insider_trades} />
              </div>
            )}
            {activeTab === "institutional" && (
              <div className="space-y-6">
                <div className="flex justify-between items-baseline pb-2 border-b border-ink-faint">
                  <h3 className="font-heading text-xl font-semibold">Institutional Ownership <em className="font-normal italic text-muted-foreground">— top holders</em></h3>
                </div>
                <InstitutionalPanel holders={results.institutional_holders ?? []} />
              </div>
            )}
            {activeTab === "events" && (
              <div className="space-y-6">
                <div className="flex justify-between items-baseline pb-2 border-b border-ink-faint">
                  <h3 className="font-heading text-xl font-semibold">Material Events <em className="font-normal italic text-muted-foreground">— 8-K filings</em></h3>
                </div>
                <EventsTimeline events={results.material_events ?? []} />
              </div>
            )}
            {activeTab === "governance" && (
              <div className="space-y-6">
                <div className="flex justify-between items-baseline pb-2 border-b border-ink-faint">
                  <h3 className="font-heading text-xl font-semibold">Governance <em className="font-normal italic text-muted-foreground">— board & compensation</em></h3>
                </div>
                {results.governance
                  ? <GovernancePanel governance={results.governance} />
                  : <div className="bg-card rounded-sm border border-border p-8 text-center text-muted-foreground font-mono">No governance data available.</div>
                }
              </div>
            )}
            {activeTab === "memo" && (results.memo || memoText) && (
              <div className="space-y-6">
                <div className="flex justify-between items-baseline pb-2 border-b border-ink-faint">
                  <h3 className="font-heading text-xl font-semibold">Full Report <em className="font-normal italic text-muted-foreground">— diligence memo</em></h3>
                </div>
                <ReportViewer results={results} memoText={memoText} />
              </div>
            )}
          </>
        )}

        {/* Empty state */}
        {!isRunning && !results && !error && (
          <div className="flex flex-col items-center justify-center py-24 text-muted-foreground animate-fade-in">
            <svg className="w-14 h-14 mb-6 text-ink-faint" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={0.75} d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z" />
            </svg>
            <p className="font-heading text-xl font-semibold text-foreground">Enter a ticker to start analysis</p>
            <p className="text-[13px] mt-2 font-mono text-ink-muted">Try AAPL, TSLA, or GME</p>
          </div>
        )}
      </main>

      {/* Footer */}
      <footer className="max-w-[1160px] mx-auto px-8 mt-0 pt-4 pb-10 border-t border-ink-faint flex justify-between text-[11px] text-ink-faint font-mono tracking-wider animate-slide-in">
        <span>DiligenceOps · Confidential</span>
        <span>AI Due Diligence Pipeline</span>
      </footer>
    </div>
  );
}
