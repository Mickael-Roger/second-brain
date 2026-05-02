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
import katex from "katex";

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

// ── LaTeX (KaTeX) ────────────────────────────────────────────────────
//
// Marked happily mangles math source — it eats backslashes, italicises
// `_`, splits on `*`, etc. So we render `$$…$$` and `$…$` to HTML up
// front, stash each rendered chunk under a sentinel marked never
// touches, then swap the sentinels back in after parsing.
//
// We also have to skip math markers that live inside fenced code blocks
// or inline `code` spans — `console.log("$5")` is not a formula.
//
// Sentinels are alphanumeric + `` so marked won't reformat them
// and they can't collide with anything a user would write. They get
// dropped into the doc as their own line for block math (so marked
// treats them as a paragraph, not as text inside another block) and
// inline for inline math.

const PLACEHOLDER_PREFIX = "SBMATH";
const PLACEHOLDER_SUFFIX = "SBMATH";
// One regex per occurrence kind. `[\s\S]` so newlines inside formulas
// (common in $$…$$ blocks) are part of the match.
const BLOCK_MATH_RE = /\$\$([\s\S]+?)\$\$/g;
// Inline math: a SINGLE `$…$`. We require non-whitespace just inside
// the dollars so prose like "it costs $5 or $10" doesn't get parsed
// as a formula. We also skip when followed immediately by a digit
// (currency-like), keeping the false-positive rate low.
const INLINE_MATH_RE = /(?<![\\$])\$(?!\s)([^\n$]+?)(?<!\s)\$(?!\d)/g;

interface MathExtraction {
  text: string;
  // PLACEHOLDER_PREFIX + idx + PLACEHOLDER_SUFFIX → final HTML.
  placeholders: Map<string, string>;
}

// Single private-use Unicode codepoint — won't appear in real notes,
// passes through marked / hljs untouched, and we swap it back to "$"
// in the final HTML pass. We only need to hide dollars from the math
// regexes; the code blocks themselves still go through marked (and
// thus marked-highlight), unlike the previous approach which extracted
// whole code blocks and broke syntax highlighting.
const DOLLAR_SENTINEL = "";

function maskDollarsInCode(md: string): string {
  // Fenced ```code``` blocks (greedy lazy across newlines).
  let out = md.replace(/```[\s\S]*?```/g, (m) =>
    m.replace(/\$/g, DOLLAR_SENTINEL),
  );
  // Inline `code` spans (single line, no nested backticks).
  out = out.replace(/`[^`\n]+`/g, (m) =>
    m.replace(/\$/g, DOLLAR_SENTINEL),
  );
  return out;
}

function unmaskDollars(html: string): string {
  if (!html.includes(DOLLAR_SENTINEL)) return html;
  return html.split(DOLLAR_SENTINEL).join("$");
}

function renderKatex(src: string, displayMode: boolean): string {
  try {
    return katex.renderToString(src, {
      displayMode,
      throwOnError: false,
      strict: "ignore",
      output: "htmlAndMathml",
    });
  } catch {
    // KaTeX failed even with throwOnError off — fall back to the raw
    // source wrapped in a code-style span so the user sees their input.
    const safe = src
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;");
    return displayMode
      ? `<pre class="katex-error">${safe}</pre>`
      : `<code class="katex-error">${safe}</code>`;
  }
}

function extractMath(md: string): MathExtraction {
  const placeholders = new Map<string, string>();
  let counter = 0;

  // Block math first (would otherwise be matched as two inline runs).
  let out = md.replace(BLOCK_MATH_RE, (_full, body) => {
    const key = `${PLACEHOLDER_PREFIX}${counter++}${PLACEHOLDER_SUFFIX}`;
    placeholders.set(key, renderKatex(body, true));
    // Surround with blank lines so marked treats the placeholder as its
    // own paragraph rather than inline text.
    return `\n\n${key}\n\n`;
  });

  out = out.replace(INLINE_MATH_RE, (_full, body) => {
    const key = `${PLACEHOLDER_PREFIX}${counter++}${PLACEHOLDER_SUFFIX}`;
    placeholders.set(key, renderKatex(body, false));
    return key;
  });

  return { text: out, placeholders };
}

function reinjectPlaceholders(
  html: string,
  placeholders: Map<string, string>,
): string {
  if (placeholders.size === 0) return html;
  // Marked may wrap a block-math placeholder in <p>…</p>. Strip those
  // wrappers so the resulting HTML keeps the .katex-display block-level
  // styling KaTeX provides.
  let out = html;
  for (const [key, value] of placeholders) {
    // First the wrapped form, then the bare form. `.split().join()`
    // does literal global replace without regex-escaping the key.
    out = out.split(`<p>${key}</p>`).join(value);
    out = out.split(key).join(value);
  }
  return out;
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
  // Order matters: embeds must run BEFORE wikilinks. `rewriteWikilinks`
  // matches `[[X]]` which is also a substring of `![[X]]`, so running it
  // first eats the inner part of every embed and the embed rewriter never
  // sees them — images would render with `src="sb:wikilink:…"` and break.
  // Math extraction runs LAST in the preprocessing chain (so wikilink
  // rewrites are done with) and is then re-injected after marked runs,
  // bypassing marked's markdown-tokeniser entirely for formula bodies.
  // Code blocks, however, must stay in the markdown stream so marked /
  // marked-highlight can syntax-highlight them — we just mask the `$`
  // characters inside them (so the math regex below leaves them alone)
  // and unmask in the final HTML.
  const piped = rewriteCallouts(rewriteWikilinks(rewriteEmbeds(md)));
  const masked = maskDollarsInCode(piped);
  const { text, placeholders } = extractMath(masked);
  const html = marked.parse(text);
  const htmlStr = typeof html === "string" ? html : "";
  const reinjected = reinjectPlaceholders(htmlStr, placeholders);
  return unmaskDollars(reinjected);
}

export type WikilinkRef = { target: string; heading: string };

export function isWikilinkHref(href: string): WikilinkRef | null {
  if (!href.startsWith("sb:wikilink:")) return null;
  const decoded = decodeURIComponent(href.slice("sb:wikilink:".length));
  const idx = decoded.indexOf("#");
  if (idx < 0) return { target: decoded, heading: "" };
  return { target: decoded.slice(0, idx), heading: decoded.slice(idx + 1) };
}
