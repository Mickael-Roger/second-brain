// Modal that appears when the user clicks a dead wikilink inside a
// Training fiche. Confirms generation, optionally enables web search,
// then calls POST /api/training/expand. On success, the parent
// navigates to the freshly-created fiche.

import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Loader2, Sparkles, X } from "lucide-react";

import { api, type TrainingExpandResponse } from "@/lib/api";

interface Props {
  targetConcept: string;
  parentPath: string;
  onClose: () => void;
  onGenerated: (path: string) => void;
}

export default function TrainingExpandModal({
  targetConcept,
  parentPath,
  onClose,
  onGenerated,
}: Props) {
  const { t } = useTranslation();
  const [webSearch, setWebSearch] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Esc closes the modal — but only when we're not in the middle of a
  // generation request (the user could lose their fiche).
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !busy) onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [busy, onClose]);

  const submit = async () => {
    setBusy(true);
    setError(null);
    try {
      const res = await api.post<TrainingExpandResponse>("/api/training/expand", {
        target_concept: targetConcept,
        parent_path: parentPath,
        web_search: webSearch,
      });
      onGenerated(res.path);
    } catch (err) {
      setError((err as Error)?.message ?? "request failed");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 px-4"
      onClick={() => {
        if (!busy) onClose();
      }}
    >
      <div
        className="w-full max-w-md rounded-lg border border-border bg-surface shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between border-b border-border px-4 py-3">
          <div className="flex items-center gap-2">
            <Sparkles className="h-4 w-4 text-accent" />
            <h2 className="text-sm font-medium">{t("training.modalTitle")}</h2>
          </div>
          <button
            type="button"
            onClick={onClose}
            disabled={busy}
            className="text-muted hover:text-text disabled:opacity-30"
            aria-label={t("common.cancel")}
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="space-y-3 px-4 py-4">
          <p className="text-sm text-text">
            {t("training.modalLead")}{" "}
            <strong className="text-accent">{targetConcept}</strong>
          </p>
          <p className="text-xs text-muted">
            {t("training.modalParent")}: <code>{parentPath}</code>
          </p>

          <label className="flex cursor-pointer items-center gap-2 text-xs text-muted">
            <input
              type="checkbox"
              checked={webSearch}
              onChange={(e) => setWebSearch(e.target.checked)}
              disabled={busy}
              className="accent-accent"
            />
            <span>{t("training.useWebSearch")}</span>
          </label>

          {error && (
            <p className="rounded border border-red-500/40 bg-red-500/10 px-2 py-1 text-xs text-red-400">
              {error}
            </p>
          )}
        </div>

        <div className="flex items-center justify-end gap-2 border-t border-border px-4 py-3">
          <button
            type="button"
            onClick={onClose}
            disabled={busy}
            className="rounded border border-border px-3 py-1 text-xs text-muted hover:border-accent hover:text-text disabled:opacity-30"
          >
            {t("common.cancel")}
          </button>
          <button
            type="button"
            onClick={submit}
            disabled={busy}
            className="flex items-center gap-1.5 rounded bg-accent px-3 py-1 text-xs text-white hover:bg-accent/90 disabled:opacity-60"
          >
            {busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Sparkles className="h-3.5 w-3.5" />}
            <span>{busy ? t("training.generating") : t("training.generate")}</span>
          </button>
        </div>
      </div>
    </div>
  );
}
