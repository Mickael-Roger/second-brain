// Inline chat dialog about a selected text block from a Wiki page.
//
// Opens as a centered modal over the wiki view (does NOT switch to the
// general chat tab). The first user send is augmented with a context
// preamble (path + quoted selection) so the LLM knows exactly what
// excerpt the user is asking about; subsequent sends are just the
// user's input — the chat history server-side already carries the
// context.

import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Send, X } from "lucide-react";

import { useChatStream } from "@/lib/chat";
import type { ContentBlock, TreeEntry } from "@/lib/api";
import MessageList from "@/components/chat/MessageList";

const MAX_SELECTION_CHARS = 1500;

interface Props {
  path: string;
  selection: string;
  treeEntries?: TreeEntry[];
  // Click on a wikilink inside the assistant's reply → open the wiki page.
  // Receives the vault-relative path and is expected to close this dialog
  // before navigating, so the caller wires in `onClose + navigate`.
  onOpenWiki?: (path: string | null) => void;
  onClose: () => void;
}

function buildContextPreamble(path: string, selection: string): string {
  let text = selection.replace(/\s+$/g, "");
  if (text.length > MAX_SELECTION_CHARS) {
    text = text.slice(0, MAX_SELECTION_CHARS).trimEnd() + "…";
  }
  const quoted = text.split("\n").map((l) => `> ${l}`).join("\n");
  return (
    `Context: I'm reading the Wiki page \`${path}\` and have ` +
    `highlighted this excerpt:\n\n${quoted}\n\nUse \`vault.read("${path}")\` ` +
    `for the surrounding context if needed.\n\n---\n\n`
  );
}

export default function WikiSelectionChat({
  path,
  selection,
  treeEntries,
  onOpenWiki,
  onClose,
}: Props) {
  const { t } = useTranslation();
  const [input, setInput] = useState("");
  const [hasSentFirst, setHasSentFirst] = useState(false);
  const inputRef = useRef<HTMLTextAreaElement | null>(null);

  const { messages, streamingText, pendingToolUse, busy, send } = useChatStream({
    chatId: null,
    // Tag chats spawned from the wiki selection flow so we can
    // filter/find them later if needed.
    moduleId: "obsidian-wiki",
  });

  // Focus the input on open + close on Esc.
  useEffect(() => {
    inputRef.current?.focus();
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  async function handleSend() {
    const trimmed = input.trim();
    if (!trimmed || busy) return;
    setInput("");

    let blocks: ContentBlock[];
    if (!hasSentFirst) {
      // First send: prepend the context preamble (path + quoted
      // selection) so the LLM has the excerpt without us needing to
      // change /api/chat. Visible-to-the-LLM only — the input box
      // showed only what the user typed.
      const text = buildContextPreamble(path, selection) + trimmed;
      blocks = [{ type: "text", text }];
      setHasSentFirst(true);
    } else {
      blocks = [{ type: "text", text: trimmed }];
    }
    await send(blocks);
  }

  function onTextareaKey(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      void handleSend();
    }
  }

  // Strip the context preamble from displayed user messages so the
  // chat thread reads naturally (we only want to see what the user
  // typed, not the wrapping prose we sent to the API).
  const displayMessages = messages.map((m) => {
    if (m.role !== "user") return m;
    return {
      ...m,
      content: m.content.map((b) => {
        if (b.type !== "text") return b;
        const idx = b.text.indexOf("\n\n---\n\n");
        if (idx >= 0 && b.text.startsWith("Context: I'm reading the Wiki page")) {
          return { ...b, text: b.text.slice(idx + "\n\n---\n\n".length) };
        }
        return b;
      }),
    };
  });

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4"
      onClick={onClose}
    >
      <div
        className="flex h-[80vh] w-full max-w-2xl flex-col rounded-lg border border-border bg-surface shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <header className="flex items-center justify-between border-b border-border px-4 py-2.5">
          <div className="min-w-0 flex-1">
            <h2 className="truncate text-sm font-semibold">
              {t("wiki.selectionChatTitle")}
            </h2>
            <p className="truncate text-xs text-muted">
              <code>{path}</code>
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="ml-2 rounded p-1 text-muted hover:bg-bg hover:text-text"
            aria-label={t("common.cancel")}
          >
            <X className="h-4 w-4" />
          </button>
        </header>

        <div className="flex-1 overflow-y-auto px-4 py-3">
          <blockquote className="mb-3 rounded border-l-2 border-accent/60 bg-bg/60 px-3 py-2 text-xs text-muted whitespace-pre-wrap">
            {selection.length > MAX_SELECTION_CHARS
              ? selection.slice(0, MAX_SELECTION_CHARS).trimEnd() + "…"
              : selection}
          </blockquote>

          {messages.length === 0 && streamingText === null && !busy ? (
            <p className="text-xs text-muted italic">
              {t("wiki.selectionChatHint")}
            </p>
          ) : (
            <MessageList
              messages={displayMessages}
              streamingText={streamingText ?? undefined}
              pendingToolUse={pendingToolUse}
              busy={busy}
              onOpenWiki={onOpenWiki}
              vaultEntries={treeEntries}
            />
          )}
        </div>

        <footer className="border-t border-border p-2">
          <div className="flex items-end gap-2">
            <textarea
              ref={inputRef}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={onTextareaKey}
              rows={2}
              placeholder={t("wiki.selectionChatPlaceholder")}
              className="flex-1 resize-none rounded-md border border-border bg-bg px-2 py-1.5 text-sm focus:border-accent focus:outline-none"
              disabled={busy}
            />
            <button
              type="button"
              onClick={handleSend}
              disabled={busy || !input.trim()}
              className="flex items-center gap-1 rounded-md border border-accent bg-accent/10 px-3 py-2 text-sm text-accent hover:bg-accent/20 disabled:opacity-50"
            >
              <Send className="h-3.5 w-3.5" />
              {busy ? t("wiki.selectionChatBusy") : t("wiki.selectionChatSend")}
            </button>
          </div>
        </footer>
      </div>
    </div>
  );
}
