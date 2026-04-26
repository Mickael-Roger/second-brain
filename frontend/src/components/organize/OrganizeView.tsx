// The Organize tab. Shows the most recent organize run + its proposals.
// User flow:
//   1. Click "Run a new organize" → POST /api/organize/runs (returns run_id).
//   2. View polls /current every 2s while status === "running".
//   3. Once "completed", review proposal cards. Discard any you don't want.
//   4. Click "Apply N pending" → POST /apply. State refreshes.

import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, Play, Sparkles } from "lucide-react";

import { api, type OrganizeProposal, type OrganizeRun } from "@/lib/api";
import ProposalCard from "./ProposalCard";

interface Props {
  onOpenWiki: (path: string | null) => void;
}

export default function OrganizeView({ onOpenWiki }: Props) {
  const { t } = useTranslation();
  const qc = useQueryClient();

  const run = useQuery<OrganizeRun | null>({
    queryKey: ["organize-current"],
    queryFn: () => api.get<OrganizeRun | null>("/api/organize/runs/current"),
    // While a run is in flight, poll every 2s. Otherwise trust manual refresh.
    refetchInterval: (q) =>
      q.state.data && q.state.data.status === "running" ? 2_000 : false,
  });

  const start = useMutation({
    mutationFn: () => api.post<{ run_id: string }>("/api/organize/runs"),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["organize-current"] }),
  });

  const discard = useMutation({
    mutationFn: ({ runId, path }: { runId: string; path: string }) =>
      api.delete(
        `/api/organize/runs/${runId}/proposals?path=${encodeURIComponent(path)}`,
      ),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["organize-current"] }),
  });

  const apply = useMutation({
    mutationFn: (runId: string) =>
      api.post<{ applied: number; failed: number }>(
        `/api/organize/runs/${runId}/apply`,
      ),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["organize-current"] }),
  });

  const applyOne = useMutation({
    mutationFn: ({ runId, path }: { runId: string; path: string }) =>
      api.post<{ state: string; operations: string[]; error: string | null }>(
        `/api/organize/runs/${runId}/proposals/apply?path=${encodeURIComponent(path)}`,
      ),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["organize-current"] }),
  });

  const revise = useMutation({
    mutationFn: ({
      runId,
      path,
      instruction,
    }: {
      runId: string;
      path: string;
      instruction: string;
    }) =>
      api.post<OrganizeProposal>(
        `/api/organize/runs/${runId}/proposals/revise?path=${encodeURIComponent(path)}`,
        { instruction },
      ),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["organize-current"] }),
  });

  const r = run.data ?? null;
  const pendingCount = r?.counts.pending ?? 0;
  const isRunning = r?.status === "running";

  // Filter — by default hide discarded items so the active list is focused.
  const [showDiscarded, setShowDiscarded] = useState(false);
  const visibleProposals = useMemo(() => {
    if (!r) return [];
    return r.proposals.filter((p) => showDiscarded || p.state !== "discarded");
  }, [r, showDiscarded]);

  return (
    <div className="flex h-full flex-col">
      <header className="flex flex-wrap items-center gap-3 border-b border-border bg-surface px-4 py-3">
        <Sparkles className="h-5 w-5 text-accent" />
        <h1 className="flex-1 text-lg font-semibold">{t("organize.title")}</h1>

        <button
          type="button"
          onClick={() => start.mutate()}
          disabled={start.isPending || isRunning}
          className="flex items-center gap-1 rounded-lg border border-border bg-bg px-3 py-1.5 text-sm hover:border-accent disabled:opacity-50"
        >
          <Play className="h-3.5 w-3.5" />
          {isRunning ? t("organize.running") : t("organize.runNew")}
        </button>

        {r && pendingCount > 0 && (
          <button
            type="button"
            onClick={() => apply.mutate(r.id)}
            disabled={apply.isPending || isRunning}
            className="flex items-center gap-1 rounded-lg bg-accent px-3 py-1.5 text-sm font-medium text-bg disabled:opacity-50"
          >
            <Check className="h-3.5 w-3.5" />
            {apply.isPending
              ? t("organize.applying")
              : t("organize.applyN", { n: pendingCount })}
          </button>
        )}
      </header>

      {/* Status strip */}
      {r && (
        <div className="flex flex-wrap items-center gap-3 border-b border-border bg-surface px-4 py-2 text-xs text-muted">
          <span>{t("organize.status")}: <b className="text-text">{r.status}</b></span>
          <span>·</span>
          <span>{t("organize.notesTotal")}: {r.notes_total}</span>
          <span>·</span>
          <span className="text-muted">
            {t("organize.pending")}: <b className="text-text">{r.counts.pending}</b>
          </span>
          <span className="text-green-300">
            {t("organize.applied")}: {r.counts.applied}
          </span>
          <span className="text-muted">
            {t("organize.discarded")}: {r.counts.discarded}
          </span>
          <span className="text-red-300">
            {t("organize.failed")}: {r.counts.failed}
          </span>
          <span className="ml-auto">
            <label className="flex items-center gap-1.5 cursor-pointer">
              <input
                type="checkbox"
                checked={showDiscarded}
                onChange={(e) => setShowDiscarded(e.target.checked)}
              />
              {t("organize.showDiscarded")}
            </label>
          </span>
        </div>
      )}

      {start.isError && (
        <p className="m-4 rounded border border-red-500/40 bg-red-500/10 p-3 text-sm text-red-300">
          {(start.error as Error).message}
        </p>
      )}
      {apply.isError && (
        <p className="m-4 rounded border border-red-500/40 bg-red-500/10 p-3 text-sm text-red-300">
          {(apply.error as Error).message}
        </p>
      )}
      {r?.error && (
        <p className="m-4 rounded border border-red-500/40 bg-red-500/10 p-3 text-sm text-red-300">
          {r.error}
        </p>
      )}

      <div className="flex-1 overflow-y-auto px-4 py-4">
        {run.isLoading ? (
          <p className="px-2 text-sm text-muted">{t("common.loading")}</p>
        ) : !r ? (
          <EmptyState onRun={() => start.mutate()} disabled={start.isPending} />
        ) : visibleProposals.length === 0 ? (
          isRunning ? (
            <p className="px-2 text-sm text-muted">{t("organize.runningMsg")}</p>
          ) : (
            <p className="px-2 text-sm text-muted">{t("organize.noProposals")}</p>
          )
        ) : (
          <div className="mx-auto max-w-3xl space-y-3">
            {visibleProposals.map((p) => (
              <ProposalCard
                key={p.path}
                proposal={p}
                onDiscard={(path) => discard.mutate({ runId: r.id, path })}
                onApply={(path) => applyOne.mutate({ runId: r.id, path })}
                onRevise={async (path, instruction) => {
                  await revise.mutateAsync({ runId: r.id, path, instruction });
                }}
                onOpenWiki={onOpenWiki}
                busy={
                  discard.isPending ||
                  apply.isPending ||
                  applyOne.isPending ||
                  revise.isPending
                }
              />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function EmptyState({ onRun, disabled }: { onRun: () => void; disabled: boolean }) {
  const { t } = useTranslation();
  return (
    <div className="flex h-full items-center justify-center px-6 text-center">
      <div className="max-w-md space-y-3">
        <Sparkles className="mx-auto h-8 w-8 text-accent" />
        <h2 className="text-lg font-medium">{t("organize.emptyTitle")}</h2>
        <p className="text-sm text-muted">{t("organize.emptyBody")}</p>
        <button
          type="button"
          onClick={onRun}
          disabled={disabled}
          className="inline-flex items-center gap-2 rounded-lg bg-accent px-4 py-2 text-sm font-medium text-bg disabled:opacity-50"
        >
          <Play className="h-4 w-4" />
          {t("organize.runNew")}
        </button>
      </div>
    </div>
  );
}

