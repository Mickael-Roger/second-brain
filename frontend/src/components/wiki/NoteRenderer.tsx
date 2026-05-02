// Render a vault note's markdown to HTML and intercept wikilink clicks so
// they switch the active note instead of navigating away.

import { useEffect, useMemo, useRef } from "react";

import { isWikilinkHref, renderMarkdown } from "@/lib/markdown";
import type { TreeEntry } from "@/lib/api";

import "highlight.js/styles/github-dark.css";

interface Props {
  content: string;
  treeEntries: TreeEntry[];
  // Vault-relative path of the note we're rendering. Used to resolve
  // relative image paths (e.g. `![alt](foo.png)` inside Notes/Tech/x.md
  // resolves to /api/vault/file?path=Notes/Tech/foo.png).
  currentPath?: string;
  onOpen: (path: string) => void;
  // Called when the user clicks a wikilink target that doesn't exist
  // in the vault. The parent decides whether to ignore (default) or
  // open a generation flow (Training feature). Returning true tells
  // the renderer that the click was handled.
  onMissingWikilink?: (target: string) => boolean;
}

function slugify(s: string): string {
  return s
    .trim()
    .toLowerCase()
    .replace(/[^\p{L}\p{N}\s-]/gu, "")
    .replace(/\s+/g, "-")
    .replace(/^-+|-+$/g, "");
}

// Decide what URL an embed (`<img src="…">` or `<a href="sb:embed:…">`)
// should actually point at. Returns null when the existing value is fine
// (external / data URI / already pointing at the vault file API).
function resolveEmbedTarget(
  src: string,
  currentPath: string | undefined,
  entries: TreeEntry[],
): string | null {
  if (!src) return null;
  // External, blob/data, or already-rewritten — leave alone.
  if (/^(https?:|data:|blob:|mailto:|sb:)/i.test(src) && !src.startsWith("sb:embed:")) {
    return null;
  }
  if (src.startsWith("/api/")) return null;

  let target: string;
  if (src.startsWith("sb:embed:")) {
    target = decodeURIComponent(src.slice("sb:embed:".length));
  } else if (src.startsWith("/")) {
    target = src.slice(1);
  } else {
    target = src;
  }

  // Strip a leading `./`.
  target = target.replace(/^\.\//, "");

  // Basename-only Obsidian embed (`![[image.png]]`): search the tree for a
  // matching filename. Falls back to the same folder as the current note.
  if (!target.includes("/")) {
    const match = entries.find(
      (e) =>
        e.type === "file" &&
        (e.path === target ||
          e.path.endsWith("/" + target)),
    );
    if (match) target = match.path;
    else if (currentPath) {
      const folder = currentPath.split("/").slice(0, -1).join("/");
      if (folder) target = `${folder}/${target}`;
    }
  } else if (currentPath && !target.startsWith("/") && !entries.some((e) => e.path === target)) {
    // Relative path that doesn't directly hit the tree — try resolving
    // against the current note's folder (e.g. `images/foo.png`).
    const folder = currentPath.split("/").slice(0, -1).join("/");
    if (folder) {
      const candidate = `${folder}/${target}`;
      if (entries.some((e) => e.path === candidate)) target = candidate;
    }
  }

  return `/api/vault/file?path=${encodeURIComponent(target)}`;
}

// Walk the rendered HTML and rewrite <img src="…"> + <a href="sb:embed:…">
// to point at /api/vault/file. Performed on the HTML string (off-DOM via
// <template>) so the browser never tries to fetch the bogus `sb:embed:` URL.
function rewriteEmbedTargets(
  html: string,
  currentPath: string | undefined,
  entries: TreeEntry[],
): string {
  if (!html) return html;
  const tpl = document.createElement("template");
  tpl.innerHTML = html;
  for (const img of tpl.content.querySelectorAll("img")) {
    const src = img.getAttribute("src");
    if (!src) continue;
    const resolved = resolveEmbedTarget(src, currentPath, entries);
    if (resolved && resolved !== src) {
      img.setAttribute("src", resolved);
      img.setAttribute("loading", "lazy");
    }
  }
  for (const a of tpl.content.querySelectorAll("a")) {
    const href = a.getAttribute("href") ?? "";
    if (!href.startsWith("sb:embed:")) continue;
    const resolved = resolveEmbedTarget(href, currentPath, entries);
    if (resolved && resolved !== href) {
      a.setAttribute("href", resolved);
      a.setAttribute("download", "");
      a.setAttribute("target", "_blank");
      a.setAttribute("rel", "noopener");
    }
  }
  return tpl.innerHTML;
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

export default function NoteRenderer({
  content,
  treeEntries,
  currentPath,
  onOpen,
  onMissingWikilink,
}: Props) {
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
        } else if (onMissingWikilink) {
          // Dead wikilink — let the parent decide (e.g. open the
          // training generation modal when we're under Training/).
          onMissingWikilink(link.target);
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
  }, [treeEntries, onOpen, onMissingWikilink]);

  // Strip frontmatter from rendered output (it'd render as a degenerate <hr>).
  const html = useMemo(() => {
    const body = content.replace(/^---\s*\n[\s\S]*?\n---\s*\n?/, "");
    const raw = renderMarkdown(body);
    return rewriteEmbedTargets(raw, currentPath, treeEntries);
  }, [content, currentPath, treeEntries]);

  // After each render, walk the DOM and mark wikilinks that don't
  // resolve in the tree as "dead". The CSS gives them a subdued dashed
  // style so the user sees they don't exist yet. The click handler
  // above forwards them to onMissingWikilink (the Training generator).
  useEffect(() => {
    const node = ref.current;
    if (!node) return;
    const links = node.querySelectorAll<HTMLAnchorElement>("a[href^='sb:wikilink:']");
    links.forEach((a) => {
      const href = a.getAttribute("href") ?? "";
      const link = isWikilinkHref(href);
      if (!link) return;
      const target = resolveWikilinkTarget(link.target, treeEntries);
      a.classList.toggle("wikilink-dead", target === null);
    });
  }, [html, treeEntries]);

  // Mermaid rendering pass — finds every <div class="mermaid-block"
  // data-source="<base64>"> the markdown rewriter inserted, decodes the
  // source, and replaces the div's contents with the rendered SVG.
  // Mermaid is loaded lazily so notes without diagrams pay no cost.
  useEffect(() => {
    const node = ref.current;
    if (!node) return;
    const blocks = node.querySelectorAll<HTMLDivElement>("div.mermaid-block[data-source]");
    if (blocks.length === 0) return;
    let cancelled = false;
    (async () => {
      try {
        const { default: mermaid } = await import("mermaid");
        if (cancelled) return;
        mermaid.initialize({ startOnLoad: false, theme: "dark", securityLevel: "strict" });
        for (let i = 0; i < blocks.length; i++) {
          const el = blocks[i];
          const encoded = el.getAttribute("data-source") ?? "";
          let src = "";
          try {
            src = decodeURIComponent(escape(atob(encoded)));
          } catch {
            el.textContent = "[mermaid: invalid source]";
            continue;
          }
          const id = `mmd-${Date.now().toString(36)}-${i}`;
          try {
            const { svg } = await mermaid.render(id, src);
            if (cancelled) return;
            el.innerHTML = svg;
          } catch (err) {
            el.textContent = `[mermaid error: ${(err as Error)?.message ?? "unknown"}]`;
          }
        }
      } catch (err) {
        // Mermaid failed to load — leave the placeholders empty rather than crash.
        // eslint-disable-next-line no-console
        console.error("mermaid load failed", err);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [html]);

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
