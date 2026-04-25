// Sidebar listing all chats with "new" and "delete" actions.

import { useTranslation } from "react-i18next";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { MessageSquarePlus, Trash2 } from "lucide-react";

import { api, type ChatSummary } from "@/lib/api";

interface Props {
  activeChatId: string | null;
  onSelect: (id: string) => void;
  onNew: () => void;
}

export default function ChatHistory({ activeChatId, onSelect, onNew }: Props) {
  const { t } = useTranslation();
  const qc = useQueryClient();

  const chats = useQuery({
    queryKey: ["chats"],
    queryFn: () => api.get<ChatSummary[]>("/api/chats"),
    refetchInterval: 5_000,
  });

  async function onDelete(e: React.MouseEvent, id: string) {
    e.stopPropagation();
    if (!confirm(t("chat.deleteConfirm"))) return;
    await api.delete(`/api/chats/${id}?hard=true`);
    if (activeChatId === id) onNew();
    qc.invalidateQueries({ queryKey: ["chats"] });
  }

  return (
    <div className="flex h-full flex-col">
      <div className="border-b border-border px-3 py-2 text-xs uppercase tracking-wide text-muted">
        {t("sidebar.chats")}
      </div>

      <button
        onClick={onNew}
        className="mx-3 my-2 flex items-center justify-center gap-2 rounded-lg border border-border bg-bg px-3 py-2 text-sm font-medium hover:border-accent"
      >
        <MessageSquarePlus className="h-4 w-4" />
        {t("sidebar.newChat")}
      </button>

      <div className="flex-1 overflow-y-auto px-2 pb-3">
        {chats.data?.map((c) => (
          <div
            key={c.id}
            onClick={() => onSelect(c.id)}
            className={`group flex items-center justify-between gap-2 rounded px-2 py-1.5 text-sm cursor-pointer ${
              activeChatId === c.id ? "bg-bg text-accent" : "hover:bg-bg"
            }`}
          >
            <span className="truncate">{c.title}</span>
            <button
              onClick={(e) => onDelete(e, c.id)}
              className="opacity-0 group-hover:opacity-100 text-muted hover:text-red-400"
              aria-label={t("common.delete")}
            >
              <Trash2 className="h-4 w-4" />
            </button>
          </div>
        ))}
        {chats.data?.length === 0 && (
          <p className="px-2 py-2 text-xs text-muted">{t("chat.noHistory")}</p>
        )}
      </div>
    </div>
  );
}
