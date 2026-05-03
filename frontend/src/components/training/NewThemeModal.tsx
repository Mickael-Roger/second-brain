// Seeds a new training theme by handing the user off to the chat with
// a pre-filled prompt — the LLM creates Training/<Theme>/Index.md via
// its existing vault.* tools (see TRAINING.md). We don't call the
// backend directly here: the conversational round-trip lets the user
// refine the scope before the brain commits anything.

import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Sparkles, X } from "lucide-react";

interface Props {
  onClose: () => void;
  onStartInChat: () => void;
}

const DRAFT_KEY = "sb.chat.draft";

function buildPrompt(subject: string, lang: string): string {
  const trimmed = subject.trim();
  if (lang.startsWith("fr")) {
    return (
      `Crée un nouveau thème de training : « ${trimmed} ».\n\n` +
      `Crée le fichier Training/<Theme>/Index.md avec :\n` +
      `- une overview honnête et calibrée du sujet,\n` +
      `- une section "## À explorer" listant 4 à 8 wikilinks vers des sous-concepts naturels.\n\n` +
      `Choisis un nom de dossier court et propre pour <Theme>.`
    );
  }
  return (
    `Start a new training theme: "${trimmed}".\n\n` +
    `Create Training/<Theme>/Index.md with:\n` +
    `- an honest, calibrated overview of the subject,\n` +
    `- a "## Going deeper" section listing 4–8 wikilinks to natural sub-concepts.\n\n` +
    `Pick a short, clean folder name for <Theme>.`
  );
}

export default function NewThemeModal({ onClose, onStartInChat }: Props) {
  const { t, i18n } = useTranslation();
  const [subject, setSubject] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    inputRef.current?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const submit = () => {
    if (!subject.trim()) return;
    const prompt = buildPrompt(subject, i18n.language);
    window.localStorage.setItem(DRAFT_KEY, prompt);
    onStartInChat();
  };

  const onKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      submit();
    }
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 px-4"
      onClick={onClose}
    >
      <div
        className="w-full max-w-md rounded-lg border border-border bg-surface shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between border-b border-border px-4 py-3">
          <div className="flex items-center gap-2">
            <Sparkles className="h-4 w-4 text-accent" />
            <h2 className="text-sm font-medium">{t("training.newTheme.title")}</h2>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="text-muted hover:text-text"
            aria-label={t("common.cancel")}
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="space-y-3 px-4 py-4">
          <p className="text-xs text-muted">{t("training.newTheme.lead")}</p>

          <label className="flex flex-col gap-1 text-xs text-muted">
            <span>{t("training.newTheme.subjectLabel")}</span>
            <input
              ref={inputRef}
              type="text"
              value={subject}
              onChange={(e) => setSubject(e.target.value)}
              onKeyDown={onKeyDown}
              placeholder={t("training.newTheme.subjectPlaceholder")}
              className="rounded border border-border bg-bg px-2 py-1.5 text-sm text-text outline-none placeholder:text-muted/60 focus:border-accent"
            />
          </label>

          <p className="text-[11px] text-muted">{t("training.newTheme.submitHint")}</p>
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
            onClick={submit}
            disabled={!subject.trim()}
            className="flex items-center gap-1.5 rounded bg-accent px-3 py-1 text-xs text-white hover:bg-accent/90 disabled:opacity-40"
          >
            <Sparkles className="h-3.5 w-3.5" />
            <span>{t("training.newTheme.submit")}</span>
          </button>
        </div>
      </div>
    </div>
  );
}
