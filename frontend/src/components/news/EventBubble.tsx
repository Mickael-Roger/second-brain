// One bubble in the News view. Sized by article_count, hover reveals
// the underlying articles via an absolute-positioned tooltip card.

import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { ExternalLink } from "lucide-react";

import { api, type NewsEventBubble, type NewsEventDetail } from "@/lib/api";

interface Props {
  bubble: NewsEventBubble;
  x: number;
  y: number;
  r: number;
  hovered: boolean;
  onHoverStart: () => void;
  onHoverEnd: () => void;
}

export default function EventBubble({
  bubble,
  x,
  y,
  r,
  hovered,
  onHoverStart,
  onHoverEnd,
}: Props) {
  const { t } = useTranslation();

  // Lazy: only fetch the article list once the user hovers. React Query
  // caches by id so re-hovers don't re-fetch.
  const detail = useQuery<NewsEventDetail>({
    queryKey: ["news-event", bubble.id],
    queryFn: () => api.get<NewsEventDetail>(`/api/news/events/${bubble.id}`),
    enabled: hovered,
    staleTime: 60_000,
  });

  // Title font scales with bubble size, but stays readable at the small end.
  const fontSize = Math.max(10, Math.min(16, r / 5));

  return (
    <div
      className="absolute"
      style={{
        left: x - r,
        top: y - r,
        width: r * 2,
        height: r * 2,
      }}
      onMouseEnter={onHoverStart}
      onMouseLeave={onHoverEnd}
    >
      <div
        className={`flex h-full w-full cursor-pointer items-center justify-center rounded-full border text-center transition ${
          hovered
            ? "border-accent bg-accent/20 text-text shadow-lg shadow-accent/20"
            : "border-accent/40 bg-accent/10 text-text/90 hover:border-accent"
        }`}
        style={{ fontSize, padding: r > 30 ? 12 : 6 }}
      >
        <span className="line-clamp-3 px-2 leading-tight">
          {bubble.title}
        </span>
      </div>

      {/* Bubble badge — count */}
      <div
        className="absolute -bottom-1 left-1/2 -translate-x-1/2 rounded-full bg-bg px-2 py-0.5 text-[10px] font-medium text-muted ring-1 ring-border"
      >
        {bubble.article_count}
      </div>

      {hovered && (
        <div
          className="pointer-events-auto absolute z-20 w-80 rounded-lg border border-border bg-surface p-3 text-left shadow-xl"
          style={{
            // Anchor the tooltip just below the bubble, but flip above
            // when there isn't enough room (rough heuristic — viewport-
            // aware positioning would need a portal).
            left: r,
            top: r * 2 + 8,
          }}
        >
          <h3 className="text-sm font-semibold text-text">{bubble.title}</h3>
          <p className="mt-1 text-xs text-muted">
            {t("news.occurredOn")} {bubble.occurred_on} ·{" "}
            {t("news.articleCount", { count: bubble.article_count })}
          </p>
          {bubble.summary && (
            <p className="mt-2 text-xs text-text/80">{bubble.summary}</p>
          )}
          <div className="mt-3 max-h-60 space-y-2 overflow-y-auto pr-1">
            {detail.isLoading && (
              <p className="text-xs text-muted">{t("common.loading")}</p>
            )}
            {detail.data?.articles.map((a) => (
              <a
                key={a.id}
                href={a.url ?? "#"}
                target="_blank"
                rel="noopener noreferrer"
                className="block rounded border border-border bg-bg px-2 py-1.5 text-xs hover:border-accent"
              >
                <div className="flex items-start gap-1.5">
                  <span className="line-clamp-2 flex-1 text-text">
                    {a.title}
                  </span>
                  {a.url && (
                    <ExternalLink className="mt-0.5 h-3 w-3 shrink-0 text-muted" />
                  )}
                </div>
                <div className="mt-0.5 text-[10px] text-muted">
                  {a.feed_title ?? a.source} · {a.published_at.slice(0, 10)}
                </div>
              </a>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
