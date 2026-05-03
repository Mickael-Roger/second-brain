// Modal that appears when the user clicks a dead wikilink inside a
// Training fiche. Confirms generation, optionally enables web search,
// then opens an SSE stream to /api/training/expand and waits for the
// `done` event. On success, the parent navigates to the freshly-
// created fiche. The endpoint is silent for 30-90s on the wire while
// the LLM works — heartbeats keep the connection alive across
// reverse-proxies (Tailscale Funnel was killing it after ~60s).

import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Loader2, Sparkles, X } from "lucide-react";

import { streamSse } from "@/lib/sse";

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
  const abortRef = useRef<AbortController | null>(null);

  // Esc closes the modal — but only when we're not in the middle of a
  // generation request (the user could lose their fiche).
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !busy) onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [busy, onClose]);

  // If the modal unmounts while a generation is in flight, abort the
  // SSE so the orphaned connection doesn't keep the server busy
  // (the backend will still finish writing the fiche — that work is
  // not aborted server-side, only the response stream we're reading).
  useEffect(() => {
    return () => {
      abortRef.current?.abort();
    };
  }, []);

  const submit = async () => {
    setBusy(true);
    setError(null);
    const ctrl = new AbortController();
    abortRef.current = ctrl;

    let resolved = false;
    try {
      await streamSse(
        "/api/training/expand",
        {
          target_concept: targetConcept,
          parent_path: parentPath,
          web_search: webSearch,
        },
        {
          signal: ctrl.signal,
          onEvent: (ev) => {
            if (ev.event === "done") {
              const d = ev.data as { path: string };
              resolved = true;
              onGenerated(d.path);
            } else if (ev.event === "error") {
              const d = ev.data as { error: string; status?: number };
              resolved = true;
              setError(d.error ?? "request failed");
            }
          },
        },
      );
      if (!resolved) {
        // Stream ended without a done/error — should never happen, but
        // surface it rather than swallow it.
        setError("server closed the stream without a result");
      }
    } catch (err) {
      if ((err as Error).name !== "AbortError") {
        setError((err as Error)?.message ?? "request failed");
      }
    } finally {
      setBusy(false);
      abortRef.current = null;
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
