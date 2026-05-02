// News tab. Three-pane layout reproducing FreshRSS's sidebar:
//   - left: feeds grouped under their FreshRSS category (folder),
//           collapsible, with unread/total counts
//   - middle: article titles for the active feed/category, with an
//             "unread only" toggle (which also collapses the sidebar
//             to feeds that still have unread items)
//   - right: selected article — image, summary, mark-as-read, and a
//            "chat about this" button that hands the article context
//            off to the Chat tab.

import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowLeft,
  BookmarkPlus,
  BookText,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  Eye,
  ExternalLink,
  Mail,
  MailOpen,
  Menu,
  MessageSquare,
  Newspaper,
  Pencil,
  Play,
  Rss,
  X,
} from "lucide-react";

import {
  api,
  ApiError,
  type NewsArticleDetail,
  type NewsArticleSummary,
  type NewsFeedSummary,
} from "@/lib/api";

type Selection =
  | { kind: "all" }
  | { kind: "category"; group: string }
  | { kind: "feed"; feedId: string };

interface Props {
  onOpenChat: () => void;
}

export default function NewsView({ onOpenChat }: Props) {
  const { t } = useTranslation();
  const qc = useQueryClient();

  // Default to "unread only" — when the user lands on the News tab
  // they almost always want what they haven't seen yet, not the full
  // 30-day archive. Untoggle to see read articles.
  const [unreadOnly, setUnreadOnly] = useState(true);
  const [selection, setSelection] = useState<Selection>({ kind: "all" });
  const [selectedId, setSelectedId] = useState<string | null>(null);
  // Mobile-only: feeds live in a slide-over drawer (left). Desktop has them
  // permanently visible in the first grid column.
  const [feedsOpen, setFeedsOpen] = useState(false);

  // No more period selector — list endpoints default to 30d (the
  // article retention window), which means the UI shows everything
  // currently in the DB. Manual fetch goes incremental (same as the
  // every-5-min cron) for speed.
  const feeds = useQuery<NewsFeedSummary[]>({
    queryKey: ["news-feeds"],
    queryFn: () => api.get<NewsFeedSummary[]>("/api/news/feeds"),
  });

  const articles = useQuery<NewsArticleSummary[]>({
    queryKey: ["news-articles", selection, unreadOnly],
    queryFn: () => {
      const qs = new URLSearchParams();
      if (selection.kind === "feed") qs.set("feed_id", selection.feedId);
      if (selection.kind === "category") qs.set("feed_group", selection.group);
      if (unreadOnly) qs.set("unread_only", "true");
      const suffix = qs.toString() ? `?${qs.toString()}` : "";
      return api.get<NewsArticleSummary[]>(`/api/news/articles${suffix}`);
    },
  });

  const selected = useQuery<NewsArticleDetail>({
    queryKey: ["news-article", selectedId],
    queryFn: () =>
      api.get<NewsArticleDetail>(
        `/api/news/articles/${encodeURIComponent(selectedId!)}`,
      ),
    enabled: !!selectedId,
  });

  const fetchNow = useMutation({
    mutationFn: () => api.post<{ started: boolean }>("/api/news/fetch"),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["news-feeds"] });
      qc.invalidateQueries({ queryKey: ["news-articles"] });
    },
  });

  const toggleRead = useMutation({
    mutationFn: ({
      articleId,
      isRead,
    }: {
      articleId: string;
      isRead: boolean;
    }) =>
      api.post<{ article_id: string; is_read: boolean }>(
        `/api/news/articles/${encodeURIComponent(articleId)}/${
          isRead ? "read" : "unread"
        }`,
      ),
    onSuccess: (_data, { articleId }) => {
      // Optimistically refresh feed counts + the active article list +
      // the open detail view.
      qc.invalidateQueries({ queryKey: ["news-feeds"] });
      qc.invalidateQueries({ queryKey: ["news-articles"] });
      qc.invalidateQueries({ queryKey: ["news-article", articleId] });
    },
  });

  function startChatAbout(a: NewsArticleDetail) {
    // Don't paste the article body / summary into the prompt — keeps the
    // user's first turn short and lets them ask their actual question.
    // The LLM has `news.read_news(article_id=…)` registered as a tool;
    // it can fetch the body on demand. We just pin the article so the
    // LLM knows which one we mean.
    const draft =
      `I'd like to discuss this article: "${a.title}" ` +
      `(news article id: \`${a.id}\`). Use \`news.read_news\` with that ` +
      `article_id to read it when you need the content; ` +
      `\`news.list_news\` / \`news.mark_read\` are also available.`;
    window.localStorage.setItem("sb.chat.draft", draft);
    onOpenChat();
  }

  // On mobile, only one pane is on-screen at a time.
  //   - selectedId === null  → article list (with a hamburger that opens feeds)
  //   - selectedId !== null  → article detail (with a back arrow)
  // On desktop (md+), all three panes are visible side-by-side and these
  // mobile-only controls are hidden.
  const showDetailMobile = selectedId !== null;

  // Prev/next ids relative to the currently displayed article list — same
  // ordering as the middle column. Boundaries return null so the buttons
  // disable cleanly.
  const articleList = articles.data ?? [];
  const currentIdx = selectedId
    ? articleList.findIndex((a) => a.id === selectedId)
    : -1;
  const prevId =
    currentIdx > 0 ? articleList[currentIdx - 1].id : null;
  const nextId =
    currentIdx >= 0 && currentIdx < articleList.length - 1
      ? articleList[currentIdx + 1].id
      : null;

  return (
    <div className="flex h-full flex-col">
      <header className="flex flex-wrap items-center gap-3 border-b border-border bg-surface px-4 py-3">
        <Newspaper className="h-5 w-5 text-accent" />
        <h1 className="flex-1 text-lg font-semibold">{t("news.title")}</h1>

        <button
          type="button"
          onClick={() => fetchNow.mutate()}
          disabled={fetchNow.isPending}
          className="flex items-center gap-1 rounded-lg border border-border bg-bg px-3 py-1.5 text-sm hover:border-accent disabled:opacity-50"
        >
          <Play className="h-3.5 w-3.5" />
          {fetchNow.isPending ? t("news.fetching") : t("news.fetch")}
        </button>
      </header>

      {fetchNow.isSuccess && (
        <div className="border-b border-border bg-accent/10 px-4 py-2 text-xs text-accent">
          {t("news.fetchTriggered")}
        </div>
      )}

      <div className="flex flex-1 min-h-0 flex-col overflow-hidden md:grid md:grid-cols-[16rem_22rem_1fr] md:divide-x md:divide-border">
        {/* Feeds: permanent column on desktop, drawer on mobile. */}
        <div className="hidden md:block">
          <FeedSidebar
            feeds={feeds.data ?? []}
            loading={feeds.isLoading}
            unreadOnly={unreadOnly}
            selection={selection}
            onSelect={(s) => {
              setSelection(s);
              setSelectedId(null);
            }}
          />
        </div>

        {/* Article list: full-screen on mobile (when no article is open),
            permanent middle column on desktop. */}
        <div className={`${showDetailMobile ? "hidden" : "flex"} h-full min-h-0 flex-col md:flex`}>
          <div className="flex items-center gap-2 border-b border-border bg-surface px-3 py-2 md:hidden">
            <button
              type="button"
              onClick={() => setFeedsOpen(true)}
              aria-label={t("news.feedsHeader")}
              className="flex h-8 w-8 items-center justify-center rounded text-muted hover:bg-bg hover:text-text"
            >
              <Menu className="h-4 w-4" />
            </button>
            <span className="flex-1 truncate text-sm">
              {selection.kind === "all"
                ? t("news.allFeeds")
                : selection.kind === "category"
                  ? selection.group || t("news.uncategorized")
                  : feeds.data?.find((f) => f.feed_id === selection.feedId)
                      ?.feed_title ?? ""}
            </span>
          </div>
          <ArticleList
            articles={articles.data ?? []}
            loading={articles.isLoading}
            selectedId={selectedId}
            unreadOnly={unreadOnly}
            onUnreadToggle={setUnreadOnly}
            onSelect={setSelectedId}
          />
        </div>

        {/* Detail: full-screen on mobile (when an article is open), permanent
            right column on desktop. */}
        <div className={`${showDetailMobile ? "flex" : "hidden"} h-full min-h-0 flex-col md:flex`}>
          <div className="flex items-center gap-2 border-b border-border bg-surface px-3 py-2 md:hidden">
            <button
              type="button"
              onClick={() => setSelectedId(null)}
              aria-label="back"
              className="flex h-8 w-8 items-center justify-center rounded text-muted hover:bg-bg hover:text-text"
            >
              <ArrowLeft className="h-4 w-4" />
            </button>
            <span className="flex-1 truncate text-sm">
              {selected.data?.title ?? ""}
            </span>
          </div>
          <DetailPane
            articleId={selectedId}
            article={selected.data}
            loading={selected.isLoading}
            toggleRead={(id, target) =>
              toggleRead.mutate({ articleId: id, isRead: target })
            }
            togglePending={toggleRead.isPending}
            onChat={startChatAbout}
            onPrev={prevId ? () => setSelectedId(prevId) : undefined}
            onNext={nextId ? () => setSelectedId(nextId) : undefined}
          />
        </div>
      </div>

      {/* Mobile feeds drawer (left slide-over). */}
      {feedsOpen && (
        <div
          className="fixed inset-0 z-40 bg-black/60 md:hidden"
          onClick={() => setFeedsOpen(false)}
        >
          <aside
            className="flex h-full w-72 flex-col border-r border-border bg-surface"
            onClick={(e) => e.stopPropagation()}
            style={{ paddingTop: "env(safe-area-inset-top)" }}
          >
            <div className="flex items-center justify-between border-b border-border px-3 py-2">
              <span className="text-sm font-medium">{t("news.feedsHeader")}</span>
              <button
                type="button"
                onClick={() => setFeedsOpen(false)}
                className="text-muted"
                aria-label={t("common.cancel")}
              >
                <X className="h-5 w-5" />
              </button>
            </div>
            <FeedSidebar
              feeds={feeds.data ?? []}
              loading={feeds.isLoading}
              unreadOnly={unreadOnly}
              selection={selection}
              onSelect={(s) => {
                setSelection(s);
                setSelectedId(null);
                setFeedsOpen(false);
              }}
            />
          </aside>
        </div>
      )}
    </div>
  );
}

