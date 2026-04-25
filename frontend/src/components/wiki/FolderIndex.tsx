// Wiki landing page for a folder (or the vault root). Shows folders + files
// at this level as cards — closer to a real wiki's folder index than to the
// raw filesystem tree on the left. If an INDEX.md exists at this level, its
// rendered content is shown above the cards.

import { useMemo } from "react";
import { useTranslation } from "react-i18next";
import { useQuery } from "@tanstack/react-query";
import { FileText, Folder } from "lucide-react";

import { api, type TreeEntry, type VaultNote } from "@/lib/api";
import NoteRenderer from "./NoteRenderer";

interface Props {
  folder: string;       // "" for vault root
  entries: TreeEntry[]; // full tree, filtered here
  onOpen: (path: string) => void;
}

function compareEntries(a: TreeEntry, b: TreeEntry): number {
  if (a.type !== b.type) return a.type === "folder" ? -1 : 1;
  return a.path.localeCompare(b.path, undefined, { sensitivity: "base" });
}

export default function FolderIndex({ folder, entries, onOpen }: Props) {
  const { t } = useTranslation();

  const { folders, files, indexPath } = useMemo(() => {
    const prefix = folder ? `${folder}/` : "";
    const expectedDepth = folder ? folder.split("/").length : 0;
    const here = entries.filter((e) => {
      if (folder === "") return e.depth === 0;
      if (!e.path.startsWith(prefix)) return false;
      const rest = e.path.slice(prefix.length);
      return rest.length > 0 && !rest.includes("/") && e.depth === expectedDepth;
    });
    here.sort(compareEntries);
    // At the vault root the INDEX.md is the system-injected map, not wiki
    // content — stays clickable in the files list, but not auto-rendered
    // above the cards. Sub-folder INDEX.md files are still auto-rendered
    // because there they act as hub notes.
    const isRoot = folder === "";
    const indexCandidate = `${prefix}INDEX.md`;
    const idx = entries.find(
      (e) => e.type === "file" && e.path === indexCandidate,
    );
    return {
      folders: here.filter((e) => e.type === "folder"),
      files: here.filter(
        (e) => e.type === "file" && (isRoot || e.path !== idx?.path),
      ),
      indexPath: isRoot ? null : (idx?.path ?? null),
    };
  }, [entries, folder]);

  const indexNote = useQuery<VaultNote | null>({
    queryKey: ["vault-note", indexPath],
    queryFn: async () =>
      indexPath
        ? api.get<VaultNote>(`/api/vault/note?path=${encodeURIComponent(indexPath)}`)
        : null,
    enabled: indexPath !== null,
  });

  const title = folder || t("wiki.vaultRoot");

  return (
    <div className="mx-auto max-w-3xl px-6 py-6">
      <h1 className="mb-2 text-2xl font-semibold">{title}</h1>
      {folder && (
        <p className="mb-4 text-xs text-muted">
          {folders.length} {t("wiki.foldersLabel")} · {files.length}{" "}
          {t("wiki.filesLabel")}
        </p>
      )}

      {indexNote.data ? (
        <div className="mb-8 rounded-xl border border-border bg-surface p-4">
          <NoteRenderer
            content={indexNote.data.content}
            treeEntries={entries}
            currentPath={indexNote.data.path}
            onOpen={onOpen}
          />
        </div>
      ) : null}

      {folders.length > 0 && (
        <section className="mb-6">
          <h2 className="mb-3 text-sm uppercase tracking-wide text-muted">
            {t("wiki.foldersLabel")}
          </h2>
          <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
            {folders.map((f) => (
              <button
                key={f.path}
                type="button"
                onClick={() => onOpen(f.path)}
                className="flex items-center gap-2 rounded-lg border border-border bg-surface px-3 py-2 text-left text-sm hover:border-accent"
              >
                <Folder className="h-4 w-4 text-accent shrink-0" />
                <span className="truncate">{f.path.split("/").pop()}</span>
              </button>
            ))}
          </div>
        </section>
      )}

      {files.length > 0 && (
        <section>
          <h2 className="mb-3 text-sm uppercase tracking-wide text-muted">
            {t("wiki.filesLabel")}
          </h2>
          <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
            {files.map((f) => (
              <button
                key={f.path}
                type="button"
                onClick={() => onOpen(f.path)}
                className="flex items-center gap-2 rounded-lg border border-border bg-surface px-3 py-2 text-left text-sm hover:border-accent"
              >
                <FileText className="h-4 w-4 text-muted shrink-0" />
                <span className="truncate">
                  {(f.path.split("/").pop() || "").replace(/\.md$/, "")}
                </span>
              </button>
            ))}
          </div>
        </section>
      )}

      {folders.length === 0 && files.length === 0 && !indexNote.data && (
        <p className="text-sm text-muted">{t("wiki.emptyFolder")}</p>
      )}
    </div>
  );
}
