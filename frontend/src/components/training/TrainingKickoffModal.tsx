// Three-state modal for starting a new training theme:
//
//   subject  →  user types the theme they want to learn
//   chat     →  Socratic kickoff conversation with the LLM (running
//               under the "training-kickoff" system prompt + a single
//               tool: training.finalize_kickoff). The LLM asks 3-5
//               clarifying questions, then calls finalize_kickoff —
//               which writes Expectations.md and triggers Index.md
//               generation.
//   done     →  Index ready. CTA to open it in the wiki view.
//
// The modal closes only via the explicit X / Cancel button so the
// user doesn't lose their kickoff session by miss-clicking the
// backdrop. Once a generation is in flight (busy + finalize tool
// pending) we also disable Esc.

import { useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { useQueryClient } from "@tanstack/react-query";
import { Loader2, Send, Sparkles, X } from "lucide-react";

import type { ContentBlock } from "@/lib/api";
import { useChatStream } from "@/lib/chat";
import MessageList from "@/components/chat/MessageList";

interface Props {
  onClose: () => void;
  onOpenIndex: (path: string) => void;
}

interface KickoffResult {
  theme: string;
  expectations_path: string;
  index_path: string;
}

type Step = "subject" | "chat" | "done";

const FINALIZE_TOOL = "training.finalize_kickoff";

function buildOpener(subject: string, lang: string): string {
  if (lang.startsWith("fr")) {
    return (
      `Je veux démarrer un thème de training sur : « ${subject.trim()} ».\n\n` +
      `Pose-moi quelques questions de clarification, puis appelle ` +
      `${FINALIZE_TOOL} avec le markdown des attendus.`
    );
  }
  return (
    `I want to start a training theme on: "${subject.trim()}".\n\n` +
    `Ask me a few clarifying questions, then call ${FINALIZE_TOOL} ` +
    `with the expectations markdown.`
  );
}

function forceFinalizeText(lang: string): string {
  return lang.startsWith("fr")
    ? "OK, on a assez discuté. Finalise maintenant avec ce que tu as recueilli — appelle training.finalize_kickoff."
    : "OK, that's enough back-and-forth. Finalize now with what you've gathered — call training.finalize_kickoff.";
}

export default function TrainingKickoffModal({ onClose, onOpenIndex }: Props) {
  const { t, i18n } = useTranslation();
  const qc = useQueryClient();
  const [step, setStep] = useState<Step>("subject");
  const [subject, setSubject] = useState("");
  const [input, setInput] = useState("");
  const [result, setResult] = useState<KickoffResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const inputRef = useRef<HTMLTextAreaElement | null>(null);
  const subjectRef = useRef<HTMLInputElement | null>(null);

  const { messages, streamingText, pendingToolUse, busy, send } = useChatStream({
    chatId: null,
    moduleId: "training-kickoff",
    systemPromptId: "training-kickoff",
    onToolResult: (ev) => {
      // The kickoff session sees only one tool — finalize_kickoff.
      // On success its text payload is the JSON {theme, …}; on
      // error it's a human-readable message we surface in the chat
      // (the LLM also sees it and can retry with a different name).
      if (ev.is_error) {
        setError(ev.text);
        return;
      }
      try {
        const parsed = JSON.parse(ev.text) as KickoffResult;
        if (parsed?.index_path) {
          setResult(parsed);
          setStep("done");
          // The themes-list query needs to refetch to pick up the
          // brand-new theme card.
          qc.invalidateQueries({ queryKey: ["training-themes"] });
        }
      } catch {
        // Non-JSON success payload — leave the chat open so the user
        // can see what the LLM did.
      }
    },
  });

  const generating =
    step === "chat" && busy && pendingToolUse?.name === FINALIZE_TOOL;

  // Focus the right field as we transition between steps.
  useEffect(() => {
    if (step === "subject") subjectRef.current?.focus();
    else if (step === "chat") inputRef.current?.focus();
  }, [step]);

  // Esc closes — except while we're actively generating the fiche
  // (would lose the kickoff session).
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !generating) onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose, generating]);

  async function startKickoff() {
    const trimmed = subject.trim();
    if (!trimmed) return;
    setStep("chat");
    setError(null);
    const text = buildOpener(trimmed, i18n.language);
    await send([{ type: "text", text }]);
  }

  async function handleSend() {
    const trimmed = input.trim();
    if (!trimmed || busy) return;
    setInput("");
    setError(null);
    const blocks: ContentBlock[] = [{ type: "text", text: trimmed }];
    await send(blocks);
  }

  async function handleForceFinalize() {
    if (busy) return;
    setError(null);
    await send([{ type: "text", text: forceFinalizeText(i18n.language) }]);
  }

  function onTextareaKey(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      void handleSend();
    }
  }

  function onSubjectKey(e: React.KeyboardEvent<HTMLInputElement>) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      void startKickoff();
    }
  }

  // Hide the synthetic opener message we sent on behalf of the user
  // (the subject field already carries that info visibly in the
  // header).
  const displayMessages = useMemo(() => {
    if (messages.length === 0) return messages;
    return messages.map((m, i) =>
      i === 0 && m.role === "user"
        ? {
            ...m,
            content: [
              {
                type: "text" as const,
                text: subject.trim()
                  ? subject.trim()
                  : m.content
                      .filter((b): b is { type: "text"; text: string } => b.type === "text")
                      .map((b) => b.text)
                      .join(""),
              },
            ],
          }
        : m,
    );
  }, [messages, subject]);

  // ─── render ──────────────────────────────────────────────────────
  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4"
      onClick={() => {
        if (!generating && step !== "chat") onClose();
      }}
    >
      <div
        className={`flex w-full flex-col rounded-lg border border-border bg-surface shadow-xl ${
          step === "subject" ? "max-w-md" : "h-[80vh] max-w-2xl"
        }`}
        onClick={(e) => e.stopPropagation()}
      >
        {/* ─── Header ─────────────────────────────────────────────── */}
        <header className="flex items-center justify-between border-b border-border px-4 py-3">
          <div className="flex min-w-0 items-center gap-2">
            <Sparkles className="h-4 w-4 shrink-0 text-accent" />
            <h2 className="truncate text-sm font-medium">
              {step === "subject"
                ? t("training.kickoff.title")
                : t("training.kickoff.titleWithSubject", {
                    subject: subject || t("training.kickoff.untitled"),
                  })}
            </h2>
          </div>
          <button
            type="button"
            onClick={onClose}
            disabled={generating}
            className="text-muted hover:text-text disabled:opacity-30"
            aria-label={t("common.cancel")}
          >
            <X className="h-4 w-4" />
          </button>
        </header>

        {/* ─── Body — varies per step ─────────────────────────────── */}
        {step === "subject" && (
          <>
            <div className="space-y-3 px-4 py-4">
              <p className="text-xs text-muted">{t("training.kickoff.lead")}</p>
              <label className="flex flex-col gap-1 text-xs text-muted">
                <span>{t("training.kickoff.subjectLabel")}</span>
                <input
                  ref={subjectRef}
                  type="text"
                  value={subject}
                  onChange={(e) => setSubject(e.target.value)}
                  onKeyDown={onSubjectKey}
                  placeholder={t("training.kickoff.subjectPlaceholder")}
                  className="rounded border border-border bg-bg px-2 py-1.5 text-sm text-text outline-none placeholder:text-muted/60 focus:border-accent"
                />
              </label>
              <p className="text-[11px] text-muted">
                {t("training.kickoff.startHint")}
              </p>
            </div>
            <div className="flex items-center justify-end gap-2 border-t border-border px-4 py-3">
              <button
                type="button"
                onClick={onClose}
                className="rounded border border-border px-3 py-1 text-xs text-muted hover:border-accent hover:text-text"
              >
                {t("common.cancel")}
              </button>
              <button
                type="button"
                onClick={startKickoff}
                disabled={!subject.trim()}
                className="flex items-center gap-1.5 rounded bg-accent px-3 py-1 text-xs text-white hover:bg-accent/90 disabled:opacity-40"
              >
                <Sparkles className="h-3.5 w-3.5" />
                <span>{t("training.kickoff.start")}</span>
              </button>
            </div>
          </>
        )}

        {step === "chat" && (
          <>
            <div className="relative flex-1 overflow-y-auto px-4 py-3">
              {messages.length === 0 && streamingText === null && busy && (
                <p className="text-xs text-muted italic">
                  {t("training.kickoff.starting")}
                </p>
              )}
              {messages.length > 0 && (
                <MessageList
                  messages={displayMessages}
                  streamingText={streamingText ?? undefined}
                  pendingToolUse={
                    pendingToolUse?.name === FINALIZE_TOOL ? null : pendingToolUse
                  }
                  busy={busy && pendingToolUse?.name !== FINALIZE_TOOL}
                />
              )}
              {error && (
                <p className="mt-2 rounded border border-red-500/40 bg-red-500/10 px-2 py-1 text-xs text-red-400">
                  {error}
                </p>
              )}
              {generating && (
                <div className="absolute inset-0 flex flex-col items-center justify-center gap-3 bg-surface/95 text-center">
                  <Loader2 className="h-6 w-6 animate-spin text-accent" />
                  <p className="max-w-xs text-sm text-text">
                    {t("training.kickoff.generating")}
                  </p>
                  <p className="max-w-xs text-xs text-muted">
                    {t("training.kickoff.generatingHint")}
                  </p>
                </div>
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
                  placeholder={t("training.kickoff.replyPlaceholder")}
                  className="flex-1 resize-none rounded-md border border-border bg-bg px-2 py-1.5 text-sm focus:border-accent focus:outline-none disabled:opacity-50"
                  disabled={busy}
                />
                <button
                  type="button"
                  onClick={handleSend}
                  disabled={busy || !input.trim()}
                  className="flex items-center gap-1 rounded-md border border-accent bg-accent/10 px-3 py-2 text-sm text-accent hover:bg-accent/20 disabled:opacity-50"
                >
                  <Send className="h-3.5 w-3.5" />
                </button>
              </div>
              <div className="mt-2 flex justify-end">
                <button
                  type="button"
                  onClick={handleForceFinalize}
                  disabled={busy || messages.length < 2}
                  className="text-[11px] text-muted underline-offset-2 hover:text-accent hover:underline disabled:opacity-40 disabled:no-underline"
                  title={t("training.kickoff.forceFinalizeHint")}
                >
                  {t("training.kickoff.forceFinalize")}
                </button>
              </div>
            </footer>
          </>
        )}

        {step === "done" && result && (
          <>
            <div className="flex flex-1 flex-col items-center justify-center gap-3 px-6 py-8 text-center">
              <Sparkles className="h-8 w-8 text-accent" />
              <h3 className="text-base font-semibold">
                {t("training.kickoff.doneTitle", { theme: result.theme })}
              </h3>
              <p className="max-w-sm text-sm text-muted">
                {t("training.kickoff.doneLead")}
              </p>
              <p className="text-[11px] text-muted">
                <code>{result.index_path}</code>
              </p>
            </div>
            <div className="flex items-center justify-end gap-2 border-t border-border px-4 py-3">
              <button
                type="button"
                onClick={onClose}
                className="rounded border border-border px-3 py-1 text-xs text-muted hover:border-accent hover:text-text"
              >
                {t("common.close")}
              </button>
              <button
                type="button"
                onClick={() => onOpenIndex(result.index_path)}
                className="flex items-center gap-1.5 rounded bg-accent px-3 py-1 text-xs text-white hover:bg-accent/90"
              >
                <span>{t("training.kickoff.openIndex")}</span>
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