// ─── Sidebar ────────────────────────────────────────────────────────

interface FeedSidebarProps {
  feeds: NewsFeedSummary[];
  loading: boolean;
  unreadOnly: boolean;
  selection: Selection;
  onSelect: (s: Selection) => void;
}

function FeedSidebar({
  feeds,
  loading,
  unreadOnly,
  selection,
  onSelect,
}: FeedSidebarProps) {
  const { t } = useTranslation();
  const visible = useMemo(
    () => (unreadOnly ? feeds.filter((f) => f.unread > 0) : feeds),
    [feeds, unreadOnly],
  );

  // Group feeds by their category (FreshRSS folder).
  const grouped = useMemo(() => {
    const m = new Map<string, NewsFeedSummary[]>();
    for (const f of visible) {
      const key = f.feed_group ?? "__uncategorized";
      const list = m.get(key);
      if (list) list.push(f);
      else m.set(key, [f]);
    }
    // Stable sort: alphabetical category, then alphabetical feed.
    return Array.from(m.entries()).sort(([a], [b]) =>
      a.localeCompare(b, undefined, { sensitivity: "base" }),
    );
  }, [visible]);

  // All categories start expanded; toggle local state.
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({});

  const totalUnread = visible.reduce((s, f) => s + f.unread, 0);
  const totalAll = visible.reduce((s, f) => s + f.total, 0);

  return (
    <aside className="overflow-y-auto bg-surface/50 p-2">
      <h3 className="mb-1 px-2 text-xs font-medium uppercase tracking-wide text-muted">
        {t("news.feedsHeader")}
      </h3>

      <button
        type="button"
        onClick={() => onSelect({ kind: "all" })}
        className={`mb-2 flex w-full items-center justify-between rounded px-2 py-1.5 text-left text-sm transition ${
          selection.kind === "all"
            ? "bg-accent/15 text-accent"
            : "text-text/85 hover:bg-bg"
        }`}
      >
        <span className="truncate font-medium">{t("news.allFeeds")}</span>
        <span className="ml-2 shrink-0 text-xs text-muted">
          <b className="text-text">{totalUnread}</b>/{totalAll}
        </span>
      </button>

      {loading ? (
        <p className="px-2 text-xs text-muted">{t("common.loading")}</p>
      ) : (
        grouped.map(([groupKey, feedsInGroup]) => {
          const groupName =
            groupKey === "__uncategorized"
              ? t("news.uncategorized")
              : groupKey;
          const groupTotal = feedsInGroup.reduce((s, f) => s + f.total, 0);
          const groupUnread = feedsInGroup.reduce((s, f) => s + f.unread, 0);
          const isCollapsed = !!collapsed[groupKey];
          const isActiveCategory =
            selection.kind === "category" &&
            selection.group === (groupKey === "__uncategorized" ? "" : groupKey);
          return (
            <div key={groupKey} className="mb-1">
              <div className="flex items-center gap-0.5">
                <button
                  type="button"
                  onClick={() =>
                    setCollapsed((c) => ({ ...c, [groupKey]: !c[groupKey] }))
                  }
                  className="rounded p-0.5 text-muted hover:text-text"
                  aria-label="toggle category"
                >
                  {isCollapsed ? (
                    <ChevronRight className="h-3 w-3" />
                  ) : (
                    <ChevronDown className="h-3 w-3" />
                  )}
                </button>
                <button
                  type="button"
                  onClick={() =>
                    onSelect({
                      kind: "category",
                      group: groupKey === "__uncategorized" ? "" : groupKey,
                    })
                  }
                  className={`flex flex-1 items-center justify-between rounded px-1.5 py-1 text-left text-xs uppercase tracking-wide transition ${
                    isActiveCategory
                      ? "bg-accent/15 text-accent"
                      : "text-muted hover:bg-bg hover:text-text"
                  }`}
                  title={groupName}
                >
                  <span className="truncate">{groupName}</span>
                  <span className="ml-2 shrink-0 normal-case">
                    {groupUnread > 0 && (
                      <b className="text-text">{groupUnread}</b>
                    )}
                    {groupUnread > 0 && "/"}
                    {groupTotal}
                  </span>
                </button>
              </div>
              {!isCollapsed && (
                <ul className="ml-4 space-y-0.5 border-l border-border pl-1.5">
                  {feedsInGroup.map((f) => {
                    const active =
                      selection.kind === "feed" && selection.feedId === f.feed_id;
                    return (
                      <li key={f.feed_id}>
                        <button
                          type="button"
                          onClick={() =>
                            onSelect({ kind: "feed", feedId: f.feed_id })
                          }
                          className={`flex w-full items-center gap-1.5 rounded px-2 py-1 text-left text-xs transition ${
                            active
                              ? "bg-accent/15 text-accent"
                              : "text-text/85 hover:bg-bg"
                          }`}
                          title={f.feed_title}
                        >
                          <FeedIcon
                            favicon={f.favicon}
                            alt={f.feed_title}
                            isRead
                            size={12}
                          />
                          <span className="flex-1 truncate">{f.feed_title}</span>
                          <span className="shrink-0 text-[10px] text-muted">
                            {f.unread > 0 ? (
                              <b className="text-text">{f.unread}</b>
                            ) : (
                              f.unread
                            )}
                            /{f.total}
                          </span>
                        </button>
                      </li>
                    );
                  })}
                </ul>
              )}
            </div>
          );
        })
      )}
    </aside>
  );
}

