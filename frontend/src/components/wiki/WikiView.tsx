// Wiki view: tree on the left, rendered note in the center, backlinks on the
// right. Search bar above the tree.

import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { useQuery } from "@tanstack/react-query";
import { BookOpen } from "lucide-react";

import { api, type TreeEntry, type VaultNote } from "@/lib/api";
import VaultTree from "./VaultTree";
import NoteRenderer from "./NoteRenderer";
import Backlinks from "./Backlinks";
import SearchBar from "./SearchBar";

export default function WikiView() {
  const { t } = useTranslation();
  const [activePath, setActivePath] = useState<string | null>(null);

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

  return (
    <div className="flex h-full">
      <aside className="hidden w-64 shrink-0 flex-col border-r border-border bg-surface md:flex">
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
      </aside>

      <main className="flex h-full min-w-0 flex-1 flex-col">
        <header className="flex items-center gap-2 border-b border-border bg-surface px-4 py-2">
          <BookOpen className="h-4 w-4 text-accent" />
          <span className="truncate text-sm font-medium">
            {activePath ?? t("wiki.title")}
          </span>
        </header>

        <div className="flex flex-1 min-h-0">
          <div className="flex-1 overflow-y-auto">
            {note.isLoading && activePath ? (
              <p className="px-6 py-6 text-sm text-muted">{t("common.loading")}</p>
            ) : note.isError ? (
              <p className="px-6 py-6 text-sm text-red-400">
                {(note.error as Error)?.message ?? "error"}
              </p>
            ) : note.data ? (
              <NoteRenderer
                content={note.data.content}
                treeEntries={tree.data ?? []}
                onOpen={setActivePath}
              />
            ) : (
              <div className="flex h-full items-center justify-center px-6 text-center text-muted">
                <p>{t("wiki.empty")}</p>
              </div>
            )}
          </div>

          <aside className="hidden w-64 shrink-0 border-l border-border bg-surface lg:block">
            {note.data ? (
              <Backlinks links={note.data.backlinks} onOpen={setActivePath} />
            ) : null}
          </aside>
        </div>
      </main>
    </div>
  );
}
