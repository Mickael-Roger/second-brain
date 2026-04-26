// News & Events view. Shows event bubbles for the selected period, with
// hover tooltips listing the underlying articles. Manual fetch + cluster
// triggers sit in the header — the cron schedules drive the regular flow.

import { useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Newspaper, Play, RefreshCw } from "lucide-react";

import { api, type NewsEventBubble } from "@/lib/api";

import EventBubble from "./EventBubble";
import { packCircles } from "./pack";

type Period = "today" | "7d" | "30d" | "custom";

export default function NewsView() {
  const { t } = useTranslation();
  const qc = useQueryClient();

  const [period, setPeriod] = useState<Period>("7d");
  const [customFrom, setCustomFrom] = useState<string>(() => isoDaysAgo(7));
  const [customTo, setCustomTo] = useState<string>(() => isoDaysAgo(0));
  const [hoveredId, setHoveredId] = useState<string | null>(null);

  const queryKey = useMemo(() => {
    if (period === "custom") return ["news-events", "custom", customFrom, customTo];
    return ["news-events", period];
  }, [period, customFrom, customTo]);

  const events = useQuery<NewsEventBubble[]>({
    queryKey,
    queryFn: () => {
      if (period === "custom") {
        const qs = new URLSearchParams({
          period: "custom",
          from: customFrom,
          to: customTo,
        });
        return api.get<NewsEventBubble[]>(`/api/news/events?${qs.toString()}`);
      }
      return api.get<NewsEventBubble[]>(`/api/news/events?period=${period}`);
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
    onSuccess: () => qc.invalidateQueries({ queryKey: ["news-events"] }),
  });

  const clusterNow = useMutation({
    mutationFn: () => api.post<{ started: boolean }>("/api/news/cluster"),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["news-events"] }),
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
        {events.isLoading ? (
          <p className="px-4 py-4 text-sm text-muted">{t("news.loading")}</p>
        ) : !events.data || events.data.length === 0 ? (
          <EmptyState />
        ) : (
          <BubbleCanvas
            events={events.data}
            hoveredId={hoveredId}
            onHover={setHoveredId}
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
  events: NewsEventBubble[];
  hoveredId: string | null;
  onHover: (id: string | null) => void;
}

function BubbleCanvas({ events, hoveredId, onHover }: CanvasProps) {
  const ref = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState({ w: 800, h: 600 });

  // Track the canvas size so the pack layout fits.
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

  // Map article counts to bubble radii. sqrt scaling keeps the area
  // proportional to the count rather than the radius itself, which is
  // what people instinctively read.
  const packed = useMemo(() => {
    if (events.length === 0) return [];
    const maxCount = Math.max(...events.map((e) => e.article_count));
    const minR = 28;
    const maxR = Math.min(size.w, size.h) / 5;
    const radii = events.map((e) => {
      const t = Math.sqrt(e.article_count / maxCount);
      return minR + t * (maxR - minR);
    });
    return packCircles(radii, size.w, size.h);
  }, [events, size]);

  return (
    <div ref={ref} className="relative h-full w-full overflow-hidden">
      {events.map((e, i) => {
        const c = packed[i];
        if (!c) return null;
        return (
          <EventBubble
            key={e.id}
            bubble={e}
            x={c.x}
            y={c.y}
            r={c.r}
            hovered={hoveredId === e.id}
            onHoverStart={() => onHover(e.id)}
            onHoverEnd={() => onHover(hoveredId === e.id ? null : hoveredId)}
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
