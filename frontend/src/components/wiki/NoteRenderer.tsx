// Render a vault note's markdown to HTML and intercept wikilink clicks so
// they switch the active note instead of navigating away.

import { useEffect, useRef } from "react";

import { isWikilinkHref, renderMarkdown } from "@/lib/markdown";
import type { TreeEntry } from "@/lib/api";

interface Props {
  content: string;
  treeEntries: TreeEntry[];
  onOpen: (path: string) => void;
}

function resolveWikilinkTarget(token: string, entries: TreeEntry[]): string | null {
  const stripExt = (s: string) => s.replace(/\.md$/, "");
  const base = stripExt(token);
  // Try exact path first (with or without .md).
  const exact = entries.find((e) => e.type === "file" && stripExt(e.path) === base);
  if (exact) return exact.path;
  // Fall back to a basename match.
  const byBasename = entries.find(
    (e) => e.type === "file" && stripExt(e.path.split("/").pop() ?? "") === base,
  );
  return byBasename ? byBasename.path : null;
}

export default function NoteRenderer({ content, treeEntries, onOpen }: Props) {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const node = ref.current;
    if (!node) return;
    const handler = (e: MouseEvent) => {
      const a = (e.target as HTMLElement).closest("a");
      if (!a) return;
      const href = a.getAttribute("href") ?? "";
      const link = isWikilinkHref(href);
      if (!link) return;
      e.preventDefault();
      const target = resolveWikilinkTarget(link.target, treeEntries);
      if (target) onOpen(target);
    };
    node.addEventListener("click", handler);
    return () => node.removeEventListener("click", handler);
  }, [treeEntries, onOpen]);

  // Strip frontmatter from rendered output (it'd render as a degenerate <hr>).
  const body = content.replace(/^---\s*\n[\s\S]*?\n---\s*\n?/, "");
  const html = renderMarkdown(body);

  return (
    <div
      ref={ref}
      className="prose-sb mx-auto max-w-3xl px-6 py-6"
      // marked output is sanitized at render input boundaries — caller
      // controls the markdown.
      dangerouslySetInnerHTML={{ __html: html }}
    />
  );
}
