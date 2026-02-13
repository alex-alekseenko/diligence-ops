"use client";

import { useState, useRef, useCallback } from "react";
import type { FinancialKPIs } from "@/lib/types";

/* ── Types ──────────────────────────────────────────────────────────── */

type NodeType = "rev" | "profit" | "cost";

interface NodeDef {
  id: string;
  label: string;
  col: number;
  type: NodeType;
  val: number;
}

interface LinkDef {
  s: string;
  t: string;
  v: number;
}

interface LayoutNode extends NodeDef {
  x: number;
  y: number;
  w: number;
  h: number;
  color: string;
}

interface LayoutLink extends LinkDef {
  sy: number;
  sh: number;
  ty: number;
  th: number;
  path: string;
  color: string;
  isProfitPath: boolean;
}

/* ── Constants ──────────────────────────────────────────────────────── */

const TYPE_COLOR: Record<NodeType, string> = {
  rev: "var(--revenue)",
  profit: "var(--profit)",
  cost: "var(--cost)",
};

const TYPE_LABEL: Record<NodeType, string> = {
  rev: "Revenue",
  profit: "Profit",
  cost: "Cost",
};

const PROFIT_PATHS = new Set([
  "revenue→gross_profit",
  "gross_profit→op_income",
  "op_income→net_income",
]);

const SVG_W = 1100;
const SVG_H = 480;
const PAD = { l: 130, r: 100, t: 60, b: 28 };
const NODE_W = 14;
const NODE_GAP = 32;
const COL_POSITIONS = [0, 0.30, 0.60, 0.90];
const COL_LABELS = ["REVENUE", "GROSS SPLIT", "OPERATING", "NET"];

/* ── Helpers ────────────────────────────────────────────────────────── */

function fmtVal(v: number): string {
  const abs = Math.abs(v);
  if (abs >= 1e12) return `$${(v / 1e12).toFixed(1)}T`;
  if (abs >= 1e9) return `$${(v / 1e9).toFixed(1)}B`;
  if (abs >= 1e6) return `$${(v / 1e6).toFixed(1)}M`;
  if (abs >= 1e3) return `$${(v / 1e3).toFixed(0)}k`;
  return `$${v.toLocaleString()}`;
}

function fmtPct(v: number): string {
  return `${(v * 100).toFixed(1)}%`;
}

/* ── Layout engine ──────────────────────────────────────────────────── */

