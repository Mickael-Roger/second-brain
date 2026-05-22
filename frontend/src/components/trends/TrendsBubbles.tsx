// Nested bubble layout for the Trends view.
//
// Each CATEGORY is a big outer bubble containing the trend sub-bubbles
// that belong to it. Only the top-N trends globally (default 10)
// are shown — categories with no top-N trend simply don't render.
//
// Bubble size encodes softmax weight (relative to the top-N set);
// colour encodes short-term direction (rising / falling / new / stable)
// computed from the history window.

import { useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import type { TrendDTO } from "@/lib/api";

export type TrendDirection = "up" | "down" | "stable" | "new";

export const TOP_N_TRENDS = 10;

interface SubBubble {
  trend: TrendDTO;
  direction: TrendDirection;
  delta: number;
  // Position relative to the parent category bubble's centre.
  dx: number;
  dy: number;
  r: number;
}

interface CategoryBubble {
  category: string;
  children: SubBubble[];
  r: number;        // outer category bubble radius (contains all children)
  x: number;
  y: number;
}

interface Props {
  trends: TrendDTO[];
  directionsByTrendId: Record<string, { direction: TrendDirection; delta: number }>;
  width: number;
  height: number;
}

const MIN_SUB = 26;
const MAX_SUB = 78;
const SUB_PADDING = 4;
const CATEGORY_PADDING = 14;     // breathing room inside a category bubble
const CATEGORY_GAP = 8;          // gap between category bubbles
const SUB_RELAX_ITERS = 240;
const OUTER_RELAX_ITERS = 220;


function colorFor(direction: TrendDirection): { fill: string; stroke: string; text: string } {
  switch (direction) {
    case "up":
      return {
        fill: "rgba(34, 197, 94, 0.18)",
        stroke: "rgba(34, 197, 94, 0.75)",
        text: "rgb(187, 247, 208)",
      };
    case "down":
      return {
        fill: "rgba(239, 68, 68, 0.18)",
        stroke: "rgba(239, 68, 68, 0.75)",
        text: "rgb(254, 202, 202)",
      };
    case "new":
      return {
        fill: "rgba(168, 85, 247, 0.18)",
        stroke: "rgba(168, 85, 247, 0.75)",
        text: "rgb(233, 213, 255)",
      };
    default:
      return {
        fill: "rgba(148, 163, 184, 0.18)",
        stroke: "rgba(148, 163, 184, 0.65)",
        text: "rgb(226, 232, 240)",
      };
  }
}


// Pack sub-bubbles relative to (0, 0). Returns the children with
// their relative positions plus the bounding radius (largest
// distance from origin to any bubble edge + padding).
function packSubBubbles(
  trends: TrendDTO[],
  directions: Props["directionsByTrendId"],
  topNSoftmax: number,
): { children: SubBubble[]; r: number } {
  if (trends.length === 0) return { children: [], r: 0 };

  const children: SubBubble[] = trends.map((t, i) => {
    const ratio = Math.sqrt(t.weight_softmax / Math.max(topNSoftmax, 1e-9));
    const r = MIN_SUB + (MAX_SUB - MIN_SUB) * ratio;
    const angle = (i / Math.max(trends.length, 1)) * 2 * Math.PI;
    const seedR = (MIN_SUB + MAX_SUB) * 0.6;
    const dir = directions[t.id];
    return {
      trend: t,
      direction: dir?.direction ?? "stable",
      delta: dir?.delta ?? 0,
      dx: Math.cos(angle) * seedR,
      dy: Math.sin(angle) * seedR,
      r,
    };
  });

  // Relax: weak pull toward (0,0), pairwise repulsion on overlap.
  for (let it = 0; it < SUB_RELAX_ITERS; it++) {
    for (let i = 0; i < children.length; i++) {
      const a = children[i];
      a.dx += (0 - a.dx) * 0.04;
      a.dy += (0 - a.dy) * 0.04;
      for (let j = i + 1; j < children.length; j++) {
        const b = children[j];
        const dx = b.dx - a.dx;
        const dy = b.dy - a.dy;
        const dist = Math.sqrt(dx * dx + dy * dy) || 0.001;
        const minDist = a.r + b.r + SUB_PADDING;
        if (dist < minDist) {
          const overlap = (minDist - dist) / 2;
          const ux = dx / dist;
          const uy = dy / dist;
          a.dx -= ux * overlap;
          a.dy -= uy * overlap;
          b.dx += ux * overlap;
          b.dy += uy * overlap;
        }
      }
    }
  }

  // Bounding radius = max(distance-from-origin + bubble radius).
  let boundR = 0;
  for (const c of children) {
    boundR = Math.max(boundR, Math.sqrt(c.dx * c.dx + c.dy * c.dy) + c.r);
  }
  return { children, r: boundR + CATEGORY_PADDING };
}


// Pack a set of pre-sized category bubbles inside the SVG.
function packCategories(cats: CategoryBubble[], w: number, h: number): void {
  if (cats.length === 0) return;
  const cx = w / 2;
  const cy = h / 2;

  // Seed positions on a ring sized to the total content area.
  const totalArea = cats.reduce((acc, c) => acc + Math.PI * c.r * c.r, 0);
  const ringR = Math.max(40, Math.sqrt(totalArea / Math.PI) * 0.8);
  for (let i = 0; i < cats.length; i++) {
    const angle = (i / cats.length) * 2 * Math.PI;
    cats[i].x = cx + Math.cos(angle) * ringR;
    cats[i].y = cy + Math.sin(angle) * ringR;
  }

  for (let it = 0; it < OUTER_RELAX_ITERS; it++) {
    for (let i = 0; i < cats.length; i++) {
      const a = cats[i];
      a.x += (cx - a.x) * 0.015;
      a.y += (cy - a.y) * 0.015;
      for (let j = i + 1; j < cats.length; j++) {
        const b = cats[j];
        const dx = b.x - a.x;
        const dy = b.y - a.y;
        const dist = Math.sqrt(dx * dx + dy * dy) || 0.001;
        const minDist = a.r + b.r + CATEGORY_GAP;
        if (dist < minDist) {
          const overlap = (minDist - dist) / 2;
          const ux = dx / dist;
          const uy = dy / dist;
          a.x -= ux * overlap;
          a.y -= uy * overlap;
          b.x += ux * overlap;
          b.y += uy * overlap;
        }
      }
      // keep inside box
      a.x = Math.max(a.r + 2, Math.min(w - a.r - 2, a.x));
      a.y = Math.max(a.r + 2, Math.min(h - a.r - 2, a.y));
    }
  }
}


function pickTopN(trends: TrendDTO[], n: number): TrendDTO[] {
  return [...trends]
    .sort((a, b) => b.weight - a.weight)
    .slice(0, n);
}


function layout(
  trends: TrendDTO[],
  directions: Props["directionsByTrendId"],
  w: number,
  h: number,
): CategoryBubble[] {
  const topN = pickTopN(trends, TOP_N_TRENDS);
  if (topN.length === 0) return [];

  // Renormalise softmax across the visible subset so the largest
  // bubble in the cloud always hits MAX_SUB. Otherwise a heavy
  // hidden trend would shrink the visible ones.
  const sumExp = topN.reduce((acc, t) => acc + Math.exp(t.weight), 0);
  const localSoftmax: Record<string, number> = {};
  for (const t of topN) {
    localSoftmax[t.id] = Math.exp(t.weight) / Math.max(sumExp, 1e-9);
  }
  const localTrends: TrendDTO[] = topN.map((t) => ({
    ...t,
    weight_softmax: localSoftmax[t.id],
  }));
  const localMaxSoftmax = Math.max(...Object.values(localSoftmax), 1e-9);

  // Group by category, preserve insertion order by trend weight (so
  // the dominant category renders first, less critical when force-
  // packed but a nice fallback).
  const byCat = new Map<string, TrendDTO[]>();
  for (const t of localTrends) {
    const cat = t.category || "Other";
    if (!byCat.has(cat)) byCat.set(cat, []);
    byCat.get(cat)!.push(t);
  }

  const cats: CategoryBubble[] = [];
  for (const [cat, ts] of byCat) {
    const sub = packSubBubbles(ts, directions, localMaxSoftmax);
    cats.push({
      category: cat,
      children: sub.children,
      r: sub.r,
      x: 0,
      y: 0,
    });
  }
  packCategories(cats, w, h);
  return cats;
}


export default function TrendsBubbles({
  trends, directionsByTrendId, width, height,
}: Props) {
  const { t, i18n } = useTranslation();
  const [hoverId, setHoverId] = useState<string | null>(null);

  const cats = useMemo(
    () => layout(trends, directionsByTrendId, width, height),
    [trends, directionsByTrendId, width, height],
  );

  // Find the hovered sub-bubble + its parent category (for absolute
  // tooltip positioning).
  const hovered = useMemo(() => {
    if (!hoverId) return null;
    for (const cat of cats) {
      for (const child of cat.children) {
        if (child.trend.id === hoverId) {
          return { cat, child };
        }
      }
    }
    return null;
  }, [hoverId, cats]);

  function truncate(s: string, n: number): string {
    return s.length <= n ? s : s.slice(0, n - 1) + "…";
  }

  function fmtDate(iso: string): string {
    try {
      return new Intl.DateTimeFormat(i18n.language, {
        dateStyle: "medium",
        timeStyle: "short",
      }).format(new Date(iso));
    } catch {
      return iso.slice(0, 16);
    }
  }

  return (
    <div className="relative">
      <svg
        width={width}
        height={height}
        className="block"
        role="img"
        aria-label={t("trends.title")}
      >
        {cats.map((cat) => (
          <g key={cat.category} transform={`translate(${cat.x}, ${cat.y})`}>
            {/* Category outer bubble */}
            <circle
              r={cat.r}
              fill="rgba(148, 163, 184, 0.06)"
              stroke="rgba(148, 163, 184, 0.35)"
              strokeWidth={1}
              strokeDasharray="3 4"
            />
            {/* Category label */}
            <text
              y={-cat.r + 16}
              textAnchor="middle"
              dominantBaseline="middle"
              fill="rgb(148, 163, 184)"
              style={{
                fontSize: "12px",
                fontWeight: 700,
                letterSpacing: "0.05em",
                textTransform: "uppercase",
                pointerEvents: "none",
                userSelect: "none",
              }}
            >
              {cat.category}
            </text>

            {/* Sub-bubbles */}
            {cat.children.map((b) => {
              const c = colorFor(b.direction);
              const isHover = b.trend.id === hoverId;
              const label = truncate(b.trend.name, Math.max(8, Math.floor(b.r / 4)));
              return (
                <g
                  key={b.trend.id}
                  transform={`translate(${b.dx}, ${b.dy})`}
                  onMouseEnter={() => setHoverId(b.trend.id)}
                  onMouseLeave={() =>
                    setHoverId((id) => (id === b.trend.id ? null : id))
                  }
                  style={{ cursor: "default" }}
                >
                  <circle
                    r={b.r}
                    fill={c.fill}
                    stroke={c.stroke}
                    strokeWidth={isHover ? 2.5 : 1.5}
                    style={{ transition: "stroke-width 120ms ease-out" }}
                  />
                  <text
                    textAnchor="middle"
                    dominantBaseline="middle"
                    fill={c.text}
                    style={{
                      fontSize: `${Math.max(10, Math.min(15, b.r / 4.5))}px`,
                      fontWeight: 600,
                      pointerEvents: "none",
                      userSelect: "none",
                    }}
                  >
                    {label}
                  </text>
                </g>
              );
            })}
          </g>
        ))}
      </svg>

      {hovered && (
        <div
          className="pointer-events-none absolute z-10 max-w-xs rounded-lg border border-border bg-surface px-3 py-2 text-xs shadow-lg"
          style={{
            left: Math.min(
              width - 280,
              Math.max(0, hovered.cat.x + hovered.child.dx + hovered.child.r + 8),
            ),
            top: Math.max(0, hovered.cat.y + hovered.child.dy - 20),
          }}
        >
          <div className="flex items-center gap-2">
            <span className="rounded bg-bg px-1.5 py-0.5 text-[10px] uppercase tracking-wide text-muted">
              {hovered.child.trend.category}
            </span>
            <div className="text-sm font-semibold text-text">
              {hovered.child.trend.name}
            </div>
          </div>
          <div className="mt-1 text-muted">{hovered.child.trend.description}</div>
          <div className="mt-2 grid grid-cols-2 gap-1 text-[11px] text-muted">
            <div>
              {t("trends.weight")}:{" "}
              <span className="font-mono text-text">
                {hovered.child.trend.weight.toFixed(3)}
              </span>
            </div>
            <div>
              {t("trends.reinforcements")}:{" "}
              <span className="font-mono text-text">
                {hovered.child.trend.reinforcement_count}
              </span>
            </div>
          </div>
          <div className="mt-1 text-[11px] text-muted">
            {t("trends.lastReinforced")}: {fmtDate(hovered.child.trend.last_reinforced_at)}
          </div>
          {hovered.child.trend.examples.length > 0 && (
            <div className="mt-1 text-[11px]">
              <span className="text-muted">{t("trends.examples")}:</span>
              <ul className="mt-0.5 list-disc pl-4">
                {hovered.child.trend.examples.slice(0, 3).map((e, i) => (
                  <li key={i} className="text-text">{truncate(e, 80)}</li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// Direction helper exported so the page can derive per-trend
// directions from the snapshot history. A trend that appears only
// in the latest snapshot (and not in the first one) is "new"; one
// whose weight rose by > 5% relative is "up"; fell by > 5% is
// "down"; otherwise "stable".
export function computeDirections(
  snapshots: { weights: Record<string, number> }[],
  activeIds: Iterable<string>,
): Record<string, { direction: TrendDirection; delta: number }> {
  const ids = Array.from(activeIds);
  if (snapshots.length === 0) {
    return Object.fromEntries(
      ids.map((id) => [id, { direction: "stable" as TrendDirection, delta: 0 }]),
    );
  }
  const first = snapshots[0].weights;
  const last = snapshots[snapshots.length - 1].weights;
  const out: Record<string, { direction: TrendDirection; delta: number }> = {};
  for (const id of ids) {
    const w0 = first[id];
    const w1 = last[id] ?? 0;
    if (w0 == null) {
      out[id] = { direction: "new", delta: w1 };
      continue;
    }
    const delta = w1 - w0;
    const rel = w0 > 1e-6 ? delta / w0 : 0;
    if (rel > 0.05) out[id] = { direction: "up", delta };
    else if (rel < -0.05) out[id] = { direction: "down", delta };
    else out[id] = { direction: "stable", delta };
  }
  return out;
}

// Tiny resize observer hook used by TrendsView to size the bubble svg.
export function useElementSize<T extends HTMLElement>(): [
  React.RefObject<T>,
  { width: number; height: number },
] {
  const ref = useRef<T>(null);
  const [size, setSize] = useState({ width: 800, height: 500 });
  useEffect(() => {
    if (!ref.current) return;
    const el = ref.current;
    const ro = new ResizeObserver((entries) => {
      for (const entry of entries) {
        const { width, height } = entry.contentRect;
        setSize({ width: Math.round(width), height: Math.round(height) });
      }
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  return [ref, size];
}
