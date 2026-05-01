// Anki-style review modal for Wiki/ pages.
//
// Opens over the wiki view, fetches a random `Wiki/**` note from the
// backend (biased toward never-seen and overdue notes), renders it
// full-screen, and lets the user rate it. Each rating closes the
// dialog and invalidates the status query so the header dot
// disappears as soon as the first review of the day is recorded.

import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowRight, Loader2, X } from "lucide-react";

import { api, type TreeEntry, type WikiReviewNext, type WikiReviewRating } from "@/lib/api";
import NoteRenderer from "./NoteRenderer";

interface Props {
  treeEntries: TreeEntry[];
  onOpenWiki: (path: string | null) => void;
  onClose: () => void;
}

const RATING_BUTTONS: {
  rating: WikiReviewRating;
  labelKey: string;
  hintKey: string;
  className: string;
}[] = [
  {
    rating: "uninteresting",
    labelKey: "wiki.review.uninteresting",
    hintKey: "wiki.review.uninterestingHint",
    className:
      "border-red-500/50 bg-red-500/10 text-red-300 hover:bg-red-500/20",
  },
  {
    rating: "soon",
    labelKey: "wiki.review.soon",
    hintKey: "wiki.review.soonHint",
    className:
      "border-amber-500/50 bg-amber-500/10 text-amber-300 hover:bg-amber-500/20",
  },
  {
    rating: "roughly",
    labelKey: "wiki.review.roughly",
    hintKey: "wiki.review.roughlyHint",
    className:
      "border-sky-500/50 bg-sky-500/10 text-sky-300 hover:bg-sky-500/20",
  },
  {
    rating: "perfect",
    labelKey: "wiki.review.perfect",
    hintKey: "wiki.review.perfectHint",
    className:
      "border-emerald-500/50 bg-emerald-500/10 text-emerald-300 hover:bg-emerald-500/20",
  },
];

export default function WikiReviewModal({
  treeEntries,
  onOpenWiki,
  onClose,
}: Props) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  // Bumping `pickNonce` re-runs the next-pick query (i.e. "draw another
  // note") without invalidating the cache for unrelated callers.
  const [pickNonce, setPickNonce] = useState(0);

  const next = useQuery<WikiReviewNext, Error>({
    queryKey: ["wiki-review-next", pickNonce],
    queryFn: () => api.get<WikiReviewNext>("/api/wiki-reviews/next"),
    staleTime: 0,
    gcTime: 0,
    refetchOnWindowFocus: false,
  });

  const rate = useMutation({
    mutationFn: (input: { path: string; rating: WikiReviewRating }) =>
      api.post("/api/wiki-reviews", input),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["wiki-review-status"] });
    },
  });

  // Close on Esc.
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  async function handleRate(rating: WikiReviewRating) {
    const path = next.data?.path;
    if (!path || rate.isPending) return;
    await rate.mutateAsync({ path, rating });
    onClose();
  }

  function handleSkip() {
    setPickNonce((n) => n + 1);
  }

  function handleOpenInWiki() {
    if (!next.data) return;
    onOpenWiki(next.data.path);
    onClose();
  }

  const lastSeen = useMemo(() => {
    const s = next.data?.state;
    if (!s) return null;
    try {
      return new Date(s.last_reviewed_at).toLocaleDateString();
    } catch {
      return s.last_reviewed_at;
    }
  }, [next.data?.state]);

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4"
      onClick={onClose}
    >
      <div
        className="flex h-[90vh] w-full max-w-3xl flex-col rounded-lg border border-border bg-surface shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <header className="flex items-center justify-between gap-3 border-b border-border px-4 py-2.5">
          <div className="min-w-0 flex-1">
            <h2 className="text-sm font-semibold">{t("wiki.review.title")}</h2>
            {next.data ? (
              <p className="truncate text-xs text-muted">
                <code>{next.data.path}</code>
                {lastSeen && (
                  <span className="ml-2">
                    · {t("wiki.review.lastSeen", { date: lastSeen })}
                  </span>
                )}
              </p>
            ) : (
              <p className="text-xs text-muted">…</p>
            )}
          </div>
          <button
            type="button"
            onClick={handleSkip}
            disabled={next.isFetching || rate.isPending}
            title={t("wiki.review.skip")}
            className="flex items-center gap-1 rounded border border-border px-2 py-1 text-xs text-muted hover:border-accent hover:text-text disabled:opacity-50"
          >
            <ArrowRight className="h-3.5 w-3.5" />
            {t("wiki.review.skip")}
          </button>
          {next.data && (
            <button
              type="button"
              onClick={handleOpenInWiki}
              className="hidden rounded border border-border px-2 py-1 text-xs text-muted hover:border-accent hover:text-text sm:block"
            >
              {t("wiki.review.openInWiki")}
            </button>
          )}
          <button
            type="button"
            onClick={onClose}
            className="rounded p-1 text-muted hover:bg-bg hover:text-text"
            aria-label={t("common.cancel")}
          >
            <X className="h-4 w-4" />
          </button>
        </header>

        <div className="flex-1 overflow-y-auto">
          {next.isLoading ? (
            <div className="flex h-full items-center justify-center text-sm text-muted">
              <Loader2 className="mr-2 h-4 w-4 animate-spin" />
              {t("common.loading")}
            </div>
          ) : next.isError ? (
            <div className="flex h-full items-center justify-center px-6 text-center text-sm text-red-400">
              {(next.error as Error)?.message ?? "error"}
            </div>
          ) : next.data ? (
            <NoteRenderer
              content={next.data.content}
              treeEntries={treeEntries}
              currentPath={next.data.path}
              onOpen={(p) => {
                onOpenWiki(p);
                onClose();
              }}
            />
          ) : null}
        </div>

        <footer className="border-t border-border bg-bg/40 px-3 py-3">
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
            {RATING_BUTTONS.map((b) => (
              <button
                key={b.rating}
                type="button"
                onClick={() => handleRate(b.rating)}
                disabled={!next.data || rate.isPending}
                title={t(b.hintKey)}
                className={`flex flex-col items-center justify-center gap-0.5 rounded border px-2 py-2 text-xs transition disabled:cursor-not-allowed disabled:opacity-50 ${b.className}`}
              >
                <span className="font-medium">{t(b.labelKey)}</span>
                <span className="text-[10px] opacity-70">{t(b.hintKey)}</span>
              </button>
            ))}
          </div>
        </footer>
      </div>
    </div>
  );
}
