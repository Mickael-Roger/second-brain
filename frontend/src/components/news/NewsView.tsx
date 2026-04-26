// News & Events view. Shows hot-topic hashtag bubbles for the selected
// period, with hover tooltips listing the underlying articles. Manual
// fetch + tagger triggers sit in the header — the cron schedules drive
// the regular flow.

import { useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Newspaper, Play, RefreshCw } from "lucide-react";

import { api, type NewsTrend } from "@/lib/api";

import TrendBubble from "./EventBubble";
import { packCircles } from "./pack";

type Period = "today" | "7d" | "30d" | "custom";

export default function NewsView() {
  const { t } = useTranslation();
  const qc = useQueryClient();

  const [period, setPeriod] = useState<Period>("7d");
  const [customFrom, setCustomFrom] = useState<string>(() => isoDaysAgo(7));
  const [customTo, setCustomTo] = useState<string>(() => isoDaysAgo(0));
  const [hoveredTag, setHoveredTag] = useState<string | null>(null);

  const queryKey = useMemo(() => {
    if (period === "custom") return ["news-trends", "custom", customFrom, customTo];
    return ["news-trends", period];
  }, [period, customFrom, customTo]);

  const trends = useQuery<NewsTrend[]>({
    queryKey,
    queryFn: () => {
      const qs = new URLSearchParams({ period });
      if (period === "custom") {
        qs.set("from", customFrom);
        qs.set("to", customTo);
      }
      return api.get<NewsTrend[]>(`/api/news/trends?${qs.toString()}`);
    },
  });

  const fetchNow = useMutation({
    mutationFn: () => {
      // Scope the manual fetch to the currently-selected period — the
      // UI's "fetch now" should only pull articles for what the user
      // is looking at, not everything FreshRSS has ever seen.
      const qs = new URLSearchParams({ period });
      if (period === "custom") {
        qs.set("from", customFrom);
        qs.set("to", customTo);
      }
      return api.post<{ started: boolean }>(
        `/api/news/fetch?${qs.toString()}`,
      );
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ["news-trends"] }),
  });

  const clusterNow = useMutation({
    // The endpoint is still named /cluster for backwards compatibility,
    // but it now drives per-article hashtag extraction (the tagger).
    mutationFn: () => api.post<{ started: boolean }>("/api/news/cluster"),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["news-trends"] }),
  });

  return (
    <div className="flex h-full flex-col">
      <header className="flex flex-wrap items-center gap-3 border-b border-border bg-surface px-4 py-3">
        <Newspaper className="h-5 w-5 text-accent" />
        <h1 className="flex-1 text-lg font-semibold">{t("news.title")}</h1>

        <PeriodSelector
          period={period}
          customFrom={customFrom}
          customTo={customTo}
          onPeriod={setPeriod}
          onFrom={setCustomFrom}
          onTo={setCustomTo}
        />

        <button
          type="button"
          onClick={() => fetchNow.mutate()}
          disabled={fetchNow.isPending}
          className="flex items-center gap-1 rounded-lg border border-border bg-bg px-3 py-1.5 text-sm hover:border-accent disabled:opacity-50"
        >
          <Play className="h-3.5 w-3.5" />
          {fetchNow.isPending ? t("news.fetching") : t("news.fetch")}
        </button>

        <button
          type="button"
          onClick={() => clusterNow.mutate()}
          disabled={clusterNow.isPending}
          className="flex items-center gap-1 rounded-lg border border-border bg-bg px-3 py-1.5 text-sm hover:border-accent disabled:opacity-50"
        >
          <RefreshCw className="h-3.5 w-3.5" />
          {clusterNow.isPending ? t("news.clustering") : t("news.cluster")}
        </button>
      </header>

      {(fetchNow.isSuccess || clusterNow.isSuccess) && (
        <div className="border-b border-border bg-accent/10 px-4 py-2 text-xs text-accent">
          {fetchNow.isSuccess && t("news.fetchTriggered")}
          {fetchNow.isSuccess && clusterNow.isSuccess && " "}
          {clusterNow.isSuccess && t("news.clusterTriggered")}
        </div>
      )}

      <div className="flex-1 overflow-hidden">
        {trends.isLoading ? (
          <p className="px-4 py-4 text-sm text-muted">{t("news.loading")}</p>
        ) : !trends.data || trends.data.length === 0 ? (
          <EmptyState />
        ) : (
          <BubbleCanvas
            trends={trends.data}
            period={period}
            customFrom={customFrom}
            customTo={customTo}
            hoveredTag={hoveredTag}
            onHover={setHoveredTag}
          />
        )}
      </div>
    </div>
  );
}

