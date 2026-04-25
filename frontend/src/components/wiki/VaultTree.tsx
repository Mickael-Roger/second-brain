// Folder-aware vault tree. Folders are collapsible; files trigger onSelect.

import { useMemo, useState } from "react";
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

function buildTree(entries: TreeEntry[]): Node {
  const root: Node = { name: "", path: "", type: "folder", children: [] };
  const byPath = new Map<string, Node>([["", root]]);

  for (const e of entries.slice().sort((a, b) => a.path.localeCompare(b.path))) {
    const parts = e.path.split("/");
    const name = parts[parts.length - 1];
    const parentPath = parts.slice(0, -1).join("/");
    const parent = byPath.get(parentPath) ?? root;
    const node: Node = { name, path: e.path, type: e.type, children: [] };
    parent.children.push(node);
    byPath.set(e.path, node);
  }
  return root;
}

function NodeView({
  node,
  activePath,
  onSelect,
}: {
  node: Node;
  activePath: string | null;
  onSelect: (path: string) => void;
}) {
  const [open, setOpen] = useState(true);
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
  // Folder
  return (
    <div>
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
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
  return (
    <div className="px-2 py-2">
      {root.children.map((c) => (
        <NodeView
          key={c.path}
          node={c}
          activePath={activePath}
          onSelect={onSelect}
        />
      ))}
    </div>
  );
}
