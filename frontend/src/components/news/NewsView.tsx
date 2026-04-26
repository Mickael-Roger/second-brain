// News tab. Three-pane layout reproducing FreshRSS's sidebar:
//   - left: feeds grouped under their FreshRSS category (folder),
//           collapsible, with unread/total counts
//   - middle: article titles for the active feed/category, with an
//             "unread only" toggle (which also collapses the sidebar
//             to feeds that still have unread items)
//   - right: selected article — image, summary, mark-as-read, and a
//            "chat about this" button that hands the article context
//            off to the Chat tab.

import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ChevronDown,
  ChevronRight,
  ExternalLink,
  Mail,
  MailOpen,
  MessageSquare,
  Newspaper,
  Play,
} from "lucide-react";

import {
  api,
  type NewsArticleDetail,
  type NewsArticleSummary,
  type NewsFeedSummary,
} from "@/lib/api";

type Period = "today" | "7d" | "30d" | "custom";

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

  const [period, setPeriod] = useState<Period>("7d");
  const [customFrom, setCustomFrom] = useState<string>(() => isoDaysAgo(7));
  const [customTo, setCustomTo] = useState<string>(() => isoDaysAgo(0));
  const [unreadOnly, setUnreadOnly] = useState(false);
  const [selection, setSelection] = useState<Selection>({ kind: "all" });
  const [selectedId, setSelectedId] = useState<string | null>(null);

  const periodQS = useMemo(() => {
    const qs = new URLSearchParams({ period });
    if (period === "custom") {
      qs.set("from", customFrom);
      qs.set("to", customTo);
    }
    return qs;
  }, [period, customFrom, customTo]);

  const feeds = useQuery<NewsFeedSummary[]>({
    queryKey: ["news-feeds", period, customFrom, customTo],
    queryFn: () =>
      api.get<NewsFeedSummary[]>(`/api/news/feeds?${periodQS.toString()}`),
  });

  const articles = useQuery<NewsArticleSummary[]>({
    queryKey: [
      "news-articles",
      period,
      customFrom,
      customTo,
      selection,
      unreadOnly,
    ],
    queryFn: () => {
      const qs = new URLSearchParams(periodQS);
      if (selection.kind === "feed") qs.set("feed_id", selection.feedId);
      if (selection.kind === "category") qs.set("feed_group", selection.group);
      if (unreadOnly) qs.set("unread_only", "true");
      return api.get<NewsArticleSummary[]>(
        `/api/news/articles?${qs.toString()}`,
      );
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
    mutationFn: () => {
      const qs = new URLSearchParams({ period });
      if (period === "custom") {
        qs.set("from", customFrom);
        qs.set("to", customTo);
      }
      return api.post<{ started: boolean }>(
        `/api/news/fetch?${qs.toString()}`,
      );
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["news-feeds"] });
      qc.invalidateQueries({ queryKey: ["news-articles"] });
    },
  });

  const markRead = useMutation({
    mutationFn: (articleId: string) =>
      api.post<{ article_id: string; is_read: boolean }>(
        `/api/news/articles/${encodeURIComponent(articleId)}/read`,
      ),
    onSuccess: (_data, articleId) => {
      // Optimistically refresh feed counts + the active article list +
      // the open detail view.
      qc.invalidateQueries({ queryKey: ["news-feeds"] });
      qc.invalidateQueries({ queryKey: ["news-articles"] });
      qc.invalidateQueries({ queryKey: ["news-article", articleId] });
    },
  });

  function startChatAbout(a: NewsArticleDetail) {
    const lines: string[] = [
      `I'm looking at this news article in my second-brain.`,
      ``,
      `Title: ${a.title}`,
      `Feed: ${a.feed_title ?? a.source}` +
        (a.feed_group ? ` / ${a.feed_group}` : ""),
      `Published: ${a.published_at}`,
    ];
    if (a.url) lines.push(`URL: ${a.url}`);
    lines.push(
      ``,
      `Summary:`,
      a.summary?.trim() || "(no summary)",
      ``,
      `Use the news.* tools (news.read_news with article_id="${a.id}",`,
      `news.mark_read, news.list_news, etc.) to dig deeper. Help me`,
      `understand it, find related articles, or take action.`,
    );
    window.localStorage.setItem("sb.chat.draft", lines.join("\n"));
    onOpenChat();
  }

  return (
    <div className="flex h-full flex-col">
      <header className="flex flex-wrap items-center gap-3 border-b border-border bg-surface px-4 py-3">
        <Newspaper className="h-5 w-5 text-accent" />
        <h1 className="flex-1 text-lg font-semibold">{t("news.title")}</h1>

        <PeriodSelector
          period={period}
          customFrom={customFrom}
          customTo={customTo}
          onPeriod={setPeriod}
          onFrom={setCustomFrom}
          onTo={setCustomTo}
        />

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

      <div className="grid flex-1 grid-cols-[16rem_22rem_1fr] divide-x divide-border overflow-hidden">
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

        <ArticleList
          articles={articles.data ?? []}
          loading={articles.isLoading}
          selectedId={selectedId}
          unreadOnly={unreadOnly}
          onUnreadToggle={setUnreadOnly}
          onSelect={setSelectedId}
        />

        <DetailPane
          articleId={selectedId}
          article={selected.data}
          loading={selected.isLoading}
          markRead={(id) => markRead.mutate(id)}
          markPending={markRead.isPending}
          onChat={startChatAbout}
        />
      </div>
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
                          className={`flex w-full items-center justify-between rounded px-2 py-1 text-left text-xs transition ${
                            active
                              ? "bg-accent/15 text-accent"
                              : "text-text/85 hover:bg-bg"
                          }`}
                          title={f.feed_title}
                        >
                          <span className="truncate">{f.feed_title}</span>
                          <span className="ml-2 shrink-0 text-[10px] text-muted">
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
                {a.is_read ? (
                  <MailOpen className="mt-0.5 h-3.5 w-3.5 shrink-0 text-muted" />
                ) : (
                  <Mail className="mt-0.5 h-3.5 w-3.5 shrink-0 text-accent" />
                )}
                <span
                  className={`line-clamp-2 flex-1 text-sm ${
                    a.is_read ? "text-muted" : "text-text"
                  }`}
                >
                  {a.title}
                </span>
              </div>
              <span className="ml-5 text-[10px] text-muted">
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
  markRead: (id: string) => void;
  markPending: boolean;
  onChat: (a: NewsArticleDetail) => void;
}