interface PeriodProps {
  period: Period;
  customFrom: string;
  customTo: string;
  onPeriod: (p: Period) => void;
  onFrom: (s: string) => void;
  onTo: (s: string) => void;
}

function PeriodSelector({
  period,
  customFrom,
  customTo,
  onPeriod,
  onFrom,
  onTo,
}: PeriodProps) {
  const { t } = useTranslation();
  const opts: { id: Period; label: string }[] = [
    { id: "today", label: t("news.periodToday") },
    { id: "7d", label: t("news.period7d") },
    { id: "30d", label: t("news.period30d") },
    { id: "custom", label: t("news.periodCustom") },
  ];
  return (
    <div className="flex flex-wrap items-center gap-2 text-xs">
      <span className="text-muted">{t("news.periodLabel")}:</span>
      <div className="flex overflow-hidden rounded-lg border border-border">
        {opts.map((o) => (
          <button
            key={o.id}
            onClick={() => onPeriod(o.id)}
            className={`px-2 py-1 ${
              period === o.id
                ? "bg-accent text-bg"
                : "bg-bg text-muted hover:text-text"
            }`}
          >
            {o.label}
          </button>
        ))}
      </div>
      {period === "custom" && (
        <>
          <input
            type="date"
            value={customFrom}
            onChange={(e) => onFrom(e.target.value)}
            className="rounded-lg border border-border bg-bg px-2 py-1 text-text"
          />
          <span className="text-muted">→</span>
          <input
            type="date"
            value={customTo}
            onChange={(e) => onTo(e.target.value)}
            className="rounded-lg border border-border bg-bg px-2 py-1 text-text"
          />
        </>
      )}
    </div>
  );
}

interface CanvasProps {
  trends: NewsTrend[];
  period: Period;
  customFrom: string;
  customTo: string;
  hoveredTag: string | null;
  onHover: (tag: string | null) => void;
}

function BubbleCanvas({
  trends,
  period,
  customFrom,
  customTo,
  hoveredTag,
  onHover,
}: CanvasProps) {
  const ref = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState({ w: 800, h: 600 });

  useEffect(() => {
    if (!ref.current) return;
    const obs = new ResizeObserver((entries) => {
      for (const e of entries) {
        const cr = e.contentRect;
        setSize({ w: Math.max(200, cr.width), h: Math.max(200, cr.height) });
      }
    });
    obs.observe(ref.current);
    return () => obs.disconnect();
  }, []);

  // sqrt scaling keeps bubble AREA proportional to article count —
  // that's what readers instinctively decode.
  const packed = useMemo(() => {
    if (trends.length === 0) return [];
    const maxCount = Math.max(...trends.map((trend) => trend.count));
    const minR = 28;
    const maxR = Math.min(size.w, size.h) / 5;
    const radii = trends.map((trend) => {
      const ratio = Math.sqrt(trend.count / maxCount);
      return minR + ratio * (maxR - minR);
    });
    return packCircles(radii, size.w, size.h);
  }, [trends, size]);

  return (
    <div ref={ref} className="relative h-full w-full overflow-hidden">
      {trends.map((trend, i) => {
        const c = packed[i];
        if (!c) return null;
        return (
          <TrendBubble
            key={trend.tag}
            tag={trend.tag}
            count={trend.count}
            period={period}
            customFrom={customFrom}
            customTo={customTo}
            x={c.x}
            y={c.y}
            r={c.r}
            hovered={hoveredTag === trend.tag}
            onHoverStart={() => onHover(trend.tag)}
            onHoverEnd={() => onHover(hoveredTag === trend.tag ? null : hoveredTag)}
          />
        );
      })}
    </div>
  );
}

function EmptyState() {
  const { t } = useTranslation();
  return (
    <div className="flex h-full items-center justify-center px-6 text-center">
      <div className="max-w-md space-y-3">
        <Newspaper className="mx-auto h-8 w-8 text-accent" />
        <p className="text-sm text-muted">{t("news.empty")}</p>
      </div>
    </div>
  );
}

function isoDaysAgo(days: number): string {
  const d = new Date();
  d.setDate(d.getDate() - days);
  return d.toISOString().slice(0, 10);
}
