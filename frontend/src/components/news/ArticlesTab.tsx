// Per-feed article browser. Three-pane layout:
//   left  — feed list (with unread/total counts; click to filter)
//   middle— article titles for the active feed (with unread-only toggle)
//   right — selected article: title, summary, hashtags, link to source

import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { useQuery } from "@tanstack/react-query";
import { ExternalLink, Mail, MailOpen, Newspaper } from "lucide-react";

import {
  api,
  type NewsArticle,
  type NewsFeedSummary,
} from "@/lib/api";

type Period = "today" | "7d" | "30d" | "custom";

interface Props {
  period: Period;
  customFrom: string;
  customTo: string;
}

export default function ArticlesTab({ period, customFrom, customTo }: Props) {
  const { t } = useTranslation();
  const [activeFeed, setActiveFeed] = useState<string | null>(null);
  const [unreadOnly, setUnreadOnly] = useState(false);
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

  const articles = useQuery<NewsArticle[]>({
    queryKey: [
      "news-articles",
      period,
      customFrom,
      customTo,
      activeFeed,
      unreadOnly,
    ],
    queryFn: () => {
      const qs = new URLSearchParams(periodQS);
      if (activeFeed) qs.set("feed_id", activeFeed);
      if (unreadOnly) qs.set("unread_only", "true");
      return api.get<NewsArticle[]>(`/api/news/articles?${qs.toString()}`);
    },
  });

  const selected = useQuery<NewsArticle>({
    queryKey: ["news-article", selectedId],
    queryFn: () =>
      api.get<NewsArticle>(`/api/news/articles/${encodeURIComponent(selectedId!)}`),
    enabled: !!selectedId,
  });

  return (
    <div className="grid h-full grid-cols-[14rem_22rem_1fr] divide-x divide-border">
      {/* Feed sidebar */}
      <aside className="overflow-y-auto bg-surface/50 p-3">
        <h3 className="mb-2 text-xs font-medium uppercase tracking-wide text-muted">
          {t("news.feedsHeader")}
        </h3>
        <button
          type="button"
          onClick={() => setActiveFeed(null)}
          className={`mb-1 flex w-full items-center justify-between rounded px-2 py-1.5 text-left text-sm transition ${
            activeFeed === null
              ? "bg-accent/15 text-accent"
              : "text-text/85 hover:bg-bg"
          }`}
        >
          <span className="truncate">{t("news.allFeeds")}</span>
          {feeds.data && (
            <span className="ml-2 shrink-0 text-xs text-muted">
              {feeds.data.reduce((s, f) => s + f.unread, 0)}/
              {feeds.data.reduce((s, f) => s + f.total, 0)}
            </span>
          )}
        </button>
        {feeds.data?.map((f) => (
          <button
            key={f.feed_id}
            type="button"
            onClick={() => setActiveFeed(f.feed_id)}
            className={`flex w-full items-start justify-between rounded px-2 py-1.5 text-left text-sm transition ${
              activeFeed === f.feed_id
                ? "bg-accent/15 text-accent"
                : "text-text/85 hover:bg-bg"
            }`}
            title={f.feed_group ? `${f.feed_group} · ${f.feed_title}` : f.feed_title}
          >
            <span className="min-w-0 flex-1">
              <span className="block truncate">{f.feed_title}</span>
              {f.feed_group && (
                <span className="block truncate text-[10px] text-muted">
                  {f.feed_group}
                </span>
              )}
            </span>
            <span className="ml-2 shrink-0 text-xs text-muted">
              {f.unread > 0 ? (
                <b className="text-text">{f.unread}</b>
              ) : (
                f.unread
              )}
              /{f.total}
            </span>
          </button>
        ))}
      </aside>

      {/* Article list */}
      <section className="flex h-full flex-col overflow-hidden">
        <div className="flex items-center justify-between border-b border-border bg-surface px-3 py-2">
          <label className="flex items-center gap-2 text-xs">
            <input
              type="checkbox"
              checked={unreadOnly}
              onChange={(e) => setUnreadOnly(e.target.checked)}
            />
            {t("news.unreadOnly")}
          </label>
          {articles.data && (
            <span className="text-[10px] text-muted">
              {articles.data.length} / {articles.data.length}
            </span>
          )}
        </div>
        <div className="flex-1 overflow-y-auto">
          {articles.isLoading ? (
            <p className="px-3 py-3 text-sm text-muted">
              {t("common.loading")}
            </p>
          ) : !articles.data || articles.data.length === 0 ? (
            <p className="px-3 py-3 text-sm text-muted">
              {t("news.noArticles")}
            </p>
          ) : (
            articles.data.map((a) => (
              <button
                key={a.id}
                type="button"
                onClick={() => setSelectedId(a.id)}
                className={`flex w-full flex-col gap-0.5 border-b border-border px-3 py-2 text-left transition ${
                  selectedId === a.id
                    ? "bg-accent/10"
                    : a.is_read
                      ? "bg-bg"
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

      {/* Article detail */}
      <section className="overflow-y-auto bg-bg p-4">
        {!selectedId ? (
          <div className="flex h-full items-center justify-center text-center">
            <div className="space-y-2">
              <Newspaper className="mx-auto h-7 w-7 text-muted" />
              <p className="text-sm text-muted">{t("news.selectArticle")}</p>
            </div>
          </div>
        ) : selected.isLoading || !selected.data ? (
          <p className="text-sm text-muted">{t("common.loading")}</p>
        ) : (
          <ArticleDetail article={selected.data} />
        )}
      </section>
    </div>
  );
}

function ArticleDetail({ article }: { article: NewsArticle }) {
  const { t } = useTranslation();
  const subtitle = [
    article.feed_group,
    article.feed_title ?? article.source,
    article.published_at.slice(0, 16).replace("T", " "),
  ]
    .filter(Boolean)
    .join(" · ");
  return (
    <article className="mx-auto max-w-2xl space-y-4">
      <header className="space-y-1">
        <h2 className="text-lg font-semibold text-text">{article.title}</h2>
        <p className="text-xs text-muted">{subtitle}</p>
        {article.url && (
          <a
            href={article.url}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center gap-1 text-xs text-accent hover:underline"
          >
            <ExternalLink className="h-3 w-3" />
            {t("news.openOriginal")}
          </a>
        )}
      </header>

      <section>
        <h3 className="mb-1 text-xs font-medium uppercase tracking-wide text-muted">
          {t("news.summaryHeader")}
        </h3>
        <p className="whitespace-pre-wrap text-sm leading-relaxed text-text/90">
          {article.description ?? t("news.noDescription")}
        </p>
      </section>

      <section>
        <h3 className="mb-1.5 text-xs font-medium uppercase tracking-wide text-muted">
          {t("news.tagsHeader")}
        </h3>
        {!article.tags || article.tags.length === 0 ? (
          <p className="text-xs text-muted">{t("news.noTags")}</p>
        ) : (
          <div className="flex flex-wrap gap-1.5">
            {article.tags.map((tag) => (
              <span
                key={tag}
                className="rounded-full border border-accent/40 bg-accent/10 px-2 py-0.5 text-xs text-accent"
              >
                #{tag}
              </span>
            ))}
          </div>
        )}
      </section>
    </article>
  );
}