function DetailPane({
  articleId,
  article,
  loading,
  markRead,
  markPending,
  onChat,
}: DetailPaneProps) {
  const { t } = useTranslation();
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
              onClick={() => markRead(article.id)}
              disabled={article.is_read || markPending}
              className="inline-flex items-center gap-1 rounded-lg border border-border bg-bg px-2 py-1 text-xs hover:border-accent disabled:opacity-50"
            >
              <MailOpen className="h-3 w-3" />
              {article.is_read
                ? t("news.alreadyRead")
                : markPending
                  ? t("news.marking")
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
          </div>
        </header>

        <section>
          <h3 className="mb-1 text-xs font-medium uppercase tracking-wide text-muted">
            {t("news.summaryHeader")}
          </h3>
          <p className="whitespace-pre-wrap text-sm leading-relaxed text-text/90">
            {article.summary ?? t("news.noDescription")}
          </p>
        </section>
      </article>
    </section>
  );
}

// ─── Period selector ────────────────────────────────────────────────

interface PeriodProps {
  period: Period;
  customFrom: string;
  customTo: string;
  onPeriod: (p: Period) => void;
  onFrom: (s: string) => void;
  onTo: (s: string) => void;
}

function PeriodSelector({
  period,
  customFrom,
  customTo,
  onPeriod,
  onFrom,
  onTo,
}: PeriodProps) {
  const { t } = useTranslation();
  const opts: { id: Period; label: string }[] = [
    { id: "today", label: t("news.periodToday") },
    { id: "7d", label: t("news.period7d") },
    { id: "30d", label: t("news.period30d") },
    { id: "custom", label: t("news.periodCustom") },
  ];
  return (
    <div className="flex flex-wrap items-center gap-2 text-xs">
      <span className="text-muted">{t("news.periodLabel")}:</span>
      <div className="flex overflow-hidden rounded-lg border border-border">
        {opts.map((o) => (
          <button
            key={o.id}
            onClick={() => onPeriod(o.id)}
            className={`px-2 py-1 ${
              period === o.id
                ? "bg-accent text-bg"
                : "bg-bg text-muted hover:text-text"
            }`}
          >
            {o.label}
          </button>
        ))}
      </div>
      {period === "custom" && (
        <>
          <input
            type="date"
            value={customFrom}
            onChange={(e) => onFrom(e.target.value)}
            className="rounded-lg border border-border bg-bg px-2 py-1 text-text"
          />
          <span className="text-muted">→</span>
          <input
            type="date"
            value={customTo}
            onChange={(e) => onTo(e.target.value)}
            className="rounded-lg border border-border bg-bg px-2 py-1 text-text"
          />
        </>
      )}
    </div>
  );
}

function isoDaysAgo(days: number): string {
  const d = new Date();
  d.setDate(d.getDate() - days);
  return d.toISOString().slice(0, 10);
}
