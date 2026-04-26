// Anki tab. Three-pane layout:
//   - left:  decks (create / rename / delete) + Sync button
//   - center: notes list of the active deck OR the review session
//   - right: note editor (when a note is selected) or empty hint
//
// Sync is full upload OR full download — the user picks the direction
// in a small confirmation dialog. There is no incremental merge.

import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowDownToLine,
  ArrowUpFromLine,
  Pencil,
  Plus,
  RotateCw,
  Trash2,
} from "lucide-react";

import {
  api,
  ApiError,
  type AnkiCardForReview,
  type AnkiDeck,
  type AnkiNote,
  type AnkiNoteType,
  type AnkiReviewResult,
  type AnkiSyncStatus,
} from "@/lib/api";

type Mode =
  | { kind: "browse" }
  | { kind: "review" };

export default function AnkiView() {
  const { t } = useTranslation();
  const qc = useQueryClient();

  const [activeDeckId, setActiveDeckId] = useState<number | null>(null);
  const [selectedNoteId, setSelectedNoteId] = useState<number | null>(null);
  const [mode, setMode] = useState<Mode>({ kind: "browse" });
  const [editorState, setEditorState] = useState<EditorState | null>(null);
  const [syncDialogOpen, setSyncDialogOpen] = useState(false);

  // ---- Queries ------------------------------------------------------

  const decks = useQuery<AnkiDeck[], ApiError>({
    queryKey: ["anki-decks"],
    queryFn: () => api.get<AnkiDeck[]>("/api/anki/decks"),
    retry: false,
  });

  const notes = useQuery<AnkiNote[]>({
    queryKey: ["anki-notes", activeDeckId],
    queryFn: () =>
      api.get<AnkiNote[]>(`/api/anki/decks/${activeDeckId}/notes`),
    enabled: activeDeckId !== null && mode.kind === "browse",
  });

  const syncStatus = useQuery<AnkiSyncStatus>({
    queryKey: ["anki-sync-status"],
    queryFn: () => api.get<AnkiSyncStatus>("/api/anki/sync/status"),
  });

  // First load: pick the first deck if none selected.
  useEffect(() => {
    if (activeDeckId === null && decks.data && decks.data.length > 0) {
      setActiveDeckId(decks.data[0].id);
    }
  }, [activeDeckId, decks.data]);

  // ---- Disabled-feature short circuit -------------------------------

  if (decks.isError && decks.error?.status === 503) {
    return (
      <div className="flex h-full items-center justify-center p-8">
        <div className="max-w-md text-center">
          <h2 className="mb-2 text-lg font-semibold">{t("anki.disabledTitle")}</h2>
          <p className="text-muted">{t("anki.disabledBody")}</p>
        </div>
      </div>
    );
  }

  // ---- Mutations ----------------------------------------------------

  const createDeck = useMutation({
    mutationFn: (name: string) =>
      api.post<AnkiDeck>("/api/anki/decks", { name }),
    onSuccess: (d) => {
      qc.invalidateQueries({ queryKey: ["anki-decks"] });
      setActiveDeckId(d.id);
    },
  });

  const renameDeckMut = useMutation({
    mutationFn: ({ id, name }: { id: number; name: string }) =>
      api.patch<AnkiDeck>(`/api/anki/decks/${id}`, { name }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["anki-decks"] }),
  });

  const deleteDeckMut = useMutation({
    mutationFn: (id: number) => api.delete(`/api/anki/decks/${id}`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["anki-decks"] });
      qc.invalidateQueries({ queryKey: ["anki-notes"] });
      setActiveDeckId(null);
      setSelectedNoteId(null);
    },
  });

  const upsertNote = useMutation({
    mutationFn: async (state: EditorState) => {
      if (state.id !== null) {
        return api.patch<AnkiNote>(`/api/anki/notes/${state.id}`, {
          fields: [state.front, state.back],
          tags: parseTags(state.tags),
        });
      }
      return api.post<AnkiNote>("/api/anki/notes", {
        deck_id: activeDeckId,
        notetype: state.notetype,
        fields: [state.front, state.back],
        tags: parseTags(state.tags),
      });
    },
    onSuccess: (n) => {
      qc.invalidateQueries({ queryKey: ["anki-notes", activeDeckId] });
      qc.invalidateQueries({ queryKey: ["anki-decks"] });
      setSelectedNoteId(n.id);
      setEditorState(null);
    },
  });

  const deleteNoteMut = useMutation({
    mutationFn: (id: number) => api.delete(`/api/anki/notes/${id}`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["anki-notes", activeDeckId] });
      qc.invalidateQueries({ queryKey: ["anki-decks"] });
      setSelectedNoteId(null);
      setEditorState(null);
    },
  });

  // ---- Layout -------------------------------------------------------

  const activeDeck = decks.data?.find((d) => d.id === activeDeckId) ?? null;

  return (
    <div className="grid h-full grid-cols-[16rem_22rem_1fr] overflow-hidden">
      <DeckSidebar
        decks={decks.data ?? []}
        activeDeckId={activeDeckId}
        onSelectDeck={(id) => {
          setActiveDeckId(id);
          setSelectedNoteId(null);
          setMode({ kind: "browse" });
          setEditorState(null);
        }}
        onCreate={(name) => createDeck.mutate(name)}
        onRename={(id, name) => renameDeckMut.mutate({ id, name })}
        onDelete={(id) => deleteDeckMut.mutate(id)}
        sync={syncStatus.data ?? null}
        onSync={() => setSyncDialogOpen(true)}
      />

      <div className="flex h-full min-w-0 flex-col border-r border-border bg-bg">
        {mode.kind === "review" && activeDeck ? (
          <ReviewPane
            deck={activeDeck}
            onExit={() => {
              setMode({ kind: "browse" });
              qc.invalidateQueries({ queryKey: ["anki-decks"] });
            }}
          />
        ) : activeDeck ? (
          <NoteList
            deck={activeDeck}
            notes={notes.data ?? []}
            loading={notes.isLoading}
            selectedId={selectedNoteId}
            onSelect={(id) => {
              setSelectedNoteId(id);
              setEditorState(null);
            }}
            onAdd={() => {
              setSelectedNoteId(null);
              setEditorState({ id: null, notetype: "basic", front: "", back: "", tags: "" });
            }}
            onReview={() => setMode({ kind: "review" })}
          />
        ) : (
          <div className="flex h-full items-center justify-center text-muted">
            {t("anki.noDecks")}
          </div>
        )}
      </div>

      <div className="min-w-0 overflow-y-auto bg-surface">
        <NoteEditor
          state={editorState}
          note={
            editorState === null && selectedNoteId !== null
              ? (notes.data ?? []).find((n) => n.id === selectedNoteId) ?? null
              : null
          }
          onEdit={(n) =>
            setEditorState({
              id: n.id,
              notetype: n.notetype,
              front: n.fields[0] ?? "",
              back: n.fields[1] ?? "",
              tags: n.tags.join(" "),
            })
          }
          onChange={setEditorState}
          onSave={(s) => upsertNote.mutate(s)}
          onCancel={() => setEditorState(null)}
          onDelete={(id) => {
            if (confirm(t("anki.deleteNoteConfirm"))) {
              deleteNoteMut.mutate(id);
            }
          }}
          isSaving={upsertNote.isPending}
        />
      </div>

      {syncDialogOpen && (
        <SyncDialog
          status={syncStatus.data ?? null}
          onClose={() => setSyncDialogOpen(false)}
          onDone={() => {
            qc.invalidateQueries({ queryKey: ["anki-sync-status"] });
            qc.invalidateQueries({ queryKey: ["anki-decks"] });
            qc.invalidateQueries({ queryKey: ["anki-notes"] });
          }}
        />
      )}
    </div>
  );
}

