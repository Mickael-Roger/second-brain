// Top-level Trends tab. Shows the top-N trends globally as a single
// nested bubble cloud: one big bubble per category, containing the
// trends that belong to it. TrendsBubbles handles the layout +
// filtering — this file is just header + chrome + plumbing.

import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Loader2, RefreshCw, TrendingUp } from "lucide-react";

import {
  api,
  type TrendsHistoryResponse,
  type TrendsListResponse,
} from "@/lib/api";
import TrendsBubbles, {
  computeDirections,
  useElementSize,
} from "./TrendsBubbles";

type WindowHours = 24 | 72 | 168;

export default function TrendsView() {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const [hours, setHours] = useState<WindowHours>(24);
  const [stageRef, stageSize] = useElementSize<HTMLDivElement>();

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

  const directions = useMemo(
    () => computeDirections(snapshots, trends.map((t) => t.id)),
    [snapshots, trends],
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
        </div>

        <div className="flex shrink-0 items-center gap-2">
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

      <div className="min-h-0 flex-1 overflow-hidden">
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

        {trends.length > 0 && (
          <div ref={stageRef} className="h-full w-full">
            <TrendsBubbles
              trends={trends}
              directionsByTrendId={directions}
              width={stageSize.width || 800}
              height={stageSize.height || 500}
            />
          </div>
        )}
      </div>
    </div>
  );
}