function computeLayout(kpis: FinancialKPIs) {
  const revenue = kpis.revenue ?? 0;
  const grossProfit = kpis.gross_profit ?? 0;
  const costOfRevenue = Math.max(0, revenue - grossProfit);
  const operatingIncome = kpis.operating_income ?? 0;
  const operatingExpenses = Math.max(0, grossProfit - operatingIncome);
  const netIncome = kpis.net_income ?? 0;
  const taxAndOther = Math.max(0, operatingIncome - netIncome);

  const grossMargin = kpis.gross_margin ?? (revenue ? grossProfit / revenue : 0);
  const operatingMargin = kpis.operating_margin ?? (revenue ? operatingIncome / revenue : 0);
  const netMargin = revenue ? netIncome / revenue : 0;

  const nodeDefs = ([
    { id: "revenue", label: "Revenue", col: 0, type: "rev" as const, val: revenue },
    { id: "gross_profit", label: "Gross Profit", col: 1, type: "profit" as const, val: grossProfit },
    { id: "cogs", label: "Cost of Revenue", col: 1, type: "cost" as const, val: costOfRevenue },
    { id: "op_income", label: "Operating Income", col: 2, type: "profit" as const, val: operatingIncome },
    { id: "opex", label: "Operating Expenses", col: 2, type: "cost" as const, val: operatingExpenses },
    { id: "net_income", label: "Net Income", col: 3, type: "profit" as const, val: netIncome },
    { id: "tax_other", label: "Tax & Other", col: 3, type: "cost" as const, val: taxAndOther },
  ] satisfies NodeDef[]).filter(n => n.val > 0);

  const linkDefs: LinkDef[] = [
    { s: "revenue", t: "gross_profit", v: grossProfit },
    { s: "revenue", t: "cogs", v: costOfRevenue },
    { s: "gross_profit", t: "op_income", v: operatingIncome },
    { s: "gross_profit", t: "opex", v: operatingExpenses },
    { s: "op_income", t: "net_income", v: netIncome },
    { s: "op_income", t: "tax_other", v: taxAndOther },
  ].filter(l => l.v > 0);

  // Build node map
  const nm: Record<string, LayoutNode> = {};
  const cols: LayoutNode[][] = [[], [], [], []];

  nodeDefs.forEach(n => {
    const ln: LayoutNode = { ...n, x: 0, y: 0, w: NODE_W, h: 0, color: TYPE_COLOR[n.type] };
    nm[n.id] = ln;
    cols[n.col].push(ln);
  });

  // Position nodes vertically within each column
  cols.forEach((col, ci) => {
    if (col.length === 0) return;
    const totalVal = col.reduce((a, n) => a + n.val, 0);
    const availH = SVG_H - PAD.t - PAD.b - (col.length - 1) * NODE_GAP;
    let y = PAD.t;
    col.forEach(n => {
      n.h = Math.max(24, (n.val / totalVal) * availH);
      n.x = PAD.l + COL_POSITIONS[ci] * (SVG_W - PAD.l - PAD.r - NODE_W);
      n.y = y;
      y += n.h + NODE_GAP;
    });
    // Center vertically
    const totalUsed = y - NODE_GAP - PAD.t;
    const offset = (SVG_H - PAD.t - PAD.b - totalUsed) / 2;
    if (offset > 0) col.forEach(n => (n.y += offset));
  });

  // Position links
  const sourceOffsets: Record<string, number> = {};
  const targetOffsets: Record<string, number> = {};

  const layoutLinks: LayoutLink[] = linkDefs
    .filter(l => nm[l.s] && nm[l.t])
    .map(l => {
      const sn = nm[l.s], tn = nm[l.t];
      if (!sourceOffsets[l.s]) sourceOffsets[l.s] = 0;
      if (!targetOffsets[l.t]) targetOffsets[l.t] = 0;

      const sTotal = linkDefs.filter(x => x.s === l.s).reduce((a, b) => a + b.v, 0);
      const tTotal = linkDefs.filter(x => x.t === l.t).reduce((a, b) => a + b.v, 0);

      const sy = sn.y + sourceOffsets[l.s];
      const sh = (l.v / sTotal) * sn.h;
      sourceOffsets[l.s] += sh;

      const ty = tn.y + targetOffsets[l.t];
      const th = (l.v / tTotal) * tn.h;
      targetOffsets[l.t] += th;

      const sx = sn.x + sn.w, tx = tn.x, mx = (sx + tx) / 2;
      const path = `M${sx},${sy} C${mx},${sy} ${mx},${ty} ${tx},${ty} L${tx},${ty + th} C${mx},${ty + th} ${mx},${sy + sh} ${sx},${sy + sh} Z`;
      const isProfitPath = PROFIT_PATHS.has(`${l.s}→${l.t}`);

      return { ...l, sy, sh, ty, th, path, color: tn.color, isProfitPath };
    });

  return { nm, cols, layoutLinks, revenue, grossMargin, operatingMargin, netMargin };
}

/* ── Component ──────────────────────────────────────────────────────── */

