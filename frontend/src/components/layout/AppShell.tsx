// Top-level shell with a thin nav rail. The two views (Chat, Wiki) own their
// own internal layout — the rail just switches between them.

import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useQueryClient } from "@tanstack/react-query";
import {
  BookOpen,
  Brain,
  LogOut,
  MessageSquare,
  Moon,
  Newspaper,
  Sun,
} from "lucide-react";

import { logout } from "@/lib/auth";
import { setLanguage, currentLanguage } from "@/lib/i18n";
import { currentTheme, setTheme, type Theme } from "@/lib/theme";

export type ViewId = "chat" | "wiki" | "news";

interface Props {
  active: ViewId;
  onSelect: (v: ViewId) => void;
  children: React.ReactNode;
}

export default function AppShell({ active, onSelect, children }: Props) {
  const { t, i18n } = useTranslation();
  const qc = useQueryClient();
  const [theme, setThemeState] = useState<Theme>(() => currentTheme());

  function toggleTheme() {
    const next: Theme = theme === "light" ? "dark" : "light";
    setTheme(next);
    setThemeState(next);
  }

  async function onLogout() {
    await logout();
    qc.invalidateQueries({ queryKey: ["me"] });
  }

  const items: { id: ViewId; icon: typeof MessageSquare; label: string }[] = [
    { id: "chat", icon: MessageSquare, label: t("nav.chat") },
    { id: "wiki", icon: BookOpen, label: t("nav.wiki") },
    { id: "news", icon: Newspaper, label: t("nav.news") },
  ];

  return (
    <div className="flex h-full">
      <nav className="flex w-16 flex-col items-center gap-1 border-r border-border bg-surface py-3">
        <div className="mb-3 flex h-9 w-9 items-center justify-center rounded-lg bg-bg text-accent">
          <Brain className="h-5 w-5" />
        </div>

        {items.map((it) => {
          const Icon = it.icon;
          const isActive = active === it.id;
          return (
            <button
              key={it.id}
              onClick={() => onSelect(it.id)}
              title={it.label}
              aria-label={it.label}
              aria-current={isActive ? "page" : undefined}
              className={`flex h-10 w-10 items-center justify-center rounded-lg transition ${
                isActive ? "bg-bg text-accent" : "text-muted hover:bg-bg hover:text-text"
              }`}
            >
              <Icon className="h-5 w-5" />
            </button>
          );
        })}

        <div className="mt-auto flex flex-col items-center gap-2">
          <div className="flex flex-col gap-0.5 text-[10px] text-muted">
            <button
              className={currentLanguage() === "fr" ? "text-accent" : ""}
              onClick={() => {
                setLanguage("fr");
                i18n.changeLanguage("fr");
              }}
            >
              FR
            </button>
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
          <button
            onClick={toggleTheme}
            title={
              theme === "light"
                ? t("sidebar.themeToDark")
                : t("sidebar.themeToLight")
            }
            aria-label={
              theme === "light"
                ? t("sidebar.themeToDark")
                : t("sidebar.themeToLight")
            }
            className="flex h-10 w-10 items-center justify-center rounded-lg text-muted hover:bg-bg hover:text-text"
          >
            {theme === "light" ? (
              <Moon className="h-5 w-5" />
            ) : (
              <Sun className="h-5 w-5" />
            )}
          </button>
          <button
            onClick={onLogout}
            title={t("sidebar.logout")}
            aria-label={t("sidebar.logout")}
            className="flex h-10 w-10 items-center justify-center rounded-lg text-muted hover:bg-bg hover:text-text"
          >
            <LogOut className="h-5 w-5" />
          </button>
        </div>
      </nav>

      <main className="flex-1 min-w-0">{children}</main>
    </div>
  );
}
