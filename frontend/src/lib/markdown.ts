// Render vault markdown to HTML with:
//  - Obsidian wikilinks rewritten into anchors the wiki view intercepts:
//      [[Note]], [[Note|Alias]], [[Note#Heading]], [[Note#Heading|Alias]]
//      [[#Heading]] / [[#Heading|Alias]]   — same-page anchor
//  - Obsidian callouts (`> [!type] title`) wrapped in styled <div> blocks.
//  - Heading slugs (gfm-style ids) so #heading anchors actually scroll.
//  - Code blocks syntax-highlighted via highlight.js.

import { Marked } from "marked";
import { gfmHeadingId } from "marked-gfm-heading-id";
import { markedHighlight } from "marked-highlight";
import hljs from "highlight.js/lib/common";

// Obsidian wikilinks. The "name" group can now be empty (for [[#Heading]]).
//                          target            heading              alias
const WIKILINK_RE = /\[\[([^\]|#]*)(#[^\]|]+)?(\|([^\]]+))?\]\]/g;
const EMBED_RE = /!\[\[([^\]|#]+)(\|([^\]]+))?\]\]/g;

// Convert Obsidian heading text into the slug gfmHeadingId produces, so
// `[[Note#My Heading]]` resolves to the right id.
function slugify(s: string): string {
  return s
    .trim()
    .toLowerCase()
    .replace(/[^\p{L}\p{N}\s-]/gu, "")
    .replace(/\s+/g, "-")
    .replace(/^-+|-+$/g, "");
}

function rewriteWikilinks(md: string): string {
  return md.replace(WIKILINK_RE, (_full, target, heading, _pipe, alias) => {
    const t = String(target || "").trim();
    const h = heading ? String(heading).slice(1).trim() : "";
    const display = (alias ?? (t || h)).trim();
    if (!t && h) {
      // Same-page anchor — emit a normal href so the browser handles scroll.
      return `[${display}](#${slugify(h)})`;
    }
    const ref = h ? `${t}#${h}` : t;
    return `[${display}](sb:wikilink:${encodeURIComponent(ref)})`;
  });
}

// Obsidian image extensions. Anything else (PDFs, zips, …) becomes a
// download link so the browser doesn't try to render it as an <img>.
const IMAGE_EXTS = new Set([
  "png", "jpg", "jpeg", "gif", "webp", "svg", "bmp", "ico", "avif",
]);

function rewriteEmbeds(md: string): string {
  return md.replace(EMBED_RE, (_full, target, _pipe, alias) => {
    const t = String(target).trim();
    const display = (alias ?? t).trim();
    const ext = t.split(".").pop()?.toLowerCase() ?? "";
    if (IMAGE_EXTS.has(ext)) {
      return `![${display}](sb:embed:${encodeURIComponent(t)})`;
    }
    return `[${display}](sb:embed:${encodeURIComponent(t)})`;
  });
}

const CALLOUT_TYPES = new Set([
  "note", "info", "tip", "hint", "important",
  "success", "check", "done",
  "warning", "caution", "attention",
  "danger", "error", "fail", "failure", "missing",
  "bug", "example", "quote", "cite",
  "todo", "abstract", "summary", "tldr", "question", "faq", "help",
]);

// Obsidian callouts:
//   > [!type] optional title
//   > body line 1
//   > body line 2
//
// We pre-process before handing to marked: replace the blockquote with an
// HTML <div class="callout callout-{type}">…body…</div>. The blank lines
// around the body let marked re-parse the inner content as markdown.
function rewriteCallouts(md: string): string {
  const lines = md.split("\n");
  const out: string[] = [];
  let i = 0;
  while (i < lines.length) {
    const m = lines[i].match(/^\s*>\s*\[!([A-Za-z]+)\]\s*(.*)$/);
    if (m && CALLOUT_TYPES.has(m[1].toLowerCase())) {
      const type = m[1].toLowerCase();
      const title = m[2].trim();
      const body: string[] = [];
      i++;
      while (i < lines.length && /^\s*>/.test(lines[i])) {
        body.push(lines[i].replace(/^\s*>\s?/, ""));
        i++;
      }
      out.push(`<div class="callout callout-${type}">`);
      out.push(`<div class="callout-title">${title || titleCase(type)}</div>`);
      out.push("");
      out.push(body.join("\n"));
      out.push("");
      out.push("</div>");
    } else {
      out.push(lines[i]);
      i++;
    }
  }
  return out.join("\n");
}

function titleCase(s: string): string {
  return s.charAt(0).toUpperCase() + s.slice(1);
}

// One Marked instance, configured once with all extensions.
const marked = new Marked(
  gfmHeadingId(),
  markedHighlight({
    langPrefix: "hljs language-",
    highlight(code, lang) {
      if (lang && hljs.getLanguage(lang)) {
        try {
          return hljs.highlight(code, { language: lang, ignoreIllegals: true }).value;
        } catch {
          /* fall through */
        }
      }
      return hljs.highlightAuto(code).value;
    },
  }),
);
marked.setOptions({ gfm: true, breaks: false, async: false });

export function renderMarkdown(md: string): string {
  const piped = rewriteCallouts(rewriteEmbeds(rewriteWikilinks(md)));
  const html = marked.parse(piped);
  return typeof html === "string" ? html : "";
}

export type WikilinkRef = { target: string; heading: string };

export function isWikilinkHref(href: string): WikilinkRef | null {
  if (!href.startsWith("sb:wikilink:")) return null;
  const decoded = decodeURIComponent(href.slice("sb:wikilink:".length));
  const idx = decoded.indexOf("#");
  if (idx < 0) return { target: decoded, heading: "" };
  return { target: decoded.slice(0, idx), heading: decoded.slice(idx + 1) };
}
