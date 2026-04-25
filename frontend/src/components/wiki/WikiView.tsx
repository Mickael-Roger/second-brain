// Wiki view: tree on the left, rendered note in the center, backlinks on the
// right. Search bar above the tree.

import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { BookOpen, Link2, Menu, Pencil, X } from "lucide-react";

import { api, type TreeEntry, type VaultNote } from "@/lib/api";
import VaultTree from "./VaultTree";
import NoteRenderer from "./NoteRenderer";
import Backlinks from "./Backlinks";
import SearchBar from "./SearchBar";
import WikiEditor from "./WikiEditor";

export default function WikiView() {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const [activePath, setActivePath] = useState<string | null>(null);
  const [editing, setEditing] = useState(false);
  const [treeOpen, setTreeOpen] = useState(false);
  const [backlinksOpen, setBacklinksOpen] = useState(false);

  // Switching notes always exits edit mode and closes mobile drawers.
  useEffect(() => {
    setEditing(false);
    setTreeOpen(false);
    setBacklinksOpen(false);
  }, [activePath]);

  const tree = useQuery({
    queryKey: ["vault-tree"],
    queryFn: () => api.get<TreeEntry[]>("/api/vault/tree"),
  });

  const note = useQuery<VaultNote | null>({
    queryKey: ["vault-note", activePath],
    queryFn: async () =>
      activePath
        ? api.get<VaultNote>(`/api/vault/note?path=${encodeURIComponent(activePath)}`)
        : null,
    enabled: activePath !== null,
  });

  // First-time: pick INDEX.md if it exists, otherwise the first file.
  useEffect(() => {
    if (activePath !== null) return;
    const list = tree.data;
    if (!list || list.length === 0) return;
    const index = list.find((e) => e.type === "file" && e.path === "INDEX.md");
    const first = list.find((e) => e.type === "file");
    setActivePath(index?.path ?? first?.path ?? null);
  }, [tree.data, activePath]);

  const treeAside = (
    <>
      <SearchBar onOpen={setActivePath} />
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
            onSelect={setActivePath}
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
          <BookOpen className="h-4 w-4 text-accent" />
          <span className="flex-1 truncate text-sm font-medium">
            {activePath ?? t("wiki.title")}
          </span>
          {note.data && !editing && (
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
            ) : note.isLoading && activePath ? (
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
                  onOpen={setActivePath}
                />
              </div>
            ) : (
              <div className="flex h-full items-center justify-center px-6 text-center text-muted">
                <p>{t("wiki.empty")}</p>
              </div>
            )}
          </div>

          {/* Backlinks (desktop: always visible at lg+; mobile: drawer) */}
          {!editing && (
            <aside className="hidden w-64 shrink-0 border-l border-border bg-surface lg:block">
              {note.data ? (
                <Backlinks links={note.data.backlinks} onOpen={setActivePath} />
              ) : null}
            </aside>
          )}

          {!editing && backlinksOpen && note.data && (
            <div
              className="fixed inset-0 z-40 bg-black/60 lg:hidden"
              onClick={() => setBacklinksOpen(false)}
            >
              <aside
                className="ml-auto flex h-full w-72 flex-col border-l border-border bg-surface"
                onClick={(e) => e.stopPropagation()}
              >
                <Backlinks links={note.data.backlinks} onOpen={setActivePath} />
              </aside>
            </div>
          )}
        </div>
      </main>
    </div>
  );
}
