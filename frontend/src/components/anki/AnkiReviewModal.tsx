import { useEffect, useRef, useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { X } from "lucide-react";

import { api, type AnkiCard } from "@/lib/api";
import AnkiCardContent from "./AnkiCardContent";
import { errorMessage } from "./errors";

interface Props {
  card: AnkiCard;
  online: boolean;
  onClose: () => void;
  onReviewed: () => void;
}

export default function AnkiReviewModal({ card, online, onClose, onReviewed }: Props) {
  const { t } = useTranslation();
  const dialog = useRef<HTMLDialogElement>(null);
  const [answerShown, setAnswerShown] = useState(false);
  const answer = useMutation({
    networkMode: "always", retry: false,
    mutationFn: (ease: number) => api.post(`/api/anki/cards/${card.card_id}/answer`, { ease }),
    onSuccess: onReviewed,
  });
  useEffect(() => {
    const node = dialog.current;
    node?.showModal();
    return () => node?.close();
  }, []);
  const buttonClass = "min-h-11 rounded-lg border border-border px-3 py-2 text-sm hover:border-accent";
  return <dialog ref={dialog} aria-labelledby="anki-review-title"
    onCancel={(event) => { event.preventDefault(); if (!answer.isPending) onClose(); }}
    className="m-auto max-h-[90dvh] w-[calc(100%-2rem)] max-w-3xl overflow-y-auto rounded-xl border border-border bg-surface p-0 text-text shadow-xl backdrop:bg-black/60">
    <div className="space-y-4 p-4 sm:p-6">
      <div className="flex items-center justify-between gap-3">
        <h2 id="anki-review-title" className="min-w-0 break-words text-lg font-semibold">{t("anki.review")} — {card.deck_name}</h2>
        <button onClick={onClose} disabled={answer.isPending} aria-label={t("anki.closeReview")} className="rounded p-2 hover:bg-bg"><X className="h-5 w-5" /></button>
      </div>
      <p className="text-sm text-muted">{t(`anki.state.${card.state}`, { defaultValue: card.state })} · {t("anki.interval", { count: card.interval })}</p>
      <AnkiCardContent key={`${card.card_id}-${answerShown}`} html={answerShown ? card.answer || card.back : card.question || card.front} css={card.css} title={t(answerShown ? "anki.back" : "anki.front")} />
      {!online && <p role="status" className="text-sm text-muted">{t("anki.offline")}</p>}
      {answer.isError && <p role="alert" className="text-sm text-red-500">{errorMessage(answer.error)}</p>}
      <div className="pb-[env(safe-area-inset-bottom)]">
        {!answerShown ? <button autoFocus onClick={() => setAnswerShown(true)} className={`${buttonClass} w-full bg-accent font-medium text-white`}>{t("anki.showAnswer")}</button>
          : <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">{["again", "hard", "good", "easy"].map((label, index) => {
            const interval = card.next_reviews?.length === 4 ? card.next_reviews[index] : "";
            const hint = interval ? t("anki.nextReview", { interval }) : t("anki.nextReviewUnavailable");
            return <button key={label} disabled={!online || answer.isPending} onClick={() => answer.mutate(index + 1)}
              aria-label={t(`anki.${label}`)} aria-describedby={`anki-next-review-${label}`} title={hint}
              className={`${buttonClass} ${index === 2 ? "border-accent text-accent" : ""}`}>
              <span className="block font-medium">{t(`anki.${label}`)}</span>
              <span id={`anki-next-review-${label}`} className="mt-1 block text-xs text-muted" aria-label={hint}>{interval || "—"}</span>
            </button>;
          })}</div>}
      </div>
      {answer.isPending && <p role="status" className="text-sm text-muted">{t("anki.saving")}</p>}
    </div>
  </dialog>;
}
