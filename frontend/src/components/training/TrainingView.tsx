// Training dashboard: lists themes (folders under Training/<theme>/Index.md)
// and surfaces a CTA to seed a new one. Click a card → open its Index.md
// in the wiki view; click "Nouveau thème" → NewThemeModal hands the
// user off to the chat with a pre-filled prompt.

import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useQuery } from "@tanstack/react-query";
import { GraduationCap, Loader2, Plus, Sparkles } from "lucide-react";

import { api, type TrainingThemeListResponse } from "@/lib/api";
import NewThemeModal from "./NewThemeModal";

interface Props {
  onOpenWiki: (path: string | null) => void;
  onOpenChat: () => void;
}

export default function TrainingView({ onOpenWiki, onOpenChat }: Props) {
  const { t, i18n } = useTranslation();
  const [newOpen, setNewOpen] = useState(false);

  const themes = useQuery({
    queryKey: ["training-themes"],
    queryFn: () => api.get<TrainingThemeListResponse>("/api/training/themes"),
    staleTime: 30_000,
  });

  const fmtDate = (iso: string) => {
    try {
      return new Intl.DateTimeFormat(i18n.language, {
        dateStyle: "medium",
      }).format(new Date(iso));
    } catch {
      return iso.slice(0, 10);
    }
  };

  const list = themes.data?.themes ?? [];

  return (
    <div className="flex h-full flex-col overflow-hidden">
      <header className="flex items-start justify-between gap-3 border-b border-border bg-surface px-4 py-3 md:px-6 md:py-4">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <GraduationCap className="h-5 w-5 text-accent" />
            <h1 className="truncate text-lg font-semibold">
              {t("training.dashboard.title")}
            </h1>
          </div>
          <p className="mt-1 text-xs text-muted md:text-sm">
            {t("training.dashboard.subtitle")}
          </p>
        </div>
        <button
          type="button"
          onClick={() => setNewOpen(true)}
          className="flex shrink-0 items-center gap-1.5 rounded-lg bg-accent px-3 py-2 text-xs font-medium text-white hover:bg-accent/90"
        >
          <Plus className="h-4 w-4" />
          <span>{t("training.dashboard.newTheme")}</span>
        </button>
      </header>

      <div className="min-h-0 flex-1 overflow-y-auto px-4 py-4 md:px-6 md:py-6">
        {themes.isLoading && (
          <div className="flex items-center justify-center gap-2 py-12 text-sm text-muted">
            <Loader2 className="h-4 w-4 animate-spin" />
            <span>{t("training.dashboard.loading")}</span>
          </div>
        )}

        {themes.isError && (
          <div className="rounded border border-red-500/40 bg-red-500/10 px-3 py-2 text-sm text-red-400">
            {t("training.dashboard.loadError")}
          </div>
        )}

        {themes.data && list.length === 0 && (
          <div className="flex flex-col items-center justify-center gap-3 py-16 text-center">
            <Sparkles className="h-8 w-8 text-muted" />
            <p className="max-w-md text-sm text-muted">
              {t("training.dashboard.empty")}
            </p>
            <button
              type="button"
              onClick={() => setNewOpen(true)}
              className="flex items-center gap-1.5 rounded-lg bg-accent px-3 py-2 text-xs font-medium text-white hover:bg-accent/90"
            >
              <Plus className="h-4 w-4" />
              <span>{t("training.dashboard.newTheme")}</span>
            </button>
          </div>
        )}

        {list.length > 0 && (
          <ul className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-3">
            {list.map((theme) => (
              <li key={theme.theme}>
                <button
                  type="button"
                  onClick={() => onOpenWiki(theme.index_path)}
                  className="group flex h-full w-full flex-col gap-2 rounded-lg border border-border bg-surface p-4 text-left transition hover:border-accent hover:bg-bg"
                >
                  <div className="flex items-start justify-between gap-2">
                    <h2 className="truncate text-sm font-semibold text-text group-hover:text-accent">
                      {theme.theme}
                    </h2>
                    <span className="shrink-0 rounded bg-bg px-1.5 py-0.5 text-[10px] uppercase tracking-wide text-muted">
                      {t("training.dashboard.ficheCount", {
                        count: theme.fiche_count,
                      })}
                    </span>
                  </div>
                  {theme.overview && (
                    <p className="line-clamp-4 text-xs text-muted">
                      {theme.overview}
                    </p>
                  )}
                  <div className="mt-auto pt-1 text-[10px] text-muted">
                    {t("training.dashboard.updated", {
                      date: fmtDate(theme.updated_at),
                    })}
                  </div>
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>

      {newOpen && (
        <NewThemeModal
          onClose={() => setNewOpen(false)}
          onStartInChat={() => {
            setNewOpen(false);
            onOpenChat();
          }}
        />
      )}
    </div>
  );
}
