import { useEffect, useRef } from "react";
import { Wrench } from "lucide-react";

import type { ChatMessage, ContentBlock } from "@/lib/api";
import { renderMarkdown } from "@/lib/markdown";

interface Props {
  messages: ChatMessage[];
  streamingText?: string;
  pendingToolUse?: { name: string; input: Record<string, unknown> } | null;
  // True while a chat round is in flight (request → message_done). Lets us
  // show a "thinking" indicator before the first token arrives and during
  // the gap between rounds while a tool runs server-side.
  busy?: boolean;
  // Hide tool_use / tool_result blocks (and the pending-tool indicator)
  // unless this is true. Default off — clean conversational view.
  showToolDetails?: boolean;
}

function ThinkingDots() {
  return (
    <span className="sb-thinking" aria-label="Thinking">
      <span className="sb-thinking-dot" />
      <span className="sb-thinking-dot" />
      <span className="sb-thinking-dot" />
    </span>
  );
}

function MarkdownText({ text }: { text: string }) {
  return (
    <div
      className="prose-sb prose-sb-chat"
      dangerouslySetInnerHTML={{ __html: renderMarkdown(text) }}
    />
  );
}

function renderAssistantBlock(block: ContentBlock, idx: number, showToolDetails: boolean) {
  switch (block.type) {
    case "text":
      return <MarkdownText key={idx} text={block.text} />;
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
      if (!showToolDetails) return null;
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
      if (!showToolDetails) return null;
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

function renderUserBlock(block: ContentBlock, idx: number, showToolDetails: boolean) {
  switch (block.type) {
    case "text":
      // User messages stay plain text — preserves exactly what they typed.
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
    case "tool_result":
      // Tool results show up on the user side because the orchestrator
      // appends them as a synthetic user turn. Hide unless toggle is on.
      if (!showToolDetails) return null;
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
    case "tool_use":
      // Shouldn't occur on user messages, but bail safely.
      return null;
  }
}

function isVisibleBlock(block: ContentBlock, showToolDetails: boolean): boolean {
  if (showToolDetails) return true;
  return block.type !== "tool_use" && block.type !== "tool_result";
}

export default function MessageList({
  messages,
  streamingText,
  pendingToolUse,
  busy,
  showToolDetails = false,
}: Props) {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    ref.current?.scrollTo({ top: ref.current.scrollHeight, behavior: "smooth" });
  }, [messages, streamingText, pendingToolUse, busy]);

  // Thinking indicator: while the LLM is working but no text is currently
  // streaming. With showToolDetails off, also keep showing it during the
  // tool dispatch (since the pendingToolUse strip is hidden then).
  const showThinking =
    busy &&
    (showToolDetails ? !pendingToolUse : true) &&
    (streamingText === undefined || streamingText === "");

  return (
    <div ref={ref} className="flex-1 overflow-y-auto px-4 py-6">
      <div className="mx-auto flex max-w-3xl flex-col gap-6">
        {messages.map((m, i) => {
          const visible = m.content.filter((b) => isVisibleBlock(b, showToolDetails));
          if (visible.length === 0) return null;
          const isUser = m.role === "user";
          const renderer = isUser ? renderUserBlock : renderAssistantBlock;
          return (
            <div
              key={i}
              className={`flex ${isUser ? "justify-end" : "justify-start"}`}
            >
              <div
                className={`max-w-[85%] space-y-2 rounded-2xl px-4 py-3 ${
                  isUser
                    ? "bg-accent text-bg"
                    : "bg-surface text-text border border-border"
                }`}
              >
                {visible.map((b, j) => renderer(b, j, showToolDetails))}
              </div>
            </div>
          );
        })}

        {streamingText !== undefined && streamingText !== "" && (
          <div className="flex justify-start">
            <div className="max-w-[85%] rounded-2xl border border-border bg-surface px-4 py-3">
              <MarkdownText text={streamingText} />
            </div>
          </div>
        )}

        {showThinking && (
          <div className="flex justify-start">
            <div className="rounded-2xl border border-border bg-surface px-4 py-3">
              <ThinkingDots />
            </div>
          </div>
        )}

        {showToolDetails && pendingToolUse && (
          <div className="flex justify-start">
            <div className="rounded-lg border border-border bg-bg/40 px-3 py-2 text-xs text-muted">
              <div className="flex items-center gap-1">
                <Wrench className="h-3 w-3" />
                <span className="font-mono">{pendingToolUse.name}</span>
                <ThinkingDots />
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
