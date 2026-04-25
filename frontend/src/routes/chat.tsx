import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Menu } from "lucide-react";

import {
  api,
  type ChatDetail,
  type ChatMessage,
  type ContentBlock,
  type ProviderInfo,
} from "@/lib/api";
import { streamSse } from "@/lib/sse";
import MessageList from "@/components/chat/MessageList";
import Composer from "@/components/chat/Composer";

interface Props {
  chatId: string | null;
  onChatCreated: (id: string) => void;
  onOpenSidebar: () => void;
}

export default function ChatPage({ chatId, onChatCreated, onOpenSidebar }: Props) {
  const { t } = useTranslation();
  const qc = useQueryClient();

  const providers = useQuery({
    queryKey: ["providers"],
    queryFn: () => api.get<ProviderInfo[]>("/api/llm/providers"),
  });
  // Selection is the canonical "provider/model" string we send to the backend.
  const [selection, setSelection] = useState<string | undefined>();
  useEffect(() => {
    if (!selection && providers.data && providers.data.length > 0) {
      const def = providers.data.find((p) => p.is_default) ?? providers.data[0];
      setSelection(`${def.name}/${def.default_model}`);
    }
  }, [providers.data, selection]);

  const providerOptions = (providers.data ?? []).flatMap((p) =>
    p.models.map((m) => ({ value: `${p.name}/${m}`, label: `${p.name}/${m}` })),
  );

  const detail = useQuery<ChatDetail | null>({
    queryKey: ["chat", chatId],
    queryFn: async () => (chatId ? api.get<ChatDetail>(`/api/chats/${chatId}`) : null),
    enabled: chatId !== null,
  });

  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [streamingText, setStreamingText] = useState<string | null>(null);
  const [pendingToolUse, setPendingToolUse] = useState<{
    name: string;
    input: Record<string, unknown>;
  } | null>(null);
  const [busy, setBusy] = useState(false);
  const abortRef = useRef<AbortController | null>(null);

  // Reset local state on chat change
  useEffect(() => {
    if (chatId === null) {
      setMessages([]);
      setStreamingText(null);
      setPendingToolUse(null);
      return;
    }
    if (detail.data) setMessages(detail.data.messages);
  }, [chatId, detail.data]);

  async function onSend(blocks: ContentBlock[]) {
    setBusy(true);
    setStreamingText("");
    setPendingToolUse(null);

    const userMsg: ChatMessage = { role: "user", content: blocks };
    setMessages((m) => [...m, userMsg]);

    const ctrl = new AbortController();
    abortRef.current = ctrl;

    let createdId: string | null = chatId;
    let textBuf = "";

    const [provider, model] = (selection ?? "/").split("/", 2);

    try {
      await streamSse(
        "/api/chat",
        {
          chat_id: chatId ?? undefined,
          provider: provider || undefined,
          model: model || undefined,
          content: blocks.map((b) =>
            b.type === "text" || b.type === "image" ? b : { type: "text", text: "" },
          ),
        },
        {
          signal: ctrl.signal,
          onEvent: (ev) => {
            switch (ev.event) {
              case "chat": {
                const d = ev.data as { id: string; title: string };
                if (!createdId) {
                  createdId = d.id;
                  onChatCreated(d.id);
                  qc.invalidateQueries({ queryKey: ["chats"] });
                }
                break;
              }
              case "text_delta": {
                const d = ev.data as { text: string };
                textBuf += d.text;
                setStreamingText(textBuf);
                break;
              }
              case "tool_use": {
                const d = ev.data as { name: string; input: Record<string, unknown> };
                setPendingToolUse({ name: d.name, input: d.input });
                break;
              }
              case "tool_result": {
                setPendingToolUse(null);
                break;
              }
              case "message_done": {
                const d = ev.data as ChatMessage;
                setMessages((m) => [...m, d]);
                setStreamingText(null);
                textBuf = "";
                setPendingToolUse(null);
                break;
              }
              case "error": {
                const d = ev.data as { error: string };
                setMessages((m) => [
                  ...m,
                  {
                    role: "assistant",
                    content: [{ type: "text", text: `⚠️ ${d.error}` }],
                  },
                ]);
                setStreamingText(null);
                textBuf = "";
                break;
              }
              case "done":
                break;
            }
          },
        },
      );
    } catch (err) {
      if ((err as Error).name !== "AbortError") {
        setMessages((m) => [
          ...m,
          {
            role: "assistant",
            content: [{ type: "text", text: `⚠️ ${(err as Error).message}` }],
          },
        ]);
      }
    } finally {
      setBusy(false);
      setStreamingText(null);
      setPendingToolUse(null);
      abortRef.current = null;
      qc.invalidateQueries({ queryKey: ["chats"] });
    }
  }

  function onStop() {
    abortRef.current?.abort();
  }

  return (
    <div className="flex h-full flex-col">
      <header className="flex items-center gap-2 border-b border-border bg-surface px-3 py-2">
        <button className="md:hidden text-muted" onClick={onOpenSidebar} aria-label="Menu">
          <Menu className="h-5 w-5" />
        </button>
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

      <Composer onSend={onSend} onStop={onStop} busy={busy} />
    </div>
  );
}