// ─── Article list ───────────────────────────────────────────────────

interface ArticleListProps {
  articles: NewsArticleSummary[];
  loading: boolean;
  selectedId: string | null;
  unreadOnly: boolean;
  onUnreadToggle: (v: boolean) => void;
  onSelect: (id: string) => void;
}

function ArticleList({
  articles,
  loading,
  selectedId,
  unreadOnly,
  onUnreadToggle,
  onSelect,
}: ArticleListProps) {
  const { t } = useTranslation();
  return (
    <section className="flex h-full flex-col overflow-hidden">
      <div className="flex items-center justify-between border-b border-border bg-surface px-3 py-2">
        <label className="flex items-center gap-2 text-xs">
          <input
            type="checkbox"
            checked={unreadOnly}
            onChange={(e) => onUnreadToggle(e.target.checked)}
          />
          {t("news.unreadOnly")}
        </label>
        <span className="text-[10px] text-muted">
          {articles.length}
        </span>
      </div>
      <div className="flex-1 overflow-y-auto">
        {loading ? (
          <p className="px-3 py-3 text-sm text-muted">{t("common.loading")}</p>
        ) : articles.length === 0 ? (
          <p className="px-3 py-3 text-sm text-muted">{t("news.noArticles")}</p>
        ) : (
          articles.map((a) => (
            <button
              key={a.id}
              type="button"
              onClick={() => onSelect(a.id)}
              className={`flex w-full flex-col gap-0.5 border-b border-border px-3 py-2 text-left transition ${
                selectedId === a.id
                  ? "bg-accent/10"
                  : "bg-bg hover:bg-surface"
              }`}
            >
              <div className="flex items-start gap-2">
                <FeedIcon
                  favicon={a.feed_favicon}
                  isRead={a.is_read}
                  alt={a.feed_title ?? a.source}
                />
                <span
                  className={`line-clamp-2 flex-1 text-sm ${
                    a.is_read ? "text-muted" : "text-text"
                  }`}
                >
                  {a.title}
                </span>
              </div>
              <span className="ml-6 text-[10px] text-muted">
                {(a.feed_title ?? a.source) + " · " + a.published_at.slice(0, 10)}
              </span>
            </button>
          ))
        )}
      </div>
    </section>
  );
}

