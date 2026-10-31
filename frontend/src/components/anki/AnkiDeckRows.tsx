import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { ChevronDown, ChevronRight } from "lucide-react";

import { api, type AnkiCard, type AnkiDeckStats } from "@/lib/api";
import { cardText } from "./AnkiCardContent";
import { errorMessage } from "./errors";

interface Props {
  deck: AnkiDeckStats;
  online: boolean;
  busy: boolean;
  onSelect: () => void;
  onReview: (card: AnkiCard) => void;
}

export default function AnkiDeckRows({ deck, online, busy, onSelect, onReview }: Props) {
  const { t } = useTranslation();
  const [expanded, setExpanded] = useState(false);
  const [limit, setLimit] = useState(100);
  const cards = useQuery({
    queryKey: ["anki-due", deck.name, limit], enabled: expanded,
    queryFn: () => api.get<AnkiCard[]>(`/api/anki/due?${new URLSearchParams({ deck: deck.name, limit: String(limit) })}`),
    retry: false,
  });
  const groupId = `anki-deck-cards-${deck.deck_id}`;
  return <>
    <tr className="hover:bg-surface">
      <th scope="row" className="text-left font-medium">
        <button disabled={busy} aria-expanded={expanded} aria-controls={groupId} onClick={() => { onSelect(); setExpanded(!expanded); }}
          className="flex w-full min-w-32 items-center gap-2 px-4 py-4 text-left text-accent">
          {expanded ? <ChevronDown className="h-4 w-4 shrink-0" /> : <ChevronRight className="h-4 w-4 shrink-0" />}
          <span className="break-words">{deck.name}</span>
        </button>
      </th>
      {[deck.review_count, deck.new_count, deck.learn_count, deck.total_in_deck].map((count, index) =>
        <td key={index} className="px-3 py-3 text-right tabular-nums">{count}</td>)}
    </tr>
    <tr id={groupId} hidden={!expanded}>
      <td colSpan={5} className="bg-surface/40 px-3 py-2 sm:pl-9">
        {cards.isPending && <p role="status" className="p-3 text-muted">{t("common.loading")}</p>}
        {cards.isError && <div className="flex flex-wrap items-center gap-3 p-3">
          <p role="alert" className="text-red-500">{errorMessage(cards.error)}</p>
          <button onClick={() => void cards.refetch()} disabled={!online || cards.isFetching || busy} className="rounded border border-border px-3 py-2">{t("anki.refresh")}</button>
        </div>}
        {cards.data?.length === 0 && <p className="p-3 text-muted">{t("anki.noDue")}</p>}
        <ul className="divide-y divide-border">{cards.data?.map((card) => <li key={card.card_id}>
          <button disabled={busy} onClick={() => onReview(card)} className="flex w-full items-center gap-3 rounded-lg px-3 py-4 text-left hover:bg-surface">
            <div className="min-w-0 flex-1">
              <p className="line-clamp-2 break-words font-medium">{cardText(card.question || card.front) || t("anki.cardNumber", { id: card.card_id })}</p>
              <p className="mt-1 text-xs text-muted">{t(`anki.state.${card.state}`, { defaultValue: card.state })} · {t("anki.interval", { count: card.interval })}</p>
            </div><ChevronRight className="h-4 w-4 shrink-0 text-muted" />
          </button>
        </li>)}</ul>
        {cards.data?.length === limit && limit < 10000 && <button onClick={() => setLimit(Math.min(10000, limit + 100))}
          disabled={!online || cards.isFetching || busy} className="m-3 rounded-lg border border-border px-3 py-2">{t("anki.loadMore")}</button>}
      </td>
    </tr>
  </>;
}