export default function IncomeFlowSankey({ kpis }: { kpis: FinancialKPIs }) {
  const wrapRef = useRef<HTMLDivElement>(null);
  const [hoveredNode, setHoveredNode] = useState<string | null>(null);
  const [hoveredLink, setHoveredLink] = useState<number | null>(null);
  const [tooltip, setTooltip] = useState<{ html: string; x: number; y: number; visible: boolean; flipX: boolean; flipY: boolean }>({
    html: "", x: 0, y: 0, visible: false, flipX: false, flipY: false,
  });

  const revenue = kpis.revenue ?? 0;
  if (revenue <= 0) {
    return (
      <div className="bg-card rounded-sm border border-border p-12 text-center text-muted-foreground font-mono text-sm">
        Revenue data not available for income flow chart.
      </div>
    );
  }

  const { nm, cols, layoutLinks, grossMargin, operatingMargin, netMargin } = computeLayout(kpis);

  // Get all connected nodes for highlighting
  const getConnected = useCallback((nodeId: string): Set<string> => {
    const ids = new Set([nodeId]);
    const linkDefs = layoutLinks;
    function fwd(id: string) {
      linkDefs.filter(l => l.s === id).forEach(l => { ids.add(l.t); fwd(l.t); });
    }
    function bwd(id: string) {
      linkDefs.filter(l => l.t === id).forEach(l => { ids.add(l.s); bwd(l.s); });
    }
    fwd(nodeId);
    bwd(nodeId);
    return ids;
  }, [layoutLinks]);

  const isLinkHighlighted = (link: LayoutLink, idx: number): boolean => {
    if (hoveredLink === idx) return true;
    if (hoveredNode) {
      const connected = getConnected(hoveredNode);
      return connected.has(link.s) && connected.has(link.t);
    }
    return false;
  };

  const isLinkDimmed = (link: LayoutLink, idx: number): boolean => {
    if (hoveredNode === null && hoveredLink === null) return false;
    return !isLinkHighlighted(link, idx);
  };

  // Tooltip positioning
  const showTooltip = (html: string, e: React.MouseEvent) => {
    if (!wrapRef.current) return;
    const r = wrapRef.current.getBoundingClientRect();
    const tipW = 240, tipH = 140;
    let left = e.clientX - r.left + 16;
    let top = e.clientY - r.top - 20;
    let flipX = false, flipY = false;
    if (left + tipW > r.width - 8) { left = e.clientX - r.left - tipW - 16; flipX = true; }
    if (top + tipH > r.height - 8) { top = e.clientY - r.top - tipH + 20; flipY = true; }
    left = Math.max(4, Math.min(left, r.width - tipW - 4));
    top = Math.max(4, Math.min(top, r.height - tipH - 4));
    setTooltip({ html, x: left, y: top, visible: true, flipX, flipY });
  };

  const hideTooltip = () => {
    setHoveredNode(null);
    setHoveredLink(null);
    setTooltip(t => ({ ...t, visible: false }));
  };

  // Build tooltip HTML for a node
  const nodeTooltipHtml = (n: LayoutNode): string => {
    const inflows = layoutLinks.filter(l => l.t === n.id);
    const outflows = layoutLinks.filter(l => l.s === n.id);
    let h = `<div class="stt-title"><div class="stt-dot" style="background:${n.color}"></div>${n.label}</div>`;
    h += `<div class="stt-row"><span class="stt-label">Amount</span><span class="stt-val">${fmtVal(n.val)}</span></div>`;
    h += `<div class="stt-row"><span class="stt-label">Type</span><span class="stt-val" style="color:${n.color}">${TYPE_LABEL[n.type]}</span></div>`;
    h += `<div class="stt-row"><span class="stt-label">% of Revenue</span><span class="stt-val">${fmtPct(n.val / revenue)}</span></div>`;
    if (inflows.length) {
      h += `<div class="stt-divider">INFLOWS</div>`;
      inflows.forEach(l => { h += `<div class="stt-row"><span class="stt-label">${nm[l.s]?.label ?? l.s}</span><span class="stt-val">${fmtVal(l.v)}</span></div>`; });
    }
    if (outflows.length) {
      h += `<div class="stt-divider">OUTFLOWS</div>`;
      outflows.forEach(l => { h += `<div class="stt-row"><span class="stt-label">${nm[l.t]?.label ?? l.t}</span><span class="stt-val">${fmtVal(l.v)}</span></div>`; });
    }
    return h;
  };

  // Build tooltip HTML for a link
  const linkTooltipHtml = (link: LayoutLink): string => {
    const sn = nm[link.s], tn = nm[link.t];
    return `<div class="stt-title"><div class="stt-dot" style="background:${tn?.color ?? link.color}"></div>${sn?.label ?? link.s} → ${tn?.label ?? link.t}</div>` +
      `<div class="stt-row"><span class="stt-label">Amount</span><span class="stt-val">${fmtVal(link.v)}</span></div>` +
      `<div class="stt-row"><span class="stt-label">Share</span><span class="stt-val">${fmtPct(link.v / revenue)}</span></div>`;
  };

  // Annotations data
  const rv = nm["revenue"], gp = nm["gross_profit"], oi = nm["op_income"], np = nm["net_income"];

  return (
    <div className="space-y-4">
      {/* Legend */}
      <div className="flex gap-7 px-4 py-3 border border-ink-ghost rounded-sm" style={{ background: "var(--canvas-warm)" }}>
        <div className="flex items-center gap-2 text-[13px] text-muted-foreground font-mono">
          <div className="w-5 h-1.5 rounded-[1px]" style={{ background: "var(--revenue)" }} />
          Revenue
        </div>
        <div className="flex items-center gap-2 text-[13px] text-muted-foreground font-mono">
          <div className="w-5 h-1.5 rounded-[1px] relative" style={{ background: "var(--profit)" }}>
            <div className="absolute inset-0 rounded-[1px]" style={{ background: "repeating-linear-gradient(90deg, transparent 0 3px, rgba(255,255,255,.45) 3px 5px)" }} />
          </div>
          Profit
        </div>
        <div className="flex items-center gap-2 text-[13px] text-muted-foreground font-mono">
          <div className="w-5 h-1.5 rounded-[1px] relative" style={{ background: "var(--cost)" }}>
            <div className="absolute inset-0 rounded-[1px]" style={{ background: "repeating-linear-gradient(135deg, transparent 0 2px, rgba(255,255,255,.5) 2px 3px)" }} />
          </div>
          Costs &amp; Expenses
        </div>
      </div>

      {/* Chart panel */}
      <div ref={wrapRef} className="bg-card rounded-sm border border-border p-8 relative overflow-hidden">
        <svg
          viewBox={`0 0 ${SVG_W} ${SVG_H}`}
          className="w-full h-auto block overflow-visible"
          role="img"
          aria-label="Income statement Sankey diagram showing revenue flow through gross profit to net income"
        >
          <title>Income Statement Flow — P&amp;L Waterfall</title>

          {/* Column labels */}
          {COL_POSITIONS.map((cp, i) => (
            <text
              key={i}
              x={PAD.l + cp * (SVG_W - PAD.l - PAD.r - NODE_W) + NODE_W / 2}
              y={28}
              textAnchor="middle"
              className="sankey-col-label"
            >
              {COL_LABELS[i]}
            </text>
          ))}
          <line x1={PAD.l} y1={38} x2={SVG_W - PAD.r} y2={38} stroke="var(--ink-ghost)" strokeWidth={0.5} />

          {/* Links (behind nodes) */}
          {layoutLinks.map((link, i) => {
            const highlighted = isLinkHighlighted(link, i);
            const dimmed = isLinkDimmed(link, i);
            const fillOp = dimmed ? 0.03 : highlighted ? 0.3 : link.isProfitPath ? 0.35 : 0.25;
            const strokeOp = dimmed ? 0.04 : highlighted ? 0.45 : link.isProfitPath ? 0.55 : 0.4;
            return (
              <path
                key={`link-${i}`}
                d={link.path}
                fill={link.color}
                fillOpacity={fillOp}
                stroke={link.color}
                strokeOpacity={strokeOp}
                strokeWidth={highlighted ? 1.5 : 0.5}
                className="sankey-link-path"
                style={{ transition: "fill-opacity 0.3s, stroke-opacity 0.3s" }}
                onMouseEnter={(e) => {
                  setHoveredLink(i);
                  setHoveredNode(null);
                  showTooltip(linkTooltipHtml(link), e);
                }}
                onMouseMove={(e) => showTooltip(linkTooltipHtml(link), e)}
                onMouseLeave={hideTooltip}
              />
            );
          })}

          {/* Nodes (on top) */}
          {Object.values(nm).map(n => {
            const lx = n.col === 0 ? n.x - 10 : n.x + n.w + 10;
            const anc = n.col === 0 ? "end" : "start";
            const ly = n.y + n.h / 2;
            return (
              <g
                key={n.id}
                className="sankey-node"
                onMouseEnter={(e) => {
                  setHoveredNode(n.id);
                  setHoveredLink(null);
                  showTooltip(nodeTooltipHtml(n), e);
                }}
                onMouseMove={(e) => showTooltip(nodeTooltipHtml(n), e)}
                onMouseLeave={hideTooltip}
              >
                <rect
                  className="sankey-node-rect"
                  x={n.x} y={n.y} width={n.w} height={n.h}
                  fill={n.color} rx={2}
                  stroke="var(--ink-ghost)" strokeWidth={1}
                />
                {/* Wider hit target */}
                <rect x={n.x - 20} y={n.y} width={n.w + 40} height={n.h} fill="transparent" className="cursor-pointer" />
                <text x={lx} y={ly - 11} textAnchor={anc} className="sankey-node-label">{n.label}</text>
                <text x={lx} y={ly + 7} textAnchor={anc} className="sankey-node-value">{fmtVal(n.val)}</text>
                {n.col >= 1 && (
                  <text x={lx} y={ly + 23} textAnchor={anc} className="sankey-node-pct">{fmtPct(n.val / revenue)}</text>
                )}
              </g>
            );
          })}

          {/* Annotations — margin labels */}
          {rv && gp && (
            <>
              {(() => {
                const gmX = (rv.x + rv.w + gp.x) / 2;
                const gmY = Math.min(rv.y, gp.y) - 14;
                return (
                  <>
                    <line x1={gmX} y1={gmY + 8} x2={gmX} y2={gmY + 28} className="sankey-annotation-line" />
                    <text x={gmX} y={gmY + 4} textAnchor="middle" className="sankey-annotation-text" style={{ fill: "var(--profit)" }}>
                      {fmtPct(grossMargin)} gross margin
                    </text>
                  </>
                );
              })()}
            </>
          )}

          {gp && oi && (
            <>
              {(() => {
                const omX = (gp.x + gp.w + oi.x) / 2;
                const omY = Math.min(gp.y, oi.y) - 14;
                return (
                  <>
                    <line x1={omX} y1={omY + 8} x2={omX} y2={omY + 28} className="sankey-annotation-line" />
                    <text x={omX} y={omY + 4} textAnchor="middle" className="sankey-annotation-text" style={{ fill: "var(--profit)" }}>
                      {fmtPct(operatingMargin)} operating margin
                    </text>
                  </>
                );
              })()}
            </>
          )}

          {/* COGS annotation */}
          {rv && nm["cogs"] && (
            (() => {
              const cogs = nm["cogs"];
              const crX = (rv.x + rv.w + cogs.x) / 2;
              const crY = cogs.y + cogs.h + 20;
              return (
                <text x={crX} y={crY} textAnchor="middle" className="sankey-annotation-text" style={{ fill: "var(--cost)" }}>
                  {fmtPct((kpis.revenue ?? 0) > 0 ? cogs.val / (kpis.revenue ?? 1) : 0)} COGS
                </text>
              );
            })()
          )}

          {/* Net margin annotation */}
          {np && (
            <>
              <line
                x1={np.x + np.w + 6} y1={np.y - 6}
                x2={np.x + np.w + 6} y2={np.y + np.h + 6}
                className="sankey-annotation-line"
              />
              <text
                x={np.x + np.w + 14} y={np.y + np.h + 18}
                className="sankey-annotation-text"
                style={{ fill: "var(--profit)" }}
              >
                {fmtPct(netMargin)} net margin
              </text>
            </>
          )}

          {/* Column totals */}
          {cols.map((col, ci) => {
            if (ci < 1 || col.length === 0) return null;
            const totalVal = col.reduce((a, n) => a + n.val, 0);
            const bottomY = Math.max(...col.map(n => n.y + n.h)) + 18;
            const cx = PAD.l + COL_POSITIONS[ci] * (SVG_W - PAD.l - PAD.r - NODE_W) + NODE_W / 2;
            return (
              <text key={`total-${ci}`} x={cx} y={bottomY} textAnchor="middle" className="sankey-col-total">
                {fmtVal(totalVal)}
              </text>
            );
          })}
        </svg>

        {/* Tooltip */}
        <div
          className={`sankey-tooltip ${tooltip.visible ? "visible" : ""} ${tooltip.flipX ? "flip-x" : ""} ${tooltip.flipY ? "flip-y" : ""}`}
          style={{ left: tooltip.x, top: tooltip.y }}
          dangerouslySetInnerHTML={{ __html: tooltip.html }}
        />
      </div>
    </div>
  );
}
