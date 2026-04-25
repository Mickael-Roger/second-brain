// Folder-aware vault tree.
//
//  - All folders start CLOSED on first connect (sessionStorage is empty).
//  - The open/closed state is kept in sessionStorage so it survives view
//    switches and reloads within a session, and resets when a new session
//    starts.
//  - Within a folder, children sort folders-first then alphabetically,
//    case-insensitively.

import { useCallback, useEffect, useMemo, useState } from "react";
import { ChevronDown, ChevronRight, FileText, Folder, FolderOpen } from "lucide-react";

import type { TreeEntry } from "@/lib/api";

interface Props {
  entries: TreeEntry[];
  activePath: string | null;
  onSelect: (path: string) => void;
}

interface Node {
  name: string;
  path: string;
  type: "folder" | "file";
  children: Node[];
}

const SS_KEY = "sb.wiki.openFolders";

function loadOpen(): Set<string> {
  if (typeof window === "undefined") return new Set();
  try {
    const raw = sessionStorage.getItem(SS_KEY);
    if (!raw) return new Set();
    const arr = JSON.parse(raw);
    return new Set(Array.isArray(arr) ? (arr as string[]) : []);
  } catch {
    return new Set();
  }
}

function persistOpen(open: Set<string>) {
  try {
    sessionStorage.setItem(SS_KEY, JSON.stringify(Array.from(open)));
  } catch {
    // sessionStorage might be unavailable (private mode, embedded webviews)
  }
}

function compareChildren(a: Node, b: Node): number {
  // Folders first.
  if (a.type !== b.type) return a.type === "folder" ? -1 : 1;
  // Case-insensitive alpha.
  return a.name.localeCompare(b.name, undefined, { sensitivity: "base" });
}

function buildTree(entries: TreeEntry[]): Node {
  const root: Node = { name: "", path: "", type: "folder", children: [] };
  const byPath = new Map<string, Node>([["", root]]);

  // Sort entries by path so parents are visited before their children.
  const sorted = entries.slice().sort((a, b) => a.path.localeCompare(b.path));
  for (const e of sorted) {
    const parts = e.path.split("/");
    const name = parts[parts.length - 1];
    const parentPath = parts.slice(0, -1).join("/");
    const parent = byPath.get(parentPath) ?? root;
    const node: Node = { name, path: e.path, type: e.type, children: [] };
    parent.children.push(node);
    byPath.set(e.path, node);
  }
  // Sort each level: folders first, then files, alpha.
  const sortRec = (n: Node) => {
    n.children.sort(compareChildren);
    n.children.forEach(sortRec);
  };
  sortRec(root);
  return root;
}

interface NodeViewProps {
  node: Node;
  activePath: string | null;
  isOpen: (path: string) => boolean;
  toggle: (path: string) => void;
  onSelect: (path: string) => void;
}

function NodeView({ node, activePath, isOpen, toggle, onSelect }: NodeViewProps) {
  if (node.type === "file") {
    const active = activePath === node.path;
    return (
      <button
        type="button"
        onClick={() => onSelect(node.path)}
        className={`flex w-full items-center gap-1.5 rounded px-1.5 py-1 text-left text-sm ${
          active ? "bg-bg text-accent" : "text-muted hover:bg-bg hover:text-text"
        }`}
      >
        <FileText className="h-3.5 w-3.5 shrink-0" />
        <span className="truncate">{node.name.replace(/\.md$/, "")}</span>
      </button>
    );
  }
  const open = isOpen(node.path);
  return (
    <div>
      <button
        type="button"
        onClick={() => toggle(node.path)}
        className="flex w-full items-center gap-1.5 rounded px-1.5 py-1 text-left text-sm text-text hover:bg-bg"
      >
        {open ? (
          <ChevronDown className="h-3.5 w-3.5 shrink-0" />
        ) : (
          <ChevronRight className="h-3.5 w-3.5 shrink-0" />
        )}
        {open ? (
          <FolderOpen className="h-3.5 w-3.5 shrink-0" />
        ) : (
          <Folder className="h-3.5 w-3.5 shrink-0" />
        )}
        <span className="truncate">{node.name}</span>
      </button>
      {open && (
        <div className="ml-3 border-l border-border/60 pl-1.5">
          {node.children.map((c) => (
            <NodeView
              key={c.path}
              node={c}
              activePath={activePath}
              isOpen={isOpen}
              toggle={toggle}
              onSelect={onSelect}
            />
          ))}
        </div>
      )}
    </div>
  );
}

export default function VaultTree({ entries, activePath, onSelect }: Props) {
  const root = useMemo(() => buildTree(entries), [entries]);

  const [openFolders, setOpenFolders] = useState<Set<string>>(() => loadOpen());

  // Persist whenever the set changes.
  useEffect(() => {
    persistOpen(openFolders);
  }, [openFolders]);

  // When the active path is set deep, auto-open ancestor folders so the
  // selection is visible. Stored to sessionStorage like a manual toggle.
  useEffect(() => {
    if (!activePath) return;
    const parts = activePath.split("/");
    if (parts.length <= 1) return;
    setOpenFolders((prev) => {
      const next = new Set(prev);
      let acc = "";
      for (let i = 0; i < parts.length - 1; i++) {
        acc = acc ? `${acc}/${parts[i]}` : parts[i];
        next.add(acc);
      }
      return next;
    });
  }, [activePath]);

  const isOpen = useCallback((p: string) => openFolders.has(p), [openFolders]);
  const toggle = useCallback(
    (p: string) =>
      setOpenFolders((prev) => {
        const next = new Set(prev);
        if (next.has(p)) next.delete(p);
        else next.add(p);
        return next;
      }),
    [],
  );

  return (
    <div className="px-2 py-2">
      {root.children.map((c) => (
        <NodeView
          key={c.path}
          node={c}
          activePath={activePath}
          isOpen={isOpen}
          toggle={toggle}
          onSelect={onSelect}
        />
      ))}
    </div>
  );
}