// ── Editor state ─────────────────────────────────────────────────────

interface EditorState {
  id: number | null;
  notetype: AnkiNoteType;
  front: string;
  back: string;
  tags: string;
}

function parseTags(s: string): string[] {
  return s.trim().split(/\s+/).filter(Boolean);
}

// ── Deck sidebar ─────────────────────────────────────────────────────

function DeckSidebar({
  decks,
  activeDeckId,
  onSelectDeck,
  onCreate,
  onRename,
  onDelete,
  sync,
  onSync,
}: {
  decks: AnkiDeck[];
  activeDeckId: number | null;
  onSelectDeck: (id: number) => void;
  onCreate: (name: string) => void;
  onRename: (id: number, name: string) => void;
  onDelete: (id: number) => void;
  sync: AnkiSyncStatus | null;
  onSync: () => void;
}) {
  const { t } = useTranslation();
  const [creating, setCreating] = useState(false);
  const [newName, setNewName] = useState("");
  const [renamingId, setRenamingId] = useState<number | null>(null);
  const [renameValue, setRenameValue] = useState("");

  function submitCreate(e: React.FormEvent) {
    e.preventDefault();
    const v = newName.trim();
    if (v) {
      onCreate(v);
      setNewName("");
      setCreating(false);
    }
  }

  function submitRename(e: React.FormEvent) {
    e.preventDefault();
    if (renamingId !== null) {
      const v = renameValue.trim();
      if (v) onRename(renamingId, v);
      setRenamingId(null);
      setRenameValue("");
    }
  }

  return (
    <aside className="flex h-full flex-col border-r border-border bg-surface">
      <div className="flex items-center justify-between border-b border-border px-4 py-3">
        <h2 className="text-sm font-semibold uppercase tracking-wide text-muted">
          {t("anki.decksHeader")}
        </h2>
        <button
          onClick={() => setCreating((v) => !v)}
          title={t("anki.newDeck")}
          aria-label={t("anki.newDeck")}
          className="rounded p-1 text-muted hover:bg-bg hover:text-text"
        >
          <Plus className="h-4 w-4" />
        </button>
      </div>

      {creating && (
        <form onSubmit={submitCreate} className="border-b border-border p-3">
          <input
            autoFocus
            value={newName}
            onChange={(e) => setNewName(e.target.value)}
            placeholder={t("anki.newDeckPlaceholder")}
            className="w-full rounded border border-border bg-bg px-2 py-1 text-sm"
          />
        </form>
      )}

      <ul className="flex-1 overflow-y-auto py-2">
        {decks.length === 0 ? (
          <li className="px-4 py-2 text-sm text-muted">{t("anki.noDecks")}</li>
        ) : (
          decks.map((d) => {
            const isActive = d.id === activeDeckId;
            const isRenaming = d.id === renamingId;
            return (
              <li key={d.id} className="group px-2">
                {isRenaming ? (
                  <form onSubmit={submitRename} className="px-2 py-1">
                    <input
                      autoFocus
                      value={renameValue}
                      onChange={(e) => setRenameValue(e.target.value)}
                      onBlur={() => setRenamingId(null)}
                      placeholder={t("anki.renameDeckPlaceholder")}
                      className="w-full rounded border border-border bg-bg px-2 py-1 text-sm"
                    />
                  </form>
                ) : (
                  <button
                    onClick={() => onSelectDeck(d.id)}
                    className={`flex w-full items-start justify-between rounded px-2 py-1.5 text-left text-sm ${
                      isActive ? "bg-bg text-accent" : "text-text hover:bg-bg"
                    }`}
                  >
                    <div className="flex min-w-0 flex-1 flex-col">
                      <span className="truncate font-medium">{d.name}</span>
                      <span className="text-[11px] text-muted">
                        {t("anki.deckCounts", { total: d.card_count, due: d.due_count, neww: d.new_count })}
                      </span>
                    </div>
                    {isActive && (
                      <span className="ml-2 flex shrink-0 items-center gap-1 opacity-0 group-hover:opacity-100">
                        <button
                          onClick={(e) => {
                            e.stopPropagation();
                            setRenamingId(d.id);
                            setRenameValue(d.name);
                          }}
                          title={t("anki.renameDeck")}
                          aria-label={t("anki.renameDeck")}
                          className="rounded p-1 text-muted hover:bg-surface hover:text-text"
                        >
                          <Pencil className="h-3.5 w-3.5" />
                        </button>
                        {d.id !== 1 && (
                          <button
                            onClick={(e) => {
                              e.stopPropagation();
                              if (confirm(t("anki.deleteDeckConfirm"))) onDelete(d.id);
                            }}
                            title={t("common.delete")}
                            aria-label={t("common.delete")}
                            className="rounded p-1 text-muted hover:bg-surface hover:text-red-500"
                          >
                            <Trash2 className="h-3.5 w-3.5" />
                          </button>
                        )}
                      </span>
                    )}
                  </button>
                )}
              </li>
            );
          })
        )}
      </ul>

      <div className="border-t border-border p-3">
        <button
          onClick={onSync}
          className="flex w-full items-center justify-center gap-2 rounded-lg border border-border bg-bg px-3 py-2 text-sm hover:bg-surface"
        >
          <RotateCw className="h-4 w-4" />
          {t("anki.sync")}
        </button>
        <p className="mt-2 text-[11px] text-muted">
          {sync?.last_sync_ms
            ? t("anki.lastSync", { when: new Date(sync.last_sync_ms).toLocaleString() })
            : t("anki.neverSynced")}
        </p>
        {sync?.last_error && (
          <p className="mt-1 text-[11px] text-red-500">{sync.last_error}</p>
        )}
      </div>
    </aside>
  );
}

