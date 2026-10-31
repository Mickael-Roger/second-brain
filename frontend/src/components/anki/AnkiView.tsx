import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Cloud, Layers, Plus, RefreshCw } from "lucide-react";

import { api, type AnkiCard, type AnkiDeckStats } from "@/lib/api";
import AnkiCreateModal from "./AnkiCreateModal";
import AnkiDeckRows from "./AnkiDeckRows";
import AnkiReviewModal from "./AnkiReviewModal";
import { errorMessage } from "./errors";

export default function AnkiView() {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const [preferredDeck, setPreferredDeck] = useState("");
  const [card, setCard] = useState<AnkiCard | null>(null);
  const [creating, setCreating] = useState(false);
  const [notice, setNotice] = useState("");
  const [online, setOnline] = useState(navigator.onLine);
  const decks = useQuery({
    queryKey: ["anki-deck-stats"],
    queryFn: () => api.get<AnkiDeckStats[]>("/api/anki/deck-stats"),
    retry: false,
  });
  function refresh() {
    return Promise.all([
      qc.invalidateQueries({ queryKey: ["anki-deck-stats"] }),
      qc.invalidateQueries({ queryKey: ["anki-due"] }),
    ]);
  }
  const sync = useMutation({
    networkMode: "always", retry: false,
    mutationFn: () => api.post<{ synced: boolean }>("/api/anki/sync"),
    onMutate: () => setNotice(""),
    onSuccess: async () => {
      await refresh();
      setNotice(t("anki.syncSuccess"));
    },
  });
  useEffect(() => {
    const update = () => setOnline(navigator.onLine);
    window.addEventListener("online", update);
    window.addEventListener("offline", update);
    return () => { window.removeEventListener("online", update); window.removeEventListener("offline", update); };
  }, []);
  useEffect(() => {
    const key = (event: KeyboardEvent) => {
      if (creating || card || sync.isPending || event.ctrlKey || event.metaKey || event.altKey
          || event.target instanceof HTMLElement && (event.target.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(event.target.tagName))) return;
      if (event.key === "c" && decks.data?.length) { event.preventDefault(); setCreating(true); }
    };
    window.addEventListener("keydown", key);
    return () => window.removeEventListener("keydown", key);
  }, [creating, card, decks.data, sync.isPending]);

  const buttonClass = "inline-flex min-h-10 items-center justify-center gap-2 rounded-lg border border-border px-3 py-2 text-sm hover:border-accent";
  return (
    <section className="flex h-full min-h-0 flex-col">
      <header className="flex flex-wrap items-center gap-3 border-b border-border bg-surface px-4 py-3">
        <Layers className="h-5 w-5 text-accent" />
        <h1 className="mr-auto text-lg font-semibold">{t("anki.title")}</h1>
        <button onClick={() => sync.mutate()} disabled={!online || sync.isPending} className={buttonClass}>
          {sync.isPending ? <RefreshCw className="h-4 w-4 animate-spin" /> : <Cloud className="h-4 w-4" />}
          {t(sync.isPending ? "anki.syncing" : "anki.sync")}
        </button>
        <button onClick={() => void refresh()} disabled={!online || decks.isFetching || sync.isPending} className={buttonClass} aria-label={t("anki.refresh")}>
          <RefreshCw className={`h-4 w-4 ${decks.isFetching ? "animate-spin" : ""}`} /><span className="hidden sm:inline">{t("anki.refresh")}</span>
        </button>
        <button onClick={() => setCreating(true)} disabled={!online || !decks.data?.length || sync.isPending} className={`${buttonClass} bg-accent text-white`}>
          <Plus className="h-4 w-4" />{t("anki.newNote")}
        </button>
      </header>
      <div className="min-h-0 flex-1 overflow-y-auto p-4 pb-[max(1rem,env(safe-area-inset-bottom))]">
        <div className="mx-auto max-w-5xl space-y-4">
          {!online && <p role="status" className="rounded-lg border border-border p-3 text-sm text-muted">{t("anki.offline")}</p>}
          {notice && <p role="status" className="text-sm text-accent">{notice}</p>}
          {sync.isError && <p role="alert" className="text-sm text-red-500">{errorMessage(sync.error)}</p>}
          {decks.isError && <p role="alert" className="text-sm text-red-500">{errorMessage(decks.error)}</p>}
          <p className="text-sm text-muted">{t("anki.description")}</p>
          <p className="text-xs text-muted">{t("anki.countsHelp")}</p>
          {decks.isPending && <p role="status">{t("common.loading")}</p>}
          {decks.data?.length === 0 && <p>{t("anki.noDecks")}</p>}
          {!!decks.data?.length && <div className="overflow-x-auto rounded-lg border border-border">
            <table className="w-full text-sm">
              <thead className="bg-surface text-left text-muted"><tr>
                <th scope="col" className="px-4 py-3">{t("anki.deck")}</th>
                {["review", "new", "learning", "total"].map((key) => <th key={key} scope="col" className="px-3 py-3 text-right">{t(`anki.${key}`)}</th>)}
              </tr></thead>
              <tbody className="divide-y divide-border">{decks.data.map((deck) =>
                <AnkiDeckRows key={deck.deck_id} deck={deck} online={online} busy={sync.isPending} onSelect={() => setPreferredDeck(deck.name)}
                  onReview={(item) => { setNotice(""); setCard({ ...item, deck_name: deck.name }); }} />
              )}</tbody>
            </table>
          </div>}
        </div>
      </div>
      {card && <AnkiReviewModal card={card} online={online} onClose={() => setCard(null)}
        onReviewed={() => { setCard(null); setNotice(t("anki.reviewSaved")); refresh(); }} />}
      {creating && <AnkiCreateModal decks={decks.data || []} initialDeck={preferredDeck} online={online}
        onClose={() => setCreating(false)} onCreated={(count) => { setCreating(false); setNotice(t("anki.success", { count })); refresh(); }} />}
    </section>
  );
}
