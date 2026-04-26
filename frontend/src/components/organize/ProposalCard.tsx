// One card per LLM-proposed change. Lets the user see what would happen
// (move_to, tags, wikilinks, refactor diff), discard it, apply it
// individually, or send a revision instruction back to the LLM.

import { useState } from "react";
import { useTranslation } from "react-i18next";
import {
  ArrowRight,
  Check,
  Circle,
  FileText,
  MessageSquare,
  Send,
  Tag,
  X,
} from "lucide-react";

import type { OrganizeProposal } from "@/lib/api";

interface Props {
  proposal: OrganizeProposal;
  onDiscard: (path: string) => void;
  onApply: (path: string) => void;
  onRevise: (path: string, instruction: string) => Promise<void>;
  onOpenWiki: (path: string | null) => void;
  busy?: boolean;
}

function StateBadge({ state }: { state: OrganizeProposal["state"] }) {
  const palette: Record<OrganizeProposal["state"], string> = {
    pending: "border-border text-muted",
    applied: "border-green-500/40 text-green-300 bg-green-500/10",
    discarded: "border-border text-muted/60 bg-bg/40",
    failed: "border-red-500/40 text-red-300 bg-red-500/10",
  };
  return (
    <span
      className={`rounded border px-1.5 py-0.5 text-[10px] uppercase tracking-wide ${palette[state]}`}
    >
      {state}
    </span>
  );
}

