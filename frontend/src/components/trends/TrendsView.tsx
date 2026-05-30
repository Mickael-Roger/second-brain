import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Loader2, RefreshCw, TrendingUp } from "lucide-react";

import {
  api,
  type TrendMomentDTO,
  type TrendMomentsResponse,
} from "@/lib/api";

type WindowHours = 24 | 72 | 168;

function directionClass(direction: string): string {
  switch (direction) {
    case "up":
      return "border-emerald-400/60 bg-emerald-500/15 text-emerald-100";
    case "down":
      return "border-red-400/50 bg-red-500/10 text-red-100";
    case "new":
      return "border-violet-400/60 bg-violet-500/15 text-violet-100";
    default:
      return "border-slate-400/40 bg-slate-500/10 text-slate-100";
  }
}

function fmtDate(iso: string, language: string): string {
  try {
    return new Intl.DateTimeFormat(language, {
      dateStyle: "medium",
      timeStyle: "short",
    }).format(new Date(iso));
  } catch {
    return iso.slice(0, 16);
  }
}

function MomentBubble({ moment, maxScore }: { moment: TrendMomentDTO; maxScore: number }) {
  const { t, i18n } = useTranslation();
  const ratio = Math.sqrt(moment.virality_score / Math.max(maxScore, 0.001));
  const size = Math.round(190 + ratio * 190);
  return (
    <article
      className={`flex flex-col rounded-full border p-6 shadow-lg ${directionClass(moment.direction)}`}
      style={{ width: size, minHeight: size }}
    >
      <div className="flex items-center justify-between gap-2">
        <span className="rounded-full bg-black/20 px-2 py-1 text-[10px] font-semibold uppercase tracking-wide">
          {moment.category}
        </span>
        <span className="font-mono text-xs">{moment.virality_score.toFixed(2)}</span>
      </div>
      <h2 className="mt-4 text-center text-base font-semibold leading-tight md:text-lg">
        {moment.title}
      </h2>
      <p className="mt-2 line-clamp-3 text-center text-xs opacity-80">
        {moment.description}
      </p>
      <div className="mt-3 text-center text-[11px] opacity-80">
        {moment.mention_count} mentions · {moment.source_count} sources · {moment.direction}
      </div>
      <div className="mt-3 min-h-0 flex-1 overflow-hidden text-[11px] opacity-90">
        {moment.articles.slice(0, 3).map((article) => (
          <div key={`${article.article_id}-${article.evidence}`} className="mt-1 line-clamp-2">
            <span className="font-semibold">{article.feed_title || article.feed_group || "Source"}</span>
            {" · "}{article.title}
          </div>
        ))}
      </div>
      <div className="mt-2 text-center text-[10px] opacity-60">
        {t("trends.lastReinforced")}: {fmtDate(moment.last_seen_at, i18n.language)}
      </div>
    </article>
  );
}

export default function TrendsView() {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const [hours, setHours] = useState<WindowHours>(24);

  const momentsQ = useQuery({
    queryKey: ["trend-moments", hours],
    queryFn: () => api.get<TrendMomentsResponse>(`/api/trends/moments?hours=${hours}`),
    refetchInterval: 30_000,
    staleTime: 15_000,
  });

  const processMut = useMutation({
    mutationFn: () => api.post<{ processed: number }>("/api/trends/process"),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["trend-moments"] });
    },
  });

  const moments = momentsQ.data?.moments ?? [];
  const maxScore = useMemo(
    () => Math.max(...moments.map((m) => m.virality_score), 0.001),
    [moments],
  );
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
          <p className="mt-1 text-xs text-muted">
            Recent event clusters sized by virality, after the synthesis delay.
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <div className="hidden items-center gap-1 rounded-lg border border-border bg-bg p-0.5 md:flex">
            {([24, 72, 168] as WindowHours[]).map((h) => (
              <button
                key={h}
                type="button"
                onClick={() => setHours(h)}
                className={`rounded-md px-2 py-1 text-xs font-medium transition ${
                  hours === h ? "bg-accent text-white" : "text-muted hover:text-text"
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
            <span>{processMut.isPending ? t("trends.processing") : t("trends.process")}</span>
          </button>
        </div>
      </header>

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

      <div className="min-h-0 flex-1 overflow-auto p-4 md:p-8">
        {momentsQ.isLoading && (
          <div className="flex h-full items-center justify-center gap-2 text-sm text-muted">
            <Loader2 className="h-4 w-4 animate-spin" />
          </div>
        )}
        {momentsQ.isError && (
          <div className="rounded border border-red-500/40 bg-red-500/10 px-3 py-2 text-sm text-red-400">
            {t("trends.loadError")}
          </div>
        )}
        {disabledFeature && (
          <div className="mb-4 rounded border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-sm text-amber-300">
            {t("trends.disabled")}
          </div>
        )}
        {processMut.isSuccess && processMut.data && (
          <div className="mb-4 rounded border border-emerald-500/30 bg-emerald-500/10 px-3 py-2 text-xs text-emerald-300">
            {t("trends.processed", { count: processMut.data.processed })}
          </div>
        )}
        {momentsQ.data && moments.length === 0 && (
          <div className="flex h-full flex-col items-center justify-center gap-2 px-6 text-center text-sm text-muted">
            <TrendingUp className="h-8 w-8 opacity-60" />
            <p className="max-w-md">{t("trends.empty")}</p>
          </div>
        )}
        {moments.length > 0 && (
          <div className="flex flex-wrap items-center justify-center gap-5">
            {moments.map((moment) => (
              <MomentBubble key={moment.id} moment={moment} maxScore={maxScore} />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
