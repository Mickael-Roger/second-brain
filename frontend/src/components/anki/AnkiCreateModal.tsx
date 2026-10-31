import { useEffect, useRef, useState, type FormEvent } from "react";
import { useMutation } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { X } from "lucide-react";

import { api, type AnkiDeckStats } from "@/lib/api";
import { errorMessage } from "./errors";

interface Props {
  decks: AnkiDeckStats[];
  initialDeck: string;
  online: boolean;
  onClose: () => void;
  onCreated: (count: number) => void;
}

export default function AnkiCreateModal({ decks, initialDeck, online, onClose, onCreated }: Props) {
  const { t } = useTranslation();
  const dialog = useRef<HTMLDialogElement>(null);
  const [deck, setDeck] = useState(initialDeck || decks[0]?.name || "");
  const [front, setFront] = useState("");
  const [back, setBack] = useState("");
  const [mode, setMode] = useState<"normal" | "reverse">("normal");
  const [tags, setTags] = useState("");
  const create = useMutation({
    networkMode: "always",
    retry: false,
    mutationFn: () => api.post<{ note_id: number; cards_created: number }>("/api/anki/cards", {
      deck, front: front.trim(), back: back.trim(), mode, tags: tags.split(/\s+/).filter(Boolean),
    }),
    onSuccess: (result) => onCreated(result.cards_created),
  });
  useEffect(() => {
    const node = dialog.current;
    node?.showModal();
    return () => node?.close();
  }, []);
  function submit(event: FormEvent) {
    event.preventDefault();
    if (!online || create.isPending || !deck || !front.trim() || !back.trim()) return;
    create.mutate();
  }
  const fieldClass = "w-full rounded-lg border border-border bg-bg px-3 py-2 text-sm focus:border-accent focus:outline-none";
  return (
    <dialog ref={dialog} onCancel={(event) => { event.preventDefault(); if (!create.isPending) onClose(); }}
      aria-labelledby="anki-create-title"
      className="m-auto max-h-[90dvh] w-[calc(100%-2rem)] max-w-2xl overflow-y-auto rounded-xl border border-border bg-surface p-0 text-text shadow-xl backdrop:bg-black/60">
      <form onSubmit={submit} className="space-y-4 p-4 sm:p-6">
        <div className="flex items-center justify-between gap-3">
          <h2 id="anki-create-title" className="text-lg font-semibold">{t("anki.newNote")}</h2>
          <button type="button" onClick={onClose} disabled={create.isPending} aria-label={t("common.cancel")} className="rounded p-2 hover:bg-bg"><X className="h-5 w-5" /></button>
        </div>
        <fieldset disabled={create.isPending} className="space-y-4">
          <label className="block space-y-1"><span>{t("anki.deck")}</span>
            <select value={deck} onChange={(event) => setDeck(event.target.value)} className={fieldClass} required>
              {decks.map((item) => <option key={item.deck_id} value={item.name}>{item.name}</option>)}
            </select>
          </label>
          <label className="block space-y-1"><span>{t("anki.front")}</span>
            <textarea autoFocus value={front} onChange={(event) => setFront(event.target.value)} rows={4} required className={fieldClass} />
          </label>
          <label className="block space-y-1"><span>{t("anki.back")}</span>
            <textarea value={back} onChange={(event) => setBack(event.target.value)} rows={5} required className={fieldClass} />
          </label>
          <label className="block space-y-1"><span>{t("anki.mode")}</span>
            <select value={mode} onChange={(event) => setMode(event.target.value as "normal" | "reverse")} className={fieldClass}>
              <option value="normal">{t("anki.normal")}</option><option value="reverse">{t("anki.reverse")}</option>
            </select>
          </label>
          <label className="block space-y-1"><span>{t("anki.tags")}</span>
            <input value={tags} onChange={(event) => setTags(event.target.value)} placeholder={t("anki.tagsPlaceholder")} className={fieldClass} />
          </label>
        </fieldset>
        {create.isError && <p role="alert" className="text-sm text-red-500">{errorMessage(create.error)}</p>}
        {!online && <p role="status" className="text-sm text-muted">{t("anki.offline")}</p>}
        <div className="flex justify-end gap-2 pb-[env(safe-area-inset-bottom)]">
          <button type="button" onClick={onClose} disabled={create.isPending} className="rounded-lg border border-border px-4 py-2">{t("common.cancel")}</button>
          <button type="submit" disabled={!online || !deck || !front.trim() || !back.trim() || create.isPending}
            className="rounded-lg bg-accent px-4 py-2 font-medium text-white">{t(create.isPending ? "anki.adding" : "anki.add")}</button>
        </div>
      </form>
    </dialog>
  );
}
