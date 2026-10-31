import { useMemo } from "react";

function mediaUrl(filename: string): string {
  if (/^(?:https?:|data:|blob:|\/|#)/i.test(filename)) return filename;
  let decoded = filename;
  try { decoded = decodeURIComponent(filename); } catch { /* Keep literal filenames. */ }
  return `/api/anki/media?${new URLSearchParams({ filename: decoded })}`;
}

function rewriteCss(css: string): string {
  return css.replace(/url\(\s*["']?([^"')]+)["']?\s*\)/gi,
    (_, filename: string) => `url("${mediaUrl(filename.trim()).replaceAll('"', "%22")}")`);
}

export function cardText(html: string): string {
  const doc = new DOMParser().parseFromString(html, "text/html");
  doc.querySelectorAll("script, style").forEach((node) => node.remove());
  return (doc.body.textContent || "").replace(/\s+/g, " ").trim();
}

function cardDocument(html: string, css: string): string {
  const doc = new DOMParser().parseFromString(html, "text/html");
  doc.body.classList.add("card");
  doc.querySelectorAll("script, base, iframe, object, embed, meta, link").forEach((node) => node.remove());
  doc.querySelectorAll("[src], [poster]").forEach((node) => {
    for (const attr of ["src", "poster"]) {
      const value = node.getAttribute(attr);
      if (value) node.setAttribute(attr, mediaUrl(value));
    }
    node.removeAttribute("srcset");
  });
  doc.querySelectorAll("style").forEach((node) => { node.textContent = rewriteCss(node.textContent || ""); });
  doc.querySelectorAll("[style]").forEach((node) => {
    node.setAttribute("style", rewriteCss(node.getAttribute("style") || ""));
  });
  const walker = doc.createTreeWalker(doc.body, NodeFilter.SHOW_TEXT);
  const soundNodes: Text[] = [];
  while (walker.nextNode()) {
    const node = walker.currentNode as Text;
    if (node.parentElement?.tagName !== "STYLE" && /\[sound:[^\]]+\]/.test(node.data)) soundNodes.push(node);
  }
  soundNodes.forEach((node) => {
    const fragment = doc.createDocumentFragment();
    for (const part of node.data.split(/(\[sound:[^\]]+\])/g)) {
      if (part.startsWith("[sound:")) {
        const audio = doc.createElement("audio");
        audio.controls = true;
        audio.preload = "none";
        audio.src = mediaUrl(part.slice(7, -1));
        fragment.append(audio);
      } else fragment.append(doc.createTextNode(part));
    }
    node.replaceWith(fragment);
  });
  const style = doc.createElement("style");
  style.textContent = `body { margin: 16px; font: 18px/1.6 system-ui; overflow-wrap: anywhere; }
    img, video, audio { max-width: 100%; } pre { white-space: pre-wrap; }
    ${rewriteCss(css)}`;
  doc.head.append(style);
  return `<!doctype html>${doc.documentElement.outerHTML}`;
}

export default function AnkiCardContent({ html, css, title }: { html: string; css: string; title: string }) {
  const content = useMemo(() => cardDocument(html, css), [html, css]);
  return <iframe title={title} sandbox="allow-same-origin" srcDoc={content}
    className="h-[50dvh] min-h-64 w-full rounded-lg border border-border bg-white text-black" />;
}
