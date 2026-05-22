// Top-level Trends tab. Trends are grouped by category, each
// category gets its own bubble cloud with the cloud's footprint
// scaled to how much weight that category currently holds.
//
// Bubble size and direction are computed globally (so a 0.3-weight
// trend looks the same in any category) — only the cloud arrangement
// is per-category.

import { useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Loader2, RefreshCw, TrendingUp } from "lucide-react";

import {
  api,
  type TrendDTO,
  type TrendsHistoryResponse,
  type TrendsListResponse,
} from "@/lib/api";
import TrendsBubbles, {
  computeDirections,
  type TrendDirection,
} from "./TrendsBubbles";

type WindowHours = 24 | 72 | 168;

interface CategoryGroup {
  category: string;
  trends: TrendDTO[];
  totalWeight: number;
}

// Order categories by total active weight (most active first), but
// pin "Other" to the end regardless.
function groupByCategory(trends: TrendDTO[]): CategoryGroup[] {
  const by: Record<string, TrendDTO[]> = {};
  for (const t of trends) {
    const cat = t.category || "Other";
    (by[cat] = by[cat] ?? []).push(t);
  }
  const groups: CategoryGroup[] = Object.entries(by).map(([category, ts]) => ({
    category,
    trends: ts.sort((a, b) => b.weight - a.weight),
    totalWeight: ts.reduce((acc, t) => acc + t.weight, 0),
  }));
  groups.sort((a, b) => {
    if (a.category === "Other") return 1;
    if (b.category === "Other") return -1;
    return b.totalWeight - a.totalWeight;
  });
  return groups;
}

function cloudHeightFor(count: number): number {
  // Just enough vertical space for the bubbles to lay out without
  // crowding. Scales with count but caps at ~520px so big categories
  // don't dominate the page.
  const min = 240;
  const max = 520;
  return Math.max(min, Math.min(max, 140 + count * 32));
}

