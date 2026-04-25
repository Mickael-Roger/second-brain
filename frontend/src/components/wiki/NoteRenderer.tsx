// Render a vault note's markdown to HTML and intercept wikilink clicks so
// they switch the active note instead of navigating away.

import { useEffect, useRef } from "react";

import { isWikilinkHref, renderMarkdown } from "@/lib/markdown";
import type { TreeEntry } from "@/lib/api";

import "highlight.js/styles/github-dark.css";

interface Props {
  content: string;
  treeEntries: TreeEntry[];
  onOpen: (path: string) => void;
}

function slugify(s: string): string {
  return s
    .trim()
    .toLowerCase()
    .replace(/[^\p{L}\p{N}\s-]/gu, "")
    .replace(/\s+/g, "-")
    .replace(/^-+|-+$/g, "");
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

      // Wikilink to another note (with optional heading).
      const link = isWikilinkHref(href);
      if (link) {
        e.preventDefault();
        const target = resolveWikilinkTarget(link.target, treeEntries);
        if (target) {
          onOpen(target);
          if (link.heading) {
            // Defer until the new note has rendered.
            const slug = slugify(link.heading);
            window.setTimeout(() => {
              const el = document.getElementById(slug);
              if (el) el.scrollIntoView({ behavior: "smooth", block: "start" });
            }, 60);
          }
        }
        return;
      }

      // Same-page anchor (#heading) — let the browser handle but use smooth scroll.
      if (href.startsWith("#")) {
        e.preventDefault();
        const slug = href.slice(1);
        const el = node.querySelector(`#${CSS.escape(slug)}`);
        if (el instanceof HTMLElement) {
          el.scrollIntoView({ behavior: "smooth", block: "start" });
        }
      }
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
