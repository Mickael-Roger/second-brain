// Auto-linkify plain-text mentions of vault notes inside chat messages.
//
// The LLM doesn't always remember to use Obsidian wikilinks. When it writes
// "see Tech/RAG.md" or "from S3NS cheatsheet.md", we still want those
// references to be clickable links into the wiki tab. This walks the
// rendered HTML, scans text nodes against the vault tree, and wraps
// matches in `<a href="sb:wikilink:…">` anchors that the chat click
// handler already routes to `onOpenWiki`.

import type { TreeEntry } from "./api";

function escapeRegex(s: string): string {
  return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function buildLookup(entries: TreeEntry[]): {
  regex: RegExp | null;
  lookup: Map<string, string>;
} {
  const files = entries
    .filter((e) => e.type === "file" && e.path.endsWith(".md"))
    .map((e) => e.path);
  if (files.length === 0) return { regex: null, lookup: new Map() };

  const lookup = new Map<string, string>();
  for (const p of files) {
    lookup.set(p, p);
    const base = p.split("/").pop();
    // First-encountered wins for duplicate basenames.
    if (base && !lookup.has(base)) lookup.set(base, p);
  }
  // Sort longest-first so "Notes/Foo.md" wins over "Foo.md" when both
  // would match at the same position.
  const candidates = Array.from(lookup.keys()).sort((a, b) => b.length - a.length);

  const alternation = candidates.map(escapeRegex).join("|");
  // (?<!\w) / (?!\w) word-boundary lookarounds so we don't snip mid-word.
  // ".md" already ends with a word char so the trailing lookaround keeps
  // us from matching `Foo.md` inside `Foo.mdx`.
  const regex = new RegExp(`(?<!\\w)(?:${alternation})(?!\\w)`, "g");
  return { regex, lookup };
}

const SKIP_TAGS = new Set(["A", "CODE", "PRE", "SCRIPT", "STYLE"]);

function walk(
  node: Node,
  regex: RegExp,
  lookup: Map<string, string>,
): void {
  if (node.nodeType === Node.ELEMENT_NODE) {
    if (SKIP_TAGS.has((node as Element).tagName)) return;
  }
  if (node.nodeType === Node.TEXT_NODE) {
    const text = node.nodeValue ?? "";
    regex.lastIndex = 0;
    if (!regex.test(text)) return;

    regex.lastIndex = 0;
    const fragment = document.createDocumentFragment();
    let last = 0;
    let m: RegExpExecArray | null;
    while ((m = regex.exec(text)) !== null) {
      if (m.index > last) {
        fragment.appendChild(document.createTextNode(text.substring(last, m.index)));
      }
      const target = lookup.get(m[0]) ?? m[0];
      const a = document.createElement("a");
      a.setAttribute("href", `sb:wikilink:${encodeURIComponent(target)}`);
      a.textContent = m[0];
      fragment.appendChild(a);
      last = m.index + m[0].length;
    }
    if (last < text.length) {
      fragment.appendChild(document.createTextNode(text.substring(last)));
    }
    node.parentNode?.replaceChild(fragment, node);
    return;
  }
  // Element with no skip — walk children. Snapshot before modifying.
  const children = Array.from(node.childNodes);
  for (const c of children) walk(c, regex, lookup);
}

/**
 * Take rendered HTML, return the same HTML with text-node mentions of vault
 * note paths wrapped in clickable `sb:wikilink:` anchors. No-op when the
 * tree is empty (e.g. before it's been fetched).
 */
export function linkifyVaultReferences(html: string, entries: TreeEntry[]): string {
  if (!html) return html;
  const { regex, lookup } = buildLookup(entries);
  if (!regex) return html;
  const tpl = document.createElement("template");
  tpl.innerHTML = html;
  walk(tpl.content, regex, lookup);
  return tpl.innerHTML;
}