export default function ProposalCard({
  proposal,
  onDiscard,
  onApply,
  onRevise,
  onOpenWiki,
  busy,
}: Props) {
  const { t } = useTranslation();
  const [showRefactor, setShowRefactor] = useState(false);
  const [reviseOpen, setReviseOpen] = useState(false);
  const [instruction, setInstruction] = useState("");
  const [revising, setRevising] = useState(false);
  const [reviseError, setReviseError] = useState<string | null>(null);

  const noChanges =
    !proposal.move_to &&
    !proposal.tags &&
    !proposal.refactor &&
    proposal.wikilinks.length === 0 &&
    !proposal.notes;

  const isPending = proposal.state === "pending";
  const canApply = isPending && !noChanges && !proposal.parse_error;

  async function handleRevise(e: React.FormEvent) {
    e.preventDefault();
    if (!instruction.trim() || revising) return;
    setRevising(true);
    setReviseError(null);
    try {
      await onRevise(proposal.path, instruction.trim());
      setInstruction("");
      setReviseOpen(false);
    } catch (err) {
      setReviseError((err as Error).message);
    } finally {
      setRevising(false);
    }
  }

  return (
    <article
      className={`rounded-xl border bg-surface p-4 ${
        proposal.state === "discarded"
          ? "border-border/60 opacity-50"
          : "border-border"
      }`}
    >
      <header className="mb-3 flex items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <button
            type="button"
            onClick={() => onOpenWiki(proposal.path)}
            className="flex items-center gap-1.5 truncate text-left text-sm font-medium text-accent hover:underline"
          >
            <FileText className="h-3.5 w-3.5 shrink-0" />
            <span className="truncate">{proposal.path}</span>
          </button>
        </div>
        <div className="flex items-center gap-2">
          <StateBadge state={proposal.state} />
          {isPending && (
            <>
              <button
                type="button"
                onClick={() => setReviseOpen((v) => !v)}
                disabled={busy || revising}
                title={t("organize.reviseThis")}
                aria-label={t("organize.reviseThis")}
                className={`rounded border p-1 hover:border-accent hover:text-text ${
                  reviseOpen ? "border-accent text-accent" : "border-border text-muted"
                }`}
              >
                <MessageSquare className="h-3.5 w-3.5" />
              </button>
              <button
                type="button"
                onClick={() => onDiscard(proposal.path)}
                disabled={busy || revising}
                title={t("organize.discardThis")}
                aria-label={t("organize.discardThis")}
                className="rounded border border-border p-1 text-muted hover:border-red-400 hover:text-red-300"
              >
                <X className="h-3.5 w-3.5" />
              </button>
              <button
                type="button"
                onClick={() => onApply(proposal.path)}
                disabled={busy || revising || !canApply}
                title={t("organize.applyThis")}
                aria-label={t("organize.applyThis")}
                className="flex items-center gap-1 rounded bg-accent px-2 py-1 text-xs font-medium text-bg disabled:opacity-50"
              >
                <Check className="h-3.5 w-3.5" />
                {t("organize.applyThisLabel")}
              </button>
            </>
          )}
        </div>
      </header>

      <div className="space-y-2 text-sm">
        {proposal.move_to && (
          <div className="flex items-center gap-2">
            <ArrowRight className="h-3.5 w-3.5 text-muted shrink-0" />
            <span className="text-muted">{t("organize.moveTo")}:</span>
            <code className="truncate font-mono text-xs text-text">
              {proposal.move_to}
            </code>
          </div>
        )}

        {proposal.tags && proposal.tags.length > 0 && (
          <div className="flex flex-wrap items-center gap-1.5">
            <Tag className="h-3.5 w-3.5 text-muted shrink-0" />
            <span className="text-muted">{t("organize.tags")}:</span>
            {proposal.tags.map((tag, i) => (
              <code
                key={i}
                className="rounded bg-bg px-1.5 py-0.5 font-mono text-xs"
              >
                {tag}
              </code>
            ))}
          </div>
        )}

        {proposal.wikilinks.length > 0 && (
          <div className="space-y-0.5">
            <div className="text-xs uppercase tracking-wide text-muted">
              {t("organize.wikilinks")}
            </div>
            <ul className="space-y-0.5">
              {proposal.wikilinks.map((w, i) => (
                <li key={i} className="text-xs">
                  <button
                    type="button"
                    onClick={() => onOpenWiki(w.target)}
                    className="font-mono text-accent hover:underline"
                  >
                    [[{w.target}]]
                  </button>
                  {w.context && <span className="text-muted"> — {w.context}</span>}
                </li>
              ))}
            </ul>
          </div>
        )}

        {proposal.refactor && (
          <div>
            <button
              type="button"
              onClick={() => setShowRefactor((v) => !v)}
              className="text-xs text-muted hover:text-text"
            >
              {showRefactor ? "▾" : "▸"} {t("organize.refactorPreview")}
            </button>
            {showRefactor && (
              <pre className="mt-1 max-h-80 overflow-auto whitespace-pre-wrap rounded bg-bg/60 p-2 font-mono text-xs">
                {proposal.refactor}
              </pre>
            )}
          </div>
        )}

        {proposal.notes && (
          <p className="text-xs italic text-muted">{proposal.notes}</p>
        )}

        {noChanges && !proposal.parse_error && (
          <p className="flex items-center gap-1 text-xs text-muted">
            <Circle className="h-3 w-3" /> {t("organize.noChanges")}
          </p>
        )}

        {proposal.parse_error && (
          <p className="text-xs text-red-300">
            {t("organize.parseError")}: {proposal.parse_error}
          </p>
        )}

        {proposal.state === "applied" && proposal.apply_ops.length > 0 && (
          <div className="flex flex-wrap items-center gap-1 text-xs text-green-300">
            <Check className="h-3 w-3" />
            {proposal.apply_ops.map((op, i) => (
              <code key={i} className="rounded bg-green-500/10 px-1.5 py-0.5">
                {op}
              </code>
            ))}
          </div>
        )}

        {proposal.state === "failed" && proposal.apply_error && (
          <p className="text-xs text-red-300">
            {t("organize.applyError")}: {proposal.apply_error}
          </p>
        )}

        {/* Revise form — collapsible */}
        {reviseOpen && isPending && (
          <form
            onSubmit={handleRevise}
            className="mt-2 space-y-2 rounded-lg border border-accent/40 bg-bg/40 p-2"
          >
            <label className="block text-[11px] uppercase tracking-wide text-muted">
              {t("organize.reviseInstructionLabel")}
            </label>
            <textarea
              value={instruction}
              onChange={(e) => setInstruction(e.target.value)}
              disabled={revising}
              rows={2}
              placeholder={t("organize.reviseInstructionPlaceholder")}
              className="w-full resize-none rounded border border-border bg-bg px-2 py-1.5 text-sm outline-none focus:border-accent"
            />
            {reviseError && (
              <p className="text-xs text-red-300">{reviseError}</p>
            )}
            <div className="flex items-center justify-end gap-2">
              <button
                type="button"
                onClick={() => {
                  setReviseOpen(false);
                  setInstruction("");
                  setReviseError(null);
                }}
                disabled={revising}
                className="text-xs text-muted hover:text-text"
              >
                {t("common.cancel")}
              </button>
              <button
                type="submit"
                disabled={revising || !instruction.trim()}
                className="flex items-center gap-1 rounded bg-accent px-2 py-1 text-xs font-medium text-bg disabled:opacity-50"
              >
                <Send className="h-3.5 w-3.5" />
                {revising ? t("organize.revising") : t("organize.reviseSend")}
              </button>
            </div>
          </form>
        )}
      </div>
    </article>
  );
}
