// Wiki view: tree on the left, rendered note in the center, backlinks on the
// right. Search bar above the tree.

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ChevronLeft,
  ChevronRight,
  Home,
  Link2,
  Menu,
  MessageSquare,
  Pencil,
  X,
} from "lucide-react";

import {
  api,
  type TrainingConfigResponse,
  type TreeEntry,
  type VaultNote,
} from "@/lib/api";
import VaultTree from "./VaultTree";
import NoteRenderer from "./NoteRenderer";
import Backlinks from "./Backlinks";
import SearchBar from "./SearchBar";
import WikiEditor from "./WikiEditor";
import FolderIndex from "./FolderIndex";
import WikiSelectionChat from "./WikiSelectionChat";
import TrainingExpandModal from "./TrainingExpandModal";

export interface WikiTarget {
  path: string | null;
  nonce: number;
}

interface Props {
  target?: WikiTarget | null;
  onOpenChat?: () => void;
}

function buildPageChatDraft(path: string): string {
  return (
    `I'd like to discuss this Wiki page: \`${path}\`. Use ` +
    `\`vault.read("${path}")\` to load the content when you need it.`
  );
}

export default function WikiView({ target, onOpenChat }: Props) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  // null = vault home (root folder index). "" is reserved as a synonym for null.
  // A non-empty string is either a file path or a folder path.
  const [activePath, setActivePath] = useState<string | null>(null);
  const [past, setPast] = useState<(string | null)[]>([]);
  const [future, setFuture] = useState<(string | null)[]>([]);
  const [editing, setEditing] = useState(false);
  const [treeOpen, setTreeOpen] = useState(false);
  const [backlinksOpen, setBacklinksOpen] = useState(false);

  // Browser-style navigation: every entry-point should call `navigate` so
  // back/forward stay coherent. The arrow buttons use `goBack` / `goForward`,
  // which traverse the stacks WITHOUT touching the future stack.
  const navigate = useCallback(
    (target: string | null) => {
      setActivePath((prev) => {
        if (prev === target) return prev;
        setPast((p) => [...p, prev]);
        setFuture([]);
        return target;
      });
    },
    [],
  );

  const goBack = useCallback(() => {
    setPast((p) => {
      if (p.length === 0) return p;
      const prev = p[p.length - 1];
      setFuture((f) => [activePath, ...f]);
      setActivePath(prev);
      return p.slice(0, -1);
    });
  }, [activePath]);

  const goForward = useCallback(() => {
    setFuture((f) => {
      if (f.length === 0) return f;
      const next = f[0];
      setPast((p) => [...p, activePath]);
      setActivePath(next);
      return f.slice(1);
    });
  }, [activePath]);

  // Alt+Left / Alt+Right keyboard shortcuts (browser convention).
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (!e.altKey || e.metaKey || e.ctrlKey) return;
      if (e.key === "ArrowLeft") {
        e.preventDefault();
        goBack();
      } else if (e.key === "ArrowRight") {
        e.preventDefault();
        goForward();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [goBack, goForward]);

  // Switching notes always exits edit mode and closes mobile drawers.
  useEffect(() => {
    setEditing(false);
    setTreeOpen(false);
    setBacklinksOpen(false);
  }, [activePath]);

  // Floating "chat about selection" toolbar. We track which text the user
  // has selected inside the rendered note so a click on the toolbar can
  // open the selection-chat popup with that excerpt as context.
  const noteContainerRef = useRef<HTMLDivElement | null>(null);
  const [selection, setSelection] = useState<{
    text: string;
    rect: DOMRect;
  } | null>(null);
  // The selection text frozen at the moment the user opened the chat
  // popup. Held independently of `selection` so the popup keeps its
  // context even when the live selection clears (closing the popup,
  // clicking elsewhere).
  const [selectionChat, setSelectionChat] = useState<{
    path: string;
    text: string;
  } | null>(null);

  useEffect(() => {
    let pending: number | null = null;
    function handle() {
      if (pending !== null) cancelAnimationFrame(pending);
      pending = requestAnimationFrame(() => {
        pending = null;
        const sel = window.getSelection();
        if (!sel || sel.isCollapsed || sel.rangeCount === 0) {
          setSelection(null);
          return;
        }
        const text = sel.toString().trim();
        if (!text) {
          setSelection(null);
          return;
        }
        const range = sel.getRangeAt(0);
        const container = noteContainerRef.current;
        if (!container || !container.contains(range.commonAncestorContainer)) {
          setSelection(null);
          return;
        }
        const rect = range.getBoundingClientRect();
        if (rect.width === 0 && rect.height === 0) {
          setSelection(null);
          return;
        }
        setSelection({ text, rect });
      });
    }
    document.addEventListener("selectionchange", handle);
    return () => {
      document.removeEventListener("selectionchange", handle);
      if (pending !== null) cancelAnimationFrame(pending);
    };
  }, []);

  // Drop the selection box when navigating away or entering edit mode
  // (the editor manages its own selection).
  useEffect(() => {
    setSelection(null);
  }, [activePath, editing]);

  // External "open this wiki path" requests (e.g. clicking a wikilink in
  // chat). Tracked by nonce so the same path can re-trigger.
  const lastNonceRef = useRef<number | null>(null);
  useEffect(() => {
    if (!target || lastNonceRef.current === target.nonce) return;
    lastNonceRef.current = target.nonce;
    navigate(target.path);
    // navigate is stable (useCallback with [] deps).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [target?.nonce]);

  const tree = useQuery({
    queryKey: ["vault-tree"],
    queryFn: () => api.get<TreeEntry[]>("/api/vault/tree"),
  });

  // Training config — knows which subtree is the training folder so we
  // can enable the "generate fiche" modal only on dead wikilinks inside
  // it. Loaded once and cached forever (config changes need a restart).
  const trainingCfg = useQuery({
    queryKey: ["training-config"],
    queryFn: () => api.get<TrainingConfigResponse>("/api/training/config"),
    staleTime: Infinity,
  });
  const trainingFolder = (trainingCfg.data?.training_folder ?? "Training")
    .replace(/^\/+|\/+$/g, "");
  const isUnderTraining = (p: string | null | undefined): boolean =>
    !!p && (p === trainingFolder || p.startsWith(`${trainingFolder}/`));

  // The dead wikilink the user just clicked — opens the training modal
  // when set. Holds the target concept (the bracket text, no path / no
  // .md) and the parent fiche path the click came from.
  const [trainingPrompt, setTrainingPrompt] = useState<{
    target: string;
    parent: string;
  } | null>(null);

  // Resolve whether activePath is a file or a folder.
  const activeKind = useMemo<"file" | "folder" | null>(() => {
    if (!activePath) return null;
    const e = tree.data?.find((x) => x.path === activePath);
    return (e?.type as "file" | "folder" | undefined) ?? null;
  }, [activePath, tree.data]);

  const note = useQuery<VaultNote | null>({
    queryKey: ["vault-note", activePath],
    queryFn: async () =>
      activePath && activeKind === "file"
        ? api.get<VaultNote>(`/api/vault/note?path=${encodeURIComponent(activePath)}`)
        : null,
    enabled: activePath !== null && activeKind === "file",
  });

  // Chat-about-page / chat-about-selection handlers. Both write the draft
  // to localStorage (the chat Composer reads it on mount) and switch the
  // active view to chat.
  const startChatAboutPage = useCallback(() => {
    if (!note.data || !onOpenChat) return;
    window.localStorage.setItem(
      "sb.chat.draft",
      buildPageChatDraft(note.data.path),
    );
    onOpenChat();
  }, [note.data, onOpenChat]);

  const startChatAboutSelection = useCallback(() => {
    if (!note.data || !selection) return;
    // Freeze the selection text into the popup state, then dismiss the
    // floating toolbar (clears the live browser selection too — the
    // user is now interacting with the dialog, not the page).
    setSelectionChat({ path: note.data.path, text: selection.text });
    setSelection(null);
    window.getSelection()?.removeAllRanges();
  }, [note.data, selection]);

  const treeAside = (
    <>
      <SearchBar onOpen={navigate} />
      <div className="flex-1 overflow-y-auto">
        {tree.isLoading ? (
          <p className="px-3 py-2 text-xs text-muted">{t("common.loading")}</p>
        ) : tree.isError ? (
          <p className="px-3 py-2 text-xs text-red-400">
            {(tree.error as Error)?.message ?? "error"}
          </p>
        ) : (
          <VaultTree
            entries={tree.data ?? []}
            activePath={activePath}
            onSelect={navigate}
          />
        )}
      </div>
    </>
  );

  return (
    <div className="flex h-full">
      {/* Tree (desktop: always visible; mobile: drawer) */}
      <aside className="hidden w-64 shrink-0 flex-col border-r border-border bg-surface md:flex">
        {treeAside}
      </aside>

      {treeOpen && (
        <div
          className="fixed inset-0 z-40 bg-black/60 md:hidden"
          onClick={() => setTreeOpen(false)}
        >
          <aside
            className="flex h-full w-72 flex-col border-r border-border bg-surface"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center justify-between border-b border-border px-3 py-2">
              <span className="text-sm font-medium">{t("wiki.title")}</span>
              <button
                type="button"
                onClick={() => setTreeOpen(false)}
                className="text-muted"
                aria-label={t("common.cancel")}
              >
                <X className="h-5 w-5" />
              </button>
            </div>
            {treeAside}
          </aside>
        </div>
      )}

      <main className="flex h-full min-w-0 flex-1 flex-col">
        <header className="flex items-center gap-2 border-b border-border bg-surface px-3 py-2 md:px-4">
          <button
            type="button"
            onClick={() => setTreeOpen(true)}
            className="text-muted md:hidden"
            aria-label={t("wiki.title")}
          >
            <Menu className="h-5 w-5" />
          </button>
          <div className="flex items-center gap-0.5">
            <button
              type="button"
              onClick={goBack}
              disabled={past.length === 0}
              className="flex items-center justify-center rounded px-1.5 py-1 text-muted hover:bg-bg hover:text-text disabled:cursor-not-allowed disabled:opacity-30 disabled:hover:bg-transparent disabled:hover:text-muted"
              title={t("wiki.back") + " (Alt+←)"}
              aria-label={t("wiki.back")}
            >
              <ChevronLeft className="h-4 w-4" />
            </button>
            <button
              type="button"
              onClick={() => navigate(null)}
              className={`flex items-center justify-center rounded px-1.5 py-1 hover:bg-bg hover:text-text ${activePath === null ? "text-accent" : "text-muted"}`}
              title={t("wiki.vaultRoot")}
              aria-label={t("wiki.vaultRoot")}
            >
              <Home className="h-4 w-4" />
            </button>
            <button
              type="button"
              onClick={goForward}
              disabled={future.length === 0}
              className="flex items-center justify-center rounded px-1.5 py-1 text-muted hover:bg-bg hover:text-text disabled:cursor-not-allowed disabled:opacity-30 disabled:hover:bg-transparent disabled:hover:text-muted"
              title={t("wiki.forward") + " (Alt+→)"}
              aria-label={t("wiki.forward")}
            >
              <ChevronRight className="h-4 w-4" />
            </button>
          </div>
          <Crumbs path={activePath} onCrumb={navigate} />
          {activeKind === "file" && note.data && !editing && (
            <>
              <button
                type="button"
                onClick={() => setBacklinksOpen(true)}
                className="flex items-center gap-1 rounded border border-border px-2 py-1 text-xs text-muted hover:border-accent hover:text-text lg:hidden"
                aria-label={t("wiki.backlinks")}
              >
                <Link2 className="h-3.5 w-3.5" />
                <span>{note.data.backlinks.length}</span>
              </button>
              {onOpenChat && (
                <button
                  type="button"
                  onClick={startChatAboutPage}
                  className="flex items-center gap-1 rounded border border-accent bg-accent/10 px-2 py-1 text-xs text-accent hover:bg-accent/20"
                  title={t("wiki.chatAboutPageHint")}
                >
                  <MessageSquare className="h-3.5 w-3.5" />
                  <span className="hidden sm:inline">{t("wiki.chatAboutPage")}</span>
                </button>
              )}
              <button
                type="button"
                onClick={() => setEditing(true)}
                className="flex items-center gap-1 rounded border border-border px-2 py-1 text-xs text-muted hover:border-accent hover:text-text"
              >
                <Pencil className="h-3.5 w-3.5" />
                <span className="hidden sm:inline">{t("common.edit")}</span>
              </button>
            </>
          )}
        </header>

        <div className="flex flex-1 min-h-0">
          <div className="flex flex-1 min-w-0 flex-col">
            {editing && note.data ? (
              <WikiEditor
                path={note.data.path}
                initialContent={note.data.content}
                onSaved={() => {
                  setEditing(false);
                  qc.invalidateQueries({ queryKey: ["vault-note", activePath] });
                }}
                onCancel={() => setEditing(false)}
              />
            ) : activeKind === "file" ? (
              note.isLoading ? (
                <p className="px-6 py-6 text-sm text-muted">{t("common.loading")}</p>
              ) : note.isError ? (
                <p className="px-6 py-6 text-sm text-red-400">
                  {(note.error as Error)?.message ?? "error"}
                </p>
              ) : note.data ? (
                <div
                  ref={noteContainerRef}
                  className="flex-1 overflow-y-auto"
                >
                  <NoteRenderer
                    content={note.data.content}
                    treeEntries={tree.data ?? []}
                    currentPath={note.data.path}
                    onOpen={navigate}
                    onMissingWikilink={(target) => {
                      // Only intercept dead links when the parent fiche
                      // lives under the configured training folder.
                      // Anywhere else, dead wikilinks are just user
                      // typos / forward references — not generation
                      // signals.
                      if (!note.data || !isUnderTraining(note.data.path)) return false;
                      setTrainingPrompt({ target, parent: note.data.path });
                      return true;
                    }}
                  />
                </div>
              ) : null
            ) : (
              <div className="flex-1 overflow-y-auto">
                <FolderIndex
                  folder={activePath ?? ""}
                  entries={tree.data ?? []}
                  onOpen={navigate}
                />
              </div>
            )}
          </div>

          {/* Backlinks (desktop: visible at lg+ only when reading a file) */}
          {!editing && activeKind === "file" && note.data && (
            <aside className="hidden w-64 shrink-0 border-l border-border bg-surface lg:block">
              <Backlinks links={note.data.backlinks} onOpen={navigate} />
            </aside>
          )}

          {!editing && activeKind === "file" && backlinksOpen && note.data && (
            <div
              className="fixed inset-0 z-40 bg-black/60 lg:hidden"
              onClick={() => setBacklinksOpen(false)}
            >
              <aside
                className="ml-auto flex h-full w-72 flex-col border-l border-border bg-surface"
                onClick={(e) => e.stopPropagation()}
              >
                <Backlinks links={note.data.backlinks} onOpen={navigate} />
              </aside>
            </div>
          )}
        </div>

        {/* Floating "chat about selection" toolbar — appears when the user
            highlights text inside the rendered note. Positioned just
            above the selection's bounding box, with viewport clamping
            so it stays visible at the edges. mouseDown.preventDefault
            keeps the click from clearing the selection before the
            handler fires. The button opens an in-page chat popup
            (NOT a switch to the general chat tab). */}
        {!editing && selection && (
          <div
            style={{
              position: "fixed",
              top: Math.max(8, selection.rect.top - 40),
              left: Math.max(
                8,
                Math.min(
                  window.innerWidth - 200,
                  selection.rect.left + selection.rect.width / 2 - 90,
                ),
              ),
              zIndex: 50,
            }}
            className="rounded-lg border border-accent bg-surface shadow-lg"
            onMouseDown={(e) => e.preventDefault()}
            onTouchStart={(e) => e.stopPropagation()}
          >
            <button
              type="button"
              onClick={startChatAboutSelection}
              className="flex items-center gap-1.5 rounded-lg bg-accent/10 px-3 py-1.5 text-xs text-accent hover:bg-accent/20"
            >
              <MessageSquare className="h-3.5 w-3.5" />
              {t("wiki.chatAboutSelection")}
            </button>
          </div>
        )}

        {/* In-page chat popup — discusses just the selected excerpt with
            the LLM, without leaving the wiki view. Closing it discards
            the conversation from the UI; the chat session itself still
            exists in the DB (tagged module_id="obsidian-wiki") and is
            findable via the Chat history sidebar later. */}
        {selectionChat && (
          <WikiSelectionChat
            path={selectionChat.path}
            selection={selectionChat.text}
            treeEntries={tree.data ?? []}
            onOpenWiki={(p) => {
              setSelectionChat(null);
              navigate(p);
            }}
            onClose={() => setSelectionChat(null)}
          />
        )}

        {trainingPrompt && (
          <TrainingExpandModal
            targetConcept={trainingPrompt.target}
            parentPath={trainingPrompt.parent}
            onClose={() => setTrainingPrompt(null)}
            onGenerated={(path) => {
              setTrainingPrompt(null);
              // The new fiche is on disk; refresh the tree so the
              // wikilink resolves and navigate the user to it.
              qc.invalidateQueries({ queryKey: ["vault-tree"] });
              navigate(path);
            }}
          />
        )}
      </main>
    </div>
  );
}

// Inline breadcrumbs for the wiki header. Path "Tech/RAG.md" → "Tech / RAG".
function Crumbs({ path, onCrumb }: { path: string | null; onCrumb: (p: string | null) => void }) {
  if (!path) {
    return <span className="flex-1 truncate text-sm text-muted" />;
  }
  const parts = path.split("/");
  const items: { label: string; target: string | null }[] = [];
  let acc = "";
  for (let i = 0; i < parts.length; i++) {
    acc = i === 0 ? parts[0] : `${acc}/${parts[i]}`;
    const isFile = i === parts.length - 1 && parts[i].endsWith(".md");
    items.push({
      label: isFile ? parts[i].replace(/\.md$/, "") : parts[i],
      target: i === parts.length - 1 ? null : acc, // last item is the current page
    });
  }
  return (
    <span className="flex flex-1 min-w-0 items-center gap-1 truncate text-sm text-muted">
      {items.map((it, i) => (
        <span key={i} className="flex items-center gap-1 truncate">
          {i > 0 && <span className="text-muted/60">/</span>}
          {it.target !== null ? (
            <button
              type="button"
              onClick={() => onCrumb(it.target)}
              className="truncate hover:text-text"
            >
              {it.label}
            </button>
          ) : (
            <span className="truncate text-text">{it.label}</span>
          )}
        </span>
      ))}
    </span>
  );
}