// ── Note list ────────────────────────────────────────────────────────

function NoteList({
  deck,
  notes,
  loading,
  selectedId,
  onSelect,
  onAdd,
  onReview,
}: {
  deck: AnkiDeck;
  notes: AnkiNote[];
  loading: boolean;
  selectedId: number | null;
  onSelect: (id: number) => void;
  onAdd: () => void;
  onReview: () => void;
}) {
  const { t } = useTranslation();
  const dueOrNew = deck.due_count + deck.new_count;

  return (
    <div className="flex h-full flex-col">
      <header className="flex items-center justify-between border-b border-border px-4 py-3">
        <div className="min-w-0">
          <h3 className="truncate text-base font-semibold">{deck.name}</h3>
          <p className="text-[11px] text-muted">
            {t("anki.deckCounts", { total: deck.card_count, due: deck.due_count, neww: deck.new_count })}
          </p>
        </div>
        <div className="flex shrink-0 gap-2">
          <button
            onClick={onReview}
            disabled={dueOrNew === 0}
            className="rounded-lg border border-border bg-bg px-3 py-1.5 text-xs hover:bg-surface disabled:opacity-50"
          >
            {t("anki.review")} {dueOrNew > 0 ? `(${dueOrNew})` : ""}
          </button>
          <button
            onClick={onAdd}
            className="rounded-lg bg-accent px-3 py-1.5 text-xs text-white hover:opacity-90"
          >
            <span className="inline-flex items-center gap-1">
              <Plus className="h-3.5 w-3.5" />
              {t("anki.addNote")}
            </span>
          </button>
        </div>
      </header>
      <ul className="flex-1 overflow-y-auto">
        {loading ? (
          <li className="p-4 text-sm text-muted">{t("common.loading")}</li>
        ) : notes.length === 0 ? (
          <li className="p-4 text-sm text-muted">{t("anki.noNotes")}</li>
        ) : (
          notes.map((n) => {
            const isActive = n.id === selectedId;
            return (
              <li key={n.id}>
                <button
                  onClick={() => onSelect(n.id)}
                  className={`block w-full border-b border-border px-4 py-2 text-left text-sm ${
                    isActive ? "bg-bg text-accent" : "hover:bg-bg"
                  }`}
                >
                  <div className="line-clamp-1 font-medium">{n.fields[0]}</div>
                  <div className="line-clamp-1 text-[12px] text-muted">{n.fields[1]}</div>
                  {n.tags.length > 0 && (
                    <div className="mt-1 flex flex-wrap gap-1">
                      {n.tags.map((tag) => (
                        <span
                          key={tag}
                          className="rounded-full border border-border bg-surface px-1.5 py-0.5 text-[10px] text-muted"
                        >
                          {tag}
                        </span>
                      ))}
                    </div>
                  )}
                </button>
              </li>
            );
          })
        )}
      </ul>
    </div>
  );
}

