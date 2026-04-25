import { useEffect, useRef } from "react";
import { Wrench } from "lucide-react";

import type { ChatMessage, ContentBlock } from "@/lib/api";

interface Props {
  messages: ChatMessage[];
  streamingText?: string;
  pendingToolUse?: { name: string; input: Record<string, unknown> } | null;
}

function renderBlock(block: ContentBlock, idx: number) {
  switch (block.type) {
    case "text":
      return (
        <p key={idx} className="whitespace-pre-wrap leading-relaxed">
          {block.text}
        </p>
      );
    case "image":
      return (
        <img
          key={idx}
          src={`data:${block.mime};base64,${block.data}`}
          alt=""
          className="max-h-80 rounded-lg border border-border"
        />
      );
    case "tool_use":
      return (
        <div
          key={idx}
          className="rounded-lg border border-border bg-bg/40 px-3 py-2 text-xs text-muted"
        >
          <div className="flex items-center gap-1">
            <Wrench className="h-3 w-3" />
            <span className="font-mono">{block.name}</span>
          </div>
          <pre className="mt-1 overflow-x-auto whitespace-pre-wrap">
            {JSON.stringify(block.input, null, 2)}
          </pre>
        </div>
      );
    case "tool_result":
      return (
        <div
          key={idx}
          className={`rounded-lg border px-3 py-2 text-xs ${
            block.is_error
              ? "border-red-500/40 bg-red-500/10 text-red-300"
              : "border-border bg-bg/40 text-muted"
          }`}
        >
          {block.content.map((c, i) =>
            c.type === "text" ? <p key={i}>{c.text}</p> : null,
          )}
        </div>
      );
  }
}

export default function MessageList({ messages, streamingText, pendingToolUse }: Props) {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    ref.current?.scrollTo({ top: ref.current.scrollHeight, behavior: "smooth" });
  }, [messages, streamingText, pendingToolUse]);

  return (
    <div ref={ref} className="flex-1 overflow-y-auto px-4 py-6">
      <div className="mx-auto flex max-w-3xl flex-col gap-6">
        {messages.map((m, i) => (
          <div
            key={i}
            className={`flex ${m.role === "user" ? "justify-end" : "justify-start"}`}
          >
            <div
              className={`max-w-[85%] space-y-2 rounded-2xl px-4 py-3 ${
                m.role === "user"
                  ? "bg-accent text-bg"
                  : "bg-surface text-text border border-border"
              }`}
            >
              {m.content.map(renderBlock)}
            </div>
          </div>
        ))}

        {streamingText !== undefined && (
          <div className="flex justify-start">
            <div className="max-w-[85%] rounded-2xl border border-border bg-surface px-4 py-3">
              <p className="whitespace-pre-wrap leading-relaxed">
                {streamingText}
                <span className="ml-0.5 inline-block h-4 w-1 animate-pulse bg-accent align-middle" />
              </p>
            </div>
          </div>
        )}

        {pendingToolUse && (
          <div className="flex justify-start">
            <div className="rounded-lg border border-border bg-bg/40 px-3 py-2 text-xs text-muted">
              <div className="flex items-center gap-1">
                <Wrench className="h-3 w-3" />
                <span className="font-mono">{pendingToolUse.name}</span>
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
