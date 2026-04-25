// Wiki view: tree on the left, rendered note in the center, backlinks on the
// right. Search bar above the tree.

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronLeft, ChevronRight, Home, Link2, Menu, Pencil, X } from "lucide-react";

import { api, type TreeEntry, type VaultNote } from "@/lib/api";
import VaultTree from "./VaultTree";
import NoteRenderer from "./NoteRenderer";
import Backlinks from "./Backlinks";
import SearchBar from "./SearchBar";
import WikiEditor from "./WikiEditor";
import FolderIndex from "./FolderIndex";

export interface WikiTarget {
  path: string | null;
  nonce: number;
}

export default function WikiView({ target }: { target?: WikiTarget | null }) {
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
                <div className="flex-1 overflow-y-auto">
                  <NoteRenderer
                    content={note.data.content}
                    treeEntries={tree.data ?? []}
                    currentPath={note.data.path}
                    onOpen={navigate}
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
