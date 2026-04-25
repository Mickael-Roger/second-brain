import { useTranslation } from "react-i18next";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { LogOut, MessageSquarePlus, Trash2, X, Brain } from "lucide-react";

import { api, type ChatSummary, type ModuleInfo } from "@/lib/api";
import { logout } from "@/lib/auth";
import { setLanguage, currentLanguage } from "@/lib/i18n";

interface Props {
  activeChatId: string | null;
  onSelectChat: (id: string) => void;
  onNewChat: () => void;
  open: boolean;
  onClose: () => void;
}

export default function Sidebar({
  activeChatId,
  onSelectChat,
  onNewChat,
  open,
  onClose,
}: Props) {
  const { t, i18n } = useTranslation();
  const qc = useQueryClient();

  const chats = useQuery({
    queryKey: ["chats"],
    queryFn: () => api.get<ChatSummary[]>("/api/chats"),
    refetchInterval: 5_000,
  });

  const modules = useQuery({
    queryKey: ["modules"],
    queryFn: () => api.get<ModuleInfo[]>("/api/modules"),
  });

  async function onDelete(e: React.MouseEvent, chatId: string) {
    e.stopPropagation();
    if (!confirm(t("chat.deleteConfirm"))) return;
    await api.delete(`/api/chats/${chatId}?hard=true`);
    if (activeChatId === chatId) onNewChat();
    qc.invalidateQueries({ queryKey: ["chats"] });
  }

  async function onLogout() {
    await logout();
    qc.invalidateQueries({ queryKey: ["me"] });
  }

  return (
    <>
      {/* Backdrop on mobile */}
      <div
        className={`fixed inset-0 z-30 bg-black/60 md:hidden ${open ? "block" : "hidden"}`}
        onClick={onClose}
      />
      <aside
        className={`fixed inset-y-0 left-0 z-40 flex w-72 flex-col border-r border-border bg-surface md:relative md:translate-x-0 ${open ? "translate-x-0" : "-translate-x-full"} transition-transform`}
      >
        {/* Header */}
        <div className="flex items-center justify-between p-3">
          <div className="flex items-center gap-2">
            <Brain className="h-5 w-5 text-accent" />
            <span className="font-medium">{t("app.title")}</span>
          </div>
          <button className="md:hidden text-muted" onClick={onClose} aria-label="Close">
            <X className="h-5 w-5" />
          </button>
        </div>

        <button
          onClick={onNewChat}
          className="mx-3 mb-2 flex items-center justify-center gap-2 rounded-lg border border-border bg-bg px-3 py-2 text-sm font-medium hover:border-accent"
        >
          <MessageSquarePlus className="h-4 w-4" />
          {t("sidebar.newChat")}
        </button>

        {/* Modules */}
        <div className="px-3 pb-1 pt-2 text-xs uppercase tracking-wide text-muted">
          {t("sidebar.modules")}
        </div>
        <div className="px-3 pb-3 text-sm text-muted">
          {modules.data?.map((m) => (
            <div
              key={m.id}
              className="rounded px-2 py-1 hover:bg-bg"
            >
              {m.name[currentLanguage()] ?? m.name.en}
            </div>
          ))}
        </div>

        {/* Chats */}
        <div className="px-3 pb-1 text-xs uppercase tracking-wide text-muted">
          {t("sidebar.chats")}
        </div>
        <div className="flex-1 overflow-y-auto px-2 pb-3">
          {chats.data?.map((c) => (
            <div
              key={c.id}
              onClick={() => onSelectChat(c.id)}
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
            <p className="px-2 py-1 text-xs text-muted">No chats yet.</p>
          )}
        </div>

        {/* Footer */}
        <div className="border-t border-border p-3 text-sm">
          <div className="mb-2 flex items-center justify-between text-xs text-muted">
            <span>{t("common.language")}</span>
            <div className="flex gap-2">
              <button
                className={currentLanguage() === "fr" ? "text-accent" : ""}
                onClick={() => {
                  setLanguage("fr");
                  i18n.changeLanguage("fr");
                }}
              >
                FR
              </button>
              <span>·</span>
              <button
                className={currentLanguage() === "en" ? "text-accent" : ""}
                onClick={() => {
                  setLanguage("en");
                  i18n.changeLanguage("en");
                }}
              >
                EN
              </button>
            </div>
          </div>
          <button
            onClick={onLogout}
            className="flex w-full items-center gap-2 rounded px-2 py-1.5 text-muted hover:bg-bg hover:text-text"
          >
            <LogOut className="h-4 w-4" />
            {t("sidebar.logout")}
          </button>
        </div>
      </aside>
    </>
  );
}