// ── Note editor ──────────────────────────────────────────────────────

function NoteEditor({
  state,
  note,
  onEdit,
  onChange,
  onSave,
  onCancel,
  onDelete,
  isSaving,
}: {
  state: EditorState | null;
  note: AnkiNote | null;
  onEdit: (n: AnkiNote) => void;
  onChange: (s: EditorState) => void;
  onSave: (s: EditorState) => void;
  onCancel: () => void;
  onDelete: (id: number) => void;
  isSaving: boolean;
}) {
  const { t } = useTranslation();

  if (state === null && note === null) {
    return (
      <div className="flex h-full items-center justify-center p-8 text-muted">
        {t("anki.selectNote")}
      </div>
    );
  }

  if (state === null && note !== null) {
    // View mode
    return (
      <div className="flex h-full flex-col">
        <header className="flex items-center justify-between border-b border-border px-4 py-3">
          <p className="text-sm text-muted">
            {note.notetype === "basic_reverse"
              ? t("anki.noteTypeBasicReverse")
              : t("anki.noteTypeBasic")}
            {" · "}
            {note.cards.length} {note.cards.length === 1 ? "card" : "cards"}
          </p>
          <div className="flex gap-2">
            <button
              onClick={() => onEdit(note)}
              className="rounded-lg border border-border px-3 py-1.5 text-xs hover:bg-bg"
            >
              {t("common.edit")}
            </button>
            <button
              onClick={() => onDelete(note.id)}
              className="rounded-lg border border-red-500/40 px-3 py-1.5 text-xs text-red-500 hover:bg-red-500/10"
            >
              {t("common.delete")}
            </button>
          </div>
        </header>
        <div className="flex-1 overflow-y-auto p-4">
          <FieldRow label={t("anki.noteFront")} value={note.fields[0] ?? ""} />
          <FieldRow label={t("anki.noteBack")} value={note.fields[1] ?? ""} />
          {note.tags.length > 0 && (
            <div className="mt-4">
              <p className="mb-1 text-xs uppercase tracking-wide text-muted">
                {t("anki.noteTags")}
              </p>
              <div className="flex flex-wrap gap-1">
                {note.tags.map((tag) => (
                  <span
                    key={tag}
                    className="rounded-full border border-border bg-bg px-2 py-0.5 text-xs"
                  >
                    {tag}
                  </span>
                ))}
              </div>
            </div>
          )}
        </div>
      </div>
    );
  }

  // Editor mode
  if (state === null) return null;
  const isNew = state.id === null;

  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        onSave(state);
      }}
      className="flex h-full flex-col"
    >
      <div className="flex-1 space-y-4 overflow-y-auto p-4">
        {isNew && (
          <label className="block">
            <span className="mb-1 block text-xs uppercase tracking-wide text-muted">
              Type
            </span>
            <select
              value={state.notetype}
              onChange={(e) =>
                onChange({ ...state, notetype: e.target.value as AnkiNoteType })
              }
              className="w-full rounded border border-border bg-bg px-2 py-1 text-sm"
            >
              <option value="basic">{t("anki.noteTypeBasic")}</option>
              <option value="basic_reverse">{t("anki.noteTypeBasicReverse")}</option>
            </select>
          </label>
        )}
        <label className="block">
          <span className="mb-1 block text-xs uppercase tracking-wide text-muted">
            {t("anki.noteFront")}
          </span>
          <textarea
            autoFocus
            rows={3}
            value={state.front}
            onChange={(e) => onChange({ ...state, front: e.target.value })}
            className="w-full rounded border border-border bg-bg px-2 py-1 text-sm"
          />
        </label>
        <label className="block">
          <span className="mb-1 block text-xs uppercase tracking-wide text-muted">
            {t("anki.noteBack")}
          </span>
          <textarea
            rows={3}
            value={state.back}
            onChange={(e) => onChange({ ...state, back: e.target.value })}
            className="w-full rounded border border-border bg-bg px-2 py-1 text-sm"
          />
        </label>
        <label className="block">
          <span className="mb-1 block text-xs uppercase tracking-wide text-muted">
            {t("anki.noteTags")}
          </span>
          <input
            value={state.tags}
            onChange={(e) => onChange({ ...state, tags: e.target.value })}
            className="w-full rounded border border-border bg-bg px-2 py-1 text-sm"
          />
        </label>
      </div>
      <div className="flex items-center justify-end gap-2 border-t border-border px-4 py-3">
        <button
          type="button"
          onClick={onCancel}
          className="rounded-lg border border-border px-3 py-1.5 text-sm hover:bg-bg"
        >
          {t("common.cancel")}
        </button>
        <button
          type="submit"
          disabled={isSaving || !state.front.trim() || !state.back.trim()}
          className="rounded-lg bg-accent px-3 py-1.5 text-sm text-white hover:opacity-90 disabled:opacity-50"
        >
          {t("anki.saveNote")}
        </button>
      </div>
    </form>
  );
}

function FieldRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="mb-4">
      <p className="mb-1 text-xs uppercase tracking-wide text-muted">{label}</p>
      <div
        className="whitespace-pre-wrap rounded border border-border bg-bg px-3 py-2 text-sm"
        dangerouslySetInnerHTML={{ __html: value }}
      />
    </div>
  );
}

// ── Review pane ──────────────────────────────────────────────────────

function ReviewPane({ deck, onExit }: { deck: AnkiDeck; onExit: () => void }) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const [showAnswer, setShowAnswer] = useState(false);
  const cardStartedAt = useRef<number>(Date.now());

  const next = useQuery<AnkiCardForReview | null>({
    queryKey: ["anki-next", deck.id],
    queryFn: () =>
      api.get<AnkiCardForReview | null>(`/api/anki/review/${deck.id}/next`),
  });

  useEffect(() => {
    setShowAnswer(false);
    cardStartedAt.current = Date.now();
  }, [next.data?.card.id]);

  const answer = useMutation({
    mutationFn: ({ cardId, ease }: { cardId: number; ease: number }) =>
      api.post<AnkiReviewResult>(`/api/anki/review/${cardId}`, {
        ease,
        time_ms: Date.now() - cardStartedAt.current,
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["anki-next", deck.id] });
    },
  });

  // Keyboard shortcuts: Space = show answer, 1..4 = ease.
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement) return;
      const card = next.data?.card;
      if (!card) return;
      if (!showAnswer) {
        if (e.code === "Space" || e.key === " ") {
          e.preventDefault();
          setShowAnswer(true);
        }
        return;
      }
      const ease = ({ "1": 1, "2": 2, "3": 3, "4": 4 } as Record<string, number>)[e.key];
      if (ease) {
        e.preventDefault();
        answer.mutate({ cardId: card.id, ease });
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [showAnswer, next.data, answer]);

  if (next.isLoading) {
    return (
      <div className="flex h-full items-center justify-center text-muted">
        {t("common.loading")}
      </div>
    );
  }

  if (!next.data) {
    return (
      <div className="flex h-full flex-col">
        <header className="flex items-center justify-between border-b border-border px-4 py-3">
          <h3 className="text-base font-semibold">{deck.name}</h3>
          <button
            onClick={onExit}
            className="rounded-lg border border-border px-3 py-1.5 text-xs hover:bg-bg"
          >
            {t("anki.exitReview")}
          </button>
        </header>
        <div className="flex flex-1 items-center justify-center text-muted">
          {t("anki.reviewEmpty")}
        </div>
      </div>
    );
  }

  const card = next.data;

  return (
    <div className="flex h-full flex-col">
      <header className="flex items-center justify-between border-b border-border px-4 py-3">
        <h3 className="text-base font-semibold">{deck.name}</h3>
        <button
          onClick={onExit}
          className="rounded-lg border border-border px-3 py-1.5 text-xs hover:bg-bg"
        >
          {t("anki.exitReview")}
        </button>
      </header>
      <div className="flex flex-1 flex-col items-stretch overflow-y-auto px-4 py-6">
        <div className="mx-auto w-full max-w-2xl flex-1">
          <div
            className="rounded-lg border border-border bg-surface p-6 text-lg"
            dangerouslySetInnerHTML={{ __html: card.front_html }}
          />
          {showAnswer && (
            <>
              <hr className="my-4 border-border" />
              <div
                className="rounded-lg border border-border bg-surface p-6 text-lg"
                dangerouslySetInnerHTML={{ __html: card.back_html }}
              />
            </>
          )}
        </div>
        <div className="mt-6 flex justify-center gap-2">
          {!showAnswer ? (
            <button
              onClick={() => setShowAnswer(true)}
              className="rounded-lg bg-accent px-4 py-2 text-sm text-white hover:opacity-90"
            >
              {t("anki.showAnswer")}
            </button>
          ) : (
            <>
              {[1, 2, 3, 4].map((e) => (
                <button
                  key={e}
                  onClick={() => answer.mutate({ cardId: card.card.id, ease: e })}
                  disabled={answer.isPending}
                  className={`rounded-lg border px-4 py-2 text-sm font-medium ${
                    e === 1
                      ? "border-red-500/40 bg-red-500/10 text-red-500"
                      : e === 2
                      ? "border-yellow-500/40 bg-yellow-500/10 text-yellow-700"
                      : e === 3
                      ? "border-green-500/40 bg-green-500/10 text-green-700"
                      : "border-blue-500/40 bg-blue-500/10 text-blue-700"
                  }`}
                >
                  {t(`anki.ease${e}`)} <span className="text-xs opacity-60">({e})</span>
                </button>
              ))}
            </>
          )}
        </div>
      </div>
    </div>
  );
}

