// Render vault markdown to HTML, rewriting Obsidian wikilinks into anchors
// the wiki view can intercept.

import { marked } from "marked";

const WIKILINK_RE = /\[\[([^\]|#]+)(#[^\]|]+)?(\|([^\]]+))?\]\]/g;

function rewriteWikilinks(md: string): string {
  return md.replace(WIKILINK_RE, (_full, target, _heading, _pipe, alias) => {
    const display = (alias ?? target).trim();
    const targetTrim = String(target).trim();
    // The href is a non-URL marker so the click handler can intercept it.
    return `[${display}](sb:wikilink:${encodeURIComponent(targetTrim)})`;
  });
}

const EMBED_RE = /!\[\[([^\]|#]+)(\|([^\]]+))?\]\]/g;

function rewriteEmbeds(md: string): string {
  return md.replace(EMBED_RE, (_full, target, _pipe, alias) => {
    const display = (alias ?? target).trim();
    const targetTrim = String(target).trim();
    return `![${display}](sb:embed:${encodeURIComponent(targetTrim)})`;
  });
}

export function renderMarkdown(md: string): string {
  // Embeds first (they're a subset of wikilink syntax with a leading !).
  const piped = rewriteEmbeds(rewriteWikilinks(md));
  const html = marked.parse(piped, { gfm: true, breaks: false, async: false });
  return typeof html === "string" ? html : "";
}

export function isWikilinkHref(href: string): { kind: "wikilink"; target: string } | null {
  if (href.startsWith("sb:wikilink:")) {
    return { kind: "wikilink", target: decodeURIComponent(href.slice("sb:wikilink:".length)) };
  }
  return null;
}
