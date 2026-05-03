// Shared chat streaming hook used by both the full Chat module view and the
// inline chatbox in non-chat modules.

import { useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";

import {
  api,
  type ChatDetail,
  type ChatMessage,
  type ContentBlock,
} from "./api";
import { streamSse } from "./sse";

export interface ToolResultEvent {
  tool_use_id: string;
  is_error: boolean;
  // Concatenated text content of the tool result (we ignore non-text
  // blocks here — the consumer can re-fetch the full message if it
  // needs richer parsing).
  text: string;
}

export interface UseChatStreamOptions {
  // Existing chat to load, or null to start fresh on first send.
  chatId?: string | null;
  // Tags new chats so they live under a specific module (e.g. "obsidian").
  moduleId?: string | null;
  // Server-side whitelisted system prompt key (e.g. "training-kickoff").
  // When set, the backend swaps the system prompt and restricts the
  // tool surface accordingly.
  systemPromptId?: string | null;
  // "provider/model" — split before sending. undefined = backend default.
  selection?: string;
  // Called when a new chat row is created server-side.
  onChatCreated?: (id: string) => void;
  // Fired whenever the stream emits a tool_result event. Consumers
  // (e.g. the kickoff modal) use this to detect completion of a
  // specific tool call without having to parse the full transcript.
  onToolResult?: (ev: ToolResultEvent) => void;
}

export interface UseChatStream {
  messages: ChatMessage[];
  streamingText: string | null;
  pendingToolUse: { name: string; input: Record<string, unknown> } | null;
  busy: boolean;
  send: (blocks: ContentBlock[]) => Promise<void>;
  stop: () => void;
  reset: () => void;
}

export function useChatStream({
  chatId,
  moduleId,
  systemPromptId,
  selection,
  onChatCreated,
  onToolResult,
}: UseChatStreamOptions): UseChatStream {
  const qc = useQueryClient();

  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [streamingText, setStreamingText] = useState<string | null>(null);
  const [pendingToolUse, setPendingToolUse] = useState<UseChatStream["pendingToolUse"]>(null);
  const [busy, setBusy] = useState(false);
  const abortRef = useRef<AbortController | null>(null);

  // Load existing transcript when switching chats.
  useEffect(() => {
    let cancelled = false;
    if (!chatId) {
      setMessages([]);
      setStreamingText(null);
      setPendingToolUse(null);
      return;
    }
    void api
      .get<ChatDetail>(`/api/chats/${chatId}`)
      .then((d) => {
        if (!cancelled) setMessages(d.messages);
      })
      .catch(() => {
        // Silent — caller will see an empty list.
      });
    return () => {
      cancelled = true;
    };
  }, [chatId]);

  function reset() {
    abortRef.current?.abort();
    setMessages([]);
    setStreamingText(null);
    setPendingToolUse(null);
    setBusy(false);
  }

  function stop() {
    abortRef.current?.abort();
  }

  async function send(blocks: ContentBlock[]) {
    if (busy || blocks.length === 0) return;
    setBusy(true);
    setStreamingText("");
    setPendingToolUse(null);
    setMessages((m) => [...m, { role: "user", content: blocks }]);

    const ctrl = new AbortController();
    abortRef.current = ctrl;

    let createdId: string | null = chatId ?? null;
    let textBuf = "";

    const [provider, model] = (selection ?? "/").split("/", 2);

    try {
      await streamSse(
        "/api/chat",
        {
          chat_id: chatId ?? undefined,
          module_id: moduleId ?? undefined,
          system_prompt_id: systemPromptId ?? undefined,
          provider: provider || undefined,
          model: model || undefined,
          content: blocks,
        },
        {
          signal: ctrl.signal,
          onEvent: (ev) => {
            switch (ev.event) {
              case "chat": {
                const d = ev.data as { id: string; title: string };
                if (!createdId) {
                  createdId = d.id;
                  onChatCreated?.(d.id);
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
                if (onToolResult) {
                  const d = ev.data as {
                    tool_use_id: string;
                    is_error: boolean;
                    content: ContentBlock[];
                  };
                  const text = (d.content ?? [])
                    .filter((b): b is { type: "text"; text: string } => b.type === "text")
                    .map((b) => b.text)
                    .join("");
                  onToolResult({
                    tool_use_id: d.tool_use_id,
                    is_error: !!d.is_error,
                    text,
                  });
                }
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

  return { messages, streamingText, pendingToolUse, busy, send, stop, reset };
}

// Pure helper, used by ChatMain & InlineChatBox to render the model picker.
export function useDefaultSelection(
  providers:
    | { name: string; default_model: string; is_default: boolean; models: string[] }[]
    | undefined,
  current: string | undefined,
  setCurrent: (v: string) => void,
) {
  useEffect(() => {
    if (!current && providers && providers.length > 0) {
      const def = providers.find((p) => p.is_default) ?? providers[0];
      setCurrent(`${def.name}/${def.default_model}`);
    }
  }, [providers, current, setCurrent]);
}