export default function TrendsView() {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const [hours, setHours] = useState<WindowHours>(24);

  const trendsQ = useQuery({
    queryKey: ["trends"],
    queryFn: () => api.get<TrendsListResponse>("/api/trends"),
    refetchInterval: 30_000,
    staleTime: 15_000,
  });

  const historyQ = useQuery({
    queryKey: ["trends-history", hours],
    queryFn: () =>
      api.get<TrendsHistoryResponse>(`/api/trends/history?hours=${hours}`),
    refetchInterval: 30_000,
    staleTime: 15_000,
  });

  const processMut = useMutation({
    mutationFn: () => api.post<{ processed: number }>("/api/trends/process"),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["trends"] });
      qc.invalidateQueries({ queryKey: ["trends-history"] });
    },
  });

  const trends = trendsQ.data?.trends ?? [];
  const snapshots = historyQ.data?.snapshots ?? [];

  const directions = useMemo<
    Record<string, { direction: TrendDirection; delta: number }>
  >(
    () => computeDirections(snapshots, trends.map((t) => t.id)),
    [snapshots, trends],
  );

  // Compute a global softmax-relative scale so bubbles are comparable
  // across categories. We pass each per-category subset to
  // TrendsBubbles but the underlying weight_softmax is already over
  // the full active set.
  const groups = useMemo(() => groupByCategory(trends), [trends]);

  const disabledFeature =
    processMut.isError &&
    (processMut.error as { status?: number } | undefined)?.status === 409;

  return (
    <div className="flex h-full flex-col overflow-hidden">
      <header className="flex items-start justify-between gap-3 border-b border-border bg-surface px-4 py-3 md:px-6 md:py-4">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <TrendingUp className="h-5 w-5 text-accent" />
            <h1 className="truncate text-lg font-semibold">{t("trends.title")}</h1>
          </div>
        </div>

        <div className="flex shrink-0 items-center gap-2">
          {/* Window selector */}
          <div className="hidden items-center gap-1 rounded-lg border border-border bg-bg p-0.5 md:flex">
            {([24, 72, 168] as WindowHours[]).map((h) => (
              <button
                key={h}
                type="button"
                onClick={() => setHours(h)}
                className={`rounded-md px-2 py-1 text-xs font-medium transition ${
                  hours === h
                    ? "bg-accent text-white"
                    : "text-muted hover:text-text"
                }`}
              >
                {t(`trends.h${h}`)}
              </button>
            ))}
          </div>

          <button
            type="button"
            onClick={() => processMut.mutate()}
            disabled={processMut.isPending}
            className="flex items-center gap-1.5 rounded-lg bg-accent px-3 py-2 text-xs font-medium text-white hover:bg-accent/90 disabled:opacity-60"
          >
            {processMut.isPending ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <RefreshCw className="h-4 w-4" />
            )}
            <span>
              {processMut.isPending ? t("trends.processing") : t("trends.process")}
            </span>
          </button>
        </div>
      </header>

      {/* Mobile window selector */}
      <div className="flex items-center gap-1 border-b border-border bg-bg/40 px-4 py-2 md:hidden">
        <span className="mr-2 text-[11px] uppercase tracking-wide text-muted">
          {t("trends.rangeHours")}
        </span>
        {([24, 72, 168] as WindowHours[]).map((h) => (
          <button
            key={h}
            type="button"
            onClick={() => setHours(h)}
            className={`rounded-md px-2 py-1 text-xs font-medium transition ${
              hours === h ? "bg-accent text-white" : "text-muted"
            }`}
          >
            {t(`trends.h${h}`)}
          </button>
        ))}
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto">
        {trendsQ.isLoading && (
          <div className="flex h-full items-center justify-center gap-2 text-sm text-muted">
            <Loader2 className="h-4 w-4 animate-spin" />
          </div>
        )}

        {trendsQ.isError && (
          <div className="m-4 rounded border border-red-500/40 bg-red-500/10 px-3 py-2 text-sm text-red-400">
            {t("trends.loadError")}
          </div>
        )}

        {disabledFeature && (
          <div className="m-4 rounded border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-sm text-amber-300">
            {t("trends.disabled")}
          </div>
        )}

        {processMut.isSuccess && processMut.data && (
          <div className="m-4 rounded border border-emerald-500/30 bg-emerald-500/10 px-3 py-2 text-xs text-emerald-300">
            {t("trends.processed", { count: processMut.data.processed })}
          </div>
        )}

        {trendsQ.data && trends.length === 0 && (
          <div className="flex h-full flex-col items-center justify-center gap-2 px-6 text-center text-sm text-muted">
            <TrendingUp className="h-8 w-8 opacity-60" />
            <p className="max-w-md">{t("trends.empty")}</p>
          </div>
        )}

        {groups.length > 0 && (
          <div className="flex flex-col gap-6 px-4 py-4 md:px-6 md:py-6">
            {groups.map((g) => (
              <section key={g.category} className="rounded-lg border border-border bg-surface/50">
                <header className="flex items-baseline justify-between border-b border-border px-4 py-2">
                  <h2 className="text-sm font-semibold text-text">{g.category}</h2>
                  <span className="text-[11px] text-muted">
                    {g.trends.length}
                  </span>
                </header>
                <CategoryCloud
                  category={g.category}
                  trends={g.trends}
                  directions={directions}
                />
              </section>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}


function CategoryCloud({
  trends,
  directions,
}: {
  category: string;
  trends: TrendDTO[];
  directions: Record<string, { direction: TrendDirection; delta: number }>;
}) {
  const [ref, size] = useResizeObserver<HTMLDivElement>();
  const height = cloudHeightFor(trends.length);
  return (
    <div ref={ref} className="w-full" style={{ height }}>
      {size.width > 0 && (
        <TrendsBubbles
          trends={trends}
          directionsByTrendId={directions}
          width={size.width}
          height={height}
        />
      )}
    </div>
  );
}


// Local lightweight resize observer — measures the parent before
// rendering the bubble cloud so we know the SVG width ahead of time.
function useResizeObserver<T extends HTMLElement>(): [
  React.RefObject<T>,
  { width: number; height: number },
] {
  const ref = useRef<T>(null);
  const [size, setSize] = useState({ width: 0, height: 0 });
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
