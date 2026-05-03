// Top-level shell.
//
// Desktop (md+): a thin vertical nav rail on the left (review icon, the three
// view tabs, and language/theme/logout at the bottom). Untouched from the
// previous design.
//
// Mobile (< md): a top app-bar (brand + review badge + settings menu trigger)
// and a bottom tab-bar with the three views. Settings (theme, language,
// logout) live in a slide-over drawer triggered from the top bar — the
// bottom bar stays uncluttered for thumb reach.

import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useQueryClient } from "@tanstack/react-query";
import {
  BookOpen,
  BookOpenCheck,
  Brain,
  GraduationCap,
  LogOut,
  Menu,
  MessageSquare,
  Moon,
  Rss,
  Sun,
  X,
} from "lucide-react";

import { logout } from "@/lib/auth";
import { setLanguage, currentLanguage } from "@/lib/i18n";
import { currentTheme, setTheme, type Theme } from "@/lib/theme";
import Logo from "@/components/layout/Logo";

export type ViewId = "chat" | "wiki" | "news" | "training";

interface Props {
  active: ViewId;
  onSelect: (v: ViewId) => void;
  reviewNeeded?: boolean;
  onOpenReview?: () => void;
  children: React.ReactNode;
}

export default function AppShell({
  active,
  onSelect,
  reviewNeeded,
  onOpenReview,
  children,
}: Props) {
  const { t, i18n } = useTranslation();
  const qc = useQueryClient();
  const [theme, setThemeState] = useState<Theme>(() => currentTheme());
  const [menuOpen, setMenuOpen] = useState(false);

  function toggleTheme() {
    const next: Theme = theme === "light" ? "dark" : "light";
    setTheme(next);
    setThemeState(next);
  }

  async function onLogout() {
    setMenuOpen(false);
    await logout();
    qc.invalidateQueries({ queryKey: ["me"] });
  }

  const items: { id: ViewId; icon: typeof MessageSquare; label: string }[] = [
    { id: "chat", icon: MessageSquare, label: t("nav.chat") },
    { id: "wiki", icon: BookOpen, label: t("nav.wiki") },
    { id: "training", icon: GraduationCap, label: t("nav.training") },
    { id: "news", icon: Rss, label: t("nav.news") },
  ];

  return (
    <div className="flex h-full flex-col md:flex-row">
      {/* ─── Desktop rail (md+) ──────────────────────────────────────── */}
      <nav className="hidden w-16 flex-col items-center gap-1 border-r border-border bg-surface py-3 md:flex">
        <div className="mb-3 flex h-9 w-9 items-center justify-center rounded-lg bg-bg text-accent">
          <Brain className="h-5 w-5" />
        </div>

        {onOpenReview && (
          <button
            onClick={onOpenReview}
            title={
              reviewNeeded
                ? t("wiki.review.dueToday")
                : t("wiki.review.openReview")
            }
            aria-label={t("wiki.review.openReview")}
            className={`relative mb-2 flex h-10 w-10 items-center justify-center rounded-lg transition ${
              reviewNeeded
                ? "bg-red-500/15 text-red-400 hover:bg-red-500/25"
                : "text-muted hover:bg-bg hover:text-text"
            }`}
          >
            <BookOpenCheck className="h-5 w-5" />
            {reviewNeeded && (
              <span
                aria-hidden="true"
                className="absolute right-1 top-1 h-2 w-2 animate-pulse rounded-full bg-red-500 ring-2 ring-surface"
              />
            )}
          </button>
        )}

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

      {/* ─── Mobile top bar (< md) ───────────────────────────────────── */}
      <header
        className="flex shrink-0 items-center gap-2 border-b border-border bg-surface px-3 py-2 md:hidden"
        style={{ paddingTop: "max(0.5rem, env(safe-area-inset-top))" }}
      >
        <Logo className="h-8 w-auto" />
        <span className="sr-only">{t("app.title")}</span>
        <div className="flex-1" />
        {onOpenReview && (
          <button
            type="button"
            onClick={onOpenReview}
            aria-label={t("wiki.review.openReview")}
            className={`relative flex h-9 w-9 items-center justify-center rounded-lg transition ${
              reviewNeeded
                ? "bg-red-500/15 text-red-400"
                : "text-muted hover:bg-bg"
            }`}
          >
            <BookOpenCheck className="h-5 w-5" />
            {reviewNeeded && (
              <span
                aria-hidden="true"
                className="absolute right-1 top-1 h-2 w-2 animate-pulse rounded-full bg-red-500 ring-2 ring-surface"
              />
            )}
          </button>
        )}
        <button
          type="button"
          onClick={() => setMenuOpen(true)}
          aria-label={t("sidebar.menu")}
          className="flex h-9 w-9 items-center justify-center rounded-lg text-muted hover:bg-bg hover:text-text"
        >
          <Menu className="h-5 w-5" />
        </button>
      </header>

      {/* ─── Main content ────────────────────────────────────────────── */}
      <main className="min-h-0 min-w-0 flex-1 overflow-hidden">{children}</main>

      {/* ─── Mobile bottom tab-bar (< md) ────────────────────────────── */}
      <nav
        className="flex shrink-0 items-stretch border-t border-border bg-surface md:hidden"
        style={{ paddingBottom: "env(safe-area-inset-bottom)" }}
      >
        {items.map((it) => {
          const Icon = it.icon;
          const isActive = active === it.id;
          return (
            <button
              key={it.id}
              type="button"
              onClick={() => onSelect(it.id)}
              aria-label={it.label}
              aria-current={isActive ? "page" : undefined}
              className={`flex flex-1 flex-col items-center justify-center gap-0.5 py-2 text-[11px] transition ${
                isActive ? "text-accent" : "text-muted hover:text-text"
              }`}
            >
              <Icon className={`h-5 w-5 ${isActive ? "" : ""}`} />
              <span className="truncate">{it.label}</span>
            </button>
          );
        })}
      </nav>

      {/* ─── Mobile settings drawer ──────────────────────────────────── */}
      {menuOpen && (
        <div
          className="fixed inset-0 z-50 bg-black/60 md:hidden"
          onClick={() => setMenuOpen(false)}
        >
          <aside
            className="ml-auto flex h-full w-72 flex-col border-l border-border bg-surface"
            onClick={(e) => e.stopPropagation()}
            style={{ paddingTop: "env(safe-area-inset-top)" }}
          >
            <div className="flex items-center justify-between border-b border-border px-4 py-3">
              <span className="text-sm font-semibold">
                {t("sidebar.settings")}
              </span>
              <button
                type="button"
                onClick={() => setMenuOpen(false)}
                aria-label={t("common.cancel")}
                className="text-muted hover:text-text"
              >
                <X className="h-5 w-5" />
              </button>
            </div>

            <div className="flex flex-col gap-1 p-3">
              <button
                type="button"
                onClick={() => {
                  toggleTheme();
                }}
                className="flex items-center gap-3 rounded-lg px-3 py-2 text-left text-sm hover:bg-bg"
              >
                {theme === "light" ? (
                  <Moon className="h-4 w-4 text-muted" />
                ) : (
                  <Sun className="h-4 w-4 text-muted" />
                )}
                <span>
                  {theme === "light"
                    ? t("sidebar.themeToDark")
                    : t("sidebar.themeToLight")}
                </span>
              </button>

              <div className="px-3 pt-3 pb-1 text-[11px] uppercase tracking-wide text-muted">
                {t("common.language")}
              </div>
              <div className="flex gap-2 px-3">
                <button
                  type="button"
                  onClick={() => {
                    setLanguage("fr");
                    i18n.changeLanguage("fr");
                  }}
                  className={`flex-1 rounded-lg border px-3 py-2 text-sm ${
                    currentLanguage() === "fr"
                      ? "border-accent bg-accent/10 text-accent"
                      : "border-border text-muted"
                  }`}
                >
                  FR
                </button>
                <button
                  type="button"
                  onClick={() => {
                    setLanguage("en");
                    i18n.changeLanguage("en");
                  }}
                  className={`flex-1 rounded-lg border px-3 py-2 text-sm ${
                    currentLanguage() === "en"
                      ? "border-accent bg-accent/10 text-accent"
                      : "border-border text-muted"
                  }`}
                >
                  EN
                </button>
              </div>

              <div className="mt-3 border-t border-border pt-3">
                <button
                  type="button"
                  onClick={onLogout}
                  className="flex w-full items-center gap-3 rounded-lg px-3 py-2 text-left text-sm text-muted hover:bg-bg hover:text-text"
                >
                  <LogOut className="h-4 w-4" />
                  <span>{t("sidebar.logout")}</span>
                </button>
              </div>
            </div>
          </aside>
        </div>
      )}
    </div>
  );
}
