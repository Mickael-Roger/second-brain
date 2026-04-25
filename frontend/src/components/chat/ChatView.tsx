// Full chat surface: history sidebar + active conversation pane.

import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { useQuery } from "@tanstack/react-query";
import { Menu, Wrench, X } from "lucide-react";

import { api, type ChatDetail, type ProviderInfo, type TreeEntry } from "@/lib/api";
import { useChatStream, useDefaultSelection } from "@/lib/chat";
import MessageList from "@/components/chat/MessageList";
import Composer from "@/components/chat/Composer";
import ChatHistory from "@/components/chat/ChatHistory";

const TOOL_DETAILS_KEY = "sb.chat.showToolDetails";

interface Props {
  onOpenWiki?: (path: string | null) => void;
}

export default function ChatView({ onOpenWiki }: Props) {
  const { t } = useTranslation();
  const [activeChatId, setActiveChatId] = useState<string | null>(null);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [showToolDetails, setShowToolDetails] = useState<boolean>(() => {
    if (typeof window === "undefined") return false;
    return window.localStorage.getItem(TOOL_DETAILS_KEY) === "1";
  });

  useEffect(() => {
    if (typeof window === "undefined") return;
    window.localStorage.setItem(TOOL_DETAILS_KEY, showToolDetails ? "1" : "0");
  }, [showToolDetails]);

  // Selecting / starting a chat closes the mobile drawer.
  useEffect(() => {
    setHistoryOpen(false);
  }, [activeChatId]);

  const providers = useQuery({
    queryKey: ["providers"],
    queryFn: () => api.get<ProviderInfo[]>("/api/llm/providers"),
  });
  // Vault tree feeds auto-linkification of plain-text note mentions in
  // assistant replies. Shared cache key with the wiki view so we don't
  // double-fetch.
  const vaultTree = useQuery({
    queryKey: ["vault-tree"],
    queryFn: () => api.get<TreeEntry[]>("/api/vault/tree"),
    staleTime: 30_000,
  });
  const [selection, setSelection] = useState<string | undefined>();
  useDefaultSelection(providers.data, selection, setSelection);

  const providerOptions = (providers.data ?? []).flatMap((p) =>
    p.models.map((m) => ({ value: `${p.name}/${m}`, label: `${p.name}/${m}` })),
  );

  const detail = useQuery<ChatDetail | null>({
    queryKey: ["chat", activeChatId],
    queryFn: async () =>
      activeChatId ? api.get<ChatDetail>(`/api/chats/${activeChatId}`) : null,
    enabled: activeChatId !== null,
  });

  const { messages, streamingText, pendingToolUse, busy, send, stop } = useChatStream({
    chatId: activeChatId,
    selection,
    onChatCreated: setActiveChatId,
  });

  const history = (
    <ChatHistory
      activeChatId={activeChatId}
      onSelect={setActiveChatId}
      onNew={() => setActiveChatId(null)}
    />
  );

  return (
    <div className="flex h-full">
      <aside className="hidden w-64 shrink-0 border-r border-border bg-surface md:block">
        {history}
      </aside>

      {historyOpen && (
        <div
          className="fixed inset-0 z-40 bg-black/60 md:hidden"
          onClick={() => setHistoryOpen(false)}
        >
          <aside
            className="flex h-full w-72 flex-col border-r border-border bg-surface"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center justify-between border-b border-border px-3 py-2">
              <span className="text-sm font-medium">{t("sidebar.chats")}</span>
              <button
                type="button"
                onClick={() => setHistoryOpen(false)}
                className="text-muted"
                aria-label={t("common.cancel")}
              >
                <X className="h-5 w-5" />
              </button>
            </div>
            {history}
          </aside>
        </div>
      )}

      <main className="flex h-full min-w-0 flex-1 flex-col">
        <header className="flex items-center gap-2 border-b border-border bg-surface px-3 py-2 md:px-4">
          <button
            type="button"
            onClick={() => setHistoryOpen(true)}
            className="text-muted md:hidden"
            aria-label={t("sidebar.chats")}
          >
            <Menu className="h-5 w-5" />
          </button>
          <div className="flex-1 truncate text-sm text-muted">
            {detail.data?.title ?? t("sidebar.newChat")}
          </div>
          <button
            type="button"
            onClick={() => setShowToolDetails((v) => !v)}
            title={t("chat.toolDetailsToggle")}
            aria-pressed={showToolDetails}
            className={`flex items-center justify-center rounded border px-2 py-1 text-xs ${
              showToolDetails
                ? "border-accent text-accent"
                : "border-border text-muted hover:border-accent hover:text-text"
            }`}
          >
            <Wrench className="h-3.5 w-3.5" />
          </button>
          {providerOptions.length > 0 && (
            <select
              value={selection}
              onChange={(e) => setSelection(e.target.value)}
              className="rounded border border-border bg-bg px-2 py-1 text-xs"
              aria-label={t("chat.model")}
            >
              {providerOptions.map((opt) => (
                <option key={opt.value} value={opt.value}>
                  {opt.label}
                </option>
              ))}
            </select>
          )}
        </header>

        {messages.length === 0 && streamingText === null && !busy ? (
          <div className="flex flex-1 items-center justify-center px-6 text-center text-muted">
            <p>{t("chat.empty")}</p>
          </div>
        ) : (
          <MessageList
            messages={messages}
            streamingText={streamingText ?? undefined}
            pendingToolUse={pendingToolUse}
            busy={busy}
            showToolDetails={showToolDetails}
            onOpenWiki={onOpenWiki}
            vaultEntries={vaultTree.data ?? []}
          />
        )}

        <Composer onSend={send} onStop={stop} busy={busy} />
      </main>
    </div>
  );
}