// ─── Detail pane ────────────────────────────────────────────────────

interface DetailPaneProps {
  articleId: string | null;
  article: NewsArticleDetail | undefined;
  loading: boolean;
  toggleRead: (id: string, target: boolean) => void;
  togglePending: boolean;
  onChat: (a: NewsArticleDetail) => void;
  // Undefined when at the corresponding boundary of the list, so the
  // arrow button can render disabled.
  onPrev?: () => void;
  onNext?: () => void;
}

type ViewMode = "summary" | "html";

type CaptureKind = "keep" | "article" | "watched" | "custom";

function DetailPane({
  articleId,
  article,
  loading,
  toggleRead,
  togglePending,
  onChat,
  onPrev,
  onNext,
}: DetailPaneProps) {
  const { t } = useTranslation();
  const [viewMode, setViewMode] = useState<ViewMode>("summary");
  const [captureBusy, setCaptureBusy] = useState<CaptureKind | null>(null);
  const [captureMessage, setCaptureMessage] = useState<
    | { kind: "ok"; path?: string; summary?: string; files?: string[] }
    | { kind: "err"; text: string }
    | null
  >(null);
  const [customOpen, setCustomOpen] = useState(false);
  const [customText, setCustomText] = useState("");

  // Switching articles resets the local content-tab + capture state.
  const currentId = article?.id ?? null;
  useEffect(() => {
    setViewMode("summary");
    setCaptureMessage(null);
    setCustomOpen(false);
    setCustomText("");
  }, [currentId]);

  async function runCapture(kind: CaptureKind, id: string) {
    if (captureBusy) return;
    setCaptureBusy(kind);
    setCaptureMessage(null);
    try {
      const res = await api.post<{ path: string }>(
        `/api/news/articles/${encodeURIComponent(id)}/${kind}`,
      );
      setCaptureMessage({ kind: "ok", path: res.path });
    } catch (err: unknown) {
      const text =
        err instanceof ApiError
          ? typeof err.detail === "object" && err.detail && "detail" in err.detail
            ? String((err.detail as { detail: unknown }).detail)
            : String(err.detail ?? err.message)
          : String((err as Error)?.message ?? err);
      setCaptureMessage({ kind: "err", text });
    } finally {
      setCaptureBusy(null);
    }
  }

  async function runCustomCapture(id: string, instruction: string) {
    const trimmed = instruction.trim();
    if (!trimmed || captureBusy) return;
    setCaptureBusy("custom");
    setCaptureMessage(null);
    try {
      const res = await api.post<{
        summary: string;
        files_touched: string[];
      }>(`/api/news/articles/${encodeURIComponent(id)}/custom`, {
        instruction: trimmed,
      });
      setCaptureMessage({
        kind: "ok",
        summary: res.summary,
        files: res.files_touched,
      });
      setCustomOpen(false);
      setCustomText("");
    } catch (err: unknown) {
      const text =
        err instanceof ApiError
          ? typeof err.detail === "object" && err.detail && "detail" in err.detail
            ? String((err.detail as { detail: unknown }).detail)
            : String(err.detail ?? err.message)
          : String((err as Error)?.message ?? err);
      setCaptureMessage({ kind: "err", text });
    } finally {
      setCaptureBusy(null);
    }
  }

  if (!articleId) {
    return (
      <section className="flex h-full items-center justify-center bg-bg p-4 text-center">
        <div className="space-y-2">
          <Newspaper className="mx-auto h-7 w-7 text-muted" />
          <p className="text-sm text-muted">{t("news.selectArticle")}</p>
        </div>
      </section>
    );
  }
  if (loading || !article) {
    return (
      <section className="overflow-y-auto bg-bg p-4">
        <p className="text-sm text-muted">{t("common.loading")}</p>
      </section>
    );
  }

  const subtitle = [
    article.feed_group,
    article.feed_title ?? article.source,
    article.published_at.slice(0, 16).replace("T", " "),
  ]
    .filter(Boolean)
    .join(" · ");

  return (
    <section className="overflow-y-auto bg-bg p-4">
      <article className="mx-auto max-w-2xl space-y-4">
        {article.image_url && (
          // Feed-supplied URL; rendering as <img> trusts the source.
          <img
            src={article.image_url}
            alt=""
            className="max-h-72 w-full rounded-lg border border-border object-cover"
            loading="lazy"
            referrerPolicy="no-referrer"
          />
        )}

        <header className="space-y-1">
          {(onPrev || onNext) && (
            <div className="flex items-center justify-between pb-1">
              <button
                type="button"
                onClick={onPrev}
                disabled={!onPrev}
                aria-label={t("news.previousArticle")}
                title={t("news.previousArticle")}
                className="flex items-center gap-1 rounded-lg border border-border bg-bg px-2 py-1 text-xs text-muted hover:border-accent hover:text-text disabled:cursor-not-allowed disabled:opacity-30 disabled:hover:border-border disabled:hover:text-muted"
              >
                <ChevronLeft className="h-4 w-4" />
                <span className="hidden sm:inline">
                  {t("news.previousArticle")}
                </span>
              </button>
              <button
                type="button"
                onClick={onNext}
                disabled={!onNext}
                aria-label={t("news.nextArticle")}
                title={t("news.nextArticle")}
                className="flex items-center gap-1 rounded-lg border border-border bg-bg px-2 py-1 text-xs text-muted hover:border-accent hover:text-text disabled:cursor-not-allowed disabled:opacity-30 disabled:hover:border-border disabled:hover:text-muted"
              >
                <span className="hidden sm:inline">
                  {t("news.nextArticle")}
                </span>
                <ChevronRight className="h-4 w-4" />
              </button>
            </div>
          )}
          <h2 className="text-lg font-semibold text-text">{article.title}</h2>
          <p className="text-xs text-muted">{subtitle}</p>
          <div className="flex flex-wrap items-center gap-2 pt-1">
            {article.url && (
              <a
                href={article.url}
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex items-center gap-1 rounded-lg border border-border bg-bg px-2 py-1 text-xs hover:border-accent"
              >
                <ExternalLink className="h-3 w-3" />
                {t("news.openOriginal")}
              </a>
            )}
            <button
              type="button"
              onClick={() => toggleRead(article.id, !article.is_read)}
              disabled={togglePending}
              className="inline-flex items-center gap-1 rounded-lg border border-border bg-bg px-2 py-1 text-xs hover:border-accent disabled:opacity-50"
            >
              {article.is_read ? (
                <Mail className="h-3 w-3" />
              ) : (
                <MailOpen className="h-3 w-3" />
              )}
              {togglePending
                ? t("news.marking")
                : article.is_read
                  ? t("news.markUnread")
                  : t("news.markRead")}
            </button>
            <button
              type="button"
              onClick={() => onChat(article)}
              className="inline-flex items-center gap-1 rounded-lg border border-accent bg-accent/10 px-2 py-1 text-xs text-accent hover:bg-accent/20"
            >
              <MessageSquare className="h-3 w-3" />
              {t("news.chatAbout")}
            </button>
            <button
              type="button"
              onClick={() => runCapture("keep", article.id)}
              disabled={captureBusy !== null}
              title={t("news.captureKeepHint")}
              className="inline-flex items-center gap-1 rounded-lg border border-border bg-bg px-2 py-1 text-xs hover:border-accent disabled:opacity-50"
            >
              <BookmarkPlus className="h-3 w-3" />
              {captureBusy === "keep" ? t("news.capturing") : t("news.captureKeep")}
            </button>
            <button
              type="button"
              onClick={() => runCapture("article", article.id)}
              disabled={captureBusy !== null}
              title={t("news.captureArticleHint")}
              className="inline-flex items-center gap-1 rounded-lg border border-border bg-bg px-2 py-1 text-xs hover:border-accent disabled:opacity-50"
            >
              <BookText className="h-3 w-3" />
              {captureBusy === "article" ? t("news.capturing") : t("news.captureArticle")}
            </button>
            <button
              type="button"
              onClick={() => runCapture("watched", article.id)}
              disabled={captureBusy !== null}
              title={t("news.captureWatchedHint")}
              className="inline-flex items-center gap-1 rounded-lg border border-border bg-bg px-2 py-1 text-xs hover:border-accent disabled:opacity-50"
            >
              <Eye className="h-3 w-3" />
              {captureBusy === "watched" ? t("news.capturing") : t("news.captureWatched")}
            </button>
            <button
              type="button"
              onClick={() => {
                setCustomOpen((v) => !v);
                setCaptureMessage(null);
              }}
              disabled={captureBusy !== null}
              title={t("news.captureCustomHint")}
              className={`inline-flex items-center gap-1 rounded-lg border px-2 py-1 text-xs disabled:opacity-50 ${
                customOpen
                  ? "border-accent bg-accent/10 text-accent"
                  : "border-border bg-bg hover:border-accent"
              }`}
            >
              <Pencil className="h-3 w-3" />
              {captureBusy === "custom"
                ? t("news.captureCustomBusy")
                : t("news.captureCustom")}
            </button>
          </div>
          {customOpen && (
            <div className="space-y-2 rounded-lg border border-border bg-bg p-2">
              <label
                htmlFor="news-custom-instruction"
                className="block text-xs text-muted"
              >
                {t("news.captureCustomLabel")}
              </label>
              <textarea
                id="news-custom-instruction"
                value={customText}
                onChange={(e) => setCustomText(e.target.value)}
                onKeyDown={(e) => {
                  if (
                    (e.key === "Enter" && (e.ctrlKey || e.metaKey)) ||
                    (e.key === "Enter" && !e.shiftKey && customText.trim())
                  ) {
                    e.preventDefault();
                    void runCustomCapture(article.id, customText);
                  }
                  if (e.key === "Escape") {
                    e.preventDefault();
                    setCustomOpen(false);
                  }
                }}
                rows={2}
                maxLength={400}
                placeholder={t("news.captureCustomPlaceholder")}
                className="w-full resize-none rounded-md border border-border bg-surface px-2 py-1 text-xs focus:border-accent focus:outline-none"
                autoFocus
              />
              <div className="flex items-center justify-between gap-2">
                <span className="text-[11px] text-muted">
                  {customText.length}/400
                </span>
                <div className="flex items-center gap-2">
                  <button
                    type="button"
                    onClick={() => {
                      setCustomOpen(false);
                      setCustomText("");
                    }}
                    className="rounded-md border border-border bg-bg px-2 py-1 text-xs hover:border-accent"
                  >
                    {t("common.cancel")}
                  </button>
                  <button
                    type="button"
                    onClick={() => runCustomCapture(article.id, customText)}
                    disabled={
                      captureBusy !== null || customText.trim().length === 0
                    }
                    className="rounded-md border border-accent bg-accent/10 px-2 py-1 text-xs text-accent hover:bg-accent/20 disabled:opacity-50"
                  >
                    {captureBusy === "custom"
                      ? t("news.captureCustomBusy")
                      : t("news.captureCustomSubmit")}
                  </button>
                </div>
              </div>
            </div>
          )}
          {captureMessage && (
            <div
              className={`pt-2 text-xs ${
                captureMessage.kind === "ok" ? "text-accent" : "text-red-500"
              }`}
            >
              {captureMessage.kind === "err" ? (
                <p>{t("news.captureFailed", { err: captureMessage.text })}</p>
              ) : captureMessage.summary ? (
                <div className="space-y-1">
                  <p>{captureMessage.summary}</p>
                  {captureMessage.files && captureMessage.files.length > 0 && (
                    <p className="text-muted">
                      {t("news.customFilesTouched", {
                        count: captureMessage.files.length,
                      })}{" "}
                      <code>{captureMessage.files.join(", ")}</code>
                    </p>
                  )}
                </div>
              ) : (
                <p>{t("news.captureOk", { path: captureMessage.path })}</p>
              )}
            </div>
          )}
        </header>

        <section>
          <div className="mb-2 flex items-center gap-2 border-b border-border">
            <button
              type="button"
              onClick={() => setViewMode("summary")}
              className={`-mb-px border-b-2 px-2 py-1 text-xs font-medium uppercase tracking-wide ${
                viewMode === "summary"
                  ? "border-accent text-accent"
                  : "border-transparent text-muted hover:text-text"
              }`}
            >
              {t("news.tabSummary")}
            </button>
            <button
              type="button"
              onClick={() => setViewMode("html")}
              disabled={!article.raw_html}
              className={`-mb-px border-b-2 px-2 py-1 text-xs font-medium uppercase tracking-wide disabled:opacity-40 ${
                viewMode === "html"
                  ? "border-accent text-accent"
                  : "border-transparent text-muted hover:text-text"
              }`}
              title={!article.raw_html ? t("news.tabHtmlMissing") : undefined}
            >
              {t("news.tabHtml")}
            </button>
          </div>
          {viewMode === "html" && article.raw_html ? (
            <ArticleBody html={article.raw_html} fallback={null} />
          ) : (
            <ArticleBody html={null} fallback={article.summary} />
          )}
        </section>
      </article>
    </section>
  );
}

