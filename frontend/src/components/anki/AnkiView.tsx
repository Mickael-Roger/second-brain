import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { ArrowLeft, ChevronRight, Layers, Plus, RefreshCw } from "lucide-react";

import { api, type AnkiCard, type AnkiDeckStats } from "@/lib/api";
import AnkiCardContent, { cardText } from "./AnkiCardContent";
import AnkiCreateModal from "./AnkiCreateModal";
import { errorMessage } from "./errors";

export default function AnkiView() {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const [deck, setDeck] = useState<string | null>(null);
  const [card, setCard] = useState<AnkiCard | null>(null);
  const [answerShown, setAnswerShown] = useState(false);
  const [creating, setCreating] = useState(false);
  const [notice, setNotice] = useState("");
  const [online, setOnline] = useState(navigator.onLine);
  const [limit, setLimit] = useState(100);
  const decks = useQuery({
    queryKey: ["anki-deck-stats"], queryFn: () => api.get<AnkiDeckStats[]>("/api/anki/deck-stats"), retry: false,
  });
  const cards = useQuery({
    queryKey: ["anki-due", deck, limit], enabled: deck !== null,
    queryFn: () => api.get<AnkiCard[]>(`/api/anki/due?${new URLSearchParams({ deck: deck!, limit: String(limit) })}`),
    retry: false,
  });
  function refresh() {
    void qc.invalidateQueries({ queryKey: ["anki-deck-stats"] });
    void qc.invalidateQueries({ queryKey: ["anki-due"] });
  }
  const answer = useMutation({
    networkMode: "always",
    retry: false,
    mutationFn: ({ id, ease }: { id: number; ease: number }) => api.post(`/api/anki/cards/${id}/answer`, { ease }),
    onSuccess: () => {
      setCard(null);
      setAnswerShown(false);
      setNotice(t("anki.reviewSaved"));
      refresh();
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
      if (creating || answer.isPending || event.ctrlKey || event.metaKey || event.altKey
          || event.target instanceof HTMLElement && (event.target.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(event.target.tagName))) return;
      if (event.key === "c" && decks.data?.length) { event.preventDefault(); setCreating(true); }
      if (event.key === "Escape") {
        if (card) setCard(null);
        else setDeck(null);
      }
    };
    window.addEventListener("keydown", key);
    return () => window.removeEventListener("keydown", key);
  }, [creating, answer.isPending, card, decks.data]);

  function openCard(item: AnkiCard) {
    answer.reset();
    setNotice("");
    setAnswerShown(false);
    setCard(item);
  }
  const buttonClass = "inline-flex min-h-10 items-center justify-center gap-2 rounded-lg border border-border px-3 py-2 text-sm hover:border-accent";
  const selectedDeck = decks.data?.find((item) => item.name === deck);
  return (
    <section className="flex h-full min-h-0 flex-col">
      <header className="flex flex-wrap items-center gap-3 border-b border-border bg-surface px-4 py-3">
        <Layers className="h-5 w-5 text-accent" />
        <h1 className="mr-auto text-lg font-semibold">{t("anki.title")}</h1>
        <button onClick={refresh} disabled={!online || decks.isFetching || cards.isFetching || answer.isPending} className={buttonClass} aria-label={t("anki.refresh")}>
          <RefreshCw className={`h-4 w-4 ${decks.isFetching || cards.isFetching ? "animate-spin" : ""}`} /><span className="hidden sm:inline">{t("anki.refresh")}</span>
        </button>
        <button onClick={() => setCreating(true)} disabled={!online || !decks.data?.length || answer.isPending} className={`${buttonClass} bg-accent text-white`}>
          <Plus className="h-4 w-4" />{t("anki.newNote")}
        </button>
      </header>
      <div className="min-h-0 flex-1 overflow-y-auto p-4 pb-[max(1rem,env(safe-area-inset-bottom))]">
        <div className="mx-auto max-w-5xl space-y-4">
          {!online && <p role="status" className="rounded-lg border border-border p-3 text-sm text-muted">{t("anki.offline")}</p>}
          {notice && <p role="status" className="text-sm text-accent">{notice}</p>}
          {decks.isError && <p role="alert" className="text-sm text-red-500">{errorMessage(decks.error)}</p>}
          {deck !== null && <div className="flex flex-wrap items-center gap-3">
            <button disabled={answer.isPending} className={buttonClass} onClick={() => { if (card) setCard(null); else { setDeck(null); setNotice(""); } }}>
              <ArrowLeft className="h-4 w-4" />{t(card ? "anki.backToCards" : "anki.backToDecks")}
            </button>
            <h2 className="min-w-0 break-words text-lg font-medium">{deck}</h2>
          </div>}
          {deck === null ? <>
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
                <tbody className="divide-y divide-border">{decks.data.map((item) => <tr key={item.deck_id} className="hover:bg-surface">
                  <th scope="row" className="text-left font-medium"><button className="flex w-full min-w-32 items-center gap-2 px-4 py-4 text-left text-accent" onClick={() => { setDeck(item.name); setLimit(100); setNotice(""); }}>
                    <ChevronRight className="h-4 w-4 shrink-0" /><span className="break-words">{item.name}</span>
                  </button></th>
                  {[item.review_count, item.new_count, item.learn_count, item.total_in_deck].map((count, index) => <td key={index} className="px-3 py-3 text-right tabular-nums">{count}</td>)}
                </tr>)}</tbody>
              </table>
            </div>}
          </> : card ? <div className="space-y-4">
            <p className="text-sm text-muted">{t(`anki.state.${card.state}`, { defaultValue: card.state })} · {t("anki.interval", { count: card.interval })}</p>
            <AnkiCardContent key={`${card.card_id}-${answerShown}`} html={answerShown ? card.answer || card.back : card.question || card.front} css={card.css} title={t(answerShown ? "anki.back" : "anki.front")} />
            {answer.isError && <p role="alert" className="text-sm text-red-500">{errorMessage(answer.error)}</p>}
            {!answerShown ? <button onClick={() => setAnswerShown(true)} className={`${buttonClass} w-full bg-accent font-medium text-white`}>{t("anki.showAnswer")}</button>
              : <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">{["again", "hard", "good", "easy"].map((label, index) =>
                <button key={label} disabled={!online || answer.isPending} onClick={() => answer.mutate({ id: card.card_id, ease: index + 1 })}
                  className={`${buttonClass} ${index === 2 ? "border-accent text-accent" : ""}`}>{t(`anki.${label}`)}</button>)}</div>}
            {answer.isPending && <p role="status" className="text-sm text-muted">{t("anki.saving")}</p>}
          </div> : <>
            {selectedDeck && <p className="text-sm text-muted">{t("anki.deckSummary", { review: selectedDeck.review_count, learning: selectedDeck.learn_count, new: selectedDeck.new_count })}</p>}
            {cards.isError && <p role="alert" className="text-sm text-red-500">{errorMessage(cards.error)}</p>}
            {cards.isPending && <p role="status">{t("common.loading")}</p>}
            {cards.data?.length === 0 && <p className="rounded-lg border border-border p-6 text-center text-muted">{t("anki.noDue")}</p>}
            <ul className="divide-y divide-border">{cards.data?.map((item) => <li key={item.card_id}>
              <button onClick={() => openCard(item)} className="flex w-full items-center gap-3 rounded-lg px-3 py-4 text-left hover:bg-surface">
                <div className="min-w-0 flex-1"><p className="line-clamp-2 break-words font-medium">{cardText(item.question || item.front) || t("anki.cardNumber", { id: item.card_id })}</p>
                  <p className="mt-1 text-xs text-muted">{t(`anki.state.${item.state}`, { defaultValue: item.state })} · {t("anki.interval", { count: item.interval })}</p>
                </div><ChevronRight className="h-4 w-4 shrink-0 text-muted" />
              </button>
            </li>)}</ul>
            {cards.data?.length === limit && limit < 10000 && <button className={buttonClass} onClick={() => setLimit(Math.min(10000, limit + 100))} disabled={cards.isFetching}>{t("anki.loadMore")}</button>}
          </>}
        </div>
      </div>
      {creating && <AnkiCreateModal decks={decks.data || []} initialDeck={deck || ""} online={online}
        onClose={() => setCreating(false)} onCreated={(count) => { setCreating(false); setNotice(t("anki.success", { count })); refresh(); }} />}
    </section>
  );
}
