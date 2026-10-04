import { useState, type FormEvent } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Layers } from "lucide-react";

import { api, ApiError } from "@/lib/api";

interface Deck {
  id: number;
  name: string;
}

function errorMessage(error: Error): string {
  if (error instanceof ApiError && typeof error.detail === "object" && error.detail
      && "detail" in error.detail && typeof error.detail.detail === "string") {
    return error.detail.detail;
  }
  return error.message;
}

export default function AnkiView() {
  const { t } = useTranslation();
  const [deck, setDeck] = useState("");
  const [front, setFront] = useState("");
  const [back, setBack] = useState("");
  const [mode, setMode] = useState<"normal" | "reverse">("normal");
  const [tags, setTags] = useState("");
  const decks = useQuery({
    queryKey: ["anki-decks"],
    queryFn: () => api.get<Deck[]>("/api/anki/decks"),
    retry: false,
  });
  const selectedDeck = deck || decks.data?.[0]?.name || "";
  const validDeck = decks.data?.some((item) => item.name === selectedDeck) === true;
  const create = useMutation({
    mutationFn: () => api.post<{ note_id: number; cards_created: number }>("/api/anki/cards", {
      deck: selectedDeck,
      front: front.trim(),
      back: back.trim(),
      mode,
      tags: tags.split(/\s+/).filter(Boolean),
    }),
    onSuccess: () => {
      setFront("");
      setBack("");
    },
  });

  function submit(event: FormEvent) {
    event.preventDefault();
    if (!validDeck || !front.trim() || !back.trim() || create.isPending) return;
    create.mutate();
  }

  const fieldClass = "w-full rounded-lg border border-border bg-bg px-3 py-2 text-sm focus:border-accent focus:outline-none";

  return (
    <div className="flex h-full flex-col">
      <header className="flex items-center gap-3 border-b border-border bg-surface px-4 py-3">
        <Layers className="h-5 w-5 text-accent" />
        <h1 className="text-lg font-semibold">{t("anki.title")}</h1>
      </header>
      <div className="flex-1 overflow-y-auto p-4">
        <form onSubmit={submit} className="mx-auto max-w-2xl space-y-4">
          <p className="text-sm text-muted">{t("anki.description")}</p>
          <div>
            <div className="mb-1 flex items-center justify-between gap-2">
              <label htmlFor="anki-deck" className="text-sm font-medium">{t("anki.deck")}</label>
              <button type="button" onClick={() => void decks.refetch()} disabled={decks.isFetching || create.isPending}
                className="text-sm text-accent disabled:opacity-50">{t("anki.refresh")}</button>
            </div>
            <select id="anki-deck" value={selectedDeck} onChange={(event) => { setDeck(event.target.value); create.reset(); }}
              disabled={decks.isLoading || create.isPending || !decks.data?.length} required className={fieldClass}>
              {!decks.data?.length && <option value="">{t(decks.isLoading ? "common.loading" : "anki.noDecks")}</option>}
              {decks.data?.map((item) => <option key={item.id} value={item.name}>{item.name}</option>)}
            </select>
          </div>
          {decks.isError && <p role="alert" className="text-sm text-red-500">{errorMessage(decks.error)}</p>}
          <label className="block space-y-1">
            <span className="text-sm font-medium">{t("anki.front")}</span>
            <textarea value={front} onChange={(event) => { setFront(event.target.value); create.reset(); }}
              rows={4} required disabled={create.isPending} className={fieldClass} />
          </label>
          <label className="block space-y-1">
            <span className="text-sm font-medium">{t("anki.back")}</span>
            <textarea value={back} onChange={(event) => { setBack(event.target.value); create.reset(); }}
              rows={5} required disabled={create.isPending} className={fieldClass} />
          </label>
          <label className="block space-y-1">
            <span className="text-sm font-medium">{t("anki.mode")}</span>
            <select value={mode} onChange={(event) => { setMode(event.target.value as "normal" | "reverse"); create.reset(); }}
              disabled={create.isPending} className={fieldClass}>
              <option value="normal">{t("anki.normal")}</option>
              <option value="reverse">{t("anki.reverse")}</option>
            </select>
          </label>
          <label className="block space-y-1">
            <span className="text-sm font-medium">{t("anki.tags")}</span>
            <input value={tags} onChange={(event) => { setTags(event.target.value); create.reset(); }}
              placeholder={t("anki.tagsPlaceholder")} disabled={create.isPending} className={fieldClass} />
          </label>
          <button type="submit" disabled={!validDeck || !front.trim() || !back.trim() || create.isPending || decks.isError}
            className="rounded-lg bg-accent px-4 py-2 text-sm font-medium text-white disabled:opacity-50">
            {t(create.isPending ? "anki.adding" : "anki.add")}
          </button>
          {create.isError && <p role="alert" className="text-sm text-red-500">{errorMessage(create.error)}</p>}
          {create.isSuccess && <p role="status" className="text-sm text-accent">{t("anki.success", { count: create.data.cards_created })}</p>}
        </form>
      </div>
    </div>
  );
}