// Strip the highest-risk parts of feed-supplied HTML (inline scripts
// and on*= event handlers) before injecting it. The user controls
// which feeds get added so the bar is "don't be obviously hostile",
// not "withstand a malicious feed". If a feed returns garbage we
// fall back to the plain-text summary.
function _sanitiseHtml(raw: string): string {
  return raw
    .replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi, "")
    .replace(/<style\b[^>]*>[\s\S]*?<\/style>/gi, "")
    .replace(/\son[a-z]+\s*=\s*"[^"]*"/gi, "")
    .replace(/\son[a-z]+\s*=\s*'[^']*'/gi, "");
}

function ArticleBody({
  html,
  fallback,
}: {
  html: string | null;
  fallback: string | null;
}) {
  const { t } = useTranslation();
  if (html && html.trim()) {
    return (
      <div
        className="prose prose-invert max-w-none text-sm leading-relaxed text-text/90 [&_a]:text-accent [&_a]:underline [&_img]:max-w-full [&_img]:rounded [&_p]:my-2 [&_h2]:mt-4 [&_h2]:mb-2 [&_h2]:text-base [&_h3]:mt-3 [&_h3]:mb-1.5 [&_h3]:text-sm [&_ul]:my-2 [&_ul]:list-disc [&_ul]:pl-5 [&_ol]:my-2 [&_ol]:list-decimal [&_ol]:pl-5 [&_blockquote]:border-l-2 [&_blockquote]:border-border [&_blockquote]:pl-3 [&_blockquote]:text-muted"
        // eslint-disable-next-line react/no-danger
        dangerouslySetInnerHTML={{ __html: _sanitiseHtml(html) }}
      />
    );
  }
  return (
    <p className="whitespace-pre-wrap text-sm leading-relaxed text-text/90">
      {fallback ?? t("news.noDescription")}
    </p>
  );
}

