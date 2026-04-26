// News tab shell. Owns the period selector + fetch/cluster triggers
// (shared by both sub-tabs) and switches between the Trends bubble
// dashboard and the per-feed article browser.

import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Newspaper, Play, RefreshCw } from "lucide-react";

import { api } from "@/lib/api";

import ArticlesTab from "./ArticlesTab";
import TrendsTab from "./TrendsTab";

type Period = "today" | "7d" | "30d" | "custom";
type SubTab = "trends" | "articles";

export default function NewsView() {
  const { t } = useTranslation();
  const qc = useQueryClient();

  const [subTab, setSubTab] = useState<SubTab>("trends");
  const [period, setPeriod] = useState<Period>("7d");
  const [customFrom, setCustomFrom] = useState<string>(() => isoDaysAgo(7));
  const [customTo, setCustomTo] = useState<string>(() => isoDaysAgo(0));

  const fetchNow = useMutation({
    mutationFn: () => {
      // Scope the manual fetch to the currently-selected period.
      const qs = new URLSearchParams({ period });
      if (period === "custom") {
        qs.set("from", customFrom);
        qs.set("to", customTo);
      }
      return api.post<{ started: boolean }>(
        `/api/news/fetch?${qs.toString()}`,
      );
    },
    onSuccess: () => {
      // Invalidate every news-* query so both tabs refresh.
      qc.invalidateQueries({ queryKey: ["news-trends"] });
      qc.invalidateQueries({ queryKey: ["news-feeds"] });
      qc.invalidateQueries({ queryKey: ["news-articles"] });
    },
  });

  const clusterNow = useMutation({
    mutationFn: () => api.post<{ started: boolean }>("/api/news/cluster"),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["news-trends"] });
      qc.invalidateQueries({ queryKey: ["news-articles"] });
    },
  });

  return (
    <div className="flex h-full flex-col">
      <header className="flex flex-wrap items-center gap-3 border-b border-border bg-surface px-4 py-3">
        <Newspaper className="h-5 w-5 text-accent" />
        <h1 className="text-lg font-semibold">{t("news.title")}</h1>

        <SubTabNav active={subTab} onSelect={setSubTab} />

        <div className="ml-auto flex flex-wrap items-center gap-3">
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
        </div>
      </header>

      {(fetchNow.isSuccess || clusterNow.isSuccess) && (
        <div className="border-b border-border bg-accent/10 px-4 py-2 text-xs text-accent">
          {fetchNow.isSuccess && t("news.fetchTriggered")}
          {fetchNow.isSuccess && clusterNow.isSuccess && " "}
          {clusterNow.isSuccess && t("news.clusterTriggered")}
        </div>
      )}

      <div className="flex-1 overflow-hidden">
        {subTab === "trends" ? (
          <TrendsTab
            period={period}
            customFrom={customFrom}
            customTo={customTo}
          />
        ) : (
          <ArticlesTab
            period={period}
            customFrom={customFrom}
            customTo={customTo}
          />
        )}
      </div>
    </div>
  );
}

interface SubTabNavProps {
  active: SubTab;
  onSelect: (tab: SubTab) => void;
}

function SubTabNav({ active, onSelect }: SubTabNavProps) {
  const { t } = useTranslation();
  const items: { id: SubTab; label: string }[] = [
    { id: "trends", label: t("news.tabTrends") },
    { id: "articles", label: t("news.tabArticles") },
  ];
  return (
    <div className="flex overflow-hidden rounded-lg border border-border">
      {items.map((it) => (
        <button
          key={it.id}
          type="button"
          onClick={() => onSelect(it.id)}
          className={`px-3 py-1.5 text-sm transition ${
            active === it.id
              ? "bg-accent text-bg"
              : "bg-bg text-muted hover:text-text"
          }`}
        >
          {it.label}
        </button>
      ))}
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

function isoDaysAgo(days: number): string {
  const d = new Date();
  d.setDate(d.getDate() - days);
  return d.toISOString().slice(0, 10);
}
