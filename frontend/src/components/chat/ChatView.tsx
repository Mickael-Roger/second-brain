// Full chat surface: history sidebar + active conversation pane.

import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useQuery } from "@tanstack/react-query";

import { api, type ChatDetail, type ProviderInfo } from "@/lib/api";
import { useChatStream, useDefaultSelection } from "@/lib/chat";
import MessageList from "@/components/chat/MessageList";
import Composer from "@/components/chat/Composer";
import ChatHistory from "@/components/chat/ChatHistory";

export default function ChatView() {
  const { t } = useTranslation();
  const [activeChatId, setActiveChatId] = useState<string | null>(null);

  const providers = useQuery({
    queryKey: ["providers"],
    queryFn: () => api.get<ProviderInfo[]>("/api/llm/providers"),
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

  return (
    <div className="flex h-full">
      <aside className="hidden w-64 shrink-0 border-r border-border bg-surface md:block">
        <ChatHistory
          activeChatId={activeChatId}
          onSelect={setActiveChatId}
          onNew={() => setActiveChatId(null)}
        />
      </aside>

      <main className="flex h-full min-w-0 flex-1 flex-col">
        <header className="flex items-center gap-2 border-b border-border bg-surface px-4 py-2">
          <div className="flex-1 truncate text-sm text-muted">
            {detail.data?.title ?? t("sidebar.newChat")}
          </div>
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

        {messages.length === 0 && streamingText === null ? (
          <div className="flex flex-1 items-center justify-center px-6 text-center text-muted">
            <p>{t("chat.empty")}</p>
          </div>
        ) : (
          <MessageList
            messages={messages}
            streamingText={streamingText ?? undefined}
            pendingToolUse={pendingToolUse}
          />
        )}

        <Composer onSend={send} onStop={stop} busy={busy} />
      </main>
    </div>
  );
}