// Feed favicon — falls back to a generic RSS glyph when the feed has
// no icon. The unread indicator is a coloured dot in the top-right
// corner so we can keep the feed icon's identity while still showing
// read state at a glance.
function FeedIcon({
  favicon,
  alt,
  isRead,
  size = 14,
}: {
  favicon: string | null;
  alt: string;
  isRead: boolean;
  size?: number;
}) {
  return (
    <span
      className="relative mt-0.5 inline-flex shrink-0 items-center justify-center"
      style={{ width: size, height: size }}
    >
      {favicon ? (
        <img
          src={favicon}
          alt={alt}
          width={size}
          height={size}
          className={`rounded-sm ${isRead ? "opacity-50" : ""}`}
          referrerPolicy="no-referrer"
          onError={(e) => {
            // If the data URI is invalid the broken-image glyph would
            // render — hide the img so the parent's empty box wins.
            (e.currentTarget as HTMLImageElement).style.visibility = "hidden";
          }}
        />
      ) : (
        <Rss
          className={isRead ? "text-muted/70" : "text-accent"}
          style={{ width: size, height: size }}
        />
      )}
      {!isRead && (
        <span className="absolute -right-0.5 -top-0.5 h-1.5 w-1.5 rounded-full bg-accent ring-1 ring-bg" />
      )}
    </span>
  );
}