// ── Sync dialog ──────────────────────────────────────────────────────

function SyncDialog({
  status,
  onClose,
  onDone,
}: {
  status: AnkiSyncStatus | null;
  onClose: () => void;
  onDone: () => void;
}) {
  const { t } = useTranslation();
  const [busy, setBusy] = useState<"upload" | "download" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [okMsg, setOkMsg] = useState<string | null>(null);

  async function run(direction: "upload" | "download") {
    setBusy(direction);
    setError(null);
    setOkMsg(null);
    try {
      await api.post(`/api/anki/sync/${direction}`);
      setOkMsg(t("anki.syncOk"));
      onDone();
    } catch (err: unknown) {
      const detail =
        err instanceof ApiError
          ? typeof err.detail === "object" && err.detail && "detail" in err.detail
            ? String((err.detail as { detail: unknown }).detail)
            : String(err.detail ?? err.message)
          : String((err as Error)?.message ?? err);
      setError(detail);
    } finally {
      setBusy(null);
    }
  }

  const localMod = status?.local_mod_ms
    ? new Date(status.local_mod_ms).toLocaleString()
    : "—";

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
      <div className="w-full max-w-md rounded-lg border border-border bg-surface p-5 shadow-lg">
        <h3 className="text-base font-semibold">{t("anki.sync")}</h3>
        <p className="mt-2 text-sm text-muted">{t("anki.syncDirection")}</p>
        <p className="mt-2 text-xs text-muted">
          Local: <span className="font-mono">{localMod}</span>
        </p>
        {error && <p className="mt-3 text-sm text-red-500">{t("anki.syncFailed", { err: error })}</p>}
        {okMsg && <p className="mt-3 text-sm text-green-600">{okMsg}</p>}
        <div className="mt-4 flex flex-col gap-2">
          <button
            onClick={() => run("upload")}
            disabled={busy !== null}
            className="flex items-center justify-center gap-2 rounded-lg border border-border bg-bg px-3 py-2 text-sm hover:bg-bg/80 disabled:opacity-50"
          >
            <ArrowUpFromLine className="h-4 w-4" />
            {busy === "upload" ? t("anki.syncing") : t("anki.syncUpload")}
          </button>
          <button
            onClick={() => run("download")}
            disabled={busy !== null}
            className="flex items-center justify-center gap-2 rounded-lg border border-border bg-bg px-3 py-2 text-sm hover:bg-bg/80 disabled:opacity-50"
          >
            <ArrowDownToLine className="h-4 w-4" />
            {busy === "download" ? t("anki.syncing") : t("anki.syncDownload")}
          </button>
        </div>
        <div className="mt-4 flex justify-end">
          <button
            onClick={onClose}
            className="rounded-lg px-3 py-1.5 text-sm text-muted hover:bg-bg"
          >
            {t("common.cancel")}
          </button>
        </div>
      </div>
    </div>
  );
}

