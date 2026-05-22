// Bubble layout for the Trends view.
//
// We use a small force-driven packing (~10ms for 50 bubbles) instead of
// pulling in D3 — the layout is a circle-packing problem with no axes
// or interactions beyond hover, so the deps would be wasted.
//
// Bubble color encodes the short-term direction: green for rising,
// red for falling, neutral for stable. The direction is derived from
// the history window: weight at the start of the window vs. now.
//
// Tooltip shows the trend's description, the most recent example
// titles, and key counters.

import { useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import type { TrendDTO } from "@/lib/api";

export type TrendDirection = "up" | "down" | "stable" | "new";

interface Bubble {
  trend: TrendDTO;
  direction: TrendDirection;
  delta: number;            // weight change over the window
  x: number;
  y: number;
  r: number;
}

interface Props {
  trends: TrendDTO[];
  directionsByTrendId: Record<string, { direction: TrendDirection; delta: number }>;
  width: number;
  height: number;
}

const MIN_RADIUS = 24;
const MAX_RADIUS = 90;

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

// Light force-pack: place bubbles tangent to each other via a fixed
// number of relaxation passes. Anchor toward the centre with a weak
// spring so the cloud stays inside the SVG.
function layout(
  trends: TrendDTO[],
  directions: Props["directionsByTrendId"],
  w: number,
  h: number,
): Bubble[] {
  if (trends.length === 0) return [];
  const cx = w / 2;
  const cy = h / 2;
  const maxSoftmax = Math.max(...trends.map((t) => t.weight_softmax), 1e-9);
  const items: Bubble[] = trends.map((t, idx) => {
    const ratio = Math.sqrt(t.weight_softmax / maxSoftmax);
    const r = MIN_RADIUS + (MAX_RADIUS - MIN_RADIUS) * ratio;
    const angle = (idx / trends.length) * 2 * Math.PI;
    const seedR = Math.min(w, h) * 0.25;
    const dir = directions[t.id];
    return {
      trend: t,
      direction: dir?.direction ?? "stable",
      delta: dir?.delta ?? 0,
      x: cx + Math.cos(angle) * seedR,
      y: cy + Math.sin(angle) * seedR,
      r,
    };
  });

  const iterations = 220;
  for (let it = 0; it < iterations; it++) {
    for (let i = 0; i < items.length; i++) {
      const a = items[i];
      // weak pull toward centre
      a.x += (cx - a.x) * 0.012;
      a.y += (cy - a.y) * 0.012;
      for (let j = i + 1; j < items.length; j++) {
        const b = items[j];
        const dx = b.x - a.x;
        const dy = b.y - a.y;
        const dist = Math.sqrt(dx * dx + dy * dy) || 0.001;
        const minDist = a.r + b.r + 4;
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
  return items;
}

export default function TrendsBubbles({
  trends, directionsByTrendId, width, height,
}: Props) {
  const { t, i18n } = useTranslation();
  const [hoverId, setHoverId] = useState<string | null>(null);

  const bubbles = useMemo(
    () => layout(trends, directionsByTrendId, width, height),
    [trends, directionsByTrendId, width, height],
  );

  const hovered = bubbles.find((b) => b.trend.id === hoverId) ?? null;

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
        {bubbles.map((b) => {
          const c = colorFor(b.direction);
          const isHover = b.trend.id === hoverId;
          const label = truncate(b.trend.name, Math.max(8, Math.floor(b.r / 4)));
          return (
            <g
              key={b.trend.id}
              transform={`translate(${b.x}, ${b.y})`}
              onMouseEnter={() => setHoverId(b.trend.id)}
              onMouseLeave={() => setHoverId((id) => (id === b.trend.id ? null : id))}
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
                  fontSize: `${Math.max(10, Math.min(15, b.r / 4))}px`,
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
      </svg>

      {hovered && (
        <div
          className="pointer-events-none absolute z-10 max-w-xs rounded-lg border border-border bg-surface px-3 py-2 text-xs shadow-lg"
          style={{
            left: Math.min(width - 280, Math.max(0, hovered.x + hovered.r + 8)),
            top: Math.max(0, hovered.y - 20),
          }}
        >
          <div className="text-sm font-semibold text-text">{hovered.trend.name}</div>
          <div className="mt-1 text-muted">{hovered.trend.description}</div>
          <div className="mt-2 grid grid-cols-2 gap-1 text-[11px] text-muted">
            <div>
              {t("trends.weight")}:{" "}
              <span className="font-mono text-text">{hovered.trend.weight.toFixed(3)}</span>
            </div>
            <div>
              {t("trends.reinforcements")}:{" "}
              <span className="font-mono text-text">{hovered.trend.reinforcement_count}</span>
            </div>
          </div>
          <div className="mt-1 text-[11px] text-muted">
            {t("trends.lastReinforced")}: {fmtDate(hovered.trend.last_reinforced_at)}
          </div>
          <div className="mt-1 text-[11px]">
            <span className="text-muted">{hovered.trend.examples.length > 0 ? `${t("trends.examples")}:` : ""}</span>
            <ul className="mt-0.5 list-disc pl-4">
              {hovered.trend.examples.slice(0, 3).map((e, i) => (
                <li key={i} className="text-text">{truncate(e, 80)}</li>
              ))}
            </ul>
          </div>
        </div>
      )}
    </div>
  );
}

// Helper exported so the page can derive directions from the history
// payload. Returns { direction, delta } per trend id. A trend that
// appears only in the latest snapshot (and not in the first one) is
// "new"; one whose weight rose by > 5% relative is "up"; fell by > 5%
// is "down"; otherwise "stable".
export function computeDirections(
  snapshots: { weights: Record<string, number> }[],
  activeIds: Iterable<string>,
): Record<string, { direction: TrendDirection; delta: number }> {
  const ids = Array.from(activeIds);
  if (snapshots.length === 0) {
    return Object.fromEntries(ids.map((id) => [id, { direction: "stable" as TrendDirection, delta: 0 }]));
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
